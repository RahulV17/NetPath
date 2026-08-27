"""Server container — receives traffic and runs analytics API."""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/app/src")

import uvicorn

from network_lab.api.app import app


async def main():
    print("[SERVER] Starting Application Server")
    print("[SERVER] IP: 10.0.2.100, API port: 8080")

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
