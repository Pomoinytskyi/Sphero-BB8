"""Transport seam: the only path between the codec and the radio.

Three implementations share one interface -- BLE, replay-from-file, and a
scriptable fake for tests. That seam is what makes ~90% of the suite runnable
with no droid present (constraint A9), which matters because hardware access
here is intermittent.

**Capture lives here** (design AD3/S2). Every byte crossing the seam is recorded
*before* dispatch, so there is no path to the radio that skips it -- unbypassable
by construction rather than by discipline. Recording is a buffered append costing
microseconds, and it happens before interpretation, so malformed frames that
would crash a decoder are still captured. That is precisely the case worth having
when debugging the Swift port.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Protocol, runtime_checkable

from bb8ctl import protocol


class Direction(str, Enum):
    TX = "tx"  # host -> droid
    RX = "rx"  # droid -> host


@dataclass(frozen=True)
class WireEvent:
    """One direction-tagged frame crossing the seam."""

    t: float
    direction: Direction
    data: bytes

    def to_json(self) -> str:
        return json.dumps({"t": round(self.t, 6), "dir": self.direction.value, "hex": self.data.hex()})

    @staticmethod
    def from_json(line: str) -> "WireEvent":
        d = json.loads(line)
        return WireEvent(t=d["t"], direction=Direction(d["dir"]), data=bytes.fromhex(d["hex"]))


class CaptureWriter:
    """Buffered JSONL sink for wire events.

    Deliberately synchronous. An async writer with backpressure accounting was
    considered and cut: appending to a buffered stream is a memcpy, and the
    machinery cost more than the problem it solved.
    """

    def __init__(self, path: str | Path, *, flush_every: int = 64) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", buffering=1 << 16)
        self._flush_every = flush_every
        self._since_flush = 0
        self.count = 0
        self._t0 = time.monotonic()

    def record(self, direction: Direction, data: bytes) -> None:
        self._fh.write(WireEvent(time.monotonic() - self._t0, direction, bytes(data)).to_json() + "\n")
        self.count += 1
        self._since_flush += 1
        if self._since_flush >= self._flush_every:
            self._fh.flush()
            self._since_flush = 0

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.flush()
            self._fh.close()

    def __enter__(self) -> "CaptureWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@runtime_checkable
class Transport(Protocol):
    """What the session layer is allowed to assume about the radio."""

    async def connect(self) -> None: ...
    async def send(self, data: bytes) -> None: ...
    async def close(self) -> None: ...
    def notifications(self) -> AsyncIterator[bytes]: ...
    @property
    def connected(self) -> bool: ...


class _QueueNotifier:
    """Shared plumbing: turn a push callback into an async iterator."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _push(self, data: bytes) -> None:
        self._q.put_nowait(bytes(data))

    def _stop(self) -> None:
        self._q.put_nowait(None)

    async def notifications(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._q.get()
            if item is None:
                return
            yield item


class BleTransport(_QueueNotifier):
    """Real BLE via bleak.

    ``write_response`` defaults to True, matching the known-good behaviour of
    spherov2. Write-*without*-response should be materially faster and is a
    Phase 3 measurement (docs/06 §5 step 4), not an assumption to bake in now.
    """

    def __init__(
        self,
        address: str,
        *,
        capture: CaptureWriter | None = None,
        write_response: bool = True,
    ) -> None:
        super().__init__()
        self.address = address
        self.capture = capture
        self.write_response = write_response
        self._client = None
        self._disconnected = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        import bleak

        self._disconnected.clear()
        self._client = bleak.BleakClient(
            self.address, timeout=15.0, disconnected_callback=lambda _: self._disconnected.set()
        )
        await self._client.connect()

    async def start_notifications(self) -> None:
        """Subscribe to the response characteristic.

        Separate from :meth:`connect` because the handshake must be written
        first -- subscribing before the anti-DOS write is one way to end up
        connected but permanently mute.
        """
        def _cb(_sender: object, data: bytearray) -> None:
            if self.capture:
                self.capture.record(Direction.RX, data)
            self._push(data)

        await self._client.start_notify(protocol.CHAR_RESPONSE, _cb)

    async def send(self, data: bytes) -> None:
        if self.capture:
            self.capture.record(Direction.TX, data)
        for chunk in protocol.chunked(data):
            await self._client.write_gatt_char(protocol.CHAR_COMMAND, chunk, self.write_response)

    async def write_raw(self, uuid: str, data: bytes) -> None:
        """Write to a characteristic other than the command one (handshake, wake)."""
        if self.capture:
            self.capture.record(Direction.TX, data)
        await self._client.write_gatt_char(uuid, data, True)

    async def wait_disconnected(self) -> None:
        await self._disconnected.wait()

    async def close(self) -> None:
        self._stop()
        if self._client is not None and self._client.is_connected:
            try:
                await self._client.disconnect()
            finally:
                self._client = None


class ReplayTransport(_QueueNotifier):
    """Replays a capture file. Needs no droid (constraint A9).

    Sends are accepted and recorded but go nowhere. RX events are re-emitted
    with their original inter-arrival gaps so timing-dependent behaviour --
    stream rates, dropouts, jitter -- reproduces faithfully.
    """

    def __init__(self, path: str | Path, *, speed: float = 1.0, realtime: bool = True) -> None:
        super().__init__()
        self.path = Path(path)
        self.speed = speed
        self.realtime = realtime
        self.sent: list[bytes] = []
        self._task: asyncio.Task | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _events(self) -> list[WireEvent]:
        with self.path.open() as fh:
            return [WireEvent.from_json(ln) for ln in fh if ln.strip()]

    async def connect(self) -> None:
        self._connected = True

    async def start_notifications(self) -> None:
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        previous = None
        try:
            for ev in self._events():
                if ev.direction is not Direction.RX:
                    continue
                if self.realtime and previous is not None:
                    gap = (ev.t - previous) / self.speed
                    if gap > 0:
                        await asyncio.sleep(gap)
                previous = ev.t
                self._push(ev.data)
        finally:
            self._stop()

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    async def write_raw(self, uuid: str, data: bytes) -> None:
        self.sent.append(bytes(data))

    async def close(self) -> None:
        self._connected = False
        if self._task is not None:
            self._task.cancel()
        self._stop()


class FakeTransport(_QueueNotifier):
    """Scriptable in-memory transport for tests.

    Optionally auto-answers acknowledged packets, so the session layer's
    ``PING``-gated readiness can be exercised without hardware.
    """

    def __init__(self, *, auto_respond: bool = True) -> None:
        super().__init__()
        self.sent: list[bytes] = []
        self.auto_respond = auto_respond
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def start_notifications(self) -> None:
        return None

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        # Only acknowledged packets (SOP2 == 0xFF) get a reply, mirroring the
        # firmware. Auto-answering everything would hide a stuck transmitter.
        if self.auto_respond and len(data) > 4 and data[1] == protocol.Sop2.ANSWER:
            self.inject_response(seq=data[4])

    async def write_raw(self, uuid: str, data: bytes) -> None:
        self.sent.append(bytes(data))

    def inject_response(self, *, code: int = 0x00, seq: int = 0, data: bytes = b"") -> None:
        body = bytes([code, seq, len(data) + 1]) + data
        self._push(bytes([0xFF, 0xFF]) + body + bytes([protocol.checksum(body)]))

    def inject_async(self, *, id_code: int, data: bytes = b"") -> None:
        dlen = len(data) + 1
        body = bytes([id_code, dlen >> 8, dlen & 0xFF]) + data
        self._push(bytes([0xFF, 0xFE]) + body + bytes([protocol.checksum(body)]))

    async def close(self) -> None:
        self._connected = False
        self._stop()


async def scan(timeout: float = 6.0) -> list[tuple[str, str]]:
    """Find advertising BB-8s. Returns ``(name, address)`` pairs.

    On macOS the address is a CoreBluetooth UUID, not a MAC -- it is stable per
    host but differs between machines, so it cannot be hard-coded portably.
    """
    import bleak

    devices = await bleak.BleakScanner.discover(timeout=timeout)
    return [
        (d.name, d.address)
        for d in devices
        if d.name and d.name.startswith(protocol.NAME_PREFIX)
    ]
