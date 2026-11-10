"""Paste text into the focused window through the clipboard and a synthetic paste keystroke.

The keystroke comes from a virtual keyboard (uinput), which every Wayland compositor and X11
accept like a real one. Dictated text is never written to disk or logged (only its length). On
Wayland it is offered as sensitive so clipboard managers such as Klipper leave it out of their
history, and the previous clipboard is put back right after the paste.
"""

import subprocess
import time
from collections.abc import Callable

import evdev
from evdev import UInput, ecodes

from . import log
from .hotkey import parse_chord

_log = log.get("paste")

VIRTUAL_NAME = "yaprflow virtual keyboard"
_SETTLE_S = 0.4         # time for the target to read the clipboard before it is restored
_KEY_GAP_S = 0.02
_DEVICE_READY_S = 0.5   # compositors need a moment to adopt a new input device
_MODIFIER_WAIT_S = 3.0
_TEXT = "text/plain;charset=utf-8"


class PasteError(Exception):
    pass


def _run(cmd: list[str], data: bytes | None = None, capture: bool = False) -> subprocess.CompletedProcess:
    # wl-copy and xclip fork a server that keeps the selection alive; without our pipes it cannot hang us.
    return subprocess.run(cmd, input=data, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=5)


class _Wayland:
    _SENSITIVE_HINT = "x-kde-passwordManagerHint"

    def save(self) -> tuple[str, bytes, bool] | None:
        types = _run(["wl-paste", "--list-types"], capture=True)
        offered = types.stdout.decode(errors="replace").split() if types.returncode == 0 else []
        if not offered:
            return None
        mime = _TEXT if _TEXT in offered else offered[0]
        data = _run(["wl-paste", "--no-newline", "--type", mime], capture=True)
        if data.returncode != 0:
            _log.warning("could not read the current clipboard; it will be cleared after the paste", mime=mime)
            return None
        return mime, data.stdout, self._SENSITIVE_HINT in offered

    def copy(self, data: bytes, mime: str = _TEXT, sensitive: bool = False) -> None:
        if _run(["wl-copy", "--type", mime] + (["--sensitive"] if sensitive else []), data).returncode != 0:
            raise PasteError("wl-copy failed")

    def restore(self, saved: tuple[str, bytes, bool] | None) -> None:
        if saved is None:
            self.clear()
        else:
            mime, data, sensitive = saved
            self.copy(data, mime, sensitive)

    def clear(self) -> None:
        _run(["wl-copy", "--clear"])


class _X11:
    def save(self) -> tuple[str, bytes, bool] | None:
        types = _run(["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"], capture=True)
        offered = types.stdout.decode(errors="replace").split() if types.returncode == 0 else []
        mime = next((t for t in ("UTF8_STRING", _TEXT, "STRING") if t in offered), None)
        if mime is None:
            if offered:
                _log.warning("clipboard holds non-text data, which is not restored on X11", types=offered[:5])
            return None
        data = _run(["xclip", "-selection", "clipboard", "-o", "-t", mime], capture=True)
        return (mime, data.stdout, False) if data.returncode == 0 else None

    def copy(self, data: bytes, mime: str = "UTF8_STRING", sensitive: bool = False) -> None:
        if _run(["xclip", "-selection", "clipboard", "-i", "-t", mime], data).returncode != 0:
            raise PasteError("xclip failed")

    def restore(self, saved: tuple[str, bytes, bool] | None) -> None:
        if saved is None:
            self.clear()
        else:
            self.copy(saved[1], saved[0])

    def clear(self) -> None:
        self.copy(b"")


class Paster:
    def __init__(self, clipboard: str, paste_keys: str, submit: bool) -> None:
        groups = parse_chord(paste_keys)
        # A chord group lists both sides of a modifier; pressing the left one is enough.
        self._combo = [min(g) for g in groups]
        self._submit = submit
        self._clipboard = _Wayland() if clipboard == "wayland" else _X11()
        try:
            self._ui = UInput({ecodes.EV_KEY: sorted(set(self._combo) | {ecodes.KEY_ENTER})}, name=VIRTUAL_NAME)
        except (PermissionError, evdev.UInputError, OSError) as exc:
            raise PasteError(f"cannot create a virtual keyboard on /dev/uinput ({exc}); it must be writable "
                             "by your user, e.g. through the input group") from exc
        time.sleep(_DEVICE_READY_S)
        _log.info("virtual keyboard ready", name=VIRTUAL_NAME,
                  paste_keys=[ecodes.KEY[c] for c in self._combo], submit=submit)

    def close(self) -> None:
        self._ui.close()

    def _tap(self, codes: list[int]) -> None:
        for c in codes:
            self._ui.write(ecodes.EV_KEY, c, 1)
            self._ui.syn()
            time.sleep(_KEY_GAP_S)
        for c in reversed(codes):
            self._ui.write(ecodes.EV_KEY, c, 0)
            self._ui.syn()
            time.sleep(_KEY_GAP_S)

    def _await_modifiers_released(self, modifiers_held: Callable[[], bool]) -> None:
        # A physical modifier still held would turn Ctrl+Shift+V into a different shortcut.
        deadline = time.monotonic() + _MODIFIER_WAIT_S
        while modifiers_held():
            if time.monotonic() > deadline:
                raise PasteError("a modifier key is still held; release all keys and dictate again")
            time.sleep(0.05)

    def paste(self, text: str, modifiers_held: Callable[[], bool], job_id: str) -> None:
        started = time.monotonic()
        self._await_modifiers_released(modifiers_held)
        try:
            saved = self._clipboard.save()
        except (subprocess.SubprocessError, OSError):
            _log.warning("could not read the current clipboard; it will be cleared after the paste", exc_info=True)
            saved = None
        try:
            self._clipboard.copy(text.encode(), sensitive=True)
            self._tap(self._combo)
            time.sleep(_SETTLE_S)
            if self._submit:
                self._tap([ecodes.KEY_ENTER])
        except (subprocess.SubprocessError, OSError) as exc:
            raise PasteError(f"paste failed: {exc}") from exc
        finally:
            self._restore(saved)
        _log.info("pasted", job_id=job_id, chars=len(text), submitted=self._submit,
                  clipboard_restored=saved is not None, seconds=round(time.monotonic() - started, 2))

    def _restore(self, saved) -> None:
        try:
            self._clipboard.restore(saved)
        except (PasteError, subprocess.SubprocessError, OSError):
            _log.error("could not restore the previous clipboard; clearing it so the dictated text does not linger",
                       exc_info=True)
            try:
                self._clipboard.clear()
            except (PasteError, subprocess.SubprocessError, OSError):
                _log.error("could not clear the clipboard", exc_info=True)
