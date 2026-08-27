"""Traffic Manager — QoS scheduling, shaping, and policing.

Implements token bucket, HTB (Hierarchical Token Bucket), DSCP remarking,
and priority queue scheduling.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import IntEnum

from .parser import ParsedPacket


# ── DSCP Classes ─────────────────────────────────────────────────────────
class DSCP(IntEnum):
    """DiffServ Code Point values (6 bits)."""
    CS0 = 0    # Best Effort
    CS1 = 8    # Scavenger
    AF11 = 10  # Bronze
    AF21 = 18  # Silver
    AF31 = 26  # Gold
    AF41 = 34  # Platinum
    EF = 46    # Expedited Forwarding (VoIP)
    CS6 = 48   # Network Control
    CS7 = 56   # Network Control


# ── Priority Levels ──────────────────────────────────────────────────────
class Priority(IntEnum):
    """Priority levels for scheduling."""
    CRITICAL = 0    # Network control
    VOICE = 1       # VoIP (EF)
    VIDEO = 2       # Video streaming
    BEST_EFFORT = 3 # Normal data
    BULK = 4        # Background transfers
    SCAVENGER = 5   # Lowest


# ═══════════════════════════════════════════════════════════════════════════
# TOKEN BUCKET SHAPER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TokenBucket:
    """Token bucket rate limiter — classic algorithm.

    Tokens are added at `rate` bytes/sec. A packet can be sent only if
    there are enough tokens. The bucket can hold at most `burst` tokens.
    """
    rate: float         # tokens per second (bytes/sec)
    burst: int          # max tokens (bucket capacity)
    _tokens: float = field(default=0, repr=False)
    _last_time: float = field(default=0, repr=False)
    _queue: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.burst)
        self._last_time = time.monotonic()

    def _replenish(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_time
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_time = now

    def consume(self, size: int) -> bool:
        """Try to consume `size` tokens. Returns True if allowed."""
        self._replenish()
        if self._tokens >= size:
            self._tokens -= size
            return True
        return False

    def consume_or_wait(self, size: int) -> float:
        """Returns wait time until enough tokens are available."""
        self._replenish()
        if self._tokens >= size:
            return 0.0
        deficit = size - self._tokens
        return deficit / self.rate

    @property
    def available_tokens(self) -> float:
        self._replenish()
        return self._tokens


class DualTokenBucket:
    """Two-rate three-color marker (for traffic policing).

    Committed Information Rate (CIR) + Peak Information Rate (PIR).
    Colors: green (within CIR), yellow (within PIR), red (exceeds PIR).
    """
    def __init__(self, cir: float, cbs: int, pir: float, pbs: int):
        self.committed = TokenBucket(cir, cbs)
        self.peak = TokenBucket(pir, pbs)

    def color(self, size: int) -> str:
        """Classify packet color (RFC 2698 trTCM).

        Peak bucket is checked FIRST — traffic exceeding PIR must be red
        without consuming committed tokens. Checking CIR first let
        out-of-contract traffic through as green.
        """
        if not self.peak.consume(size):
            return "red"
        if self.committed.consume(size):
            return "green"
        return "yellow"


# ═══════════════════════════════════════════════════════════════════════════
# HTB (HIERARCHICAL TOKEN BUCKET)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class HTBClass:
    """HTB class — hierarchical bandwidth allocation.

    rate: guaranteed rate
    ceil: maximum rate (can borrow above rate up to ceil)
    priority: lower = higher priority for borrowing
    """
    class_id: int
    rate: float      # bytes/sec
    ceil: float      # bytes/sec
    burst: int = 1520
    cburst: int = 1520
    priority: int = 0
    # Token buckets for rate and ceil
    _tokens: float = field(default=0, repr=False)
    _ctokens: float = field(default=0, repr=False)
    _last_time: float = field(default=0, repr=False)
    children: list[HTBClass] = field(default_factory=list)
    parent: HTBClass | None = None

    def __post_init__(self) -> None:
        self._last_time = time.monotonic()
        self._tokens = self.burst
        self._ctokens = self.cburst

    def _replenish(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_time
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._ctokens = min(self.cburst, self._ctokens + elapsed * self.ceil)
        self._last_time = now

    def can_send(self, size: int) -> bool:
        self._replenish()
        return self._ctokens >= size

    def deduct(self, size: int) -> None:
        self._tokens -= size
        self._ctokens -= size


class HTBScheduler:
    """Hierarchical Token Bucket scheduler — Linux HTB in Python.

    Structure:
        root (total bandwidth)
        ├── class 1: VoIP (guaranteed 10%, ceil 20%)
        ├── class 2: Video (guaranteed 30%, ceil 50%)
        └── class 3: Best Effort (guaranteed 40%, ceil 100%)
    """

    def __init__(self, root_rate: float, root_ceil: float):
        self.root = HTBClass(
            class_id=0, rate=root_rate, ceil=root_ceil,
            burst=int(root_rate * 0.01), cburst=int(root_ceil * 0.01)
        )
        self.classes: dict[int, HTBClass] = {0: self.root}
        self._queues: dict[int, deque[ParsedPacket]] = defaultdict(deque)

    def add_class(self, class_id: int, rate: float, ceil: float,
                  parent_id: int = 0, priority: int = 0) -> HTBClass:
        cls = HTBClass(
            class_id=class_id, rate=rate, ceil=ceil, priority=priority
        )
        parent = self.classes[parent_id]
        cls.parent = parent
        parent.children.append(cls)
        self.classes[class_id] = cls
        return cls

    def enqueue(self, class_id: int, pkt: ParsedPacket) -> None:
        self._queues[class_id].append(pkt)

    def dequeue(self) -> tuple[ParsedPacket, int] | None:
        """Dequeue next packet using HTB borrowing algorithm."""
        # Try classes in priority order
        for cls in sorted(self.classes.values(), key=lambda c: c.priority):
            if cls.class_id == 0:
                continue  # skip root
            if self._queues[cls.class_id] and cls.can_send(1500):
                pkt = self._queues[cls.class_id].popleft()
                cls.deduct(len(pkt.raw))
                return pkt, cls.class_id
        return None

    @property
    def queue_depths(self) -> dict[int, int]:
        return {cid: len(q) for cid, q in self._queues.items()}


# ═══════════════════════════════════════════════════════════════════════════
# PRIORITY QUEUE SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════

class PriorityScheduler:
    """Strict priority queue scheduler with weighted deficit round-robin fallback.

    6 priority levels (CRITICAL to SCAVENGER). Higher priority always
    served first. Within same priority, uses deficit round-robin.
    """

    def __init__(self, weights: list[int] | None = None):
        # One weight per priority level (6) — fewer caused KeyError on
        # BULK/SCAVENGER dequeue.
        self.weights = weights or [8, 4, 2, 1, 1, 1]
        self._queues: dict[int, deque[ParsedPacket]] = {
            p: deque() for p in range(6)
        }
        self._deficit: dict[int, float] = {p: 0.0 for p in range(6)}
        self._quantum: dict[int, float] = {
            p: w * 1500 for p, w in enumerate(self.weights)
        }
        self._max_queue = 256  # per-priority drop-tail bound
        self.dropped_count = 0

    def enqueue(self, priority: int, pkt: ParsedPacket) -> None:
        """Enqueue with drop-tail bounding.

        The engine forwards packets inline and never drains these queues;
        without a bound every forwarded packet leaked into memory
        indefinitely (~360k/hour at default rate).
        """
        q = self._queues[priority]
        if len(q) >= self._max_queue:
            q.popleft()  # drop-tail: discard oldest
            self.dropped_count += 1
        q.append(pkt)

    def dequeue(self) -> tuple[ParsedPacket, int] | None:
        """Dequeue using strict priority with DRR within level."""
        for prio in range(6):
            if self._queues[prio]:
                self._deficit[prio] += self._quantum[prio]
                while self._queues[prio]:
                    pkt = self._queues[prio][0]
                    size = len(pkt.raw)
                    if size <= self._deficit[prio]:
                        self._deficit[prio] -= size
                        self._queues[prio].popleft()
                        return pkt, prio
                    else:
                        break  # move to next priority
        return None

    @property
    def queue_depths(self) -> dict[int, int]:
        return {p: len(q) for p, q in self._queues.items()}


# ═══════════════════════════════════════════════════════════════════════════
# FLOW CLASSIFIER → maps flows to priority/DSCP
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FlowRule:
    """Classification rule for traffic."""
    name: str
    priority: int
    dscp: int
    rate_limit_mbps: float | None = None
    # Match conditions
    dst_port: int | None = None
    src_port: int | None = None
    protocol: int | None = None
    dscp_match: int | None = None


class FlowClassifier:
    """Classify packets into priority queues based on rules."""

    def __init__(self):
        self.rules: list[FlowRule] = []
        self._default_priority = Priority.BEST_EFFORT
        self._default_dscp = DSCP.CS0

    def add_rule(self, rule: FlowRule) -> None:
        self.rules.append(rule)

    def classify(self, pkt: ParsedPacket) -> tuple[int, int]:
        """Returns (priority, dscp) for a packet."""
        for rule in self.rules:
            if self._matches(pkt, rule):
                return rule.priority, rule.dscp
        return self._default_priority, self._default_dscp

    def _matches(self, pkt: ParsedPacket, rule: FlowRule) -> bool:
        if rule.protocol is not None:
            if pkt.ipv4 and pkt.ipv4.protocol != rule.protocol:
                return False
        # Port rules require a transport layer — ARP/ICMP/raw frames must
        # not match port-based rules (previously matched the first rule).
        if rule.dst_port is not None or rule.src_port is not None:
            dport = pkt.tcp.dst_port if pkt.tcp else (
                pkt.udp.dst_port if pkt.udp else None)
            sport = pkt.tcp.src_port if pkt.tcp else (
                pkt.udp.src_port if pkt.udp else None)
            if rule.dst_port is not None and dport != rule.dst_port:
                return False
            if rule.src_port is not None and sport != rule.src_port:
                return False
        if rule.dscp_match is not None:
            if pkt.ipv4 and pkt.ipv4.dscp != rule.dscp_match:
                return False
        return True


# ═══════════════════════════════════════════════════════════════════════════
# TRAFFIC MANAGER (combines all QoS components)
# ═══════════════════════════════════════════════════════════════════════════

class TrafficManager:
    """Main QoS engine — classifies, shapes, and schedules traffic."""

    def __init__(self, total_bandwidth_mbps: float = 1000.0):
        self.total_bandwidth = total_bandwidth_mbps * 1_000_000 / 8  # bytes/sec
        self.classifier = FlowClassifier()
        self.scheduler = HTBScheduler(self.total_bandwidth, self.total_bandwidth)
        self.prio_scheduler = PriorityScheduler()
        self._shapers: dict[int, TokenBucket] = {}
        # Whether a dequeue consumer drains prio_scheduler. Default True so
        # TrafficManager users get classify→shape→schedule round-trip
        # (the CLI dashboard relies on it). The inline DataPathEngine sets
        # this False because it forwards packets itself and never dequeues.
        self._drain_active = True
        self._stats = {
            "classified": 0,
            "shaped": 0,
            "policed": 0,
            "dropped": 0,
            "last_priority": "BEST_EFFORT",
        }
        self._setup_default_rules()

    def _setup_default_rules(self) -> None:
        """Default classification rules."""
        self.classifier.add_rule(FlowRule(
            name="VoIP", priority=Priority.VOICE, dscp=DSCP.EF,
            dst_port=5060, protocol=17  # SIP
        ))
        self.classifier.add_rule(FlowRule(
            name="RTP", priority=Priority.VOICE, dscp=DSCP.EF,
            dst_port=10000, protocol=17  # RTP range
        ))
        self.classifier.add_rule(FlowRule(
            name="Video", priority=Priority.VIDEO, dscp=DSCP.AF41,
            dst_port=443, protocol=6  # HTTPS streaming
        ))
        self.classifier.add_rule(FlowRule(
            name="SSH", priority=Priority.CRITICAL, dscp=DSCP.CS6,
            dst_port=22, protocol=6
        ))
        self.classifier.add_rule(FlowRule(
            name="DNS", priority=Priority.CRITICAL, dscp=DSCP.CS6,
            dst_port=53, protocol=17
        ))
        self.classifier.add_rule(FlowRule(
            name="Bulk", priority=Priority.BULK, dscp=DSCP.CS1,
            dst_port=21, protocol=6  # FTP
        ))

    def add_shaper(self, flow_key: tuple, rate_mbps: float, burst_kb: int = 64) -> None:
        """Add a token bucket shaper for a specific flow."""
        self._shapers[flow_key] = TokenBucket(
            rate=rate_mbps * 1_000_000 / 8,
            burst=burst_kb * 1024
        )

    def process(self, pkt: ParsedPacket) -> tuple[ParsedPacket, bool]:
        """Process packet through QoS pipeline.

        Returns (packet, should_forward).

        NOTE: DataPathEngine forwards packets inline (it never calls
        dequeue()), so enqueuing into prio_scheduler would grow the
        queues unbounded — the 256 drop-tail bound merely converts that
        into silent packet loss. Instead we classify + police, count the
        scheduling decision, and return. The DRR scheduler is still fully
        exercised by the CLI dashboard, which runs a real dequeue loop.
        """
        self._stats["classified"] += 1

        # 1. Classify
        priority, dscp = self.classifier.classify(pkt)
        self._stats["last_priority"] = priority.name

        # 2. Police (token bucket per-flow)
        if pkt.flow_key and pkt.flow_key in self._shapers:
            shaper = self._shapers[pkt.flow_key]
            if not shaper.consume(len(pkt.raw)):
                self._stats["policed"] += 1
                return pkt, False  # drop

        # 3. Schedule — only enqueue when a drain worker exists
        # (the CLI dashboard wires one via dequeue(); the inline datapath
        # does not, so we skip the no-op enqueue to avoid queue growth).
        if self._drain_active:
            self.prio_scheduler.enqueue(priority, pkt)
            self._stats["shaped"] += 1
        else:
            self._stats["shaped"] += 1
        return pkt, True

    def dequeue(self) -> tuple[ParsedPacket, int] | None:
        """Get next packet from scheduler (used by the CLI drain loop)."""
        return self.prio_scheduler.dequeue()

    def enable_drain(self) -> None:
        """Mark that a background dequeue loop is consuming the scheduler."""
        self._drain_active = True

    @property
    def stats(self) -> dict:
        return {
            **self._stats,
            "queue_depths": self.prio_scheduler.queue_depths,
        }
