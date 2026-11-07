"""Interfaces de las etapas de IA. Cada backend (real o de prueba) las implementa."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from time import perf_counter

import numpy as np

from ..messages import AudioChunk, SpeechSegment, Transcript, Translation


class STTBackend(ABC):
    """Voz -> texto en el idioma de entrada fijado por el usuario."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> str: ...


class MTBackend(ABC):
    """Texto -> texto entre dos idiomas (códigos ISO 639-1: es, en, pt, fr)."""

    @abstractmethod
    def translate(self, text: str, source: str, target: str) -> str: ...


class TTSBackend(ABC):
    """Texto -> audio. ``synthesize`` puede producir varios trozos (streaming)."""

    sample_rate: int

    @abstractmethod
    def synthesize(self, text: str, language: str) -> Iterator[np.ndarray]: ...


# --- Adaptadores a "etapas" del StageRunner -------------------------------------------------------


DropCallback = Callable[[str], None]


class STTStage:
    name = "stt"

    def __init__(
        self,
        backend: STTBackend,
        language: str,
        on_text: Callable[[str], None] | None = None,
        on_drop: DropCallback | None = None,
    ) -> None:
        self.backend = backend
        self.language = language
        self.on_text = on_text
        self.on_drop = on_drop

    def process(self, seg: SpeechSegment) -> Iterator[Transcript]:
        t0 = perf_counter()
        text = self.backend.transcribe(seg.audio, seg.sample_rate, self.language).strip()
        seg.timings.stt = perf_counter() - t0
        seg.timings.stt_done_at = perf_counter()
        if text:
            if self.on_text is not None:
                self.on_text(text)
            yield Transcript(text, self.language, seg.generation, seg.timings)
        elif self.on_drop is not None:
            # idioma distinto (eco de la propia salida), poca confianza (ruido) o texto vacío
            reason = getattr(self.backend, "last_reject", None)
            self.on_drop(
                {"idioma": "idioma_distinto", "confianza": "poca_confianza"}.get(reason or "", "sin_texto")
            )


class MTStage:
    name = "mt"

    def __init__(
        self,
        backend: MTBackend,
        target: str,
        on_text: Callable[[str], None] | None = None,
        on_drop: DropCallback | None = None,
    ) -> None:
        self.backend = backend
        self.target = target  # se puede cambiar en caliente desde otro hilo (asignación atómica)
        self.on_text = on_text
        self.on_drop = on_drop

    def process(self, tr: Transcript) -> Iterator[Translation]:
        target = self.target
        t0 = perf_counter()
        text = self.backend.translate(tr.text, tr.language, target).strip()
        tr.timings.mt = perf_counter() - t0
        tr.timings.mt_done_at = perf_counter()
        if text:
            if self.on_text is not None:
                self.on_text(text)
            yield Translation(text, tr.language, target, tr.generation, tr.timings)
        elif self.on_drop is not None:
            self.on_drop("traduccion_vacia")


class TTSStage:
    name = "tts"

    def __init__(self, backend: TTSBackend, on_drop: DropCallback | None = None) -> None:
        self.backend = backend
        self.on_drop = on_drop

    def process(self, tl: Translation) -> Iterator[AudioChunk]:
        first = True
        for audio in self.backend.synthesize(tl.text, tl.target_language):
            if first:
                tl.timings.tts_first_audio = tl.timings.e2e()
            yield AudioChunk(audio, self.backend.sample_rate, tl.generation, first, tl.timings)
            first = False
        if first and self.on_drop is not None:
            self.on_drop("sin_audio")  # la síntesis no produjo ningún trozo
