"""Unified Data Path Engine — integrates parser, ML classifier, HW offload,
QoS traffic manager, bridge, router, and tunnel logic.

Combines the best of both projects:
- From network-lab: full protocol parser, bridge/router, API, dashboard, Docker
- From PyFlow: ML classifier, HW offload engine, ACL, flow table
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum, auto

from .hw_offload import OffloadEngine, OffloadTarget, profile_from_parsed_packet
from .ml_classifier import FlowTable, TrafficClass, TrafficClassifier
from .parser import ParsedPacket
from .qos import TrafficManager


class ForwardAction(Enum):
    """Actions the data path can take on a packet."""
    DROP = auto()
    FORWARD = auto()
    BRIDGE = auto()
    ROUTE = auto()
    TO_CPU = auto()


# Backward compatibility alias
FwdAction = ForwardAction


@dataclass
class PacketMeta:
    """Metadata attached to each packet as it traverses the data path."""
    ingress_iface: str
    egress_iface: str | None = None
    action: ForwardAction = ForwardAction.FORWARD
    offload_target: OffloadTarget | None = None
    traffic_class: TrafficClass | None = None
    reason: str = ""
    flood_ports: list[int] = field(default_factory=list)

    # Timing for performance analysis
    ingress_ts: float = field(default_factory=time.time)
    egress_ts: float | None = None

    @property
    def latency_us(self) -> float:
        if self.egress_ts:
            return (self.egress_ts - self.ingress_ts) * 1_000_000
        return 0.0


# ── Interfaces ───────────────────────────────────────────────────────────
class InterfaceState(Enum):
    DOWN = auto()
    UP = auto()


@dataclass
class Interface:
    port_id: int
    name: str
    mac: str
    ip: str | None = None
    vlan: int | None = None  # access VLAN (None = trunk)
    allowed_vlans: list[int] = field(default_factory=list)
    state: InterfaceState = InterfaceState.UP
    is_loopback: bool = False
    tunnel_type: str | None = None
    tunnel_vni: int | None = None
    tunnel_dst_ip: str | None = None
    tunnel_src_ip: str | None = None
    rx_packets: int = 0
    tx_packets: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    drops: int = 0


# ── Bridge (L2) ─────────────────────────────────────────────────────────
@dataclass
class BridgeEntry:
    mac: str
    port_id: int
    vlan: int | None = None
    is_static: bool = False
    age: float = 0.0


class Bridge:
    """L2 bridge with MAC learning, VLAN support."""

    def __init__(self, name: str = "br0", ageing_time: float = 300.0):
        self.name = name
        self.ageing_time = ageing_time
        self.mac_table: dict[tuple[str, int | None], BridgeEntry] = {}
        self.interfaces: dict[int, Interface] = {}

    def add_interface(self, iface: Interface) -> None:
        self.interfaces[iface.port_id] = iface

    def learn(self, mac: str, port_id: int, vlan: int | None = None) -> None:
        self.mac_table[(mac, vlan)] = BridgeEntry(
            mac=mac, port_id=port_id, vlan=vlan, age=time.time()
        )

    def lookup(self, mac: str, vlan: int | None = None) -> BridgeEntry | None:
        return self.mac_table.get((mac, vlan))

    def flood_ports(self, exclude_port: int, vlan: int | None = None) -> list[int]:
        ports = []
        for pid, iface in self.interfaces.items():
            if pid == exclude_port or iface.state != InterfaceState.UP:
                continue
            if vlan is not None:
                if iface.vlan == vlan or vlan in iface.allowed_vlans:
                    ports.append(pid)
            else:
                ports.append(pid)
        return ports

    def forward(self, pkt: ParsedPacket, rx_port: int) -> tuple[ForwardAction, list[int], str]:
        """Returns (action, ports, reason)."""
        if pkt.ethernet is None:
            return ForwardAction.DROP, [], "no ethernet header"

        src_mac = pkt.ethernet.src_mac
        dst_mac = pkt.ethernet.dst_mac
        vlan = pkt.vlan.vid if pkt.vlan else None

        self.learn(src_mac, rx_port, vlan)

        # Broadcast / multicast
        if dst_mac.startswith("ff:") or dst_mac.startswith("01:00:5e"):
            ports = self.flood_ports(rx_port, vlan)
            return ForwardAction.BRIDGE, ports, f"broadcast flood (vlan={vlan})"

        # Unicast lookup
        entry = self.lookup(dst_mac, vlan)
        if entry and entry.port_id != rx_port:
            return ForwardAction.BRIDGE, [entry.port_id], f"known unicast -> port {entry.port_id}"
        elif entry and entry.port_id == rx_port:
            return ForwardAction.DROP, [], "same port (hairpin)"
        else:
            ports = self.flood_ports(rx_port, vlan)
            return ForwardAction.BRIDGE, ports, f"unknown unicast flood (vlan={vlan})"

    def age_out(self) -> int:
        now = time.time()
        stale = [k for k, v in self.mac_table.items()
                 if not v.is_static and now - v.age > self.ageing_time]
        for k in stale:
            del self.mac_table[k]
        return len(stale)


# ── Router (L3) ─────────────────────────────────────────────────────────
@dataclass
class RouteEntry:
    prefix: str
    next_hop: str | None = None
    iface: str | None = None
    metric: int = 1


class Router:
    """L3 router with longest-prefix match and ARP resolution.

    Routes are indexed by prefix length so LPM only scans buckets at or
    below the longest prefix seen — mirroring how real FIBs use a trie
    keyed by prefix length instead of scanning every route.
    """

    def __init__(self, name: str = "r0"):
        self.name = name
        self.routes: list[RouteEntry] = []
        # prefix_len -> list of routes at that length (sorted desc for LPM)
        self._routes_by_len: dict[int, list[RouteEntry]] = {}
        self._sorted_lens: list[int] = []
        self.arp_table: dict[str, str] = {}
        self.interfaces: dict[str, Interface] = {}

    def add_interface(self, name: str, iface: Interface) -> None:
        self.interfaces[name] = iface

    def add_route(self, prefix: str, next_hop: str | None = None,
                  iface: str | None = None, metric: int = 1) -> None:
        entry = RouteEntry(prefix, next_hop, iface, metric)
        self.routes.append(entry)
        plen = int(prefix.split("/")[1])
        self._routes_by_len.setdefault(plen, []).append(entry)
        self._sorted_lens = sorted(self._routes_by_len, reverse=True)

    def resolve_arp(self, ip: str) -> str | None:
        return self.arp_table.get(ip)

    def learn_arp(self, ip: str, mac: str) -> None:
        self.arp_table[ip] = mac

    def longest_prefix_match(self, dst_ip: str) -> RouteEntry | None:
        """Longest-prefix match via prefix-length buckets.

        Checks /32 first, then /31... First hit wins — no need to scan
        shorter prefixes once a match is found.
        """
        for plen in self._sorted_lens:
            for route in self._routes_by_len[plen]:
                if self._ip_in_prefix(dst_ip, route.prefix):
                    return route
        return None

    def forward(self, pkt: ParsedPacket) -> tuple[ForwardAction, str | None, str]:
        """Returns (action, egress_iface, reason)."""
        if pkt.ipv4:
            return self._forward_ipv4(pkt)
        elif pkt.ipv6:
            return self._forward_ipv6(pkt)
        return ForwardAction.DROP, None, "no L3 header"

    def _forward_ipv4(self, pkt: ParsedPacket) -> tuple[ForwardAction, str | None, str]:
        ipv4 = pkt.ipv4
        if ipv4 is None:
            return ForwardAction.DROP, None, "no IPv4"

        if ipv4.ttl <= 1:
            return ForwardAction.TO_CPU, None, "TTL exceeded"

        route = self.longest_prefix_match(ipv4.dst_ip)
        if not route:
            return ForwardAction.DROP, None, "no route"

        next_hop = route.next_hop or ipv4.dst_ip
        dst_mac = self.resolve_arp(next_hop)
        if not dst_mac:
            return ForwardAction.TO_CPU, None, f"ARP miss for {next_hop}"

        return ForwardAction.FORWARD, route.iface, f"routed to {next_hop} via {route.iface}"

    def _forward_ipv6(self, pkt: ParsedPacket) -> tuple[ForwardAction, str | None, str]:
        ipv6 = pkt.ipv6
        if ipv6 is None:
            return ForwardAction.DROP, None, "no IPv6"
        if ipv6.hop_limit <= 1:
            return ForwardAction.TO_CPU, None, "hop limit exceeded"

        route = self.longest_prefix_match(ipv6.dst_ip)
        if not route:
            return ForwardAction.DROP, None, "no route"

        next_hop = route.next_hop or ipv6.dst_ip
        dst_mac = self.resolve_arp(next_hop)
        if not dst_mac:
            return ForwardAction.TO_CPU, None, f"NDP miss for {next_hop}"

        return ForwardAction.FORWARD, route.iface, f"routed to {next_hop} via {route.iface}"

    @staticmethod
    def _ip_in_prefix(ip: str, prefix: str) -> bool:
        import ipaddress
        try:
            ip_obj = ipaddress.ip_address(ip)
            net_obj = ipaddress.ip_network(prefix, strict=False)
            # Mixed-version comparisons (IPv4 vs IPv6 route) raise TypeError
            if ip_obj.version != net_obj.version:
                return False
            return ip_obj in net_obj
        except (ValueError, TypeError):
            return False


# ═══════════════════════════════════════════════════════════════════════════
# UNIFIED DATA PATH ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class DataPathEngine:
    """
    Unified data path engine — processes packets through the full pipeline.

    Pipeline stages:
    1. Parse headers (L2-L7) — manual byte-level parser
    2. ACL check — 5-tuple rule matching
    3. Flow tracking + ML classification — Random Forest classifier
    4. HW offload decision — CPU vs HW acceleration
    5. L2 bridge or L3 route lookup — longest-prefix match
    6. QoS shaping — token bucket / HTB scheduling
    7. Forward/drop

    Integrates components from both projects:
    - network-lab: parser, generator, bridge, router, QoS, API, dashboard
    - PyFlow: ML classifier, HW offload, ACL, flow table, latency tracking
    """

    def __init__(self,
                 enable_ml: bool = True,
                 enable_hw_offload: bool = True,
                 enable_qos: bool = True):
        self.enable_ml = enable_ml
        self.enable_hw_offload = enable_hw_offload
        self.enable_qos = enable_qos

        # Subsystems (from both projects)
        self.bridge = Bridge("br0")
        self.router = Router("r0")
        self.traffic_mgr = TrafficManager(total_bandwidth_mbps=1000)
        # Inline forwarding never calls dequeue() — disable the scheduler
        # enqueue so packets don't accumulate in the priority queues.
        self.traffic_mgr._drain_active = False

        # ML + flow table (from PyFlow)
        self.classifier = TrafficClassifier() if enable_ml else None
        self.flow_table = FlowTable(timeout_sec=60.0)

        # HW offload (from PyFlow)
        self.offload_engine = OffloadEngine() if enable_hw_offload else None

        # ACL rules (from PyFlow)
        self.acls: list[tuple[str, str, int, int, int, str]] = []
        self._acl_index: dict[tuple[int, int] | None, list[tuple]] = {None: []}

        # Stats
        self.stats = {
            "packets_processed": 0,
            "packets_dropped": 0,
            "packets_bridged": 0,
            "packets_routed": 0,
            "packets_tunneled": 0,
            "packets_trapped": 0,
            "avg_latency_us": 0.0,
        }

        self._listeners = []

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def _compute_flow_hash(self, src_ip: str, dst_ip: str,
                          src_port: int, dst_port: int, proto: int) -> str:
        key = f"{src_ip}|{dst_ip}|{src_port}|{dst_port}|{proto}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    async def process_packet(self, pkt: ParsedPacket,
                            ingress_iface: str = "eth0") -> PacketMeta:
        """
        Main packet processing pipeline — full L2-L7 path.

        Returns PacketMeta with action, offload target, and traffic class.
        """
        meta = PacketMeta(ingress_iface=ingress_iface)

        # === Stage 1: Parse (already done, pkt is ParsedPacket) ===
        # Native 802.11 frames (parse_wifi_frame) carry wifi, not ethernet
        if pkt.ethernet is None and pkt.wifi is None:
            meta.action = ForwardAction.DROP
            self.stats["packets_dropped"] += 1
            return meta

        # Extract 5-tuple for ACL, flow tracking, offload
        src_ip = dst_ip = ""
        src_port = dst_port = 0
        proto = 0

        if pkt.ipv4:
            src_ip = pkt.ipv4.src_ip
            dst_ip = pkt.ipv4.dst_ip
            proto = pkt.ipv4.protocol
        elif pkt.ipv6:
            src_ip = pkt.ipv6.src_ip
            dst_ip = pkt.ipv6.dst_ip
            proto = pkt.ipv6.next_header

        if pkt.tcp:
            src_port = pkt.tcp.src_port
            dst_port = pkt.tcp.dst_port
        elif pkt.udp:
            src_port = pkt.udp.src_port
            dst_port = pkt.udp.dst_port

        # === Stage 2: ACL Check ===
        if self._check_acl_drop(src_ip, dst_ip, src_port, dst_port, proto):
            meta.action = ForwardAction.DROP
            self.stats["packets_dropped"] += 1
            return meta

        # === Stage 3: Flow Tracking + ML Classification ===
        if pkt.flow_key and self.classifier:
            flow = await self.flow_table.get_or_create(
                src_ip, dst_ip, src_port, dst_port, proto
            )
            await self.flow_table.update(flow, len(pkt.raw), pkt.timestamp)

            if len(flow.packet_sizes) >= 5:
                tc = await self.classifier.classify(flow)
                meta.traffic_class = tc
                await self.flow_table.set_classification(flow, tc)

        # === Stage 4: HW Offload Decision ===
        if self.offload_engine:
            profile = profile_from_parsed_packet(pkt)
            flow_hash = self._compute_flow_hash(src_ip, dst_ip, src_port, dst_port, proto)
            meta.offload_target = self.offload_engine.decide_offload(profile, flow_hash)

            # If HW can handle it entirely, it bypasses CPU forwarding but we
            # still account for the L2/L3 decision the hardware would make.
            if meta.offload_target in (OffloadTarget.HW_NIC_OFFLOAD,
                                       OffloadTarget.HW_WIFI_OFFLOAD,
                                       OffloadTarget.HW_QOS):
                self._account_hw_forwarding(pkt)
                meta.action = ForwardAction.FORWARD
                self.stats["packets_processed"] += 1
                return meta

        # === Stage 5: L2 Bridge or L3 Route ===
        rx_port = self._resolve_rx_port(ingress_iface)
        if pkt.ipv4 or pkt.ipv6:
            # L3 routing
            action, egress, reason = self.router.forward(pkt)
            meta.action = action
            meta.egress_iface = egress
            if action == ForwardAction.FORWARD:
                self.stats["packets_routed"] += 1
        elif pkt.arp:
            # ARP — bridge it AND snoop sender bindings for the router's
            # ARP table (previously routing always failed on ARP miss
            # because learn_arp had no production caller).
            if pkt.arp.opcode == 1 and self.router is not None:
                self.router.learn_arp(pkt.arp.sender_ip, pkt.arp.sender_mac)
            action, ports, reason = self.bridge.forward(pkt, rx_port)
            meta.action = action
            meta.reason = reason
            meta.flood_ports = ports
            self.stats["packets_bridged"] += 1
        else:
            # Unknown L3 (pure L2 frame) — bridge it
            action, ports, reason = self.bridge.forward(pkt, rx_port)
            meta.action = action
            meta.reason = reason
            meta.flood_ports = ports
            self.stats["packets_bridged"] += 1

        # === Stage 6: QoS Shaping ===
        # Gate on enable_qos alone — previously required traffic_class
        # (ML output), silently disabling QoS when ML was off.
        if self.enable_qos:
            result_pkt, should_fwd = self.traffic_mgr.process(pkt)
            if not should_fwd:
                meta.action = ForwardAction.DROP
                self.stats["packets_dropped"] += 1
                return meta

        # === Stage 7: Forward ===
        # Dropped/trapped packets are outcomes, not successful forwards.
        if meta.action in (ForwardAction.DROP, ForwardAction.TO_CPU):
            if meta.action == ForwardAction.TO_CPU:
                self.stats["packets_trapped"] += 1
            else:
                self.stats["packets_dropped"] += 1
            return meta

        meta.egress_ts = time.time()
        self.stats["packets_processed"] += 1
        self._update_latency_stats(meta.latency_us)

        return meta

    def _resolve_rx_port(self, ingress_iface: str) -> int:
        """Map an interface name to its bridge port id (0 = unknown)."""
        for pid, iface in self.bridge.interfaces.items():
            if iface.name == ingress_iface:
                return pid
        return 0

    def _account_hw_forwarding(self, pkt: ParsedPacket) -> None:
        """Account for the L2/L3 decision hardware makes on offloaded packets.

        Real switches count bridged vs routed flows even when the packet
        never touches the CPU. This mirrors that behavior so dashboard
        stats stay accurate for HW-accelerated traffic.
        """
        if pkt.ipv4 or pkt.ipv6:
            route = self.router.longest_prefix_match(
                pkt.ipv4.dst_ip if pkt.ipv4 else pkt.ipv6.dst_ip
            )
            if route:
                self.stats["packets_routed"] += 1
            else:
                self.stats["packets_bridged"] += 1
        elif pkt.arp or pkt.ethernet:
            self.stats["packets_bridged"] += 1

    def _check_acl_drop(self, src_ip: str, dst_ip: str, src_port: int,
                       dst_port: int, proto: int) -> bool:
        """First-match ACL evaluation over an indexed rule table.

        Buckets are probed most-specific first, but within the probe order
        the FIRST matching rule wins — a matching allow terminates the
        lookup immediately (permit must not be overridden by a later
        catch-all drop; that was a firewall bypass).
        """
        seen = 0
        for key in ((proto, dst_port), (proto, -1), None):
            bucket = self._acl_index.get(key)
            if not bucket:
                continue
            for acl_src, acl_dst, acl_sport, acl_dport, acl_proto, action in bucket:
                if (acl_src in (src_ip, "*") and
                    acl_dst in (dst_ip, "*") and
                    acl_sport in (src_port, -1) and
                    acl_dport in (dst_port, -1) and
                    acl_proto in (proto, -1)):
                    return action.lower() == "drop"  # first match wins
            seen += len(bucket)
        return False

    def _reindex_acls(self) -> None:
        """Rebuild the ACL index. Bucket None = fully-wildcard rules."""
        self._acl_index = {None: []}
        for rule in self.acls:
            src, dst, sport, dport, proto, action = rule
            if proto != -1 and dport != -1:
                key = (proto, dport)
            elif proto != -1:
                key = (proto, -1)
            else:
                key = None  # fully wildcard protocol → scan-always bucket
            self._acl_index.setdefault(key, []).append(rule)

    def _update_latency_stats(self, latency_us: float) -> None:
        """Exponential moving average for latency."""
        alpha = 0.1
        self.stats["avg_latency_us"] = (
            alpha * latency_us +
            (1 - alpha) * self.stats["avg_latency_us"]
        )

    def add_acl(self, src: str, dst: str, sport: int, dport: int,
                proto: int, action: str) -> None:
        """Add an ACL rule."""
        self.acls.append((src, dst, sport, dport, proto, action))
        self._reindex_acls()

    async def run_cleanup(self) -> None:
        """Periodic cleanup task."""
        while True:
            removed = await self.flow_table.cleanup()
            aged = self.bridge.age_out()
            if removed > 0 or aged > 0:
                print(f"[Datapath] Cleaned up {removed} flows, {aged} MAC entries")
            await asyncio.sleep(30)

    def get_stats(self) -> dict:
        """Return comprehensive statistics."""
        stats = self.stats.copy()
        stats["bridge_fdb_size"] = len(self.bridge.mac_table)
        stats["flow_table_size"] = self.flow_table.size
        if self.offload_engine:
            stats["offload"] = self.offload_engine.get_stats()
        return stats
