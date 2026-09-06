# Phase 5 — High-Level Design

Answers constraints A1–A12 from [Phase 4](04-use-cases.md). Traceability in §7.

---

## 1. Architecture decisions

### AD1 — asyncio throughout; no worker threads
Both ends of the stack are already async: `bleak` is asyncio-native, and Textual
runs its own event loop. Threads would mean bridging that boundary twice.

`pygame` joystick reads are the only synchronous piece, and they are *non-blocking*
(`event.pump()` + `get_axis()` are microseconds), so they poll inside a plain async
task at 60 Hz. No executor needed.

**Consequence:** one event loop, no locks. Contention becomes ordering, which is far
easier to reason about — and to observe in the TUI.

### AD2 — Own the whole BLE stack; spherov2 becomes reference material
`spherov2`'s `Toy` is thread-based with blocking queues and a synchronous
`_execute()` that waits on every packet. It contradicts AD1, it owns the
reconnection we decided to own, and its `SOP2=0xFF` default is the rate ceiling we
are trying to escape.

We build on `bleak` directly with our own codec.

**The decisive argument is step 2.** Swift will have no spherov2 either — it gets a
raw BLE client, a hand-written codec and hand-written reconnection. Building the CLI
the same way means step 1 exercises *the architecture step 2 actually ships*.
Anything spherov2 hides is something we would discover for the first time in Swift.

*Hedge:* keep spherov2 as a **dev dependency only**, as a cross-check oracle — if
our handshake fails on hardware, we can A/B against a known-good implementation on
the same droid in the same session. Cheap insurance, zero runtime coupling.

### AD3 — Capture emits at the Transport seam, writes asynchronously
> *This is the "better place" — it is neither the BLE client nor the codec.*

Two requirements pull in opposite directions: capture must be **unbypassable** (A8)
yet must **never stall the drive loop** (N2). A decorator around the BLE client is
unbypassable but puts file I/O in the hot path. A hook in the codec is cheap but
bypassable and misses framing errors.

Splitting emission from writing satisfies both:

- **Emission** sits in `Transport`, which every byte necessarily crosses. Each TX
  and RX frame is published to a `wire` bus — an in-memory, non-blocking put.
  Unbypassable by construction: there is no path to the radio that skips it.
- **Writing** is an ordinary async subscriber draining that bus to disk in batches.
  If it falls behind it **counts drops rather than applying backpressure**. Capture
  fidelity is sacrificed before driving quality, never the reverse.

Three further wins: it composes with `ReplayTransport` (A9) since the seam is the
interface, not an implementation; it records the *raw* bytes Swift needs, before any
interpretation; and the decoder is just another subscriber to the same bus, so
capture and decode are independent — malformed frames are captured even when
decoding throws.

### AD4 — Prioritised transmitter over a single-slot drive cell
One component owns the radio and enforces the §6 N1 budget. Three lanes:

| Lane | Discipline | Contents |
|---|---|---|
| **Immediate** | jumps everything | E-stop (UC11) |
| **Drive** | **single slot, overwritten** | latest desired `ROLL` (UC2, A2) |
| **Background** | bounded queue, droppable | lighting, telemetry config, keepalive |

Each tick: immediate first; then the drive slot if dirty; then one background item
if budget remains. Lighting is structurally incapable of delaying driving, which is
how L5 is enforced by construction rather than by discipline.

The drive slot is a **cell, not a queue** — the whole point. New input overwrites
un-sent input, so latency cannot accumulate (A2, N3).

### AD5 — Adaptive rate governor
The 60 ms floor is spherov2's constant, not a measured fact. The governor starts
conservative, and Phase 3 measurement tunes it. It exposes live packets/sec, ack
latency and drop counts to the TUI Traffic panel — making the budget question
observable while driving, which is the TUI's reason to exist.

### AD6 — Explicit connection and control state machines
Connection: `DISCONNECTED → SCANNING → CONNECTING → HANDSHAKING → WAKING → READY`,
plus `DEGRADED` and `RECONNECTING`. `PING` gates `READY` — connected is not ready
(A1). Every transition is published, so the TUI shows exactly which step failed (A12).

Control mode: `IDLE | DRIVING | AIMING | ESTOP`. Aiming re-routes stick input and
suppresses drive output, so the two can never emit at once (A4).

---

## 2. Layers

```
┌──────────────────────────────────────────────────────────┐
│  TUI (Textual)          ← subscribes; never commands      │
├──────────────────────────────────────────────────────────┤
│  Session orchestration                                    │
│    ControlMode SM · connect-time config (idempotent, A10) │
├────────────────────────────┬─────────────────────────────┤
│  Input            60 Hz    │  Behaviours          ~5 Hz   │
│   gamepad → curve → mapper │   lighting / moods           │
│   (drive model strategy)   │   (droppable)                │
├────────────────────────────┴─────────────────────────────┤
│  Transmitter               ~15 Hz, adaptive               │
│    immediate │ drive cell │ background · rate governor    │
├──────────────────────────────────────────────────────────┤
│  Protocol codec        PURE · no I/O · ports to Swift     │
│    encoders · Decoder (sync/async demux)                  │
├──────────────────────────────────────────────────────────┤
│  Transport (interface)          → publishes `wire` bus    │
│    BleTransport (bleak) │ ReplayTransport (file)          │
└──────────────────────────────────────────────────────────┘
        buses:  wire · telemetry · events · status
```

**Dependency rule:** every arrow points down. The codec knows nothing of transports;
the transport knows nothing of driving. The TUI is a pure consumer — it renders
buses and never issues commands, so it cannot perturb what it measures.

## 3. Async task inventory

| Task | Rate | Responsibility |
|---|---|---|
| `gamepad_poll` | 60 Hz | Sample stick/buttons → `InputState` |
| `control_tick` | 60 Hz | Curve → mapper → write drive cell |
| `transmitter` | ~15 Hz adaptive | Drain lanes, encode, hand to transport |
| `notify_pump` | event-driven | BLE notify → `Decoder` → telemetry/events |
| `capture_writer` | batched | Drain `wire` → JSONL; count drops |
| `link_supervisor` | 1 Hz | Watchdog, keepalive ping, reconnect (A10) |
| `lighting` | ~5 Hz | Mood → background lane |
| TUI | ~10 Hz | Render (Textual-owned) |

Shutdown (A11): one `TaskGroup`. Cancellation propagates; a single `finally` stops
the droid, flushes capture, disconnects. Every exit — SIGINT, exception, TUI quit —
funnels through it.

## 4. Module layout

```
src/bb8ctl/
  protocol/        # PURE. The Swift port target.
    codec.py         encoders + Decoder + constants
    sensors.py       stream mask tables, sample decoding
  transport/
    base.py          Transport interface + wire bus emission
    ble.py           bleak implementation
    replay.py        file-backed fake (A9)
    capture.py       async writer, drop-counting
  control/
    curve.py         deadzone, expo
    models.py        absolute / tank strategies (A3)
    profiles.py      tortoise / rabbit (C13)
    mapper.py        InputState → DriveCommand
  input/gamepad.py
  link/
    session.py       connection SM, handshake, reconnect (A1, A10)
    transmitter.py   lanes + rate governor (AD4, AD5)
  behaviours/lighting.py
  tui/app.py
  bus.py             tiny async pub/sub
  cli.py             drive · probe · replay · capture-export
```

## 5. Interfaces that matter

```python
class Transport(Protocol):
    async def connect(self) -> None: ...
    async def send(self, data: bytes) -> None:      # publishes to wire bus
    def notifications(self) -> AsyncIterator[bytes]: ...
    async def close(self) -> None: ...

class DriveModel(Protocol):
    def map(self, inp: InputState, prof: SpeedProfile) -> DriveCommand: ...
```

Two seams carry the whole design: `Transport` makes replay and capture possible;
`DriveModel` makes tank-vs-absolute a runtime choice.

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Hand-rolled handshake fails on hardware | Blocks everything | spherov2 kept as A/B oracle (AD2 hedge) |
| `SOP2=0xFE` unsupported | Light show budget collapses | Governor is adaptive; lighting already droppable |
| 60 ms floor is real and firm | Feature contention | Priority lanes already assume scarcity |
| Textual + 60 Hz polling contend | Input jitter | TUI is pure consumer, decoupled by buses |
| Locator drift worse than expected | Trajectory viz weak | Already a `Could`; not on the critical path |

## 7. Traceability

| Constraint | Satisfied by |
|---|---|
| A1 state machine, PING gates ready | AD6 · `link/session.py` |
| A2 single-slot cell | AD4 drive lane |
| A3 swappable drive model | `DriveModel` protocol |
| A4 explicit control mode | AD6 control SM |
| A5 prioritised transmitter | AD4 three lanes |
| A6 lighting tolerates drops | AD4 background lane |
| A7 async/sync demux, non-blocking | `Decoder` + `notify_pump` |
| A8 capture unbypassable | AD3 emission in `Transport` |
| A9 fake transport first-class | `Transport` interface + `replay.py` |
| A10 idempotent connect config | AD6 · session configure step |
| A11 one shutdown path | §3 `TaskGroup` + `finally` |
| A12 distinguishable failures | AD6 published transitions |
| N1 command budget | AD4 lanes + AD5 governor |
| N8 portable protocol layer | `protocol/` has no I/O imports |

---

## 8. Simplification pass

Reviewed for over-engineering before implementation. **AD1 (asyncio) stands** — a
synchronous design would need a background loop thread for `bleak` plus cross-thread
queues for Textual, which is more machinery, not less, and is precisely the
thread-bridging that crashed the original notebook.

The overhead was in the structure around it. Cut:

### S1 — Priority lanes: three → one cell + a queue
**The three-lane transmitter was designed for a feature step 1 does not have.**
Lighting and moods are `[iOS]` (requirement I6); the CLI only needs to *verify* LED
and drive interleaving inside `probe`. With one real-time producer there is nothing
to prioritise.

Now: a single-slot drive cell, plus an `asyncio.Queue` for one-off commands (setup,
probe steps). E-stop writes `STOP` into the cell and marks it urgent — no lane
needed. Priority returns in step 2, when a second producer actually exists.

### S2 — Capture: async writer → buffered append
The emission point in `Transport` was right and stays (unbypassable, composes with
replay, captures raw bytes, keeps the decoder independent). The **async writer with
drop-counting was not.** Appending to a buffered file object costs microseconds; it
was never a hot-path risk. Write directly at the seam, flush periodically.

### S3 — Four buses → one shared state object
Single event loop means no preemption between `await` points, so plain shared
mutable state is safe without locks — one of asyncio's real payoffs. A single
`State` dataclass that tasks mutate and the TUI reads replaces the pub/sub layer.

The `wire` bus stays as a genuine stream (capture + decode both consume it), but it
is one `asyncio.Queue`, not a bus abstraction.

### S4 — Also trimmed
- `DriveModel` Protocol classes → one `map()` function switching on a mode enum
- Adaptive rate governor → **fixed interval constant**, CLI-tunable. Measure in
  Phase 3 first; adapt only if measurement says to
- 7-state connection SM → a linear `connect()` coroutine with retry. Named states
  are kept for diagnosis (A12 is real) but need no framework
- 6 packages → ~8 flat modules
- `link_supervisor` + `capture_writer` tasks fold away

### Resulting shape

| | Before | After |
|---|---|---|
| Async tasks | 8 | **4** — input+control, transmit, notify, TUI |
| Buses | 4 | **1 queue + 1 state object** |
| Modules | 15 across 6 packages | **8 flat** |
| Est. lines | ~1800 | **~950** |

Constraints A1–A12 all still hold; §7 traceability is unchanged except A5, which
narrows to "urgent flag on the drive cell" until step 2 reintroduces a second
producer. The cuts removed *machinery*, not *properties*.
