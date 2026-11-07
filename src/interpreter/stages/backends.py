"""Backends reales (locales, CPU). Las importaciones pesadas son perezosas para no frenar los tests.

Todos se limitan a paquetes disponibles en el índice corporativo de Ford (ver docs/plans/…-plan…).
Los modelos se descargan a ``models/hf`` (ignorado por git) y después funcionan sin red.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from .base import MTBackend, STTBackend, TTSBackend

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def use_local_model_cache() -> Path:
    """Dirige la caché de Hugging Face a ``models/hf`` dentro del proyecto."""
    cache = PROJECT_ROOT / "models" / "hf"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    return cache


def quantize_int8(model: Any) -> Any:
    """Cuantización dinámica int8 de las capas lineales (CPU): menos memoria y más velocidad."""
    import torch

    return torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)


def _resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return audio.astype(np.float32, copy=False)
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(sr_in, sr_out)
    return resample_poly(audio, sr_out // g, sr_in // g).astype(np.float32)


# ISO 639-1 -> código FLORES-200 de NLLB
NLLB_CODES = {
    "es": "spa_Latn",
    "en": "eng_Latn",
    "pt": "por_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "it": "ita_Latn",
}
# ISO 639-1 -> código de MMS-TTS (ISO 639-3)
MMS_CODES = {"es": "spa", "en": "eng", "pt": "por", "fr": "fra", "de": "deu"}


class WhisperSTT(STTBackend):
    """Whisper de Hugging Face Transformers en CPU (modelos ``tiny``/``base``/``small``)."""

    def __init__(
        self, model: str = "openai/whisper-base", threads: int | None = None, quantize: bool = False
    ) -> None:
        use_local_model_cache()
        import torch
        from transformers import pipeline

        if threads:
            torch.set_num_threads(threads)
        self.model_name = model
        self._asr = pipeline("automatic-speech-recognition", model=model, device="cpu", dtype=torch.float32)
        if quantize:
            self._asr.model = quantize_int8(self._asr.model)

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> str:
        audio16 = _resample(audio, sample_rate, 16000)
        out: Any = self._asr(
            {"raw": audio16, "sampling_rate": 16000},
            generate_kwargs={"language": language, "task": "transcribe"},
        )
        return str(out["text"])


class NLLBTranslator(MTBackend):
    """NLLB-200 distilled (600M) de Transformers en CPU."""

    def __init__(
        self,
        model: str = "facebook/nllb-200-distilled-600M",
        threads: int | None = None,
        quantize: bool = False,
        beams: int = 2,
    ) -> None:
        use_local_model_cache()
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        if threads:
            torch.set_num_threads(threads)
        self._torch = torch
        self.model_name = model
        self.beams = beams
        self._tok = AutoTokenizer.from_pretrained(model)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(model, dtype=torch.float32).eval()
        if quantize:
            self._model = quantize_int8(self._model)

    def translate(self, text: str, source: str, target: str) -> str:
        self._tok.src_lang = NLLB_CODES[source]
        inputs = self._tok(text, return_tensors="pt")
        with self._torch.inference_mode():
            out = self._model.generate(
                **inputs,
                forced_bos_token_id=self._tok.convert_tokens_to_ids(NLLB_CODES[target]),
                max_new_tokens=int(inputs["input_ids"].shape[1] * 2.5) + 10,
                num_beams=self.beams,
            )
        return self._tok.batch_decode(out, skip_special_tokens=True)[0]


class MMSTTS(TTSBackend):
    """Perfil *rápido*: VITS (MMS-TTS) sin clonación. Un modelo por idioma, cargados bajo demanda."""

    def __init__(self) -> None:
        use_local_model_cache()
        self.sample_rate = 16000
        self._models: dict[str, tuple] = {}

    def _load(self, language: str):
        if language not in self._models:
            import torch
            from transformers import AutoTokenizer, VitsModel

            repo = f"facebook/mms-tts-{MMS_CODES[language]}"
            self._models[language] = (
                AutoTokenizer.from_pretrained(repo),
                VitsModel.from_pretrained(repo, dtype=torch.float32).eval(),
            )
        return self._models[language]

    def synthesize(self, text: str, language: str) -> Iterator[np.ndarray]:
        import torch

        tok, model = self._load(language)
        self.sample_rate = int(model.config.sampling_rate)
        with torch.inference_mode():
            wav = model(**tok(text, return_tensors="pt")).waveform[0].numpy()
        yield wav.astype(np.float32)


class XTTSClone(TTSBackend):
    """Perfil *clonado*: XTTSv2 vía ``coqui-tts``. Requiere aceptar la licencia CPML (no comercial).

    La aceptación es una decisión del usuario: se hace exportando ``COQUI_TOS_AGREED=1`` en su sesión.
    """

    def __init__(self, reference_wav: str | Path, device: str = "cpu") -> None:
        if os.environ.get("COQUI_TOS_AGREED") != "1":
            raise RuntimeError(
                "XTTSv2 usa la licencia Coqui Public Model License (solo uso no comercial). "
                "Si la aceptas, exporta COQUI_TOS_AGREED=1 y vuelve a ejecutar."
            )
        use_local_model_cache()
        os.environ.setdefault("COQUI_TTS_HOME", str(PROJECT_ROOT / "models" / "tts"))
        warnings.filterwarnings("ignore")
        from TTS.api import TTS  # type: ignore[import-not-found]

        self.sample_rate = 24000
        self.reference = str(reference_wav)
        self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

    def synthesize(self, text: str, language: str) -> Iterator[np.ndarray]:
        wav = self._tts.tts(text=text, speaker_wav=self.reference, language=language)
        yield np.asarray(wav, dtype=np.float32)
