"""HW Offload Engine — Hardware offload decision engine.

Adapted from PyFlow project. Simulates the "mapping packet processing
to various HW offload/CPU options" requirement from the Qualcomm JD.

In real Qualcomm AP/router silicon, this would interface with:
- NSS (Network Subsystem) offload engine
- EDMA (Enhanced Direct Memory Access)
- WiFi MAC offload
- Crypto engine for IPsec
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .parser import ParsedPacket


class OffloadTarget(Enum):
    """Where a packet gets processed."""
    CPU_SLOW_PATH = auto()      # Complex processing needed
    CPU_FAST_PATH = auto()      # Simple lookup + forward
    HW_NIC_OFFLOAD = auto()     # NIC checksum/TSO/RSS
    HW_WIFI_OFFLOAD = auto()    # WiFi MAC reassembly/encryption
    HW_CRYPTO = auto()          # IPsec/SSL acceleration
    HW_QOS = auto()             # Traffic shaping in hardware


class PacketComplexity(Enum):
    """Categorizes packet processing complexity."""
    SIMPLE = auto()       # Known flow, no options, standard headers
    MODERATE = auto()     # VLAN tag, fragmentation, options
    COMPLEX = auto()      # Tunnel encapsulation, deep inspection needed
    EXCEPTION = auto()    # Unknown protocol, error conditions


@dataclass(frozen=True)
class PacketProfile:
    """Key attributes used for offload decisions."""
    ethertype: int              # 0x0800=IPv4, 0x86DD=IPv6, 0x8100=VLAN
    ip_protocol: int | None  # 6=TCP, 17=UDP, 1=ICMP, etc.
    has_vlan: bool
    is_fragment: bool
    has_ip_options: bool
    has_tcp_options: bool
    is_tunnel: bool             # VXLAN, GRE, etc.
    is_encrypted: bool          # IPsec, WPA3
    payload_size: int
    dscp: int                   # QoS marking

    @property
    def is_ipv4(self) -> bool:
        return self.ethertype == 0x0800

    @property
    def is_ipv6(self) -> bool:
        return self.ethertype == 0x86DD


class OffloadEngine:
    """
    Decides whether a packet can be handled by hardware offload
    or must traverse the CPU slow path.

    Models real-world constraints:
    - HW has limited table capacity (flow cache size)
    - Certain header combinations break HW parsing
    - Crypto offload only supports specific ciphers
    """

    def __init__(self,
                 flow_cache_size: int = 8192,
                 hw_supports_vxlan: bool = True,
                 hw_supports_ipsec: bool = True,
                 hw_supports_qos: bool = True):
        self.flow_cache_size = flow_cache_size
        self.hw_supports_vxlan = hw_supports_vxlan
        self.hw_supports_ipsec = hw_supports_ipsec
        self.hw_supports_qos = hw_supports_qos

        # Simulated HW flow table
        self._hw_flows: set[str] = set()
        self._cpu_exceptions: int = 0
        self._hw_accelerated: int = 0

    def classify_complexity(self, profile: PacketProfile) -> PacketComplexity:
        """Determine how complex this packet is to process.

        EXCEPTION checks come first — an unknown protocol or ethertype is
        exceptional regardless of other flags. Previously the fragment/
        tunnel check short-circuited these, letting e.g. encrypted ESP
        with a fragment flag get hardware-offloaded.
        """
        if profile.ethertype not in (0x0800, 0x86DD, 0x8100):
            return PacketComplexity.EXCEPTION

        if profile.ip_protocol not in (6, 17, 1, 58):  # TCP, UDP, ICMP, ICMPv6
            return PacketComplexity.EXCEPTION

        if profile.is_fragment or profile.has_ip_options or profile.is_tunnel:
            if not self.hw_supports_vxlan and profile.is_tunnel:
                return PacketComplexity.COMPLEX
            return PacketComplexity.MODERATE

        return PacketComplexity.SIMPLE

    def decide_offload(self, profile: PacketProfile,
                       flow_hash: str) -> OffloadTarget:
        """
        Main decision engine. Returns where this packet should be processed.

        Logic mirrors real Qualcomm data path decisions:
        1. Check if flow is already in HW cache (fast path)
        2. If new flow, check if HW can handle the complexity
        3. If too complex or HW table full → CPU slow path
        4. If simple and table has space → HW offload

        The HW cache evicts its oldest half when full (real caches age out
        idle entries; without eviction every post-8192-flow connection was
        permanently stuck on the CPU).
        """
        complexity = self.classify_complexity(profile)

        # Already cached in HW → fast path
        if flow_hash in self._hw_flows:
            self._hw_accelerated += 1
            if profile.is_encrypted and self.hw_supports_ipsec:
                return OffloadTarget.HW_CRYPTO
            if profile.dscp > 0 and self.hw_supports_qos:
                return OffloadTarget.HW_QOS
            return OffloadTarget.HW_NIC_OFFLOAD

        # Exception path — CPU must handle
        if complexity == PacketComplexity.EXCEPTION:
            self._cpu_exceptions += 1
            return OffloadTarget.CPU_SLOW_PATH

        # Complex packets — CPU unless HW explicitly supports
        if complexity == PacketComplexity.COMPLEX:
            if profile.is_tunnel and not self.hw_supports_vxlan:
                self._cpu_exceptions += 1
                return OffloadTarget.CPU_SLOW_PATH
            # Fall through to HW if supported

        # HW table full → fall back to CPU fast path
        if len(self._hw_flows) >= self.flow_cache_size:
            return OffloadTarget.CPU_FAST_PATH

        # Install in HW flow cache
        self._hw_flows.add(flow_hash)
        self._hw_accelerated += 1

        if profile.is_encrypted and self.hw_supports_ipsec:
            return OffloadTarget.HW_CRYPTO
        if profile.dscp > 0 and self.hw_supports_qos:
            return OffloadTarget.HW_QOS
        return OffloadTarget.HW_NIC_OFFLOAD

    def evict_flow(self, flow_hash: str) -> None:
        """Remove a flow from HW cache (e.g., on timeout or teardown)."""
        self._hw_flows.discard(flow_hash)

    def get_stats(self) -> dict:
        """Return offload statistics."""
        total = self._hw_accelerated + self._cpu_exceptions
        return {
            "hw_accelerated": self._hw_accelerated,
            "cpu_exceptions": self._cpu_exceptions,
            "hw_cache_utilization": len(self._hw_flows) / self.flow_cache_size,
            "hw_offload_rate": self._hw_accelerated / max(total, 1),
        }


def profile_from_parsed_packet(pkt: ParsedPacket) -> PacketProfile:
    """Convert a ParsedPacket to a PacketProfile for offload decisions."""
    ethertype = pkt.ethernet.ethertype if pkt.ethernet else 0
    has_vlan = pkt.vlan is not None
    is_tunnel = pkt.vxlan is not None or pkt.gre is not None
    ip_protocol = None
    is_fragment = False
    has_ip_options = False
    has_tcp_options = False
    dscp = 0
    payload_size = len(pkt.raw)

    if pkt.ipv4:
        ip_protocol = pkt.ipv4.protocol
        is_fragment = pkt.ipv4.fragment_offset > 0 or pkt.ipv4.mf == 1
        has_ip_options = len(pkt.ipv4.options) > 0
        dscp = pkt.ipv4.dscp
    elif pkt.ipv6:
        ip_protocol = pkt.ipv6.next_header
        dscp = pkt.ipv6.traffic_class >> 2  # was missing — IPv6 never got HW_QOS

    if pkt.tcp:
        has_tcp_options = len(pkt.tcp.options) > 0

    # Detect encryption (simplified — in real code, check for ESP/AH)
    is_encrypted = False
    if ip_protocol == 50:  # ESP
        is_encrypted = True

    return PacketProfile(
        ethertype=ethertype,
        ip_protocol=ip_protocol,
        has_vlan=has_vlan,
        is_fragment=is_fragment,
        has_ip_options=has_ip_options,
        has_tcp_options=has_tcp_options,
        is_tunnel=is_tunnel,
        is_encrypted=is_encrypted,
        payload_size=payload_size,
        dscp=dscp,
    )
