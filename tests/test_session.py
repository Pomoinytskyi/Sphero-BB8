"""Session lifecycle tests, all against FakeTransport -- no droid needed.

The handshake ordering test is the important one. Getting that order wrong
produces a droid that connects and then ignores everything, which looks exactly
like a bug in the command layer and costs hours to diagnose.
"""

import asyncio

import pytest

from bb8ctl import protocol
from bb8ctl.session import HandshakeError, Session
from bb8ctl.state import LinkState
from bb8ctl.transport import FakeTransport


async def connected_session(**kwargs) -> Session:
    s = Session(FakeTransport(**kwargs))
    s.WAKE_SETTLE = 0.0        # no firmware to wait for in tests
    await s.connect()
    return s


class TestHandshake:
    async def test_reaches_ready(self):
        s = await connected_session()
        assert s.state.link is LinkState.READY
        await s.shutdown()

    async def test_anti_dos_is_written_before_anything_else(self):
        """Subscribing before anti-DOS leaves the droid connected but mute."""
        s = await connected_session()
        assert s.transport.sent[0] == b"011i3"
        await s.shutdown()

    async def test_handshake_order_is_anti_dos_then_tx_power_then_wake(self):
        s = await connected_session()
        assert s.transport.sent[:3] == [b"011i3", bytes([0x07]), bytes([0x01])]
        await s.shutdown()

    async def test_ping_gates_readiness(self):
        """'Connected' is not 'ready' (A1). Without a PING reply the link is
        DEGRADED, and the operator is told which step failed, not just 'failed'."""
        s = Session(FakeTransport(auto_respond=False))
        s.PING_TIMEOUT = 0.05
        s.WAKE_SETTLE = 0.0
        with pytest.raises(HandshakeError):
            await s.connect()
        assert s.state.link is LinkState.DEGRADED
        assert "PING" in s.state.link_detail
        await s.shutdown()

    async def test_ping_is_acknowledged(self):
        s = await connected_session()
        pings = [p for p in s.transport.sent if len(p) > 3 and p[3] == protocol.CoreCmd.PING]
        assert pings and pings[0][1] == protocol.Sop2.ANSWER
        await s.shutdown()


class TestSequencing:
    async def test_sequence_numbers_advance(self):
        s = await connected_session()
        await s.request(protocol.ping)
        await s.request(protocol.ping)
        seqs = [p[4] for p in s.transport.sent if len(p) > 4 and p[3] == protocol.CoreCmd.PING]
        assert len(set(seqs)) == len(seqs)
        await s.shutdown()

    async def test_sequence_wraps_at_256(self):
        s = await connected_session()
        s._seq = 255
        assert s._next_seq() == 0
        await s.shutdown()

    async def test_request_repairs_checksum_after_forcing_ack_bit(self):
        """request() rewrites SOP2, so the checksum must be recomputed or the
        droid rejects the packet with a checksum failure."""
        s = await connected_session()
        await s.request(lambda seq: protocol.set_heading(0, seq=seq))
        pkt = [p for p in s.transport.sent if len(p) > 3 and p[3] == protocol.SpheroCmd.SET_HEADING][0]
        assert pkt[1] == protocol.Sop2.ANSWER
        assert pkt[-1] == protocol.checksum(pkt[2:-1])
        await s.shutdown()

    async def test_request_times_out_rather_than_hanging(self):
        s = Session(FakeTransport(auto_respond=False))
        await s.transport.connect()
        await s.transport.start_notifications()
        s._pump_task = asyncio.create_task(s._pump())
        with pytest.raises(asyncio.TimeoutError):
            await s.request(protocol.ping, timeout=0.05)
        await s.shutdown()


class TestConfigure:
    async def test_arms_the_motion_timeout(self):
        """The dead-man switch (N4) must be set before anything can move."""
        s = await connected_session()
        before = len(s.transport.sent)
        await s.configure()
        new = s.transport.sent[before:]
        assert any(len(p) > 3 and p[3] == protocol.SpheroCmd.SET_MOTION_TIMEOUT for p in new)
        await s.shutdown()

    async def test_is_idempotent_so_reconnect_can_replay_it(self):
        """A10: reconnection replays configure() verbatim rather than
        re-deriving setup, so a reconnected session matches a fresh one."""
        s = await connected_session()
        s.transport.sent.clear()          # drop handshake + ping
        await s.configure()
        first = list(s.transport.sent)
        s.transport.sent.clear()
        await s.configure()
        assert [p[3] for p in s.transport.sent if len(p) > 3] == [
            p[3] for p in first if len(p) > 3
        ]
        await s.shutdown()

    async def test_streaming_masks_are_stored_for_decoding(self):
        s = await connected_session()
        await s.configure(stream=("yaw", "speed"))
        assert s._masks != (0, 0)
        await s.shutdown()


class TestAsyncHandling:
    async def test_collision_is_logged(self):
        s = await connected_session()
        s.transport.inject_async(id_code=protocol.AsyncId.COLLISION_DETECTED, data=b"\xaa")
        await asyncio.sleep(0.05)
        assert any(e.kind == "collision" for e in s.state.events)
        await s.shutdown()

    async def test_sensor_data_lands_in_telemetry(self):
        s = await connected_session()
        await s.configure(stream=("yaw",))
        s.transport.inject_async(
            id_code=protocol.AsyncId.SENSOR_DATA, data=(42).to_bytes(2, "big", signed=True)
        )
        await asyncio.sleep(0.05)
        assert s.state.telemetry.get("yaw") == 42
        await s.shutdown()

    async def test_droid_sleeping_degrades_the_link(self):
        """Distinguishes 'the droid slept' from 'the app is broken' (A12)."""
        s = await connected_session()
        s.transport.inject_async(id_code=protocol.AsyncId.DID_SLEEP)
        await asyncio.sleep(0.05)
        assert s.state.link is LinkState.DEGRADED
        await s.shutdown()

    async def test_malformed_sensor_frame_does_not_kill_the_pump(self):
        """A dead pump makes a live droid look deaf. Errors are counted, not fatal."""
        s = await connected_session()
        await s.configure(stream=("yaw", "speed"))
        s.transport.inject_async(id_code=protocol.AsyncId.SENSOR_DATA, data=b"\x00")  # short
        await asyncio.sleep(0.05)
        assert s.state.traffic.errors == 1

        s.transport.inject_async(
            id_code=protocol.AsyncId.SENSOR_DATA,
            data=(1).to_bytes(2, "big") + (2).to_bytes(2, "big"),
        )
        await asyncio.sleep(0.05)
        assert s.state.telemetry.get("yaw") == 1      # pump still alive
        await s.shutdown()


class TestShutdown:
    async def test_stops_the_droid_before_disconnecting(self):
        s = await connected_session()
        await s.shutdown()
        last = s.transport.sent[-1]
        assert last[3] == protocol.SpheroCmd.ROLL and last[6] == 0   # speed zero
        assert last[9] == protocol.RollMode.STOP

    async def test_leaves_the_link_disconnected(self):
        s = await connected_session()
        await s.shutdown()
        assert s.state.link is LinkState.DISCONNECTED

    async def test_survives_a_transport_that_is_already_gone(self):
        """Shutdown runs on the error path too, so it must never raise."""
        s = await connected_session()
        await s.transport.close()
        await s.shutdown()
