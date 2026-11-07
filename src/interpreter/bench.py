"""Benchmark de las etapas en la PC real (hito 0).

Mide, por frase: STT, traducción y TTS (tiempo hasta el primer audio y total), más el factor de tiempo
real (RTF = tiempo de cómputo / duración del audio). Las definiciones siguen las de ``speech-to-speech``.

Uso:
    python -m interpreter.bench --stt openai/whisper-base --tts mms --target en
    python -m interpreter.bench --tts xtts --ref data/mi_voz.wav     # requiere COQUI_TOS_AGREED=1

Sin ``--wav`` el audio de entrada se sintetiza con MMS-TTS en español (sirve para medir tiempos, no para
juzgar la calidad del reconocimiento; para eso usa ``--wav`` con tu voz).
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

from .stages.backends import MMSTTS, NLLBTranslator, WhisperSTT, XTTSClone, use_local_model_cache

FRASES_ES = [
    "Buenos días a todos.",
    "Necesito que revisemos el plan de entregas de esta semana.",
    "Creo que el problema está en la secuencia de los contenedores, pero lo quiero confirmar con los datos.",
    "¿Pueden compartir la pantalla para ver el reporte actualizado?",
]


def _timed(fn, *a, **k):
    t0 = time.perf_counter()
    r = fn(*a, **k)
    return r, time.perf_counter() - t0


def _load(label: str, factory):
    obj, dt = _timed(factory)
    print(f"  [carga] {label}: {dt:.1f} s", flush=True)
    return obj, dt


def _first_and_total(tts, text: str, lang: str) -> tuple[float, float, float, int]:
    t0 = time.perf_counter()
    first = None
    samples = 0
    for chunk in tts.synthesize(text, lang):
        if first is None:
            first = time.perf_counter() - t0
        samples += chunk.size
    total = time.perf_counter() - t0
    dur = samples / tts.sample_rate
    return first or total, total, dur, samples


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stt", default="openai/whisper-base")
    ap.add_argument("--mt", default="facebook/nllb-200-distilled-600M")
    ap.add_argument("--tts", choices=["mms", "xtts"], default="mms")
    ap.add_argument("--source", default="es")
    ap.add_argument("--target", default="en")
    ap.add_argument("--ref", help="wav de referencia de tu voz (para --tts xtts)")
    ap.add_argument("--wav", help="wav propio con voz en el idioma de entrada (en vez de la sintética)")
    ap.add_argument("--threads", type=int, default=None, help="hilos de torch (por defecto: todos)")
    ap.add_argument("--quantize", action="store_true", help="int8 dinámico en STT y traducción")
    ap.add_argument("--beams", type=int, default=2, help="haces de la traducción (1 = voraz)")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="data/benchmark.json")
    args = ap.parse_args(argv)

    use_local_model_cache()
    print(f"Equipo: {platform.processor()} | Python {platform.python_version()}", flush=True)
    result: dict = {"args": vars(args), "frases": [], "carga": {}}

    print("Cargando modelos…", flush=True)
    stt, result["carga"]["stt"] = _load(args.stt, lambda: WhisperSTT(args.stt, args.threads, args.quantize))
    mt, result["carga"]["mt"] = _load(
        args.mt, lambda: NLLBTranslator(args.mt, args.threads, args.quantize, args.beams)
    )
    if args.tts == "xtts":
        if not args.ref:
            print("--tts xtts necesita --ref con un wav de tu voz", file=sys.stderr)
            return 2
        tts, result["carga"]["tts"] = _load("xtts_v2", lambda: XTTSClone(args.ref))
    else:
        tts, result["carga"]["tts"] = _load("mms-tts", MMSTTS)
    src_tts = MMSTTS()  # para sintetizar el audio de entrada

    rows = []
    for frase in FRASES_ES:
        if args.wav:
            import soundfile as sf

            audio, sr = sf.read(args.wav, dtype="float32")
            audio = audio if audio.ndim == 1 else audio.mean(axis=1)
            texto_ref = "(wav propio)"
        else:
            audio = next(iter(src_tts.synthesize(frase, args.source)))
            sr = src_tts.sample_rate
            texto_ref = frase
        dur_in = audio.size / sr
        for rep in range(args.repeat):
            texto, t_stt = _timed(stt.transcribe, audio, sr, args.source)
            trad, t_mt = _timed(mt.translate, texto.strip() or frase, args.source, args.target)
            first, total, dur_out, _ = _first_and_total(tts, trad, args.target)
            e2e = t_stt + t_mt + first
            row = {
                "frase": texto_ref,
                "rep": rep,
                "dur_in_s": round(dur_in, 2),
                "stt_s": round(t_stt, 3),
                "mt_s": round(t_mt, 3),
                "tts_primer_audio_s": round(first, 3),
                "tts_total_s": round(total, 3),
                "dur_out_s": round(dur_out, 2),
                "e2e_s": round(e2e, 3),
                "rtf_tts": round(total / dur_out, 2) if dur_out else None,
                "texto_stt": texto.strip(),
                "traduccion": trad.strip(),
            }
            rows.append(row)
            tag = "(calentamiento)" if rep == 0 and args.repeat > 1 else ""
            print(
                f"  e2e={e2e:5.2f}s  stt={t_stt:4.2f} mt={t_mt:4.2f} tts1={first:4.2f}/{total:4.2f} "
                f"rtf={row['rtf_tts']}  {tag}\n      «{texto.strip()}» → «{trad.strip()}»",
                flush=True,
            )
    result["frases"] = rows

    medidas = [r for r in rows if r["rep"] > 0] or rows
    result["resumen"] = {
        k: round(statistics.median(r[k] for r in medidas), 3)
        for k in ("stt_s", "mt_s", "tts_primer_audio_s", "tts_total_s", "e2e_s")
    }
    print("\nMediana (sin calentamiento):", json.dumps(result["resumen"], ensure_ascii=False), flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Guardado en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
