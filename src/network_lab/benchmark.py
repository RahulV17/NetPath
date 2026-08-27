#!/usr/bin/env python3
"""Benchmark script — measures packet processing throughput.

Run: python -m network_lab.benchmark
"""

import asyncio
import time

from network_lab import DataPathEngine
from network_lab.core.generator import PacketCrafter
from network_lab.core.parser import parse_packet


async def benchmark_parser(iterations: int = 5000) -> dict:
    """Benchmark raw packet parsing speed."""
    crafter = PacketCrafter()
    packets = [
        crafter.arp_request(),
        crafter.ipv4_tcp_syn(),
        crafter.ipv4_tcp_data(),
        crafter.ipv4_udp(),
        crafter.ipv4_icmp_echo(),
        crafter.vxlan_encapsulated(),
        crafter.gre_tunnel(),
        crafter.wifi_beacon(),
        crafter.arp_reply(),
    ]

    # Warmup
    for raw in packets[:5]:
        parse_packet(raw)

    start = time.perf_counter()
    for i in range(iterations):
        raw = packets[i % len(packets)]
        parse_packet(raw, time.time())
    elapsed = time.perf_counter() - start

    return {
        "iterations": iterations,
        "total_seconds": elapsed,
        "packets_per_second": iterations / elapsed,
        "avg_latency_us": (elapsed / iterations) * 1_000_000,
    }


async def benchmark_datapath(iterations: int = 1000) -> dict:
    """Benchmark full data path processing."""
    engine = DataPathEngine(enable_ml=True, enable_hw_offload=True, enable_qos=True)
    engine.router.add_route("10.0.0.0/24", "10.0.0.1", "eth0")
    engine.router.add_route("192.168.1.0/24", "192.168.1.1", "eth1")

    crafter = PacketCrafter()
    packets = [
        parse_packet(crafter.ipv4_udp("10.0.0.1", "10.0.0.2", 12345, 53), time.time()),
        parse_packet(crafter.ipv4_tcp_syn("10.0.0.1", "10.0.0.2", 12345, 80), time.time()),
        parse_packet(crafter.ipv4_tcp_data("10.0.0.1", "192.168.1.2", 12345, 80, b"X" * 100), time.time()),
        parse_packet(crafter.arp_request(), time.time()),
        parse_packet(crafter.vxlan_encapsulated(), time.time()),
    ]

    # Warmup
    for pkt in packets[:3]:
        await engine.process_packet(pkt)

    start = time.perf_counter()
    for i in range(iterations):
        pkt = packets[i % len(packets)]
        await engine.process_packet(pkt)
    elapsed = time.perf_counter() - start

    stats = engine.get_stats()
    offload_stats = stats.get("offload", {})

    return {
        "iterations": iterations,
        "total_seconds": elapsed,
        "packets_per_second": iterations / elapsed,
        "avg_latency_us": (elapsed / iterations) * 1_000_000,
        "packets_processed": stats["packets_processed"],
        "packets_dropped": stats["packets_dropped"],
        "hw_offload_rate": offload_stats.get("hw_offload_rate", 0.0),
        "flow_table_size": engine.flow_table.size,
    }


async def benchmark_qos(iterations: int = 5000) -> dict:
    """Benchmark QoS classification and shaping."""
    from network_lab.core.qos import TrafficManager
    tm = TrafficManager(total_bandwidth_mbps=1000)

    crafter = PacketCrafter()
    packets = [
        parse_packet(crafter.ipv4_tcp_syn("10.0.0.1", "10.0.0.2", 12345, 22)),   # SSH
        parse_packet(crafter.ipv4_udp("10.0.0.1", "10.0.0.2", 12345, 53)),      # DNS
        parse_packet(crafter.ipv4_tcp_syn("10.0.0.1", "10.0.0.2", 12345, 80)),  # HTTP
        parse_packet(crafter.ipv4_udp("10.0.0.1", "10.0.0.2", 12345, 5060)),    # SIP
        parse_packet(crafter.ipv4_tcp_data("10.0.0.1", "10.0.0.2", 12345, 443, b"X" * 100)), # HTTPS
    ]

    # Warmup
    for pkt in packets[:3]:
        tm.process(pkt)

    start = time.perf_counter()
    for i in range(iterations):
        pkt = packets[i % len(packets)]
        tm.process(pkt)
    elapsed = time.perf_counter() - start

    return {
        "iterations": iterations,
        "total_seconds": elapsed,
        "packets_per_second": iterations / elapsed,
        "avg_latency_us": (elapsed / iterations) * 1_000_000,
        "queue_depths": tm.prio_scheduler.queue_depths,
    }


async def benchmark_ml(iterations: int = 100) -> dict:
    """Benchmark ML classification."""
    from network_lab.core.ml_classifier import FlowFeatures, TrafficClassifier
    classifier = TrafficClassifier()

    flows = []
    for i in range(10):
        flow = FlowFeatures(f"10.0.0.{i}", f"10.0.0.{i+10}", 12345, 80, 6)
        for _ in range(20):
            flow.packet_sizes.append(200)
            flow.inter_arrival_times.append(0.02)
        flows.append(flow)

    # Warmup
    await classifier.classify(flows[0])

    start = time.perf_counter()
    for i in range(iterations):
        await classifier.classify(flows[i % len(flows)])
    elapsed = time.perf_counter() - start

    return {
        "iterations": iterations,
        "total_seconds": elapsed,
        "classifications_per_second": iterations / elapsed,
        "avg_latency_us": (elapsed / iterations) * 1_000_000,
    }


async def main():
    print("=" * 70)
    print("NetPath — Performance Benchmarks")
    print("=" * 70)

    print("\n[1/4] Benchmarking Parser...")
    parser_results = await benchmark_parser()
    print(f"  {parser_results['packets_per_second']:.0f} packets/sec")
    print(f"  {parser_results['avg_latency_us']:.1f} us avg latency")

    print("\n[2/4] Benchmarking Data Path (ML + HW Offload + QoS)...")
    datapath_results = await benchmark_datapath()
    print(f"  {datapath_results['packets_per_second']:.0f} packets/sec")
    print(f"  {datapath_results['avg_latency_us']:.1f} us avg latency")
    print(f"  HW offload rate: {datapath_results['hw_offload_rate']:.1%}")

    print("\n[3/4] Benchmarking QoS...")
    qos_results = await benchmark_qos()
    print(f"  {qos_results['packets_per_second']:.0f} packets/sec")
    print(f"  {qos_results['avg_latency_us']:.1f} us avg latency")

    print("\n[4/4] Benchmarking ML Classifier...")
    ml_results = await benchmark_ml()
    print(f"  {ml_results['classifications_per_second']:.0f} classifications/sec")
    print(f"  {ml_results['avg_latency_us']:.1f} us avg latency")

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  Parser:     {parser_results['packets_per_second']:>10.0f} pkt/sec")
    print(f"  Data Path:  {datapath_results['packets_per_second']:>10.0f} pkt/sec")
    print(f"  QoS:        {qos_results['packets_per_second']:>10.0f} pkt/sec")
    print(f"  ML Classify:{ml_results['classifications_per_second']:>10.0f} cls/sec")
    print("=" * 70)


def run_benchmark() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run_benchmark()
