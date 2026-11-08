"""Backends reales (locales, CPU). Las importaciones pesadas son perezosas para no frenar los tests.

Todos se limitan a paquetes disponibles en el índice corporativo de Ford (ver docs/plans/…-plan…).
Los modelos se descargan a ``models/hf`` (ignorado por git) y después funcionan sin red.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from ..numbers import is_numeric_phrase
from .base import MTBackend, STTBackend, TTSBackend

log = logging.getLogger(__name__)


def app_home() -> Path:
    """Carpeta de datos de la app (modelos y perfiles de voz).

    - ``INTERPRETER_HOME`` si está definida.
    - Ejecutable empaquetado: ``%LOCALAPPDATA%\\Interpreter`` (no dentro de la carpeta de la app).
    - Desarrollo: la raíz del proyecto.
    """
    override = os.environ.get("INTERPRETER_HOME")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Interpreter"
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = app_home()


def use_local_model_cache() -> Path:
    """Dirige la caché de Hugging Face a ``models/hf`` dentro de la carpeta de datos de la app."""
    cache = app_home() / "models" / "hf"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if models_ready_marker().exists():
        # Modelos ya descargados: no consultar Hugging Face (todo local, sin red).
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    return cache


def models_ready_marker() -> Path:
    """Archivo que indica que los modelos ya se descargaron y cargaron correctamente una vez."""
    return app_home() / "models" / ".ready"


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
    """Whisper de Transformers en CPU (modelos ``tiny``/``base``/``small``), con ``generate`` directo.

    No se usa la clase ``pipeline``: añadía ~0,6 s de sobrecarga por frase (medido).
    """

    device = "CPU"
    # Umbrales medidos con voz real, eco en inglés y ruido: la voz en el idioma esperado da
    # p(idioma) ≈ 0,97–1,0 y logprob ≈ -0,1…-0,3; el eco y el ruido dan p(idioma) ≤ 0,02.
    min_language_prob = 0.35
    min_avg_logprob = -1.0
    min_mixed_language_prob = 0.03  # frases con términos del glosario (mezcla de idiomas)
    min_mixed_logprob = -0.8
    min_numeric_logprob = -0.5  # frases que son solo números

    def __init__(
        self, model: str = "openai/whisper-base", threads: int | None = None, quantize: bool = False
    ) -> None:
        use_local_model_cache()
        import torch
        from transformers import WhisperFeatureExtractor, WhisperForConditionalGeneration, WhisperTokenizer

        if threads:
            torch.set_num_threads(threads)
        self._torch = torch
        self.model_name = model
        self._fe = WhisperFeatureExtractor.from_pretrained(model)
        self._tok = WhisperTokenizer.from_pretrained(model)
        self._model = WhisperForConditionalGeneration.from_pretrained(model, dtype=torch.float32).eval()
        if quantize:
            self._model = quantize_int8(self._model)
        self.last_reject: str | None = None  # motivo del último rechazo: "idioma", "confianza" o None
        self.last_stats: dict[str, float] = {}
        self.glossary: Any = None  # interpreter.glossary.Glossary (opcional)
        self.use_prompt = True  # sesgar a Whisper con el glosario (prompt_ids)
        self._prompt_cache: tuple[str, Any] = ("", None)

    def _prompt_ids(self) -> Any:
        """``prompt_ids`` con los términos del glosario, o ``None`` (se recalcula si el glosario cambió)."""
        if self.glossary is None or not self.use_prompt:
            return None
        prompt = self.glossary.initial_prompt()
        if not prompt:
            return None
        if self._prompt_cache[0] != prompt:
            self._prompt_cache = (prompt, self._tok.get_prompt_ids(prompt, return_tensors="pt"))
        return self._prompt_cache[1]

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> str:
        """Texto reconocido, o ``""`` si no parece voz en ``language`` (eco, ruido, alucinación)."""
        audio16 = _resample(audio, sample_rate, 16000)
        feats = self._fe(audio16, sampling_rate=16000, return_tensors="pt").input_features
        max_tokens = int(min(200, audio16.size / 16000 * 8 + 20))  # corta repeticiones descontroladas
        gc = self._model.generation_config
        with self._torch.inference_mode():
            enc = self._model.model.encoder(feats)  # una sola vez: sirve para el idioma y para generar
            start = self._torch.tensor([[gc.decoder_start_token_id]])
            probs = self._torch.softmax(
                self._model(encoder_outputs=enc, decoder_input_ids=start).logits[0, -1].float(), -1
            )
            lang_ids = list(gc.lang_to_id.values())
            target = gc.lang_to_id.get(f"<|{language}|>")
            p_lang = float(probs[target] / probs[lang_ids].sum()) if target is not None else 1.0
            extra: dict[str, Any] = {}
            prompt_ids = self._prompt_ids()
            if prompt_ids is not None:
                extra["prompt_ids"] = prompt_ids
            out = self._model.generate(
                encoder_outputs=enc,
                language=language,
                task="transcribe",
                max_new_tokens=max_tokens,
                return_dict_in_generate=True,
                output_scores=True,
                **extra,
            )
            steps = self._model.compute_transition_scores(out.sequences, out.scores, normalize_logits=True)
            avg_logprob = float(steps[0].mean()) if steps.numel() else 0.0
            text = str(self._tok.batch_decode(out.sequences, skip_special_tokens=True)[0]).strip()
        return self._judge(text, language, p_lang, avg_logprob)

    def _judge(self, text: str, language: str, p_lang: float, avg_logprob: float) -> str:
        """Aplica el glosario y decide si se acepta la frase (vacío = descartada, ver ``last_reject``)."""
        found: list[str] = []
        # Las variantes del glosario son cómo «oye» Whisper un término hablado en ESPAÑOL («fort» por «Ford»):
        # al oír inglés u otro idioma no se corrige, o se estropearían palabras normales.
        if self.glossary is not None and text and language == "es":
            text, found = self.glossary.correct(text)
        self.last_stats = {"p_idioma": p_lang, "logprob": avg_logprob}
        self.last_reject = None
        if is_known_hallucination(text):  # «Thanks for watching»: Whisper la inventa con ruido o silencio
            self.last_reject = "confianza"
            log.info(
                "Reconocimiento: p_idioma=%.2f logprob=%.2f -> alucinación conocida: %s",
                p_lang,
                avg_logprob,
                text,
            )
            return ""
        # Una frase con términos del glosario puede llevar mucho inglés: se tolera una probabilidad de idioma
        # baja si el reconocimiento está seguro de lo que oyó.
        mixed = (
            bool(found) and p_lang >= self.min_mixed_language_prob and avg_logprob >= self.min_mixed_logprob
        )
        # Los números sueltos («uno», «4, 5, 6») no parecen de ningún idioma: se aceptan si se oyen claros.
        numeric = bool(text) and avg_logprob >= self.min_numeric_logprob and is_numeric_phrase(text, language)
        if p_lang < self.min_language_prob and not (mixed or numeric):
            self.last_reject = "idioma"
        elif avg_logprob < self.min_avg_logprob:
            self.last_reject = "confianza"
        log.info(
            "Reconocimiento: p_idioma=%.2f logprob=%.2f términos=%s -> %s: %s",
            p_lang,
            avg_logprob,
            found or "-",
            self.last_reject or "aceptado",
            text[:90],
        )
        return "" if self.last_reject else text


# Frases que Whisper «oye» cuando solo hay ruido o silencio (aprendidas de subtítulos de vídeos de internet).
_HALLUCINATIONS = (
    "thanks for watching", "thank you for watching", "thank you for listening", "please subscribe",
    "like and subscribe", "subtitles by", "subtitles made by", "amara.org", "see you in the next video",
    "gracias por ver", "gracias por mirar", "suscribete", "subtitulos realizados por", "subtitulos por",
    "nos vemos en el proximo video", "obrigado por assistir", "merci d'avoir regarde", "sous-titres",
)  # fmt: skip


def is_known_hallucination(text: str) -> bool:
    """¿Es una de las frases que Whisper inventa con ruido? (nadie la dice en una reunión)."""
    from ..phrasebook import normalize

    plain = normalize(text)
    return any(h in plain for h in _HALLUCINATIONS)


def split_sentences(text: str) -> list[str]:
    """Parte un texto en frases (tras ``. ! ?`` y un espacio). Las cifras («8.30») y siglas no se parten."""
    parts = re.split(r"(?<=[.!?])\s+(?=[\"'¿¡(]?[A-ZÁÉÍÓÚÑ0-9])", text.strip())
    return [p for p in (x.strip() for x in parts) if p] or [text.strip()]


_DASH_CONTINUATION = re.compile(r"(?<=[.!?…])\s+[-–—]\s*\S.*$", re.DOTALL)


def clean_translation(text: str, source_text: str = "") -> str:
    """Limpia lo que NLLB inventa: guion de diálogo inicial y una «segunda réplica» tras un guion.

    Con enunciados muy cortos el modelo sigue generando un diálogo: «Gracias.» -> «Thank you. - Thank you.».
    Si el original no lleva ese guion, lo que viene detrás de «. - » no es traducción: se descarta.
    """
    text = text.strip().lstrip("-–—").strip()
    if source_text and not re.search(r"\s[-–—]\s", source_text):
        text = _DASH_CONTINUATION.sub("", text).strip()
    return text


def openvino_devices() -> list[str]:
    """Dispositivos que OpenVINO ve (``CPU``, ``GPU``, ``NPU``). Vacío si no está instalado."""
    try:
        import openvino as ov

        return list(ov.Core().available_devices)
    except Exception:  # noqa: BLE001 - sin openvino o sin drivers
        return []


class OVWhisperSTT(WhisperSTT):
    """Whisper con el *codificador* en OpenVINO (GPU/NPU/CPU de Intel) y el decodificador en PyTorch.

    El codificador procesa siempre 30 s de espectrograma (forma fija), la parte más pesada para frases
    cortas. Se convierte una vez a ``models/ov`` y las siguientes cargas lo reutilizan. El resto de la
    generación (decodificador, tokenizador, idioma) sigue siendo el de Transformers.
    """

    def __init__(
        self,
        model: str = "openai/whisper-base",
        threads: int | None = None,
        device: str = "GPU",
        precision: str = "f16",
        quantize_decoder: bool = False,
    ) -> None:
        super().__init__(model, threads, quantize=False)
        import openvino as ov
        import torch
        from transformers.modeling_outputs import BaseModelOutput

        self.device, self.precision = device, precision
        if quantize_decoder:  # int8 dinámico solo del decodificador (el codificador va en OpenVINO)
            self._model.model.decoder = quantize_int8(self._model.model.decoder)
            self._model.proj_out = quantize_int8(self._model.proj_out)
        encoder = self._model.model.encoder
        xml = app_home() / "models" / "ov" / f"{model.replace('/', '_')}_encoder_{precision}.xml"
        core = ov.Core()
        if not xml.is_file():
            xml.parent.mkdir(parents=True, exist_ok=True)

            class _EncoderOnly(torch.nn.Module):
                def __init__(self, enc: Any) -> None:
                    super().__init__()
                    self.enc = enc

                def forward(self, features: torch.Tensor) -> torch.Tensor:
                    return self.enc(features).last_hidden_state

            example = torch.zeros(1, encoder.conv1.in_channels, 3000)
            with torch.inference_mode():
                converted = ov.convert_model(_EncoderOnly(encoder), example_input=example)
            ov.save_model(converted, str(xml), compress_to_fp16=precision == "f16")
        compiled = core.compile_model(
            core.read_model(str(xml)), device, {"INFERENCE_PRECISION_HINT": precision}
        )
        self._compiled = compiled
        original_forward = encoder.forward

        def ov_forward(input_features: torch.Tensor, *args: Any, return_dict: bool | None = None, **kw: Any):
            feats = input_features.detach().cpu().numpy().astype(np.float32, copy=False)
            if feats.shape[0] != 1 or feats.shape[-1] != 3000:  # forma no prevista: usa PyTorch
                return original_forward(input_features, *args, return_dict=return_dict, **kw)
            hidden = torch.from_numpy(np.array(compiled(feats)[0], dtype=np.float32))
            if return_dict is False:
                return (hidden,)
            return BaseModelOutput(last_hidden_state=hidden)  # type: ignore[arg-type]

        encoder.forward = ov_forward  # type: ignore[method-assign]


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
        return clean_translation(self._tok.batch_decode(out, skip_special_tokens=True)[0], text)


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


def prepare_reference(reference_wav: str | Path, seconds: float = 10.0) -> Path:
    """Recorta la muestra de voz a ``seconds`` de voz y la deja en ``<datos>/voices`` (mono, 16 bits).

    YourTTS calcula la huella de la voz en cada frase: con una referencia corta es más rápido y la
    calidad de la clonación no mejora con más de ~10 s.
    """
    import soundfile as sf

    audio, sr = sf.read(str(reference_wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame = max(1, sr // 50)  # 20 ms
    n = audio.size // frame
    rms = np.sqrt((audio[: n * frame].reshape(n, frame) ** 2).mean(axis=1)) if n else np.zeros(1)
    voiced = np.flatnonzero(rms > 10 ** (-45 / 20))
    start = int(voiced[0]) * frame if voiced.size else 0
    clip = audio[start : start + int(seconds * sr)]
    out = app_home() / "voices" / "referencia_corta.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), clip, sr, subtype="PCM_16")
    return out


class YourTTSClone(TTSBackend):
    """Perfil *clonado rápido*: YourTTS (VITS multilingüe con clonación) vía ``coqui-tts``.

    Habla en inglés, francés y portugués con el timbre de la muestra del usuario. En CPU genera unas 7
    veces más rápido que el tiempo real (RTF ≈ 0,14), frente a XTTSv2 (RTF ≈ 1,8). Licencia del modelo:
    CC BY-NC-ND 4.0 (uso no comercial, sin obras derivadas: se usa tal cual).
    """

    LANGS = {"en": "en", "fr": "fr-fr", "pt": "pt-br"}

    def __init__(self, reference_wav: str | Path, device: str = "cpu") -> None:
        use_local_model_cache()
        os.environ.setdefault("COQUI_TTS_HOME", str(app_home() / "models" / "tts"))
        warnings.filterwarnings("ignore")
        from TTS.api import TTS  # type: ignore[import-not-found]

        self.reference = str(prepare_reference(reference_wav))
        self._tts = TTS("tts_models/multilingual/multi-dataset/your_tts").to(device)
        self.sample_rate = int(self._tts.synthesizer.output_sample_rate)

    def synthesize(self, text: str, language: str) -> Iterator[np.ndarray]:
        code = self.LANGS.get(language)
        if code is None:
            raise ValueError(
                f"El perfil clonado rápido solo habla inglés, francés y portugués ({language!r})."
            )
        # Frase por frase: la primera se oye mientras se sintetizan las demás (la primera voz sale antes).
        for sentence in split_sentences(text):
            wav = self._tts.tts(text=sentence, speaker_wav=self.reference, language=code)
            yield np.asarray(wav, dtype=np.float32)


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
        os.environ.setdefault("COQUI_TTS_HOME", str(app_home() / "models" / "tts"))
        warnings.filterwarnings("ignore")
        from TTS.api import TTS  # type: ignore[import-not-found]

        self.sample_rate = 24000
        self.reference = str(reference_wav)
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
        self._model = tts.synthesizer.tts_model
        # La huella de la voz se calcula UNA vez al crear el backend, no en cada frase.
        self._gpt_cond_latent, self._speaker_embedding = self._model.get_conditioning_latents(
            audio_path=[self.reference]
        )

    def synthesize(self, text: str, language: str, chunk_size: int = 20) -> Iterator[np.ndarray]:
        """Streaming: cada trozo sale en cuanto está listo, sin esperar a la frase completa."""
        import torch

        with torch.inference_mode():
            for chunk in self._model.inference_stream(
                text,
                language,
                self._gpt_cond_latent,
                self._speaker_embedding,
                stream_chunk_size=chunk_size,
                enable_text_splitting=False,
            ):
                yield chunk.detach().cpu().numpy().astype(np.float32)
