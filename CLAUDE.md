# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Truth & Accuracy Rules (highest priority — apply to every response)

You are committed to truth and accuracy above everything else, including being helpful. A wrong answer delivered confidently is worse than no answer.

1. **UNCERTAINTY**: If not fully certain about something, say so clearly ("I am not certain, but...", "You may want to verify this..."). Never state guesses as facts.
2. **SOURCES**: Do not invent paper titles, author names, URLs, or book references. If you cannot name a real, verifiable source, say "I do not have a verified source for this."
3. **STATISTICS**: Flag any number you are not 100% confident in. Say "approximately" and recommend verification from a primary source.
4. **RECENT EVENTS**: Warn when a topic may have changed since your knowledge cutoff. Do not present outdated info as current.
5. **PEOPLE and QUOTES**: Never attribute a quote to a real person unless certain they said it. If unsure, say "I cannot confirm this quote is accurate."
6. **CODE and TECHNICAL**: Never invent function names, library methods, or API syntax. If unsure a function exists, tell the user to verify it in the current docs.
7. **LOGIC GAPS**: Do not fill missing context with assumptions. If something is unclear, ask a clarifying question before answering.

If a response would require breaking any of these rules, choose honesty over helpfulness every time.

## Project Overview

NetPath — a userspace network stack written in pure Python: manual byte-level protocol parsing (L2–L7), L2 bridging / L3 routing, ML traffic classification, hardware-offload modeling, and QoS, all exposed through a FastAPI backend and a real-time React dashboard. Requires Python ≥3.10 (the `.venv` here runs 3.12). Source lives under `src/network_lab/` (src layout, installed editable).

## Commands

### Python backend (from repo root; activate `.venv` first)

```bash
pip install -e ".[dev]"                                   # install package + dev deps

pytest tests/ -v                                          # full suite
pytest tests/unit/test_parser.py -v                       # single file
pytest tests/unit/test_datapath.py::TestBridge::test_learn_mac -v   # single test
coverage run -m pytest && coverage report                 # coverage

ruff check src tests                                      # lint (line-length 100, target py310)
pyright                                                   # type check

python -m network_lab.demo                                # scripted 20-packet walkthrough, no server needed
network-lab-benchmark                                     # or: python -m network_lab.benchmark
network-lab                                               # Textual TUI dashboard (g/s/r keys control generator)
```

Pytest notes: `asyncio_mode = "auto"` (async tests need no marker), `testpaths = ["tests"]`. Parser tests use Hypothesis fuzzing — random bytes must never crash `parse_packet()`.

### Dashboard (backend + frontend in separate terminals)

```bash
uvicorn network_lab.api.app:app --reload --port 8000      # terminal 1: REST + WebSocket API
cd web && npm install && npm run dev                      # terminal 2: Vite dev server
npm run build                                             # frontend prod build (tsc && vite build)
```

### Docker topology (integration view)

```bash
cd docker && docker-compose up --build
# 3 containers across two bridge networks: STA (traffic source) → AP (NetPath bridge) → Server (analytics)
```

## Architecture

Everything centers on the 7-stage packet pipeline in `DataPathEngine.process_packet()` (`src/network_lab/core/datapath.py`):

```
parse → ACL → flow/ML classify → HW offload decision → Bridge(L2)/Router(L3) forward → QoS → egress
```

Per-stage facts that matter when modifying code:

- **Parser is hand-written by design** (`core/parser.py`): every header decoded from raw bytes via `struct.unpack` and bit shifts — Ethernet/VLAN(802.1Q)/ARP, IPv4 (options, fragmentation, DSCP/ECN), IPv6, TCP/UDP/ICMP, DHCP TLV walk, VXLAN (RFC 7348), GRE, 802.11. **Never add scapy/dpkt for parsing** — scapy exists solely to *generate* synthetic packets (`core/generator.py`). Malformed input must degrade gracefully: retain whatever layers parsed successfully and never raise to the caller.
- **ACL** (`_check_acl_drop` in datapath.py): 5-tuple wildcard-capable rules bucket-indexed by `(proto, dport)`; first match wins.
- **ML classifier** (`core/ml_classifier.py`): sklearn RandomForest (50 trees) over 8 flow features (packet-size stats, inter-arrival, jitter, throughput, pps). Pre-seeded with synthetic Gaussian archetypes (voice/video/bulk) so it works with zero training data; flows remain BEST_EFFORT until ≥5 packets. `feedback()` collects labels and retrains every 500 samples. Thread-safe FlowTable with idle expiry is shared here and by the API.
- **HW offload** (`core/hw_offload.py`): models real silicon decision logic (Qualcomm NSS style) — classifies packets SIMPLE/MODERATE/COMPLEX/EXCEPTION against a simulated 8192-entry flow cache. Simple learned flows take `HW_NIC_OFFLOAD` and skip software forwarding entirely; fragments/options/tunnels force the CPU slow path.
- **Deliberate slowness**: the full CPU path costs ~13 ms/pkt (asyncio locking + sklearn inference). This is intentional — the project's point is that 80–100% of packets bypass it via the fast path, mirroring real HW offloads. Do not "fix" this trade-off.
- **QoS** (`core/qos.py`): TokenBucket + DualTokenBucket (RFC 2697 two-rate three-color marker), HTBScheduler (Linux `tc qdisc htb` algorithm), strict-priority scheduler with deficit round robin. Port-based rules mark DSCP (SIP→EF, SSH→CS6, HTTPS→AF41…).
- **API** (`api/app.py`): FastAPI REST endpoints (`/api/stats`, `/api/flows`, `/api/acl`, `/api/qos/policy`, generator start/stop) plus `WS /ws/live` pushing a full stats snapshot every 100 ms.
- **Frontend** (`web/`): React 18 + TypeScript + Vite + Tailwind, zustand for state, three.js/@react-three/fiber. `web/src/context.tsx` holds the WebSocket client with auto-reconnect; components render stat cards, charts, flow table, gauges.
- **Docker roles** (`docker/`): `ap/main.py` runs the NetPath bridge, `sta/main.py` generates traffic, `server/main.py` consumes analytics.

## Testing Expectations

- Parser changes: extend the hex-fixture unit tests in `tests/unit/test_parser.py`; Hypothesis fuzz properties must keep passing (no exception may escape `parse_packet()`).
- Datapath behavior changes need coverage in `tests/unit/test_datapath.py` (bridge MAC learning/flooding, router longest-prefix/TTL, ACL semantics) or `tests/integration/test_pipeline.py`.
