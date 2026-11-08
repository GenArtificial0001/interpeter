import io
import wave

import numpy as np

from interpreter import sound


def test_el_sonido_de_listo_es_un_wav_valido_corto_y_no_muy_fuerte():
    data = sound.make_ready_chime()
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        seconds = w.getnframes() / w.getframerate()
    assert 0.6 < seconds < 1.2  # corto: se oye y termina
    peak = float(np.max(np.abs(frames)))
    assert 0.15 < peak <= 0.27  # ≈ -12 dBFS: audible pero no asusta con auriculares
    assert abs(frames[-1]) < 0.01  # termina en silencio (sin clic)
    assert float(np.sqrt(np.mean(frames**2))) > 0.01  # no es silencio


def test_el_sonido_es_siempre_el_mismo():
    assert sound.make_ready_chime() == sound.make_ready_chime()


def test_fuera_de_windows_o_si_falla_no_rompe(monkeypatch):
    monkeypatch.setattr(sound.os, "name", "posix")
    sound.play_wav(b"RIFF")  # no hace nada
    monkeypatch.setattr(sound.os, "name", "nt")
    sound.play_wav(b"no es un wav")  # winsound puede fallar: se traga el error
