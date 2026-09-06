"""Transport seam tests -- all offline.

The property that matters most: capture is unbypassable. If a future change adds
a path to the radio that skips recording, these tests should fail.
"""

import json

import pytest

from bb8ctl import protocol
from bb8ctl.transport import (
    CaptureWriter, Direction, FakeTransport, ReplayTransport, Transport, WireEvent,
)


class TestWireEvent:
    def test_json_round_trip(self):
        ev = WireEvent(t=1.5, direction=Direction.TX, data=b"\xff\xfe\x02")
        assert WireEvent.from_json(ev.to_json()) == ev

    def test_serialises_as_hex_not_escapes(self):
        """Hex keeps captures greppable and diffable by hand."""
        assert json.loads(WireEvent(0.0, Direction.RX, b"\xff\x00").to_json())["hex"] == "ff00"


class TestCaptureWriter:
    def test_writes_one_line_per_frame(self, tmp_path):
        with CaptureWriter(tmp_path / "c.jsonl") as cap:
            cap.record(Direction.TX, b"\x01")
            cap.record(Direction.RX, b"\x02")
        assert len((tmp_path / "c.jsonl").read_text().strip().splitlines()) == 2

    def test_timestamps_are_monotonic(self, tmp_path):
        with CaptureWriter(tmp_path / "c.jsonl") as cap:
            for _ in range(20):
                cap.record(Direction.TX, b"\x01")
        ts = [WireEvent.from_json(l).t for l in (tmp_path / "c.jsonl").read_text().splitlines()]
        assert ts == sorted(ts)

    def test_creates_parent_directory(self, tmp_path):
        CaptureWriter(tmp_path / "deep" / "nested" / "c.jsonl").close()
        assert (tmp_path / "deep" / "nested").is_dir()

    def test_flushes_on_close_even_below_batch_size(self, tmp_path):
        cap = CaptureWriter(tmp_path / "c.jsonl", flush_every=1000)
        cap.record(Direction.TX, b"\x01")
        cap.close()
        assert (tmp_path / "c.jsonl").read_text().strip()


class TestFakeTransport:
    async def test_records_sends(self):
        t = FakeTransport()
        await t.connect()
        await t.send(protocol.roll(100, 90))
        assert t.sent == [protocol.roll(100, 90)]

    async def test_auto_responds_only_to_acknowledged_packets(self):
        """Mirrors firmware: SOP2=0xFE gets no reply. Auto-answering
        everything would mask a transmitter stuck waiting on a fire-and-forget
        packet -- exactly the bug this design exists to avoid."""
        t = FakeTransport()
        await t.connect()
        notifications = t.notifications()

        await t.send(protocol.roll(100, 90))          # unacknowledged
        await t.send(protocol.ping(seq=7))            # acknowledged
        first = await anext(notifications)
        decoded = list(protocol.Decoder().feed(first))
        assert decoded[0].seq == 7                     # the ping, not the roll

    async def test_inject_async_is_decodable(self):
        t = FakeTransport()
        await t.connect()
        t.inject_async(id_code=0x07, data=b"\xaa")
        msg = list(protocol.Decoder().feed(await anext(t.notifications())))[0]
        assert isinstance(msg, protocol.AsyncMessage) and msg.id_code == 0x07

    async def test_close_terminates_the_notification_stream(self):
        """A stream that never ends would hang shutdown (A11)."""
        t = FakeTransport()
        await t.connect()
        await t.close()
        assert [n async for n in t.notifications()] == []

    def test_satisfies_the_transport_protocol(self):
        assert isinstance(FakeTransport(), Transport)


class TestReplayTransport:
    @pytest.fixture
    def capture_file(self, tmp_path):
        path = tmp_path / "session.jsonl"
        with CaptureWriter(path) as cap:
            cap.record(Direction.TX, protocol.ping(seq=1))
            cap.record(Direction.RX, b"\xff\xff\x00\x01\x01\xfd")
            cap.record(Direction.RX, b"\xff\xff\x00\x02\x01\xfc")
        return path

    async def test_replays_only_received_frames(self, capture_file):
        t = ReplayTransport(capture_file, realtime=False)
        await t.connect()
        await t.start_notifications()
        assert len([n async for n in t.notifications()]) == 2

    async def test_replayed_frames_decode(self, capture_file):
        t = ReplayTransport(capture_file, realtime=False)
        await t.connect()
        await t.start_notifications()
        decoder = protocol.Decoder()
        seqs = [m.seq async for n in t.notifications() for m in decoder.feed(n)]
        assert seqs == [1, 2]

    async def test_sends_are_captured_not_transmitted(self, capture_file):
        t = ReplayTransport(capture_file, realtime=False)
        await t.connect()
        await t.send(b"\x01\x02")
        assert t.sent == [b"\x01\x02"]

    async def test_stream_terminates_so_shutdown_completes(self, capture_file):
        t = ReplayTransport(capture_file, realtime=False)
        await t.connect()
        await t.start_notifications()
        async for _ in t.notifications():
            pass  # must not hang

    def test_satisfies_the_transport_protocol(self, capture_file):
        assert isinstance(ReplayTransport(capture_file), Transport)


class TestCaptureReplayRoundTrip:
    async def test_captured_bytes_replay_identically(self, tmp_path):
        """The core guarantee behind offline development: what the droid said
        once can be replayed verbatim, forever, with no droid."""
        path = tmp_path / "rt.jsonl"
        original = [b"\xff\xff\x00\x01\x01\xfd", b"\xff\xfe\x03\x00\x03\x01\x02\xf7"]
        with CaptureWriter(path) as cap:
            for frame in original:
                cap.record(Direction.RX, frame)

        t = ReplayTransport(path, realtime=False)
        await t.connect()
        await t.start_notifications()
        assert [n async for n in t.notifications()] == original
