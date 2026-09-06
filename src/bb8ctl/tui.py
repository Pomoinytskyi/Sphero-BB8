"""Terminal instrument panel.

Not a dashboard -- an oscilloscope. Its job is making link behaviour observable
while driving, which is how requirements C6 (measure latency and max rate) and
C7 (decode streams) are actually satisfied rather than reconstructed from logs
afterwards.

**Pure consumer.** It reads :class:`~bb8ctl.state.State` and never issues a
command, so the instrument cannot perturb what it measures. Every panel here is
a read; there is no write path out of this module.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static

from bb8ctl.state import LinkState, State

_LINK_COLOURS = {
    LinkState.READY: "bold green",
    LinkState.DEGRADED: "bold yellow",
    LinkState.RECONNECTING: "bold yellow",
    LinkState.DISCONNECTED: "bold red",
}


class Panel(Static):
    """A titled panel that re-renders from shared state on each tick."""

    def __init__(self, title: str, state: State, **kwargs) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.state = state
        self.border_title = title


class LinkPanel(Panel):
    def refresh_content(self) -> None:
        s = self.state
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", justify="right")
        table.add_column()
        table.add_row("state", Text(s.link.value, style=_LINK_COLOURS.get(s.link, "white")))
        if s.link_detail:
            table.add_row("", Text(s.link_detail, style="yellow"))
        table.add_row("droid", s.droid_name or "-")
        table.add_row("battery", f"{s.battery_v:.2f} V" if s.battery_v else "-")
        table.add_row("uptime", f"{s.uptime:.0f} s")
        table.add_row("reconnects", str(s.reconnects))
        self.update(table)


class TrafficPanel(Panel):
    """The most important panel.

    This is where the command-budget question from requirements §6 N1 is
    answered empirically, live, instead of argued about in advance.
    """

    def refresh_content(self) -> None:
        t = self.state.traffic
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", justify="right")
        table.add_column()
        rate = t.packets_per_sec
        # Above ~16/s we are pushing the firmware's documented floor.
        style = "green" if rate <= 16 else "bold yellow"
        table.add_row("rate", Text(f"{rate:5.1f} pkt/s", style=style))
        latency = t.last_latency_ms
        table.add_row("latency", f"{latency:.0f} ms" if latency else "-")
        table.add_row("sent", str(t.sent))
        table.add_row("recv", str(t.received))
        # Coalesced is healthy: the single-slot cell dropping stale input.
        table.add_row("coalesced", Text(str(t.coalesced), style="dim cyan"))
        table.add_row("errors", Text(str(t.errors), style="red" if t.errors else "dim"))
        self.update(table)


class ModePanel(Panel):
    def refresh_content(self) -> None:
        s = self.state
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", justify="right")
        table.add_column()
        table.add_row("control", Text(s.control.value, style="bold magenta"))
        table.add_row("drive", s.drive_mode)
        profile_style = "bold green" if s.speed_profile == "tortoise" else "bold red"
        table.add_row("profile", Text(s.speed_profile, style=profile_style))
        table.add_row("heading", f"{s.heading}deg")
        table.add_row("speed", str(s.speed))
        self.update(table)


class InputPanel(Panel):
    def refresh_content(self) -> None:
        x, y = self.state.stick
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", justify="right")
        table.add_column()
        table.add_row("stick", f"x={x:+.2f}  y={y:+.2f}")
        table.add_row("", _bar(x))
        table.add_row("", _bar(y))
        held = [n for n, v in self.state.buttons.items() if v]
        table.add_row("held", Text(" ".join(held) if held else "-", style="cyan"))
        self.update(table)


class TelemetryPanel(Panel):
    def refresh_content(self) -> None:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", justify="right")
        table.add_column(justify="right")
        if not self.state.telemetry:
            table.add_row("", Text("no stream", style="dim"))
        for name, value in list(self.state.telemetry.items())[:10]:
            table.add_row(name, f"{value:.1f}")
        self.update(table)


class EventsPanel(Panel):
    _STYLES = {"collision": "bold red", "estop": "bold red",
               "sleeping-soon": "yellow", "slept": "yellow",
               "decode-error": "red", "cmd-error": "red"}

    def refresh_content(self) -> None:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", justify="right", width=7)
        table.add_column(width=14)
        table.add_column()
        for event in list(self.state.events)[-8:]:
            table.add_row(f"{event.t:6.1f}",
                          Text(event.kind, style=self._STYLES.get(event.kind, "white")),
                          Text(event.detail[:40], style="dim"))
        self.update(table)


class WirePanel(Panel):
    """Rolling hex dump -- the raw bytes that become Swift test vectors."""

    def refresh_content(self) -> None:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", justify="right", width=7)
        table.add_column(width=3)
        table.add_column()
        for t, direction, data in list(self.state.wire)[-8:]:
            table.add_row(
                f"{t:6.1f}",
                Text(direction, style="cyan" if direction == "tx" else "green"),
                Text(data.hex(" ")[:60], style="dim"),
            )
        self.update(table)


def _bar(value: float, width: int = 21) -> Text:
    """A centre-origin bar, so stick drift is visible at a glance."""
    centre = width // 2
    position = max(0, min(width - 1, int(centre + value * centre)))
    cells = ["-"] * width
    cells[centre] = "|"
    cells[position] = "#"
    return Text("".join(cells), style="cyan")


class Dashboard(App):
    """The instrument. Read-only by construction."""

    CSS = """
    Screen { layout: vertical; }
    .row { height: 1fr; }
    Panel { border: round $accent; padding: 0 1; width: 1fr; }
    """
    BINDINGS = [("q", "quit", "Quit")]
    #: 10 Hz. Fast enough to read stick movement, slow enough to stay out of
    #: the control loop's way.
    REFRESH = 0.1

    def __init__(self, state: State) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(classes="row"):
                yield LinkPanel("Link", self.state)
                yield TrafficPanel("Traffic", self.state)
                yield ModePanel("Mode", self.state)
            with Horizontal(classes="row"):
                yield InputPanel("Input", self.state)
                yield TelemetryPanel("Telemetry", self.state)
            with Horizontal(classes="row"):
                yield EventsPanel("Events", self.state)
                yield WirePanel("Wire", self.state)
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(self.REFRESH, self.refresh_panels)
        self.refresh_panels()

    def refresh_panels(self) -> None:
        for panel in self.query(Panel):
            panel.refresh_content()
