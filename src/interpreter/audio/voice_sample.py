"""Muestra de voz para el perfil clonado: evaluación de calidad y guardado.

La clonación (XTTSv2) depende mucho de la referencia: debe ser larga, limpia, sin saturar y con tono de
conversación. ``assess_sample`` revisa eso con reglas simples y devuelve avisos en español.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Texto sugerido para leer (~25 s a ritmo natural): cubre vocales, consonantes y entonación variada.
PROMPT_TEXT = (
    "Hola, buenos días a todos. Hoy quiero repasar con ustedes el plan de trabajo de esta semana. "
    "Primero revisamos las entregas pendientes y después los contenedores que llegan el jueves. "
    "Si algo no queda claro, por favor pregunten sin problema. ¿Les parece bien que empecemos? "
    "Perfecto, entonces vamos paso a paso y confirmamos cada punto antes de seguir."
)

MIN_SECONDS = 10.0
GOOD_SECONDS = 20.0


@dataclass(slots=True)
class SampleReport:
    seconds: float
    level_dbfs: float  # RMS de las partes con voz
    peak: float
    noise_floor_dbfs: float
    ok: bool
    warnings: list[str] = field(default_factory=list)


def _db(x: float) -> float:
    return 20.0 * float(np.log10(max(x, 1e-9)))


def assess_sample(audio: np.ndarray, sample_rate: int) -> SampleReport:
    """Evalúa una grabación mono float32 en [-1, 1]."""
    audio = np.asarray(audio, dtype=np.float32)
    seconds = audio.size / sample_rate if sample_rate else 0.0
    warnings: list[str] = []
    if audio.size == 0:
        return SampleReport(0.0, -90.0, 0.0, -90.0, False, ["La grabación está vacía."])

    frame = max(1, sample_rate // 50)  # 20 ms
    n = audio.size // frame
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1)) if n else np.array([0.0])
    voiced = rms[rms > 10 ** (-45 / 20)]
    level = _db(float(np.sqrt(np.mean(voiced**2)))) if voiced.size else -90.0
    noise = _db(float(np.percentile(rms, 10)))
    peak = float(np.max(np.abs(audio)))

    if seconds < MIN_SECONDS:
        warnings.append(
            f"Muy corta ({seconds:.0f} s): graba al menos {MIN_SECONDS:.0f} s, "
            f"ideal {GOOD_SECONDS:.0f} s o más."
        )
    elif seconds < GOOD_SECONDS:
        warnings.append(f"Algo corta ({seconds:.0f} s): con {GOOD_SECONDS:.0f} s o más la clonación mejora.")
    if peak >= 0.99:
        warnings.append("Hay saturación (el audio llega al máximo): baja el volumen del micrófono o aléjate.")
    if voiced.size == 0 or level < -38:
        warnings.append("Voz muy baja: acércate al micrófono o sube su ganancia.")
    elif level > -12:
        warnings.append("Voz demasiado fuerte: puede distorsionar; aléjate un poco.")
    if noise > -50:
        warnings.append("Hay ruido de fondo notable: graba en un lugar más silencioso.")

    blocking = seconds < MIN_SECONDS or voiced.size == 0 or peak >= 0.99
    return SampleReport(seconds, level, peak, noise, ok=not blocking, warnings=warnings)


def save_sample(audio: np.ndarray, sample_rate: int, path: Path) -> Path:
    """Guarda el wav (PCM 16 bits, mono) normalizando el pico a -1 dBFS si hace falta."""
    import soundfile as sf

    audio = np.asarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio = audio * (0.89 / peak)  # -1 dBFS
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, sample_rate, subtype="PCM_16")
    return path


def record(device: int | None, seconds: float, on_level=None, sample_rate: int | None = None):
    """Graba ``seconds`` del micrófono. Devuelve ``(audio, sample_rate)``. Bloqueante: usar en un hilo."""
    import sounddevice as sd

    info = sd.query_devices(device if device is not None else sd.default.device[0])
    sr = int(sample_rate or info["default_samplerate"])
    chunks: list[np.ndarray] = []

    def cb(indata, frames, time_info, status):  # noqa: ANN001
        chunks.append(indata[:, 0].copy())
        if on_level is not None:
            on_level(float(np.sqrt(np.mean(indata[:, 0] ** 2))))

    with sd.InputStream(device=device, channels=1, samplerate=sr, dtype="float32", callback=cb):
        sd.sleep(int(seconds * 1000))
    return (np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)), sr
