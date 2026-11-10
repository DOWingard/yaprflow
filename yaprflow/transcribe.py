import time

import numpy as np
from faster_whisper import WhisperModel

from . import log

_log = log.get("transcribe")

# Below this peak the input is treated as silent (wrong or muted source), not as quiet speech.
SILENT_PEAK_DBFS = -70.0
TARGET_PEAK = 0.7


class SilentInput(Exception):
    pass


def _dbfs(x: float) -> float:
    return round(20 * np.log10(max(x, 1e-9)), 1)


def normalise(audio: np.ndarray, job_id: str) -> np.ndarray:
    """Scale the peak to TARGET_PEAK.

    Whisper's voice-activity filter drops audio that is merely quiet, so a microphone with its
    volume turned down otherwise produces "no speech" for perfectly audible dictation.
    """
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
    _log.info("input level", job_id=job_id, peak_dbfs=_dbfs(peak), rms_dbfs=_dbfs(rms))
    if _dbfs(peak) < SILENT_PEAK_DBFS:
        raise SilentInput(f"microphone input is silent (peak {_dbfs(peak)} dBFS)")
    return audio * (TARGET_PEAK / peak)


class Transcriber:
    def __init__(self, model_name: str) -> None:
        started = time.monotonic()
        _log.info("loading whisper model", model=model_name)
        # The first run downloads the model to the Hugging Face cache.
        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _log.info("whisper model ready", model=model_name, seconds=round(time.monotonic() - started, 2))

    def transcribe(self, audio: np.ndarray, job_id: str) -> str:
        started = time.monotonic()
        segments, info = self._model.transcribe(normalise(audio, job_id), vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        # Only the length is logged: dictated text may be private.
        _log.info("transcribed", job_id=job_id, audio_seconds=round(info.duration, 2),
                  seconds=round(time.monotonic() - started, 2), chars=len(text))
        return text
