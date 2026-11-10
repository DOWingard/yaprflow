"""Platform-neutral hold-to-talk chord logic.

Chords are written as "+"-joined key names. Modifier names match either side of the keyboard and
are the same on every platform: `super` is the Windows key on Windows and Command on macOS.
Each input backend maps the names to its own key identifiers.
"""

import threading
from collections.abc import Callable, Hashable

from . import log

_log = log.get("chord")

MODIFIER_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt", "option": "alt",
    "super": "super", "meta": "super", "win": "super", "cmd": "super", "command": "super",
}

Groups = tuple[frozenset[Hashable], ...]


class ChordError(Exception):
    pass


def split(spec: str) -> list[str]:
    """'Ctrl+Win+F9' -> ['ctrl', 'super', 'f9']; modifiers are normalised to one name each."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ChordError(f"empty key chord {spec!r}")
    return [MODIFIER_ALIASES.get(p, p) for p in parts]


class ChordTracker:
    """Turns key events into press/release/cancel of a held chord.

    The chord fires only when it is held on its own, and pressing any other key while it is held
    cancels it, so shortcuts that extend the chord (Ctrl+Super+Left, …) never start a recording.
    Key state is kept per source device so that unplugging one keyboard cannot leave keys stuck.
    Repeated key-down events for a held key are harmless.
    """

    def __init__(self, groups: Groups, modifiers: frozenset[Hashable], on_press: Callable[[], None],
                 on_release: Callable[[], None], on_cancel: Callable[[], None]) -> None:
        self._groups = groups
        self._chord_keys = frozenset().union(*groups)
        self._modifiers = modifiers
        self._on_press, self._on_release, self._on_cancel = on_press, on_release, on_cancel
        self._held: dict[Hashable, set[Hashable]] = {}
        self._active = False
        self._spent = False  # cancelled; stays inert until the chord is fully let go
        self._lock = threading.Lock()

    def _all_held(self) -> set[Hashable]:
        return set().union(*self._held.values()) if self._held else set()

    def _satisfied(self, held: set[Hashable]) -> bool:
        return all(held & g for g in self._groups)

    def modifiers_held(self) -> bool:
        with self._lock:
            return bool(self._all_held() & self._modifiers)

    def key(self, source: Hashable, key: Hashable, down: bool) -> None:
        with self._lock:
            keys = self._held.setdefault(source, set())
            if down:
                keys.add(key)
            else:
                keys.discard(key)
            action = self._step(key, down)
        self._fire(action)

    def drop_source(self, source: Hashable) -> None:
        with self._lock:
            if self._held.pop(source, None) is None:
                return
            action = self._step(None, False)
        self._fire(action)

    def _step(self, key: Hashable | None, down: bool) -> str | None:
        held = self._all_held()
        satisfied = self._satisfied(held)
        if self._active:
            if down and key not in self._chord_keys:
                self._active, self._spent = False, True
                return "cancel"
            if not satisfied:
                self._active = False
                return "release"
            return None
        if not satisfied:
            self._spent = False
            return None
        if not self._spent and held <= self._chord_keys:
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
