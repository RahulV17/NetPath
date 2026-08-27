"""Unit tests for ML classifier and HW offload."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from network_lab.core.hw_offload import (
    OffloadEngine,
    OffloadTarget,
    PacketComplexity,
    PacketProfile,
    profile_from_parsed_packet,
)
from network_lab.core.ml_classifier import (
    FlowFeatures,
    FlowTable,
    TrafficClass,
    TrafficClassifier,
)
from network_lab.core.parser import parse_packet


class TestFlowFeatures:
    def test_to_vector_insufficient_data(self):
        flow = FlowFeatures("10.0.0.1", "10.0.0.2", 12345, 80, 6)
        vec = flow.to_vector()
        assert vec.shape == (1, 8) or vec.shape == (8,)
        assert np.all(vec == 0)

    def test_to_vector_with_data(self):
        flow = FlowFeatures("10.0.0.1", "10.0.0.2", 12345, 80, 6)
        for _ in range(10):
            flow.packet_sizes.append(100)
            flow.inter_arrival_times.append(0.02)
        vec = flow.to_vector()
        assert vec.shape == (1, 8) or vec.shape == (8,)
        assert not np.all(vec == 0)


class TestTrafficClassifier:
    @pytest.mark.asyncio
    async def test_initial_classification(self):
        """Pre-seeded model should classify without training."""
        classifier = TrafficClassifier()
        flow = FlowFeatures("10.0.0.1", "10.0.0.2", 12345, 80, 6)
        # Add voice-like features
        for _ in range(10):
            flow.packet_sizes.append(200)
            flow.inter_arrival_times.append(0.02)
        tc = await classifier.classify(flow)
        assert isinstance(tc, TrafficClass)

    @pytest.mark.asyncio
    async def test_feedback_and_retrain(self):
        """Feedback should improve model over time."""
        classifier = TrafficClassifier(retrain_interval=10)
        flow = FlowFeatures("10.0.0.1", "10.0.0.2", 12345, 80, 6)
        for _ in range(20):
            flow.packet_sizes.append(200)
            flow.inter_arrival_times.append(0.02)
        # Provide feedback
        for _ in range(15):
            await classifier.feedback(flow, TrafficClass.VOICE)
        # Model should still work
        tc = await classifier.classify(flow)
        assert isinstance(tc, TrafficClass)


class TestFlowTable:
    @pytest.mark.asyncio
    async def test_get_or_create(self):
        ft = FlowTable()
        flow = await ft.get_or_create("10.0.0.1", "10.0.0.2", 12345, 80, 6)
        assert flow.src_ip == "10.0.0.1"
        assert flow.dst_port == 80

    @pytest.mark.asyncio
    async def test_update(self):
        ft = FlowTable()
        flow = await ft.get_or_create("10.0.0.1", "10.0.0.2", 12345, 80, 6)
        await ft.update(flow, 100, 1.0)
        assert flow.total_packets == 1
        assert flow.total_bytes == 100

    @pytest.mark.asyncio
    async def test_cleanup(self):
        ft = FlowTable(timeout_sec=0.1)
        flow = await ft.get_or_create("10.0.0.1", "10.0.0.2", 12345, 80, 6)
        await ft.update(flow, 100, 1.0)
        await asyncio.sleep(0.15)
        removed = await ft.cleanup()
        assert removed >= 1


class TestOffloadEngine:
    def test_classify_complexity_simple(self):
        engine = OffloadEngine()
        profile = PacketProfile(
            ethertype=0x0800, ip_protocol=6, has_vlan=False,
            is_fragment=False, has_ip_options=False, has_tcp_options=False,
            is_tunnel=False, is_encrypted=False, payload_size=64, dscp=0
        )
        assert engine.classify_complexity(profile) == PacketComplexity.SIMPLE

    def test_classify_complexity_exception(self):
        engine = OffloadEngine()
        profile = PacketProfile(
            ethertype=0x0800, ip_protocol=99, has_vlan=False,
            is_fragment=False, has_ip_options=False, has_tcp_options=False,
            is_tunnel=False, is_encrypted=False, payload_size=64, dscp=0
        )
        assert engine.classify_complexity(profile) == PacketComplexity.EXCEPTION

    def test_decide_offload_new_flow(self):
        engine = OffloadEngine(flow_cache_size=100)
        profile = PacketProfile(
            ethertype=0x0800, ip_protocol=6, has_vlan=False,
            is_fragment=False, has_ip_options=False, has_tcp_options=False,
            is_tunnel=False, is_encrypted=False, payload_size=64, dscp=0
        )
        target = engine.decide_offload(profile, "flow1")
        assert target == OffloadTarget.HW_NIC_OFFLOAD

    def test_decide_offload_cached_flow(self):
        engine = OffloadEngine(flow_cache_size=100)
        profile = PacketProfile(
            ethertype=0x0800, ip_protocol=6, has_vlan=False,
            is_fragment=False, has_ip_options=False, has_tcp_options=False,
            is_tunnel=False, is_encrypted=False, payload_size=64, dscp=0
        )
        engine.decide_offload(profile, "flow1")
        target = engine.decide_offload(profile, "flow1")
        assert target == OffloadTarget.HW_NIC_OFFLOAD

    def test_decide_offload_exception(self):
        engine = OffloadEngine()
        profile = PacketProfile(
            ethertype=0x0800, ip_protocol=99, has_vlan=False,
            is_fragment=False, has_ip_options=False, has_tcp_options=False,
            is_tunnel=False, is_encrypted=False, payload_size=64, dscp=0
        )
        target = engine.decide_offload(profile, "flow1")
        assert target == OffloadTarget.CPU_SLOW_PATH

    def test_cache_eviction(self):
        engine = OffloadEngine(flow_cache_size=2)
        profile = PacketProfile(
            ethertype=0x0800, ip_protocol=6, has_vlan=False,
            is_fragment=False, has_ip_options=False, has_tcp_options=False,
            is_tunnel=False, is_encrypted=False, payload_size=64, dscp=0
        )
        engine.decide_offload(profile, "flow1")
        engine.decide_offload(profile, "flow2")
        # Cache full — next flow should go to CPU fast path
        target = engine.decide_offload(profile, "flow3")
        assert target == OffloadTarget.CPU_FAST_PATH

    def test_get_stats(self):
        engine = OffloadEngine()
        profile = PacketProfile(
            ethertype=0x0800, ip_protocol=6, has_vlan=False,
            is_fragment=False, has_ip_options=False, has_tcp_options=False,
            is_tunnel=False, is_encrypted=False, payload_size=64, dscp=0
        )
        engine.decide_offload(profile, "flow1")
        stats = engine.get_stats()
        assert stats["hw_accelerated"] == 1
        assert stats["hw_offload_rate"] == 1.0


class TestProfileFromParsedPacket:
    def test_ipv4_tcp(self):
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "4500002800000000400600000a0000640a000001"
            "3039005000000001000000005002200000000000"
        )
        pkt = parse_packet(raw)
        profile = profile_from_parsed_packet(pkt)
        assert profile.is_ipv4
        assert profile.ip_protocol == 6
        assert not profile.has_vlan
        assert not profile.is_tunnel

    def test_vlan_tagged(self):
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "8100" "0064" "0800"
            "4500002800000000400600000a0000640a000001"
            "3039005000000001000000005002200000000000"
        )
        pkt = parse_packet(raw)
        profile = profile_from_parsed_packet(pkt)
        assert profile.has_vlan

    def test_vxlan_tunnel(self):
        # VXLAN packet: Eth + IP + UDP + VXLAN + inner Eth
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"  # Outer Eth
            "4500004000000000401100000a0000010a000002"  # Outer IP
            "303912b5002c0000"  # Outer UDP (src=12345, dst=4789)
            "0800000000001388"  # VXLAN header (flags=8, vni=5000)
            "ffffffffffff" "0011223344aa" "0800"  # Inner Eth
            "450000140000000040000000c0a8010ac0a80114"  # Inner IP (minimal)
        )
        pkt = parse_packet(raw)
        profile = profile_from_parsed_packet(pkt)
        # VXLAN is detected via UDP port 4789 in payload
        assert profile.is_tunnel or pkt.vxlan is not None
