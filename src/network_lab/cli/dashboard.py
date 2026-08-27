"""CLI Dashboard — terminal-based live traffic monitor using textual.

Fixed version: uses ForwardAction, correct stats keys, async process_packet,
and displays ML + HW offload stats.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from ..core.datapath import DataPathEngine, ForwardAction, Interface
from ..core.generator import GeneratorConfig, TrafficGenerator
from ..core.parser import ParsedPacket
from ..core.ml_classifier import TrafficClass
from ..core.hw_offload import OffloadTarget


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD SCREEN
# ═══════════════════════════════════════════════════════════════════════════

class DashboardScreen(Screen):
    """Main dashboard with live stats."""

    datapath: DataPathEngine = None
    generator: TrafficGenerator | None = None

    # Reactive stats
    packet_count = reactive(0)
    bridged_count = reactive(0)
    routed_count = reactive(0)
    dropped_count = reactive(0)
    flow_count = reactive(0)
    hw_offload_rate = reactive(0.0)
    avg_latency_us = reactive(0.0)

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("g", "start_generator", "Start Gen"),
        ("s", "stop_generator", "Stop Gen"),
        ("r", "reset_stats", "Reset"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.datapath = DataPathEngine(enable_ml=True, enable_hw_offload=True, enable_qos=True)
        # The CLI runs a real dequeue loop (QoSStats), so mark the drain
        # active so the priority scheduler is actually exercised.
        self.datapath.traffic_mgr.enable_drain()
        # Add some interfaces
        self.datapath.bridge.add_interface(Interface(1, "eth0", "00:11:22:33:44:55"))
        self.datapath.bridge.add_interface(Interface(2, "eth1", "00:11:22:33:44:66"))
        self.datapath.router.add_interface("eth0", Interface(1, "eth0", "00:11:22:33:44:55", "10.0.0.1"))
        self.datapath.router.add_route("10.0.0.0/24", iface="eth0")
        self._start_time = time.time()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Overview", id="overview"):
                with Horizontal():
                    with Vertical():
                        yield StatCard("Packets", "packet_count", id="pkts")
                        yield StatCard("Bridged", "bridged_count", id="bridged")
                    with Vertical():
                        yield StatCard("Routed", "routed_count", id="routed")
                        yield StatCard("Dropped", "dropped_count", id="dropped")
                with Horizontal():
                    with Vertical():
                        yield StatCard("Flows", "flow_count", id="flows")
                        yield StatCard("HW Offload %", "hw_offload_rate", id="hw")
                    with Vertical():
                        yield StatCard("Latency (us)", "avg_latency_us", id="lat")
                        yield StatCard("PPS", "pps", id="pps")
            with TabPane("Flows", id="flows-tab"):
                yield MLFlowTable()
            with TabPane("Protocols", id="protocols-tab"):
                yield ProtocolChart(id="proto-chart")
            with TabPane("QoS", id="qos-tab"):
                yield QoSStats(id="qos-stats")
            with TabPane("HW Offload", id="hw-tab"):
                yield HWOffloadStats(id="hw-stats")
        yield Footer()

    def on_mount(self) -> None:
        """Start background tasks."""
        self.set_interval(0.5, self.update_stats)
        # Wire live panels to the engine (they poll it on their own interval)
        self.query_one("#flows-tab MLFlowTable", MLFlowTable).engine = self.datapath
        self.query_one("#proto-chart", ProtocolChart).engine = self.datapath
        self.query_one("#qos-stats", QoSStats).engine = self.datapath
        self.query_one("#hw-stats", HWOffloadStats).engine = self.datapath

    async def update_stats(self) -> None:
        """Update reactive stats from datapath."""
        stats = self.datapath.stats
        self.packet_count = stats.get("packets_processed", 0)
        self.bridged_count = stats.get("packets_bridged", 0)
        self.routed_count = stats.get("packets_routed", 0)
        self.dropped_count = stats.get("packets_dropped", 0)
        self.flow_count = self.datapath.flow_table.size
        self.avg_latency_us = stats.get("avg_latency_us", 0.0)

        elapsed = max(1.0, time.time() - self._start_time)
        self.pps = self.packet_count / elapsed

        if self.datapath.offload_engine:
            hw_stats = self.datapath.offload_engine.get_stats()
            self.hw_offload_rate = hw_stats.get("hw_offload_rate", 0.0) * 100

    def action_start_generator(self) -> None:
        """Start traffic generator."""
        if self.generator and self.generator._running:
            return
        config = GeneratorConfig(rate_pps=100, duration=60)
        self.generator = TrafficGenerator(config)
        self._start_time = time.time()

        async def generate_and_process() -> None:
            async for pkt in self.generator.generate_burst(int(config.rate_pps * config.duration)):
                await self.datapath.process_packet(pkt, "eth0")
                self.packet_count += 1

        self.run_worker(generate_and_process())

    def action_stop_generator(self) -> None:
        if self.generator:
            self.generator.stop()

    def action_reset_stats(self) -> None:
        self.datapath.stats = {k: 0 for k in self.datapath.stats}
        self.packet_count = 0
        self.bridged_count = 0
        self.routed_count = 0
        self.dropped_count = 0


class StatCard(Static):
    """A card showing a single statistic."""

    value = reactive("0")
    label = "Stat"

    def __init__(self, label: str, value_key: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.value_key = value_key

    def render(self) -> Panel:
        # Reactive stats live on DashboardScreen — read them from the screen,
        # not self (StatCard instances never receive updates themselves).
        val = getattr(self.screen, self.value_key, None) if self.screen else None
        if val is None:
            display = "0"
        elif isinstance(val, float):
            display = f"{val:.1f}"
        else:
            display = str(val)
        return Panel(
            Text(display, style="bold cyan", justify="center"),
            title=self.label,
            border_style="blue",
        )


class MLFlowTable(DataTable):
    """Live ML flow classification table."""

    engine: NetPathEngine | None = None

    def on_mount(self) -> None:
        self.add_columns("Source", "Destination", "Proto", "Packets", "Class")
        self.set_interval(2.0, self.refresh_flows)

    async def refresh_flows(self) -> None:
        if self.engine is None:
            return
        self.clear()
        for f in self.engine.flow_table.list_flows()[:20]:
            proto = {6: "TCP", 17: "UDP", 1: "ICMP"}.get(f.protocol, str(f.protocol))
            self.add_row(
                f"{f.src_ip}:{f.src_port}",
                f"{f.dst_ip}:{f.dst_port}",
                proto,
                str(f.total_packets),
                f.trafficClass.name if hasattr(f, "trafficClass") else (
                    f.traffic_class.name if hasattr(f, "traffic_class") else "—"
                ),
            )


class _LivePanel(Static):
    """Base for panels that render live engine state.

    Subclasses implement `render_content()` and receive the DataPathEngine
    via `.engine` (wired by DashboardScreen.on_mount). Falls back to a
    placeholder when no engine is attached yet.
    """
    engine: DataPathEngine | None = None
    BAR_WIDTH: int = 24

    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh)

    # ── shared bar helper ──
    @staticmethod
    def _bar(fraction: float, width: int) -> str:
        fraction = max(0.0, min(1.0, fraction))
        filled = round(fraction * width)
        return "#" * filled + "." * (width - filled)

    def render(self) -> Text:
        if self.engine is None:
            return Text("waiting for engine...", style="dim")
        try:
            return self.render_content()
        except Exception as e:  # never kill the TUI over one panel
            return Text(f"panel error: {e}", style="red")

    def render_content(self) -> Text:
        raise NotImplementedError


class ProtocolChart(_LivePanel):
    """Protocol distribution computed from parsed packets seen by the engine."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._counts: dict[str, int] = {}
        self._total = 0

    def render_content(self) -> Text:
        text = Text()
        text.append("Protocol Distribution\n\n", style="bold")

        # Sample recent packets through the classifier to build counts.
        # Cheap approximation: classify the flows currently tracked and
        # weight by packet count per flow's dominant protocol.
        counts: dict[str, int] = {}
        for flow in self.engine.flow_table.list_flows():
            proto = {6: "TCP", 17: "UDP", 1: "ICMP"}.get(flow.protocol, "Other")
            counts[proto] = counts.get(proto, 0) + flow.total_packets

        total = sum(counts.values())
        if total == 0:
            text.append("no packets processed yet", style="dim")
            return text

        order = ["TCP", "UDP", "ICMP", "ARP", "Other"]
        colors = {"TCP": "blue", "UDP": "green", "ICMP": "yellow",
                  "ARP": "magenta", "Other": "white"}
        for proto in order + [p for p in counts if p not in order]:
            n = counts.get(proto, 0)
            if n == 0:
                continue
            pct = n / total
            text.append(f"{proto:<6}", style=colors.get(proto, "white"))
            text.append(f"{self._bar(pct, self.BAR_WIDTH)} ")
            text.append(f"{pct * 100:4.1f}%  ({n})\n")
        return text


class QoSStats(_LivePanel):
    """QoS queue depths from the priority scheduler."""

    QUEUE_LABELS = ["CRITICAL", "VOICE", "VIDEO", "BEST", "BULK", "SCAVENGER"]

    def render_content(self) -> Text:
        text = Text()
        text.append("QoS Queue Depths\n\n", style="bold")

        depths = self.engine.traffic_mgr.prio_scheduler.queue_depths
        max_depth = max(depths.values(), default=0) or 1

        for prio in range(6):
            depth = depths.get(prio, 0)
            label = self.QUEUE_LABELS[prio] if prio < len(self.QUEUE_LABELS) else str(prio)
            style = ("red" if prio < 2 else
                     "yellow" if prio < 4 else "green")
            text.append(f"{label:<10}", style="cyan")
            text.append(self._bar(depth / max_depth, self.BAR_WIDTH), style=style)
            text.append(f" {depth}\n")
        return text


class HWOffloadStats(_LivePanel):
    """HW offload statistics from the offload engine."""

    def render_content(self) -> Text:
        text = Text()
        text.append("HW Offload Statistics\n\n", style="bold")

        if not self.engine.offload_engine:
            text.append("offload engine disabled", style="dim")
            return text

        s = self.engine.offload_engine.get_stats()
        hw = s["hw_accelerated"]
        cpu_exc = s["cpu_exceptions"]
        cache_util = s["hw_cache_utilization"]
        rate = s["hw_offload_rate"]

        total = max(hw + cpu_exc, 1)
        text.append(f"HW Accelerated: {self._bar(hw / total, self.BAR_WIDTH)} {hw}\n")
        text.append(f"CPU Exceptions: {self._bar(cpu_exc / total, self.BAR_WIDTH)} {cpu_exc}\n")
        text.append(f"Cache Util:     {self._bar(cache_util, self.BAR_WIDTH)} {cache_util * 100:.1f}%\n")
        text.append(f"Offload Rate:   ", )
        rate_style = "green" if rate > 0.5 else ("yellow" if rate > 0.2 else "red")
        text.append(f"{self._bar(rate, self.BAR_WIDTH)}", style=rate_style)
        text.append(f" {rate * 100:.1f}%\n")
        return text


# ═══════════════════════════════════════════════════════════════════════════
# MAIN APP
# ═══════════════════════════════════════════════════════════════════════════

class NetworkLabApp(App):
    """Terminal UI for Network Lab."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #pkts, #bridged, #routed, #dropped, #flows, #hw, #lat, #pps {
        width: 1fr;
        height: 3;
        margin: 1;
    }
    """

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())


def run_cli() -> None:
    """Entry point for CLI dashboard."""
    app = NetworkLabApp()
    app.run()


if __name__ == "__main__":
    run_cli()
