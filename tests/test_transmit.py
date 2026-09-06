"""Transmitter tests -- the heart of the latency design.

The staleness test is the one that matters: it asserts that driving the cell far
faster than the link can carry produces *recent* commands, not a backlog. That
property is the entire reason this component exists.
"""

import asyncio

import pytest

from bb8ctl import protocol
from bb8ctl.control import CommandKind, DriveCommand
from bb8ctl.session import Session
from bb8ctl.state import State
from bb8ctl.transmit import Transmitter
from bb8ctl.transport import FakeTransport


@pytest.fixture
async def rig():
    state = State()
    session = Session(FakeTransport(), state)
    session.WAKE_SETTLE = 0.0
    await session.connect()
    session.transport.sent.clear()
    tx = Transmitter(session, state, interval=0.01)
    yield session, state, tx
    tx.stop()
    await session.shutdown()


def rolls(session):
    return [p for p in session.transport.sent
            if len(p) > 9 and p[3] == protocol.SpheroCmd.ROLL]


class TestSingleSlotCell:
    async def test_overwrites_rather_than_queues(self, rig):
        session, state, tx = rig
        for speed in range(1, 51):
            tx.set_drive(DriveCommand(speed=speed, heading=0))
        task = asyncio.create_task(tx.run())
        await asyncio.sleep(0.05)
        tx.stop()
        await task
        # 50 commands set, at most a couple of ticks elapsed -- the rest were
        # superseded in place, never buffered.
        assert len(rolls(session)) <= 6

    async def test_the_command_sent_is_the_most_recent_one(self, rig):
        """Staleness is dropped, not delayed. This is the anti-rubber-band
        property: the droid acts on where the stick is *now*."""
        session, state, tx = rig
        task = asyncio.create_task(tx.run())
        for speed in range(1, 30):
            tx.set_drive(DriveCommand(speed=speed, heading=0))
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.03)
        tx.stop()
        await task
        assert rolls(session)[-1][6] == 29     # the newest, not the oldest

    async def test_superseded_commands_are_counted_as_coalesced(self, rig):
        _, state, tx = rig
        tx.set_drive(DriveCommand(speed=1, heading=0))
        tx.set_drive(DriveCommand(speed=2, heading=0))
        tx.set_drive(DriveCommand(speed=3, heading=0))
        assert state.traffic.coalesced == 2    # health signal, not an error


class TestRateLimiting:
    async def test_send_rate_tracks_the_interval(self, rig):
        session, _, tx = rig
        tx.interval = 0.02
        task = asyncio.create_task(tx.run())
        for i in range(200):
            tx.set_drive(DriveCommand(speed=(i % 90) + 1, heading=i % 360))
            await asyncio.sleep(0.001)
        tx.stop()
        await task
        # ~0.2 s at 50/s is ~10 packets. Generous bounds; the point is that
        # 200 inputs do not become 200 packets.
        assert 4 <= len(rolls(session)) <= 20

    async def test_idle_droid_is_not_spammed_with_stop_packets(self, rig):
        """A parked droid needs no ROLL stream; the freed budget is what makes
        background traffic viable in step 2."""
        session, _, tx = rig
        task = asyncio.create_task(tx.run())
        for _ in range(20):
            tx.set_drive(DriveCommand(speed=0, heading=0, kind=CommandKind.STOP))
            await asyncio.sleep(0.002)
        tx.stop()
        await task
        assert len(rolls(session)) == 1        # one stop, then silence


class TestEstop:
    async def test_estop_sends_a_stop(self, rig):
        session, _, tx = rig
        task = asyncio.create_task(tx.run())
        tx.set_drive(DriveCommand(speed=200, heading=90))
        await asyncio.sleep(0.03)
        tx.estop()
        await asyncio.sleep(0.03)
        tx.stop()
        await task
        last = rolls(session)[-1]
        assert last[6] == 0 and last[9] == protocol.RollMode.STOP

    async def test_estop_overrides_redundancy_suppression(self, rig):
        """A stop must go out even if the last command was also a stop --
        suppression must never swallow a panic stop."""
        session, _, tx = rig
        task = asyncio.create_task(tx.run())
        tx.set_drive(DriveCommand(speed=0, heading=0, kind=CommandKind.STOP))
        await asyncio.sleep(0.03)
        before = len(rolls(session))
        tx.estop()
        await asyncio.sleep(0.03)
        tx.stop()
        await task
        assert len(rolls(session)) > before


class TestBackgroundQueue:
    async def test_background_packets_are_sent_when_idle(self, rig):
        session, _, tx = rig
        await tx.enqueue(protocol.set_main_led(255, 0, 0, seq=1))
        task = asyncio.create_task(tx.run())
        await asyncio.sleep(0.05)
        tx.stop()
        await task
        assert any(len(p) > 3 and p[3] == protocol.SpheroCmd.SET_MAIN_LED
                   for p in session.transport.sent)

    async def test_background_yields_to_driving(self, rig):
        """L5, enforced structurally: lighting cannot delay the droid because it
        only ever uses ticks that driving did not want."""
        session, _, tx = rig
        for i in range(60):
            await tx.enqueue(protocol.set_main_led(i, 0, 0, seq=i))
        task = asyncio.create_task(tx.run())
        for i in range(40):
            tx.set_drive(DriveCommand(speed=(i % 80) + 1, heading=i))
            await asyncio.sleep(0.002)
        tx.stop()
        await task
        leds = [p for p in session.transport.sent
                if len(p) > 3 and p[3] == protocol.SpheroCmd.SET_MAIN_LED]
        assert len(rolls(session)) > len(leds)

    async def test_queue_is_bounded_and_drops_rather_than_blocking(self, rig):
        """Backpressure here would stall the control loop."""
        _, _, tx = rig
        results = [await tx.enqueue(protocol.ping(seq=i % 256)) for i in range(200)]
        assert results[0] is True and results[-1] is False


class TestStateTracking:
    async def test_state_reflects_the_last_sent_command(self, rig):
        session, state, tx = rig
        task = asyncio.create_task(tx.run())
        tx.set_drive(DriveCommand(speed=77, heading=123))
        await asyncio.sleep(0.03)
        tx.stop()
        await task
        assert state.speed == 77 and state.heading == 123

    async def test_traffic_counter_advances(self, rig):
        session, state, tx = rig
        task = asyncio.create_task(tx.run())
        tx.set_drive(DriveCommand(speed=50, heading=0))
        await asyncio.sleep(0.03)
        tx.stop()
        await task
        assert state.traffic.sent > 0
        assert state.traffic.packets_per_sec >= 0


class TestPacing:
    async def test_period_absorbs_send_time_rather_than_adding_to_it(self, rig):
        """Sleeping `interval` then working makes the real period
        `interval + work`. Measured on hardware that was 13.9 Hz against a
        30 Hz target, because a BLE write costs about as long as the sleep.
        """
        session, _, tx = rig
        tx.interval = 0.02

        original = session.transport.send
        async def slow_send(data, **kwargs):
            await asyncio.sleep(0.01)      # simulate a costly write
            await original(data, **kwargs)
        session.transport.send = slow_send

        task = asyncio.create_task(tx.run())
        start = asyncio.get_running_loop().time()
        for i in range(120):
            tx.set_drive(DriveCommand(speed=(i % 80) + 1, heading=i % 360))
            await asyncio.sleep(0.002)
        elapsed = asyncio.get_running_loop().time() - start
        tx.stop()
        await task

        rate = len(rolls(session)) / elapsed
        # Fixed-sleep pacing would give ~33 Hz here (20ms + 10ms). Deadline
        # pacing should stay near the 50 Hz target.
        assert rate > 40, f"pacing only achieved {rate:.1f} Hz"

    async def test_does_not_burst_to_catch_up_after_a_stall(self, rig):
        """A deadline scheme that replays missed ticks would fire a burst at
        the droid the moment it recovers. Resync instead of catching up."""
        session, _, tx = rig
        tx.interval = 0.01

        stamps: list[float] = []
        original = session.transport.send
        async def timed_send(data, **kwargs):
            stamps.append(asyncio.get_running_loop().time())
            await original(data, **kwargs)
        session.transport.send = timed_send

        task = asyncio.create_task(tx.run())
        tx.set_drive(DriveCommand(speed=10, heading=0))
        await asyncio.sleep(0.03)

        # Stall the loop well past several deadlines.
        await asyncio.sleep(0)
        import time as _time
        _time.sleep(0.08)

        for i in range(20):
            tx.set_drive(DriveCommand(speed=i + 20, heading=i))
            await asyncio.sleep(0.004)
        tx.stop()
        await task

        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        # Recovery must not produce a run of near-zero gaps.
        tiny = [g for g in gaps if g < tx.interval * 0.3]
        assert len(tiny) <= 2, f"burst detected: {len(tiny)} packets crammed together"
