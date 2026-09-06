"""Command-line entry points.

``scan``   -- find advertising droids
``probe``  -- hardware verification suite (the point of step 1)
``drive``  -- controller driving with the TUI instrument
``replay`` -- re-render a capture with no droid present
``vectors``-- export Swift test vectors from the codec
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from bb8ctl import preflight
from bb8ctl import probe as probe_module
from bb8ctl import protocol, sensors
from bb8ctl.session import Session
from bb8ctl.state import State
from bb8ctl.transmit import DEFAULT_INTERVAL, Transmitter
from bb8ctl.transport import BleTransport, CaptureWriter, ReplayTransport

CAPTURE_DIR = Path("captures")


def _capture_path(kind: str) -> Path:
    return CAPTURE_DIR / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"


async def _resolve_address(explicit: str | None) -> str | None:
    from bb8ctl.transport import scan as ble_scan

    if not preflight.require_bluetooth():
        return None
    if explicit:
        return explicit
    print("scanning for BB-8 ...")
    found = await ble_scan()
    if not found:
        print("no BB-8 advertising. Wake it by putting it on and off the charger.")
        return None
    name, address = found[0]
    print(f"found {name} at {address}")
    return address


# -- commands -------------------------------------------------------------

async def cmd_scan(args: argparse.Namespace) -> int:
    from bb8ctl.transport import scan as ble_scan

    if not preflight.require_bluetooth():
        return 2
    found = await ble_scan(timeout=args.timeout)
    if not found:
        print("no BB-8 found. Is it awake? Roll it, or seat it on the charger briefly.")
        return 1
    for name, address in found:
        print(f"{name}\t{address}")
    return 0


async def cmd_probe(args: argparse.Namespace) -> int:
    address = await _resolve_address(args.address)
    if address is None:
        return 1

    capture_path = _capture_path("probe")
    with CaptureWriter(capture_path) as capture:
        state = State()
        state.droid_address = address
        transport = BleTransport(address, capture=capture, write_response=not args.no_response)
        session = Session(transport, state)
        try:
            await session.connect()
        except Exception as exc:  # noqa: BLE001
            print(f"\nconnect failed in state {state.link.value}: {exc}")
            print("If this persists, A/B against spherov2 before debugging our code.")
            await session.shutdown()
            return 1

        prober = probe_module.Prober(session)
        try:
            if args.stage:
                await getattr(prober, f"stage_{args.stage}")()
            else:
                await prober.run_all()
        finally:
            await session.shutdown()

    print("\n" + prober.report.render())
    print(f"\ncapture: {capture_path}  ({capture.count} frames)")
    return 0 if prober.report.failed == 0 else 1


async def cmd_drive(args: argparse.Namespace) -> int:
    from bb8ctl.app import DriveApp
    from bb8ctl.gamepad import Gamepad
    from bb8ctl.tui import Dashboard

    pad = Gamepad()
    if not pad.open():
        print("no controller found. Pair the Xbox pad and try again.")
        return 1
    print(f"controller: {pad.name}")

    address = await _resolve_address(args.address)
    if address is None:
        return 1

    capture_path = _capture_path("drive")
    with CaptureWriter(capture_path) as capture:
        state = State()
        state.droid_address, state.droid_name = address, "BB-8"
        session = Session(BleTransport(address, capture=capture), state)
        try:
            await session.connect()
        except Exception as exc:  # noqa: BLE001
            print(f"connect failed in state {state.link.value}: {exc}")
            await session.shutdown()
            return 1

        await session.configure(stream=sensors.DRIVE_PRESET if args.telemetry else None)
        transmitter = Transmitter(session, state, interval=args.interval)
        drive_app = DriveApp(session, state, pad, transmitter)

        if args.no_tui:
            await drive_app.run()
        else:
            await _drive_with_dashboard(drive_app, Dashboard(state))
        pad.close()

    print(f"capture: {capture_path}")
    return 0


async def _drive_with_dashboard(drive_app, dashboard) -> None:
    """Run driving and the TUI together, with either able to end the session.

    Both directions matter: quitting the TUI must stop the droid, and quitting
    from the controller must close the TUI rather than leaving a dead panel on
    screen. Whichever finishes first tears down the other, so there is still
    exactly one shutdown path (A11).
    """
    async def drive() -> None:
        try:
            await drive_app.run()
        finally:
            dashboard.exit()

    async def show() -> None:
        try:
            await dashboard.run_async()
        finally:
            drive_app.quit_requested = True

    await asyncio.gather(drive(), show())


async def cmd_replay(args: argparse.Namespace) -> int:
    """Render a recorded session with no droid attached (UC9)."""
    from bb8ctl.tui import Dashboard

    state = State()
    state.droid_name = f"replay:{Path(args.capture).name}"
    session = Session(ReplayTransport(args.capture, speed=args.speed), state)
    await session.transport.connect()
    await session.transport.start_notifications()
    session._masks = sensors.build_masks(sensors.DRIVE_PRESET)

    pump = asyncio.create_task(session._pump())
    if args.headless:
        await pump
        print(f"replayed {state.traffic.received} frames; "
              f"{len(state.events)} events; telemetry keys: {sorted(state.telemetry)}")
    else:
        from bb8ctl.state import LinkState
        state.link = LinkState.READY
        await Dashboard(state).run_async()
        pump.cancel()
    return 0


def cmd_vectors(args: argparse.Namespace) -> int:
    """Export golden vectors for the Swift port (requirement C5)."""
    import json

    cases = [
        ("roll_100_90", protocol.roll(100, 90, seq=5)),
        ("roll_full_0", protocol.roll(255, 0, seq=0)),
        ("stop", protocol.stop(seq=6)),
        ("calibrate_180", protocol.calibrate(180, seq=1)),
        ("set_heading_0", protocol.set_heading(0, seq=7)),
        ("main_led_red", protocol.set_main_led(255, 0, 0, seq=8)),
        ("back_led_full", protocol.set_back_led(255, seq=2)),
        ("motion_timeout_2000", protocol.set_motion_timeout(2000, seq=3)),
        ("stabilization_on", protocol.set_stabilization(True, seq=4)),
        ("ping", protocol.ping(seq=9)),
        ("power_state", protocol.get_power_state(seq=10)),
    ]
    primary, extended = sensors.build_masks(sensors.DRIVE_PRESET)
    payload = {
        "note": "Golden vectors for the BB-8 v1 codec. The Swift port must "
                "reproduce these byte-for-byte.",
        "handshake": [{"uuid": u, "hex": d.hex()} for u, d in protocol.HANDSHAKE],
        "characteristics": {
            "wake": protocol.CHAR_WAKE, "tx_power": protocol.CHAR_TX_POWER,
            "anti_dos": protocol.CHAR_ANTI_DOS, "command": protocol.CHAR_COMMAND,
            "response": protocol.CHAR_RESPONSE,
        },
        "packets": {name: packet.hex() for name, packet in cases},
        "sensor_masks": {"drive_preset": list(sensors.DRIVE_PRESET),
                         "primary": primary, "extended": extended,
                         "field_order": [f.name for f in sensors.layout(primary, extended)]},
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {len(cases)} packet vectors to {out}")
    return 0


# -- wiring ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bb8ctl", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="find advertising BB-8s")
    p.add_argument("--timeout", type=float, default=6.0)
    p.set_defaults(func=cmd_scan, is_async=True)

    p = sub.add_parser("probe", help="run the hardware verification suite")
    p.add_argument("--address")
    p.add_argument("--stage", choices=probe_module.STAGES,
                   help="run a single stage instead of all")
    p.add_argument("--no-response", action="store_true",
                   help="use BLE write-without-response (faster; a rate experiment)")
    p.set_defaults(func=cmd_probe, is_async=True)

    p = sub.add_parser("drive", help="drive with a controller")
    p.add_argument("--address")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                   help="seconds between packets")
    p.add_argument("--telemetry", action="store_true", help="enable sensor streaming")
    p.add_argument("--no-tui", action="store_true")
    p.set_defaults(func=cmd_drive, is_async=True)

    p = sub.add_parser("replay", help="re-render a capture without a droid")
    p.add_argument("capture")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_replay, is_async=True)

    p = sub.add_parser("vectors", help="export golden vectors for the Swift port")
    p.add_argument("--output", default="tests/vectors/bb8_vectors.json")
    p.set_defaults(func=cmd_vectors, is_async=False)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.is_async:
            return asyncio.run(args.func(args))
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
