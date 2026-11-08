import time

import numpy as np
import pytest

from interpreter.engine import Engine, EngineConfig, EngineError, EngineEvents, EngineState, check_devices
from interpreter.messages import SpeechSegment
from interpreter.stages.fakes import FakeMT, FakeSTT, FakeTTS


class FakeCapture:
    def __init__(self, device, segmenter, on_segment, on_level, on_error=None):
        self.on_segment = on_segment
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakePlayer:
    def __init__(self, device, get_audio, on_error=None):
        self.get_audio = get_audio
        self.played = []
        self.running = False

    def start(self):
        import threading

        self.running = True

        def run():
            while self.running:
                c = self.get_audio(0.05)
                if c is not None:
                    self.played.append(c)

        self._t = threading.Thread(target=run, daemon=True)
        self._t.start()

    def stop(self):
        self.running = False
        self._t.join(1)


def fake_backends(cfg):
    return FakeSTT(), FakeMT(), FakeTTS(chunks=1)


def make(events=None, cfg=None):
    holder = {}

    def cap(*a, **k):
        holder["cap"] = FakeCapture(*a, **k)
        return holder["cap"]

    def ply(*a):
        holder["ply"] = FakePlayer(*a)
        return holder["ply"]

    eng = Engine(cfg or EngineConfig(), events or EngineEvents(), fake_backends, cap, ply)
    return eng, holder


def seg():
    return SpeechSegment(np.zeros(16000, dtype=np.float32))


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_flujo_completo_y_eventos():
    states, original, trad, metrics = [], [], [], []
    ev = EngineEvents(
        on_state=lambda s, d: states.append(s),
        on_original=original.append,
        on_translation=trad.append,
        on_metrics=metrics.append,
    )
    eng, h = make(ev)
    eng.load()
    assert eng.state is EngineState.READY
    eng.start()
    assert eng.state is EngineState.RUNNING and h["cap"].started
    h["cap"].on_segment(seg())
    assert wait_for(lambda: h["ply"].played)
    assert original == ["hola mundo"] and trad == ["hello world"]
    assert wait_for(lambda: len(metrics) == 1)
    eng.stop()
    assert eng.state is EngineState.READY and h["cap"].stopped
    assert states == [EngineState.LOADING, EngineState.READY, EngineState.RUNNING, EngineState.READY]


def test_start_exige_modelos_cargados():
    eng, _ = make()
    with pytest.raises(EngineError):
        eng.start()


def test_mismo_idioma_entrada_y_salida_es_error():
    eng, _ = make(cfg=EngineConfig(source="es", target="es"))
    eng.load()
    with pytest.raises(EngineError):
        eng.start()


def test_silenciar_descarta_enunciados():
    eng, h = make()
    eng.load()
    eng.start()
    eng.set_muted(True)
    h["cap"].on_segment(seg())
    time.sleep(0.4)
    assert h["ply"].played == [] and eng.segments_sent == 0
    eng.stop()


def test_cambiar_idioma_destino_en_caliente():
    trad = []
    eng, h = make(EngineEvents(on_translation=trad.append))
    eng._backend_factory = lambda cfg: (FakeSTT(), FakeMT(table={"x": "y"}), FakeTTS(chunks=1))
    eng.load()
    eng.start()
    eng.set_target_language("fr")
    h["cap"].on_segment(seg())
    assert wait_for(lambda: trad)
    assert trad == ["[fr] hola mundo"]
    eng.stop()


def test_fallo_al_cargar_modelos_deja_estado_error():
    errors = []

    def boom(cfg):
        raise RuntimeError("sin memoria")

    eng = Engine(events=EngineEvents(on_error=errors.append), backend_factory=boom)
    with pytest.raises(RuntimeError):
        eng.load()
    assert eng.state is EngineState.ERROR and "sin memoria" in errors[0]


def test_fallo_del_microfono_vuelve_a_listo():
    class BadCapture(FakeCapture):
        def start(self):
            raise OSError("dispositivo ocupado")

    eng = Engine(backend_factory=fake_backends, capture_factory=BadCapture, player_factory=FakePlayer)
    eng.load()
    with pytest.raises(EngineError, match="micrófono"):
        eng.start()
    assert eng.state is EngineState.READY


def test_aviso_de_realimentacion():
    assert check_devices("Jack", "Jack").warnings
    assert check_devices("Mic USB", "Jack").ok


# -- selección del reconocimiento (GPU Intel con respaldo a CPU) ------------------------------------


def test_make_stt_usa_openvino_si_hay_gpu(monkeypatch):
    from interpreter import engine as eng_mod
    from interpreter.stages import backends

    elegido = {}

    class FakeOV:
        device = "GPU"

        def __init__(self, model, threads=None, device="GPU", **kw):
            elegido["ov"] = (model, device)

    monkeypatch.setattr(backends, "openvino_devices", lambda: ["CPU", "GPU", "NPU"])
    monkeypatch.setattr(backends, "OVWhisperSTT", FakeOV)
    stt = eng_mod.make_stt(EngineConfig(stt_model="openai/whisper-base"))
    assert isinstance(stt, FakeOV) and elegido["ov"] == ("openai/whisper-base", "GPU")


def test_make_stt_cae_a_cpu_si_openvino_falla(monkeypatch):
    from interpreter import engine as eng_mod
    from interpreter.stages import backends

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("driver de GPU no disponible")

    class FakeCPU:
        device = "CPU"

        def __init__(self, model, threads=None, quantize=False):
            self.quantize = quantize

    monkeypatch.setattr(backends, "openvino_devices", lambda: ["CPU", "GPU"])
    monkeypatch.setattr(backends, "OVWhisperSTT", Boom)
    monkeypatch.setattr(backends, "WhisperSTT", FakeCPU)
    stt = eng_mod.make_stt(EngineConfig())
    assert isinstance(stt, FakeCPU) and stt.quantize is False


def test_make_stt_respeta_solo_cpu(monkeypatch):
    from interpreter import engine as eng_mod
    from interpreter.stages import backends

    class NoDebeUsarse:
        def __init__(self, *a, **k):
            raise AssertionError("no debía usar OpenVINO")

    class FakeCPU:
        device = "CPU"

        def __init__(self, *a, **k): ...

    monkeypatch.setattr(backends, "openvino_devices", lambda: ["CPU", "GPU"])
    monkeypatch.setattr(backends, "OVWhisperSTT", NoDebeUsarse)
    monkeypatch.setattr(backends, "WhisperSTT", FakeCPU)
    assert isinstance(eng_mod.make_stt(EngineConfig(accelerator="cpu")), FakeCPU)


def test_make_stt_sin_gpu_usa_pytorch(monkeypatch):
    from interpreter import engine as eng_mod
    from interpreter.stages import backends

    class FakeCPU:
        device = "CPU"

        def __init__(self, *a, **k): ...

    monkeypatch.setattr(backends, "openvino_devices", lambda: ["CPU"])
    monkeypatch.setattr(backends, "WhisperSTT", FakeCPU)
    assert isinstance(eng_mod.make_stt(EngineConfig()), FakeCPU)


def test_el_estado_listo_informa_el_dispositivo():
    class Dev(FakeSTT):
        device = "GPU"

    estados = []
    eng = Engine(
        EngineConfig(),
        EngineEvents(on_state=lambda s, d: estados.append((s, d))),
        lambda cfg: (Dev(), FakeMT(), FakeTTS(chunks=1)),
        FakeCapture,
        FakePlayer,
    )
    eng.load()
    assert estados[-1] == (EngineState.READY, "Listo · reconocimiento en GPU")


# -- silencio visible y avisos de dispositivos -------------------------------------------------------


def test_una_frase_silenciada_se_informa_en_vez_de_perderse():
    caidas, enviadas = [], []
    ev = EngineEvents(
        on_dropped=lambda motivo, s: caidas.append((motivo, round(s, 1))), on_segment=enviadas.append
    )
    eng, h = make(ev)
    eng.load()
    eng.start()
    eng.set_muted(True)
    h["cap"].on_segment(seg())
    assert caidas == [("silenciado", 1.0)] and enviadas == []
    assert eng.segments_dropped == 1 and eng.segments_sent == 0
    eng.set_muted(False)
    h["cap"].on_segment(seg())
    assert wait_for(lambda: enviadas == [1.0])
    eng.stop()


def test_el_motor_registra_cada_etapa_en_el_log(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="interpreter.engine")
    eng, h = make()
    eng.load()
    eng.start()
    h["cap"].on_segment(seg())
    assert wait_for(lambda: h["ply"].played)
    eng.stop()
    texto = caplog.text
    assert (
        "Frase detectada" in texto and "Reconocido: hola mundo" in texto and "Traducido: hello world" in texto
    )


def test_aviso_si_micro_y_salida_son_del_mismo_auricular_bluetooth():
    r = check_devices("Headset (WL5024)", "Headphones (WL5024)")
    assert not r.ok and "Bluetooth" in r.warnings[0]


def test_aviso_de_eco_con_micro_y_altavoces_de_la_laptop():
    r = check_devices(
        "Microphone Array on SoundWire Device (9- Cirrus Logic XU)", "Altavoces (11- Cirrus Logic XU)"
    )
    assert not r.ok


def test_combinaciones_de_equipos_distintos_no_avisan():
    assert check_devices(
        "Microphone Array on SoundWire Device (9- Cirrus Logic XU)", "Headphones (WL5024)"
    ).ok
    assert check_devices("Headset (WL5024)", "Altavoces (11- Cirrus Logic XU)").ok
    assert check_devices("Mic USB", "Salida sin etiqueta").ok


def test_el_desborde_de_la_cola_se_informa():
    caidas = []
    eng, h = make(EngineEvents(on_dropped=lambda motivo, s: caidas.append(motivo)))
    eng._backend_factory = lambda cfg: (FakeSTT(delay=0.4), FakeMT(), FakeTTS(chunks=1))
    eng.load()
    eng.start()
    for _ in range(8):
        h["cap"].on_segment(seg())
    assert "cola" in caidas
    eng.stop()


def test_el_motor_pasa_el_manejador_de_errores_a_la_captura():
    recibido = {}

    class Cap(FakeCapture):
        def __init__(self, device, segmenter, on_segment, on_level, on_error=None):
            super().__init__(device, segmenter, on_segment, on_level, on_error)
            recibido["on_error"] = on_error

    errores = []
    eng = Engine(
        EngineConfig(),
        EngineEvents(on_error=errores.append),
        fake_backends,
        Cap,
        FakePlayer,
    )
    eng.load()
    eng.start()
    recibido["on_error"]("fallo del micrófono")
    assert errores == ["fallo del micrófono"]
    eng.stop()


# -- perfil clonado opcional: si no carga, no se atasca la app ----------------------------------------


def test_si_el_perfil_clonado_falla_se_usa_la_voz_rapida_y_se_avisa():
    errores, intentos = [], []

    def fabrica(cfg):
        intentos.append(cfg.tts_profile)
        if cfg.tts_profile == "clone":
            raise RuntimeError("XTTSv2 usa la licencia CPML")
        return FakeSTT(), FakeMT(), FakeTTS(chunks=1)

    eng = Engine(EngineConfig(tts_profile="clone"), EngineEvents(on_error=errores.append), fabrica)
    eng.load()
    assert eng.state is EngineState.READY
    assert intentos == ["clone", "fast"] and eng.config.tts_profile == "fast"
    assert len(errores) == 1 and "voz rápida" in errores[0] and "CPML" in errores[0]


def test_un_fallo_con_el_perfil_rapido_sigue_siendo_error():
    def fabrica(cfg):
        raise RuntimeError("sin memoria")

    eng = Engine(EngineConfig(tts_profile="fast"), backend_factory=fabrica)
    with pytest.raises(RuntimeError):
        eng.load()
    assert eng.state is EngineState.ERROR


# -- guarda anti-eco -------------------------------------------------------------------------------------


def _seg_de(segundos):
    return SpeechSegment(np.zeros(int(16000 * segundos), dtype=np.float32))


def test_anti_eco_ignora_frases_que_empezaron_mientras_sonaba_la_traduccion():
    caidas, enviadas = [], []
    eng, h = make(
        EngineEvents(on_dropped=lambda m, s: caidas.append(m), on_segment=enviadas.append),
        EngineConfig(anti_echo=True),
    )
    eng.load()
    eng.start()
    h["ply"].busy_until = time.monotonic() + 5.0  # la traducción sigue sonando 5 s más
    h["cap"].on_segment(_seg_de(1.0))
    assert caidas == ["eco"] and enviadas == []
    eng.stop()


def test_anti_eco_deja_pasar_las_frases_que_empezaron_despues():
    caidas, enviadas = [], []
    eng, h = make(
        EngineEvents(on_dropped=lambda m, s: caidas.append(m), on_segment=enviadas.append),
        EngineConfig(anti_echo=True),
    )
    eng.load()
    eng.start()
    h["ply"].busy_until = time.monotonic() - 10.0  # terminó hace rato
    h["cap"].on_segment(_seg_de(1.0))
    assert wait_for(lambda: enviadas == [1.0]) and caidas == []
    eng.stop()


def test_anti_eco_desactivado_no_descarta_nada():
    enviadas = []
    eng, h = make(EngineEvents(on_segment=enviadas.append), EngineConfig(anti_echo=False))
    eng.load()
    eng.start()
    h["ply"].busy_until = time.monotonic() + 5.0
    h["cap"].on_segment(_seg_de(1.0))
    assert wait_for(lambda: enviadas == [1.0])
    eng.stop()


# -- perfil «Clonado rápido» (YourTTS) --------------------------------------------------------------------


def test_default_backends_elige_yourtts_para_clone_fast(monkeypatch, tmp_path):
    from interpreter import engine as eng_mod
    from interpreter.stages import backends

    elegidos = []

    class Falso:
        sample_rate = 16000

        def __init__(self, *a, **k):
            elegidos.append((type(self).__name__, a))

    class FakeYour(Falso): ...

    class FakeXtts(Falso): ...

    class FakeMms(Falso): ...

    class FakeMT(Falso): ...

    monkeypatch.setenv("INTERPRETER_HOME", str(tmp_path))
    monkeypatch.setattr(backends, "YourTTSClone", FakeYour)
    monkeypatch.setattr(backends, "XTTSClone", FakeXtts)
    monkeypatch.setattr(backends, "MMSTTS", FakeMms)
    monkeypatch.setattr(backends, "NLLBTranslator", FakeMT)
    monkeypatch.setattr(eng_mod, "make_stt", lambda cfg: Falso())

    for perfil, esperado in (("clone_fast", "FakeYour"), ("clone", "FakeXtts"), ("fast", "FakeMms")):
        elegidos.clear()
        eng_mod.default_backends(EngineConfig(tts_profile=perfil, reference_wav="voz.wav"))
        assert esperado in [n for n, _ in elegidos], (perfil, elegidos)


def test_el_perfil_clonado_usa_la_muestra_grabada_por_defecto(monkeypatch, tmp_path):
    from interpreter import engine as eng_mod

    monkeypatch.setenv("INTERPRETER_HOME", str(tmp_path))
    assert eng_mod.default_reference() is None
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "mi_voz.wav").write_bytes(b"RIFF")
    assert eng_mod.default_reference().endswith("mi_voz.wav")


def test_sin_muestra_de_voz_el_clonado_pide_grabarla(monkeypatch, tmp_path):
    from interpreter import engine as eng_mod
    from interpreter.stages import backends

    monkeypatch.setenv("INTERPRETER_HOME", str(tmp_path))
    monkeypatch.setattr(eng_mod, "make_stt", lambda cfg: FakeSTT())
    monkeypatch.setattr(backends, "NLLBTranslator", lambda *a, **k: FakeMT())
    with pytest.raises(EngineError, match="Grabar mi voz"):
        eng_mod.default_backends(EngineConfig(tts_profile="clone_fast"))


def test_si_el_clonado_rapido_falla_tambien_se_vuelve_a_la_voz_rapida():
    errores, intentos = [], []

    def fabrica(cfg):
        intentos.append(cfg.tts_profile)
        if cfg.tts_profile == "clone_fast":
            raise RuntimeError("coqui-tts no está instalado")
        return FakeSTT(), FakeMT(), FakeTTS(chunks=1)

    eng = Engine(EngineConfig(tts_profile="clone_fast"), EngineEvents(on_error=errores.append), fabrica)
    eng.load()
    assert intentos == ["clone_fast", "fast"] and eng.state is EngineState.READY
    assert "voz rápida" in errores[0]


# -- salida al navegador (micrófono virtual de Teams web) --------------------------------------------------


class FakeWebMic:
    def __init__(self, on_clients_changed=None):
        self.on_clients_changed = on_clients_changed
        self.running, self.clients, self.pushes, self.flushes, self.stopped = False, 1, [], 0, False

    def start(self):
        self.running = True

    def stop(self):
        self.running, self.stopped = False, True

    def push(self, audio, rate):
        self.pushes.append((len(audio), rate))
        return self.clients

    def flush(self):
        self.flushes += 1


def test_en_modo_navegador_el_audio_va_al_micro_virtual_y_no_a_un_dispositivo():
    creados = []

    def fabrica(cb):
        creados.append(FakeWebMic(cb))
        return creados[0]

    eng = Engine(
        EngineConfig(output_mode="browser"),
        EngineEvents(),
        fake_backends,
        FakeCapture,
        lambda *a, **k: pytest.fail("no debía abrir un dispositivo de salida"),
        webmic_factory=fabrica,
    )
    eng.load()
    holder = {}
    original = eng._capture_factory
    eng._capture_factory = lambda *a, **k: holder.setdefault("cap", original(*a, **k))
    eng.start()
    assert creados and creados[0].running
    holder["cap"].on_segment(seg())
    assert wait_for(lambda: creados[0].pushes)
    eng.stop()
    assert creados[0].flushes >= 1  # al parar se vacía lo pendiente en la extensión
    eng.close()
    assert creados[0].stopped


def test_el_modo_dispositivo_no_arranca_el_servidor_del_navegador():
    creados = []
    eng = Engine(
        EngineConfig(output_mode="device"),
        EngineEvents(),
        fake_backends,
        FakeCapture,
        FakePlayer,
        webmic_factory=lambda cb: creados.append(1) or FakeWebMic(cb),
    )
    eng.load()
    eng.start()
    eng.stop()
    assert creados == []


def test_un_puerto_ocupado_se_informa_como_error_del_motor():
    class Roto(FakeWebMic):
        def start(self):
            raise RuntimeError("No se pudo abrir el puerto 8765: ocupado")

    eng = Engine(
        EngineConfig(output_mode="browser"),
        EngineEvents(),
        fake_backends,
        FakeCapture,
        FakePlayer,
        webmic_factory=lambda cb: Roto(cb),
    )
    eng.load()
    with pytest.raises(EngineError, match="puerto"):
        eng.start()


def test_el_motor_pasa_su_glosario_al_reconocimiento_y_a_la_traduccion():
    from interpreter.glossary import Glossary, GlossaryEntry

    stt = FakeSTT("hola SharePoint")
    stt.glossary = None
    mt = FakeMT({"hola SharePoint": "hello SharePoint"})
    glossary = Glossary([GlossaryEntry("SharePoint")])
    eng = Engine(
        EngineConfig(),
        EngineEvents(),
        lambda cfg: (stt, mt, FakeTTS(chunks=1)),
        lambda *a, **k: FakeCapture(*a, **k),
        lambda *a: FakePlayer(*a),
        glossary=glossary,
    )
    eng.load()
    eng.start()
    try:
        assert stt.glossary is glossary
        # Editar el glosario en caliente (desde la interfaz) llega a las etapas sin reiniciar.
        glossary.replace_with(Glossary([GlossaryEntry("Ford")]))
        assert [e.term for e in stt.glossary.entries] == ["Ford"]
    finally:
        eng.stop()


# -- inglés -> español: qué voz habla qué ---------------------------------------------------------------


def test_cada_perfil_dice_que_idiomas_habla():
    from interpreter.engine import voice_problem

    assert voice_problem("fast", "es") is None and voice_problem("clone", "es") is None
    assert voice_problem("clone_fast", "en") is None and voice_problem("clone_fast", "pt") is None
    msg = voice_problem("clone_fast", "es")
    assert "no habla Español" in msg and "Rápido (voz genérica)" in msg  # sugiere una que sí


def test_inglés_a_español_funciona_con_la_voz_generica_y_se_rechaza_con_yourtts():
    eng, _ = make(cfg=EngineConfig(source="en", target="es", tts_profile="fast"))
    eng.load()
    eng.start()
    eng.stop()
    eng2, _ = make(cfg=EngineConfig(source="en", target="es", tts_profile="clone_fast"))
    eng2.load()
    with pytest.raises(EngineError, match="no habla Español"):
        eng2.start()
    assert eng2.state is EngineState.READY


def test_cambiar_en_caliente_a_un_idioma_que_la_voz_no_habla_se_rechaza():
    eng, _ = make(cfg=EngineConfig(target="en", tts_profile="clone_fast"))
    eng.load()
    eng.start()
    try:
        with pytest.raises(EngineError):
            eng.set_target_language("es")
        assert eng.config.target == "en"
        eng.set_target_language("fr")
        assert eng.config.target == "fr"
    finally:
        eng.stop()
