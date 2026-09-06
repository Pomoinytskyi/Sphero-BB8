# Phase 4 — Use Cases

Ordered so that the ones which *force architectural decisions* come first. Each
notes the design pressure it applies; §13 collects those into the constraints that
Phase 5 must answer.

Actor is always the single operator. `[CLI]` / `[iOS]` marks where each applies.

---

## UC1 — Wake a cold droid `[CLI][iOS]`

**Pre:** BB-8 asleep or in cradle. **Post:** connected, awake, motion timeout armed.

1. Operator starts the app.
2. Scan for peripherals advertising name prefix `BB-`.
3. Connect; write anti-DOS `"011i3"`; write TX power `0x07`.
4. Subscribe to the response characteristic.
5. Write `0x01` to the wake characteristic.
6. `PING` (acknowledged) → confirms the droid is actually listening.
7. `SET_MOTION_TIMEOUT(2000)` → arm the dead-man switch **before** any motion.
8. `SET_INACTIVITY_TIMEOUT`, `SET_BACK_LED`, stabilization on.

**Failure paths, all of which must be distinguishable to the operator:**
- No advertisement → droid asleep beyond BLE wake, or flat. *Not* an app bug.
- Connects, then silence → anti-DOS skipped or wrong. The classic failure.
- `PING` times out → connected but not awake.

> **Design pressure:** connection is a *state machine* with named, reportable
> states, not a boolean. The TUI Link panel shows which step failed. `PING`
> gates readiness — "connected" is not "ready".

---

## UC2 — Drive, absolute mode `[CLI][iOS]`

**Pre:** connected, aimed. **Post:** BB-8 moves as the stick directs.

1. Stick sampled at 60 Hz.
2. Radial deadzone, then expo curve, then the active speed profile's cap.
3. `heading = atan2(x, y)` in degrees — **stick direction is world direction**.
4. `speed = magnitude × profile cap`.
5. Desired `(speed, heading)` written into a **single-slot cell**, overwriting.
6. Transmitter drains that cell on its own cadence and sends one `ROLL`.
7. Stick returns to centre → `ROLL` with mode `STOP`.

> **Design pressure — the central one.** Input rate (60 Hz) and transmit rate
> (~15 Hz) differ by 4×. The single-slot overwrite is what prevents the queue
> growth that makes the notebook version rubber-band. Stale input is *dropped*,
> never buffered (N3).

---

## UC3 — Drive, tank mode `[CLI][iOS]`

Same pipeline; only step 3–4 differ: `Y` = throttle, `X` = turn rate integrated
into heading relative to current facing.

> **Design pressure:** drive model is a **strategy** behind one interface, chosen
> at runtime. Everything downstream of the mapper is identical.

---

## UC4 — Aim / calibrate `[CLI][iOS]`

**Pre:** connected. **Post:** "forward" redefined; blue tail light faces operator.

1. Operator holds the aim button.
2. Back LED to full — the visual reference.
3. Stick X now rotates the droid **in place** via `ROLL` mode `CALIBRATE`, which
   moves the heading reference without driving.
4. Operator spins until the tail light points at themselves.
5. Release → `SET_HEADING(0)`; back LED to resting; normal driving resumes.

> **Design pressure:** a modal state that re-routes stick input and suppresses
> normal drive commands. Aim and drive must never emit simultaneously — an
> explicit control mode enum, not a flag.

---

## UC5 — Switch speed profile mid-drive `[CLI][iOS]`

Operator presses the profile button while moving; cap and curve change on the next
tick without stopping. TUI Mode panel updates. Optionally the LED mood shifts.

> **Design pressure:** profile is mapper state, not transport state. Changing it
> alters the *next* command, never the packet rate.

---

## UC6 — Collision reaction `[iOS]` (capture-only `[CLI]`)

1. `CONFIGURE_COLLISION_DETECTION` at connect.
2. Droid hits something; firmware emits async `0x07`.
3. Decoder routes it to the event bus.
4. Light show reacts (flash); TUI Events panel logs it.

> **Design pressure:** async messages arrive **unsolicited and interleaved** with
> command responses on the same characteristic. The decoder must demultiplex by
> SOP2, and async handling cannot block the drive path.

---

## UC7 — Run a mood `[iOS]`

A named behaviour drives the LED continuously, optionally reacting to speed
telemetry, while the operator keeps driving normally.

> **Design pressure — the N1 budget, made concrete.** Two independent producers
> (drive + lighting) contend for one ~16 packet/sec link. Requires a **prioritised
> transmitter**: drive commands pre-empt, lighting fills gaps and is dropped under
> pressure (L5). Lighting must therefore tolerate its updates being discarded.

---

## UC8 — Protocol probe run `[CLI]` ← *primary purpose of step 1*

**Post:** a timestamped capture file plus a verification report.

1. Operator runs `bb8ctl probe`.
2. Suite walks the capability catalog: ping, versions, power, each drive command,
   LED, sensor config, collision config.
3. **Every byte in both directions is logged with timestamps.**
4. Latency and max sustainable rate measured (C6).
5. Report marks each catalog row verified / failed / unsupported.
6. Capture is promoted into Swift test vectors (C5).

> **Design pressure:** capture is a **transport-layer concern**, wrapping the
> adapter so it cannot be bypassed. Not sprinkled through call sites.

---

## UC9 — Offline replay `[CLI]` ← *makes intermittent hardware workable*

**Pre:** a capture file. **No droid required.**

1. Operator runs `bb8ctl replay <capture>`.
2. Recorded bytes are fed to the decoder; telemetry and events reconstructed.
3. TUI renders exactly as it would live.

> **Design pressure:** the decoder and TUI must be drivable by a **fake transport**.
> This is a testability requirement, and given intermittent hardware access it is
> the difference between progress and waiting.

---

## UC10 — BLE drop and reconnect `[CLI][iOS]`

Link drops mid-drive. Droid coasts, then firmware motion timeout stops it (N4).
App detects disconnect, enters `RECONNECTING`, retries with backoff, replays
connect-time configuration, resumes. Operator never restarts the app.

> **Design pressure:** all connect-time setup must be **replayable as a unit** —
> a single idempotent "configure session" step, not scattered init.

---

## UC11 — Emergency stop `[CLI][iOS]`

Panic button → immediate `ROLL` mode `STOP`, **pre-empting anything queued**,
control mode to `IDLE` until input re-centres.

> **Design pressure:** the transmitter needs a priority lane that jumps the
> single-slot cell. Confirms priority is a transport concept, matching UC7.

---

## UC12 — Graceful shutdown `[CLI][iOS]`

Ctrl-C / app background → stop the droid → flush capture → disconnect cleanly.
Must hold for SIGINT, exceptions, and TUI quit alike.

> **Design pressure:** one shutdown path, always reached. The notebook's crash on
> exit is exactly this being absent.

---

## UC13 — Low battery / sleeping soon `[CLI][iOS]`

Async `0x05` (sleeping soon) or low voltage → warn prominently, optionally
auto-stop. Distinguish "droid slept" from "app bug" — a recurring theme.

---

## §13 — Constraints extracted for Phase 5

| # | Constraint | From |
|---|---|---|
| A1 | Connection is a named state machine; `PING` gates readiness | UC1, UC10 |
| A2 | Single-slot overwrite between input and transmit; never a queue | UC2, N3 |
| A3 | Drive model is a runtime-swappable strategy | UC2, UC3 |
| A4 | Control mode is an explicit enum (drive / aim / idle / estop) | UC4, UC11 |
| A5 | Transmitter is **prioritised**: estop > drive > lighting > telemetry | UC7, UC11 |
| A6 | Lighting must tolerate dropped updates | UC7, L5 |
| A7 | Decoder demultiplexes sync vs async; async never blocks driving | UC6 |
| A8 | Capture wraps the transport, unbypassable | UC8 |
| A9 | Transport is an interface; a replay/fake implementation is first-class | UC9 |
| A10 | Connect-time config is one idempotent, replayable unit | UC10 |
| A11 | Exactly one shutdown path, reached from every exit | UC12 |
| A12 | Failures are distinguishable and reportable, never a bare "didn't work" | UC1, UC13 |
