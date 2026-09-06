# Phase 3 — BB-8 Bluetooth Capability Catalog

**Source-derived** from `spherov2` 0.12.1 (`commands/sphero.py`, `commands/core.py`,
`toy/sphero.py`, `controls/v1.py`) cross-read against the Sphero API 1.20 framing.

> ✅ **Hardware-verified 2026-09-06** against BB-D36B (firmware `011e010445480006`,
> 8.07 V, 31 charge cycles). Capture: `captures/probe-20260906-101816.jsonl`,
> 427 frames. Results in §10; corrections folded into the tables below.

---

## 1. Transport

| | UUID |
|---|---|
| BLE service | `22bb746f-2bb0-7554-2d6f-726568705327` |
| ├ Wake | `22bb746f-2bbf-7554-2d6f-726568705327` ← write `01` |
| ├ TX power | `22bb746f-2bb2-7554-2d6f-726568705327` ← write `07` |
| └ Anti-DOS | `22bb746f-2bbd-7554-2d6f-726568705327` ← write `"011i3"` |
| Robot control service | `22bb746f-2ba0-7554-2d6f-726568705327` |
| ├ Command (write) | `22bb746f-2ba1-7554-2d6f-726568705327` |
| └ Response (notify) | `22bb746f-2ba6-7554-2d6f-726568705327` |

**Connect sequence:** connect → write anti-DOS → write TX power → subscribe to
response → write wake. Skipping anti-DOS leaves the toy connected but permanently
mute. Advertised name prefix is `BB-`.

**Packet frame:** `FF <SOP2> <DID> <CID> <SEQ> <DLEN> <data…> <CHK>`
where `DLEN = len(data)+1` and `CHK = 0xFF − (sum(DID…data) & 0xFF)`.

`SOP2` is a **flags byte**, not a constant — this matters a lot:

| Value | Meaning |
|---|---|
| `0xFF` | acknowledge + reset inactivity timer |
| `0xFE` | **no acknowledgement** + reset timer ← use for the drive loop |
| `0xFD` | acknowledge, don't reset timer |
| `0xFC` | fire-and-forget, don't reset timer |

Writes chunk to 20 bytes. Firmware drops packets arriving faster than ~60 ms apart.

---

## 2. Driving — `DID 0x02`

| Cmd | CID | Payload | State | Notes |
|---|---|---|---|---|
| `ROLL` | `0x30` | `speed(1) heading(2) mode(1) rsv(1)` | SRC | **The workhorse.** Speed and heading in one packet |
| `SET_HEADING` | `0x01` | `heading(2)` | SRC | Declares current facing = N°. `0` = reset aim |
| `SET_STABILIZATION` | `0x02` | `bool(1)` | SRC | Off = free-spinning, no IMU hold |
| `SET_ROTATION_RATE` | `0x03` | `rate(1)` | SRC | Units of 0.784 °/s. Low = smooth aiming |
| `SET_RAW_MOTORS` | `0x33` | `lmode lspd rmode rspd` | SRC | Bypasses stabilisation entirely |
| `SET_MOTION_TIMEOUT` | `0x34` | `ms(2)` | SRC | **Dead-man switch** — auto-stop if commands stop arriving |
| `BOOST` | `0x31` | `on(1) heading(2)` | SRC | The app's "turbo" |
| `SELF_LEVEL` | `0x09` | opts | SRC | Head-alignment routine |

`ROLL` mode: `0`=stop, `1`=go, **`2`=calibrate** — rotates the heading reference
*without* driving the motors. This is precisely how aim mode is implemented.

Speed is 0–255 but anything above ~120 is unmanageable indoors.

## 3. Lights — `DID 0x02`

| Cmd | CID | Payload | State |
|---|---|---|---|
| `SET_MAIN_LED` | `0x20` | `r g b` | SRC |
| `SET_BACK_LED` | `0x21` | `brightness(1)` | SRC |

The back LED is the blue tail light — it marks the **back** of the droid and is the
visual reference for aiming.

## 4. Power & system — `DID 0x00`

| Cmd | CID | State | Notes |
|---|---|---|---|
| `PING` | `0x01` | SRC | Cheapest keepalive / liveness probe |
| `GET_VERSIONS` | `0x02` | SRC | Firmware identification |
| `GET_POWER_STATE` | `0x20` | SRC | Battery voltage + charge state |
| `ENABLE_BATTERY_NOTIFY` | `0x21` | SRC | Push battery changes |
| `SLEEP` | `0x22` | SRC | Explicit sleep |
| `SET_INACTIVITY_TIMEOUT` | `0x25` | SRC | Default 600 s before auto-sleep |
| `GET_CHARGER_STATE` | `0x38` | SRC | In-cradle detection |

---

## 5. Sensors — streaming

`SET_DATA_STREAMING` (`0x11`) with
`divisor(2) samples(2) mask(4) count(1) ext_mask(4)`.

> **The first word is a DIVISOR of a 400 Hz clock, not a period in milliseconds.**
> Rate = `400 / divisor`. Measured: divisor 100 → 4.01 Hz, 50 → 8.01 Hz,
> 25 → 15.96 Hz — all within 0.25%. Reading it as milliseconds understates the
> rate by 25x. `count=0` streams indefinitely, `mask=0` stops.

### Primary mask

| Field | Bits | Range | Scale |
|---|---|---|---|
| Attitude pitch / roll / yaw | `0x40000` `0x20000` `0x10000` | −179…180 | degrees |
| Accelerometer X/Y/Z | `0x8000` `0x4000` `0x2000` | ±32768 | ÷4096 → g |
| Gyroscope X/Y/Z | `0x1000` `0x800` `0x400` | ±20000 | ×0.1 → °/s |
| Back-EMF left / right | `0x40` `0x20` | ±32768 | raw |

### Extended mask

| Field | Bits | Scale |
|---|---|---|
| Quaternion X/Y/Z/W | `0x80000000`…`0x10000000` | ÷10000 |
| **Locator X / Y** | `0x8000000` `0x4000000` | centimetres |
| Accel one | `0x2000000` | — |
| Velocity X / Y | `0x1000000` `0x800000` | ×0.1 → cm/s |
| Speed | `0x400000` | cm/s |

**Locator caveat:** dead reckoning from a ball that slips. Expect meaningful drift
over a minute of driving. Good enough for trajectory *visualisation*, not for
closed-loop position control without correction.

## 6. Async messages (unsolicited)

Frame: `FF FE <ID> <DLEN_MSB> <DLEN_LSB> <data…> <CHK>` — note the **2-byte length**,
unlike synchronous responses.

| ID | Meaning |
|---|---|
| `0x01` | Power/battery notification |
| `0x03` | **Sensor stream data** |
| `0x05` | Sleeping soon ← reconnect/keepalive trigger |
| `0x07` | **Collision detected** |
| `0x0C` | Gyro axis limit exceeded |
| `0x0E` | Did sleep |

`CONFIGURE_COLLISION_DETECTION` (`0x12`):
`method x_thresh x_speed y_thresh y_speed dead_time/10`.

---

## 7. Not available on BB-8

- **Head rotation.** BB-8's head is held by magnets and follows passively — there
  is no head motor. (R2-D2 has motorised head/leg control; BB-8 does not.)
- **Sphero v2 protocol** commands — BB-8 predates it.
- Matrix/LED-array commands (Bolt only).

---

## 8. Outstanding hardware verification

Needs the droid awake. Proposed `bb8ctl probe` run, capturing every byte:

1. Handshake + wake → confirm connection and notify subscription
2. `PING` → confirm round-trip and measure latency
3. `GET_VERSIONS`, `GET_POWER_STATE` → firmware + battery baseline
4. **`SOP2=0xFE` unacknowledged `ROLL`** → the key assumption. Confirm the droid
   acts on it and stays silent
5. Command-rate sweep → find the real floor below the assumed 60 ms
6. `ROLL` mode 2 (calibrate) → confirm it rotates heading without driving
7. `SET_MOTION_TIMEOUT` → confirm the dead-man stop actually fires
8. Sensor streaming at 10/20/40 Hz → confirm decode, measure jitter and dropouts
9. Collision detection → confirm async `0x07` arrives on impact

Output: `captures/*.jsonl` byte logs, promoted into Swift test vectors for step 2.

---

## 9. Host-side blocker found during implementation

**macOS silently kills processes that use CoreBluetooth without permission.**

A process touching CoreBluetooth with no `NSBluetoothAlwaysUsageDescription` in its
Info.plist is terminated with `SIGABRT` (exit 134) — no traceback, no stderr, no
crash report, no TCC prompt. A bare Python interpreter has no such plist.

Measured on this machine: `CBManager.authorization()` returns `0` (notDetermined),
and `BleakScanner.discover()` aborts instantly.

Note that **constructing a `CBCentralManager` does not trip it** — only starting a
scan does. A preflight that merely builds a manager reports success and the real
command dies moments later; `bb8ctl.preflight` therefore runs an actual short scan,
in a subprocess so the abort cannot take the parent with it.

**Fix:** run from Terminal.app or iTerm2 (permission belongs to the *responsible
application*, not to Python), and grant Bluetooth when prompted, or enable it under
System Settings → Privacy & Security → Bluetooth.

This matters for step 2 as well: an iOS app must declare
`NSBluetoothAlwaysUsageDescription` in its Info.plist or it will be killed the same
way, with the same absence of diagnostics.


---

## 10. Hardware verification results (2026-09-06)

Droid `BB-D36B`, firmware `011e010445480006`, 8.07 V, 31 charge cycles.
Run: `bb8ctl probe` → `captures/probe-20260906-101816.jsonl` (427 frames).

### Answers to the Phase 2 §8 open questions

| # | Question | Answer |
|---|---|---|
| 1 | Does BB-8 honour `SOP2=0xFE`? | **YES.** 50 unacknowledged packets sent, **0 replies.** |
| 2 | True inter-packet floor? | **Not yet answered** — the first rate stage measured the wrong thing (see below). Fastest spacing observed: **36.9 ms**. |
| 3 | Can `SET_MAIN_LED` and `ROLL` interleave? | **YES.** 21 LED packets alternating with ROLL, **0 errors.** |
| 4 | Max reliable stream rate? | **≥16 Hz with zero loss.** Limited by divisor, not by the link. |
| 5 | Does `SET_MOTION_TIMEOUT` fire? | Sent and accepted; droid stopped unattended. |
| 6 | What does `ROLL` mode 2 do? | Accepted; rotates heading reference. |

### Verified constants

- `GET_POWER_STATE` payload: `[0]` record version, `[1]` power state
  (1=charging, 2=OK, 3=low, 4=critical), `[2:4]` **centivolts**, `[4:6]` charge
  count, `[6:8]` seconds since charge.
- Sensor sample clock: **400 Hz**, parameter is a divisor.
- `DRIVE_PRESET` decodes correctly: 6 int16 fields, 12-byte payload, `dlen=13`.

### Measured performance

| Metric | Value |
|---|---|
| Acknowledged round-trip latency | min 66.8 ms, **median 69 ms**, max 120 ms |
| Achieved acknowledged rate | **8.3 Hz** |
| Fastest TX spacing observed | 36.9 ms |
| Sensor stream loss | **0%** at 4, 8 and 16 Hz |

The 8.3 Hz acknowledged rate confirms the Phase 1 prediction almost exactly: an
acknowledge-everything design like spherov2's caps out near 8 commands/sec.

### Three probe defects found by this run

1. **Battery parsed one byte early** — sliced `[1:3]` instead of `[2:4]`, reporting
   5.15 V for an 8.07 V pack and flagging a healthy battery as flat.
2. **Sensor expectations assumed milliseconds** — computed `1000/param` instead of
   `400/param`, so a stream with *zero* loss was reported at 40% delivery. The
   consistent ~41% across all three rates was the clue: congestion is not that tidy.
3. **The rate stage measured the wrong quantity** — it slept the target interval
   *after* a blocking acknowledged round-trip, so achieved spacing was always
   (latency + interval). It never exceeded ~11 Hz, then reported "20 ms sustainable,
   50 Hz" as though the target had been met.

All three are fixed; the rate stage now measures achieved unacknowledged throughput.

### Outstanding: BLE write mode

`0xFE` removes the *application*-layer acknowledgement, but the run used BLE
**write-with-response**, which still costs a link-layer round trip per write —
the likely cause of the ~37-60 ms floor. `bb8ctl probe --no-response` uses
write-without-response and should be the next measurement. If it lifts throughput
materially, the §6 N1 command budget stops being a constraint at all.
