"""Pipeline completo STT -> MT -> TTS con hilos, colas y cancelación por generación."""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from dataclasses import dataclass

from .cancel import CancelScope
from .messages import PIPELINE_END, AudioChunk, SpeechSegment, Timings
from .runner import ErrorCallback, StageRunner
from .stages.base import MTBackend, MTStage, STTBackend, STTStage, TTSBackend, TTSStage

log = logging.getLogger(__name__)

MetricsCallback = Callable[["UtteranceMetrics"], None]


@dataclass(slots=True)
class UtteranceMetrics:
    """Latencias de un enunciado (segundos). ``None`` si la etapa no llegó a medirse."""

    stt: float | None
    mt: float | None
    first_audio_from_speech_end: float | None  # equivalente a ``tts_ttfa`` + etapas previas (e2e)

    @classmethod
    def from_timings(cls, t: Timings) -> UtteranceMetrics:
        return cls(t.stt, t.mt, t.tts_first_audio)


class Pipeline:
    """Cola de entrada de enunciados -> cola de salida de audio.

    La cola de entrada está acotada: si el modelo no da abasto, ``submit`` descarta el enunciado más
    viejo en lugar de acumular retraso (ver diseño §8).
    """

    def __init__(
        self,
        stt: STTBackend,
        mt: MTBackend,
        tts: TTSBackend,
        source_language: str,
        target_language: str,
        max_pending: int = 3,
        on_error: ErrorCallback | None = None,
        on_metrics: MetricsCallback | None = None,
        on_transcript: Callable[[str], None] | None = None,
        on_translation: Callable[[str], None] | None = None,
        on_drop: Callable[[str], None] | None = None,
    ) -> None:
        self.cancel = CancelScope()
        self.on_metrics = on_metrics
        self.dropped_overflow = 0
        self._in: queue.Queue = queue.Queue(maxsize=max_pending)
        q_text: queue.Queue = queue.Queue()
        q_tr: queue.Queue = queue.Queue()
        self.audio_out: queue.Queue = queue.Queue()
        self._mt_stage = MTStage(mt, target_language, on_translation, on_drop)
        stt_stage = STTStage(stt, source_language, on_transcript, on_drop)
        self._runners = [
            StageRunner(stt_stage, self._in, q_text, self.cancel, on_error),
            StageRunner(self._mt_stage, q_text, q_tr, self.cancel, on_error),
            StageRunner(TTSStage(tts, on_drop), q_tr, self.audio_out, self.cancel, on_error),
        ]
        self.sample_rate = tts.sample_rate

    @property
    def target_language(self) -> str:
        return self._mt_stage.target

    def set_target_language(self, language: str) -> None:
        """Cambia el idioma de salida en caliente: aplica desde el siguiente enunciado."""
        self._mt_stage.target = language

    def start(self) -> None:
        for r in self._runners:
            r.start()

    def stop(self) -> None:
        for r in self._runners:
            r.stop()

    def submit(self, seg: SpeechSegment) -> bool:
        """Encola un enunciado. Si la cola está llena, descarta el más viejo. Devuelve si hubo descarte."""
        seg.generation = self.cancel.generation
        dropped = False
        while True:
            try:
                self._in.put_nowait(seg)
                return dropped
            except queue.Full:
                try:
                    self._in.get_nowait()
                    self.dropped_overflow += 1
                    dropped = True
                except queue.Empty:
                    pass

    def interrupt(self) -> int:
        """Invalida todo lo que está en vuelo (el usuario cambió de idea o apagó el intérprete)."""
        return self.cancel.cancel()

    def get_audio(self, timeout: float | None = None) -> AudioChunk | None:
        """Siguiente trozo de audio. Emite las métricas del enunciado con su primer trozo."""
        try:
            item = self.audio_out.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is PIPELINE_END:
            return None
        if item.first and self.on_metrics is not None:
            self.on_metrics(UtteranceMetrics.from_timings(item.timings))
        return item
