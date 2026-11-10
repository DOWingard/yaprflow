"""Per-platform keyboard input and paste.

Each backend module provides:

- `MODIFIERS`: every key identifier that counts as a modifier.
- `parse_chord(spec) -> Groups`: one group of key identifiers per chord part.
- `Listener(tracker, groups)`: `await start()` fails fast on missing permissions, `await run()` feeds
  key events into the tracker until cancelled, and `close()` releases resources.
- `Paster(paste_keys, submit)`: `paste(text, modifiers_held, job_id)` puts the text into the focused
  window without leaving it on the clipboard, and `close()` releases resources.
"""

import sys
from types import ModuleType


class BackendError(Exception):
    """A missing permission or tool that stops yaprflow from starting."""


def load() -> ModuleType:
    if sys.platform == "linux":
        from . import linux as backend
    elif sys.platform in ("darwin", "win32"):
        from . import native as backend
    else:
        raise BackendError(f"platform {sys.platform!r} is not supported")
    return backend


DEFAULT_PASTE_KEYS = {
    "linux": "ctrl+shift+v",  # terminals reserve Ctrl+V for a literal next character
    "darwin": "super+v",
    "win32": "ctrl+v",
}
