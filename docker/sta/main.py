"""Station container — generates traffic toward the server."""

from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, "/app/src")

from network_lab.core.generator import GeneratorConfig, TrafficGenerator
from network_lab.core.parser import parse_packet


async def main():
    print("[STA] Starting Station (client)")
    print("[STA] IP: 10.0.1.100, Gateway: 10.0.1.1")

    config = GeneratorConfig(
        src_ip="10.0.1.100",
        dst_ip="10.0.2.100",
        rate_pps=10,
        duration=30,
    )
    gen = TrafficGenerator(config)

    print("[STA] Generating traffic...")
    count = 0

    def on_packet(pkt):
        nonlocal count
        count += 1
        if count % 10 == 0:
            print(f"[STA] Sent {count} packets")

    await gen.generate_stream(on_packet)
    print(f"[STA] Done. Sent {count} packets.")
    print(f"[STA] Stats: {gen.stats}")


if __name__ == "__main__":
    asyncio.run(main())
