# bb8ctl

Drive a Sphero BB-8 with an Xbox controller, and document its Bluetooth protocol
well enough to reimplement in Swift.

The official BB-8 app is gone. This is step 1 of two: a Python CLI that drives the
droid *and* captures every byte it exchanges, so step 2 (a native iOS app) is a
transcription rather than a reverse-engineering project.

## Status

**Step 1 complete. Step 2 builds and runs in the Simulator.**

The iOS app (`ios/`) ports the codec to Swift and validates it against the same
golden vectors — **44 Swift tests prove the two implementations emit identical
bytes.** See [docs/07-ios-app.md](docs/07-ios-app.md). Not yet run on a physical
device.

### Step 1 Verified on hardware 2026-09-06 against BB-D36B: driven with
an Xbox controller for 47 s, 358 drive commands across 237 distinct headings, peak
105 cm/s, ~2.2 m travelled, **zero errors**. 183 tests, none requiring the droid.

| Component | State |
|---|---|
| Protocol codec + sensor decoding | Done, golden-vector tested |
| Transport, capture, replay | Done |
| Session lifecycle, reconnect-ready config | Done |
| Control math, drive models, speed profiles | Done |
| Transmitter (single-slot cell) | Done |
| Gamepad, control loop, TUI | Done |
| Probe suite | Done, **hardware-verified** |
| Aim mode (`ROLL` mode 2) | Implemented, **not yet exercised on hardware** |

### What the hardware told us

| Measurement | Result |
|---|---|
| `SOP2=0xFE` unacknowledged | **Honoured** — 50 packets, 0 replies |
| BLE write-without-response | **89.2 pkt/s** ceiling |
| BLE write-with-response | 16.7 pkt/s (59.8 ms) |
| Acknowledged round-trip | median 69 ms |
| Sensor streaming | 0% loss at 4/8/16 Hz |
| Sensor rate parameter | **400 Hz divisor**, not milliseconds |

spherov2's 60 ms "firmware safe interval" turns out to be the BLE
write-with-response round trip, not a firmware limit — the with-response ceiling
measures 59.8 ms, that constant almost exactly. Switching to write-without-response
is a 5.3x throughput gain, which makes the planned iOS light show free rather than
something that has to compete with driving.

## Quick start

```bash
python3.13 -m venv .venv && ./.venv/bin/pip install -e '.[dev]'
```

```bash
./.venv/bin/python -m pytest -q
```

Everything above runs with no droid and no controller.

### With hardware

> **Run these from Terminal.app or iTerm2, not an embedded terminal.** macOS kills
> any process that touches CoreBluetooth without a Bluetooth usage description —
> silently, with exit code 134 and no error. `bb8ctl` detects this and tells you
> how to fix it, but the permission has to be granted to a real terminal app.

```bash
./.venv/bin/python -m bb8ctl.cli scan
```

```bash
./.venv/bin/python -m bb8ctl.cli probe
```

```bash
./.venv/bin/python -m bb8ctl.cli drive --telemetry
```

### Without hardware

Replay a recorded session through the real decoder and TUI:

```bash
./.venv/bin/python -m bb8ctl.cli replay captures/demo-session.jsonl
```

Export golden vectors for the Swift port:

```bash
./.venv/bin/python -m bb8ctl.cli vectors
```

## Controls

| Input | Action |
|---|---|
| Left stick | Drive |
| **RB (hold)** | Aim — stick rotates the heading reference without driving |
| A | Reset aim — current facing becomes forward |
| B | Emergency stop (latches until the stick recentres) |
| X | Toggle drive model — absolute ↔ tank |
| Y | Toggle speed profile — tortoise ↔ rabbit |
| Back | Quit |

**Tortoise** caps speed at 90 with a softer curve; **rabbit** unlocks the full 255.
Above roughly 120 BB-8 is unmanageable indoors — it reaches a wall faster than you
react — so tortoise is the default.

## How it works

```
gamepad 60Hz ─→ control mapper ─→ [single-slot cell] ─→ transmitter ~15Hz ─→ BLE
                                        ↑ overwrites                    │
                                   never queues                    wire capture
                                                                        ↓
                    TUI ←── shared state ←── decoder ←── notifications ─┘
```

Input is sampled four times faster than the radio can carry it. The gap is bridged
by **overwriting a single slot, never queueing** — so latency cannot accumulate and
the droid always acts on where the stick is *now*. Queuing here is what makes the
naive version rubber-band.

Capture sits at the transport seam, so no path to the radio can skip it, and bytes
are recorded *before* interpretation — meaning frames that would crash a decoder are
still captured. That is exactly the case you want when debugging the Swift port.

## Design notes

Written up in order, in [`docs/`](docs/):

1. [Platform understanding](docs/01-platform-understanding.md)
2. [Requirements](docs/02-requirements.md)
3. [BB-8 capability catalog](docs/03-capability-catalog.md) — the protocol reference
4. [Use cases](docs/04-use-cases.md)
5. [High-level design](docs/05-high-level-design.md)
6. [Implementation plan](docs/06-implementation-plan.md)

Two decisions carry most of the architecture:

- **spherov2 is not a runtime dependency.** It is thread-based and blocking, it
  acknowledges every packet (capping throughput near 8 commands/sec), and it hides
  the connection lifecycle. Swift will have none of it either — so building directly
  on `bleak` means step 1 exercises the architecture step 2 actually ships. It stays
  as a dev-only A/B oracle in case our handshake misbehaves on hardware.
- **`protocol.py` and `sensors.py` import nothing but stdlib.** Enforced by a test.
  They are the Swift port target.

## Licence

Personal project. Sphero and BB-8 are trademarks of their respective owners.
