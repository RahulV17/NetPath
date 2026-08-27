# NetPath — Python Network Data Path Simulator & Traffic Analyzer

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
  <img src="https://img.shields.io/badge/frontend-React%2018%20%2B%20Three.js-9cf" alt="frontend">
  <a href="https://github.com/RahulV17/NetPath/actions/workflows/ci.yml"><img src="https://github.com/RahulV17/NetPath/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
</p>

A pure-Python userspace network stack: byte-level protocol parsing (Ethernet→802.11), L2 bridging / L3 routing, ML flow classification, hardware-offload decision engine, and RFC 2698 QoS — exposed through a FastAPI backend and a real-time React/Three.js 3D lab.

> Built to demonstrate the exact skill set of a networking data-path engineer: protocol parsing, packet forwarding, AI/ML traffic management, HW offload mapping, and performance engineering.

**Repository:** https://github.com/RahulV17/NetPath

---

## The Packet's Journey

Every packet flows through 7 stages — this is the heart of the project:

```
raw bytes
   │
   ▼
1. PARSE      manual byte-level decode (struct.unpack)
             Ethernet → VLAN? → ARP or IPv4/IPv6 → TCP/UDP/ICMP
             → VXLAN (4789) or GRE tunnel → DHCP (67/68)
             Malformed layers degrade gracefully — never crash.
   ▼
2. ACL        first-match 5-tuple firewall (DROP or continue)
   ▼
3. FLOW + ML  per-flow stats; RandomForest predicts VOICE/VIDEO/BULK/BE
             (cold flows <5 pkts stay BEST_EFFORT)
   ▼
4. HW OFFLOAD SIMPLE + cache → HW_NIC_OFFLOAD (fast path)
             fragments/options/tunnel → CPU_SLOW_PATH
   ▼
5. BRIDGE/L3  L2 learn/flood or L3 longest-prefix match + ARP snoop
   ▼
6. QoS        RFC 2698 trTCM police → HTB/DRR schedule
   ▼
7. EGRESS     forward (or drop)
```

## Why This Project

Most portfolio projects show CRUD apps. This one shows you understand what happens **inside** a router.

| Networking Concept | Where It Lives |
|---|---|
| Protocol header parsing (L2–L7) | `core/parser.py` |
| L2 bridging (MAC learning, VLANs) | `core/datapath.py` → `Bridge` |
| L3 routing (FIB, longest-prefix match) | `core/datapath.py` → `Router` |
| Firewall / ACLs (5-tuple rules) | `core/datapath.py` → `_check_acl_drop` |
| AI/ML smart traffic management | `core/ml_classifier.py` |
| HW offload vs CPU path decisions | `core/hw_offload.py` |
| QoS shaping, policing, scheduling | `core/qos.py` |
| Observability (stats, live telemetry) | `api/app.py`, `web/` |

## Quick Start

```bash
# Backend (terminal 1)
python -m pip install -e ".[dev]"
uvicorn network_lab.api.app:app --reload --port 8000

# Frontend (terminal 2)
cd web && npm install && npm run dev      # http://localhost:3000

# Or run the scripted walkthrough (no server needed)
python -m network_lab.demo
```

## Design Decisions Worth Discussing

**Why parse headers manually instead of using dpkt/scapy?**
Because the point is understanding protocols, not gluing libraries. Writing `(tci >> 13) & 0x7` for VLAN PCP proves you've read IEEE 802.1Q. Scapy is used only to *generate* test packets.

**Why synthetic seed data for ML?**
Lets the system work out-of-the-box with zero setup; archetypes (voice = small/regular, video = large/bursty, bulk = huge/fast) document domain knowledge. The `feedback()` hook shows where real labeled data plugs in.

**Isn't the CPU path slow?**
Deliberately — the slow path pays asyncio locks + sklearn inference (~13 ms). The lesson is that 80–100% of packets take the HW fast path and skip it. Modeling *that trade-off* is the point.

## Roadmap

- [ ] Live capture on a real NIC (scapy sniff / AF_XDP)
- [ ] pyroute2 netlink integration (veth pairs, FIB sync)
- [ ] eBPF/XDP probe for kernel-side counting
- [ ] IPFIX/NetFlow export from FlowTable
- [ ] Per-class HTB hierarchy wired to ML end-to-end
- [ ] Grafana dashboard on the same WebSocket feed

## Author

**Rahul V** — [@RahulV17](https://github.com/RahulV17) · vrahulece@gmail.com

Portfolio project demonstrating networking data-path engineering: protocol parsing, L2/L3 forwarding, ML traffic classification, hardware-offload modeling, and QoS.

## License

MIT
