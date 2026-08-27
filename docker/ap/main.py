"""Access Point container — bridges two networks."""

from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, "/app/src")

from network_lab.core.datapath import Bridge, DataPathEngine, Interface
from network_lab.core.generator import PacketCrafter
from network_lab.core.parser import parse_packet


async def main():
    print("[AP] Starting Access Point (bridge mode)")
    print("[AP] Interfaces: eth0 (10.0.1.1) <--> eth1 (10.0.2.1)")

    engine = DataPathEngine()
    engine.bridge.add_interface(Interface(1, "eth0", "00:11:22:33:44:55"))
    engine.bridge.add_interface(Interface(2, "eth1", "00:11:22:33:44:66"))

    # Simulate forwarding some packets
    crafter = PacketCrafter()
    packets = [
        crafter.arp_request("00:11:22:33:44:aa", "10.0.1.100", "10.0.1.1"),
        crafter.ipv4_tcp_syn("10.0.1.100", "10.0.2.100", 12345, 80),
        crafter.ipv4_udp("10.0.1.100", "10.0.2.100", 50000, 53),
    ]

    for raw in packets:
        pkt = parse_packet(raw, time.time())
        meta = await engine.process_packet(pkt, ingress_iface="eth0")
        print(f"[AP] {pkt.summary} -> {meta.action.name}")

    print(f"[AP] Stats: {engine.stats}")
    print("[AP] Bridge running (Ctrl+C to stop)")

    # Keep running
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
