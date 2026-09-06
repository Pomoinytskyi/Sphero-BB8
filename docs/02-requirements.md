# Phase 2 — Requirements

Single stakeholder (owner-operator), personal project, no external users.
Delivered in two steps: a **Python CLI** (throwaway instrumentation) then an
**iOS app** (the product).

---

## 1. Goals

**G1** — Restore full control of a BB-8 whose official app is dead.
**G2** — Produce a *verified, documented* BB-8 Bluetooth protocol spec, complete
enough to reimplement from scratch in Swift without a live reverse-engineering
session.
**G3** — Ship an iOS app that drives BB-8 with an Xbox controller (and an on-screen
fallback), with expressive lighting as the signature feature.

## 2. Non-goals

- Multi-droid support, or toys other than BB-8
- App Store distribution — personal signing only
- Android, web, or a Mac GUI
- Autonomous navigation / closed-loop position control (locator drift makes this a
  research project, explicitly deferred)
- Preserving the Python CLI as a long-term product

---

## 3. Step 1 — Python CLI (`bb8ctl`)

**Role: protocol lab.** Its output is knowledge and test vectors, not a product.
Judged on how much it de-risks the Swift port, not on how nice it is to use.

### Functional

| ID | Requirement | Priority |
|---|---|---|
| C1 | Discover, connect, handshake and wake a BB-8 | Must |
| C2 | Log **every** byte in both directions, timestamped, to a replayable file | Must |
| C3 | Drive from an Xbox controller well enough to generate realistic traffic | Must |
| C4 | Scriptable probe suite exercising each capability in the catalog, recording results | Must |
| C5 | Emit Swift-ready test vectors (input → expected bytes) from captures | Must |
| C6 | Measure and report command latency and the real max sustainable rate | Must |
| C7 | Decode and log sensor streams and async messages (collision, sleep, battery) | Must |
| C8 | Both drive models (absolute + tank), switchable, to validate both feel | Should |
| C9 | Aim/calibration mode via `ROLL` mode 2 | Should |
| C10 | **Live TUI instrument panel** — see §4.1 | Must |
| C11 | In-memory telemetry bus feeding the TUI; session export to file | Should |
| C12 | Macro record & replay | Won't (moved to iOS) |
| C13 | **Speed profiles** — tortoise / rabbit, switchable while driving | Must |

### 4.1 The TUI is an instrument, not a dashboard

A throwaway tool does not earn a dashboard — but it very much earns an
**oscilloscope**. The TUI's purpose is observing protocol behaviour in real time,
which is how requirements C6 (measure latency and max rate) and C7 (decode streams)
are actually satisfied. Without it, those measurements happen through log
archaeology after the fact.

Designed for diagnosis, not for looking good. Panels:

| Panel | Shows | Serves |
|---|---|---|
| Link | Connection state, RSSI, reconnect count, uptime | C1, N6 |
| **Traffic** | Packets/sec sent, ack latency, queue depth, **drops** | **C6, N1, N3** |
| Wire | Rolling hex dump of recent packets, both directions | C2, C5 |
| Telemetry | Speed, heading, locator X/Y, IMU, battery | C7 |
| Events | Async log — collisions, sleep warnings, errors | C7 |
| Input | Live stick/button state, post-curve values | C3, C8 |
| Mode | Drive model, speed profile, aim state | C8, C9, C13 |

The Traffic panel is the most important thing on screen: it is where the §6 N1
command budget is either confirmed or refuted, live, while driving.

---

## 4. Step 2 — iOS app

### Functional

| ID | Requirement | Priority |
|---|---|---|
| I1 | Connect to BB-8 over CoreBluetooth; auto-reconnect on drop | Must |
| I2 | Xbox controller input via GameController framework | Must |
| I3 | On-screen `GCVirtualController` fallback when no pad is present | Must |
| I4 | Both drive models, switchable in-app | Must |
| I5 | Aim/calibration mode (hold → spin → release) | Must |
| I6 | **Expressive lighting** — see §5 | Must |
| I7 | Live status: connection, battery, speed, heading, active mode | Should |
| I8 | Session recording + trajectory visualisation | Could |
| I9 | Macro record & replay bound to controller buttons | Could |

---

## 5. Expressive lighting (the signature feature)

**L1** — Reactive lighting: main LED responds continuously to speed.
**L2** — Collision response: distinct visual reaction to async collision events.
**L3** — Named "moods" — scripted colour/motion behaviours, selectable.
**L4** — Idle behaviour: ambient animation when parked.
**L5** — Lighting must **never** degrade driving responsiveness. Drive commands win
every contention. This is a hard constraint, not a preference.

---

## 6. Non-functional

### N1 — Command budget (the governing constraint)

This is the tightest limit in the system and every design decision answers to it.

```
Firmware floor           ~60 ms between packets   →  ~16 packets/sec TOTAL
Drive loop, responsive    12-15 Hz                →   12-15 packets/sec
Reactive lighting          5-10 Hz                →    5-10 packets/sec
                                                     ----------------------
                          Required 17-25/s  vs.  Available ~16/s   OVER BUDGET
```

**Driving and reactive lighting cannot both run naively.** Mitigations, in order of
preference, all to be settled by hardware measurement in Phase 3:

1. Measure the *actual* floor — 60 ms is spherov2's conservative constant, not a
   datasheet value. Unacknowledged packets may sustain a higher rate.
2. Send LED updates only on **material** colour change, not every frame.
3. Strict priority: drive commands pre-empt; lighting fills idle gaps.
4. Drop the drive rate when the stick is still — a parked droid needs no `ROLL`.

**This makes the SOP2=0xFE verification the single most valuable experiment in
Phase 3.** If unacknowledged packets sustain a materially higher rate, the budget
problem dissolves. If not, lighting must be strictly subordinate.

### Others

| ID | Requirement |
|---|---|
| N2 | Stick-to-motion latency under ~150 ms, consistently — no growing lag |
| N3 | **No unbounded command queue.** Stale drive commands are dropped, never buffered |
| N4 | `SET_MOTION_TIMEOUT` armed at all times — firmware stops the droid if the app dies |
| N5 | Clean shutdown always stops the droid before disconnecting |
| N6 | Reconnect automatically on BLE drop without restarting the app |
| N7 | Two speed profiles, switchable **while driving** without stopping (see below) |
| N8 | Protocol layer has zero dependency on BLE library or platform, so it ports as-is |

### N7 — Speed profiles

BB-8's speed byte is 0-255. Above ~120 it is genuinely unmanageable indoors: it
reaches a wall faster than a human reacts. Rather than hide this in a config cap,
expose it as two named modes on a controller button, clearly indicated in the TUI.

| Profile | Speed cap | Response curve | Rotation rate | Use |
|---|---|---|---|---|
| **Tortoise** | ~90 | Softer expo, gentler ramp | Low — smooth aiming | Default. Indoors, near furniture, aiming |
| **Rabbit** | 255 | Sharper, more direct | High | Open floor, corridors, outdoors |

Both are `SpeedProfile` values, so a third ("creep" for tight spaces) costs nothing
later. The profile is a property of the **control mapper**, not the transport — it
shapes the command before encoding, never the packet rate.

Ties into the light show (§5): profile is a natural input to the LED mood, so the
droid can *look* calm in tortoise and aggressive in rabbit.

---

## 7. Constraints & assumptions

- **Hardware access is intermittent** — BB-8 is charged but not always available.
  Everything must be developable and testable offline, with hardware runs batched.
  Drives a strong requirement for a **replay/simulation mode** (C2 captures replayed
  against the decoder without a droid present).
- Xcode 26.6 present but `xcode-select` misconfigured; iOS work is blocked until fixed.
- Free Apple signing expires every 7 days. Decide on the $99 account before step 2.
- BB-8 firmware version is unknown until Phase 3 — capability rows may not all hold.

## 8. Open questions for Phase 3

1. Does BB-8 honour `SOP2=0xFE` (unacknowledged)? **Highest value.**
2. What is the true minimum inter-packet interval?
3. Can `SET_MAIN_LED` and `ROLL` interleave without either being dropped?
4. What is the maximum reliable sensor-stream rate before dropouts?
5. Does `SET_MOTION_TIMEOUT` actually fire, and how promptly?
6. What does `ROLL` mode 2 (calibrate) do to the heading reference in practice?
