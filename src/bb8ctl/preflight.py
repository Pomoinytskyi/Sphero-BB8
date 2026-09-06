"""macOS Bluetooth permission preflight.

On macOS, a process that touches CoreBluetooth without an
``NSBluetoothAlwaysUsageDescription`` in its Info.plist is **killed with
SIGABRT** rather than prompted. A bare Python interpreter has no such plist, so
``bleak`` dies instantly with no traceback, no stderr and no crash report --
just exit code 134.

That failure is indistinguishable from a bug in our own code, which is exactly
the confusion constraint A12 exists to prevent. So we detect it deliberately, in
a throwaway subprocess, and turn it into an instruction.

The permission belongs to the *responsible* application -- the terminal hosting
the interpreter -- not to Python itself. A terminal that has never prompted
leaves authorization at ``notDetermined``, and a process that cannot show a
prompt simply dies.
"""

from __future__ import annotations

import subprocess
import sys
from enum import IntEnum

#: Signal 6. Subprocess return codes are negated when a signal kills the child.
_SIGABRT = -6

#: Must actually *start a scan*. Merely constructing a CBCentralManager does
#: not trip the permission abort -- discovered the hard way -- so a weaker probe
#: reports success and the real command dies moments later.
_CHECK_SCRIPT = (
    "import asyncio, bleak;"
    "asyncio.run(bleak.BleakScanner.discover(timeout=0.4));"
    "print('ok')"
)


class Authorization(IntEnum):
    """Mirrors ``CBManagerAuthorization``."""

    NOT_DETERMINED = 0
    RESTRICTED = 1
    DENIED = 2
    ALLOWED = 3


GUIDANCE = """\
macOS has not granted Bluetooth access to the process running bb8ctl.

CoreBluetooth aborts rather than prompting when the host application has no
Bluetooth usage description, which is why this looks like a silent crash.

To fix it, run bb8ctl from a real terminal application:

    1. Open Terminal.app (or iTerm2) directly -- not an embedded terminal.
    2. Run the command again. macOS should prompt for Bluetooth access.
    3. If no prompt appears, enable it manually:
       System Settings -> Privacy & Security -> Bluetooth -> enable your terminal.

The permission belongs to the terminal application, not to Python, so granting
it once covers every later run from that terminal."""


def authorization() -> Authorization | None:
    """Current CoreBluetooth authorization, or ``None`` off macOS."""
    if sys.platform != "darwin":
        return None
    try:
        from CoreBluetooth import CBManager

        return Authorization(CBManager.authorization())
    except Exception:  # noqa: BLE001
        return None


def bluetooth_available(timeout: float = 10.0) -> tuple[bool, str]:
    """Probe CoreBluetooth in a subprocess so an abort cannot take us with it.

    Returns ``(ok, message)``. This is the whole point of the module: the check
    must survive the failure it is checking for, which an in-process attempt
    cannot do.
    """
    if sys.platform != "darwin":
        return True, ""

    state = authorization()
    if state is Authorization.DENIED:
        return False, GUIDANCE
    if state is Authorization.RESTRICTED:
        return False, "Bluetooth access is restricted by policy on this Mac."
    if state is Authorization.ALLOWED:
        return True, ""     # already granted; skip the subprocess round-trip

    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHECK_SCRIPT],
            capture_output=True, timeout=timeout, text=True,
        )
    except subprocess.TimeoutExpired:
        return False, "Timed out probing CoreBluetooth. Is Bluetooth switched on?"
    except OSError as exc:
        return False, f"Could not probe CoreBluetooth: {exc}"

    if result.returncode == _SIGABRT:
        return False, GUIDANCE
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return False, "CoreBluetooth unavailable: " + (detail[-1] if detail else
                                                       f"exit {result.returncode}")
    return True, ""


def require_bluetooth() -> bool:
    """Print guidance and return False when Bluetooth is unusable."""
    ok, message = bluetooth_available()
    if not ok:
        print(message, file=sys.stderr)
    return ok
