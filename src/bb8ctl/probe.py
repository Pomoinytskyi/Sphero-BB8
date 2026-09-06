"""Hardware verification suite -- the primary purpose of step 1.

Every capability in ``docs/03-capability-catalog.md`` is currently marked SRC:
read from a working library, never confirmed against this particular droid with
this particular firmware. This module flips those to verified, and answers the
six open questions in ``docs/02-requirements.md`` §8.

Stages run cheapest-and-most-diagnostic first. If PING fails there is no point
measuring throughput, and reporting twenty failures when one thing is wrong just
buries the actual cause.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from dataclasses import dataclass, field

from bb8ctl import protocol, sensors
from bb8ctl.session import Session


#: GET_POWER_STATE byte [1]. Verified: a healthy BB-8 reports 2.
POWER_STATES = {1: "charging", 2: "battery OK", 3: "battery low", 4: "battery critical"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    numbers: dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        extra = "  ".join(f"{k}={v:.2f}" for k, v in self.numbers.items())
        return f"[{mark}] {self.name:<34} {self.detail} {extra}".rstrip()


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def failed(self) -> int:
        return len(self.checks) - self.passed

    def render(self) -> str:
        lines = [str(c) for c in self.checks]
        lines.append("")
        lines.append(f"{self.passed} passed, {self.failed} failed")
        return "\n".join(lines)


class Prober:
    """Runs verification stages against a live droid."""

    def __init__(self, session: Session, *, observer=print) -> None:
        self.session = session
        self.report = Report()
        self.say = observer

    def _record(self, check: Check) -> Check:
        self.report.add(check)
        self.say(str(check))
        return check

    # -- stages ------------------------------------------------------------

    async def stage_identity(self) -> None:
        """Firmware and battery baseline. Cheap, and contextualises everything else."""
        try:
            response = await self.session.request(protocol.get_power_state)
            data = response.data
            # GET_POWER_STATE payload, verified on hardware:
            #   [0] record version  [1] power state  [2:4] centivolts
            #   [4:6] charge count  [6:8] seconds since charge
            # (An earlier version sliced [1:3] and reported 5.15 V for an 8.07 V
            # pack -- an off-by-one that looked like a flat battery.)
            state_code = data[1] if len(data) > 1 else 0
            voltage = int.from_bytes(data[2:4], "big") / 100 if len(data) >= 4 else 0.0
            charges = int.from_bytes(data[4:6], "big") if len(data) >= 6 else 0
            self.session.state.battery_v = voltage
            label = POWER_STATES.get(state_code, f"unknown({state_code})")
            self._record(Check("power state", True, f"{voltage:.2f} V, {label}, {charges} charges",
                               {"volts": voltage}))
            # BB-8 carries a 2-cell pack: ~8.4 V full, ~7.0 V is genuinely low.
            self._record(Check("battery healthy", state_code in (1, 2),
                               label if state_code in (1, 2)
                               else f"{label} -- charge before trusting drive tests"))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("power state", False, str(exc)))

        try:
            response = await self.session.request(
                lambda seq: protocol.build(protocol.Did.CORE, protocol.CoreCmd.GET_VERSIONS,
                                           seq, answer=True))
            self._record(Check("get versions", True, response.data.hex()))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("get versions", False, str(exc)))

    async def stage_latency(self, samples: int = 20) -> None:
        """Round-trip latency for acknowledged commands."""
        timings: list[float] = []
        for _ in range(samples):
            start = time.monotonic()
            try:
                await self.session.request(protocol.ping, timeout=2.0)
            except Exception:  # noqa: BLE001
                continue
            timings.append((time.monotonic() - start) * 1000)
            await asyncio.sleep(0.05)

        if not timings:
            self._record(Check("ping latency", False, "no replies"))
            return
        self._record(Check("ping latency", True, f"n={len(timings)}", {
            "min_ms": min(timings),
            "median_ms": statistics.median(timings),
            "max_ms": max(timings),
        }))

    async def stage_unacknowledged(self, count: int = 20) -> None:
        """**Open question 1** -- does BB-8 honour SOP2=0xFE?

        The highest-value measurement in the project. If unacknowledged packets
        work, the drive loop never waits on a reply and the §6 N1 command-budget
        conflict between driving and lighting dissolves. If they do not,
        lighting must stay strictly subordinate to driving.

        Detected by counting replies: an honoured no-answer flag produces
        silence. The operator confirms the droid still *acted* -- twenty stop
        commands are harmless, so this is safe to run before drive tests.
        """
        before = self.session.state.traffic.received
        for _ in range(count):
            await self.session.send(protocol.stop(0, seq=self.session._next_seq()))
            await asyncio.sleep(0.07)
        await asyncio.sleep(0.5)
        replies = self.session.state.traffic.received - before

        honoured = replies == 0
        self._record(Check(
            "SOP2=0xFE unacknowledged", honoured,
            "silent as requested" if honoured else f"droid replied {replies}x -- flag ignored",
            {"replies": replies, "sent": count},
        ))

    async def stage_rate(self) -> None:
        """**Open question 2** -- the true packet throughput ceiling.

        Measures *achieved* rate for unacknowledged packets, which is what the
        drive loop actually sends.

        An earlier version slept the target interval **after** a blocking
        acknowledged round-trip, so real spacing was always (latency + interval)
        and it never probed above ~11 Hz -- then reported the target interval as
        if it had been achieved. Measure what happens, not what was asked for.
        """
        results: list[tuple[float, float]] = []
        for target in (0.10, 0.07, 0.05, 0.04, 0.03, 0.02, 0.015, 0.010):
            errors_before = self.session.state.traffic.errors
            start = time.monotonic()
            for _ in range(30):
                await self.session.send(protocol.stop(0, seq=self.session._next_seq()))
                await asyncio.sleep(target)
            elapsed = time.monotonic() - start
            achieved = 30 / elapsed
            errors = self.session.state.traffic.errors - errors_before
            results.append((target, achieved))
            self._record(Check(
                f"rate target {target * 1000:.0f}ms", errors == 0,
                f"achieved {achieved:.1f} Hz",
                {"target_hz": 1 / target, "achieved_hz": achieved, "errors": errors},
            ))

        # The ceiling is where achieved rate stops tracking the target: past that
        # point the link, not our pacing, is the limit.
        ceiling = max(a for _, a in results)
        self._record(Check("throughput ceiling", True, f"{ceiling:.1f} pkt/s",
                           {"hz": ceiling, "interval_ms": 1000 / ceiling}))
        self._record(Check("write mode", True,
                           "with-response" if getattr(self.session.transport, "write_response", True)
                           else "without-response (--no-response)"))

    async def _ensure_stream(self, hz: float = 16.0) -> None:
        """Turn on telemetry so motion can be *measured* rather than eyeballed."""
        primary, extended = sensors.build_masks(sensors.DRIVE_PRESET)
        self.session._masks = (primary, extended)
        await self.session.send(protocol.set_data_streaming(
            divisor=protocol.hz_to_divisor(hz), samples_per_packet=1, mask=primary,
            count=0, extended_mask=extended, seq=self.session._next_seq()), reliable=True)
        await asyncio.sleep(0.4)

    async def _watch(self, duration: float) -> dict[str, float]:
        """Sample telemetry across a window, returning what actually happened.

        Turning "confirm the droid moved" into a measurement matters more than
        it looks: the first probe run reported PASS for three drive checks on a
        droid that never budged, because 'packet accepted' was all it tested.
        """
        tel = self.session.state.telemetry
        x0, y0 = tel.get("locator_x", 0.0), tel.get("locator_y", 0.0)
        yaw0 = tel.get("yaw", 0.0)
        peak_speed = 0.0
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            peak_speed = max(
                peak_speed,
                abs(tel.get("speed", 0.0)),
                math.hypot(tel.get("velocity_x", 0.0), tel.get("velocity_y", 0.0)),
            )
        dx = tel.get("locator_x", 0.0) - x0
        dy = tel.get("locator_y", 0.0) - y0
        dyaw = abs((tel.get("yaw", 0.0) - yaw0 + 180) % 360 - 180)
        return {"peak_speed": peak_speed, "distance_cm": math.hypot(dx, dy), "yaw_change": dyaw}

    async def stage_drive(self) -> None:
        """**Open question 6** -- ROLL modes, verified against telemetry.

        The droid will move. Give it a metre of clear floor.
        """
        await self._ensure_stream()

        # Stabilization must be on or ROLL is inert -- well-formed packets, a
        # motionless droid, and no error to explain it.
        await self.session.send(protocol.set_stabilization(True, seq=self.session._next_seq()),
                                reliable=True)
        await asyncio.sleep(0.3)

        try:
            await self.session.send(protocol.roll(70, 0, seq=self.session._next_seq()))
            moved = await self._watch(1.2)
            await self.session.send(protocol.stop(0, seq=self.session._next_seq()))
            await asyncio.sleep(0.6)
            ok = moved["peak_speed"] > 3.0 or moved["distance_cm"] > 5.0
            self._record(Check(
                "roll moves the droid", ok,
                "measured motion" if ok else "NO MOTION DETECTED -- check floor space and head",
                {"peak_speed": moved["peak_speed"], "distance_cm": moved["distance_cm"]},
            ))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("roll moves the droid", False, str(exc)))

        try:
            # Calibrate should swing the heading reference while the ball stays put.
            for heading in (0, 120, 240, 0):
                await self.session.send(protocol.calibrate(heading, seq=self.session._next_seq()))
                await asyncio.sleep(0.5)
            turned = await self._watch(0.8)
            stayed = turned["distance_cm"] < 10.0
            self._record(Check(
                "ROLL mode 2 rotates without driving", stayed,
                "stayed in place" if stayed else "droid translated -- not a pure rotation",
                {"distance_cm": turned["distance_cm"]},
            ))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("ROLL mode 2 rotates without driving", False, str(exc)))

    async def stage_safety(self) -> None:
        """**Open question 5** -- does SET_MOTION_TIMEOUT actually fire?

        Sets a short timeout, starts rolling, then deliberately stops sending.
        A droid that keeps going has no dead-man switch, which would make N4
        unenforceable and every crash a runaway.
        """
        await self._ensure_stream()
        try:
            await self.session.send(protocol.set_motion_timeout(800, seq=self.session._next_seq()),
                                    reliable=True)
            await asyncio.sleep(0.2)
            await self.session.send(protocol.roll(70, 0, seq=self.session._next_seq()))
            rolling = await self._watch(0.7)          # should be moving here
            # Now go silent. The firmware timeout must stop it unaided.
            coasting = await self._watch(2.0)
            started = rolling["peak_speed"] > 3.0
            stopped = coasting["peak_speed"] < max(3.0, rolling["peak_speed"] * 0.4)
            self._record(Check(
                "motion timeout stops the droid", started and stopped,
                "stopped unaided" if started and stopped
                else ("never started -- inconclusive" if not started
                      else "STILL MOVING -- no dead-man switch, N4 unenforceable"),
                {"rolling_speed": rolling["peak_speed"], "after_silence": coasting["peak_speed"]},
            ))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("motion timeout stops the droid", False, str(exc)))
        finally:
            await self.session.send(protocol.stop(0, seq=self.session._next_seq()))
            await self.session.send(
                protocol.set_motion_timeout(Session.MOTION_TIMEOUT_MS, seq=self.session._next_seq()))

    async def stage_sensors(self) -> None:
        """**Open question 4** -- stream rates and dropouts."""
        for hz in (4.0, 8.0, 16.0):
            self.session.state.telemetry.clear()
            before = self.session.state.traffic.received
            primary, extended = sensors.build_masks(sensors.DRIVE_PRESET)
            self.session._masks = (primary, extended)
            divisor = protocol.hz_to_divisor(hz)
            await self.session.send(protocol.set_data_streaming(
                divisor=divisor, samples_per_packet=1, mask=primary,
                count=0, extended_mask=extended, seq=self.session._next_seq()))
            await asyncio.sleep(3.0)
            received = self.session.state.traffic.received - before
            # Rate is 400/divisor, NOT 1000/divisor. Getting this wrong made a
            # perfectly healthy stream look like 40% packet loss.
            expected = 3.0 * protocol.divisor_to_hz(divisor)
            ratio = received / expected if expected else 0.0
            self._record(Check(f"sensor stream {hz:.0f} Hz (divisor {divisor})",
                               ratio > 0.85, f"{received} frames of ~{expected:.0f}",
                               {"delivered_pct": ratio * 100}))

        # Stop streaming; mask=0 is the off switch.
        await self.session.send(protocol.set_data_streaming(
            divisor=0, samples_per_packet=0, mask=0, count=0,
            extended_mask=0, seq=self.session._next_seq()))
        self._record(Check("telemetry decoded", bool(self.session.state.telemetry),
                           str(dict(list(self.session.state.telemetry.items())[:4]))))

    async def stage_led_contention(self) -> None:
        """**Open question 3** -- can LED and ROLL interleave?

        Decides whether the iOS light show is free or must be subordinate to
        driving. Alternates the two at the drive cadence and watches for errors.
        """
        errors_before = self.session.state.traffic.errors
        try:
            for i in range(20):
                await self.session.send(
                    protocol.set_main_led((i * 12) % 256, 0, 128, seq=self.session._next_seq()))
                await asyncio.sleep(0.035)
                await self.session.send(protocol.stop(0, seq=self.session._next_seq()))
                await asyncio.sleep(0.035)
            await asyncio.sleep(0.3)
            errors = self.session.state.traffic.errors - errors_before
            self._record(Check("LED + ROLL interleaved", errors == 0,
                               "confirm the LED changed colour while responsive",
                               {"errors": errors}))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("LED + ROLL interleaved", False, str(exc)))
        finally:
            await self.session.send(protocol.set_main_led(0, 0, 0, seq=self.session._next_seq()))

    async def run_all(self) -> Report:
        stages = (
            ("identity", self.stage_identity),
            ("latency", self.stage_latency),
            ("unacknowledged", self.stage_unacknowledged),
            ("rate", self.stage_rate),
            ("sensors", self.stage_sensors),
            ("led", self.stage_led_contention),
            ("drive", self.stage_drive),
            ("safety", self.stage_safety),
        )
        for name, stage in stages:
            self.say(f"\n--- {name} ---")
            try:
                await stage()
            except Exception as exc:  # noqa: BLE001
                self._record(Check(f"stage {name}", False, f"crashed: {exc}"))
        return self.report


STAGES = ("identity", "latency", "unacknowledged", "rate", "sensors", "led", "drive", "safety")
