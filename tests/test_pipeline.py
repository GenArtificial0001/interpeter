import time

import numpy as np

from interpreter.cancel import CancelScope
from interpreter.messages import SpeechSegment
from interpreter.pipeline import Pipeline, UtteranceMetrics
from interpreter.stages.fakes import FakeMT, FakeSTT, FakeTTS


def seg() -> SpeechSegment:
    return SpeechSegment(np.zeros(16000, dtype=np.float32))


def collect(p: Pipeline, n: int, timeout: float = 3.0) -> list:
    out, end = [], time.time() + timeout
    while len(out) < n and time.time() < end:
        c = p.get_audio(timeout=0.1)
        if c is not None:
            out.append(c)
    return out


def test_cancel_scope_marca_obsoletos():
    c = CancelScope()
    g = c.generation
    assert not c.is_stale(g)
    c.cancel()
    assert c.is_stale(g)
    assert not c.is_stale(c.generation)


def test_pipeline_extremo_a_extremo_y_metricas():
    metrics: list[UtteranceMetrics] = []
    p = Pipeline(FakeSTT(), FakeMT(), FakeTTS(chunks=2), "es", "en", on_metrics=metrics.append)
    p.start()
    try:
        p.submit(seg())
        chunks = collect(p, 2)
    finally:
        p.stop()
    assert len(chunks) == 2
    assert chunks[0].first and not chunks[1].first
    assert chunks[0].sample_rate == 24000
    assert len(metrics) == 1  # una sola vez por enunciado
    m = metrics[0]
    assert m.stt is not None and m.mt is not None and m.first_audio_from_speech_end is not None


def test_interrupt_descarta_lo_que_va_en_vuelo():
    p = Pipeline(FakeSTT(delay=0.3), FakeMT(), FakeTTS(), "es", "en")
    p.start()
    try:
        p.submit(seg())
        time.sleep(0.05)  # ya entró a STT
        p.interrupt()
        chunks = collect(p, 1, timeout=1.0)
    finally:
        p.stop()
    assert chunks == []


def test_desborde_descarta_lo_mas_viejo():
    p = Pipeline(FakeSTT(delay=0.5), FakeMT(), FakeTTS(), "es", "en", max_pending=2)
    # sin start(): la cola se llena y el desborde debe aplicarse
    results = [p.submit(seg()) for _ in range(4)]
    assert p.dropped_overflow == 2
    assert results[-1] is True


def test_error_en_una_etapa_no_mata_el_pipeline():
    class Boom(FakeSTT):
        def transcribe(self, audio, sample_rate, language):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("fallo simulado")
            return "hola mundo"

    errors: list[str] = []
    p = Pipeline(Boom(), FakeMT(), FakeTTS(chunks=1), "es", "en", on_error=lambda n, e: errors.append(n))
    p.start()
    try:
        p.submit(seg())
        p.submit(seg())
        chunks = collect(p, 1)
    finally:
        p.stop()
    assert errors == ["stt"]
    assert len(chunks) == 1


def test_texto_vacio_no_produce_audio():
    p = Pipeline(FakeSTT(text="   "), FakeMT(), FakeTTS(), "es", "en")
    p.start()
    try:
        p.submit(seg())
        chunks = collect(p, 1, timeout=0.6)
    finally:
        p.stop()
    assert chunks == []


def test_cada_etapa_avisa_cuando_no_produce_nada():
    from interpreter.stages.fakes import FakeMT, FakeSTT, FakeTTS

    casos = [
        (FakeSTT(text="   "), FakeMT(), FakeTTS(), "sin_texto"),
        (FakeSTT(), FakeMT(table={"hola mundo": "  "}), FakeTTS(), "traduccion_vacia"),
        (FakeSTT(), FakeMT(), FakeTTS(chunks=0), "sin_audio"),
    ]
    for stt, mt, tts, esperado in casos:
        motivos = []
        p = Pipeline(stt, mt, tts, "es", "en", on_drop=motivos.append)
        p.start()
        try:
            p.submit(seg())
            end = time.time() + 3
            while not motivos and time.time() < end:
                time.sleep(0.02)
        finally:
            p.stop()
        assert motivos == [esperado], (esperado, motivos)


def test_el_motivo_del_rechazo_del_reconocedor_llega_al_aviso():
    from interpreter.stages.base import STTBackend, STTStage

    class Rechaza(STTBackend):
        def __init__(self, motivo):
            self.last_reject = motivo

        def transcribe(self, audio, sample_rate, language):
            return ""

    esperado = {"idioma": "idioma_distinto", "confianza": "poca_confianza", None: "sin_texto"}
    for motivo, razon in esperado.items():
        vistos = []
        stage = STTStage(Rechaza(motivo), "es", on_drop=vistos.append)
        assert list(stage.process(seg())) == []
        assert vistos == [razon]
