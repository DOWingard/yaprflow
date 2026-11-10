"""Hold-to-talk chord read straight from the kernel's input devices (evdev).

Reading /dev/input works the same under any Wayland compositor or X11, and unlike desktop shortcut
APIs it can see a modifier-only chord such as Ctrl+Super. The keys are observed, never grabbed, so
they still reach the focused application. Reading requires membership of the `input` group.
"""

import asyncio
import glob
import os
import threading
from collections.abc import Callable

import evdev
from evdev import ecodes

from . import log

_log = log.get("hotkey")

RESCAN_S = 3.0  # how often to look for newly plugged keyboards

_SIDES = {
    "ctrl": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "control": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
    "alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "super": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "meta": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "win": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "cmd": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
}
MODIFIER_CODES = frozenset(ecodes.ecodes[n] for names in _SIDES.values() for n in names)


class HotkeyError(Exception):
    pass


def _code(name: str) -> int:
    key = name if name.upper().startswith("KEY_") else f"KEY_{name}"
    code = ecodes.ecodes.get(key.upper())
    if code is None:
        raise HotkeyError(f"unknown key {name!r}; use a modifier ({', '.join(sorted(_SIDES))}) or an evdev "
                          "key name such as f9, space or KEY_RIGHTALT")
    return code


def parse_chord(spec: str) -> tuple[frozenset[int], ...]:
    """'ctrl+super' -> one group per part; a group is satisfied when any key in it is held."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise HotkeyError("empty key chord")
    return tuple(frozenset(ecodes.ecodes[n] for n in _SIDES[p]) if p in _SIDES else frozenset([_code(p)])
                 for p in parts)


class ChordTracker:
    """Turns key events into press/release/cancel of a held chord.

    The chord fires only when it is held on its own, and pressing any other key while it is held
    cancels it, so shortcuts that extend the chord (Ctrl+Super+Left, …) never start a recording.
    Key state is kept per device so that unplugging one keyboard cannot leave keys stuck down.
    """

    def __init__(self, groups: tuple[frozenset[int], ...], on_press: Callable[[], None],
                 on_release: Callable[[], None], on_cancel: Callable[[], None]) -> None:
        self._groups = groups
        self._chord_codes = frozenset().union(*groups)
        self._on_press, self._on_release, self._on_cancel = on_press, on_release, on_cancel
        self._held: dict[str, set[int]] = {}
        self._active = False
        self._spent = False  # cancelled; stays inert until the chord is fully let go
        self._lock = threading.Lock()

    def _all_held(self) -> set[int]:
        return set().union(*self._held.values()) if self._held else set()

    def _satisfied(self, held: set[int]) -> bool:
        return all(held & g for g in self._groups)

    def modifiers_held(self) -> bool:
        with self._lock:
            return bool(self._all_held() & MODIFIER_CODES)

    def key(self, source: str, code: int, value: int) -> None:
        if value == 2:  # autorepeat
            return
        with self._lock:
            keys = self._held.setdefault(source, set())
            if value == 1:
                keys.add(code)
            else:
                keys.discard(code)
            action = self._step(code, value == 1)
        self._fire(action)

    def drop_source(self, source: str) -> None:
        with self._lock:
            if self._held.pop(source, None) is None:
                return
            action = self._step(None, False)
        self._fire(action)

    def _step(self, code: int | None, down: bool) -> str | None:
        held = self._all_held()
        satisfied = self._satisfied(held)
        if self._active:
            if down and code not in self._chord_codes:
                self._active, self._spent = False, True
                return "cancel"
            if not satisfied:
                self._active = False
                return "release"
            return None
        if not satisfied:
            self._spent = False
            return None
        if not self._spent and held <= self._chord_codes:
            self._active = True
            return "press"
        return None

    def _fire(self, action: str | None) -> None:
        if action is None:
            return
        handler = {"press": self._on_press, "release": self._on_release, "cancel": self._on_cancel}[action]
        _log.debug("hotkey event", event=action)
        try:
            handler()
        except Exception:
            _log.exception("hotkey handler failed", event=action)


class KeyboardListener:
    """Feeds every keyboard able to type the chord into a ChordTracker, following hotplug."""

    def __init__(self, tracker: ChordTracker, groups: tuple[frozenset[int], ...], ignore_name: str) -> None:
        self._tracker = tracker
        self._groups = groups
        self._ignore_name = ignore_name
        self._tasks: dict[str, asyncio.Task] = {}
        self._denied: set[str] = set()

    def _scan(self) -> list[evdev.InputDevice]:
        found = []
        for path in evdev.list_devices():
            if path in self._tasks:
                continue
            try:
                dev = evdev.InputDevice(path)
            except PermissionError:
                if path not in self._denied:
                    self._denied.add(path)
                    _log.debug("no permission to read input device", path=path)
                continue
            except OSError:
                _log.debug("input device vanished while opening", path=path, exc_info=True)
                continue
            keys = set(dev.capabilities().get(ecodes.EV_KEY, []))
            if dev.name == self._ignore_name or not all(keys & g for g in self._groups):
                dev.close()
                continue
            found.append(dev)
        return found

    def _watch(self, dev: evdev.InputDevice) -> None:
        self._tasks[dev.path] = asyncio.ensure_future(self._read(dev))
        _log.info("watching keyboard", path=dev.path, name=dev.name)

    async def start(self) -> None:
        for dev in self._scan():
            self._watch(dev)
        if not self._tasks:
            # list_devices() silently skips unreadable nodes, so look at the nodes themselves.
            nodes = glob.glob("/dev/input/event*")
            if self._denied or not all(os.access(n, os.R_OK) for n in nodes):
                raise HotkeyError("no permission to read keyboards under /dev/input; add yourself to the input "
                                  "group (sudo usermod -aG input $USER) and log in again")
            raise HotkeyError("no keyboard that can type the hotkey was found")

    async def run(self) -> None:
        while True:
            await asyncio.sleep(RESCAN_S)
            try:
                for dev in self._scan():
                    self._watch(dev)
            except Exception:
                _log.exception("keyboard rescan failed; retrying")

    async def _read(self, dev: evdev.InputDevice) -> None:
        source = dev.path
        try:
            async for event in dev.async_read_loop():
                if event.type == ecodes.EV_KEY:
                    self._tracker.key(source, event.code, event.value)
        except OSError as exc:
            _log.info("keyboard disconnected", path=source, name=dev.name, error=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("reading keyboard failed", path=source)
        finally:
            self._tasks.pop(source, None)
            self._tracker.drop_source(source)
            try:
                dev.close()
            except OSError:
                pass
