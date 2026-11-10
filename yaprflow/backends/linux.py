"""Linux input through the kernel (evdev) and output through a virtual keyboard (uinput).

Reading /dev/input works the same under any Wayland compositor or X11, and unlike desktop shortcut
APIs it can see a modifier-only chord such as Ctrl+Super. Keys are observed, never grabbed, so
they still reach the focused application. Reading requires membership of the `input` group;
creating the virtual keyboard requires write access to /dev/uinput.

Dictated text reaches the focused window through the clipboard: on Wayland it is offered as
sensitive so clipboard managers such as Klipper keep it out of their history, and the previous
clipboard is put back right after the paste.
"""

import asyncio
import glob
import os
import shutil
import subprocess
import time
from collections.abc import Callable

import evdev
from evdev import UInput, ecodes

from .. import log
from ..chord import ChordTracker, Groups, split
from . import BackendError

_log = log.get("linux")

VIRTUAL_NAME = "yaprflow virtual keyboard"
RESCAN_S = 3.0          # how often to look for newly plugged keyboards
_SETTLE_S = 0.4         # time for the target to read the clipboard before it is restored
_KEY_GAP_S = 0.02
_DEVICE_READY_S = 0.5   # compositors need a moment to adopt a new input device
_MODIFIER_WAIT_S = 3.0
_TEXT = "text/plain;charset=utf-8"

_SIDES = {
    "ctrl": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
    "alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "super": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
}
MODIFIERS = frozenset(ecodes.ecodes[n] for pair in _SIDES.values() for n in pair)


def _code(name: str) -> int:
    key = name.upper() if name.upper().startswith("KEY_") else f"KEY_{name.upper()}"
    code = ecodes.ecodes.get(key)
    if code is None:
        raise BackendError(f"unknown key {name!r}; use a modifier ({', '.join(_SIDES)}) or an evdev key name "
                           "such as f9, space or rightalt")
    return code


def parse_chord(spec: str) -> Groups:
    return tuple(frozenset(ecodes.ecodes[n] for n in _SIDES[p]) if p in _SIDES else frozenset([_code(p)])
                 for p in split(spec))


class Listener:
    """Feeds every keyboard able to type the chord into the tracker, following hotplug."""

    def __init__(self, tracker: ChordTracker, groups: Groups) -> None:
        self._tracker = tracker
        self._groups = groups
        self._tasks: dict[str, asyncio.Task] = {}

    def _scan(self) -> list[evdev.InputDevice]:
        found = []
        for path in evdev.list_devices():  # lists only nodes this process can read
            if path in self._tasks:
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                _log.debug("input device vanished or became unreadable while opening", path=path, exc_info=True)
                continue
            keys = set(dev.capabilities().get(ecodes.EV_KEY, []))
            if dev.name == VIRTUAL_NAME or not all(keys & g for g in self._groups):
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
        if self._tasks:
            return
        if not all(os.access(n, os.R_OK) for n in glob.glob("/dev/input/event*")):
            raise BackendError("no permission to read keyboards under /dev/input; add yourself to the input "
                               "group (sudo usermod -aG input $USER) and log in again")
        raise BackendError("no keyboard that can type the hotkey was found")

    async def run(self) -> None:
        while True:
            await asyncio.sleep(RESCAN_S)
            try:
                for dev in self._scan():
                    self._watch(dev)
            except Exception:
                _log.exception("keyboard rescan failed; retrying")

    def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()

    async def _read(self, dev: evdev.InputDevice) -> None:
        source = dev.path
        try:
            async for event in dev.async_read_loop():
                if event.type == ecodes.EV_KEY and event.value != 2:  # 2 is autorepeat
                    self._tracker.key(source, event.code, event.value == 1)
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


def _run(cmd: list[str], data: bytes | None = None, capture: bool = False) -> subprocess.CompletedProcess:
    # wl-copy and xclip fork a server that keeps the selection alive; without our pipes it cannot hang us.
    return subprocess.run(cmd, input=data, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=5)


class _Wayland:
    TOOLS = ("wl-copy", "wl-paste")
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
            raise BackendError("wl-copy failed")

    def restore(self, saved: tuple[str, bytes, bool] | None) -> None:
        if saved is None:
            self.clear()
        else:
            mime, data, sensitive = saved
            self.copy(data, mime, sensitive)

    def clear(self) -> None:
        _run(["wl-copy", "--clear"])


class _X11:
    TOOLS = ("xclip",)

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
            raise BackendError("xclip failed")

    def restore(self, saved: tuple[str, bytes, bool] | None) -> None:
        if saved is None:
            self.clear()
        else:
            self.copy(saved[1], saved[0])

    def clear(self) -> None:
        self.copy(b"")


def _clipboard() -> "_Wayland | _X11":
    if os.environ.get("WAYLAND_DISPLAY"):
        board = _Wayland()
    elif os.environ.get("DISPLAY"):
        board = _X11()
    else:
        raise BackendError("neither WAYLAND_DISPLAY nor DISPLAY is set; yaprflow needs a graphical session")
    missing = [t for t in board.TOOLS if shutil.which(t) is None]
    if missing:
        raise BackendError(f"{', '.join(missing)} not on PATH; install "
                           f"{'wl-clipboard' if isinstance(board, _Wayland) else 'xclip'}")
    _log.info("clipboard backend selected", backend=type(board).__name__.strip("_").lower())
    return board


class Paster:
    def __init__(self, paste_keys: str, submit: bool) -> None:
        # A chord group lists both sides of a modifier; the left one has the lower code.
        self._combo = [min(g) for g in parse_chord(paste_keys)]
        self._submit = submit
        self._clipboard = _clipboard()
        try:
            self._ui = UInput({ecodes.EV_KEY: sorted(set(self._combo) | {ecodes.KEY_ENTER})}, name=VIRTUAL_NAME)
        except (PermissionError, evdev.UInputError, OSError) as exc:
            raise BackendError(f"cannot create a virtual keyboard on /dev/uinput ({exc}); it must be writable "
                               "by your user, e.g. through the input group") from exc
        time.sleep(_DEVICE_READY_S)
        _log.info("virtual keyboard ready", name=VIRTUAL_NAME, paste_keys=[ecodes.KEY[c] for c in self._combo],
                  submit=submit)

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

    def paste(self, text: str, modifiers_held: Callable[[], bool], job_id: str) -> None:
        started = time.monotonic()
        _await_modifiers_released(modifiers_held)
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
            raise BackendError(f"paste failed: {exc}") from exc
        finally:
            self._restore(saved)
        _log.info("pasted", job_id=job_id, chars=len(text), submitted=self._submit,
                  clipboard_restored=saved is not None, seconds=round(time.monotonic() - started, 2))

    def _restore(self, saved) -> None:
        try:
            self._clipboard.restore(saved)
        except (BackendError, subprocess.SubprocessError, OSError):
            _log.error("could not restore the previous clipboard; clearing it so the dictated text does not linger",
                       exc_info=True)
            try:
                self._clipboard.clear()
            except (BackendError, subprocess.SubprocessError, OSError):
                _log.error("could not clear the clipboard", exc_info=True)


def _await_modifiers_released(modifiers_held: Callable[[], bool]) -> None:
    # A physical modifier still held would turn the paste keystroke into a different shortcut.
    deadline = time.monotonic() + _MODIFIER_WAIT_S
    while modifiers_held():
        if time.monotonic() > deadline:
            raise BackendError("a modifier key is still held; release all keys and dictate again")
        time.sleep(0.05)
