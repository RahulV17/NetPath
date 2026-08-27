"""Analytics API — FastAPI backend with WebSocket for real-time stats.

Updated to expose ML classifier and HW offload stats from the unified
data path engine.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from network_lab.core.datapath import DataPathEngine
from network_lab.core.generator import GeneratorConfig, TrafficGenerator
from network_lab.core.parser import ParsedPacket

# ═══════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════

class GeneratorRequest(BaseModel):
    # gt=1 avoids ZeroDivisionError (rate 0) and negative-rate hot loops
    rate_pps: int = Field(default=100, gt=0, le=100_000)
    duration: float = Field(default=10.0, gt=0)
    src_ip: str = "10.0.0.100"
    dst_ip: str = "10.0.0.1"


class QoSPolicyRequest(BaseModel):
    flow_type: str
    priority: int
    dscp: int
    rate_limit_mbps: float | None = None
    dst_port: int | None = None
    src_port: int | None = None


class ACLRequest(BaseModel):
    src: str = "*"
    dst: str = "*"
    sport: int = -1
    dport: int = -1
    proto: int = -1
    action: str = "drop"


# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class AnalyticsEngine:
    """Collects and aggregates packet/flow statistics.

    Flow tracking is delegated to the DataPathEngine's ML FlowTable
    (single source of truth) — this class only tracks protocol-level
    counters and the sliding throughput window.
    """

    def __init__(self):
        self.protocol_counts: dict = defaultdict(int)
        self.distribution_window: deque = deque(maxlen=10000)
        self._lock = asyncio.Lock()

    async def ingest(self, pkt: ParsedPacket, meta=None) -> None:
        """Ingest a parsed packet with metadata."""
        async with self._lock:
            proto = "Other"
            if pkt.tcp:
                proto = "TCP"
            elif pkt.udp:
                proto = "UDP"
            elif pkt.icmp:
                proto = "ICMP"
            elif pkt.arp:
                proto = "ARP"
            self.protocol_counts[proto] += 1
            self.distribution_window.append((pkt.timestamp, proto))

    def get_protocol_distribution(self) -> dict:
        return dict(self.protocol_counts)

    def get_throughput(self, window_seconds: float = 10.0) -> dict:
        now = time.time()
        cutoff = now - window_seconds
        recent = [p for t, p in self.distribution_window if t > cutoff]
        return {
            "pps": len(recent) / window_seconds,
            "protocols": {p: recent.count(p) for p in set(recent)},
            "window": window_seconds,
        }


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# ═══════════════════════════════════════════════════════════════════════════

datapath = DataPathEngine(enable_ml=True, enable_hw_offload=True, enable_qos=True)
analytics = AnalyticsEngine()
generator: TrafficGenerator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(datapath.run_cleanup())
    yield
    task.cancel()


app = FastAPI(title="Network Lab Analytics", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "timestamp": time.time()}


@app.get("/api/stats")
async def get_stats() -> dict:
    """Get current system statistics."""
    return {
        "datapath": datapath.stats,
        "qos": datapath.traffic_mgr.stats,
        "analytics": {
            "protocol_distribution": analytics.get_protocol_distribution(),
            "throughput": analytics.get_throughput(),
        },
        "ml": {
            "flow_table_size": datapath.flow_table.size,
            "classifier_enabled": datapath.enable_ml,
        },
        "hw_offload": datapath.offload_engine.get_stats() if datapath.offload_engine else None,
    }


@app.get("/api/protocols")
async def get_protocols() -> dict:
    return analytics.get_protocol_distribution()


@app.get("/api/flows")
async def get_flows() -> list[dict]:
    """Return active flows from the unified ML FlowTable."""
    flows = []
    for f in datapath.flow_table.list_flows():
        tc = await datapath.flow_table.get_classification(f)
        flows.append({
            "src_ip": f.src_ip,
            "dst_ip": f.dst_ip,
            "src_port": f.src_port,
            "dst_port": f.dst_port,
            "protocol": f.protocol,
            "packets": f.total_packets,
            "bytes": f.total_bytes,
            "duration": round(time.time() - f.start_time, 2),
            "traffic_class": tc.name if tc else None,
        })
    return flows


# Generator task lifecycle — kept referenced so tasks aren't GC-able
# mid-flight and exceptions are observed (spec audit: fire-and-forget fix)
_generator_task: asyncio.Task | None = None
_packet_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    """create_task + retain reference + log exceptions."""
    task = asyncio.create_task(coro)
    _packet_tasks.add(task)
    task.add_done_callback(_packet_tasks.discard)
    return task


@app.post("/api/generator/start")
async def start_generator(req: GeneratorRequest) -> dict:
    global generator, _generator_task
    if generator and generator._running:
        return {"status": "already_running"}

    config = GeneratorConfig(
        rate_pps=req.rate_pps,
        duration=req.duration,
        src_ip=req.src_ip,
        dst_ip=req.dst_ip,
    )
    generator = TrafficGenerator(config)

    def on_packet(pkt: ParsedPacket) -> None:
        _spawn(datapath.process_packet(pkt))
        _spawn(analytics.ingest(pkt))

    # Retain the stream task so /stop can cancel it deterministically
    _generator_task = asyncio.create_task(generator.generate_stream(on_packet))
    return {"status": "started", "config": req.model_dump()}


@app.post("/api/generator/stop")
async def stop_generator() -> dict:
    global generator, _generator_task
    if generator:
        generator.stop()
        if _generator_task and not _generator_task.done():
            _generator_task.cancel()
        _generator_task = None
        return {"status": "stopped", "stats": generator.stats}
    return {"status": "not_running"}


@app.post("/api/qos/policy")
async def add_qos_policy(req: QoSPolicyRequest) -> dict:
    from network_lab.core.qos import FlowRule

    # Validation (audit: unvalidated priority crashed the scheduler;
    # rules without match fields reclassified everything; rate_limit
    # was silently ignored without a shaper).
    if not 0 <= req.priority <= 5:
        return {"status": "error", "reason": "priority must be 0-5"}
    has_match = req.dst_port is not None or req.src_port is not None
    if not has_match:
        return {
            "status": "error",
            "reason": "rule must match at least one port field",
        }

    rule = FlowRule(
        name=req.flow_type,
        priority=req.priority,
        dscp=req.dscp,
        rate_limit_mbps=req.rate_limit_mbps,
        dst_port=req.dst_port,
        src_port=req.src_port,
    )
    datapath.traffic_mgr.classifier.add_rule(rule)
    if req.rate_limit_mbps is not None:
        datapath.traffic_mgr.add_shaper(
            ("policy", req.flow_type), req.rate_limit_mbps
        )
    return {"status": "added", "rule": req.model_dump()}


@app.post("/api/acl")
async def add_acl(req: ACLRequest) -> dict:
    """Add an ACL rule."""
    datapath.add_acl(req.src, req.dst, req.sport, req.dport, req.proto, req.action)
    return {"status": "added", "rule": req.model_dump()}


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket for real-time stats stream."""
    await websocket.accept()
    try:
        while True:
            # Real per-flow telemetry for the web lab's readouts
            flow_stats = datapath.flow_table.snapshot(top=5)

            stats = {
                "timestamp": time.time(),
                "datapath": datapath.stats,
                "qos": datapath.traffic_mgr.stats,
                "analytics": {
                    "protocol_distribution": analytics.get_protocol_distribution(),
                    "throughput": analytics.get_throughput(),
                },
                "ml": {
                    "flow_table_size": datapath.flow_table.size,
                },
                "hw_offload": datapath.offload_engine.get_stats() if datapath.offload_engine else None,
                "flows": flow_stats,
            }
            await websocket.send_json(stats)
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    except Exception:
        # Abrupt TCP resets raise ConnectionClosedError/BrokenResourceError,
        # not just WebSocketDisconnect — don't leak sockets/log noise.
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
