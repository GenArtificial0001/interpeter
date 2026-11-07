import time

import numpy as np
import pytest

from interpreter.engine import Engine, EngineConfig, EngineError, EngineEvents, EngineState, check_devices
from interpreter.messages import SpeechSegment
from interpreter.stages.fakes import FakeMT, FakeSTT, FakeTTS


class FakeCapture:
    def __init__(self, device, segmenter, on_segment, on_level):
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

    def cap(*a):
        holder["cap"] = FakeCapture(*a)
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
