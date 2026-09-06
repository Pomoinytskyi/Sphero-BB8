"""Control-loop tests via DriveApp.step() -- no loop, no droid, no controller.

step() takes a sample and a dt and mutates state, which makes the interesting
behaviour (modes, latching, toggles) testable as ordinary function calls.
"""

import pytest

from bb8ctl import control
from bb8ctl.app import DriveApp
from bb8ctl.control import CommandKind, DriveMode
from bb8ctl.gamepad import DEFAULT_BINDINGS, InputState
from bb8ctl.session import Session
from bb8ctl.state import ControlMode, State
from bb8ctl.transmit import Transmitter
from bb8ctl.transport import FakeTransport


def sample(lx=0.0, ly=0.0, **buttons) -> InputState:
    return InputState(lx=lx, ly=ly, buttons={n: buttons.get(n, False) for n in
                                             ("a", "b", "x", "y", "lb", "rb", "back", "start")})


@pytest.fixture
async def rig():
    state = State()
    session = Session(FakeTransport(), state)
    session.WAKE_SETTLE = 0.0
    await session.connect()
    tx = Transmitter(session, state, interval=0.01)
    app = DriveApp(session, state, gamepad=None, transmitter=tx)
    yield app, state, tx
    await session.shutdown()


class TestDriving:
    async def test_stick_forward_produces_a_drive_command(self, rig):
        app, state, tx = rig
        app.step(sample(ly=1.0), dt=1 / 60)
        assert tx._cell.kind is CommandKind.DRIVE
        assert tx._cell.speed > 0 and state.control is ControlMode.DRIVING

    async def test_centred_stick_is_idle(self, rig):
        app, state, tx = rig
        app.step(sample(), dt=1 / 60)
        assert tx._cell.kind is CommandKind.STOP and state.control is ControlMode.IDLE

    async def test_state_mirrors_the_stick(self, rig):
        app, state, _ = rig
        app.step(sample(lx=0.3, ly=0.6), dt=1 / 60)
        assert state.stick == (0.3, 0.6)


class TestAimMode:
    async def test_holding_aim_enters_aiming(self, rig):
        app, state, tx = rig
        app.step(sample(rb=True), dt=1 / 60)
        assert state.control is ControlMode.AIMING

    async def test_aiming_emits_calibrate_not_drive(self, rig):
        """ROLL mode 2 rotates the heading reference without driving (UC4)."""
        app, _, tx = rig
        app.step(sample(lx=1.0, rb=True), dt=1 / 60)
        assert tx._cell.kind is CommandKind.CALIBRATE
        assert tx._cell.speed == 0

    async def test_aiming_suppresses_driving_entirely(self, rig):
        """A4: aim and drive must never emit at once. Full forward stick while
        aiming must still not move the droid."""
        app, _, tx = rig
        app.step(sample(ly=1.0, rb=True), dt=1 / 60)
        assert tx._cell.kind is CommandKind.CALIBRATE and tx._cell.speed == 0

    async def test_aim_rotates_the_reference_over_time(self, rig):
        app, _, tx = rig
        for _ in range(10):
            app.step(sample(lx=1.0, rb=True), dt=0.05)
        assert tx._cell.heading > 0

    async def test_releasing_aim_returns_to_driving(self, rig):
        app, state, tx = rig
        app.step(sample(rb=True), dt=1 / 60)
        app.step(sample(ly=1.0), dt=1 / 60)
        assert state.control is ControlMode.DRIVING
        assert tx._cell.kind is CommandKind.DRIVE


class TestEstop:
    async def test_estop_latches_until_the_stick_recentres(self, rig):
        """Releasing the panic button must not immediately resume driving."""
        app, state, tx = rig
        app._handle_edges({"b"}, sample())
        assert state.control is ControlMode.ESTOP

        app.step(sample(ly=1.0), dt=1 / 60)          # still deflected
        assert state.control is ControlMode.ESTOP
        assert tx._cell.kind is CommandKind.STOP

        app.step(sample(), dt=1 / 60)                # recentred -> released
        assert state.control is ControlMode.IDLE
        app.step(sample(ly=1.0), dt=1 / 60)
        assert state.control is ControlMode.DRIVING


class TestToggles:
    async def test_drive_mode_toggles(self, rig):
        app, state, _ = rig
        assert app.mode is DriveMode.ABSOLUTE
        app._handle_edges({"x"}, sample())
        assert app.mode is DriveMode.TANK and state.drive_mode == "tank"
        app._handle_edges({"x"}, sample())
        assert app.mode is DriveMode.ABSOLUTE

    async def test_speed_profile_toggles(self, rig):
        app, state, _ = rig
        assert app.profile is control.TORTOISE
        app._handle_edges({"y"}, sample())
        assert app.profile is control.RABBIT and state.speed_profile == "rabbit"

    async def test_profile_change_takes_effect_on_the_next_tick(self, rig):
        """C13: switchable *while driving*, without stopping."""
        app, _, tx = rig
        app.step(sample(ly=1.0), dt=1 / 60)
        slow = tx._cell.speed
        app._handle_edges({"y"}, sample())
        app.step(sample(ly=1.0), dt=1 / 60)
        assert tx._cell.speed > slow

    async def test_reset_aim_queues_set_heading_zero(self, rig):
        app, _, tx = rig
        app._handle_edges({"a"}, sample())
        assert not tx._queue.empty()

    async def test_quit_is_requested(self, rig):
        app, _, _ = rig
        app._handle_edges({"back"}, sample())
        assert app.quit_requested

    async def test_bindings_are_indirect_not_positional(self, rig):
        """Rebinding must work without touching loop logic -- the defect the
        original notebook's get_button(2) had."""
        app, state, _ = rig
        app.bindings = dict(DEFAULT_BINDINGS, toggle_profile="lb")
        app._handle_edges({"lb"}, sample())
        assert app.profile is control.RABBIT
