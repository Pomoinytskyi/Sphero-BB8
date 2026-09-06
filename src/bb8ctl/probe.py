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
import statistics
import time
from dataclasses import dataclass, field

from bb8ctl import protocol, sensors
from bb8ctl.session import Session


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
            # Byte 1 is the state code; bytes 2-3 are battery centivolts.
            voltage = int.from_bytes(data[1:3], "big") / 100 if len(data) >= 3 else 0.0
            self.session.state.battery_v = voltage
            self._record(Check("power state", True, f"{voltage:.2f} V", {"volts": voltage}))
            if 0 < voltage < 7.0:
                self._record(Check("battery healthy", False,
                                   "low voltage -- charge before trusting drive tests"))
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
        """**Open question 2** -- the true inter-packet floor.

        spherov2's 0.06 s is a conservative constant, not a datasheet figure.
        Walks the interval down and watches for errors; the last clean interval
        is the real budget the whole design is sized against.
        """
        best = None
        for interval in (0.10, 0.08, 0.06, 0.05, 0.04, 0.03, 0.02):
            errors_before = self.session.state.traffic.errors
            failures = 0
            for _ in range(15):
                try:
                    await self.session.request(protocol.ping, timeout=1.5)
                except Exception:  # noqa: BLE001
                    failures += 1
                await asyncio.sleep(interval)
            decode_errors = self.session.state.traffic.errors - errors_before
            if failures or decode_errors:
                self._record(Check(f"rate @ {interval * 1000:.0f}ms", False,
                                   f"{failures} timeouts, {decode_errors} decode errors"))
                break
            self._record(Check(f"rate @ {interval * 1000:.0f}ms", True, "clean"))
            best = interval

        if best is not None:
            self._record(Check("sustainable interval", True, f"{best * 1000:.0f} ms",
                               {"hz": 1.0 / best}))

    async def stage_drive(self) -> None:
        """**Open question 6** -- ROLL modes, including calibrate.

        Uses low speeds only. The droid will move; give it clear floor.
        """
        try:
            await self.session.send(protocol.roll(40, 0, seq=self.session._next_seq()))
            await asyncio.sleep(0.8)
            await self.session.send(protocol.stop(0, seq=self.session._next_seq()))
            self._record(Check("roll + stop", True, "sent -- confirm the droid moved"))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("roll + stop", False, str(exc)))

        try:
            for heading in (0, 90, 180, 270, 0):
                await self.session.send(protocol.calibrate(heading, seq=self.session._next_seq()))
                await asyncio.sleep(0.4)
            self._record(Check("ROLL mode 2 (calibrate)", True,
                               "sent -- confirm it rotated WITHOUT driving"))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("ROLL mode 2 (calibrate)", False, str(exc)))

    async def stage_safety(self) -> None:
        """**Open question 5** -- does SET_MOTION_TIMEOUT actually fire?

        Sets a short timeout, starts rolling, then deliberately stops sending.
        A droid that keeps going has no dead-man switch, which would make N4
        unenforceable and every crash a runaway.
        """
        try:
            await self.session.send(protocol.set_motion_timeout(800, seq=self.session._next_seq()))
            await asyncio.sleep(0.1)
            await self.session.send(protocol.roll(40, 0, seq=self.session._next_seq()))
            await asyncio.sleep(2.5)  # silence -- the timeout should bite
            self._record(Check("motion timeout", True,
                               "confirm the droid stopped on its own within ~1 s"))
        except Exception as exc:  # noqa: BLE001
            self._record(Check("motion timeout", False, str(exc)))
        finally:
            await self.session.send(protocol.stop(0, seq=self.session._next_seq()))
            await self.session.send(
                protocol.set_motion_timeout(Session.MOTION_TIMEOUT_MS, seq=self.session._next_seq()))

    async def stage_sensors(self) -> None:
        """**Open question 4** -- stream rates and dropouts."""
        for interval_ms, label in ((100, "10 Hz"), (50, "20 Hz"), (25, "40 Hz")):
            self.session.state.telemetry.clear()
            before = self.session.state.traffic.received
            primary, extended = sensors.build_masks(sensors.DRIVE_PRESET)
            self.session._masks = (primary, extended)
            await self.session.send(protocol.set_data_streaming(
                interval_ms=interval_ms, samples_per_packet=1, mask=primary,
                count=0, extended_mask=extended, seq=self.session._next_seq()))
            await asyncio.sleep(3.0)
            received = self.session.state.traffic.received - before
            expected = 3.0 * (1000 / interval_ms)
            ratio = received / expected if expected else 0.0
            self._record(Check(f"sensor stream {label}", ratio > 0.7,
                               f"{received} frames of ~{expected:.0f}",
                               {"delivered_pct": ratio * 100}))

        # Stop streaming; mask=0 is the off switch.
        await self.session.send(protocol.set_data_streaming(
            interval_ms=0, samples_per_packet=0, mask=0, count=0,
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
