import numpy as np

from interpreter.vad import EnergyVAD, Segmenter, VadConfig

SR = 16000


def tone(ms: int, amp: float = 0.3) -> np.ndarray:
    t = np.arange(SR * ms // 1000) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(ms: int) -> np.ndarray:
    return np.zeros(SR * ms // 1000, dtype=np.float32)


def make() -> Segmenter:
    return Segmenter(EnergyVAD(-42.0), VadConfig())


def test_energy_vad_distingue_voz_de_silencio():
    vad = EnergyVAD(-42.0)
    assert vad.is_speech(tone(32))
    assert not vad.is_speech(silence(32))
    assert not vad.is_speech(np.zeros(0, dtype=np.float32))


def test_un_enunciado_se_cierra_tras_la_pausa():
    seg = make()
    out = seg.feed(silence(300)) + seg.feed(tone(1000)) + seg.feed(silence(800))
    assert len(out) == 1
    assert out[0].sample_rate == SR
    assert 0.8 * SR < out[0].audio.size < 1.6 * SR  # incluye algo de relleno previo


def test_ruido_corto_se_descarta():
    seg = make()
    out = seg.feed(tone(100)) + seg.feed(silence(800))
    assert out == []


def test_dos_enunciados_separados():
    seg = make()
    audio = np.concatenate([silence(200), tone(900), silence(700), tone(700), silence(700)])
    out = seg.feed(audio)
    assert len(out) == 2


def test_feed_en_trozos_pequenos_da_lo_mismo():
    audio = np.concatenate([silence(200), tone(900), silence(700)])
    a = make().feed(audio)
    b_seg = make()
    b = []
    for i in range(0, audio.size, 137):  # tamaño que no divide la trama
        b += b_seg.feed(audio[i : i + 137])
    assert len(a) == len(b) == 1
    assert abs(a[0].audio.size - b[0].audio.size) <= VadConfig().frame_samples


def test_enunciado_larguisimo_se_corta():
    cfg = VadConfig(max_speech_ms=2000)
    seg = Segmenter(EnergyVAD(-42.0), cfg)
    out = seg.feed(tone(5000))
    assert len(out) >= 2


def test_flush_cierra_el_enunciado_en_curso():
    seg = make()
    seg.feed(tone(900))
    assert seg.in_speech
    assert seg.flush() is not None
    assert not seg.in_speech
