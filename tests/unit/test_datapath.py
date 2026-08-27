"""Unit tests for data path engine."""

from __future__ import annotations

import pytest

from network_lab.core.datapath import Bridge, DataPathEngine, ForwardAction, Interface, InterfaceState, Router
from network_lab.core.parser import parse_packet


class TestBridge:
    def setup_method(self):
        self.bridge = Bridge("br0")
        self.bridge.add_interface(Interface(1, "eth0", "00:11:22:33:44:55"))
        self.bridge.add_interface(Interface(2, "eth1", "00:11:22:33:44:66"))
        self.bridge.add_interface(Interface(3, "eth2", "00:11:22:33:44:77"))

    def test_learn_mac(self):
        self.bridge.learn("00:11:22:33:44:aa", 1)
        entry = self.bridge.lookup("00:11:22:33:44:aa")
        assert entry is not None
        assert entry.port_id == 1

    def test_flood_unknown_unicast(self):
        # Packet to unknown MAC should flood
        raw = bytes.fromhex("ffffffffffff" "001122334455" "0800" + "00" * 46)
        pkt = parse_packet(raw)
        action, ports, reason = self.bridge.forward(pkt, rx_port=1)
        assert action == ForwardAction.BRIDGE
        assert 2 in ports
        assert 3 in ports
        assert 1 not in ports

    def test_forward_known_unicast(self):
        # Learn then forward
        self.bridge.learn("00:11:22:33:44:aa", 2)
        raw = bytes.fromhex("0011223344aa" "001122334455" "0800" + "00" * 46)
        pkt = parse_packet(raw)
        action, ports, reason = self.bridge.forward(pkt, rx_port=1)
        assert action == ForwardAction.BRIDGE
        assert 2 in ports

    def test_broadcast_flood(self):
        raw = bytes.fromhex("ffffffffffff" "001122334455" "0800" + "00" * 46)
        pkt = parse_packet(raw)
        action, ports, reason = self.bridge.forward(pkt, rx_port=1)
        assert action == ForwardAction.BRIDGE
        assert 2 in ports
        assert 3 in ports
        assert 1 not in ports  # exclude sender

    def test_hairpin_drop(self):
        # Source and dest on same port
        self.bridge.learn("00:11:22:33:44:aa", 1)
        raw = bytes.fromhex("0011223344aa" "001122334455" "0800" + "00" * 46)
        pkt = parse_packet(raw)
        action, ports, reason = self.bridge.forward(pkt, rx_port=1)
        assert action == ForwardAction.DROP


class TestRouter:
    def setup_method(self):
        self.router = Router("r0")
        self.router.add_interface("eth0", Interface(1, "eth0", "00:11:22:33:44:55", "10.0.0.1"))
        self.router.learn_arp("10.0.0.1", "00:11:22:33:44:55")
        self.router.learn_arp("10.0.0.2", "00:11:22:33:44:66")
        self.router.add_route("10.0.0.0/24", iface="eth0")
        self.router.add_route("192.168.1.0/24", next_hop="10.0.0.2", iface="eth0")

    def test_directly_connected(self):
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "4500002800000000400600000a0000640a000001"
            "3039005000000001000000005002200000000000"
        )
        pkt = parse_packet(raw)
        action, egress, reason = self.router.forward(pkt)
        assert action == ForwardAction.FORWARD
        assert egress == "eth0"

    def test_no_route(self):
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "4500002800000000400600000a00006408080808"
            "3039005000000001000000005002200000000000"
        )
        pkt = parse_packet(raw)
        action, egress, reason = self.router.forward(pkt)
        assert action == ForwardAction.DROP
        assert "no route" in reason

    def test_ttl_exceeded(self):
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "4500002800000000010600000a0000640a000001"
            "3039005000000001000000005002200000000000"
        )
        pkt = parse_packet(raw)
        action, egress, reason = self.router.forward(pkt)
        assert action == ForwardAction.TO_CPU
        assert "TTL" in reason


class TestDataPathEngine:
    async def test_process_bridge_packet(self):
        engine = DataPathEngine(enable_ml=False, enable_hw_offload=False, enable_qos=False)
        engine.bridge.add_interface(Interface(1, "eth0", "00:11:22:33:44:55"))
        engine.bridge.add_interface(Interface(2, "eth1", "00:11:22:33:44:66"))

        raw = bytes.fromhex("ffffffffffff" "001122334455" "0800" + "00" * 46)
        pkt = parse_packet(raw)
        meta = await engine.process_packet(pkt, "eth0")
        assert meta.action == ForwardAction.BRIDGE
        assert engine.stats["packets_processed"] == 1

    async def test_process_router_packet(self):
        engine = DataPathEngine(enable_ml=False, enable_hw_offload=False, enable_qos=False)
        engine.router.add_interface("eth0", Interface(1, "eth0", "00:11:22:33:44:55", "10.0.0.1"))
        engine.router.learn_arp("10.0.0.1", "00:11:22:33:44:55")
        engine.router.add_route("10.0.0.0/24", iface="eth0")

        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "4500002800000000400600000a0000640a000001"
            "3039005000000001000000005002200000000000"
        )
        pkt = parse_packet(raw)
        meta = await engine.process_packet(pkt, "eth0")
        assert meta.action == ForwardAction.FORWARD
        assert engine.stats["packets_routed"] == 1

    async def test_acl_drop(self):
        engine = DataPathEngine(enable_ml=False, enable_hw_offload=False, enable_qos=False)
        engine.add_acl("10.0.0.100", "*", -1, 22, 6, "drop")

        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"
            "4500002800000000400600000a0000640a000001"
            "3039001600000001000000005002200000000000"
        )
        pkt = parse_packet(raw)
        meta = await engine.process_packet(pkt, "eth0")
        assert meta.action == ForwardAction.DROP
        assert engine.stats["packets_dropped"] == 1
