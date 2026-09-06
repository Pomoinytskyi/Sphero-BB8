"""Sphero API v1.20 wire protocol for BB-8 (and other v1 toys: Ollie, SPRK+, BB-9E).

This module is deliberately **standalone** -- it has no dependency on spherov2 or
on any BLE library. It encodes and decodes bytes, nothing more.

That isolation is the point. This is the reference implementation that gets
ported to Swift/CoreBluetooth in step 2 of this project, and keeping it free of
Python-specific machinery means the port is a transcription rather than a
redesign. Every constant here was verified against a live BB-8; see
``docs/BB8_PROTOCOL.md`` and the capture files under ``captures/``.

Wire format (host -> toy)::

    [SOP1] [SOP2] [DID] [CID] [SEQ] [DLEN] [ ...data... ] [CHK]
      FF     Fx    dev   cmd   seq   n+1      n bytes      sum

``SOP2`` is a flags byte, not a constant. The high six bits are always 1; the
low two select whether the toy answers and whether the command resets the
inactivity timer. spherov2 hardcodes it to 0xFF, which forces an acknowledgement
for *every* packet -- fine for setup commands, ruinous for a 20 Hz drive loop.
We use ``0xFE`` (no answer) on the hot path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Iterator

# --------------------------------------------------------------------------
# BLE topology
# --------------------------------------------------------------------------
# v1 toys expose two services. The "BLE service" holds the wake/anti-DOS
# plumbing that must be poked before the robot will talk at all; the "robot
# control service" carries the actual command protocol.

BLE_SERVICE = "22bb746f-2bb0-7554-2d6f-726568705327"
CHAR_WAKE = "22bb746f-2bbf-7554-2d6f-726568705327"  # write [0x01] to wake
CHAR_TX_POWER = "22bb746f-2bb2-7554-2d6f-726568705327"  # write [0x07]
CHAR_ANTI_DOS = "22bb746f-2bbd-7554-2d6f-726568705327"  # write b"011i3"

ROBOT_SERVICE = "22bb746f-2ba0-7554-2d6f-726568705327"
CHAR_COMMAND = "22bb746f-2ba1-7554-2d6f-726568705327"  # write commands here
CHAR_RESPONSE = "22bb746f-2ba6-7554-2d6f-726568705327"  # subscribe for notifies

#: Written in order, before subscribing to :data:`CHAR_RESPONSE`. Skipping the
#: anti-DOS string leaves the toy connected but permanently mute -- the single
#: most common reason a from-scratch implementation "connects" and then sees
#: nothing. Expect to rediscover this in Swift.
HANDSHAKE: tuple[tuple[str, bytes], ...] = (
    (CHAR_ANTI_DOS, b"011i3"),
    (CHAR_TX_POWER, bytes([0x07])),
)

#: BLE characteristic writes are chunked to this many bytes.
MTU_CHUNK = 20

#: Minimum spacing between packets. BB-8's firmware drops commands that arrive
#: faster than this; spherov2 calls it ``cmd_safe_interval``.
CMD_SAFE_INTERVAL = 0.06

#: BB-8 advertises with this local-name prefix.
NAME_PREFIX = "BB-"


# --------------------------------------------------------------------------
# Packet framing
# --------------------------------------------------------------------------

SOP1 = 0xFF


class Sop2(IntFlag):
    """Second start-of-packet byte: a flags field.

    ``BASE`` alone (0xFC) is a fire-and-forget packet that does not touch the
    inactivity timer. Almost everything wants ``NO_ANSWER`` or ``ANSWER``.
    """

    BASE = 0xFC
    ANSWER_REQUESTED = 0x01
    RESET_TIMEOUT = 0x02

    #: Acknowledged. Use for setup and for anything whose result you read.
    ANSWER = 0xFF
    #: Unacknowledged. Use for the drive loop -- no reply means no stall.
    NO_ANSWER = 0xFE


class Did(IntEnum):
    """Device (virtual subsystem) identifiers."""

    CORE = 0x00
    BOOTLOADER = 0x01
    SPHERO = 0x02


class CoreCmd(IntEnum):
    """Commands under :attr:`Did.CORE`."""

    PING = 0x01
    GET_VERSIONS = 0x02
    SET_BLUETOOTH_NAME = 0x10
    GET_BLUETOOTH_INFO = 0x11
    GET_POWER_STATE = 0x20
    ENABLE_BATTERY_NOTIFY = 0x21
    SLEEP = 0x22
    SET_INACTIVITY_TIMEOUT = 0x25
    GET_CHARGER_STATE = 0x38


class SpheroCmd(IntEnum):
    """Commands under :attr:`Did.SPHERO` -- the robot proper."""

    SET_HEADING = 0x01
    SET_STABILIZATION = 0x02
    SET_ROTATION_RATE = 0x03
    GET_CHASSIS_ID = 0x07
    SELF_LEVEL = 0x09
    SET_DATA_STREAMING = 0x11
    CONFIGURE_COLLISION_DETECTION = 0x12
    CONFIGURE_LOCATOR = 0x13
    GET_TEMPERATURE = 0x16
    SET_MAIN_LED = 0x20
    SET_BACK_LED = 0x21
    ROLL = 0x30
    BOOST = 0x31
    SET_RAW_MOTORS = 0x33
    SET_MOTION_TIMEOUT = 0x34
    SET_PERSISTENT_OPTIONS = 0x35
    GET_PERSISTENT_OPTIONS = 0x36
    SET_TEMPORARY_OPTIONS = 0x37
    GET_PERMANENT_OPTIONS = 0x3A


class RollMode(IntEnum):
    STOP = 0
    GO = 1
    #: Rotates the heading reference *without* driving the motors. This is how
    #: aim/calibration mode works -- the blue tail light swings around while the
    #: ball stays put.
    CALIBRATE = 2


class ResponseCode(IntEnum):
    OK = 0x00
    GENERAL_ERROR = 0x01
    CHECKSUM_FAILURE = 0x02
    FRAGMENT = 0x03
    UNKNOWN_COMMAND = 0x04
    UNSUPPORTED = 0x05
    BAD_MESSAGE_FORMAT = 0x06
    INVALID_PARAMETER = 0x07
    EXECUTE_FAILED = 0x08
    UNKNOWN_DEVICE = 0x09
    VOLTAGE_TOO_LOW = 0x31


def checksum(payload: bytes) -> int:
    """Sphero's packet checksum: ``0xFF - (sum mod 256)``.

    ``payload`` runs from DID through the last data byte -- the two SOP bytes
    are excluded, the CHK byte itself obviously so.
    """
    return 0xFF - (sum(payload) & 0xFF)


def build(
    did: int,
    cid: int,
    seq: int = 0,
    data: bytes = b"",
    *,
    answer: bool = False,
) -> bytes:
    """Frame one command packet.

    ``answer=False`` is the default because the drive loop dominates traffic and
    must never wait. Setup commands that you actually read a result from should
    pass ``answer=True`` and match the reply on ``seq``.
    """
    if not 0 <= seq <= 0xFF:
        raise ValueError(f"seq must fit in a byte, got {seq}")
    sop2 = Sop2.ANSWER if answer else Sop2.NO_ANSWER
    body = bytes([did, cid, seq, len(data) + 1]) + data
    return bytes([SOP1, sop2]) + body + bytes([checksum(body)])


def chunked(packet: bytes, size: int = MTU_CHUNK) -> Iterator[bytes]:
    """Split a packet into BLE-writable chunks."""
    for i in range(0, len(packet), size):
        yield packet[i : i + size]


# --------------------------------------------------------------------------
# Command encoders
# --------------------------------------------------------------------------
# Each returns a ready-to-write packet. Grouped to mirror the Swift enum you'll
# write in step 2.


def _u16(value: int) -> bytes:
    return int(value).to_bytes(2, "big")


def _i16(value: int) -> bytes:
    return int(value).to_bytes(2, "big", signed=True)


def roll(speed: int, heading: int, mode: RollMode = RollMode.GO, seq: int = 0) -> bytes:
    """The workhorse. Sets speed *and* heading in one packet.

    Doing this as a single ROLL rather than SET_HEADING + SET_SPEED halves the
    radio traffic per control tick, which is the difference between BB-8 feeling
    responsive and feeling like it is driving underwater.

    :param speed: 0-255. Above ~120 is unmanageable indoors.
    :param heading: 0-359 degrees, relative to the current aim origin.
    """
    speed = max(0, min(255, int(speed)))
    heading = int(heading) % 360
    data = bytes([speed]) + _u16(heading) + bytes([mode, 0])
    return build(Did.SPHERO, SpheroCmd.ROLL, seq, data)


def stop(heading: int = 0, seq: int = 0) -> bytes:
    """Halt, keeping the current heading."""
    return roll(0, heading, RollMode.STOP, seq)


def calibrate(heading: int, seq: int = 0) -> bytes:
    """Rotate the heading reference without driving. Used by aim mode."""
    return roll(0, heading, RollMode.CALIBRATE, seq)


def set_heading(heading: int, seq: int = 0) -> bytes:
    """Declare the current orientation to be ``heading`` degrees.

    ``set_heading(0)`` is the 'reset aim' operation: whatever way BB-8 is facing
    right now becomes forward.
    """
    return build(Did.SPHERO, SpheroCmd.SET_HEADING, seq, _u16(int(heading) % 360))


def set_stabilization(enabled: bool, seq: int = 0) -> bytes:
    return build(Did.SPHERO, SpheroCmd.SET_STABILIZATION, seq, bytes([int(enabled)]))


def set_rotation_rate(rate: int, seq: int = 0) -> bytes:
    """Turn rate, 0-255 in units of 0.784 deg/s. Lower = smoother aiming."""
    return build(Did.SPHERO, SpheroCmd.SET_ROTATION_RATE, seq, bytes([max(0, min(255, rate))]))


def set_main_led(r: int, g: int, b: int, seq: int = 0) -> bytes:
    """BB-8's body RGB LED."""
    clamp = lambda v: max(0, min(255, int(v)))  # noqa: E731
    return build(Did.SPHERO, SpheroCmd.SET_MAIN_LED, seq, bytes([clamp(r), clamp(g), clamp(b)]))


def set_back_led(brightness: int, seq: int = 0) -> bytes:
    """The blue tail light. Brightness 0-255.

    This is the aiming reference: it marks the *back* of the droid, so pointing
    it at yourself means 'forward' is away from you.
    """
    return build(Did.SPHERO, SpheroCmd.SET_BACK_LED, seq, bytes([max(0, min(255, brightness))]))


def set_motion_timeout(millis: int, seq: int = 0) -> bytes:
    """Auto-stop if no command arrives within ``millis``. A dead-man switch.

    Worth setting: if the app crashes or BLE drops mid-roll, BB-8 otherwise
    keeps going until it hits something.
    """
    return build(Did.SPHERO, SpheroCmd.SET_MOTION_TIMEOUT, seq, _u16(millis))


def set_inactivity_timeout(seconds: int, seq: int = 0) -> bytes:
    """Delay before BB-8 falls asleep on its own. Default is 600 s."""
    return build(Did.CORE, CoreCmd.SET_INACTIVITY_TIMEOUT, seq, _u16(seconds))


def configure_locator(x: int = 0, y: int = 0, yaw_tare: int = 0, flags: int = 1, seq: int = 0) -> bytes:
    """Zero/position the onboard dead-reckoning locator (centimetres)."""
    return build(
        Did.SPHERO,
        SpheroCmd.CONFIGURE_LOCATOR,
        seq,
        bytes([flags]) + _i16(x) + _i16(y) + _i16(yaw_tare),
    )


def set_data_streaming(
    interval_ms: int, samples_per_packet: int, mask: int, count: int = 0, extended_mask: int = 0, seq: int = 0
) -> bytes:
    """Start (or stop, with ``mask=0``) async sensor streaming.

    ``interval_ms`` is divided into a 400 Hz base clock by the firmware, so the
    effective rate is ``400 / interval_ms`` Hz. ``count=0`` streams forever.
    """
    data = _u16(interval_ms) + _u16(samples_per_packet) + int(mask).to_bytes(4, "big") + bytes([count])
    data += int(extended_mask).to_bytes(4, "big")
    return build(Did.SPHERO, SpheroCmd.SET_DATA_STREAMING, seq, data, answer=True)


def configure_collision_detection(
    method: int = 1, x_threshold: int = 100, x_speed: int = 100,
    y_threshold: int = 100, y_speed: int = 100, dead_time_ms: int = 100, seq: int = 0
) -> bytes:
    return build(
        Did.SPHERO,
        SpheroCmd.CONFIGURE_COLLISION_DETECTION,
        seq,
        bytes([method, x_threshold, x_speed, y_threshold, y_speed, dead_time_ms // 10]),
    )


def ping(seq: int = 0) -> bytes:
    """Round-trip probe. Also the cheapest keepalive."""
    return build(Did.CORE, CoreCmd.PING, seq, answer=True)


def get_power_state(seq: int = 0) -> bytes:
    return build(Did.CORE, CoreCmd.GET_POWER_STATE, seq, answer=True)


def sleep(seq: int = 0) -> bytes:
    return build(Did.CORE, CoreCmd.SLEEP, seq, bytes([0, 0, 0, 0]))


# --------------------------------------------------------------------------
# Response decoding
# --------------------------------------------------------------------------

ASYNC_SOP2 = 0xFE


@dataclass(frozen=True)
class Response:
    """A synchronous reply to a command we sent, matched by ``seq``."""

    code: int
    seq: int
    data: bytes

    @property
    def ok(self) -> bool:
        return self.code == ResponseCode.OK


@dataclass(frozen=True)
class AsyncMessage:
    """An unsolicited message: sensor stream, collision, sleep warning."""

    id_code: int
    data: bytes


class AsyncId(IntEnum):
    POWER_NOTIFICATION = 0x01
    LEVEL_1_DIAGNOSTIC = 0x02
    SENSOR_DATA = 0x03
    CONFIG_BLOCK = 0x04
    SLEEPING_SOON = 0x05
    MACRO_MARKERS = 0x06
    COLLISION_DETECTED = 0x07
    GYRO_AXIS_LIMIT = 0x0C
    DID_SLEEP = 0x0E


class Decoder:
    """Reassembles notification chunks into whole packets.

    BLE delivers arbitrary fragments, so this buffers until a full frame is
    present. Feed it every notification; it yields decoded packets.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> Iterator[Response | AsyncMessage]:
        self._buf.extend(data)
        while True:
            # Resynchronise: drop anything before a plausible start byte.
            while self._buf and self._buf[0] != SOP1:
                del self._buf[0]
            if len(self._buf) < 5:
                return

            sop2 = self._buf[1]
            if sop2 == SOP1:  # synchronous response
                dlen = self._buf[4]
                total = 5 + dlen
                if len(self._buf) < total:
                    return
                frame = bytes(self._buf[:total])
                del self._buf[:total]
                yield Response(code=frame[2], seq=frame[3], data=frame[5:-1])
            elif sop2 == ASYNC_SOP2:  # asynchronous message
                if len(self._buf) < 5:
                    return
                dlen = (self._buf[3] << 8) | self._buf[4]
                total = 5 + dlen
                if len(self._buf) < total:
                    return
                frame = bytes(self._buf[:total])
                del self._buf[:total]
                yield AsyncMessage(id_code=frame[2], data=frame[5:-1])
            else:
                # Not a frame we recognise; drop the byte and resynchronise.
                del self._buf[0]
