# Controls Reference

Xbox One S controller → BB-8. Everything here is read from the implementation
([`gamepad.py`](../src/bb8ctl/gamepad.py), [`control.py`](../src/bb8ctl/control.py),
[`app.py`](../src/bb8ctl/app.py)), not from intent.

---

## 1. Button map

| Control | Action | Type | What it does |
|---|---|---|---|
| **Left stick** | Drive | analog | Meaning depends on drive mode — see §2 |
| **RB** | **Aim** | **hold** | Modal. Stick rotates the heading reference *without driving* |
| **A** | Reset aim | press | Current facing becomes 0° / forward |
| **B** | **E-stop** | press | Immediate stop, latches until the stick recentres |
| **X** | Drive mode | press | Absolute ⇄ Tank |
| **Y** | Speed profile | press | Tortoise ⇄ Rabbit |
| **Back** | Quit | press | Stops the droid, flushes capture, disconnects |

**Hold vs press matters.** Aim is level-triggered — held down, it's a mode. The
rest fire once on the press edge; level-triggering a toggle would flip it 60
times a second while your thumb rested on it.

Bindings are indirect, not positional. Rebinding touches `DEFAULT_BINDINGS`
only, never loop logic — the fix for the original notebook's `get_button(2)`.

### Unbound and available

`LB` · `Start` · `LS` · `RS` · `D-pad ×4` · **right stick** · **both triggers**

Fourteen inputs spare. The triggers are analog, which makes them the natural
home for a light-show intensity control in step 2.

---

## 2. Drive modes — `X` toggles

### Absolute (default)

**Stick direction is world direction.** Push the stick where you want the droid
to go; it goes there, regardless of which way it was previously facing.

| Stick | Heading sent |
|---|---|
| Up | 0° |
| Up-right | 45° |
| Right | 90° |
| Down | 180° |
| Left | 270° |

Uses `atan2(x, y)` — **x first, deliberately**. That yields 0° for "pushed away",
matching BB-8's clockwise-from-forward convention. The conventional `atan2(y, x)`
gives a counterclockwise-from-east frame and drives at 90° to your stick.

**Requires aiming first**, and is meaningless until you do — "forward" is
whatever direction aim last established, not a property of the droid.

Feels like a top-down video game. Better once aimed; better for chasing things.

### Tank (= relative)

**Y is throttle, X is turn rate, both relative to BB-8's current facing.**

- Push forward → droid goes wherever its nose points
- Push left → droid *turns* left, it does not translate left
- Pull back → speed on the same axis, heading flipped 180°

Turn rate is integrated over `dt`, so steering feel is frame-rate independent —
a faster control loop must not steer faster.

Steering with **no** throttle still updates the heading. Without that, releasing
the throttle mid-turn snaps the droid back to its previous bearing.

Needs no aiming. Feels like an RC car. Better in tight spaces.

> The two words are the same thing here: **tank *is* the relative mode.**

---

## 3. Speed profiles — `Y` toggles

BB-8's speed byte is 0–255, but above roughly 120 it is unmanageable indoors —
it reaches a wall faster than a person reacts. Hence two named modes rather than
a buried config cap.

| | Tortoise (default) | Rabbit |
|---|---|---|
| Speed cap | **90** | **255** |
| Expo curve | 2.0 (soft) | 1.4 (sharp) |
| Turn rate | 120 °/s | 220 °/s |
| Deadzone | 0.12 | 0.12 |

### Same stick, very different droid

| Stick deflection | Tortoise speed | Rabbit speed |
|---|---|---|
| 12% | 0 (deadzone) | 0 (deadzone) |
| 20% | 1 | 9 |
| 35% | 6 | 39 |
| 50% | **17** | **79** |
| 70% | 39 | 142 |
| 85% | 62 | 196 |
| 100% | 90 | 255 |

At half stick, rabbit is **4.6×** faster — the cap and the curve compound.
Switchable mid-drive; it applies from the next control tick, no stop needed.

---

## 4. Control modes — internal state

Only one is ever active, so aiming and driving can never emit at once.

```
        ┌──────── stick centred ────────┐
        ↓                               │
     ┌──────┐  stick moved      ┌────────────┐
     │ IDLE │ ───────────────→  │  DRIVING   │
     └──────┘                   └────────────┘
        ↑ ↓ hold RB                   │ press B
        │ └──────→ ┌─────────┐        ↓
        │          │ AIMING  │   ┌─────────┐
        └──────────└─────────┘   │  ESTOP  │
           release RB            └─────────┘
                                      │ stick recentres
        └─────────────────────────────┘
```

- **IDLE** — parked. Redundant stop packets are suppressed, freeing the link.
- **DRIVING** — normal. `ROLL` mode 1 (GO).
- **AIMING** — `ROLL` mode 2 (CALIBRATE). Speed forced to 0; drive output
  suppressed entirely, so full forward stick while aiming still will not move it.
- **ESTOP** — **latches.** Releasing B does not resume driving; the stick must
  return to centre first, so you can't accidentally lurch off after a panic stop.

---

## 5. Aiming — the one that matters

Absolute mode is unusable without it, and it's the closest thing to the original
app's calibration.

1. **Hold RB.** Back LED brightens — that's the blue tail light, marking the
   droid's *back*.
2. **Push the stick left/right.** The droid rotates **in place**. This is a
   firmware feature (`ROLL` mode 2), not a trick: it moves the heading reference
   without engaging drive.
3. **Spin until the tail light points at you.** Now "away from the light" is away
   from you.
4. **Release.** `SET_HEADING(0)` — that facing becomes forward.

Aim rotation runs at **half** the profile's turn rate (60 °/s tortoise,
110 °/s rabbit). Aiming is precision work; full rate overshoots.

**A** does the same thing without the spin — it just declares the current facing
to be forward. Useful when the droid is already pointing the right way.

---

## 6. Signal path

Every 1/60 s, per stick sample:

```
raw stick  (-1..+1, Y inverted by SDL)
   ↓  flip Y so positive = away from you
   ↓  RADIAL deadzone 0.12, rescaled to full range
   ↓     radial, not per-axis: a square gate lets one axis creep
   ↓     while the other is suppressed -- reads as the droid drifting
   ↓  circular clamp (square-gated hardware reports 1.41 on diagonals)
   ↓  expo curve  magnitude ** profile.expo
   ↓  × profile.cap
   ↓
DriveCommand(speed, heading, kind)
   ↓  written into the SINGLE-SLOT CELL -- overwrites, never queues
   ↓
transmitter @ 30 Hz  →  ROLL packet  →  BLE (unacknowledged)
```

Input is sampled at 60 Hz but sent at 30 Hz. The surplus is not waste: it keeps
the cell holding a *current* desired state, so whichever tick fires next sends
where the stick is **now**. In a 47-second session that discarded 894 superseded
commands against 387 sent — the discards are the design working.

---

## 7. Rebinding

```python
from bb8ctl.gamepad import DEFAULT_BINDINGS
bindings = dict(DEFAULT_BINDINGS, toggle_profile="lb", aim="lt")
```

Valid names: `a b x y lb rb back start ls rs dpad_up dpad_down dpad_left dpad_right`

Names come from SDL's GameController database, so they mean the same thing on a
PlayStation or generic pad — and the same model carries to iOS, whose
GameController framework presents named inputs too.

---

## 8. Hardware verification status

| Path | Verified |
|---|---|
| Absolute drive | ✅ 358 commands, 237 headings, peak 105 cm/s |
| E-stop | ✅ twice |
| Profile toggle | ✅ switched to rabbit mid-session |
| Quit / clean shutdown | ✅ |
| **Tank mode** | ❌ **never exercised** |
| **Aim mode** | ❌ **never exercised** — 0 calibrate packets |
| Reset aim (A) | ❌ not observed |

The two untested paths are related: absolute mode only works *after* aiming, so
if aim is broken, absolute feels broken and tank is the fallback. Both should be
exercised in the next session.
