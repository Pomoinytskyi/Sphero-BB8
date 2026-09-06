# Phase 6 — Low-Level Implementation Plan

Reflects the §8 simplification pass: 8 flat modules, 4 async tasks, ~950 lines.

---

## 1. Build order

Ordered by **testability without hardware**, because BB-8 is only intermittently
available. Everything through M5 is fully verifiable with no droid; when it *is*
available, the capture tooling already exists and no session is wasted.

| # | Module | Lines | Needs droid? | Unlocks |
|---|---|---|---|---|
| M1 | `protocol.py` | ~280 | No — pure | Golden vectors; the step-2 deliverable |
| M2 | `transport.py` | ~170 | No — replay | Capture + offline integration |
| M3 | `session.py` | ~130 | Verify only | Connect, handshake, reconnect |
| M4 | `probe.py` | ~120 | **Yes, to run** | Phase 3 hardware answers |
| M5 | `gamepad.py` + `control.py` | ~200 | No — pad only | Drive feel, tunable dry |
| M6 | `transmit.py` | ~90 | Verify only | The drive loop |
| M7 | `tui.py` | ~220 | No — replay | The instrument |
| M8 | `cli.py` + wiring | ~110 | — | `drive` / `probe` / `replay` |

M1–M3 and M5 are written and tested before the droid is ever needed. M4 is the
first thing to run the moment it's awake.

---

## 2. Module specifications

### M1 `protocol.py` — pure codec
No imports beyond stdlib. This is the file that becomes Swift.

```python
def build(did, cid, seq=0, data=b"", *, answer=False) -> bytes
def checksum(payload: bytes) -> int
def roll(speed, heading, mode=RollMode.GO, seq=0) -> bytes
def calibrate(heading, seq=0) -> bytes          # ROLL mode 2 — aim
def set_heading(heading, seq=0) -> bytes
def set_main_led(r, g, b, seq=0) -> bytes
def set_motion_timeout(ms, seq=0) -> bytes      # dead-man switch
def set_data_streaming(...) -> bytes
class Decoder:  def feed(self, data: bytes) -> Iterator[Response | AsyncMessage]
```

**Test:** golden vectors — every encoder against a hand-computed hex string;
`Decoder` fed fragmented, concatenated, and corrupt input. 100% offline.
*Draft already exists from Phase 1 research; needs review, sensor decoding, tests.*

### M2 `transport.py` — the seam
```python
class Transport(Protocol):
    async def connect(self); async def send(self, data: bytes)
    def notifications(self) -> AsyncIterator[bytes]; async def close(self)

class BleTransport:     # bleak
class ReplayTransport:  # reads a capture file, replays with original timing
class CaptureWriter:    # buffered JSONL append at the seam (AD3, S2)
```
Every `send`/notify writes `{t, dir, hex}` to the capture file **before** dispatch.

**Test:** round-trip — capture a `ReplayTransport` session, replay it, assert
byte-identical. Offline.

### M3 `session.py` — connection lifecycle
```python
class LinkState(Enum): DISCONNECTED SCANNING CONNECTING HANDSHAKING WAKING READY DEGRADED RECONNECTING

class Session:
    async def connect(self)      # scan → connect → anti-DOS → tx power → subscribe → wake → PING
    async def configure(self)    # idempotent; replayed verbatim on reconnect (A10)
    async def run(self)          # watchdog + reconnect with backoff
    async def shutdown(self)     # STOP → flush → disconnect (A11)
```
`configure()` sets motion timeout, inactivity timeout, stabilization, back LED —
one unit, so reconnect is a replay, not a re-derivation.

**Test:** fake transport asserting exact handshake byte order; simulated mid-session
drop asserting `configure()` replays. Offline.

### M4 `probe.py` — the Phase 3 instrument
Walks the capability catalog, recording result and latency per command, and answers
the six open questions from Phase 2 §8. Emits `docs/03-capability-catalog.md` status
updates and `captures/probe-<ts>.jsonl`.

Ordered so the **cheapest, most diagnostic** checks run first — if `PING` fails
there is no point continuing.

### M5 `gamepad.py` + `control.py`
```python
# gamepad.py — SDL GameController layer, named buttons not indices
@dataclass class InputState: lx ly rx ry: float; buttons: dict[str, bool]

# control.py
def apply_curve(x, y, profile) -> tuple[float, float]   # radial deadzone + expo
class DriveMode(Enum): ABSOLUTE, TANK
class SpeedProfile: cap: int; expo: float; rotation_rate: int
TORTOISE = SpeedProfile(cap=90,  expo=2.0, rotation_rate=90)
RABBIT   = SpeedProfile(cap=255, expo=1.4, rotation_rate=180)
def map_input(inp, mode, profile, heading) -> DriveCommand
```
**Test:** pure functions — deadzone boundaries, expo monotonicity, cap enforcement,
`atan2` quadrants (the classic sign-error site). Offline, no droid, no pad.

### M6 `transmit.py` — drive cell + queue (S1)
```python
class Transmitter:
    def set_drive(self, cmd: DriveCommand, urgent=False)   # OVERWRITES (A2)
    async def enqueue(self, packet: bytes)                 # one-offs
    async def run(self)   # every INTERVAL: urgent → dirty cell → one queued
```
`INTERVAL` is a module constant (default 0.07 s), CLI-overridable. Phase 3 measures
the real floor.

**Test:** drive the cell at 60 Hz against a fake transport for 5 s; assert sent
count ≈ `5/INTERVAL` and that the **last** command sent matches the last set — i.e.
staleness is dropped, not buffered.

### M7 `tui.py` — the instrument
Textual app, pure consumer of `State`. Panels per requirements §4.1: Link, Traffic,
Wire, Telemetry, Events, Input, Mode. **Traffic is the primary panel.**

**Test:** run against `ReplayTransport` with a recorded capture — full TUI
verification with no droid.

### M8 `cli.py`
`bb8ctl scan | probe | drive | replay <file> | export-vectors <file>`

---

## 3. Test strategy

| Layer | Kind | Hardware |
|---|---|---|
| `protocol` | Golden byte vectors, fuzz the decoder | No |
| `control` | Property tests on pure functions | No |
| `transport` | Capture/replay round-trip | No |
| `session` | Fake transport, handshake order, reconnect | No |
| `transmit` | Rate + staleness assertions | No |
| `tui` | Replay a recorded capture | No |
| **end-to-end** | **`probe` suite** | **Yes** |

**~90% of the test suite runs with no droid.** That is a direct consequence of the
`Transport` seam (A9) and is what makes intermittent hardware workable.

`pytest` + `pytest-asyncio`. Golden vectors live in `tests/vectors/` and are
**exported for step 2** — the Swift codec is validated against the same file, so
both implementations are provably identical.

---

## 4. Milestones

| M | Done when |
|---|---|
| **1** | `pytest` green on codec + control; vectors exported. No droid touched. |
| **2** | `bb8ctl replay` renders a synthetic capture in the TUI. Still no droid. |
| **3** | `bb8ctl probe` runs end-to-end on hardware; catalog rows flip SRC → verified; the six open questions answered. |
| **4** | `bb8ctl drive` — controller drives BB-8, both modes, both profiles, aim works, clean shutdown. |
| **5** | Capture corpus + Swift-ready vectors committed. **Step 1 complete.** |

Milestones 1–2 need nothing but a laptop. Milestone 3 is one focused hardware
session. Only milestone 4 needs iterative droid access.

---

## 5. Hardware session checklist

For when BB-8 is next available — ordered to get irreversible answers first.

1. `bb8ctl scan` — does it advertise? Note name + address.
2. `bb8ctl probe --stage=connect` — handshake, wake, `PING`. **If this fails, A/B
   against spherov2 immediately** (AD2 hedge) before debugging our code.
3. `probe --stage=identity` — versions, battery baseline.
4. `probe --stage=rate` — **the SOP2=0xFE question and the true interval floor.**
   The single most valuable measurement; everything in §6 N1 depends on it.
5. `probe --stage=drive` — roll, stop, calibrate. Confirm mode 2 rotates without driving.
6. `probe --stage=safety` — confirm `SET_MOTION_TIMEOUT` actually fires.
7. `probe --stage=sensors` — streaming at 10/20/40 Hz; measure dropouts.
8. `probe --stage=collision` — confirm async `0x07` on impact.
9. `probe --stage=led` — **LED and ROLL interleaved**, which decides whether the
   iOS light show is free or must be subordinate.

Everything captured. One session should answer all of Phase 2 §8.
