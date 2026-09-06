"""Sensor stream masks and sample decoding for BB-8.

Pure, like :mod:`bb8ctl.protocol` -- no I/O, no platform dependency, so it ports
to Swift alongside the codec (requirement N8).

BB-8 streams samples as a flat sequence of big-endian **signed 16-bit** values.
There are no field tags on the wire: the receiver must know which sensors were
requested and reconstruct the layout. Values appear in **descending mask-bit
order**, all primary-mask fields first, then all extended-mask fields.

That implicit contract is the whole reason this module exists. Get the ordering
wrong and every field silently decodes as its neighbour -- plausible-looking
numbers that are entirely wrong, which is far worse than a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class SensorSpec:
    """One streamable field."""

    name: str
    bit: int
    #: Converts the raw int16 to a physical unit.
    scale: Callable[[int], float] = float
    unit: str = ""


def _div(n: float) -> Callable[[int], float]:
    return lambda v: v / n


def _mul(n: float) -> Callable[[int], float]:
    return lambda v: v * n


#: Fields selected by the primary 32-bit mask, descending bit order.
PRIMARY: tuple[SensorSpec, ...] = (
    SensorSpec("accel_raw_x", 0x00400000, unit="raw"),
    SensorSpec("accel_raw_y", 0x00200000, unit="raw"),
    SensorSpec("accel_raw_z", 0x00100000, unit="raw"),
    SensorSpec("pitch", 0x00040000, unit="deg"),
    SensorSpec("roll", 0x00020000, unit="deg"),
    SensorSpec("yaw", 0x00010000, unit="deg"),
    SensorSpec("accel_x", 0x00008000, _div(4096), "g"),
    SensorSpec("accel_y", 0x00004000, _div(4096), "g"),
    SensorSpec("accel_z", 0x00002000, _div(4096), "g"),
    SensorSpec("gyro_x", 0x00001000, _mul(0.1), "deg/s"),
    SensorSpec("gyro_y", 0x00000800, _mul(0.1), "deg/s"),
    SensorSpec("gyro_z", 0x00000400, _mul(0.1), "deg/s"),
    SensorSpec("emf_left", 0x00000040, unit="raw"),
    SensorSpec("emf_right", 0x00000020, unit="raw"),
)

#: Fields selected by the extended 32-bit mask, descending bit order.
EXTENDED: tuple[SensorSpec, ...] = (
    SensorSpec("quat_x", 0x80000000, _div(10000)),
    SensorSpec("quat_y", 0x40000000, _div(10000)),
    SensorSpec("quat_z", 0x20000000, _div(10000)),
    SensorSpec("quat_w", 0x10000000, _div(10000)),
    SensorSpec("locator_x", 0x08000000, unit="cm"),
    SensorSpec("locator_y", 0x04000000, unit="cm"),
    SensorSpec("accel_one", 0x02000000, unit="raw"),
    SensorSpec("velocity_x", 0x01000000, _mul(0.1), "cm/s"),
    SensorSpec("velocity_y", 0x00800000, _mul(0.1), "cm/s"),
    SensorSpec("speed", 0x00400000, unit="cm/s"),
)

BY_NAME: dict[str, SensorSpec] = {s.name: s for s in (*PRIMARY, *EXTENDED)}

#: A useful default for driving: enough to see motion and position without
#: saturating the link. Locator and speed are what the trajectory work needs.
DRIVE_PRESET: tuple[str, ...] = (
    "yaw", "speed", "locator_x", "locator_y", "velocity_x", "velocity_y",
)


class UnknownSensor(KeyError):
    """Raised for a sensor name not in :data:`BY_NAME`."""


def build_masks(names: Iterable[str]) -> tuple[int, int]:
    """Turn sensor names into the ``(primary, extended)`` mask pair.

    Raises :class:`UnknownSensor` rather than silently skipping a typo -- a
    dropped field would shift every subsequent value in the decoded sample.
    """
    primary = extended = 0
    primary_bits = {s.name: s.bit for s in PRIMARY}
    for name in names:
        if name in primary_bits:
            primary |= primary_bits[name]
        elif name in {s.name for s in EXTENDED}:
            extended |= BY_NAME[name].bit
        else:
            raise UnknownSensor(f"unknown sensor {name!r}")
    return primary, extended


def layout(primary: int, extended: int) -> tuple[SensorSpec, ...]:
    """The field order a sample with these masks will arrive in."""
    return tuple(s for s in PRIMARY if s.bit & primary) + tuple(
        s for s in EXTENDED if s.bit & extended
    )


def expected_length(primary: int, extended: int) -> int:
    """Bytes in one sample. Two per field, since every value is an int16."""
    return 2 * len(layout(primary, extended))


class SampleLengthError(ValueError):
    """Payload length disagrees with the requested masks."""


def decode_sample(data: bytes, primary: int, extended: int) -> dict[str, float]:
    """Decode one scaled sample.

    The length check is the only defence against a mask/payload mismatch: with
    no field tags on the wire, a wrong mask produces confidently wrong numbers
    rather than an error. Fail loudly instead.
    """
    fields = layout(primary, extended)
    if len(data) != 2 * len(fields):
        raise SampleLengthError(
            f"expected {2 * len(fields)} bytes for {len(fields)} fields, got {len(data)}"
        )
    out: dict[str, float] = {}
    for i, spec in enumerate(fields):
        raw = int.from_bytes(data[i * 2 : i * 2 + 2], "big", signed=True)
        out[spec.name] = spec.scale(raw)
    return out


def decode_frame(data: bytes, primary: int, extended: int) -> list[dict[str, float]]:
    """Decode an async 0x03 payload, which may carry several samples."""
    size = expected_length(primary, extended)
    if size == 0:
        return []
    if len(data) % size:
        raise SampleLengthError(f"payload {len(data)} is not a multiple of sample size {size}")
    return [decode_sample(data[i : i + size], primary, extended) for i in range(0, len(data), size)]
