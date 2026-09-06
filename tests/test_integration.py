"""End-to-end tests with no droid attached.

This is milestone 2 from docs/06 §4: a recorded session replays through the real
decoder into the real TUI. It exercises transport, session, decoder, sensor
decoding and every panel -- the whole stack minus the radio.

That this is possible at all is the payoff from putting the Transport seam in
(constraint A9), and it is what makes progress possible between the intermittent
windows when the droid is actually available.
"""

import asyncio

import pytest

from bb8ctl import protocol, sensors
from bb8ctl.session import Session
from bb8ctl.state import LinkState, State
from bb8ctl.transport import CaptureWriter, Direction, ReplayTransport
from bb8ctl.tui import Dashboard, Panel


def synthetic_capture(path, *, frames: int = 40):
    """A plausible session: ping reply, telemetry, a collision, a sleep warning."""
    primary, extended = sensors.build_masks(sensors.DRIVE_PRESET)
    field_count = len(sensors.layout(primary, extended))

    def response(code=0, seq=0, data=b""):
        body = bytes([code, seq, len(data) + 1]) + data
        return bytes([0xFF, 0xFF]) + body + bytes([protocol.checksum(body)])

    def async_msg(id_code, data=b""):
        dlen = len(data) + 1
        body = bytes([id_code, dlen >> 8, dlen & 0xFF]) + data
        return bytes([0xFF, 0xFE]) + body + bytes([protocol.checksum(body)])

    with CaptureWriter(path) as cap:
        cap.record(Direction.TX, protocol.ping(seq=1))
        cap.record(Direction.RX, response(seq=1))
        for i in range(frames):
            sample = b"".join(
                ((i * 7 + f * 3) % 300).to_bytes(2, "big", signed=True)
                for f in range(field_count)
            )
            cap.record(Direction.TX, protocol.roll(60, i * 9 % 360, seq=i % 256))
            cap.record(Direction.RX, async_msg(protocol.AsyncId.SENSOR_DATA, sample))
            if i == frames // 2:
                cap.record(Direction.RX, async_msg(protocol.AsyncId.COLLISION_DETECTED, b"\x01\x02"))
            if i == frames - 2:
                cap.record(Direction.RX, async_msg(protocol.AsyncId.SLEEPING_SOON))
    return path


def _screen_text(app) -> str:
    """Flatten the rendered screen to plain text for assertions."""
    return "\n".join(
        "".join(segment.text for segment in strip)
        for strip in app.screen._compositor.render_strips()
    )


@pytest.fixture
def capture(tmp_path):
    return synthetic_capture(tmp_path / "synthetic.jsonl")


async def replayed_state(capture) -> State:
    state = State()
    session = Session(ReplayTransport(capture, realtime=False), state)
    session._masks = sensors.build_masks(sensors.DRIVE_PRESET)
    await session.transport.connect()
    await session.transport.start_notifications()
    await session._pump()          # runs to completion; replay terminates
    return state


class TestReplayPipeline:
    async def test_frames_are_received(self, capture):
        assert (await replayed_state(capture)).traffic.received > 40

    async def test_telemetry_is_decoded(self, capture):
        state = await replayed_state(capture)
        assert set(state.telemetry) == set(sensors.DRIVE_PRESET)

    async def test_collision_is_surfaced(self, capture):
        state = await replayed_state(capture)
        assert any(e.kind == "collision" for e in state.events)

    async def test_sleep_warning_is_surfaced(self, capture):
        """Distinguishes 'droid slept' from 'app broke' (A12)."""
        state = await replayed_state(capture)
        assert any(e.kind == "sleeping-soon" for e in state.events)

    async def test_replay_decodes_without_errors(self, capture):
        assert (await replayed_state(capture)).traffic.errors == 0

    async def test_wire_log_is_populated_for_vector_export(self, capture):
        assert len((await replayed_state(capture)).wire) > 0


class TestDashboard:
    async def test_all_panels_render(self, capture):
        state = await replayed_state(capture)
        state.link = LinkState.READY
        state.droid_name = "BB-1234"
        async with Dashboard(state).run_test() as pilot:
            panels = pilot.app.query(Panel)
            assert len(panels) == 7
            for panel in panels:
                assert panel.visual is not None

    async def test_panels_show_live_values_not_placeholders(self, capture):
        """Rendering without crashing is not enough -- the panels must actually
        display state, or the instrument silently reads blank."""
        state = await replayed_state(capture)
        state.link = LinkState.READY
        state.droid_name = "BB-1234"
        async with Dashboard(state).run_test() as pilot:
            screen = _screen_text(pilot.app)
        assert "ready" in screen
        assert "BB-1234" in screen
        assert "collision" in screen          # from the Events panel
        assert "locator_x" in screen          # decoded telemetry

    async def test_dashboard_survives_empty_state(self):
        """It must render before the first packet arrives, not crash."""
        async with Dashboard(State()).run_test() as pilot:
            assert len(pilot.app.query(Panel)) == 7

    async def test_dashboard_never_writes_to_state(self, capture):
        """The instrument must not perturb what it measures -- it is a pure
        consumer, so a full render cycle must leave state untouched."""
        state = await replayed_state(capture)
        state.link = LinkState.READY
        before = (state.traffic.sent, state.traffic.received, state.heading,
                  state.speed, len(state.events), state.control)
        async with Dashboard(state).run_test() as pilot:
            pilot.app.refresh_panels()
            await pilot.pause()
        after = (state.traffic.sent, state.traffic.received, state.heading,
                 state.speed, len(state.events), state.control)
        assert before == after


class TestVectorExport:
    def test_exported_vectors_match_the_codec(self, tmp_path):
        """C5: the Swift port is validated against this file, so a drift between
        the exporter and the codec would silently certify a wrong port."""
        import argparse
        import json

        from bb8ctl.cli import cmd_vectors

        out = tmp_path / "v.json"
        cmd_vectors(argparse.Namespace(output=str(out)))
        payload = json.loads(out.read_text())

        assert payload["packets"]["roll_100_90"] == protocol.roll(100, 90, seq=5).hex()
        assert payload["packets"]["ping"] == protocol.ping(seq=9).hex()
        assert payload["characteristics"]["command"] == protocol.CHAR_COMMAND
        assert [d["hex"] for d in payload["handshake"]] == [d.hex() for _, d in protocol.HANDSHAKE]

    def test_field_order_matches_the_decoder(self, tmp_path):
        """The wire carries no field tags, so a mismatch here decodes every
        sensor as its neighbour -- plausible numbers, all wrong."""
        import argparse
        import json

        from bb8ctl.cli import cmd_vectors

        out = tmp_path / "v.json"
        cmd_vectors(argparse.Namespace(output=str(out)))
        payload = json.loads(out.read_text())
        primary, extended = sensors.build_masks(sensors.DRIVE_PRESET)
        assert payload["sensor_masks"]["field_order"] == [
            f.name for f in sensors.layout(primary, extended)
        ]


class TestDriveDashboardTeardown:
    """Either side ending must tear down the other -- one exit path (A11).

    Uses doubles rather than a live Textual app: the behaviour under test is the
    teardown wiring, and driving a real app's lifecycle from inside its own test
    harness tests Textual instead.
    """

    class FakeDashboard:
        def __init__(self) -> None:
            self.exited = asyncio.Event()

        async def run_async(self) -> None:
            await self.exited.wait()

        def exit(self) -> None:
            self.exited.set()

    class FakeDriveApp:
        def __init__(self, run_forever: bool = False) -> None:
            self.quit_requested = False
            self.run_forever = run_forever

        async def run(self) -> None:
            while self.run_forever and not self.quit_requested:
                await asyncio.sleep(0.005)

    async def test_controller_quit_closes_the_dashboard(self):
        """Back on the pad must not leave a dead panel on screen."""
        from bb8ctl.cli import _drive_with_dashboard

        dashboard = self.FakeDashboard()
        await _drive_with_dashboard(self.FakeDriveApp(), dashboard)
        assert dashboard.exited.is_set()

    async def test_dashboard_quit_stops_driving(self):
        """Quitting the TUI must stop the droid, not orphan the control loop."""
        from bb8ctl.cli import _drive_with_dashboard

        dashboard = self.FakeDashboard()
        drive_app = self.FakeDriveApp(run_forever=True)
        task = asyncio.create_task(_drive_with_dashboard(drive_app, dashboard))
        await asyncio.sleep(0.02)
        dashboard.exit()
        await asyncio.wait_for(task, timeout=1.0)
        assert drive_app.quit_requested is True
