"""Valida el codificador de Whisper en OpenVINO frente a PyTorch: calidad y tiempo.

Genera voz sintética en español (MMS-TTS) con variantes (velocidad y ruido) y compara las transcripciones
de PyTorch (referencia) y de OpenVINO en cada dispositivo/precisión. Informa de coincidencias exactas, de
la distancia de caracteres (CER) y de los tiempos.

Uso:  python scripts/validate_ov.py [--model openai/whisper-base] [--devices GPU CPU]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import unicodedata

import numpy as np

FRASES = [
    "Buenos días a todos, vamos a empezar la reunión.",
    "Necesito que revisemos el plan de entregas de esta semana.",
    "El camión llega el jueves a las ocho de la mañana al muelle tres.",
    "Creo que el problema está en la secuencia de los contenedores.",
    "¿Pueden compartir la pantalla para ver el reporte actualizado?",
    "Tenemos doscientos cuarenta y cinco pallets pendientes de ubicar.",
    "Voy a enviar el resumen por correo después de la llamada.",
    "No estoy de acuerdo con esa fecha, es demasiado ajustada.",
    "Gracias por la aclaración, ahora está todo mucho más claro.",
    "Si no hay más preguntas, damos por terminada la reunión.",
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if c.isalnum() or c == " ").strip()


def cer(ref: str, hyp: str) -> float:
    a, b = _norm(ref), _norm(hyp)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / len(a)


def variantes(audio: np.ndarray, sr: int) -> dict[str, np.ndarray]:
    from scipy.signal import resample_poly

    rng = np.random.default_rng(0)
    rms = float(np.sqrt(np.mean(audio**2)))
    ruido = audio + rng.normal(0, rms / 10 ** (20 / 20), audio.size).astype(np.float32)  # SNR 20 dB
    return {
        "normal": audio,
        "rápida": resample_poly(audio, 9, 10).astype(np.float32),
        "lenta": resample_poly(audio, 11, 10).astype(np.float32),
        "ruido": ruido.astype(np.float32),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/whisper-base")
    ap.add_argument(
        "--variants",
        nargs="+",
        default=["GPU:f16:0", "GPU:f16:1"],
        help="DISPOSITIVO:precisión:cuantizar_decodificador, p. ej. GPU:f16:1",
    )
    ap.add_argument("--frases", type=int, default=len(FRASES))
    args = ap.parse_args()

    from interpreter.stages.backends import MMSTTS, OVWhisperSTT, WhisperSTT, openvino_devices

    print("Dispositivos OpenVINO:", openvino_devices(), flush=True)
    tts = MMSTTS()
    clips: list[tuple[str, str, np.ndarray]] = []
    for f in FRASES[: args.frases]:
        audio = next(iter(tts.synthesize(f, "es")))
        for name, a in variantes(audio, tts.sample_rate).items():
            clips.append((f, name, a))
    sr = tts.sample_rate
    print(f"{len(clips)} clips ({args.frases} frases x 4 variantes)\n", flush=True)

    ref_stt = WhisperSTT(args.model)
    ref_stt.transcribe(clips[0][2], sr, "es")  # calentamiento
    ref_txt, t_ref = [], []
    for _, _, a in clips:
        t0 = time.perf_counter()
        ref_txt.append(ref_stt.transcribe(a, sr, "es"))
        t_ref.append(time.perf_counter() - t0)
    cer_ref = statistics.mean(cer(f, h) for (f, _, _), h in zip(clips, ref_txt, strict=True))
    print(
        f"PyTorch CPU (referencia): mediana {statistics.median(t_ref) * 1000:5.0f} ms/clip | CER vs texto original {cer_ref:.3f}"
    )

    ok = True
    for variant in args.variants:
        dev, prec, qd = variant.split(":")
        label = f"{dev:<3} {prec} {'+int8 dec' if qd == '1' else ''}".strip()
        if True:
            try:
                stt = OVWhisperSTT(args.model, device=dev, precision=prec, quantize_decoder=qd == "1")
                stt.transcribe(clips[0][2], sr, "es")
            except Exception as exc:  # noqa: BLE001
                print(f"OpenVINO {label}: no disponible ({str(exc)[:90]})")
                continue
            txt, ts = [], []
            for _, _, a in clips:
                t0 = time.perf_counter()
                txt.append(stt.transcribe(a, sr, "es"))
                ts.append(time.perf_counter() - t0)
            same = sum(_norm(x) == _norm(y) for x, y in zip(txt, ref_txt, strict=True))
            drift = statistics.mean(cer(r, h) for r, h in zip(ref_txt, txt, strict=True))
            cer_txt = statistics.mean(cer(f, h) for (f, _, _), h in zip(clips, txt, strict=True))
            med = statistics.median(ts)
            print(
                f"OpenVINO {label:<16}: mediana {med * 1000:5.0f} ms/clip ({statistics.median(t_ref) / med:3.1f}x) | "
                f"idéntico a PyTorch {same}/{len(clips)} | desvío {drift:.3f} | CER vs original {cer_txt:.3f}",
                flush=True,
            )
            if same < len(clips):
                for (f, name, _), r, h in zip(clips, ref_txt, txt, strict=True):
                    if _norm(r) != _norm(h):
                        print(f"    · [{name}] torch: «{r.strip()}»\n      ov:    «{h.strip()}»")
                        break
            ok &= drift < 0.05
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
