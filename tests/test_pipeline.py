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


def test_limpia_el_guion_de_dialogo_que_nllb_antepone():
    from interpreter.stages.backends import clean_translation

    assert clean_translation("- Bye, then.") == "Bye, then."
    assert clean_translation("— Hello!") == "Hello!"
    assert clean_translation("Well-known plan - done") == "Well-known plan - done"
    assert clean_translation("  Good day to you!  ") == "Good day to you!"


def test_prepare_reference_recorta_a_diez_segundos_de_voz(tmp_path, monkeypatch):
    import soundfile as sf

    from interpreter.stages.backends import prepare_reference

    monkeypatch.setenv("INTERPRETER_HOME", str(tmp_path))
    sr = 16000
    silencio = np.zeros(sr * 2, dtype=np.float32)
    t = np.arange(sr * 25) / sr
    voz = (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    sf.write(tmp_path / "larga.wav", np.concatenate([silencio, voz]), sr)
    corta = prepare_reference(tmp_path / "larga.wav", seconds=10.0)
    data, sr_out = sf.read(corta)
    assert sr_out == sr and 9.9 < len(data) / sr < 10.1  # sin los 2 s de silencio inicial
    assert float(np.max(np.abs(data[: sr // 2]))) > 0.1


# -- glosario en las etapas ---------------------------------------------------------------------------


class RecordingMT(FakeMT):
    """Traductor que anota lo que recibe y copia las marcas sin tocarlas, como hace NLLB."""

    def __init__(self, lose_marks: bool = False) -> None:
        super().__init__()
        self.seen: list[str] = []
        self.lose_marks = lose_marks

    def translate(self, text: str, source: str, target: str) -> str:
        self.seen.append(text)
        if self.lose_marks and "QZ" in text:
            return "perdí todo"
        return text.replace("Abre", "Open").replace("con", "with")


class RecordingTTS(FakeTTS):
    def __init__(self) -> None:
        super().__init__(chunks=1)
        self.spoken: list[str] = []

    def synthesize(self, text, language):
        self.spoken.append(text)
        yield from super().synthesize(text, language)


def _glossary():
    from interpreter.glossary import Glossary, GlossaryEntry

    return Glossary(
        [
            GlossaryEntry("SharePoint", ["sherpoin"]),
            GlossaryEntry("SAP", ["ese a pe"], "S A P"),
            GlossaryEntry("patio de contenedores", [], "", {"en": "container yard"}),
        ]
    )


def test_el_glosario_protege_terminos_en_la_traduccion_y_ajusta_la_pronunciacion():
    translations: list[str] = []
    mt, tts = RecordingMT(), RecordingTTS()
    p = Pipeline(
        FakeSTT("Abre SharePoint con el SAP en el patio de contenedores"),
        mt,
        tts,
        "es",
        "en",
        on_translation=lambda t: translations.append(t),
        glossary=_glossary(),
    )
    p.start()
    try:
        p.submit(seg())
        collect(p, 1)
    finally:
        p.stop()
    assert "SharePoint" not in mt.seen[0] and "QZ0Q" in mt.seen[0]  # el traductor nunca ve el término
    assert translations == ["Open SharePoint with el SAP en el container yard"]
    assert tts.spoken == ["Open SharePoint with el S A P en el container yard"]


def test_si_el_traductor_pierde_una_marca_se_reintenta_sin_proteger():
    translations: list[str] = []
    mt = RecordingMT(lose_marks=True)
    p = Pipeline(
        FakeSTT("Abre SharePoint"),
        mt,
        FakeTTS(chunks=1),
        "es",
        "en",
        on_translation=lambda t: translations.append(t),
        glossary=_glossary(),
    )
    p.start()
    try:
        p.submit(seg())
        collect(p, 1)
    finally:
        p.stop()
    assert len(mt.seen) == 2 and "QZ" in mt.seen[0] and mt.seen[1] == "Abre SharePoint"
    assert translations == ["Open SharePoint"]


def test_sin_glosario_el_flujo_no_cambia():
    mt = RecordingMT()
    p = Pipeline(FakeSTT("Abre SharePoint"), mt, FakeTTS(chunks=1), "es", "en")
    p.start()
    try:
        p.submit(seg())
        collect(p, 1)
    finally:
        p.stop()
    assert mt.seen == ["Abre SharePoint"]


def test_split_sentences_parte_por_frases_sin_romper_cifras_ni_siglas():
    from interpreter.stages.backends import split_sentences

    assert split_sentences("Hello there. How are you? Fine!") == ["Hello there.", "How are you?", "Fine!"]
    assert split_sentences("Meet at 8.30 a.m. at dock three.") == ["Meet at 8.30 a.m. at dock three."]
    assert split_sentences("One sentence only.") == ["One sentence only."]
    assert split_sentences("  ") == [""]
