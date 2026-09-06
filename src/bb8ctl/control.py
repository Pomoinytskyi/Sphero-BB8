"""Stick input to drive command: deadzone, response curve, drive models, profiles.

Pure functions throughout, so the feel of driving is tunable and verifiable with
no hardware attached. Given intermittent droid access, being able to reason about
and test the control math dry is worth a lot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class SpeedProfile:
    """A named speed/response envelope (requirement C13/N7).

    BB-8's speed byte runs 0-255, but above roughly 120 it is unmanageable
    indoors -- it reaches a wall faster than a person reacts. Rather than bury
    that in a config value, it is two named modes on a button, switchable while
    driving and shown in the TUI.
    """

    name: str
    #: Maximum speed byte actually emitted.
    cap: int
    #: Response-curve exponent. >1 gives fine control near centre; 1.0 is linear.
    expo: float
    #: Degrees/second of heading change at full stick deflection, tank mode.
    turn_rate: float
    deadzone: float = 0.12


#: Default. Indoors, near furniture, and while aiming.
TORTOISE = SpeedProfile("tortoise", cap=90, expo=2.0, turn_rate=120.0)
#: Open floor. Sharper response, full speed range.
RABBIT = SpeedProfile("rabbit", cap=255, expo=1.4, turn_rate=220.0)

PROFILES: dict[str, SpeedProfile] = {p.name: p for p in (TORTOISE, RABBIT)}


class DriveMode(str, Enum):
    """How stick position becomes motion.

    ABSOLUTE is the more intuitive once aimed; TANK is better in tight spaces
    and needs no aiming. Both ship, switchable (UC2/UC3).
    """

    #: Stick direction is world direction, like a top-down game.
    ABSOLUTE = "absolute"
    #: Y is throttle, X is turn rate, relative to current facing. Like an RC car.
    TANK = "tank"


class CommandKind(str, Enum):
    """What a drive command asks the droid to do.

    A kind rather than a pair of booleans: DRIVE/STOP/CALIBRATE are mutually
    exclusive, and the type should say so. CALIBRATE maps to ROLL mode 2, which
    rotates the heading reference without engaging the motors (UC4).

    Named locally rather than reusing protocol.RollMode so this module stays
    free of protocol imports -- the control math is tunable independently of
    the wire format.
    """

    DRIVE = "drive"
    STOP = "stop"
    CALIBRATE = "calibrate"


@dataclass(frozen=True)
class DriveCommand:
    """What the transmitter will encode into a ROLL packet."""

    speed: int
    heading: int
    kind: CommandKind = CommandKind.DRIVE

    @property
    def stop(self) -> bool:
        return self.kind is CommandKind.STOP

    def __post_init__(self) -> None:
        if not 0 <= self.speed <= 255:
            raise ValueError(f"speed out of range: {self.speed}")
        if not 0 <= self.heading <= 359:
            raise ValueError(f"heading out of range: {self.heading}")


def radial_deadzone(x: float, y: float, deadzone: float) -> tuple[float, float]:
    """Apply a *radial* deadzone and rescale the remainder to full range.

    Radial, not per-axis: a square deadzone lets a stick resting slightly off
    centre creep in one axis while the other is suppressed, which reads as the
    droid drifting on its own. Rescaling matters too -- without it, output jumps
    discontinuously from 0 to ``deadzone`` the moment the stick crosses the
    threshold.
    """
    magnitude = math.hypot(x, y)
    if magnitude <= deadzone:
        return 0.0, 0.0
    if magnitude > 1.0:  # circular clamp; diagonals on a square gate exceed 1
        x, y, magnitude = x / magnitude, y / magnitude, 1.0
    scaled = (magnitude - deadzone) / (1.0 - deadzone)
    return x / magnitude * scaled, y / magnitude * scaled


def apply_expo(magnitude: float, expo: float) -> float:
    """Bend the response curve. Higher ``expo`` = finer control near centre."""
    return magnitude**expo


def map_absolute(x: float, y: float, profile: SpeedProfile) -> DriveCommand:
    """Stick direction is world direction.

    ``atan2(x, y)`` -- x first -- yields 0 degrees for 'stick pushed away',
    matching BB-8's heading convention where 0 is forward and angles increase
    clockwise. The conventional ``atan2(y, x)`` would give a
    counterclockwise-from-east frame and drive at 90 degrees to the stick.
    """
    dx, dy = radial_deadzone(x, y, profile.deadzone)
    magnitude = math.hypot(dx, dy)
    if magnitude == 0.0:
        return DriveCommand(speed=0, heading=0, kind=CommandKind.STOP)
    heading = int(math.degrees(math.atan2(dx, dy))) % 360
    speed = round(apply_expo(magnitude, profile.expo) * profile.cap)
    return DriveCommand(speed=speed, heading=heading)


def map_tank(
    x: float, y: float, profile: SpeedProfile, heading: int, dt: float
) -> DriveCommand:
    """Y is throttle, X steers relative to the current facing.

    Turn rate is integrated over ``dt`` so steering feel is frame-rate
    independent -- otherwise the droid would turn faster simply because the
    control loop ran quicker.
    """
    dx, dy = radial_deadzone(x, y, profile.deadzone)
    new_heading = int(heading + dx * profile.turn_rate * dt) % 360

    throttle = abs(dy)
    if throttle == 0.0:
        # Steering with no throttle still updates the heading, so releasing the
        # throttle mid-turn does not snap the droid back to its old bearing.
        return DriveCommand(speed=0, heading=new_heading, kind=CommandKind.STOP)

    if dy < 0:  # reverse: same axis, opposite bearing
        new_heading = (new_heading + 180) % 360
    speed = round(apply_expo(throttle, profile.expo) * profile.cap)
    return DriveCommand(speed=speed, heading=new_heading)


def map_input(
    x: float,
    y: float,
    mode: DriveMode,
    profile: SpeedProfile,
    heading: int = 0,
    dt: float = 1 / 60,
) -> DriveCommand:
    """Dispatch to the active drive model (constraint A3)."""
    if mode is DriveMode.ABSOLUTE:
        return map_absolute(x, y, profile)
    return map_tank(x, y, profile, heading, dt)


def aim_delta(x: float, profile: SpeedProfile, heading: int, dt: float) -> int:
    """Heading change while aiming (UC4).

    Aiming uses ROLL mode 2 (calibrate), which rotates the heading reference
    without driving the motors. Deliberately slower than steering -- aiming is a
    precision task and full turn rate overshoots.
    """
    if abs(x) < profile.deadzone:
        return heading
    return int(heading + x * profile.turn_rate * 0.5 * dt) % 360
