import numpy as np

from interpreter.audio.calibrate import analyze_loopback, make_tone
from interpreter.audio.voice_sample import assess_sample, save_sample

SR = 48000


def speechlike(seconds: float, level_db: float = -20.0, noise_db: float = -70.0, sr: int = 16000):
    """Señal con 'voz' (ráfagas de tono modulado) y ruido de fondo controlado."""
    rng = np.random.default_rng(0)
    t = np.arange(int(sr * seconds)) / sr
    env = (np.sin(2 * np.pi * 1.5 * t) > -0.3).astype(np.float64)  # ráfagas con pausas
    voice = env * np.sin(2 * np.pi * 180 * t) * np.sqrt(2) * 10 ** (level_db / 20)
    noise = rng.normal(0, 10 ** (noise_db / 20), t.size)
    return (voice + noise).astype(np.float32)


# -- calibración del bucle ---------------------------------------------------------------------


def _received(gain_db: float, noise_db: float = -75.0):
    tone = make_tone(SR, 1.5)
    rng = np.random.default_rng(1)
    return (tone * 10 ** (gain_db / 20) + rng.normal(0, 10 ** (noise_db / 20), tone.size)).astype(np.float32)


def test_nivel_correcto():
    r = analyze_loopback(_received(0.0), SR)
    assert r.verdict == "ok" and -22 < r.tone_dbfs < -10 and r.snr_db > 30


def test_senal_debil():
    r = analyze_loopback(_received(-25.0), SR)
    assert r.verdict == "bajo"


def test_senal_saturada():
    r = analyze_loopback(np.clip(_received(20.0), -1, 1), SR)
    assert r.verdict == "alto" and r.clipped


def test_sin_senal_solo_ruido():
    rng = np.random.default_rng(2)
    r = analyze_loopback(rng.normal(0, 0.001, SR * 2).astype(np.float32), SR)
    assert r.verdict == "sin_senal"


def test_grabacion_demasiado_corta():
    assert analyze_loopback(np.zeros(100, dtype=np.float32), SR).verdict == "sin_senal"


# -- muestra de voz ----------------------------------------------------------------------------


def test_muestra_buena():
    rep = assess_sample(speechlike(25.0), 16000)
    assert rep.ok and not rep.warnings, rep.warnings


def test_muestra_corta_no_es_valida():
    rep = assess_sample(speechlike(4.0), 16000)
    assert not rep.ok and any("corta" in w.lower() for w in rep.warnings)


def test_muestra_saturada_se_rechaza():
    rep = assess_sample(np.clip(speechlike(25.0) * 40, -1, 1), 16000)
    assert not rep.ok and any("saturaci" in w.lower() for w in rep.warnings)


def test_muestra_con_ruido_avisa():
    rep = assess_sample(speechlike(25.0, noise_db=-40.0), 16000)
    assert any("ruido" in w.lower() for w in rep.warnings)


def test_muestra_vacia():
    assert not assess_sample(np.zeros(0, dtype=np.float32), 16000).ok


def test_guardar_normaliza_el_pico(tmp_path):
    import soundfile as sf

    path = save_sample(speechlike(2.0, level_db=-30.0), 16000, tmp_path / "v" / "mi_voz.wav")
    data, sr = sf.read(path)
    assert sr == 16000 and 0.85 < np.max(np.abs(data)) < 0.92
