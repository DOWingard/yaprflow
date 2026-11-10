import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import log
from .backends import DEFAULT_PASTE_KEYS

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
    log_level: str


def config_file() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "yaprflow" / "env"


def load_env_file(path: Path) -> list[str]:
    """Apply KEY=value lines from path; variables already set in the environment win."""
    if not path.is_file():
        return []
    applied = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if not sep or not key:
            raise ConfigError(f"{path}:{n}: expected KEY=value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    return applied


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


def load() -> Config:
    path = config_file()
    applied = load_env_file(path)
    if applied:
        _log.info("read config file", path=str(path), keys=applied)
    cfg = Config(
        hotkey=os.environ.get("YAPRFLOW_HOTKEY", "ctrl+super"),
        paste_keys=os.environ.get("YAPRFLOW_PASTE_KEYS", DEFAULT_PASTE_KEYS.get(sys.platform, "ctrl+v")),
        submit=_bool("YAPRFLOW_SUBMIT", "0"),
        audio_device=os.environ.get("YAPRFLOW_AUDIO_DEVICE", ""),
        whisper_model=os.environ.get("YAPRFLOW_WHISPER_MODEL", "small.en"),
        min_seconds=_float("YAPRFLOW_MIN_SECONDS", "0.5"),
        notify=_bool("YAPRFLOW_NOTIFY", "1"),
        log_level=os.environ.get("YAPRFLOW_LOG_LEVEL", "info"),
    )
    _log.info("configuration loaded", platform=sys.platform, **{k: str(v) for k, v in cfg.__dict__.items()})
    return cfg
