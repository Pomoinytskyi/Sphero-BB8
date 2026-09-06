# Phase 1 — Platform Understanding

Status: **complete for host/tooling**, **source-verified for the droid** (hardware
confirmation pending — see Phase 3).

---

## 1. Host environment

| Item | Finding | Consequence |
|---|---|---|
| macOS | 26.6.2 (Darwin 25.6.0), Apple Silicon | CoreBluetooth backend for BLE |
| System Python | 3.9.6 at `/usr/bin/python3` | Too old; do not use |
| Homebrew Python | 3.14.7 | `spherov2`/`bleak` OK, **pygame will not build** (no cp314 wheels, needs SDL headers) |
| **Chosen Python** | **3.13.15** (installed this session) | Full stack installs from wheels, matches the original notebook |
| Xcode | 26.6 installed, but `xcode-select` points at CommandLineTools | `simctl`/iOS builds unavailable until `sudo xcode-select -s /Applications/Xcode.app` |
| Controller | Xbox One S, pairs over Bluetooth, seen by SDL as `"Xbox One S Controller"` | Natively supported by iOS 13+ GameController too — same hardware serves both phases |

Verified installing cleanly into `.venv` (Python 3.13.15):
`spherov2` 0.12.1 · `bleak` · `pygame` 2.6.1 (SDL 2.28.4) · `textual` · `rich`

### Radio contention
The Xbox pad and BB-8 share the host's 2.4 GHz radio. If latency looks
inexplicable, connect the pad by USB-C to eliminate it as a variable. Worth
knowing before it costs an afternoon.

---

## 2. Existing code assessment

Two scratch files, both useful as proof-of-life, neither a foundation.

- `gp.py` — standalone pygame joystick dump. Confirms controller enumeration.
- `bb8.ipynb` — the real prototype. Confirms **the two hard things work**:
  `spherov2` discovers the toy over BLE, and the pad reads correctly.

Defects to carry forward as requirements, not to patch:

1. **10 Hz blocking loop.** `time.sleep(0.1)` gates input sampling, BLE writes and
   exit checks together.
2. **Two BLE round-trips per tick.** `set_heading()` + `set_speed()` are separate
   packets when `ROLL` carries both.
3. **No write coalescing.** Commands queue faster than they drain, producing
   rubber-banding — BB-8 executes stick input from seconds ago.
4. **No aim/calibration UX.** `reset_aim()` on a button is not the original app's
   hold-spin-release calibration, and driving without it is miserable.
5. **Raw button indices.** `get_button(2)` is positional and breaks on any
   controller change.
6. **Unclean shutdown.** The notebook kernel crashed on exit — `SpheroEduAPI` runs
   its own asyncio loop and `nest_asyncio` fights it.

---

## 3. Library assessment — `spherov2`

BB-8's class hierarchy is `BB8 → Ollie → Sphero → Toy`, i.e. it is a **v1-protocol
toy** (Sphero API 1.20), not the newer v2 protocol used by Bolt/Mini/RVR.

### What spherov2 gives us
- BLE scanning and connection (`bleak` under the hood)
- The wake / anti-DOS handshake — non-obvious and easy to get wrong
- Packet framing, checksums, sequence numbers, response collection
- Sensor stream parsing with a documented mask table
- A pluggable `adapter_cls`, which is our hook for **protocol capture**

### Where it blocks us — the critical finding

Three separate mechanisms serialise the command path:

```python
# controls/v1.py — SOP2 is hardcoded to 0xFF, i.e. "answer requested"
payload = bytearray([Packet.SOP, Packet.SOP, self.did, ...])

# toy/__init__.py — every command blocks on a reply (10 s timeout)
def _execute(self, packet):
    self.__packet_queue.put(packet.build())
    return self._wait_packet(packet.id)       # <-- blocks

# toy/__init__.py — and a forced sleep after every packet
    time.sleep(self.toy_type.cmd_safe_interval)   # 0.06 s for BB-8
```

`SOP2` is not a constant in the real protocol — it is a **flags byte** where bit 0
requests an acknowledgement. spherov2 always sets it, so every packet costs a
round-trip *plus* a 60 ms sleep. With two packets per tick, the notebook's
effective ceiling is roughly **8 control updates per second**.

**Conclusion:** use `spherov2` for discovery, handshake, connection management and
sensor decoding; **bypass it on the drive hot path** with our own unacknowledged
`ROLL` packets. This is not premature optimisation — it is the difference between
the toy feeling connected to the stick or not.

Convenient consequence: the standalone encoder we need for speed in Python is
exactly the reference implementation we port to Swift in step 2. One artifact,
both phases.

---

## 4. Implication for step 2 (iOS)

Both halves are natively supported and need no third-party SDK:

- **Xbox pad** → `GameController` framework. `GCExtendedGamepad` gives named,
  normalised inputs with change handlers — strictly better than pygame's indices.
  `GCVirtualController` (iOS 15+) provides an on-screen stick through the *same*
  interface, so a touch fallback costs no extra control code.
- **BB-8** → `CoreBluetooth` as a central. No entitlement needed beyond an
  `NSBluetoothAlwaysUsageDescription` string.

The cost is that no maintained Swift Sphero SDK exists, so the protocol must be
reimplemented — which is why Phase 3 captures real byte sequences as ground truth
and test vectors. Porting against known-good captures turns a reverse-engineering
job into a transcription job.

**Deployment note:** a free Apple ID signs a build for 7 days before it must be
re-signed. A paid account ($99/yr) gets a year. Relevant if this is meant to live
on the phone permanently.
