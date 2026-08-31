<h1 align="center">NetPath</h1>
<p align="center">
  <b>A Pure-Python Userspace Network Stack & Traffic Analyzer</b><br>
  <i>Byte-level protocol parsing, L2/L3 forwarding, ML flow classification, and RFC 2698 QoS — with a real-time 3D visualization lab.</i>
</p>

<p align="center">
  <a href="https://github.com/RahulV17/NetPath/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  </a>
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/FastAPI-0.100+-009688.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-18-61DAFB.svg" alt="React 18">
  <img src="https://img.shields.io/badge/Three.js-r160-black.svg" alt="Three.js">
  <a href="https://github.com/RahulV17/NetPath/actions/workflows/ci.yml">
    <img src="https://github.com/RahulV17/NetPath/actions/workflows/ci.yml/badge.svg" alt="CI status">
  </a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/RahulV17/NetPath/main/docs/screenshots/clip_20260828_111745_3.png" 
       alt="NetPath Interactive 3D Lab" 
       width="90%">
  <br>
  <sub><b>🧪 Interactive 3D Lab</b> — Traffic visualization, per-station telemetry, and live packet tracing.</sub>
</p>

---

## Overview

NetPath is a **networking data-path engineering portfolio project** that simulates what happens *inside* a router — from raw bytes hitting the wire to the final egress decision. Every packet is parsed manually at the byte level, classified by an ML model, routed through L2/L3 tables, shaped by QoS policies, and visualized in real-time through a WebSocket-driven 3D React/Three.js frontend.

> **Built to demonstrate:** Protocol parsing, packet forwarding, AI/ML traffic management, hardware offload modeling, and performance engineering.

---

## The Packet's Journey

Every packet flows through **7 stages** — this is the heart of the project:

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

---

## Features

| Feature | Description | Module |
|---------|-------------|--------|
| **Byte-Level Parsing** | Manual `struct.unpack` of Ethernet, VLAN, ARP, IPv4/IPv6, TCP/UDP, ICMP, VXLAN, GRE, DHCP | `src/network_lab/core/parser.py` |
| **L2 Bridging** | MAC learning table, VLAN tagging, flooding/unicast forwarding | `src/network_lab/core/datapath.py` → `Bridge` |
| **L3 Routing** | Longest-prefix match FIB, ARP snooping, next-hop resolution | `src/network_lab/core/datapath.py` → `Router` |
| **5-Tuple ACL Firewall** | First-match rule engine with wildcard support | `src/network_lab/core/datapath.py` |
| **ML Flow Classification** | RandomForest predicts traffic class (Voice/Video/Bulk/Best-Effort) | `src/network_lab/core/ml_classifier.py` |
| **Hardware Offload Modeling** | Fast-path vs slow-path decision engine with cache simulation | `src/network_lab/core/hw_offload.py` |
| **RFC 2698 QoS** | trTCM policing + HTB/DRR scheduling per traffic class | `src/network_lab/core/qos.py` |
| **Real-Time 3D Lab** | React/Three.js frontend with WebSocket telemetry | `web/` |
| **FastAPI Backend** | REST API + WebSocket for live packet telemetry | `src/network_lab/api/app.py` |
| **Demo Mode** | Standalone scripted walkthrough without server setup | `src/network_lab/demo.py` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      React / Three.js                        │
│                   (3D Network Visualization)                 │
└──────────────────────────┬──────────────────────────────────┘
                           │ WebSocket
┌──────────────────────────▼──────────────────────────────────┐
│                      FastAPI Backend                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐   │
│  │ REST API    │  │ WebSocket   │  │ Packet Pipeline     │   │
│  │ /api/*      │  │ /ws/live    │  │ (7-stage engine)    │   │
│  └─────────────┘  └─────────────┘  └─────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                           │
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
┌─────────┐      ┌──────────┐      ┌──────────┐
│ Parser  │      │ ML Model │      │ QoS      │
│ (L2-L7) │      │ (sklearn)│      │ (trTCM)  │
└─────────┘      └──────────┘      └──────────┘
```

---

## Tech Stack

**Backend**
- Python 3.10+
- FastAPI (async REST + WebSocket)
- scapy (test packet generation only)
- scikit-learn (RandomForest classifier)
- asyncio

**Frontend**
- React 18
- Three.js / @react-three/fiber (3D network topology visualization)
- WebSocket client (real-time telemetry)
- zustand (state management)

**Testing**
- pytest + pytest-asyncio
- Hypothesis (fuzz testing the parser)
- ruff (lint)

---

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js 18+
- pip / npm

### 1. Clone & Install Backend

```bash
git clone https://github.com/RahulV17/NetPath.git
cd NetPath
python -m pip install -e ".[dev]"
```

### 2. Start the Backend

```bash
uvicorn network_lab.api.app:app --reload --port 8000
# API docs: http://localhost:8000/docs
```

### 3. Start the Frontend

```bash
cd web
npm install
npm run dev
# Open http://localhost:3000
```

### 4. Run the Standalone Demo (no server needed)

```bash
python -m network_lab.demo
```

---

## Screenshots

<table align="center">
  <tr>
    <td align="center" width="50%">
      <img src="https://raw.githubusercontent.com/RahulV17/NetPath/main/docs/screenshots/clip_20260828_111708_1.png" width="100%">
      <br>
      <sub><b>3D Pipeline Overview</b></sub>
      <br>
      <sub>All 7 stages from Ingress → Protocol Parser → ACL Filter → ML Classifier → HW Offload → L2/L3 Forwarder → QoS Shaper → Egress</sub>
    </td>
    <td align="center" width="50%">
      <img src="https://raw.githubusercontent.com/RahulV17/NetPath/main/docs/screenshots/clip_20260828_111745_3.png" width="100%">
      <br>
      <sub><b>Interactive Lab Mode</b></sub>
      <br>
      <sub>Traffic Visualization panel, camera controls, and per-station telemetry readouts</sub>
    </td>
  </tr>
</table>

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/stats` | Live pipeline statistics (pps, flows, drops, offload) |
| GET | `/api/protocols` | Protocol distribution counters |
| GET | `/api/flows` | Active flow table with ML predictions |
| POST | `/api/generator/start` | Start synthetic traffic stream (`rate_pps`, `duration`, `src_ip`, `dst_ip`) |
| POST | `/api/generator/stop` | Stop the traffic stream |
| POST | `/api/qos/policy` | Add a QoS policy rule (DSCP marking / rate limit) |
| POST | `/api/acl` | Add an ACL rule (5-tuple, action `allow`/`drop`) |
| WS | `/ws/live` | Real-time telemetry stream at 10 Hz |

---

## Project Structure

```
NetPath/
├── src/network_lab/
│   ├── core/
│   │   ├── parser.py          # Byte-level protocol decoder
│   │   ├── datapath.py        # Bridge, Router, ACL engine
│   │   ├── ml_classifier.py   # RandomForest traffic classifier
│   │   ├── hw_offload.py      # Fast/slow path decision engine
│   │   └── qos.py             # trTCM policing + HTB/DRR scheduling
│   ├── api/
│   │   └── app.py             # FastAPI app with REST + WebSocket
│   ├── cli/
│   │   └── dashboard.py       # Textual terminal dashboard
│   ├── demo.py                # Standalone CLI walkthrough
│   └── benchmark.py           # Throughput/latency benchmark suite
├── web/                       # React + Three.js frontend
├── docker/                    # AP/STA/Server container topology
├── tests/                     # pytest + Hypothesis suite
├── pyproject.toml            # Package config
└── README.md
```

---

## Design Decisions

**Why parse headers manually instead of using dpkt/scapy?**
Because the point is understanding protocols, not gluing libraries. Writing `(tci >> 13) & 0x7` for VLAN PCP proves you've read IEEE 802.1Q. Scapy is used only to generate test packets.

**Why synthetic seed data for ML?**
Lets the system work out-of-the-box with zero setup; archetypes (voice = small/regular, video = large/bursty, bulk = huge/fast) document domain knowledge. The `feedback()` hook shows where real labeled data plugs in.

**Isn't the CPU path slow?**
Deliberately — the slow path pays asyncio locks + sklearn inference (~13 ms). The lesson is that 80–100% of packets take the HW fast path and skip it. Modeling *that trade-off* is the point.

---

## Testing

```bash
# Run the full test suite
pytest tests/ -v

# Run with coverage
coverage run -m pytest -q
coverage report
```

CI runs `ruff` + `pytest` on Python 3.10 and 3.12 for every push/PR.

---

## Roadmap

| Priority | Item | Status |
|----------|------|--------|
| High | Live capture on a real NIC (scapy sniff / AF_XDP) | Planned |
| High | pyroute2 netlink integration (veth pairs, FIB sync) | Planned |
| Med | eBPF/XDP probe for kernel-side counting | Planned |
| Med | IPFIX/NetFlow export from FlowTable | Planned |
| Low | Per-class HTB hierarchy wired to ML end-to-end | Planned |
| Low | Grafana dashboard on the same WebSocket feed | Planned |

---

## Contributing

Contributions are welcome! This is primarily a portfolio/educational project, but if you find bugs or have ideas for new protocol parsers or visualization features, feel free to open an issue or PR.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing-thing`)
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

---

## Author

**Rahul V** — [@RahulV17](https://github.com/RahulV17) · vrahulece@gmail.com

Portfolio project demonstrating networking data-path engineering: protocol parsing, L2/L3 forwarding, ML traffic classification, hardware-offload modeling, and QoS.

---

## License

MIT
