"""Mensajes que viajan entre etapas del pipeline.

Cada mensaje lleva la *generación* de cancelación con la que nació: si el usuario vuelve a hablar y se
pide cancelar, las etapas descartan los mensajes con una generación anterior en lugar de acumular
retraso (patrón tomado de ``speech-to-speech``, ver docs/plans/2026-11-07-analisis-repos-referencia.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np


class _End:
    """Marcador de cierre de pipeline."""

    def __repr__(self) -> str:  # pragma: no cover - solo depuración
        return "PIPELINE_END"


PIPELINE_END = _End()


@dataclass(slots=True)
class Timings:
    """Tiempos (segundos) por etapa de un mismo enunciado, para medir la latencia."""

    speech_end: float = field(default_factory=perf_counter)  # fin estimado del habla
    stt: float | None = None
    mt: float | None = None
    tts_first_audio: float | None = None  # desde el fin del habla hasta el primer audio
    stt_done_at: float | None = None
    mt_done_at: float | None = None

    def e2e(self, now: float | None = None) -> float:
        return (now if now is not None else perf_counter()) - self.speech_end


@dataclass(slots=True)
class SpeechSegment:
    """Audio de un enunciado detectado por el VAD (mono, float32, 16 kHz)."""

    audio: np.ndarray
    sample_rate: int = 16000
    generation: int = 0
    timings: Timings = field(default_factory=Timings)


@dataclass(slots=True)
class Transcript:
    text: str
    language: str
    generation: int = 0
    timings: Timings = field(default_factory=Timings)


@dataclass(slots=True)
class Translation:
    text: str
    source_language: str
    target_language: str
    generation: int = 0
    timings: Timings = field(default_factory=Timings)


@dataclass(slots=True)
class AudioChunk:
    """Trozo de audio sintetizado (mono, float32) listo para reproducir."""

    audio: np.ndarray
    sample_rate: int
    generation: int = 0
    first: bool = False
    timings: Timings = field(default_factory=Timings)
