"""Packet generator using scapy for crafting test traffic."""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from scapy.all import (
    ARP,
    BOOTP,
    DHCP,
    GRE,
    ICMP,
    IP,
    TCP,
    UDP,
    VXLAN,
    Dot11,
    Dot11Beacon,
    Dot11Elt,
    Ether,
    IPv6,
    RandMAC,
    Raw,
)

from .parser import ParsedPacket, parse_packet


# ── Configuration ────────────────────────────────────────────────────────
@dataclass
class GeneratorConfig:
    """Traffic generation parameters."""
    src_mac: str = "00:11:22:33:44:55"
    dst_mac: str = "ff:ff:ff:ff:ff:ff"
    src_ip: str = "10.0.0.100"
    dst_ip: str = "10.0.0.1"
    src_port: int = 12345
    dst_port: int = 80
    rate_pps: int = 100  # packets per second
    duration: float = 10.0  # seconds
    payload_size: int = 64


# ═══════════════════════════════════════════════════════════════════════════
# PACKET CRAFTERS
# ═══════════════════════════════════════════════════════════════════════════

class PacketCrafter:
    """Craft various packet types for testing."""

    @staticmethod
    def ethernet_broadcast() -> bytes:
        pkt = Ether(src=RandMAC(), dst="ff:ff:ff:ff:ff:ff") / Raw(b"\x00" * 46)
        return bytes(pkt)

    @staticmethod
    def arp_request(src_mac: str = "00:11:22:33:44:55",
                    src_ip: str = "10.0.0.100",
                    target_ip: str = "10.0.0.1") -> bytes:
        pkt = Ether(src=src_mac, dst="ff:ff:ff:ff:ff:ff") / ARP(
            hwsrc=src_mac, psrc=src_ip, pdst=target_ip, op=1
        )
        return bytes(pkt)

    @staticmethod
    def arp_reply(src_mac: str = "00:11:22:33:44:55",
                  dst_mac: str = "00:11:22:33:44:66",
                  src_ip: str = "10.0.0.1",
                  dst_ip: str = "10.0.0.100") -> bytes:
        pkt = Ether(src=src_mac, dst=dst_mac) / ARP(
            hwsrc=src_mac, hwdst=dst_mac, psrc=src_ip, pdst=dst_ip, op=2
        )
        return bytes(pkt)

    @staticmethod
    def ipv4_tcp_syn(src_ip: str = "10.0.0.100",
                     dst_ip: str = "10.0.0.1",
                     src_port: int = 12345,
                     dst_port: int = 80) -> bytes:
        pkt = Ether(src=GeneratorConfig.src_mac, dst=GeneratorConfig.dst_mac) / IP(src=src_ip, dst=dst_ip) / TCP(
            sport=src_port, dport=dst_port, flags="S", seq=random.randint(0, 0xFFFFFFFF)
        )
        return bytes(pkt)

    @staticmethod
    def ipv4_tcp_data(src_ip: str = "10.0.0.100",
                      dst_ip: str = "10.0.0.1",
                      src_port: int = 12345,
                      dst_port: int = 80,
                      payload: bytes = b"GET / HTTP/1.1\r\nHost: test\r\n\r\n") -> bytes:
        pkt = Ether(src=GeneratorConfig.src_mac, dst=GeneratorConfig.dst_mac) / IP(src=src_ip, dst=dst_ip) / TCP(
            sport=src_port, dport=dst_port, flags="PA",
            seq=random.randint(0, 0xFFFFFFFF), ack=random.randint(0, 0xFFFFFFFF)
        ) / Raw(payload)
        return bytes(pkt)

    @staticmethod
    def ipv4_udp(src_ip: str = "10.0.0.100",
                 dst_ip: str = "10.0.0.1",
                 src_port: int = 12345,
                 dst_port: int = 53,
                 payload: bytes = b"\x00" * 32) -> bytes:
        pkt = Ether(src=GeneratorConfig.src_mac, dst=GeneratorConfig.dst_mac) / IP(src=src_ip, dst=dst_ip) / UDP(
            sport=src_port, dport=dst_port
        ) / Raw(payload)
        return bytes(pkt)

    @staticmethod
    def ipv4_icmp_echo(src_ip: str = "10.0.0.100",
                       dst_ip: str = "10.0.0.1",
                       ident: int = 1,
                       seq: int = 1) -> bytes:
        pkt = Ether(src=GeneratorConfig.src_mac, dst=GeneratorConfig.dst_mac) / IP(src=src_ip, dst=dst_ip) / ICMP(
            type=8, id=ident, seq=seq
        ) / Raw(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        return bytes(pkt)

    @staticmethod
    def ipv6_tcp(src_ip: str = "2001:db8::1",
                 dst_ip: str = "2001:db8::2",
                 src_port: int = 12345,
                 dst_port: int = 80) -> bytes:
        pkt = Ether(src=GeneratorConfig.src_mac, dst=GeneratorConfig.dst_mac) / IPv6(src=src_ip, dst=dst_ip) / TCP(
            sport=src_port, dport=dst_port, flags="S"
        )
        return bytes(pkt)

    @staticmethod
    def dhcp_discover(src_mac: str = "00:11:22:33:44:55") -> bytes:
        pkt = Ether(src=src_mac, dst="ff:ff:ff:ff:ff:ff") / IP(
            src="0.0.0.0", dst="255.255.255.255"
        ) / UDP(sport=68, dport=67) / BOOTP(
            op=1, htype=1, hlen=1, xid=random.randint(0, 0xFFFFFFFF),
            chaddr=bytes.fromhex(src_mac.replace(":", "")) + b"\x00" * 10,
        ) / DHCP(
            options=[("message-type", "discover"), "end"]
        )
        return bytes(pkt)

    @staticmethod
    def vxlan_encapsulated(vni: int = 5000,
                           inner_src: str = "00:11:22:33:44:aa",
                           inner_dst: str = "00:11:22:33:44:bb") -> bytes:
        inner = Ether(src=inner_src, dst=inner_dst) / IP(
            src="192.168.1.10", dst="192.168.1.20"
        ) / TCP(sport=54321, dport=80, flags="S")
        pkt = Ether(src=GeneratorConfig.src_mac, dst=GeneratorConfig.dst_mac) / IP(src="10.0.0.1", dst="10.0.0.2") / UDP(
            sport=12345, dport=4789
        ) / VXLAN(vni=vni) / inner
        return bytes(pkt)

    @staticmethod
    def gre_tunnel(src_ip: str = "10.0.0.1",
                   dst_ip: str = "10.0.0.2") -> bytes:
        inner = IP(src="192.168.1.10", dst="192.168.1.20") / TCP(
            sport=12345, dport=80, flags="S"
        )
        pkt = Ether(src=GeneratorConfig.src_mac, dst=GeneratorConfig.dst_mac) / IP(src=src_ip, dst=dst_ip) / GRE(proto=0x0800) / inner
        return bytes(pkt)

    @staticmethod
    def wifi_beacon(ssid: str = "TestAP",
                    bssid: str = "00:11:22:33:44:55",
                    channel: int = 6) -> bytes:
        pkt = Dot11(type=0, subtype=8, addr1="ff:ff:ff:ff:ff:ff",
                    addr2=bssid, addr3=bssid) / Dot11Beacon(
            cap="ESS+privacy"
        ) / Dot11Elt(ID="SSID", info=ssid) / Dot11Elt(
            ID="DSset", info=bytes([channel])
        )
        return bytes(pkt)

    @staticmethod
    def wifi_qos_data(src: str = "00:11:22:33:44:aa",
                      dst: str = "00:11:22:33:44:bb",
                      bssid: str = "00:11:22:33:44:55") -> bytes:
        pkt = Dot11(type=2, subtype=8, addr1=dst, addr2=src, addr3=bssid, SC=0x10) / Raw(b"\x00" * 32)
        return bytes(pkt)


# ═══════════════════════════════════════════════════════════════════════════
# TRAFFIC GENERATOR (async)
# ═══════════════════════════════════════════════════════════════════════════

class TrafficGenerator:
    """Generate synthetic traffic patterns for testing."""

    def __init__(self, config: GeneratorConfig | None = None):
        self.config = config or GeneratorConfig()
        self.crafter = PacketCrafter()
        self._running = False
        self._packet_count = 0
        self._start_time = 0.0
        # Method list resolved once — generate_stream picks from it directly
        self._methods = [
            self.crafter.arp_request,
            self.crafter.ipv4_tcp_syn,
            self.crafter.ipv4_tcp_data,
            self.crafter.ipv4_udp,
            self.crafter.ipv4_icmp_echo,
            self.crafter.dhcp_discover,
            self.crafter.vxlan_encapsulated,
            self.crafter.gre_tunnel,
        ]

    async def generate_burst(self, count: int) -> AsyncIterator[ParsedPacket]:
        """Generate a burst of mixed traffic."""
        crafter = self.crafter
        methods = [
            crafter.arp_request,
            crafter.ipv4_tcp_syn,
            crafter.ipv4_tcp_data,
            crafter.ipv4_udp,
            crafter.ipv4_icmp_echo,
            crafter.ipv6_tcp,
            crafter.dhcp_discover,
            crafter.vxlan_encapsulated,
            crafter.gre_tunnel,
        ]
        for _ in range(count):
            method = random.choice(methods)
            raw = method()
            ts = time.time()
            pkt = parse_packet(raw, ts)
            yield pkt

    async def generate_stream(self, callback: Callable[[ParsedPacket], None]) -> None:
        """Generate continuous traffic with realistic bursty arrivals.

        Uses exponential (Poisson) inter-arrival times instead of fixed
        spacing: real traffic clusters and lulls rather than arriving like
        a metronome. Mean interval = 1/rate, so the long-run average still
        honors rate_pps — but instant pps fluctuates, and throughput in
        the dashboard visibly breathes.
        """
        self._running = True
        self._start_time = time.time()
        if self.config.rate_pps <= 0:
            raise ValueError("rate_pps must be positive")

        # Burst envelope: slow sinusoidal modulation of the mean rate
        # (±30%) so even minute-scale averages drift realistically.
        burst_period = 20.0  # seconds

        while self._running:
            # Honor configured duration — previously accepted but ignored,
            # so streams ran forever.
            if (
                self.config.duration
                and time.time() - self._start_time >= self.config.duration
            ):
                break

            elapsed_in_cycle = (time.time() - self._start_time) % burst_period
            modulation = 1.0 + 0.3 * math.sin(2 * math.pi * elapsed_in_cycle / burst_period)

            # Exponentially distributed gap (Poisson arrivals)
            gap = random.expovariate(self.config.rate_pps * modulation)
            await asyncio.sleep(gap)
            if not self._running:
                break

            method = random.choice(self._methods)
            raw = method()
            pkt = parse_packet(raw, time.time())
            self._packet_count += 1
            callback(pkt)

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        elapsed = time.time() - self._start_time if self._start_time else 0
        return {
            "packets_generated": self._packet_count,
            "elapsed_seconds": elapsed,
            "rate_pps": self._packet_count / elapsed if elapsed > 0 else 0,
        }


# ═══════════════════════════════════════════════════════════════════════════
# PCAP REPLAY
# ═══════════════════════════════════════════════════════════════════════════

async def replay_pcap(filepath: str) -> AsyncIterator[ParsedPacket]:
    """Replay packets from a PCAP file (true async iterator per annotation)."""
    from scapy.all import rdpcap
    packets = rdpcap(filepath)
    for pkt in packets:
        raw = bytes(pkt)
        yield parse_packet(raw, time.time())
        await asyncio.sleep(0)  # yield control to the loop
