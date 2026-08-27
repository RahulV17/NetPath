"""Manual byte-level protocol parser (L2-L7).

Parses raw packet bytes into structured dataclasses without external libraries.
Demonstrates deep understanding of network protocol headers (Ethernet, ARP,
IPv4, IPv6, TCP, UDP, ICMP, DHCP, 802.11 WiFi).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import ClassVar


# ── EtherTypes ────────────────────────────────────────────────────────────
class EtherType(IntEnum):
    IPv4 = 0x0800
    ARP = 0x0806
    IPv6 = 0x86DD
    VLAN = 0x8100
    # 802.11 QoS
    QOS = 0x888E


# ── IP Protocol Numbers ──────────────────────────────────────────────────
class IPProto(IntEnum):
    ICMP = 1
    TCP = 6
    UDP = 17
    IPv6_ROUTE = 43
    IPv6_FRAG = 44
    IPv6_ICMP = 58
    IPv6_NONE = 59
    IPv6_OPTS = 60
    GRE = 47


# ── DHCP Message Types ───────────────────────────────────────────────────
class DHCPMsgType(IntEnum):
    DISCOVER = 1
    OFFER = 2
    REQUEST = 3
    ACK = 5


# ── TCP Flags ────────────────────────────────────────────────────────────
class TCPFlag(IntEnum):
    FIN = 0x01
    SYN = 0x02
    RST = 0x04
    PSH = 0x08
    ACK = 0x10
    URG = 0x20
    ECE = 0x40
    CWR = 0x80


# ── 802.11 Frame Types ──────────────────────────────────────────────────
class WiFiType(IntEnum):
    MANAGEMENT = 0
    CONTROL = 1
    DATA = 2


class WiFiSubtype(IntEnum):
    # Management
    ASSOC_REQ = 0
    ASSOC_RESP = 1
    PROBE_REQ = 4
    PROBE_RESP = 5
    BEACON = 8
    AUTH = 11
    DEAUTH = 12
    # DATA
    DATA_FRAME = 0
    QOS_DATA = 8


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 2 — Data Link
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Ethernet:
    dst_mac: str
    src_mac: str
    ethertype: int
    payload: bytes

    FORMAT: ClassVar[str] = "!6s6sH"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @classmethod
    def parse(cls, raw: bytes) -> Ethernet:
        if len(raw) < cls.SIZE:
            raise ValueError(f"Ethernet header too short: {len(raw)} bytes")
        dst, src, etype = struct.unpack(cls.FORMAT, raw[:cls.SIZE])
        return cls(
            dst_mac=_format_mac(dst),
            src_mac=_format_mac(src),
            ethertype=etype,
            payload=raw[cls.SIZE:],
        )


@dataclass(frozen=True)
class VLAN:
    pcp: int  # Priority Code Point (3 bits)
    dei: int  # Drop Eligible Indicator (1 bit)
    vid: int  # VLAN ID (12 bits)
    inner_ethertype: int
    payload: bytes

    FORMAT: ClassVar[str] = "!HH"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @classmethod
    def parse(cls, raw: bytes) -> VLAN:
        if len(raw) < cls.SIZE:
            raise ValueError(f"VLAN tag too short: {len(raw)} bytes")
        tci, etype = struct.unpack(cls.FORMAT, raw[:cls.SIZE])
        return cls(
            pcp=(tci >> 13) & 0x7,
            dei=(tci >> 12) & 0x1,
            vid=tci & 0xFFF,
            inner_ethertype=etype,
            payload=raw[cls.SIZE:],
        )


@dataclass(frozen=True)
class ARP:
    hw_type: int
    proto_type: int
    hw_len: int
    proto_len: int
    opcode: int
    sender_mac: str
    sender_ip: str
    target_mac: str
    target_ip: str

    FORMAT: ClassVar[str] = "!HHBBH6s4s6s4s"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @classmethod
    def parse(cls, raw: bytes) -> ARP:
        if len(raw) < cls.SIZE:
            raise ValueError(f"ARP packet too short: {len(raw)} bytes")
        hw, proto, hw_l, proto_l, op, smac, sip, tmac, tip = struct.unpack(
            cls.FORMAT, raw[:cls.SIZE]
        )
        return cls(
            hw_type=hw,
            proto_type=proto,
            hw_len=hw_l,
            proto_len=proto_l,
            opcode=op,
            sender_mac=_format_mac(smac),
            sender_ip=_format_ipv4(sip),
            target_mac=_format_mac(tmac),
            target_ip=_format_ipv4(tip),
        )


# ═══════════════════════════════════════════════════════════════════════════
# 802.11 WiFi (simplified — no radiotap, just 802.11 MAC header)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class WiFi:
    frame_control: int
    duration: int
    addr1: str  # RA / DA
    addr2: str  # TA / SA
    addr3: str  # BSSID
    seq_ctrl: int
    addr4: str | None  # optional (WDS)
    qos_ctrl: int | None
    payload: bytes

    FORMAT: ClassVar[str] = "<HH6s6s6sH"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)  # 24 — standard MAC header

    @property
    def type(self) -> int:
        return (self.frame_control >> 2) & 0x3

    @property
    def subtype(self) -> int:
        return (self.frame_control >> 4) & 0xF

    @property
    def to_ds(self) -> int:
        return (self.frame_control >> 8) & 0x1

    @property
    def from_ds(self) -> int:
        return (self.frame_control >> 9) & 0x1

    @property
    def is_qos(self) -> bool:
        return self.type == WiFiType.DATA and self.subtype == WiFiSubtype.QOS_DATA

    @classmethod
    def parse(cls, raw: bytes) -> WiFi:
        if len(raw) < cls.SIZE:
            raise ValueError(f"802.11 header too short: {len(raw)} bytes")
        # FC/duration/seq are little-endian over the air; Sequence Control
        # is ONE 16-bit field (frag<<8 | seq) — not two separate H fields.
        fc, dur, a1, a2, a3, seq = struct.unpack("<HH6s6s6sH", raw[:cls.SIZE])
        offset = cls.SIZE
        addr4 = None
        qos = None
        # WDS 4-address mode: BOTH to_ds and from_ds set
        if ((fc >> 8) & 0x3) == 0x3 and len(raw) >= offset + 6:
            addr4 = _format_mac(raw[offset:offset+6])
            offset += 6
        # QoS field for QoS-data frames
        subtype = (fc >> 4) & 0xF
        if ((fc >> 2) & 0x3) == WiFiType.DATA and subtype == WiFiSubtype.QOS_DATA:
            if len(raw) >= offset + 2:
                qos = struct.unpack("<H", raw[offset:offset+2])[0]
                offset += 2
        return cls(
            frame_control=fc,
            duration=dur,
            addr1=_format_mac(a1),
            addr2=_format_mac(a2),
            addr3=_format_mac(a3),
            seq_ctrl=seq,
            addr4=addr4,
            qos_ctrl=qos,
            payload=raw[offset:],
        )


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 3 — Network
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class IPv4:
    version: int
    ihl: int  # header length in 32-bit words
    dscp: int  # DSCP (6 bits)
    ecn: int  # Explicit Congestion Notification (2 bits)
    total_length: int
    identification: int
    flags: int  # 3 bits
    fragment_offset: int  # 13 bits
    ttl: int
    protocol: int
    checksum: int
    src_ip: str
    dst_ip: str
    options: bytes
    payload: bytes

    FORMAT: ClassVar[str] = "!BBHHHBBH4s4s"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @property
    def header_length(self) -> int:
        return self.ihl * 4

    @property
    def df(self) -> int:  # Don't Fragment
        return (self.flags >> 1) & 0x1

    @property
    def mf(self) -> int:  # More Fragments
        return self.flags & 0x1

    @classmethod
    def parse(cls, raw: bytes) -> IPv4:
        if len(raw) < cls.SIZE:
            raise ValueError(f"IPv4 header too short: {len(raw)} bytes")
        ver_ihl, dscp_ecn, total_len, ident, flags_fo, ttl, proto, cksum, src, dst = (
            struct.unpack(cls.FORMAT, raw[:cls.SIZE])
        )
        version = (ver_ihl >> 4) & 0xF
        ihl = ver_ihl & 0xF
        if version != 4:
            raise ValueError(f"invalid IPv4 version: {version}")
        if ihl < 5:
            raise ValueError(f"invalid IPv4 IHL: {ihl}")
        hdr_len = ihl * 4
        options = raw[cls.SIZE:hdr_len] if hdr_len > cls.SIZE else b""
        return cls(
            version=version,
            ihl=ihl,
            dscp=dscp_ecn >> 2,
            ecn=dscp_ecn & 0x3,
            total_length=total_len,
            identification=ident,
            flags=(flags_fo >> 13) & 0x7,
            fragment_offset=flags_fo & 0x1FFF,
            ttl=ttl,
            protocol=proto,
            checksum=cksum,
            src_ip=_format_ipv4(src),
            dst_ip=_format_ipv4(dst),
            options=options,
            payload=raw[hdr_len:],
        )


@dataclass(frozen=True)
class IPv6:
    version: int
    traffic_class: int
    flow_label: int
    payload_length: int
    next_header: int
    hop_limit: int
    src_ip: str
    dst_ip: str
    payload: bytes

    FORMAT: ClassVar[str] = "!IHBB16s16s"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @classmethod
    def parse(cls, raw: bytes) -> IPv6:
        if len(raw) < cls.SIZE:
            raise ValueError(f"IPv6 header too short: {len(raw)} bytes")
        ver_tc_fl, payload_len, next_hdr, hop, src, dst = struct.unpack(
            cls.FORMAT, raw[:cls.SIZE]
        )
        return cls(
            version=(ver_tc_fl >> 28) & 0xF,
            traffic_class=(ver_tc_fl >> 20) & 0xFF,
            flow_label=ver_tc_fl & 0xFFFFF,
            payload_length=payload_len,
            next_header=next_hdr,
            hop_limit=hop,
            src_ip=_format_ipv6(src),
            dst_ip=_format_ipv6(dst),
            payload=raw[cls.SIZE:],
        )


@dataclass(frozen=True)
class ICMP:
    type: int
    code: int
    checksum: int
    identifier: int | None
    sequence: int | None
    payload: bytes

    FORMAT: ClassVar[str] = "!BBH"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @classmethod
    def parse(cls, raw: bytes) -> ICMP:
        if len(raw) < cls.SIZE:
            raise ValueError(f"ICMP header too short: {len(raw)} bytes")
        typ, code, cksum = struct.unpack(cls.FORMAT, raw[:cls.SIZE])
        ident, seq = None, None
        if typ in (0, 8) and len(raw) >= cls.SIZE + 4:  # Echo Reply/Request
            ident, seq = struct.unpack("!HH", raw[cls.SIZE:cls.SIZE+4])
        return cls(
            type=typ,
            code=code,
            checksum=cksum,
            identifier=ident,
            sequence=seq,
            payload=raw[cls.SIZE+4:] if ident is not None else raw[cls.SIZE:],
        )


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 4 — Transport
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TCP:
    src_port: int
    dst_port: int
    seq_num: int
    ack_num: int
    data_offset: int  # in 32-bit words
    flags: int
    window: int
    checksum: int
    urgent: int
    options: bytes
    payload: bytes

    FORMAT: ClassVar[str] = "!HHIIBBHHH"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @property
    def header_length(self) -> int:
        return self.data_offset * 4

    @property
    def flag_names(self) -> list[str]:
        return [f.name for f in TCPFlag if self.flags & f]

    @classmethod
    def parse(cls, raw: bytes) -> TCP:
        if len(raw) < cls.SIZE:
            raise ValueError(f"TCP header too short: {len(raw)} bytes")
        sp, dp, seq, ack, off_res, flags, win, cksum, urg = struct.unpack(
            cls.FORMAT, raw[:cls.SIZE]
        )
        data_offset = (off_res >> 4) & 0xF
        hdr_len = data_offset * 4
        options = raw[cls.SIZE:hdr_len] if hdr_len > cls.SIZE else b""
        return cls(
            src_port=sp,
            dst_port=dp,
            seq_num=seq,
            ack_num=ack,
            data_offset=data_offset,
            flags=flags,
            window=win,
            checksum=cksum,
            urgent=urg,
            options=options,
            payload=raw[hdr_len:],
        )


@dataclass(frozen=True)
class UDP:
    src_port: int
    dst_port: int
    length: int
    checksum: int
    payload: bytes

    FORMAT: ClassVar[str] = "!HHHH"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @classmethod
    def parse(cls, raw: bytes) -> UDP:
        if len(raw) < cls.SIZE:
            raise ValueError(f"UDP header too short: {len(raw)} bytes")
        sp, dp, length, cksum = struct.unpack(cls.FORMAT, raw[:cls.SIZE])
        return cls(
            src_port=sp,
            dst_port=dp,
            length=length,
            checksum=cksum,
            payload=raw[cls.SIZE:],
        )


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 7 — Application
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DHCP:
    op: int  # 1=request, 2=reply
    htype: int
    hlen: int
    hops: int
    xid: int
    secs: int
    flags: int
    ciaddr: str
    yiaddr: str
    siaddr: str
    giaddr: str
    chaddr: str
    magic: int
    msg_type: int | None
    options_raw: bytes

    FORMAT: ClassVar[str] = "!BBBBIHH4s4s4s4s16s"  # op..flags + addrs + chaddr
    SIZE: ClassVar[int] = 236  # full header: 12 + 16 + 16(chaddr) + 64(sname) + 128(file)
    MAGIC: ClassVar[int] = 0x63825363

    @classmethod
    def parse(cls, raw: bytes) -> DHCP:
        if len(raw) < cls.SIZE:
            raise ValueError(f"DHCP packet too short: {len(raw)} bytes")
        op, htype, hlen, hops, xid, secs, flags, ci, yi, si, gi, ch = struct.unpack(
            cls.FORMAT, raw[:struct.calcsize(cls.FORMAT)]
        )
        # Magic cookie lives right after sname+file (offset 236); exactly-236
        # byte packets have no cookie — treat as no options, don't IndexError.
        options_start = cls.SIZE
        magic = 0
        if len(raw) >= options_start + 4:
            magic = struct.unpack("!I", raw[options_start:options_start+4])[0]
        msg_type = None
        if magic == cls.MAGIC:
            # Scan options for message type (option 53)
            opt_raw = raw[options_start+4:]
            i = 0
            while i < len(opt_raw):
                opt = opt_raw[i]
                if opt == 255:
                    break
                if opt == 0:
                    i += 1
                    continue
                if i + 1 >= len(opt_raw):
                    break
                opt_len = opt_raw[i+1]
                # Bounds-check the value bytes before reading (option 53 read
                # could IndexError on a truncated buffer)
                if i + 2 + opt_len > len(opt_raw):
                    break
                if opt == 53 and opt_len == 1:
                    msg_type = opt_raw[i+2]
                i += 2 + opt_len
        return cls(
            op=op, htype=htype, hlen=hlen, hops=hops, xid=xid,
            secs=secs, flags=flags, ciaddr=_format_ipv4(ci),
            yiaddr=_format_ipv4(yi), siaddr=_format_ipv4(si),
            giaddr=_format_ipv4(gi), chaddr=_format_mac(ch[:hlen]),
            magic=magic, msg_type=msg_type, options_raw=raw[options_start+4:],
        )


# ═══════════════════════════════════════════════════════════════════════════
# TUNNELING — VXLAN / GRE
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class VXLAN:
    flags: int
    vni: int  # 24-bit VXLAN Network Identifier
    payload: bytes  # inner Ethernet frame

    FORMAT: ClassVar[str] = "!II"
    SIZE: ClassVar[int] = 8

    @classmethod
    def parse(cls, raw: bytes) -> VXLAN:
        if len(raw) < cls.SIZE:
            raise ValueError(f"VXLAN header too short: {len(raw)} bytes")
        # Wire layout: flags(1B) | reserved(3B) | VNI(3B) | reserved(1B)
        flags_byte = raw[0]
        vni = int.from_bytes(raw[4:7], 'big')
        return cls(
            flags=flags_byte,
            vni=vni,
            payload=raw[cls.SIZE:],
        )


@dataclass(frozen=True)
class GRE:
    flags: int
    protocol: int
    payload: bytes

    FORMAT: ClassVar[str] = "!HH"
    SIZE: ClassVar[int] = struct.calcsize(FORMAT)

    @classmethod
    def parse(cls, raw: bytes) -> GRE:
        if len(raw) < cls.SIZE:
            raise ValueError(f"GRE header too short: {len(raw)} bytes")
        cksum_flags, proto = struct.unpack(cls.FORMAT, raw[:cls.SIZE])
        return cls(
            flags=cksum_flags,
            protocol=proto,
            payload=raw[cls.SIZE:],
        )


# ═══════════════════════════════════════════════════════════════════════════
# TOP-LEVEL PARSED PACKET
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ParsedPacket:
    """Fully parsed packet with all layers accessible."""
    timestamp: float
    raw: bytes
    ethernet: Ethernet | None = None
    vlan: VLAN | None = None
    arp: ARP | None = None
    ipv4: IPv4 | None = None
    ipv6: IPv6 | None = None
    icmp: ICMP | None = None
    tcp: TCP | None = None
    udp: UDP | None = None
    dhcp: DHCP | None = None
    vxlan: VXLAN | None = None
    gre: GRE | None = None
    wifi: WiFi | None = None
    # Flow key (5-tuple when available)
    flow_key: tuple | None = None

    @property
    def protocol_chain(self) -> list[str]:
        chain = []
        if self.ethernet:
            chain.append("Ethernet")
        if self.vlan:
            chain.append(f"VLAN({self.vlan.vid})")
        if self.arp:
            chain.append("ARP")
        if self.ipv4:
            chain.append("IPv4")
        if self.ipv6:
            chain.append("IPv6")
        if self.gre:
            chain.append("GRE")
        if self.vxlan:
            chain.append(f"VXLAN(vni={self.vxlan.vni})")
        if self.icmp:
            chain.append("ICMP")
        if self.tcp:
            chain.append("TCP")
        if self.udp:
            chain.append("UDP")
        if self.dhcp:
            chain.append("DHCP")
        return chain

    @property
    def summary(self) -> str:
        parts = self.protocol_chain
        if self.tcp:
            return f"{' -> '.join(parts)} {self.ipv4.src_ip if self.ipv4 else ''}:{self.tcp.src_port} → {self.ipv4.dst_ip if self.ipv4 else ''}:{self.tcp.dst_port} [{','.join(self.tcp.flag_names)}]"
        if self.udp:
            return f"{' -> '.join(parts)} {self.ipv4.src_ip if self.ipv4 else ''}:{self.udp.src_port} → {self.ipv4.dst_ip if self.ipv4 else ''}:{self.udp.dst_port}"
        if self.icmp:
            return f"{' -> '.join(parts)} type={self.icmp.type} code={self.icmp.code}"
        if self.arp:
            return f"{' -> '.join(parts)} {self.arp.sender_ip} → {self.arp.target_ip} op={self.arp.opcode}"
        return " -> ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# PARSER ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def parse_packet(raw: bytes, timestamp: float = 0.0) -> ParsedPacket:
    """Parse raw bytes into a structured ParsedPacket (L2-L7)."""
    pkt = ParsedPacket(timestamp=timestamp, raw=raw)

    # ── L2: Ethernet ──
    if len(raw) < Ethernet.SIZE:
        return pkt
    pkt.ethernet = Ethernet.parse(raw)
    payload = pkt.ethernet.payload
    etype = pkt.ethernet.ethertype

    # Each subsequent layer degrades gracefully: a truncated or malformed
    # inner header keeps the layers parsed so far (never raises to caller).
    def _safe(fn, *args):
        try:
            return fn(*args)
        except (ValueError, struct.error, IndexError):
            return None

    # VLAN tag
    if etype == EtherType.VLAN:
        vlan = _safe(VLAN.parse, payload)
        if vlan is None:
            return pkt
        pkt.vlan = vlan
        payload = vlan.payload
        etype = vlan.inner_ethertype

    # ── L3: ARP / IPv4 / IPv6 ──
    if etype == EtherType.ARP:
        arp = _safe(ARP.parse, payload)
        if arp is not None:
            pkt.arp = arp
            _set_flow_key(pkt)
        return pkt

    if etype == EtherType.IPv4:
        ipv4 = _safe(IPv4.parse, payload)
        if ipv4 is None:
            return pkt
        pkt.ipv4 = ipv4
        payload = ipv4.payload
        proto = ipv4.protocol
        # Non-first fragments carry no L4 header — parsing payload bytes as
        # TCP/UDP fabricates phantom flows. Record the fragment and stop.
        if ipv4.fragment_offset != 0:
            _set_flow_key(pkt)
            return pkt
    elif etype == EtherType.IPv6:
        ipv6 = _safe(IPv6.parse, payload)
        if ipv6 is None:
            return pkt
        pkt.ipv6 = ipv6
        payload = ipv6.payload
        proto = ipv6.next_header
    else:
        return pkt  # Unknown L3

    # ── L4: ICMP / TCP / UDP ──
    if proto == IPProto.ICMP:
        icmp = _safe(ICMP.parse, payload)
        if icmp is not None:
            pkt.icmp = icmp
    elif proto == IPProto.TCP:
        tcp = _safe(TCP.parse, payload)
        if tcp is not None:
            pkt.tcp = tcp
            payload = tcp.payload
    elif proto == IPProto.UDP:
        udp = _safe(UDP.parse, payload)
        if udp is not None:
            pkt.udp = udp
            payload = udp.payload
            # Check for VXLAN over UDP port 4789
            if udp.dst_port == 4789 or udp.src_port == 4789:
                vx = _safe(VXLAN.parse, payload)
                if vx is not None and len(vx.payload) > 0:
                    pkt.vxlan = vx
    elif proto == IPProto.GRE:
        gre = _safe(GRE.parse, payload)
        if gre is not None:
            pkt.gre = gre
            payload = gre.payload
            # GRE with ethertype 0x0800 carries an inner IPv4 frame
            if gre.protocol == EtherType.IPv4:
                inner = _safe(IPv4.parse, payload)
                if inner is not None:
                    pkt.ipv4 = inner
                    payload = inner.payload
                    proto = inner.protocol
                    if proto == IPProto.TCP:
                        itcp = _safe(TCP.parse, payload)
                        if itcp is not None:
                            pkt.tcp = itcp
                    elif proto == IPProto.UDP:
                        iudp = _safe(UDP.parse, payload)
                        if iudp is not None:
                            pkt.udp = iudp
                            payload = iudp.payload  # advance for L7 (DHCP)
        else:
            _set_flow_key(pkt)
            return pkt
    else:
        _set_flow_key(pkt)
        return pkt

    # ── L7: DHCP (over UDP 67/68) ──
    if pkt.udp and (pkt.udp.dst_port == 67 or pkt.udp.dst_port == 68) and not pkt.vxlan:
        dhcp = _safe(DHCP.parse, payload)
        if dhcp is not None:
            pkt.dhcp = dhcp

    _set_flow_key(pkt)
    return pkt


def parse_wifi_frame(raw: bytes, timestamp: float = 0.0) -> ParsedPacket:
    """Parse an 802.11 WiFi frame (with optional LLC/SNAP → EtherType)."""
    pkt = ParsedPacket(timestamp=timestamp, raw=raw)

    def _safe(fn, *args):
        try:
            return fn(*args)
        except (ValueError, struct.error, IndexError):
            return None

    wifi = _safe(WiFi.parse, raw)
    if wifi is None:            # truncated/corrupt frame — nothing parsed
        return pkt
    pkt.wifi = wifi
    payload = wifi.payload

    # LLC/SNAP header (8 bytes) → EtherType
    if len(payload) >= 8:
        llc = struct.unpack("!BBB", payload[:3])
        if llc == (0xAA, 0xAA, 0x03):  # SNAP
            etype = struct.unpack("!H", payload[6:8])[0]
            payload = payload[8:]
            if etype == EtherType.IPv4:
                ipv4 = _safe(IPv4.parse, payload)
                if ipv4 is not None:
                    pkt.ipv4 = ipv4
                    proto = ipv4.protocol
                    if proto == IPProto.TCP:
                        tcp = _safe(TCP.parse, ipv4.payload)
                        if tcp is not None:
                            pkt.tcp = tcp
                    elif proto == IPProto.UDP:
                        udp = _safe(UDP.parse, ipv4.payload)
                        if udp is not None:
                            pkt.udp = udp
            elif etype == EtherType.ARP:
                arp = _safe(ARP.parse, payload)
                if arp is not None:
                    pkt.arp = arp
    _set_flow_key(pkt)
    return pkt


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _format_mac(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw[:6])


def _format_ipv4(raw: bytes) -> str:
    return ".".join(str(b) for b in raw[:4])


def _format_ipv6(raw: bytes) -> str:
    # 16 bytes → 8 groups of 4 hex chars
    groups = [raw[i:i+2].hex() for i in range(0, 16, 2)]
    return ":".join(groups)


def _set_flow_key(pkt: ParsedPacket) -> None:
    """Set 5-tuple flow key when possible."""
    if pkt.tcp and pkt.ipv4:
        pkt.flow_key = (pkt.ipv4.src_ip, pkt.ipv4.dst_ip, pkt.tcp.src_port, pkt.tcp.dst_port, 6)
    elif pkt.udp and pkt.ipv4:
        pkt.flow_key = (pkt.ipv4.src_ip, pkt.ipv4.dst_ip, pkt.udp.src_port, pkt.udp.dst_port, 17)
    elif pkt.icmp and pkt.ipv4:
        pkt.flow_key = (pkt.ipv4.src_ip, pkt.ipv4.dst_ip, pkt.icmp.identifier or 0, pkt.icmp.sequence or 0, 1)
    elif pkt.tcp and pkt.ipv6:
        pkt.flow_key = (pkt.ipv6.src_ip, pkt.ipv6.dst_ip, pkt.tcp.src_port, pkt.tcp.dst_port, 6)
    elif pkt.udp and pkt.ipv6:
        pkt.flow_key = (pkt.ipv6.src_ip, pkt.ipv6.dst_ip, pkt.udp.src_port, pkt.udp.dst_port, 17)
