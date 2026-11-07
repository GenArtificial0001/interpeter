import time

import numpy as np

from interpreter.audio.io import Player
from interpreter.messages import AudioChunk


class FakeStream:
    def __init__(self, owner, samplerate):
        self.owner, self.samplerate = owner, samplerate
        self.writes = []

    def start(self): ...
    def stop(self): ...
    def close(self): ...

    def write(self, data):
        self.writes.append(data)


class FakeSD:
    """Simula WASAPI en modo compartido: solo acepta la frecuencia del dispositivo (48 kHz)."""

    class default:
        device = (0, 0)

    def __init__(self, device_rate=48000):
        self.device_rate = device_rate
        self.streams = []

    def query_devices(self, device):
        return {"default_samplerate": float(self.device_rate)}

    def OutputStream(self, device, channels, samplerate, dtype):  # noqa: N802
        if samplerate != self.device_rate:
            raise OSError(f"Invalid sample rate {samplerate}")
        st = FakeStream(self, samplerate)
        self.streams.append(st)
        return st


def _chunk(sr, seconds=0.5):
    return AudioChunk(np.full(int(sr * seconds), 0.1, dtype=np.float32), sr)


def _feeder(chunks):
    items = list(chunks)

    def get(timeout):
        if items:
            return items.pop(0)
        time.sleep(min(timeout, 0.02))
        return None

    return get


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_remuestrea_16k_y_24k_a_la_frecuencia_del_dispositivo():
    sd = FakeSD(48000)
    p = Player(None, _feeder([_chunk(16000), _chunk(24000)]), sd_module=sd)
    p.start()
    assert _wait(lambda: p.played_chunks == 2)
    p.stop()
    escrito = sd.streams[0].writes
    assert sd.streams[0].samplerate == 48000
    assert [len(w) for w in escrito] == [24000, 24000]  # 0,5 s a 48 kHz cada uno
    assert abs(float(np.mean(escrito[0]))) > 0.05  # la señal sobrevive al remuestreo


def test_un_error_del_dispositivo_se_informa_en_vez_de_perderse():
    class Roto(FakeSD):
        def OutputStream(self, *a, **k):  # noqa: N802
            raise OSError("dispositivo ocupado")

    errores = []
    p = Player(None, _feeder([_chunk(16000)]), on_error=errores.append, sd_module=Roto())
    p.start()
    assert _wait(lambda: errores)
    p.stop()
    assert "dispositivo ocupado" in errores[0] and "salida elegida" in errores[0]
