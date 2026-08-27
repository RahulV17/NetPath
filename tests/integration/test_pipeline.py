"""Integration tests using Docker topology."""

from __future__ import annotations

import pytest
import asyncio

from network_lab.core.datapath import Bridge, DataPathEngine, ForwardAction, Interface
from network_lab.core.generator import GeneratorConfig, PacketCrafter, TrafficGenerator
from network_lab.core.parser import parse_packet, parse_wifi_frame
from network_lab.core.qos import TrafficManager


class TestPacketCrafter:
    def test_arp_request(self):
        raw = PacketCrafter.arp_request()
        pkt = parse_packet(raw)
        assert pkt.arp is not None
        assert pkt.arp.opcode == 1

    def test_arp_reply(self):
        raw = PacketCrafter.arp_reply()
        pkt = parse_packet(raw)
        assert pkt.arp is not None
        assert pkt.arp.opcode == 2

    def test_ipv4_tcp_syn(self):
        raw = PacketCrafter.ipv4_tcp_syn()
        pkt = parse_packet(raw)
        assert pkt.ipv4 is not None
        assert pkt.tcp is not None
        assert "SYN" in pkt.tcp.flag_names

    def test_ipv4_udp(self):
        raw = PacketCrafter.ipv4_udp()
        pkt = parse_packet(raw)
        assert pkt.udp is not None

    def test_ipv4_icmp_echo(self):
        raw = PacketCrafter.ipv4_icmp_echo()
        pkt = parse_packet(raw)
        assert pkt.icmp is not None
        assert pkt.icmp.type == 8

    def test_ipv6_tcp(self):
        raw = PacketCrafter.ipv6_tcp()
        pkt = parse_packet(raw)
        assert pkt.ipv6 is not None
        assert pkt.tcp is not None

    def test_dhcp_discover(self):
        raw = PacketCrafter.dhcp_discover()
        pkt = parse_packet(raw)
        assert pkt.dhcp is not None
        assert pkt.dhcp.msg_type == 1

    def test_vxlan_encapsulated(self):
        raw = PacketCrafter.vxlan_encapsulated()
        pkt = parse_packet(raw)
        assert pkt.vxlan is not None
        assert pkt.vxlan.vni == 5000

    def test_gre_tunnel(self):
        raw = PacketCrafter.gre_tunnel()
        pkt = parse_packet(raw)
        assert pkt.gre is not None

    def test_wifi_beacon(self):
        raw = PacketCrafter.wifi_beacon()
        pkt = parse_wifi_frame(raw)
        assert pkt.wifi is not None
        assert pkt.wifi.type == 0  # management
        assert pkt.wifi.subtype == 8  # beacon


class TestEndToEnd:
    async def test_bridge_learning_and_forwarding(self):
        """Full bridge E2E: pure-L2 frames flood, then forward after MAC learning."""
        engine = DataPathEngine(enable_ml=False, enable_hw_offload=False, enable_qos=False)
        engine.bridge.add_interface(Interface(1, "eth0", "00:11:22:33:44:55"))
        engine.bridge.add_interface(Interface(2, "eth1", "00:11:22:33:44:66"))

        # Step 1: Unknown unicast (pure L2 frame) from 44:aa → flood out other ports
        raw = bytes.fromhex("001122334455" "0011223344aa" "0800" + "00" * 46)
        pkt = parse_packet(raw)
        meta = await engine.process_packet(pkt, "eth0")
        assert meta.action == ForwardAction.BRIDGE
        assert meta.flood_ports == [2]  # flooded to eth1 only

        # Step 2: Source MAC learned on port 1 during that frame
        assert engine.bridge.lookup("00:11:22:33:44:aa") is not None

        # Step 3: Frame towards the learned MAC → known unicast forward
        raw2 = bytes.fromhex("0011223344aa" "001122334455" "0800" + "00" * 46)
        pkt2 = parse_packet(raw2)
        meta2 = await engine.process_packet(pkt2, "eth1")
        assert meta2.action == ForwardAction.BRIDGE
        assert meta2.flood_ports == [1]

    def test_qos_classification_pipeline(self):
        """Test full QoS pipeline: classify → shape → schedule."""
        tm = TrafficManager(total_bandwidth_mbps=100)

        # SSH packet (should be CRITICAL priority)
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "45 00 0028 0000 0000 40 06 0000 0a000064 0a000001"
            "3039 0016 00000001 00000000 50 02 2000 0000 0000"
        )
        pkt = parse_packet(raw)
        result_pkt, should_fwd = tm.process(pkt)
        assert should_fwd is True

        # Dequeue should return the packet
        result = tm.dequeue()
        assert result is not None

    def test_generator_produces_valid_packets(self):
        """Traffic generator produces parseable packets."""
        config = GeneratorConfig(rate_pps=10, duration=0.5)
        gen = TrafficGenerator(config)
        packets = []

        async def collect():
            async for pkt in gen.generate_burst(10):
                packets.append(pkt)

        asyncio.run(collect())
        assert len(packets) == 10
        for pkt in packets:
            assert pkt.ethernet is not None
            assert len(pkt.protocol_chain) > 0
