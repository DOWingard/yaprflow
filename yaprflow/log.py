import json
import logging
import sys
import traceback
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "context": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["error"] = repr(record.exc_info[1])
            entry["stack"] = "".join(traceback.format_exception(*record.exc_info))
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry, default=str)


def setup(level: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # faster-whisper and its HTTP stack are chatty at debug; their warnings still pass.
    for noisy in ("faster_whisper", "httpx", "httpx2", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(max(root.level, logging.WARNING))


def get(name: str) -> logging.LoggerAdapter:
    return _Adapter(logging.getLogger(name), {})


class _Adapter(logging.LoggerAdapter):
    """Accepts structured fields as keyword arguments: log.info("msg", job_id=...)."""

    def process(self, msg, kwargs):
        fields = {k: kwargs.pop(k) for k in list(kwargs) if k not in ("exc_info", "stack_info", "stacklevel", "extra")}
        kwargs["extra"] = {"fields": fields}
        return msg, kwargs
