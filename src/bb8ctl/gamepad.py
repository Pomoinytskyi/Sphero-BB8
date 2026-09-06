"""Xbox controller input via SDL's GameController layer.

Uses ``pygame._sdl2.controller`` rather than the raw joystick API deliberately.
Raw joystick reads address inputs positionally -- ``get_button(2)`` -- which
silently means something different on a different pad, or after a firmware
update. SDL's GameController layer maps through its controller database and
gives named inputs, so bindings stay meaningful.

This also matches step 2: iOS's GameController framework presents the same
named-input model, so bindings port conceptually rather than being re-derived.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# SDL wants a video driver even when only reading input. Set before pygame init.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402
from pygame._sdl2 import controller as sdl_controller  # noqa: E402

#: SDL's stick range.
_AXIS_MAX = 32767.0

BUTTONS: dict[str, int] = {
    "a": pygame.CONTROLLER_BUTTON_A,
    "b": pygame.CONTROLLER_BUTTON_B,
    "x": pygame.CONTROLLER_BUTTON_X,
    "y": pygame.CONTROLLER_BUTTON_Y,
    "lb": pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
    "rb": pygame.CONTROLLER_BUTTON_RIGHTSHOULDER,
    "back": pygame.CONTROLLER_BUTTON_BACK,
    "start": pygame.CONTROLLER_BUTTON_START,
    "ls": pygame.CONTROLLER_BUTTON_LEFTSTICK,
    "rs": pygame.CONTROLLER_BUTTON_RIGHTSTICK,
    "dpad_up": pygame.CONTROLLER_BUTTON_DPAD_UP,
    "dpad_down": pygame.CONTROLLER_BUTTON_DPAD_DOWN,
    "dpad_left": pygame.CONTROLLER_BUTTON_DPAD_LEFT,
    "dpad_right": pygame.CONTROLLER_BUTTON_DPAD_RIGHT,
}

#: Action bindings. Aim is a *hold* (a modal state, UC4); the rest are edges.
DEFAULT_BINDINGS: dict[str, str] = {
    "aim": "rb",             # hold to enter aim mode
    "estop": "b",            # panic stop
    "toggle_drive_mode": "x",
    "toggle_profile": "y",
    "reset_aim": "a",
    "quit": "back",
}


@dataclass
class InputState:
    """One sample. ``ly``/``ry`` are already flipped so positive is *forward*."""

    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    lt: float = 0.0
    rt: float = 0.0
    buttons: dict[str, bool] = field(default_factory=dict)
    connected: bool = False

    def pressed(self, action: str, bindings: dict[str, str] | None = None) -> bool:
        binding = (bindings or DEFAULT_BINDINGS).get(action)
        return bool(binding and self.buttons.get(binding))


class Gamepad:
    """Polls the first attached controller.

    Polling rather than events: at 60 Hz we want the *current* stick position,
    not a backlog of past ones. An event queue here would reintroduce exactly
    the staleness the single-slot drive cell exists to prevent.
    """

    def __init__(self) -> None:
        self._controller: sdl_controller.Controller | None = None
        self._previous: dict[str, bool] = {}
        self.name: str | None = None

    def open(self) -> bool:
        pygame.init()
        sdl_controller.init()
        for index in range(sdl_controller.get_count()):
            if sdl_controller.is_controller(index):
                self._controller = sdl_controller.Controller(index)
                self._controller.init()
                self.name = self._controller.name
                return True
        return False

    def read(self) -> InputState:
        if self._controller is None:
            return InputState()
        pygame.event.pump()
        get = self._controller.get_axis
        return InputState(
            lx=get(pygame.CONTROLLER_AXIS_LEFTX) / _AXIS_MAX,
            # SDL reports Y positive downward; flip so positive is away-from-you,
            # which is what the drive models expect.
            ly=-get(pygame.CONTROLLER_AXIS_LEFTY) / _AXIS_MAX,
            rx=get(pygame.CONTROLLER_AXIS_RIGHTX) / _AXIS_MAX,
            ry=-get(pygame.CONTROLLER_AXIS_RIGHTY) / _AXIS_MAX,
            lt=get(pygame.CONTROLLER_AXIS_TRIGGERLEFT) / _AXIS_MAX,
            rt=get(pygame.CONTROLLER_AXIS_TRIGGERRIGHT) / _AXIS_MAX,
            buttons={n: bool(self._controller.get_button(c)) for n, c in BUTTONS.items()},
            connected=True,
        )

    def edges(self, state: InputState) -> set[str]:
        """Buttons that went down since the last call.

        Mode toggles must fire once per press, not once per 60 Hz frame -- level
        triggering would flip the drive mode 60 times a second while held.
        """
        down = {n for n, v in state.buttons.items() if v and not self._previous.get(n)}
        self._previous = dict(state.buttons)
        return down

    def close(self) -> None:
        if self._controller is not None:
            self._controller.quit()
            self._controller = None
        sdl_controller.quit()
        pygame.quit()


class ScriptedGamepad:
    """Test double: replays a list of :class:`InputState` samples."""

    def __init__(self, samples: list[InputState]) -> None:
        self.samples = samples
        self.index = 0
        self._previous: dict[str, bool] = {}
        self.name = "scripted"

    def open(self) -> bool:
        return True

    def read(self) -> InputState:
        if not self.samples:
            return InputState(connected=True)
        sample = self.samples[min(self.index, len(self.samples) - 1)]
        self.index += 1
        return sample

    edges = Gamepad.edges
    def close(self) -> None:
        return None
