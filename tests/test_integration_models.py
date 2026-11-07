"""Extremo a extremo con los modelos reales. Lenta: se activa con INTERPRETER_TEST_MODELS=1.

La entrada es voz sintética en español que pasa por el VAD y el pipeline completo; la salida se captura
en memoria (no suena nada).
"""

import os
import threading
import time

import numpy as np
import pytest

from interpreter.engine import Engine, EngineConfig, EngineEvents

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(os.environ.get("INTERPRETER_TEST_MODELS") != "1", reason="requiere modelos (lento)"),
]


class FeedCapture:
    """Simula el micrófono: entrega audio con silencio antes y después, en bloques de 80 ms."""

    audio: np.ndarray

    def __init__(self, device, segmenter, on_segment, on_level):
        self.segmenter, self.on_segment, self.on_level = segmenter, on_segment, on_level
        self._t = None

    def start(self):
        def run():
            sr = 16000
            x = np.concatenate([np.zeros(sr // 2), FeedCapture.audio, np.zeros(sr * 2)]).astype(np.float32)
            for i in range(0, x.size, 1280):
                for seg in self.segmenter.feed(x[i : i + 1280]):
                    self.on_segment(seg)
                time.sleep(0.01)

        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()

    def stop(self):
        if self._t:
            self._t.join(2)


class SinkPlayer:
    played: list = []

    def __init__(self, device, get_audio):
        self.get_audio = get_audio
        self._run = False

    def start(self):
        self._run = True
        SinkPlayer.played = []

        def loop():
            while self._run:
                c = self.get_audio(0.1)
                if c is not None:
                    SinkPlayer.played.append(c)

        self._t = threading.Thread(target=loop, daemon=True)
        self._t.start()

    def stop(self):
        self._run = False
        self._t.join(2)


def test_es_a_en_con_modelos_reales():
    from interpreter.stages.backends import MMSTTS

    src = MMSTTS()
    FeedCapture.audio = next(iter(src.synthesize("Necesito que revisemos el plan de entregas.", "es")))

    original, trad, metrics = [], [], []
    ev = EngineEvents(
        on_original=original.append,
        on_translation=trad.append,
        on_metrics=metrics.append,
        on_error=lambda m: pytest.fail(m),
    )
    eng = Engine(
        EngineConfig(source="es", target="en"), ev, capture_factory=FeedCapture, player_factory=SinkPlayer
    )
    eng.load()
    eng.start()
    end = time.time() + 40
    while time.time() < end and not SinkPlayer.played:
        time.sleep(0.1)
    eng.stop()

    assert original, "no hubo transcripción"
    assert "plan" in original[0].lower()
    assert trad and any(w in trad[0].lower() for w in ("plan", "schedule", "deliver"))
    assert SinkPlayer.played and SinkPlayer.played[0].audio.size > 1000
    assert metrics and metrics[0].first_audio_from_speech_end is not None
    print(
        f"\n  original: {original[0]}\n  traducción: {trad[0]}"
        f"\n  1.er audio: {metrics[0].first_audio_from_speech_end:.2f} s"
    )
