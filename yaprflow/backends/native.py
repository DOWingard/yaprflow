"""macOS and Windows input and output through pynput.

macOS: the hotkey comes from a Quartz event tap and the paste keystroke from synthetic CGEvents.
Both need the Python interpreter to be allowed under System Settings → Privacy & Security →
Accessibility (and Input Monitoring). Windows: a low-level keyboard hook and SendInput, with no
special permission, though Windows refuses to inject keys into an elevated (administrator) window
from a non-elevated process.

The clipboard is handled as text: the previous text is restored after the paste, but non-text
clipboard content (an image, files) is not preserved.
"""

import asyncio
import sys
import time
from collections.abc import Callable, Hashable

import pyperclip
from pynput import keyboard
from pynput.keyboard import Key, KeyCode

from .. import log
from ..chord import ChordTracker, Groups, split
from . import BackendError

_log = log.get("native")

_SETTLE_S = 0.4         # time for the target to read the clipboard before it is restored
_KEY_GAP_S = 0.02
_MODIFIER_WAIT_S = 3.0
_SOURCE = "keyboard"    # the OS merges all keyboards into one event stream

_MODIFIER_KEYS = {"ctrl": (Key.ctrl,), "shift": (Key.shift,), "alt": (Key.alt, Key.alt_gr), "super": (Key.cmd,)}

# Listener.canonical() folds left/right modifiers together and drops modifier state from
# characters; it needs an instance but does not start a thread.
_canon = keyboard.Listener().canonical


def _token(key: Key | KeyCode) -> Hashable:
    """A hashable identity for a key; pynput's KeyCode hash is inconsistent with its equality."""
    key = _canon(key)
    if isinstance(key, Key):
        return ("key", key.name)
    if key.char is not None:
        return ("char", key.char)
    return ("vk", key.vk)


MODIFIERS = frozenset(_token(k) for keys in _MODIFIER_KEYS.values() for k in keys)


def _key(name: str) -> Key | KeyCode:
    if name in _MODIFIER_KEYS:
        return _MODIFIER_KEYS[name][0]
    if hasattr(Key, name):
        return Key[name]
    if len(name) == 1:
        return KeyCode.from_char(name)
    raise BackendError(f"unknown key {name!r}; use a modifier ({', '.join(_MODIFIER_KEYS)}), a single character "
                       "or a key name such as f9, space or insert")


def parse_chord(spec: str) -> Groups:
    return tuple(frozenset(_token(k) for k in _MODIFIER_KEYS[p]) if p in _MODIFIER_KEYS
                 else frozenset([_token(_key(p))]) for p in split(spec))


def _accessibility_trusted() -> bool:
    import HIServices  # from pyobjc-framework-ApplicationServices, a pynput dependency on macOS

    return bool(HIServices.AXIsProcessTrusted())


class Listener:
    def __init__(self, tracker: ChordTracker, groups: Groups) -> None:
        self._tracker = tracker
        self._listener: keyboard.Listener | None = None

    def _event(self, key: Key | KeyCode | None, down: bool) -> None:
        if key is None:  # pynput reports keys it cannot identify as None
            return
        try:
            self._tracker.key(_SOURCE, _token(key), down)
        except Exception:
            _log.exception("handling a key event failed")

    async def start(self) -> None:
        if sys.platform == "darwin" and not _accessibility_trusted():
            raise BackendError(f"macOS has not allowed {sys.executable} to monitor the keyboard; add it under System "
                               "Settings → Privacy & Security → Accessibility and Input Monitoring, then restart")
        self._listener = keyboard.Listener(on_press=lambda k: self._event(k, True),
                                           on_release=lambda k: self._event(k, False))
        self._listener.start()
        self._listener.wait()
        _log.info("keyboard hook installed", platform=sys.platform)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if not self._listener.is_alive():
                raise BackendError("the keyboard hook stopped unexpectedly")

    def close(self) -> None:
        if self._listener is not None:
            self._listener.stop()


class Paster:
    def __init__(self, paste_keys: str, submit: bool) -> None:
        self._combo = [_key(p) for p in split(paste_keys)]
        self._submit = submit
        self._keys = keyboard.Controller()
        try:
            pyperclip.paste()
        except pyperclip.PyperclipException as exc:
            raise BackendError(f"the clipboard is not accessible: {exc}") from exc
        _log.info("paster ready", paste_keys=paste_keys, submit=submit)

    def close(self) -> None:
        pass

    def _tap(self, keys: list[Key | KeyCode]) -> None:
        for k in keys:
            self._keys.press(k)
            time.sleep(_KEY_GAP_S)
        for k in reversed(keys):
            self._keys.release(k)
            time.sleep(_KEY_GAP_S)

    def paste(self, text: str, modifiers_held: Callable[[], bool], job_id: str) -> None:
        started = time.monotonic()
        deadline = started + _MODIFIER_WAIT_S
        while modifiers_held():
            if time.monotonic() > deadline:
                raise BackendError("a modifier key is still held; release all keys and dictate again")
            time.sleep(0.05)
        try:
            saved = pyperclip.paste()
        except pyperclip.PyperclipException:
            _log.warning("could not read the current clipboard; it will be cleared after the paste", exc_info=True)
            saved = ""
        try:
            pyperclip.copy(text)
            self._tap(self._combo)
            time.sleep(_SETTLE_S)
            if self._submit:
                self._tap([Key.enter])
        except pyperclip.PyperclipException as exc:
            raise BackendError(f"paste failed: {exc}") from exc
        finally:
            self._restore(saved)
        _log.info("pasted", job_id=job_id, chars=len(text), submitted=self._submit,
                  seconds=round(time.monotonic() - started, 2))

    @staticmethod
    def _restore(saved: str) -> None:
        try:
            pyperclip.copy(saved)
        except pyperclip.PyperclipException:
            _log.error("could not restore the previous clipboard", exc_info=True)
