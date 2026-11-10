import os
import shutil
import sys
from dataclasses import dataclass

from . import log

_log = log.get("config")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    hotkey: str
    paste_keys: str
    submit: bool
    audio_device: str
    whisper_model: str
    min_seconds: float
    notify: bool
    clipboard: str  # "wayland" or "x11"
    log_level: str


def _bool(name: str, default: str) -> bool:
    value = os.environ.get(name, default).strip().lower()
    if value not in ("0", "1", "true", "false", "yes", "no"):
        raise ConfigError(f"{name} must be a boolean, got {value!r}")
    return value in ("1", "true", "yes")


def _float(name: str, default: str) -> float:
    raw = os.environ.get(name, default)
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None


def _clipboard() -> str:
    if os.environ.get("WAYLAND_DISPLAY"):
        backend, tools = "wayland", ("wl-copy", "wl-paste")
    elif os.environ.get("DISPLAY"):
        backend, tools = "x11", ("xclip",)
    else:
        raise ConfigError("neither WAYLAND_DISPLAY nor DISPLAY is set; yaprflow needs a graphical session")
    for tool in tools:
        if shutil.which(tool) is None:
            raise ConfigError(f"required executable {tool!r} is not on PATH (needed for the {backend} clipboard)")
    return backend


def load() -> Config:
    if sys.platform != "linux":
        # Recording and transcription are portable; the hotkey and keystroke injection use Linux evdev.
        raise ConfigError(f"platform {sys.platform!r} is not supported yet; yaprflow currently runs on Linux only")

    notify = _bool("YAPRFLOW_NOTIFY", "1")
    if notify and shutil.which("notify-send") is None:
        _log.warning("notify-send is not on PATH; desktop notifications are disabled")
        notify = False

    cfg = Config(
        hotkey=os.environ.get("YAPRFLOW_HOTKEY", "ctrl+super"),
        paste_keys=os.environ.get("YAPRFLOW_PASTE_KEYS", "ctrl+shift+v"),
        submit=_bool("YAPRFLOW_SUBMIT", "0"),
        audio_device=os.environ.get("YAPRFLOW_AUDIO_DEVICE", ""),
        whisper_model=os.environ.get("YAPRFLOW_WHISPER_MODEL", "small.en"),
        min_seconds=_float("YAPRFLOW_MIN_SECONDS", "0.5"),
        notify=notify,
        clipboard=_clipboard(),
        log_level=os.environ.get("YAPRFLOW_LOG_LEVEL", "info"),
    )
    _log.info("configuration loaded", **{k: str(v) for k, v in cfg.__dict__.items()})
    return cfg
