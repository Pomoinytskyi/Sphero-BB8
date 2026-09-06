"""Shared mutable state, read by the TUI and written by the async tasks.

This replaces the pub/sub bus layer from the original design (simplification S3).
It is safe without locks precisely because everything runs on one event loop:
there is no preemption between ``await`` points, so a task mutating this object
between awaits cannot be interrupted mid-update. That is one of asyncio's real
payoffs, and the reason AD1 survived the simplification pass.

The TUI only ever *reads* this. Keeping it a pure consumer means the instrument
cannot perturb what it measures.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class LinkState(str, Enum):
    """Named connection states, so a failure says *which step* failed.

    Three very different problems -- droid asleep, handshake wrong, droid flat --
    look identical from outside if the app only tracks a boolean (constraint A12).
    """

    DISCONNECTED = "disconnected"
    SCANNING = "scanning"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    WAKING = "waking"
    READY = "ready"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"


class ControlMode(str, Enum):
    """Explicit mode, so driving and aiming can never emit at once (A4)."""

    IDLE = "idle"
    DRIVING = "driving"
    AIMING = "aiming"
    ESTOP = "estop"


@dataclass
class Traffic:
    """Live link metrics -- the TUI's most important panel.

    This is where the §6 N1 command-budget question gets answered empirically
    while driving, rather than argued about in advance.
    """

    sent: int = 0
    received: int = 0
    #: Drive commands superseded before transmission. Healthy and expected --
    #: it is the single-slot cell doing its job, not an error.
    coalesced: int = 0
    errors: int = 0
    last_latency_ms: float | None = None
    _send_times: deque[float] = field(default_factory=lambda: deque(maxlen=64))

    def note_send(self) -> None:
        self.sent += 1
        self._send_times.append(time.monotonic())

    @property
    def packets_per_sec(self) -> float:
        if len(self._send_times) < 2:
            return 0.0
        span = self._send_times[-1] - self._send_times[0]
        return (len(self._send_times) - 1) / span if span > 0 else 0.0


@dataclass
class Event:
    t: float
    kind: str
    detail: str


@dataclass
class State:
    link: LinkState = LinkState.DISCONNECTED
    link_detail: str = ""
    droid_name: str | None = None
    droid_address: str | None = None
    reconnects: int = 0

    control: ControlMode = ControlMode.IDLE
    drive_mode: str = "absolute"
    speed_profile: str = "tortoise"

    heading: int = 0
    speed: int = 0
    stick: tuple[float, float] = (0.0, 0.0)
    buttons: dict[str, bool] = field(default_factory=dict)

    battery_v: float | None = None
    telemetry: dict[str, float] = field(default_factory=dict)

    traffic: Traffic = field(default_factory=Traffic)
    events: deque[Event] = field(default_factory=lambda: deque(maxlen=200))
    wire: deque[tuple[float, str, bytes]] = field(default_factory=lambda: deque(maxlen=200))

    _t0: float = field(default_factory=time.monotonic)

    @property
    def uptime(self) -> float:
        return time.monotonic() - self._t0

    @property
    def ready(self) -> bool:
        return self.link is LinkState.READY

    def log(self, kind: str, detail: str = "") -> None:
        self.events.append(Event(self.uptime, kind, detail))
