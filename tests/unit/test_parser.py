"""Unit tests for protocol parser."""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from network_lab.core.parser import (
    ARP,
    DHCP,
    ICMP,
    TCP,
    UDP,
    IPv4,
    IPv6,
    VLAN,
    VXLAN,
    Ethernet,
    GRE,
    ParsedPacket,
    parse_packet,
    parse_wifi_frame,
)


# ═══════════════════════════════════════════════════════════════════════════
# ETHERNET
# ═══════════════════════════════════════════════════════════════════════════

class TestEthernet:
    def test_parse_minimum(self):
        # 14-byte Ethernet header
        raw = bytes.fromhex("ffffffffffff" "001122334455" "0800")
        eth = Ethernet.parse(raw)
        assert eth.dst_mac == "ff:ff:ff:ff:ff:ff"
        assert eth.src_mac == "00:11:22:33:44:55"
        assert eth.ethertype == 0x0800

    def test_parse_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            Ethernet.parse(b"\x00" * 10)

    def test_vlan_tagged(self):
        raw = bytes.fromhex("ffffffffffff" "001122334455" "8100" "0064" "0800")
        eth = Ethernet.parse(raw)
        assert eth.ethertype == 0x8100


# ═══════════════════════════════════════════════════════════════════════════
# ARP
# ═══════════════════════════════════════════════════════════════════════════

class TestARP:
    def test_arp_request(self):
        raw = bytes.fromhex(
            "0001"  # hw_type
            "0800"  # proto_type
            "06"    # hw_len
            "04"    # proto_len
            "0001"  # opcode (request)
            "001122334455"  # sender_mac
            "0a000064"      # sender_ip (10.0.0.100)
            "000000000000"  # target_mac
            "0a000001"      # target_ip (10.0.0.1)
        )
        arp = ARP.parse(raw)
        assert arp.opcode == 1
        assert arp.sender_ip == "10.0.0.100"
        assert arp.target_ip == "10.0.0.1"
        assert arp.sender_mac == "00:11:22:33:44:55"

    def test_arp_reply(self):
        raw = bytes.fromhex(
            "0001" "0800" "06" "04" "0002"  # reply
            "001122334455" "0a000001"
            "001122334466" "0a000064"
        )
        arp = ARP.parse(raw)
        assert arp.opcode == 2
        assert arp.sender_ip == "10.0.0.1"
        assert arp.target_ip == "10.0.0.100"


# ═══════════════════════════════════════════════════════════════════════════
# IPv4
# ═══════════════════════════════════════════════════════════════════════════

class TestIPv4:
    def test_parse_basic(self):
        # 20-byte IPv4 header
        raw = bytes.fromhex(
            "45"        # version=4, ihl=5
            "00"        # dscp=0, ecn=0
            "0014"      # total_length=20
            "1234"      # identification
            "0000"      # flags=0, fragment_offset=0
            "40"        # ttl=64
            "06"        # protocol=TCP
            "0000"      # checksum
            "0a000064"  # src=10.0.0.100
            "0a000001"  # dst=10.0.0.1
        )
        ip = IPv4.parse(raw)
        assert ip.version == 4
        assert ip.ihl == 5
        assert ip.ttl == 64
        assert ip.protocol == 6
        assert ip.src_ip == "10.0.0.100"
        assert ip.dst_ip == "10.0.0.1"
        assert ip.header_length == 20

    def test_parse_with_options(self):
        # 24-byte header (ihl=6)
        raw = bytes.fromhex(
            "46"        # version=4, ihl=6
            "00" "0018" # total_length=24
            "0000" "0000"
            "40" "11"   # ttl=64, UDP
            "0000"
            "0a000064" "0a000001"
            "01020304"  # options (4 bytes)
        )
        ip = IPv4.parse(raw)
        assert ip.ihl == 6
        assert ip.header_length == 24
        assert ip.options == bytes.fromhex("01020304")

    def test_dscp_ecn(self):
        raw = bytes.fromhex(
            "45" "b8"   # dscp=46 (EF), ecn=0
            "0014" "0000" "0000"
            "40" "06" "0000"
            "0a000064" "0a000001"
        )
        ip = IPv4.parse(raw)
        assert ip.dscp == 46  # EF
        assert ip.ecn == 0


# ═══════════════════════════════════════════════════════════════════════════
# TCP
# ═══════════════════════════════════════════════════════════════════════════

class TestTCP:
    def test_syn_packet(self):
        raw = bytes.fromhex(
            "3039"     # src_port=12345
            "0050"     # dst_port=80
            "00000001" # seq=1
            "00000000" # ack=0
            "50"       # data_offset=5
            "02"       # flags=SYN
            "2000"     # window=8192
            "0000"     # checksum
            "0000"     # urgent
        )
        tcp = TCP.parse(raw)
        assert tcp.src_port == 12345
        assert tcp.dst_port == 80
        assert tcp.seq_num == 1
        assert tcp.flags == 0x02
        assert "SYN" in tcp.flag_names
        assert tcp.header_length == 20

    def test_syn_ack(self):
        raw = bytes.fromhex(
            "0050" "3039"
            "00000001" "00000001"
            "50" "12"  # SYN+ACK
            "2000" "0000" "0000"
        )
        tcp = TCP.parse(raw)
        assert "SYN" in tcp.flag_names
        assert "ACK" in tcp.flag_names

    def test_with_options(self):
        # 24-byte TCP header (data_offset=6)
        raw = bytes.fromhex(
            "3039" "0050"
            "00000001" "00000000"
            "60"  # data_offset=6 (24 bytes)
            "02" "2000" "0000" "0000"
            "01020304"  # options (NOP NOP + 2 bytes)
        )
        tcp = TCP.parse(raw)
        assert tcp.data_offset == 6
        assert tcp.header_length == 24
        assert tcp.options == bytes.fromhex("01020304")


# ═══════════════════════════════════════════════════════════════════════════
# UDP
# ═══════════════════════════════════════════════════════════════════════════

class TestUDP:
    def test_basic(self):
        raw = bytes.fromhex(
            "1234"     # src_port=4660
            "0035"     # dst_port=53 (DNS)
            "0010"     # length=16
            "0000"     # checksum
        )
        udp = UDP.parse(raw)
        assert udp.src_port == 4660
        assert udp.dst_port == 53
        assert udp.length == 16


# ═══════════════════════════════════════════════════════════════════════════
# ICMP
# ═══════════════════════════════════════════════════════════════════════════

class TestICMP:
    def test_echo_request(self):
        raw = bytes.fromhex(
            "08"       # type=8 (echo request)
            "00"       # code=0
            "0000"     # checksum
            "0001"     # identifier=1
            "0001"     # sequence=1
        )
        icmp = ICMP.parse(raw)
        assert icmp.type == 8
        assert icmp.identifier == 1
        assert icmp.sequence == 1

    def test_echo_reply(self):
        raw = bytes.fromhex("00" "00" "0000" "0001" "0001")
        icmp = ICMP.parse(raw)
        assert icmp.type == 0


# ═══════════════════════════════════════════════════════════════════════════
# DHCP
# ═══════════════════════════════════════════════════════════════════════════

class TestDHCP:
    def test_discover(self):
        # Build a minimal DHCP discover
        raw = bytes.fromhex(
            "01"        # op=1 (request)
            "01"        # htype=1 (ethernet)
            "06"        # hlen=6
            "00"        # hops=0
            "12345678"  # xid
            "0000"      # secs
            "0000"      # flags
            "00000000"  # ciaddr
            "00000000"  # yiaddr
            "00000000"  # siaddr
            "00000000"  # giaddr
            "001122334455"  # chaddr (6 bytes, rest padded)
        )
        # Pad to minimum size (236 bytes for options to start)
        raw += b"\x00" * (236 - len(raw))
        # Magic cookie + message type option
        raw += bytes.fromhex("63825363")  # magic
        raw += bytes.fromhex("350101")    # option 53, len 1, type 1 (discover)
        raw += b"\xff"  # end option

        dhcp = DHCP.parse(raw)
        assert dhcp.op == 1
        assert dhcp.xid == 0x12345678
        assert dhcp.msg_type == 1  # discover


# ═══════════════════════════════════════════════════════════════════════════
# VLAN
# ═══════════════════════════════════════════════════════════════════════════

class TestVLAN:
    def test_parse(self):
        raw = bytes.fromhex("0064" "0800")  # pcp=0, dei=0, vid=100
        vlan = VLAN.parse(raw)
        assert vlan.vid == 100
        assert vlan.pcp == 0
        assert vlan.dei == 0
        assert vlan.inner_ethertype == 0x0800

    def test_priority(self):
        raw = bytes.fromhex("e064" "0800")  # pcp=7
        vlan = VLAN.parse(raw)
        assert vlan.pcp == 7


# ═══════════════════════════════════════════════════════════════════════════
# VXLAN
# ═══════════════════════════════════════════════════════════════════════════

class TestVXLAN:
    def test_parse(self):
        # Wire format: flags(1B)=0x08 | reserved(3B) | vni(3B)=0x001388 (5000) | reserved(1B)
        raw = bytes.fromhex("08" "000000" "001388" "00")
        vxlan = VXLAN.parse(raw)
        assert vxlan.vni == 5000
        assert vxlan.flags == 0x08


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION — Full Packet Parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestFullPacket:
    def test_ethernet_ipv4_tcp_syn(self):
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"  # Ethernet
            "45 00 0028 0000 0000 40 06 0000 0a000064 0a000001"  # IPv4
            "3039 0050 00000001 00000000 50 02 2000 0000 0000"  # TCP SYN
        )
        pkt = parse_packet(raw)
        assert pkt.ethernet is not None
        assert pkt.ipv4 is not None
        assert pkt.tcp is not None
        assert "SYN" in pkt.tcp.flag_names
        assert "TCP" in pkt.protocol_chain
        assert pkt.flow_key is not None

    def test_ethernet_vlan_ipv4_udp(self):
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "8100" "0064" "0800"  # Eth + VLAN
            "45 00 001c 0000 0000 40 11 0000 0a000064 0a000001"  # IPv4
            "1234 0035 0008 0000"  # UDP
        )
        pkt = parse_packet(raw)
        assert pkt.vlan is not None
        assert pkt.vlan.vid == 100
        assert pkt.udp is not None
        assert "VLAN(100)" in pkt.protocol_chain

    def test_arp_packet(self):
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0806"  # Ethernet
            "0001 0800 06 04 0001"  # ARP
            "001122334455" "0a000064" "000000000000" "0a000001"
        )
        pkt = parse_packet(raw)
        assert pkt.arp is not None
        assert pkt.arp.opcode == 1
        assert "ARP" in pkt.protocol_chain

    def test_dhcp_discover(self):
        # Ethernet + IPv4 + UDP + DHCP
        raw = bytes.fromhex(
            "ffffffffffff" "001122334455" "0800"  # Ethernet
            "45 00 011c 0000 0000 40 11 0000 00000000 ffffffff"  # IPv4
            "0044 0043 0108 0000"  # UDP (bootps=67, bootpc=68)
        )
        # DHCP header
        raw += bytes.fromhex("01 01 06 00 12345678 0000 0000")
        raw += bytes.fromhex("00000000" "00000000" "00000000" "00000000")
        raw += bytes.fromhex("001122334455")
        raw += b"\x00" * (236 - len(raw) + 14 + 20 + 8)  # pad
        raw += bytes.fromhex("63825363")  # magic
        raw += bytes.fromhex("350101")  # discover
        raw += b"\xff"

        pkt = parse_packet(raw)
        assert pkt.dhcp is not None
        assert pkt.dhcp.msg_type == 1


# ═══════════════════════════════════════════════════════════════════════════
# FUZZ TESTS (Hypothesis)
# ═══════════════════════════════════════════════════════════════════════════

class TestFuzz:
    @given(st.binary(min_size=14, max_size=2000))
    def test_parse_no_crash(self, raw):
        """Parser must NEVER raise — graceful degradation is the invariant.

        No try/except here: parse_packet's _safe wrappers guarantee it.
        Swallowing exceptions in the test (as before) defeated the very
        property under test and let IndexError bugs ship.
        """
        pkt = parse_packet(raw)
        assert isinstance(pkt, ParsedPacket)

    @given(
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=0, max_value=255),
    )
    def test_tcp_flags(self, flags, window):
        """TCP flag parsing handles all values."""
        raw = bytes.fromhex("30390050000000010000000050") + bytes([flags, 0x20, window]) + bytes.fromhex("00000000")
        tcp = TCP.parse(raw)
        assert tcp.flags == flags


import struct
