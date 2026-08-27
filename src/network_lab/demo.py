#!/usr/bin/env python3
"""demo.py — Quick demonstration of the NetPath data path.

Run: python -m network_lab.demo
"""

import asyncio
import time

from network_lab import DataPathEngine
from network_lab.core.generator import PacketCrafter
from network_lab.core.parser import parse_packet


async def main():
    print("=" * 70)
    print("NetPath — Network Data Path Simulator Demo")
    print("=" * 70)

    # Initialize engine with all features
    engine = DataPathEngine(enable_ml=True, enable_hw_offload=True, enable_qos=True)

    # Configure network
    engine.bridge.learn("00:11:22:33:44:55", 1)
    engine.router.add_route("10.0.0.0/24", "10.0.0.1", "eth0")
    engine.router.add_route("192.168.1.0/24", "192.168.1.1", "eth1")
    engine.add_acl("10.0.0.1", "192.168.1.1", -1, 22, 6, "drop")  # Block SSH

    print("\n[Config] Routes and ACLs loaded")
    print(f"  Routes: {len(engine.router.routes)}")
    print(f"  ACLs: {len(engine.acls)}")
    print(f"  MAC table: {len(engine.bridge.mac_table)}")

    # Build sample packets using PacketCrafter
    crafter = PacketCrafter()
    packets = [
        crafter.ipv4_udp("10.0.0.1", "10.0.0.2", 12345, 53, b"\x00" * 200),    # DNS-like
        crafter.ipv4_udp("10.0.0.1", "10.0.0.2", 12345, 53, b"\x00" * 200),
        crafter.ipv4_udp("10.0.0.1", "10.0.0.2", 12345, 53, b"\x00" * 200),
        crafter.ipv4_tcp_data("10.0.0.1", "192.168.1.2", 12345, 80, b"X" * 1400),  # HTTP-like
        crafter.ipv4_tcp_data("10.0.0.1", "192.168.1.2", 12345, 80, b"X" * 1400),
        crafter.ipv4_tcp_syn("10.0.0.1", "192.168.1.1", 12345, 22),   # SSH (should drop)
    ] * 3 + [crafter.ipv4_udp("10.0.0.1", "10.0.0.2", 12345, 53, b"\x00" * 200)] * 2

    # Parse all packets first
    parsed_packets = [parse_packet(bytes(p), time.time()) for p in packets]

    # Simulate traffic
    print("\n[Traffic] Injecting 20 packets...")
    start = time.time()
    for i, pkt in enumerate(parsed_packets):
        meta = await engine.process_packet(pkt, "eth0")
        if i < 5 or i >= len(parsed_packets) - 3:
            action = meta.action.name
            tc = meta.traffic_class.name if meta.traffic_class else "N/A"
            offload = meta.offload_target.name if meta.offload_target else "N/A"
            lat = f"{meta.latency_us:.1f}us" if meta.latency_us > 0 else "offloaded"
            print(f"  Packet {i+1:2d}: {action:8s} | TC: {tc:13s} | Offload: {offload:16s} | {lat}")

    elapsed = time.time() - start

    # Stats
    print("\n[Results]")
    stats = engine.get_stats()
    print(f"  Total processed: {stats['packets_processed']}")
    print(f"  Dropped: {stats['packets_dropped']}")
    print(f"  Bridged: {stats['packets_bridged']}")
    print(f"  Routed:  {stats['packets_routed']}")
    print(f"  Avg latency: {stats['avg_latency_us']:.1f}us")
    print(f"  Throughput: {len(parsed_packets)/elapsed:.0f} pkt/sec")

    if 'offload' in stats:
        off = stats['offload']
        print("\n[Offload]")
        print(f"  HW accelerated: {off['hw_accelerated']}")
        print(f"  CPU exceptions: {off['cpu_exceptions']}")
        print(f"  HW cache util:  {off['hw_cache_utilization']:.1%}")
        print(f"  Offload rate:   {off['hw_offload_rate']:.1%}")

    print("\n[ML Classifier]")
    print(f"  Flow table size: {engine.flow_table.size}")
    print(f"  Classifier enabled: {engine.enable_ml}")

    print("\n" + "=" * 70)
    print("Demo complete!")
    print("=" * 70)


def run_demo() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run_demo()
