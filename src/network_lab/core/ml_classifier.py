"""ML Classifier — AI/ML based smart traffic management.

Adapted from PyFlow project. Trains on flow features and dynamically
assigns QoS policies. Demonstrates the "AI/ML based smart traffic
management" requirement from the Qualcomm JD.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler


class TrafficClass(Enum):
    """QoS traffic classes aligned with DSCP/802.11e."""
    VOICE = auto()       # VoIP, low latency
    VIDEO = auto()       # Streaming, consistent bandwidth
    INTERACTIVE = auto() # Gaming, RDP — low jitter
    BULK = auto()        # File transfer, backup
    BEST_EFFORT = auto() # Default


@dataclass
class FlowFeatures:
    """Per-flow statistical features for ML classification."""
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: int  # 6=TCP, 17=UDP

    # Time-series features (rolling window)
    packet_sizes: deque = field(default_factory=lambda: deque(maxlen=100))
    inter_arrival_times: deque = field(default_factory=lambda: deque(maxlen=100))

    # Derived features
    total_bytes: int = 0
    total_packets: int = 0
    start_time: float = field(default_factory=time.time)
    last_time: float = field(default_factory=time.time)

    def to_vector(self) -> np.ndarray:
        """Convert flow statistics to ML feature vector (always (1, 8))."""
        if len(self.packet_sizes) < 5:
            return np.zeros((1, 8))

        sizes = np.array(self.packet_sizes)
        iats = np.array(self.inter_arrival_times)
        duration = max(time.time() - self.start_time, 0.001)

        features = [
            np.mean(sizes),           # avg packet size
            np.std(sizes),            # packet size variance
            np.min(sizes),            # min packet size
            np.max(sizes),            # max packet size
            np.mean(iats) * 1000,     # avg inter-arrival (ms)
            np.std(iats) * 1000,      # jitter (ms)
            self.total_bytes / duration,  # throughput
            self.total_packets / duration, # pps
        ]
        return np.array(features).reshape(1, -1)


class TrafficClassifier:
    """
    Online traffic classifier using Random Forest.

    Simulates the "AI/ML based smart traffic management" requirement
    from the Qualcomm JD. Runs in the control plane and pushes
    classification results to the data path via shared state.
    """

    def __init__(self, retrain_interval: int = 500):
        self.model: Optional[RandomForestClassifier] = None
        self.scaler = StandardScaler()
        self.retrain_interval = retrain_interval
        self._samples: List[np.ndarray] = []
        self._labels: List[int] = []
        self._sample_count = 0
        self._lock = asyncio.Lock()

        # Pre-seed with synthetic training data so it works out-of-box
        self._seed_model()

    def _seed_model(self) -> None:
        """Seed with synthetic labeled data representing typical traffic patterns."""
        np.random.seed(42)

        # Voice: small packets (~200B), regular intervals (~20ms)
        voice = np.column_stack([
            np.random.normal(200, 30, 50),   # avg size
            np.random.normal(20, 5, 50),     # size std
            np.full(50, 150),                 # min
            np.full(50, 250),                 # max
            np.random.normal(20, 2, 50),      # IAT mean (ms)
            np.random.normal(2, 1, 50),       # jitter
            np.random.normal(80000, 10000, 50), # throughput
            np.random.normal(50, 5, 50),      # pps
        ])

        # Video: larger packets (~1200B), bursty
        video = np.column_stack([
            np.random.normal(1200, 200, 50),
            np.random.normal(300, 100, 50),
            np.full(50, 800),
            np.full(50, 1500),
            np.random.normal(8, 3, 50),
            np.random.normal(5, 2, 50),
            np.random.normal(1200000, 200000, 50),
            np.random.normal(100, 20, 50),
        ])

        # Bulk: huge packets, irregular
        bulk = np.column_stack([
            np.random.normal(1400, 100, 50),
            np.random.normal(50, 20, 50),
            np.full(50, 1300),
            np.full(50, 1500),
            np.random.normal(0.5, 0.2, 50),
            np.random.normal(0.1, 0.05, 50),
            np.random.normal(5000000, 1000000, 50),
            np.random.normal(400, 50, 50),
        ])

        X = np.vstack([voice, video, bulk])
        y = np.array([0]*50 + [1]*50 + [3]*50)  # VOICE=0, VIDEO=1, BULK=3

        self.scaler.fit(X)
        X_scaled = self.scaler.transform(X)
        self.model = RandomForestClassifier(n_estimators=50, max_depth=10, random_state=42)
        self.model.fit(X_scaled, y)
        self._sample_count = len(y)

    async def classify(self, flow: FlowFeatures) -> TrafficClass:
        """Classify a flow and return its traffic class."""
        async with self._lock:
            if self.model is None or len(flow.packet_sizes) < 5:
                return TrafficClass.BEST_EFFORT

            vec = flow.to_vector()
            vec_scaled = self.scaler.transform(vec)
            pred = self.model.predict(vec_scaled)[0]

            mapping = {
                0: TrafficClass.VOICE,
                1: TrafficClass.VIDEO,
                2: TrafficClass.INTERACTIVE,
                3: TrafficClass.BULK,
            }
            return mapping.get(pred, TrafficClass.BEST_EFFORT)

    async def feedback(self, flow: FlowFeatures, true_class: TrafficClass) -> None:
        """Online learning: incorporate ground truth for model improvement."""
        async with self._lock:
            vec = flow.to_vector()
            if np.all(vec == 0):
                return

            self._samples.append(vec[0])
            self._labels.append(true_class.value - 1)
            self._sample_count += 1

            if self._sample_count % self.retrain_interval == 0:
                await self._retrain()

    async def _retrain(self) -> None:
        """Retrain on seed archetypes ∪ feedback.

        Training on feedback alone caused catastrophic forgetting of the
        150 seed samples — the model drifted toward whatever it recently
        saw. Mixing seeds back in preserves the base distributions.
        """
        if len(self._samples) < 100:
            return

        # Re-generate seed data and mix with feedback (feedback weighted 2x)
        np.random.seed(42)
        voice = self._seed_archetype(200, 30, 150, 250, 20, 2, 80000, 10000, 50, 5)
        video = self._seed_archetype(1200, 200, 800, 1500, 8, 3, 1200000, 200000, 100, 20)
        bulk = self._seed_archetype(1400, 100, 1300, 1500, 0.5, 0.1, 5000000, 1000000, 400, 50)
        X_seed = np.vstack([voice, video, bulk])
        y_seed = np.array([0] * 50 + [1] * 50 + [3] * 50)

        X_fb = np.array(self._samples * 2)  # feedback emphasized
        y_fb = np.array(self._labels * 2)

        X = np.vstack([X_seed, X_fb])
        y = np.concatenate([y_seed, y_fb])
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled, y)
        print(f"[ML] Retrained on {len(X)} samples ({len(self._samples)} feedback)")

    @staticmethod
    def _seed_archetype(mean_s, std_s, min_s, max_s, mean_iat, std_iat,
                        thr, thr_std, pps_m, pps_std, n=50):
        return np.column_stack([
            np.random.normal(mean_s, std_s, n),
            np.random.normal(std_s, std_s * 0.25, n),
            np.full(n, min_s),
            np.full(n, max_s),
            np.random.normal(mean_iat, std_iat * 0.4, n),
            np.random.normal(std_iat, std_iat * 0.5, n),
            np.random.normal(thr, thr_std, n),
            np.random.normal(pps_m, pps_std, n),
        ])


class FlowTable:
    """Thread-safe flow table with automatic expiry."""

    def __init__(self, timeout_sec: float = 60.0):
        self._flows: Dict[str, FlowFeatures] = {}
        self._classifications: Dict[str, TrafficClass] = {}
        self._timeout = timeout_sec
        self._lock = asyncio.Lock()

    def _flow_key(self, src_ip: str, dst_ip: str, src_port: int,
                   dst_port: int, proto: int) -> str:
        return f"{src_ip}:{src_port}-{dst_ip}:{dst_port}/{proto}"

    async def get_or_create(self, src_ip: str, dst_ip: str, src_port: int,
                           dst_port: int, proto: int) -> FlowFeatures:
        key = self._flow_key(src_ip, dst_ip, src_port, dst_port, proto)
        async with self._lock:
            if key not in self._flows:
                self._flows[key] = FlowFeatures(src_ip, dst_ip, src_port, dst_port, proto)
            return self._flows[key]

    async def update(self, flow: FlowFeatures, packet_size: int,
                     arrival_time: float) -> None:
        """Update flow statistics with new packet.

        Mutates flow state under the lock — FlowTable is advertised as
        thread-safe (CLAUDE.md) but update() previously ran unlocked while
        get_or_create/set_classification held it, risking interleaved
        list.append / dict writes across coroutines.
        """
        async with self._lock:
            flow.packet_sizes.append(packet_size)
            if len(flow.inter_arrival_times) > 0:
                flow.inter_arrival_times.append(arrival_time - flow.last_time)
            else:
                flow.inter_arrival_times.append(0.0)
            flow.last_time = arrival_time
            flow.total_bytes += packet_size
            flow.total_packets += 1

    async def set_classification(self, flow: FlowFeatures,
                                   tc: TrafficClass) -> None:
        key = self._flow_key(flow.src_ip, flow.dst_ip, flow.src_port,
                            flow.dst_port, flow.protocol)
        async with self._lock:
            self._classifications[key] = tc

    async def get_classification(self, flow: FlowFeatures) -> Optional[TrafficClass]:
        key = self._flow_key(flow.src_ip, flow.dst_ip, flow.src_port,
                            flow.dst_port, flow.protocol)
        async with self._lock:
            return self._classifications.get(key)

    async def cleanup(self) -> int:
        """Remove expired flows. Returns count removed."""
        now = time.time()
        removed = 0
        async with self._lock:
            expired = [k for k, f in self._flows.items()
                      if now - f.last_time > self._timeout]
            for k in expired:
                del self._flows[k]
                self._classifications.pop(k, None)
                removed += 1
        return removed

    @property
    def size(self) -> int:
        return len(self._flows)

    def list_flows(self) -> List[FlowFeatures]:
        """Snapshot of active flows (API/UI safe).

        Sync public accessor over an asyncio-locked dict. We copy the
        values list once; this avoids 'dictionary changed size during
        iteration' in telemetry callers. Concurrent mutation of an
        individual flow object is still possible, but FlowFeatures fields
        are read for display only and update() holds the lock for writes,
        so this is safe in practice.
        """
        return list(self._flows.values())

    def snapshot(self, top: int = 5) -> dict:
        """Rich flow telemetry for API/WebSocket consumers.

        Returns counts, per-class distribution, and the busiest flows
        with their ML classifications resolved from _classifications.
        """
        now = time.time()
        classified: Dict[str, int] = {}
        rows: List[dict] = []
        # Iterate a snapshot under the lock — the live dict mutates from
        # other coroutines (update/get_or_create), which raised
        # "dictionary changed size during iteration" under load.
        with self._lock:
            items = list(self._flows.items())
        for key, f in items:
            tc = self._classifications.get(key)
            tc_name = tc.name if tc else "UNCLASSIFIED"
            classified[tc_name] = classified.get(tc_name, 0) + 1
            rows.append({
                "src": f"{f.src_ip}:{f.src_port}",
                "dst": f"{f.dst_ip}:{f.dst_port}",
                "proto": f.protocol,
                "packets": f.total_packets,
                "bytes": f.total_bytes,
                "age_sec": round(now - f.start_time, 1),
                "avg_size": round(f.total_bytes / max(f.total_packets, 1)),
                "class": tc_name,
            })
        rows.sort(key=lambda r: r["packets"], reverse=True)
        return {
            "count": len(self._flows),
            "classified": classified,
            "top": rows[:top],
        }
