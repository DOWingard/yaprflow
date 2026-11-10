import asyncio
import queue
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from . import log, notify
from .config import Config, ConfigError, load
from . import backends
from .backends import BackendError
from .chord import ChordError, ChordTracker
from .recorder import Recorder, RecorderError, check_available
from .transcribe import SilentInput, Transcriber

_log = log.get("yaprflow")


@dataclass
class Job:
    job_id: str
    audio: np.ndarray
    toast: str | None


class Worker(threading.Thread):
    """Transcribes and pastes one dictation at a time, in the order they were spoken."""

    def __init__(self, cfg: Config, transcriber: Transcriber, paster, tracker: ChordTracker) -> None:
        super().__init__(name="worker", daemon=True)
        self.cfg = cfg
        self.transcriber = transcriber
        self.paster = paster
        self.tracker = tracker
        self.jobs: queue.Queue[Job] = queue.Queue()

    def run(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                self._process(job)
            except Exception:
                _log.exception("dictation crashed", job_id=job.job_id)
                notify.send("yaprflow: failed", "Unexpected error; see the logs", job.toast)
            finally:
                job.audio = None  # drop the samples as soon as the job is done

    def _process(self, job: Job) -> None:
        try:
            text = self.transcriber.transcribe(job.audio, job.job_id)
        except SilentInput as exc:
            _log.warning("recording discarded", job_id=job.job_id, error=str(exc),
                         device=self.cfg.audio_device or "default")
            notify.send("yaprflow: microphone is silent",
                        f"Recording from {self.cfg.audio_device or 'the default input'}; set YAPRFLOW_AUDIO_DEVICE",
                        job.toast, timeout_ms=10000)
            return
        if not text:
            _log.warning("no speech detected", job_id=job.job_id)
            notify.send("yaprflow", "No speech detected", job.toast, timeout_ms=2000)
            return
        try:
            self.paster.paste(text, self.tracker.modifiers_held, job.job_id)
        except BackendError as exc:
            # Discarded rather than kept for retry: dictated text must not persist anywhere.
            _log.error("paste failed, text discarded", job_id=job.job_id, error=str(exc))
            notify.send("yaprflow: not pasted", f"{exc}\nSay it again.", job.toast, timeout_ms=10000)
            return
        hint = "Sent" if self.cfg.submit else "Review it, then press Enter"
        notify.send("yaprflow: pasted", f"{len(text)} characters. {hint}", job.toast, timeout_ms=2500)


class App:
    def __init__(self, cfg: Config, worker: Worker | None = None) -> None:
        self.cfg = cfg
        self.worker = worker
        self.recorder = Recorder(cfg.audio_device)
        self.toast: str | None = None

    def on_press(self) -> None:
        if not self.recorder.start():
            notify.send("yaprflow: microphone unavailable", "See the logs", timeout_ms=5000)
            return
        self.toast = notify.send("yaprflow: listening…", "Release the keys to paste", timeout_ms=0)

    def on_cancel(self) -> None:
        if self.recorder.stop() is not None:
            _log.info("recording cancelled by another key")
        self._close_toast("Cancelled")

    def on_release(self) -> None:
        stopped = self.recorder.stop()
        if stopped is None:
            self._close_toast("Nothing recorded")
            return
        audio, seconds = stopped
        if self.worker is None:
            _log.warning("dictation discarded, the speech model is still loading")
            self._close_toast("Still loading the speech model, try again shortly")
            return
        if seconds < self.cfg.min_seconds:
            _log.info("recording too short, discarded", seconds=round(seconds, 2))
            self._close_toast("Too short, discarded")
            return
        job = Job(job_id=datetime.now().strftime("%Y%m%d-%H%M%S-%f"), audio=audio, toast=self.toast)
        self.toast = None
        notify.send("yaprflow: transcribing…", f"{seconds:.1f}s recorded", job.toast, timeout_ms=60000)
        self.worker.jobs.put(job)
        _log.info("dictation queued", job_id=job.job_id, queue_depth=self.worker.jobs.qsize())

    def _close_toast(self, message: str) -> None:
        if self.toast:
            notify.send("yaprflow", message, self.toast, timeout_ms=1000)
            self.toast = None


def _on_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops have no signal handlers
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def run(cfg: Config) -> None:
    check_available()
    backend = backends.load()
    groups = backend.parse_chord(cfg.hotkey)
    backend.parse_chord(cfg.paste_keys)  # reject a bad paste chord before the slow model load
    app = App(cfg)
    tracker = ChordTracker(groups, backend.MODIFIERS, app.on_press, app.on_release, app.on_cancel)
    listener = backend.Listener(tracker, groups)
    await listener.start()  # fail on missing permissions before the slow model load
    paster = backend.Paster(cfg.paste_keys, cfg.submit)
    try:
        app.worker = Worker(cfg, Transcriber(cfg.whisper_model), paster, tracker)
        app.worker.start()
        _log.info("ready", hotkey=cfg.hotkey)
        notify.send("yaprflow ready", f"Hold {cfg.hotkey} and speak", timeout_ms=2500)

        stop = asyncio.Event()
        _on_signals(stop)
        watching = asyncio.ensure_future(listener.run())
        stopping = asyncio.ensure_future(stop.wait())
        await asyncio.wait([watching, stopping], return_when=asyncio.FIRST_COMPLETED)
        if watching.done():
            watching.result()  # re-raises why keyboard monitoring ended
        watching.cancel()
        _log.info("shutting down", queued=app.worker.jobs.qsize())
    finally:
        app.recorder.stop()
        listener.close()
        paster.close()


def main() -> int:
    log.setup("info")
    try:
        cfg = load()
    except ConfigError as exc:
        _log.error("invalid configuration", error=str(exc))
        notify.configure(True)
        notify.send("yaprflow: invalid configuration", str(exc), timeout_ms=0)
        return 2
    log.setup(cfg.log_level)
    notify.configure(cfg.notify)
    try:
        asyncio.run(run(cfg))
    except (BackendError, ChordError, RecorderError) as exc:
        _log.error("cannot start", error=str(exc))
        notify.send("yaprflow cannot start", str(exc), timeout_ms=0)
        return 3  # distinct from a crash so the service does not restart into the same failure
    return 0


if __name__ == "__main__":
    sys.exit(main())
