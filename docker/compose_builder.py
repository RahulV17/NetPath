"""Docker Compose topology for integration testing.

Topology:
    sta (Station) ─── ap (Access Point) ─── server (Application Server)
                      (bridge mode)

Each container runs a Python script that simulates network behavior.
"""

from __future__ import annotations


COMPOSE_YAML = """
version: "3.9"

services:
  # ── Access Point (Bridge) ─────────────────────────────────────────────
  ap:
    build:
      context: ..
      dockerfile: docker/Dockerfile.ap
    container_name: netlab-ap
    hostname: ap
    cap_add:
      - NET_ADMIN
    networks:
      sta_net:
        ipv4_address: 10.0.1.1
      srv_net:
        ipv4_address: 10.0.2.1
    command: python -m docker.ap.main

  # ── Station (Client) ─────────────────────────────────────────────────
  sta:
    build:
      context: ..
      dockerfile: docker/Dockerfile.sta
    container_name: netlab-sta
    hostname: sta
    cap_add:
      - NET_ADMIN
    networks:
      sta_net:
        ipv4_address: 10.0.1.100
    depends_on:
      - ap
    command: python -m docker.sta.main

  # ── Server ───────────────────────────────────────────────────────────
  server:
    build:
      context: ..
      dockerfile: docker/Dockerfile.server
    container_name: netlab-server
    hostname: server
    networks:
      srv_net:
        ipv4_address: 10.0.2.100
    ports:
      - "8080:8080"
    depends_on:
      - ap
    command: python -m docker.server.main

networks:
  sta_net:
    driver: bridge
    ipam:
      config:
        - subnet: 10.0.1.0/24
  srv_net:
    driver: bridge
    ipam:
      config:
        - subnet: 10.0.2.0/24
"""


def write_compose_file(path: str = "docker/docker-compose.yml") -> None:
    with open(path, "w") as f:
        f.write(COMPOSE_YAML)


if __name__ == "__main__":
    write_compose_file()
    print("Created docker/docker-compose.yml")
