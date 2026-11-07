"""Calibración del bucle de audio hacia Teams (salida del jack -> entrada del jack).

Reproduce un tono de 1 kHz por la salida elegida y lo graba por la entrada elegida. Con eso se mide cuánta
señal llega, si satura y cuánto ruido hay, y se aconseja subir o bajar el volumen.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TONE_HZ = 1000.0
TONE_SENT_DBFS = -12.0  # pico del tono enviado


@dataclass(slots=True)
class LoopbackReport:
    tone_dbfs: float  # nivel RMS del tono recibido
    noise_dbfs: float  # nivel RMS de lo que no es el tono
    snr_db: float
    peak: float
    clipped: bool
    verdict: str  # "ok" | "bajo" | "alto" | "sin_senal"
    advice: str


def make_tone(sample_rate: int, seconds: float = 1.5, level_dbfs: float = TONE_SENT_DBFS) -> np.ndarray:
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    tone = 10 ** (level_dbfs / 20.0) * np.sin(2 * np.pi * TONE_HZ * t)
    fade = int(0.05 * sample_rate)  # entrada y salida suaves: sin chasquidos
    env = np.ones_like(tone)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return (tone * env).astype(np.float32)


def _db(x: float) -> float:
    return 20.0 * float(np.log10(max(x, 1e-9)))


def analyze_loopback(recorded: np.ndarray, sample_rate: int) -> LoopbackReport:
    """Analiza lo recibido con detección síncrona a 1 kHz (ignora el primer y último 20 %)."""
    x = np.asarray(recorded, dtype=np.float64)
    if x.size < sample_rate // 2:
        return LoopbackReport(-90.0, -90.0, 0.0, 0.0, False, "sin_senal", "La grabación fue demasiado corta.")
    peak = float(np.max(np.abs(x)))
    seg = x[int(x.size * 0.2) : int(x.size * 0.8)]
    seg = seg - np.mean(seg)
    t = np.arange(seg.size) / sample_rate
    c, s = np.cos(2 * np.pi * TONE_HZ * t), np.sin(2 * np.pi * TONE_HZ * t)
    i_comp, q_comp = 2.0 * np.mean(seg * c), 2.0 * np.mean(seg * s)
    amplitude = float(np.hypot(i_comp, q_comp))
    tone_rms = amplitude / np.sqrt(2.0)
    total_rms = float(np.sqrt(np.mean(seg**2)))
    noise_rms = float(np.sqrt(max(total_rms**2 - tone_rms**2, 0.0)))
    tone_db, noise_db = _db(tone_rms), _db(noise_rms)
    snr = tone_db - noise_db
    clipped = peak >= 0.99

    if tone_db < -60 or snr < 6:
        verdict = "sin_senal"
        advice = (
            "No llega el tono: revisa que el cable esté en la hembra de micrófono del divisor y que la "
            "entrada elegida sea la del jack."
        )
    elif clipped or tone_db > -6:
        verdict = "alto"
        advice = "La señal satura: baja el volumen de la salida o la ganancia del micrófono."
    elif tone_db < -30:
        verdict = "bajo"
        advice = "La señal es débil: sube el volumen de la salida o la ganancia del micrófono."
    else:
        verdict = "ok"
        advice = "Nivel correcto para Teams."
    return LoopbackReport(tone_db, noise_db, snr, peak, clipped, verdict, advice)


def run_loopback_test(output_device: int | None, input_device: int | None, seconds: float = 1.5):
    """Reproduce el tono y graba a la vez. Bloqueante: usar en un hilo. Devuelve ``LoopbackReport``."""
    import sounddevice as sd

    sr = 48000
    tone = make_tone(sr, seconds)
    rec = sd.playrec(
        tone.reshape(-1, 1),
        samplerate=sr,
        channels=1,
        dtype="float32",
        device=(input_device, output_device),
    )
    sd.wait()
    return analyze_loopback(rec[:, 0], sr)
