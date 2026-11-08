"""Captura del micrófono y reproducción de la traducción.

``MicCapture`` alimenta un ``Segmenter`` desde el callback de audio (sin trabajo pesado en el callback:
solo copia y encola). ``Player`` reproduce los trozos sintetizados desde una cola.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from ..messages import SpeechSegment
from ..vad import Segmenter

log = logging.getLogger(__name__)

TARGET_SR = 16000
ECHO_TAIL_S = 0.6  # reverberación y retardo del micrófono tras terminar de sonar


class MicCapture:
    """Captura a la frecuencia nativa del dispositivo y entrega 16 kHz mono al segmentador."""

    def __init__(
        self,
        device: int | None,
        segmenter: Segmenter,
        on_segment: Callable[[SpeechSegment], None],
        on_level: Callable[[float], None] | None = None,
        samplerate: int | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        import sounddevice as sd

        self._on_error = on_error
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
            try:
                x = self._resample(block)
                if self.on_level is not None:
                    self.on_level(float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0)
                for seg in self.segmenter.feed(x):
                    self.on_segment(seg)
            except Exception as exc:  # noqa: BLE001 - no matar el hilo: informar y seguir con el siguiente bloque
                log.exception("Error procesando el audio del micrófono")
                if self._on_error is not None:
                    self._on_error(f"Error procesando el micrófono: {exc}")

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


class _FIFO:
    """Cola de audio mono (float32): ``push`` desde un hilo, ``pull`` desde el callback de PortAudio."""

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._pos = 0
        self._lock = threading.Lock()

    def push(self, audio: np.ndarray) -> None:
        with self._lock:
            self._chunks.append(audio)

    def pull(self, frames: int) -> np.ndarray:
        """Hasta ``frames`` muestras; si no hay suficientes, rellena con silencio (nunca bloquea)."""
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < frames and self._chunks:
                head = self._chunks[0]
                take = min(frames - filled, head.size - self._pos)
                out[filled : filled + take] = head[self._pos : self._pos + take]
                filled += take
                self._pos += take
                if self._pos >= head.size:
                    self._chunks.popleft()
                    self._pos = 0
        return out

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._pos = 0


class Player:
    """Reproduce trozos de audio en orden por el dispositivo elegido.

    WASAPI en modo compartido solo acepta la frecuencia del dispositivo (normalmente 48 kHz), mientras que
    las voces sintetizadas salen a 16–24 kHz: cada trozo se remuestrea a la frecuencia del dispositivo.

    La reproducción es por **callback** con un colchón propio (``_FIFO``): PortAudio pide audio
    cuando lo necesita y recibe silencio si aún no hay (nunca espera). Antes se usaba ``stream.write``
    bloqueante con un flujo mono sobre un dispositivo estéreo y latencia mínima, un patrón de WASAPI
    que da micro-cortes sin reportarlos y suena «robótico»; el navegador no los tiene porque usa su
    propio motor de audio.
    Los errores se informan por ``on_error`` en lugar de matar el hilo en silencio.
    """

    LATENCY_S = 0.12  # colchón del dispositivo: lo bastante grande para no cortarse, sin retrasar de más

    def __init__(
        self,
        device: int | None,
        get_audio: Callable[[float], object | None],
        on_error: Callable[[str], None] | None = None,
        sd_module: Any = None,
    ) -> None:
        if sd_module is None:
            import sounddevice

            sd_module = sounddevice
        self._sd = sd_module
        self.device = device
        self._get_audio = get_audio
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fifo = _FIFO()
        self.played_chunks = 0
        self.underflows = (
            0  # veces que el dispositivo pidió audio y no había (silencio entre frases también cuenta)
        )
        self._scheduled_end = 0.0
        self.busy_until = 0.0  # time.monotonic() en que termina de sonar lo ya encolado (+ cola de eco)

    def _device_info(self) -> dict:
        return self._sd.query_devices(self.device if self.device is not None else self._sd.default.device[1])

    def _device_rate(self) -> int:
        return int(self._device_info()["default_samplerate"])

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: ANN001
        mono = self._fifo.pull(frames)
        outdata[:] = mono.reshape(-1, 1)  # el mismo audio en todos los canales (altavoz o auricular estéreo)
        if status and getattr(status, "output_underflow", False):
            self.underflows += 1

    def _run(self) -> None:
        stream: Any = None
        try:
            info = self._device_info()
            rate = int(info["default_samplerate"])
            channels = (
                2 if int(info.get("max_output_channels", 2)) >= 2 else 1
            )  # estéreo: sin conversión rara de mono
            stream = self._sd.OutputStream(
                device=self.device,
                channels=channels,
                samplerate=rate,
                dtype="float32",
                latency=self.LATENCY_S,
                callback=self._callback,
            )
            stream.start()
            while not self._stop.is_set():
                chunk = self._get_audio(0.1)
                if chunk is None:
                    continue
                audio = _resample_audio(chunk.audio, chunk.sample_rate, rate)  # type: ignore[attr-defined]
                now = time.monotonic()
                self._scheduled_end = max(self._scheduled_end, now) + audio.size / rate
                self.busy_until = self._scheduled_end + ECHO_TAIL_S
                self._fifo.push(audio)
                self.played_chunks += 1
        except Exception as exc:  # noqa: BLE001 - se informa y se registra, no se pierde en silencio
            log.exception("Fallo del reproductor de audio")
            if self._on_error is not None:
                self._on_error(f"No se puede reproducir por la salida elegida: {exc}")
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:  # noqa: BLE001
                    log.debug("Error al cerrar el stream de salida", exc_info=True)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
        self._fifo.clear()


def _resample_audio(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return np.ascontiguousarray(audio, dtype=np.float32)
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(sr_in, sr_out)
    return resample_poly(audio, sr_out // g, sr_in // g).astype(np.float32)
