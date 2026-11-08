import threading
import time

import numpy as np
import scipy.signal  # noqa: F401  (se precarga: importarlo dentro del hilo bajo carga lo retrasa)

from interpreter.audio.io import _FIFO, Player
from interpreter.messages import AudioChunk


class FakeStream:
    """Simula el hilo de PortAudio: llama al callback cada pocos ms y guarda lo que «sonaría»."""

    def __init__(self, owner, samplerate, channels, callback, latency):
        self.owner, self.samplerate, self.channels = owner, samplerate, channels
        self.callback, self.latency = callback, latency
        self.frames = 480  # 10 ms a 48 kHz
        self.out = []
        self._run = False
        self._thread = None

    def start(self):
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._run:
            buf = np.zeros((self.frames, self.channels), dtype=np.float32)
            self.callback(buf, self.frames, None, None)
            self.out.append(buf)
            time.sleep(0.005)

    def stop(self):
        self._run = False
        if self._thread is not None:
            self._thread.join(2.0)

    def close(self): ...

    def sound(self):
        """Todo lo emitido, concatenado (frames × canales)."""
        return np.concatenate(self.out) if self.out else np.zeros((0, self.channels), dtype=np.float32)


class FakeSD:
    """Simula WASAPI en modo compartido: solo acepta la frecuencia del dispositivo (48 kHz)."""

    class default:
        device = (0, 0)

    def __init__(self, device_rate=48000, max_out=2):
        self.device_rate = device_rate
        self.max_out = max_out
        self.streams = []

    def query_devices(self, device):
        return {"default_samplerate": float(self.device_rate), "max_output_channels": self.max_out}

    def OutputStream(self, device, channels, samplerate, dtype, latency, callback):  # noqa: N802
        if samplerate != self.device_rate:
            raise OSError(f"Invalid sample rate {samplerate}")
        st = FakeStream(self, samplerate, channels, callback, latency)
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


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _sonido_util(stream):
    """Muestras no nulas del canal izquierdo (el silencio de relleno se descarta)."""
    sound = stream.sound()[:, 0]
    return sound[np.abs(sound) > 1e-6]


def test_remuestrea_16k_y_24k_a_la_frecuencia_del_dispositivo():
    sd = FakeSD(48000)
    p = Player(None, _feeder([_chunk(16000), _chunk(24000)]), sd_module=sd)
    p.start()
    assert _wait(lambda: p.played_chunks == 2)
    st = sd.streams[0]
    assert _wait(lambda: len(_sonido_util(st)) >= 48000)  # 2 × 0,5 s a 48 kHz
    p.stop()
    util = _sonido_util(st)
    assert st.samplerate == 48000 and st.latency == Player.LATENCY_S
    assert len(util) == 48000  # ni se pierde ni se inventa audio
    assert abs(float(np.mean(util))) > 0.05  # la señal sobrevive al remuestreo


def test_sale_en_estereo_con_el_mismo_audio_en_ambos_canales():
    sd = FakeSD(48000, max_out=2)
    p = Player(None, _feeder([_chunk(24000)]), sd_module=sd)
    p.start()
    st = None
    assert _wait(lambda: sd.streams)
    st = sd.streams[0]
    assert _wait(lambda: len(_sonido_util(st)) >= 24000)
    p.stop()
    sound = st.sound()
    assert st.channels == 2
    assert np.array_equal(sound[:, 0], sound[:, 1])


def test_dispositivo_mono_usa_un_solo_canal():
    sd = FakeSD(48000, max_out=1)
    p = Player(None, _feeder([_chunk(24000)]), sd_module=sd)
    p.start()
    assert _wait(lambda: sd.streams)
    assert _wait(lambda: p.played_chunks == 1)
    p.stop()
    assert sd.streams[0].channels == 1


def test_sin_audio_pendiente_el_callback_entrega_silencio_sin_bloquear():
    fifo = _FIFO()
    assert not fifo.pull(480).any()
    fifo.push(np.full(100, 0.5, dtype=np.float32))
    out = fifo.pull(480)
    assert out[:100].tolist() == [0.5] * 100 and not out[100:].any()


def test_fifo_respeta_el_orden_y_trocea_sin_perder_muestras():
    fifo = _FIFO()
    fifo.push(np.arange(0, 5, dtype=np.float32))
    fifo.push(np.arange(5, 8, dtype=np.float32))
    assert fifo.pull(3).tolist() == [0, 1, 2]
    assert fifo.pull(3).tolist() == [3, 4, 5]
    assert fifo.pull(4).tolist() == [6, 7, 0, 0]


def test_fifo_clear_descarta_lo_pendiente():
    fifo = _FIFO()
    fifo.push(np.ones(50, dtype=np.float32))
    fifo.clear()
    assert not fifo.pull(50).any()


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


def test_busy_until_cubre_lo_encolado_mas_la_cola_de_eco():
    from interpreter.audio.io import ECHO_TAIL_S

    sd = FakeSD(48000)
    p = Player(None, _feeder([_chunk(16000, 0.5), _chunk(24000, 0.5)]), sd_module=sd)
    antes = time.monotonic()
    p.start()
    assert _wait(lambda: p.played_chunks == 2)
    p.stop()
    # 1,0 s de audio en total + la cola de eco, contado desde que se encoló el primer trozo
    assert p.busy_until >= antes + 1.0 + ECHO_TAIL_S - 0.05
    assert p.busy_until <= time.monotonic() + 1.0 + ECHO_TAIL_S
