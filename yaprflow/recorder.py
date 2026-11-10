"""Microphone capture held entirely in memory; no audio is ever written to a file."""

import threading
import time

import numpy as np

from . import log

_log = log.get("recorder")

SAMPLE_RATE = 16000  # what Whisper expects

try:
    import sounddevice as sd
except OSError as exc:  # raised when the PortAudio shared library is missing
    sd = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class RecorderError(Exception):
    pass


def check_available() -> None:
    if sd is None:
        raise RecorderError(f"PortAudio is not installed ({_IMPORT_ERROR}); install the portaudio package")


def _device(spec: str) -> int | str | None:
    if not spec:
        return None
    return int(spec) if spec.isdigit() else spec


class Recorder:
    """One input stream per held hotkey, collecting mono float32 chunks.

    The stream is opened per press rather than kept open, so the microphone is only live while
    the hotkey is held.
    """

    def __init__(self, device: str) -> None:
        self._device = _device(device)
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._rate = SAMPLE_RATE
        self._started = 0.0
        self._xruns = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            self._xruns += 1
        self._chunks.append(indata[:, 0].copy())

    def _open(self, rate: int):
        return sd.InputStream(samplerate=rate, channels=1, dtype="float32", device=self._device,
                              callback=self._callback)

    def start(self) -> bool:
        with self._lock:
            if self._stream is not None:
                return True
            self._chunks, self._xruns = [], 0
            try:
                try:
                    stream, rate = self._open(SAMPLE_RATE), SAMPLE_RATE
                except sd.PortAudioError:
                    # Raw hardware devices often reject 16 kHz; record natively and resample later.
                    rate = int(sd.query_devices(self._device, "input")["default_samplerate"])
                    _log.warning("input device rejected 16 kHz, recording at its native rate",
                                 device=str(self._device or "default"), rate=rate)
                    stream = self._open(rate)
                stream.start()
            except Exception:
                _log.exception("could not open the microphone", device=str(self._device or "default"))
                return False
            self._stream, self._rate, self._started = stream, rate, time.monotonic()
        _log.info("recording started", device=str(self._device or "default"), rate=rate)
        return True

    def stop(self) -> tuple[np.ndarray, float] | None:
        """Stop recording; returns (16 kHz samples, seconds held) or None if nothing was captured."""
        with self._lock:
            stream, self._stream = self._stream, None
            if stream is None:
                return None
            seconds = time.monotonic() - self._started
            try:
                stream.stop()
                stream.close()
            except Exception:
                _log.exception("closing the input stream failed; keeping what was captured")
            chunks, self._chunks = self._chunks, []
        if self._xruns:
            _log.warning("input overflowed during recording; some audio was dropped", xruns=self._xruns)
        if not chunks:
            _log.error("the microphone delivered no audio", seconds=round(seconds, 2))
            return None
        audio = np.concatenate(chunks)
        if self._rate != SAMPLE_RATE:
            n = int(len(audio) * SAMPLE_RATE / self._rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        _log.info("recording stopped", seconds=round(seconds, 2), samples=len(audio))
        return audio, seconds
