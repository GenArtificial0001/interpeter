"""Segmentación del habla: convierte un flujo de tramas de audio en enunciados.

``EnergyVAD`` es la implementación base (sin dependencias, determinista y fácil de probar). Un VAD
neuronal (Silero ONNX) puede sustituirlo implementando ``is_speech(frame) -> bool``.

Parámetros de partida tomados de ``speech-to-speech`` (VAD Silero): ``min_speech_ms=384``,
``min_silence_ms`` corto y ``speech_pad_ms=500`` de audio previo.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .messages import SpeechSegment, Timings


class FrameClassifier(Protocol):
    def is_speech(self, frame: np.ndarray) -> bool: ...


class EnergyVAD:
    """Clasifica una trama como voz si su RMS supera un umbral (en dBFS)."""

    def __init__(self, threshold_db: float = -42.0) -> None:
        self.threshold_db = threshold_db

    def is_speech(self, frame: np.ndarray) -> bool:
        if frame.size == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        if rms <= 1e-9:
            return False
        return 20.0 * np.log10(rms) > self.threshold_db


@dataclass(slots=True)
class VadConfig:
    sample_rate: int = 16000
    frame_ms: int = 32
    min_speech_ms: int = 384  # menos que esto se descarta como ruido
    end_silence_ms: int = 480  # pausa que cierra un enunciado
    pad_ms: int = 320  # audio previo conservado al detectar el inicio
    max_speech_ms: int = 12000  # corte forzado de enunciados larguísimos

    @property
    def frame_samples(self) -> int:
        return self.sample_rate * self.frame_ms // 1000


class Segmenter:
    """Máquina de estados: silencio -> voz -> silencio. Emite un ``SpeechSegment`` por enunciado."""

    def __init__(self, classifier: FrameClassifier, config: VadConfig | None = None) -> None:
        self.classifier = classifier
        self.cfg = config or VadConfig()
        self._pad: deque[np.ndarray] = deque(maxlen=max(1, self.cfg.pad_ms // self.cfg.frame_ms))
        self._speech: list[np.ndarray] = []
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._tail = np.zeros(0, dtype=np.float32)

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(self, samples: np.ndarray, generation: int = 0) -> list[SpeechSegment]:
        """Procesa audio mono float32. Devuelve los enunciados que se completaron."""
        out: list[SpeechSegment] = []
        data = np.concatenate([self._tail, samples.astype(np.float32, copy=False)])
        n = self.cfg.frame_samples
        usable = (data.size // n) * n
        self._tail = data[usable:].copy()
        for i in range(0, usable, n):
            seg = self._step(data[i : i + n], generation)
            if seg is not None:
                out.append(seg)
        return out

    def flush(self, generation: int = 0) -> SpeechSegment | None:
        """Cierra el enunciado en curso (p. ej. al apagar el intérprete)."""
        return self._close(generation) if self._in_speech else None

    # -- internos ------------------------------------------------------------------------------

    def _step(self, frame: np.ndarray, generation: int) -> SpeechSegment | None:
        voiced = self.classifier.is_speech(frame)
        if not self._in_speech:
            if voiced:
                self._in_speech = True
                self._speech = list(self._pad) + [frame]
                self._speech_frames = 1
                self._silence_frames = 0
            else:
                self._pad.append(frame)
            return None

        self._speech.append(frame)
        if voiced:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        too_long = len(self._speech) * self.cfg.frame_ms >= self.cfg.max_speech_ms
        if self._silence_frames * self.cfg.frame_ms >= self.cfg.end_silence_ms or too_long:
            return self._close(generation)
        return None

    def _close(self, generation: int) -> SpeechSegment | None:
        voiced_ms = self._speech_frames * self.cfg.frame_ms
        audio = np.concatenate(self._speech) if self._speech else np.zeros(0, dtype=np.float32)
        self._speech = []
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._pad.clear()
        if voiced_ms < self.cfg.min_speech_ms:
            return None
        # Recorta el silencio final: el fin de habla real es antes de la pausa que lo confirmó.
        trim = self.cfg.end_silence_ms // self.cfg.frame_ms * self.cfg.frame_samples // 2
        if audio.size > trim:
            audio = audio[: audio.size - trim]
        return SpeechSegment(audio, self.cfg.sample_rate, generation, Timings())
