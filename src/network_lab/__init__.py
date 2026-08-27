"""NetPath — Python Network Data Path Simulator + Traffic Analyzer.

Combines the best of two projects:
- network-lab: full protocol parser, generator, bridge/router, QoS, API, dashboard, Docker
- PyFlow: ML classifier, HW offload engine, ACL, flow table, latency tracking

Demonstrates skills from Qualcomm's Software Engineering JD:
- Manual byte-level protocol parsing (Ethernet, ARP, IPv4/6, TCP/UDP, ICMP, DHCP, 802.11, VXLAN, GRE)
- L2/L3 forwarding (bridge, router with longest-prefix match)
- AI/ML traffic classification (Random Forest)
- Hardware offload decision engine (CPU vs HW acceleration)
- QoS traffic management (token bucket, HTB scheduling, DSCP classification)
- Real-time analytics via WebSocket
- Docker-based network topology for integration testing
"""

__version__ = "0.2.0"

from .core.datapath import DataPathEngine, ForwardAction, PacketMeta
from .core.generator import GeneratorConfig, PacketCrafter, TrafficGenerator
from .core.hw_offload import OffloadEngine, OffloadTarget, PacketProfile, profile_from_parsed_packet
from .core.ml_classifier import FlowFeatures, FlowTable, TrafficClass, TrafficClassifier
from .core.parser import ParsedPacket, parse_packet
from .core.qos import HTBScheduler, PriorityScheduler, TokenBucket, TrafficManager

__all__ = [
    "DataPathEngine",
    "FlowFeatures",
    "FlowTable",
    "ForwardAction",
    "GeneratorConfig",
    "HTBScheduler",
    "OffloadEngine",
    "OffloadTarget",
    "PacketCrafter",
    "PacketMeta",
    "PacketProfile",
    "ParsedPacket",
    "PriorityScheduler",
    "TokenBucket",
    "TrafficClass",
    "TrafficClassifier",
    "TrafficGenerator",
    "TrafficManager",
    "parse_packet",
    "profile_from_parsed_packet",
]
