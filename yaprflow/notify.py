"""Desktop notifications: notify-send on Linux, Notification Center on macOS, none on Windows."""

import shutil
import subprocess
import sys

from . import log

_log = log.get("notify")

_enabled = False


def configure(enabled: bool) -> None:
    global _enabled
    if enabled and sys.platform == "linux" and shutil.which("notify-send") is None:
        _log.warning("notify-send is not on PATH; desktop notifications are disabled")
        enabled = False
    elif enabled and sys.platform not in ("linux", "darwin"):
        _log.info("desktop notifications are not implemented on this platform", platform=sys.platform)
        enabled = False
    _enabled = enabled


def _command(summary: str, body: str, replace_id: str | None, timeout_ms: int) -> list[str]:
    if sys.platform == "darwin":
        # Passing the strings as arguments avoids quoting them into AppleScript source.
        return ["osascript", "-e", "on run argv", "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run",
                summary, body or " "]
    cmd = ["notify-send", "-a", "yaprflow", "-p", "-t", str(timeout_ms)]
    if replace_id:
        cmd += ["-r", replace_id]
    return cmd + [summary, body]


def send(summary: str, body: str = "", replace_id: str | None = None, timeout_ms: int = 4000) -> str | None:
    """Show a notification; on Linux returns its id so a later call can replace it in place."""
    if not _enabled:
        return None
    try:
        out = subprocess.run(_command(summary, body, replace_id, timeout_ms), capture_output=True, text=True,
                             timeout=5, check=True)
        return (out.stdout.strip() or None) if sys.platform == "linux" else None
    except (subprocess.SubprocessError, OSError):
        # A missing notification never blocks dictation.
        _log.warning("notification failed", exc_info=True, summary=summary)
        return None
