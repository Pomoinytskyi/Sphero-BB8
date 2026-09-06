"""The transmitter: one owner of the radio, enforcing the command budget.

The single most important structure in the project. Input arrives at 60 Hz;
BB-8's firmware accepts roughly 16 packets/sec. Bridging that 4x gap by
*queueing* is what makes the droid execute stick input from seconds ago -- the
rubber-banding that motivated this rewrite.

So the drive path is a **single slot, not a queue**. New input overwrites
un-sent input. Latency cannot accumulate, because there is nowhere for it to
accumulate (constraints A2, N3).
"""

from __future__ import annotations

import asyncio

from bb8ctl import protocol
from bb8ctl.control import CommandKind, DriveCommand
from bb8ctl.state import State

#: Seconds between packets. spherov2 uses 0.06 as a firmware-safe floor; we
#: start slightly above it and let Phase 3 measurement tune it down
#: (docs/06 §5 step 4). Deliberately a constant, not an adaptive governor --
#: measure before building machinery to react to the measurement.
DEFAULT_INTERVAL = 0.07


class Transmitter:
    """Drains a single-slot drive cell plus a queue of one-off commands."""

    def __init__(self, session, state: State, interval: float = DEFAULT_INTERVAL) -> None:
        self.session = session
        self.state = state
        self.interval = interval
        self._cell: DriveCommand | None = None
        self._urgent = False
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._last_sent: DriveCommand | None = None
        self._running = False

    def set_drive(self, command: DriveCommand, *, urgent: bool = False) -> None:
        """Overwrite the desired drive state.

        Never blocks and never queues. Superseding an un-sent command is the
        design working, so it is counted as ``coalesced`` rather than treated as
        a loss -- the TUI shows it as a health signal, not an error.
        """
        if self._cell is not None:
            self.state.traffic.coalesced += 1
        self._cell = command
        if urgent:
            self._urgent = True

    def estop(self) -> None:
        """Panic stop. Pre-empts whatever is pending (UC11)."""
        self.set_drive(
            DriveCommand(speed=0, heading=self.state.heading, kind=CommandKind.STOP),
            urgent=True,
        )

    def enqueue_nowait(self, packet: bytes) -> bool:
        """Synchronous variant for the control loop, which must never await."""
        try:
            self._queue.put_nowait(packet)
            return True
        except asyncio.QueueFull:
            return False

    async def enqueue(self, packet: bytes) -> bool:
        """Queue a one-off command (setup, LED, probe steps).

        Bounded and non-blocking: under pressure these are dropped rather than
        allowed to delay driving (L5). Background traffic must never be able to
        starve the control loop.
        """
        try:
            self._queue.put_nowait(packet)
            return True
        except asyncio.QueueFull:
            return False

    def _redundant_idle(self, command: DriveCommand) -> bool:
        return (
            command.kind is CommandKind.STOP
            and self._last_sent is not None
            and self._last_sent.kind is CommandKind.STOP
        )

    def _should_send(self, command: DriveCommand) -> bool:
        """Suppress redundant repeats of an idle droid.

        A parked droid needs no ROLL stream. Skipping unchanged stop commands
        frees most of the budget for background traffic exactly when driving is
        not using it -- which is what makes the lighting work in step 2 viable.
        """
        if self._urgent:
            return True
        if self._last_sent is None:
            return True
        if self._redundant_idle(command):
            return False
        return command != self._last_sent

    async def run(self) -> None:
        """Fixed-cadence drain: urgent, then drive, then one background item."""
        self._running = True
        try:
            while self._running:
                await asyncio.sleep(self.interval)
                sent_drive = False

                command = self._cell
                if command is not None and self._should_send(command):
                    packet = _encode(command, self.session._next_seq())
                    await self.session.send(packet)
                    self._last_sent = command
                    self.state.speed, self.state.heading = command.speed, command.heading
                    sent_drive = True
                self._cell = None
                self._urgent = False

                # One background packet per tick, and only when driving did not
                # need the slot. Structurally incapable of delaying the droid.
                if not sent_drive and not self._queue.empty():
                    await self.session.send(self._queue.get_nowait())
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False


#: Maps the control layer's intent onto the wire encoding. The one place the two
#: vocabularies meet, so control.py needs no protocol import.
_ENCODERS = {
    CommandKind.DRIVE: lambda c, seq: protocol.roll(c.speed, c.heading, seq=seq),
    CommandKind.STOP: lambda c, seq: protocol.stop(c.heading, seq=seq),
    CommandKind.CALIBRATE: lambda c, seq: protocol.calibrate(c.heading, seq=seq),
}


def _encode(command: DriveCommand, seq: int) -> bytes:
    return _ENCODERS[command.kind](command, seq)
