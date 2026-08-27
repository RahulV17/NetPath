"""Unit tests for QoS traffic manager."""

from __future__ import annotations

import pytest

from network_lab.core.qos import (
    DSCP,
    DualTokenBucket,
    FlowClassifier,
    FlowRule,
    HTBScheduler,
    HTBClass,
    Priority,
    PriorityScheduler,
    TokenBucket,
    TrafficManager,
)
from network_lab.core.parser import parse_packet


class TestTokenBucket:
    def test_allows_within_burst(self):
        bucket = TokenBucket(rate=1000, burst=5000)
        assert bucket.consume(1000) is True
        assert bucket.consume(2000) is True
        assert bucket.consume(2000) is True

    def test_blocks_when_empty(self):
        bucket = TokenBucket(rate=1000, burst=1000)
        assert bucket.consume(1000) is True
        assert bucket.consume(1) is False

    def test_replenishes_over_time(self):
        bucket = TokenBucket(rate=10000, burst=1000)
        bucket.consume(1000)  # empty
        assert bucket.consume(1) is False
        # Wait for replenishment
        import time
        time.sleep(0.2)
        assert bucket.consume(1000) is True

    def test_consume_or_wait(self):
        bucket = TokenBucket(rate=1000, burst=1000)
        bucket.consume(1000)
        wait = bucket.consume_or_wait(1000)
        assert wait > 0  # Should need to wait


class TestDualTokenBucket:
    def test_green(self):
        dtb = DualTokenBucket(cir=1000, cbs=2000, pir=2000, pbs=4000)
        assert dtb.color(500) == "green"

    def test_yellow(self):
        dtb = DualTokenBucket(cir=1000, cbs=1000, pir=2000, pbs=2000)
        # Drain the committed bucket first so packet exceeds CIR but fits PIR
        assert dtb.committed.consume(1000) is True
        assert dtb.color(500) == "yellow"

    def test_red(self):
        dtb = DualTokenBucket(cir=1000, cbs=1000, pir=1000, pbs=1000)
        # Drain both buckets so packet exceeds PIR
        assert dtb.committed.consume(1000) is True
        assert dtb.peak.consume(1000) is True
        assert dtb.color(500) == "red"


class TestHTBClass:
    def test_can_send_within_rate(self):
        cls = HTBClass(class_id=1, rate=1000, ceil=2000, burst=1520)
        assert cls.can_send(1000) is True

    def test_cannot_exceed_ceil(self):
        cls = HTBClass(class_id=1, rate=1000, ceil=1000, burst=1000, cburst=1000)
        cls.deduct(1000)
        assert cls.can_send(1) is False


class TestHTBScheduler:
    def test_add_class(self):
        sched = HTBScheduler(root_rate=10000, root_ceil=10000)
        cls = sched.add_class(1, rate=1000, ceil=2000)
        assert cls.class_id == 1
        assert cls in sched.root.children

    def test_dequeue_empty(self):
        sched = HTBScheduler(root_rate=10000, root_ceil=10000)
        assert sched.dequeue() is None


class TestPriorityScheduler:
    def test_strict_priority(self):
        sched = PriorityScheduler()
        # Enqueue low priority first
        raw = bytes.fromhex("ffffffffffff" "001122334455" "0800")
        pkt1 = parse_packet(raw)
        pkt2 = parse_packet(raw)
        sched.enqueue(Priority.BULK, pkt1)
        sched.enqueue(Priority.VOICE, pkt2)
        # Should dequeue voice first
        result = sched.dequeue()
        assert result is not None
        assert result[1] == Priority.VOICE

    def test_fifo_within_priority(self):
        sched = PriorityScheduler()
        raw = bytes.fromhex("ffffffffffff" "001122334455" "0800")
        pkt1 = parse_packet(raw)
        pkt2 = parse_packet(raw)
        sched.enqueue(Priority.BEST_EFFORT, pkt1)
        sched.enqueue(Priority.BEST_EFFORT, pkt2)
        result1 = sched.dequeue()
        result2 = sched.dequeue()
        assert result1[0] is pkt1
        assert result2[0] is pkt2


class TestFlowClassifier:
    def test_default_classification(self):
        fc = FlowClassifier()
        raw = bytes.fromhex("ffffffffffff" "001122334455" "0800")
        pkt = parse_packet(raw)
        priority, dscp = fc.classify(pkt)
        assert priority == Priority.BEST_EFFORT
        assert dscp == DSCP.CS0

    def test_port_based_rule(self):
        fc = FlowClassifier()
        fc.add_rule(FlowRule(
            name="SSH", priority=Priority.CRITICAL, dscp=DSCP.CS6,
            dst_port=22, protocol=6
        ))
        # Build TCP SYN to port 22
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "45 00 0028 0000 0000 40 06 0000 0a000064 0a000001"
            "3039 0016 00000001 00000000 50 02 2000 0000 0000"
        )
        pkt = parse_packet(raw)
        priority, dscp = fc.classify(pkt)
        assert priority == Priority.CRITICAL
        assert dscp == DSCP.CS6


class TestTrafficManager:
    def test_process_packet(self):
        tm = TrafficManager(total_bandwidth_mbps=100)
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "45 00 0028 0000 0000 40 06 0000 0a000064 0a000001"
            "3039 0016 00000001 00000000 50 02 2000 0000 0000"
        )
        pkt = parse_packet(raw)
        result_pkt, should_fwd = tm.process(pkt)
        assert should_fwd is True
        assert tm.stats["classified"] == 1

    def test_rate_limiting(self):
        tm = TrafficManager(total_bandwidth_mbps=1)
        # Add a restrictive shaper with burst capacity for exactly one 54-byte packet
        tm._shapers[("10.0.0.100", "10.0.0.1", 12345, 80, 6)] = TokenBucket(rate=1, burst=60)
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "45 00 0028 0000 0000 40 06 0000 0a000064 0a000001"
            "3039 0050 00000001 00000000 50 02 2000 0000 0000"
        )
        pkt = parse_packet(raw)
        # First packet should pass
        _, should_fwd = tm.process(pkt)
        assert should_fwd is True
        # Second immediate packet exceeds available tokens and is policed/dropped
        _, should_fwd = tm.process(pkt)
        assert should_fwd is False
        assert tm.stats["policed"] == 1
