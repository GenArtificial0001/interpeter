"""Captura del micrófono y reproducción de la traducción.

``MicCapture`` alimenta un ``Segmenter`` desde el callback de audio (sin trabajo pesado en el callback:
solo copia y encola). ``Player`` reproduce los trozos sintetizados desde una cola.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from ..messages import SpeechSegment
from ..vad import Segmenter

log = logging.getLogger(__name__)

TARGET_SR = 16000


class MicCapture:
    """Captura a la frecuencia nativa del dispositivo y entrega 16 kHz mono al segmentador."""

    def __init__(
        self,
        device: int | None,
        segmenter: Segmenter,
        on_segment: Callable[[SpeechSegment], None],
        on_level: Callable[[float], None] | None = None,
        samplerate: int | None = None,
    ) -> None:
        import sounddevice as sd

        self._sd = sd
        self.device = device
        self.segmenter = segmenter
        self.on_segment = on_segment
        self.on_level = on_level
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        self.samplerate = int(samplerate or info["default_samplerate"])
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream: Any = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self.overruns = 0

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: ANN001
        if status:
            self.overruns += 1
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.overruns += 1

    def _resample(self, x: np.ndarray) -> np.ndarray:
        if self.samplerate == TARGET_SR:
            return x
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(self.samplerate, TARGET_SR)
        return resample_poly(x, TARGET_SR // g, self.samplerate // g).astype(np.float32)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                block = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            x = self._resample(block)
            if self.on_level is not None:
                self.on_level(float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0)
            for seg in self.segmenter.feed(x):
                self.on_segment(seg)

    def start(self) -> None:
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="mic-segmenter", daemon=True)
        self._worker.start()
        self._stream = self._sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=self.samplerate,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._worker is not None:
            self._worker.join(1.0)
        tail = self.segmenter.flush()
        if tail is not None:
            self.on_segment(tail)


class Player:
    """Reproduce trozos de audio en orden por el dispositivo elegido."""

    def __init__(self, device: int | None, get_audio: Callable[[float], object | None]) -> None:
        import sounddevice as sd

        self._sd = sd
        self.device = device
        self._get_audio = get_audio
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        stream = None
        current_sr = None
        try:
            while not self._stop.is_set():
                chunk = self._get_audio(0.1)
                if chunk is None:
                    continue
                if stream is None or current_sr != chunk.sample_rate:  # type: ignore[attr-defined]
                    if stream is not None:
                        stream.stop()
                        stream.close()
                    current_sr = chunk.sample_rate  # type: ignore[attr-defined]
                    stream = self._sd.OutputStream(
                        device=self.device, channels=1, samplerate=current_sr, dtype="float32"
                    )
                    stream.start()
                stream.write(chunk.audio.reshape(-1, 1))  # type: ignore[attr-defined]
        finally:
            if stream is not None:
                stream.stop()
                stream.close()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
