"""Sonido de «listo»: un acorde ascendente futurista, generado por código (sin archivos de audio ni paquetes).

Avisa con un sonido corto cuando terminaron de cargar los modelos y ya se puede usar el intérprete.
Suena por la salida predeterminada de Windows (los auriculares), NUNCA por el micrófono virtual de la reunión.
"""

from __future__ import annotations

import io
import logging
import os
import wave

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100
PEAK = 0.25  # ≈ -12 dBFS: se oye bien con auriculares sin asustar


def make_ready_chime(sample_rate: int = SAMPLE_RATE) -> bytes:
    """WAV mono de 16 bits de ~0,9 s: arpegio ascendente de tres notas con brillo metálico y un eco corto."""
    duration = 0.9
    t = np.arange(int(sample_rate * duration)) / sample_rate
    out = np.zeros_like(t)
    for freq, start in ((523.25, 0.0), (783.99, 0.11), (1174.66, 0.22)):  # do5 → sol5 → re6
        n = np.clip(t - start, 0.0, None)
        env = np.where(t >= start, np.exp(-n * 5.5) * (1.0 - np.exp(-n * 220.0)), 0.0)
        tone = (
            np.sin(2 * np.pi * freq * n)
            + 0.35 * np.sin(2 * np.pi * 2 * freq * n)
            + 0.18 * np.sin(2 * np.pi * 3 * freq * n + 0.5)
            + 0.12 * np.sin(2 * np.pi * freq * 1.005 * n)  # ligero desafinado: brillo «sintético»
        )
        out += env * tone
    delay = int(0.18 * sample_rate)  # eco corto
    echo = np.zeros_like(out)
    echo[delay:] = out[:-delay] * 0.35
    out = out + echo
    fade = int(0.05 * sample_rate)
    out[-fade:] *= np.linspace(1.0, 0.0, fade)
    out *= PEAK / max(1e-9, float(np.max(np.abs(out))))
    pcm = (out * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def play_wav(data: bytes) -> None:
    """Reproduce un WAV en memoria por la salida predeterminada de Windows, sin bloquear."""
    if os.name != "nt":
        return
    try:
        import winsound

        winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC)
    except Exception:  # noqa: BLE001 - un sonido que no suena nunca debe romper la aplicación
        log.debug("No se pudo reproducir el sonido", exc_info=True)
