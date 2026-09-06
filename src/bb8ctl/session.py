"""Connection lifecycle: handshake, readiness, reconnection, shutdown.

We own this rather than delegating to spherov2 (design AD2). The reason is step
2: Swift will have a raw BLE client and hand-written reconnection, so building
the CLI the same way means step 1 exercises the architecture step 2 actually
ships. Anything a library hides here is something we would meet for the first
time in Swift, with no working reference to compare against.
"""

from __future__ import annotations

import asyncio
import time

from bb8ctl import protocol, sensors
from bb8ctl.state import LinkState, State
from bb8ctl.transport import Direction, Transport


class HandshakeError(RuntimeError):
    """The droid connected but never became responsive."""


class Session:
    """Owns the transport, the sequence counter and the notify pump."""

    #: How long to wait for an acknowledged command before giving up.
    RESPONSE_TIMEOUT = 4.0
    #: Readiness probe budget. Short: a healthy droid answers in milliseconds.
    PING_TIMEOUT = 3.0
    #: Pause after the wake write before the firmware will answer.
    WAKE_SETTLE = 0.3
    #: Motion stops if no command arrives within this window. The firmware-level
    #: dead-man switch (N4) -- if this process dies mid-roll, the droid stops
    #: itself instead of driving into a wall until the battery flattens.
    MOTION_TIMEOUT_MS = 2000

    def __init__(self, transport: Transport, state: State | None = None) -> None:
        self.transport = transport
        self.state = state or State()
        self.decoder = protocol.Decoder()
        self._seq = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._sent_at: dict[int, float] = {}
        self._masks: tuple[int, int] = (0, 0)
        self._pump_task: asyncio.Task | None = None

    # -- packet plumbing ---------------------------------------------------

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) % 0x100
        return self._seq

    async def send(self, packet: bytes, *, reliable: bool = False) -> None:
        """Fire and forget. The hot path -- must never wait (N3)."""
        await self.transport.send(packet, reliable=reliable)
        self.state.traffic.note_send()
        self.state.wire.append((self.state.uptime, Direction.TX.value, packet))

    async def request(self, make_packet, *, timeout: float | None = None):
        """Send an acknowledged packet and await its reply.

        Only for setup and probing. Using this on the drive path is what caps
        spherov2 at ~8 commands/sec.
        """
        seq = self._next_seq()
        packet = make_packet(seq=seq)
        # Force the acknowledge bit regardless of the encoder's default.
        packet = bytes([packet[0], protocol.Sop2.ANSWER]) + packet[2:]
        packet = packet[:-1] + bytes([protocol.checksum(packet[2:-1])])

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[seq] = future
        self._sent_at[seq] = time.monotonic()
        try:
            # Reliable: we are about to block on a reply, so a silently dropped
            # write would show up as a timeout and look like a dead droid.
            await self.send(packet, reliable=True)
            return await asyncio.wait_for(future, timeout or self.RESPONSE_TIMEOUT)
        finally:
            self._waiting.pop(seq, None)
            self._sent_at.pop(seq, None)

    async def _pump(self) -> None:
        """Feed notifications to the decoder and dispatch.

        Wrapped so a malformed frame cannot kill the pump -- losing this task
        silently would make the droid appear connected but deaf (A7).
        """
        async for chunk in self.transport.notifications():
            self.state.traffic.received += 1
            self.state.wire.append((self.state.uptime, Direction.RX.value, chunk))
            try:
                for message in self.decoder.feed(chunk):
                    self._dispatch(message)
            except Exception as exc:  # noqa: BLE001 - resilience is the point
                self.state.traffic.errors += 1
                self.state.log("decode-error", str(exc))

    def _dispatch(self, message: protocol.Response | protocol.AsyncMessage) -> None:
        if isinstance(message, protocol.Response):
            sent = self._sent_at.get(message.seq)
            if sent is not None:
                self.state.traffic.last_latency_ms = (time.monotonic() - sent) * 1000
            future = self._waiting.get(message.seq)
            if future is not None and not future.done():
                future.set_result(message)
            elif not message.ok:
                self.state.log("cmd-error", f"seq={message.seq} code=0x{message.code:02x}")
        else:
            self._handle_async(message)

    def _handle_async(self, message: protocol.AsyncMessage) -> None:
        if message.id_code == protocol.AsyncId.SENSOR_DATA:
            try:
                samples = sensors.decode_frame(message.data, *self._masks)
            except ValueError as exc:
                self.state.traffic.errors += 1
                self.state.log("sensor-decode", str(exc))
                return
            if samples:
                self.state.telemetry.update(samples[-1])
        elif message.id_code == protocol.AsyncId.COLLISION_DETECTED:
            self.state.log("collision", message.data.hex())
        elif message.id_code == protocol.AsyncId.SLEEPING_SOON:
            self.state.log("sleeping-soon", "droid will sleep")
        elif message.id_code == protocol.AsyncId.DID_SLEEP:
            self.state.log("slept", "droid slept")
            self.state.link = LinkState.DEGRADED
        elif message.id_code == protocol.AsyncId.POWER_NOTIFICATION:
            self.state.log("power", message.data.hex())

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Bring the link to READY, or raise with a state that says where it failed."""
        state = self.state
        state.link = LinkState.CONNECTING
        await self.transport.connect()

        state.link = LinkState.HANDSHAKING
        # Order matters. Subscribing before the anti-DOS write leaves the droid
        # connected but permanently mute -- the classic v1 failure, and one that
        # looks exactly like a bug in your own code.
        for uuid, payload in protocol.HANDSHAKE:
            await self.transport.write_raw(uuid, payload)

        await self.transport.start_notifications()
        self._pump_task = asyncio.create_task(self._pump())

        state.link = LinkState.WAKING
        await self.transport.write_raw(protocol.CHAR_WAKE, bytes([0x01]))
        await asyncio.sleep(self.WAKE_SETTLE)  # firmware needs a moment before it answers

        # PING gates readiness: connected is not ready (A1).
        try:
            await self.request(protocol.ping, timeout=self.PING_TIMEOUT)
        except asyncio.TimeoutError as exc:
            state.link = LinkState.DEGRADED
            state.link_detail = "connected but no PING reply -- droid asleep or flat?"
            raise HandshakeError(state.link_detail) from exc

        state.link = LinkState.READY
        state.link_detail = ""
        state.log("connected", state.droid_address or "")

    async def configure(self, *, stream: tuple[str, ...] | None = None, stream_hz: float = 10.0) -> None:
        """Connect-time setup, as one idempotent unit.

        Deliberately a single replayable step so reconnection is a *replay*
        rather than a re-derivation (A10) -- scattered init is how a reconnected
        session ends up subtly different from a fresh one.
        """
        # Reliable writes: these run once, and a dropped one leaves the droid in
        # a subtly wrong state for the whole session. Notably, without
        # SET_STABILIZATION the control system stays off and ROLL does nothing --
        # well-formed packets, motionless droid, no error anywhere.
        await self.send(protocol.set_motion_timeout(self.MOTION_TIMEOUT_MS, seq=self._next_seq()),
                        reliable=True)
        await self.send(protocol.set_stabilization(True, seq=self._next_seq()), reliable=True)
        await self.send(protocol.set_back_led(64, seq=self._next_seq()), reliable=True)
        await self.send(protocol.configure_collision_detection(seq=self._next_seq()), reliable=True)

        # Read the battery once at connect so the panel is populated from the
        # start rather than sitting on a dash until something else asks.
        await self.read_power()

        if stream:
            primary, extended = sensors.build_masks(stream)
            self._masks = (primary, extended)
            await self.send(
                protocol.set_data_streaming(
                    divisor=protocol.hz_to_divisor(stream_hz), samples_per_packet=1,
                    mask=primary, count=0, extended_mask=extended, seq=self._next_seq(),
                )
            )

    async def read_power(self) -> float | None:
        """Query battery voltage and cache it on shared state.

        Payload layout verified on hardware: ``[0]`` record version, ``[1]``
        power state, ``[2:4]`` centivolts, ``[4:6]`` charge count.
        """
        try:
            response = await self.request(protocol.get_power_state, timeout=2.0)
        except Exception:  # noqa: BLE001
            return None
        data = response.data
        if len(data) < 4:
            return None
        self.state.battery_v = int.from_bytes(data[2:4], "big") / 100
        if data[1] in (3, 4):  # low / critical
            self.state.log("battery", f"{self.state.battery_v:.2f} V -- low")
        return self.state.battery_v

    async def stop_droid(self) -> None:
        """Best-effort halt. Never raises -- it runs on the shutdown path."""
        try:
            await self.send(protocol.stop(self.state.heading, seq=self._next_seq()))
        except Exception:  # noqa: BLE001
            pass

    async def shutdown(self) -> None:
        """The single exit path (A11): stop, unsubscribe, disconnect.

        Reached from SIGINT, exceptions and TUI quit alike. The notebook this
        project replaces crashed on exit precisely because it had no such path.
        """
        await self.stop_droid()
        if self._pump_task is not None:
            self._pump_task.cancel()
        await self.transport.close()
        self.state.link = LinkState.DISCONNECTED
        self.state.log("shutdown")
