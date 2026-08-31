"""Unit tests for the AnalyticsEngine and WebSocket endpoint."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket

from network_lab.api.app import AnalyticsEngine, websocket_endpoint
from network_lab.core.parser import ParsedPacket


@pytest.fixture
def analytics():
    return AnalyticsEngine()


@pytest.fixture
def sample_packet():
    pkt = ParsedPacket(timestamp=time.time(), raw=b"\x00" * 100)
    pkt.tcp = MagicMock()
    return pkt


@pytest.fixture
def sample_udp_packet():
    pkt = ParsedPacket(timestamp=time.time(), raw=b"\x00" * 200)
    pkt.udp = MagicMock()
    return pkt


@pytest.fixture
def sample_icmp_packet():
    pkt = ParsedPacket(timestamp=time.time(), raw=b"\x00" * 50)
    pkt.icmp = MagicMock()
    return pkt


@pytest.fixture
def sample_arp_packet():
    pkt = ParsedPacket(timestamp=time.time(), raw=b"\x00" * 60)
    pkt.arp = MagicMock()
    return pkt


class TestAnalyticsEngine:
    async def test_ingest_tcp_packet(self, analytics, sample_packet):
        await analytics.ingest(sample_packet)
        assert analytics.protocol_counts["TCP"] == 1
        assert analytics.total_bytes == 100

    async def test_ingest_udp_packet(self, analytics, sample_udp_packet):
        await analytics.ingest(sample_udp_packet)
        assert analytics.protocol_counts["UDP"] == 1
        assert analytics.total_bytes == 200

    async def test_ingest_icmp_packet(self, analytics, sample_icmp_packet):
        await analytics.ingest(sample_icmp_packet)
        assert analytics.protocol_counts["ICMP"] == 1
        assert analytics.total_bytes == 50

    async def test_ingest_arp_packet(self, analytics, sample_arp_packet):
        await analytics.ingest(sample_arp_packet)
        assert analytics.protocol_counts["ARP"] == 1
        assert analytics.total_bytes == 60

    async def test_ingest_multiple_packets(self, analytics, sample_packet, sample_udp_packet):
        await analytics.ingest(sample_packet)
        await analytics.ingest(sample_udp_packet)
        assert analytics.protocol_counts["TCP"] == 1
        assert analytics.protocol_counts["UDP"] == 1
        assert analytics.total_bytes == 300

    async def test_get_protocol_distribution(self, analytics, sample_packet, sample_udp_packet):
        await analytics.ingest(sample_packet)
        await analytics.ingest(sample_udp_packet)
        dist = analytics.get_protocol_distribution()
        assert dist == {"TCP": 1, "UDP": 1}

    async def test_get_total_bytes(self, analytics, sample_packet, sample_udp_packet):
        await analytics.ingest(sample_packet)
        await analytics.ingest(sample_udp_packet)
        assert analytics.get_total_bytes() == 300

    async def test_get_throughput_basic(self, analytics, sample_packet):
        await analytics.ingest(sample_packet)
        result = analytics.get_throughput(window_seconds=10.0)
        assert result["pps"] >= 0
        assert result["total_bytes"] == 100
        assert "TCP" in result["protocols"]
        assert result["window"] == 10.0

    async def test_get_throughput_empty(self, analytics):
        result = analytics.get_throughput(window_seconds=10.0)
        assert result["pps"] == 0.0
        assert result["total_bytes"] == 0
        assert result["protocols"] == {}

    async def test_get_throughput_window_excludes_old(self, analytics, sample_packet):
        # Ingest a packet with an old timestamp
        old_pkt = ParsedPacket(timestamp=time.time() - 20, raw=b"\x00" * 100)
        old_pkt.tcp = MagicMock()
        await analytics.ingest(old_pkt)
        result = analytics.get_throughput(window_seconds=10.0)
        assert result["pps"] == 0.0
        assert result["bytes_per_second"] == 0.0

    async def test_get_throughput_bytes_per_second(self, analytics, sample_packet):
        await analytics.ingest(sample_packet)
        result = analytics.get_throughput(window_seconds=1.0)
        assert result["bytes_per_second"] >= 100

    async def test_concurrent_ingest(self, analytics):
        packets = []
        for i in range(100):
            pkt = ParsedPacket(timestamp=time.time(), raw=b"\x00" * 64)
            pkt.tcp = MagicMock()
            packets.append(pkt)

        await asyncio.gather(*[analytics.ingest(p) for p in packets])
        assert analytics.protocol_counts["TCP"] == 100
        assert analytics.total_bytes == 6400

    async def test_get_throughput_correct_tuple_unpacking(self, analytics):
        """Regression test: ensure timestamp is compared, not protocol."""
        # Ingest packets at different times
        pkt1 = ParsedPacket(timestamp=time.time(), raw=b"\x00" * 100)
        pkt1.tcp = MagicMock()
        await analytics.ingest(pkt1)
        
        # The bug was: _ > cutoff compared string (proto) instead of timestamp
        # This test verifies the fix works
        result = analytics.get_throughput(window_seconds=0.5)
        assert result["pps"] > 0  # Recent packet should be counted


class TestWebSocketEndpoint:
    async def test_websocket_accepts_and_sends_data(self):
        mock_ws = AsyncMock(spec=WebSocket)
        mock_ws.send_json = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()
        # Make send_json raise after 2 calls to exit the infinite loop
        mock_ws.send_json.side_effect = [None, None, Exception("disconnect")]

        await websocket_endpoint(mock_ws)

        mock_ws.accept.assert_called_once()
        assert mock_ws.send_json.call_count >= 2
        # Verify the structure of sent data
        call_args = mock_ws.send_json.call_args_list[0][0][0]
        assert "timestamp" in call_args
        assert "datapath" in call_args
        assert "analytics" in call_args
        assert "flows" in call_args

    async def test_websocket_disconnect_does_not_raise(self):
        mock_ws = AsyncMock(spec=WebSocket)
        mock_ws.send_json = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()
        # Simulate immediate disconnect
        mock_ws.send_json.side_effect = Exception("disconnect")

        # Should not raise - disconnect is caught gracefully
        await websocket_endpoint(mock_ws)