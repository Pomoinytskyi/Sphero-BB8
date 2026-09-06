"""The control loop: gamepad to drive cell at 60 Hz.

Reads input four times faster than the radio can carry it, deliberately. The
extra samples are not waste -- they keep the drive cell holding a *current*
desired state, so whenever the transmitter's next tick arrives it sends where
the stick is now rather than where it was.
"""

from __future__ import annotations

import asyncio
import time

from bb8ctl import control, protocol
from bb8ctl.control import CommandKind, DriveCommand, DriveMode, SpeedProfile
from bb8ctl.gamepad import DEFAULT_BINDINGS, InputState
from bb8ctl.state import ControlMode, State


class DriveApp:
    """Wires gamepad, control mapping and transmitter together."""

    CONTROL_HZ = 60

    def __init__(self, session, state: State, gamepad, transmitter, bindings=None) -> None:
        self.session = session
        self.state = state
        self.gamepad = gamepad
        self.tx = transmitter
        self.bindings = bindings or DEFAULT_BINDINGS
        self.mode = DriveMode.ABSOLUTE
        self.profile: SpeedProfile = control.TORTOISE
        self.quit_requested = False
        self._aim_heading = 0

    # -- discrete actions --------------------------------------------------

    def _handle_edges(self, edges: set[str], state: InputState) -> None:
        """Act on button *presses*, not held state.

        Level-triggering a toggle would flip it 60 times a second while held.
        """
        def bound(action: str) -> bool:
            return self.bindings.get(action) in edges

        if bound("quit"):
            self.quit_requested = True
        if bound("estop"):
            self.tx.estop()
            self.state.control = ControlMode.ESTOP
            self.state.log("estop", "operator")
        if bound("toggle_drive_mode"):
            self.mode = DriveMode.TANK if self.mode is DriveMode.ABSOLUTE else DriveMode.ABSOLUTE
            self.state.drive_mode = self.mode.value
            self.state.log("drive-mode", self.mode.value)
        if bound("toggle_profile"):
            self.profile = control.RABBIT if self.profile is control.TORTOISE else control.TORTOISE
            self.state.speed_profile = self.profile.name
            self.state.log("profile", self.profile.name)
        if bound("reset_aim"):
            # Whatever way the droid faces now becomes forward.
            self.tx.enqueue_nowait(protocol.set_heading(0, seq=self.session._next_seq()))
            self.state.log("aim", "reset")

    # -- the loop ----------------------------------------------------------

    def step(self, sample: InputState, dt: float) -> None:
        """One control tick. Pure enough to test without a loop or a droid."""
        self.state.stick = (sample.lx, sample.ly)
        self.state.buttons = sample.buttons

        if sample.pressed("aim", self.bindings):
            # Aim is modal: stick rotates the heading reference via ROLL mode 2
            # and normal drive output is suppressed, so the two can never emit
            # at once (constraint A4).
            if self.state.control is not ControlMode.AIMING:
                self._aim_heading = self.state.heading
                self.state.log("aim", "entered")
            self.state.control = ControlMode.AIMING
            self._aim_heading = control.aim_delta(sample.lx, self.profile, self._aim_heading, dt)
            self.tx.set_drive(
                DriveCommand(speed=0, heading=self._aim_heading, kind=CommandKind.CALIBRATE)
            )
            return

        if self.state.control is ControlMode.AIMING:
            self.state.log("aim", "released")
            self.state.control = ControlMode.IDLE

        command = control.map_input(
            sample.lx, sample.ly, self.mode, self.profile, self.state.heading, dt
        )
        # An e-stop stays latched until the stick returns to centre, so letting
        # go of the panic button does not immediately resume driving.
        if self.state.control is ControlMode.ESTOP:
            if command.kind is CommandKind.STOP:
                self.state.control = ControlMode.IDLE
            else:
                return

        self.state.control = (
            ControlMode.IDLE if command.kind is CommandKind.STOP else ControlMode.DRIVING
        )
        self.tx.set_drive(command)

    async def control_loop(self) -> None:
        period = 1.0 / self.CONTROL_HZ
        previous = time.monotonic()
        while not self.quit_requested:
            await asyncio.sleep(period)
            now = time.monotonic()
            dt, previous = now - previous, now
            sample = self.gamepad.read()
            self._handle_edges(self.gamepad.edges(sample), sample)
            self.step(sample, dt)

    #: Battery refresh period. Slow on purpose -- it costs an acknowledged
    #: round trip, and BB-8 does not flatten in thirty seconds.
    BATTERY_PERIOD = 30.0

    async def battery_loop(self) -> None:
        """Keep the battery reading fresh, and double as a liveness probe."""
        while not self.quit_requested:
            await asyncio.sleep(self.BATTERY_PERIOD)
            if self.state.ready:
                await self.session.read_power()

    async def run(self) -> None:
        """Run until quit, then shut down through the single exit path (A11)."""
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.tx.run())
                tg.create_task(self.battery_loop())
                tg.create_task(self._until_quit())
        finally:
            self.tx.stop()
            await self.session.shutdown()

    async def _until_quit(self) -> None:
        await self.control_loop()
        self.tx.stop()
