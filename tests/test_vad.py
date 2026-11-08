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


# -- detector adaptativo ------------------------------------------------------------------------------


def _db_tone(db, ms=1000):
    t = np.arange(SR * ms // 1000) / SR
    return (np.sqrt(2) * 10 ** (db / 20) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _ruido(db, ms, seed=0):
    return np.random.default_rng(seed).normal(0, 10 ** (db / 20), SR * ms // 1000).astype(np.float32)


def test_adaptativo_detecta_voz_baja_con_microfono_muy_silencioso():
    from interpreter.vad import AdaptiveEnergyVAD

    # auricular Bluetooth: ruido -95 dBFS y voz a -48 dBFS (el umbral fijo de -42 no la vería)
    seg = Segmenter(AdaptiveEnergyVAD(), VadConfig())
    audio = np.concatenate([_ruido(-95, 1500), _db_tone(-48, 1200), _ruido(-95, 900, 1)])
    assert len(seg.feed(audio)) == 1
    assert Segmenter(EnergyVAD(-42.0), VadConfig()).feed(audio) == []  # el fijo no la detecta


def test_adaptativo_no_se_dispara_con_ruido_constante():
    from interpreter.vad import AdaptiveEnergyVAD

    seg = Segmenter(AdaptiveEnergyVAD(), VadConfig())
    assert seg.feed(_ruido(-66, 8000)) == []


def test_adaptativo_el_umbral_sigue_al_ruido_y_respeta_los_limites():
    from interpreter.vad import AdaptiveEnergyVAD

    vad = AdaptiveEnergyVAD()
    for i in range(0, SR * 3, 512):  # 3 s de ruido muy bajo
        vad.is_speech(_ruido(-100, 32, i)[:512])
    assert vad.threshold_db == vad.min_db  # nunca por debajo del mínimo
    vad2 = AdaptiveEnergyVAD(initial_floor_db=-20.0)
    assert vad2.threshold_db == vad2.max_db  # nunca por encima del máximo


def test_un_enunciado_largo_se_cierra_en_la_primera_pausa_corta_pasados_5_segundos():
    seg = make()
    # 6 s de voz, una pausa de 250 ms (menor que la pausa normal de 480 ms) y más voz
    out = seg.feed(silence(300)) + seg.feed(tone(6000)) + seg.feed(silence(250)) + seg.feed(tone(2000))
    assert len(out) == 1  # se cerró en la pausa corta, sin esperar los 480 ms
    assert out[0].audio.size > 5.5 * SR
    rest = seg.feed(silence(800))
    assert len(rest) == 1  # y lo que siguió es otro enunciado


def test_una_pausa_corta_antes_de_5_segundos_no_parte_la_frase():
    seg = make()
    out = seg.feed(silence(300)) + seg.feed(tone(3000)) + seg.feed(silence(250)) + seg.feed(tone(2000))
    assert out == []  # sigue siendo el mismo enunciado


def test_el_corte_forzado_no_se_come_el_final_de_la_voz():
    seg = make()
    out = seg.feed(silence(300)) + seg.feed(tone(14000))  # sin una sola pausa
    assert len(out) >= 1
    assert out[0].audio.size >= 11.9 * SR  # antes se recortaban 240 ms de voz real al cortar
