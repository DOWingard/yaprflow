import subprocess

from . import log

_log = log.get("notify")

_enabled = True


def configure(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def send(summary: str, body: str = "", replace_id: str | None = None, timeout_ms: int = 4000) -> str | None:
    """Show a desktop notification; returns its id so a later call can replace it in place."""
    if not _enabled:
        return None
    cmd = ["notify-send", "-a", "yaprflow", "-p", "-t", str(timeout_ms)]
    if replace_id:
        cmd += ["-r", replace_id]
    cmd += [summary, body]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True)
        return out.stdout.strip() or None
    except (subprocess.SubprocessError, OSError):
        # A missing notification never blocks dictation.
        _log.warning("notification failed", exc_info=True, summary=summary)
        return None
