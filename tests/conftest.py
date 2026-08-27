"""Shared test fixtures."""

from __future__ import annotations

import pytest

from network_lab.core.datapath import Bridge, DataPathEngine, Interface
from network_lab.core.generator import GeneratorConfig, TrafficGenerator
from network_lab.core.qos import TrafficManager


@pytest.fixture
def bridge():
    """Pre-configured bridge with 3 ports."""
    br = Bridge("br0")
    br.add_interface(Interface(1, "eth0", "00:11:22:33:44:55"))
    br.add_interface(Interface(2, "eth1", "00:11:22:33:44:66"))
    br.add_interface(Interface(3, "eth2", "00:11:22:33:44:77"))
    return br


@pytest.fixture
def engine():
    """Pre-configured data path engine."""
    eng = DataPathEngine()
    eng.bridge.add_interface(Interface(1, "eth0", "00:11:22:33:44:55"))
    eng.bridge.add_interface(Interface(2, "eth1", "00:11:22:33:44:66"))
    return eng


@pytest.fixture
def traffic_manager():
    """Pre-configured traffic manager."""
    return TrafficManager(total_bandwidth_mbps=100)


@pytest.fixture
def generator():
    """Pre-configured traffic generator."""
    config = GeneratorConfig(rate_pps=100, duration=10)
    return TrafficGenerator(config)
