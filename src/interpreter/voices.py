"""Voces genéricas (sin clonar): catálogo por idioma y el motor que las reproduce.

El perfil «Rápido (voz genérica)» ofrece varias voces de **hombre** y de **mujer** por idioma, de cuatro
orígenes:

- **MMS** (Meta): la voz de siempre; es de hombre. Solo español e inglés (los modelos de portugués y francés
  no vienen descargados).
- **YourTTS con voces incorporadas** (inglés, francés, portugués): una mujer en los tres y hombres en inglés
  y portugués (los hombres en francés salían ininteligibles y se quitaron). Muy rápidas.
- **Coqui** (``css10``): hombre en español y en francés, muy rápido.
- **Voces de Windows** (SAPI): las que tenga instaladas el equipo (en este: Helena y Sabina en español, David
  y Zira en inglés). Sin paquetes ni permisos de administrador; el género lo informa el propio Windows.

El tono de cada voz se midió (mediana de F0): hombre < 165 Hz < mujer.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import queue
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np

from .stages.base import TTSBackend

log = logging.getLogger(__name__)

HOMBRE, MUJER = "hombre", "mujer"
YOURTTS_CODES = {"en": "en", "fr": "fr-fr", "pt": "pt-br"}
CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class Voice:
    id: str  # estable: se guarda en los ajustes
    label: str
    gender: str  # HOMBRE | MUJER
    langs: tuple[str, ...]
    engine: str  # "mms" | "yourtts" | "coqui" | "sapi"
    ref: str = ""  # modelo, nombre del hablante o de la voz de Windows
    license: str | None = None  # perfil cuya licencia hay que aceptar (YourTTS: «clone_fast»)

    @property
    def title(self) -> str:
        return f"{'Mujer' if self.gender == MUJER else 'Hombre'} · {self.label}"


@dataclass(frozen=True)
class SapiVoice:
    name: str
    culture: str  # «es-ES»
    gender: str  # HOMBRE | MUJER


_STATIC = (
    Voice("mms", "MMS (estándar)", HOMBRE, ("es", "en"), "mms"),
    Voice("coqui:es", "Coqui", HOMBRE, ("es",), "coqui", "tts_models/es/css10/vits"),
    Voice("coqui:fr", "Coqui", HOMBRE, ("fr",), "coqui", "tts_models/fr/css10/vits"),
    Voice("yourtts:male-pt-3", "YourTTS A", HOMBRE, ("en", "pt"), "yourtts", "male-pt-3", "clone_fast"),
    Voice("yourtts:male-en-2", "YourTTS B", HOMBRE, ("en",), "yourtts", "male-en-2", "clone_fast"),
    Voice(
        "yourtts:female-pt-4", "YourTTS", MUJER, ("en", "fr", "pt"), "yourtts", "female-pt-4", "clone_fast"
    ),
)


def voices_for(language: str, sapi: list[SapiVoice] | None = None) -> list[Voice]:
    """Voces disponibles para hablar ``language``, la predeterminada primero (la de siempre en es/en)."""
    voices = [v for v in _STATIC if language in v.langs]
    for s in sapi or []:
        if s.culture.lower().split("-")[0] == language:
            short = s.name.replace("Microsoft ", "").replace(" Desktop", "")
            voices.append(
                Voice(f"sapi:{s.name}", f"{short} (Windows)", s.gender, (language,), "sapi", s.name)
            )
    # Predeterminada: MMS donde existe (no cambia lo que usaba el usuario); si no, un hombre; orden estable.
    voices.sort(key=lambda v: (v.id != "mms", v.gender != HOMBRE, v.engine == "sapi"))
    return voices


def resolve_voice(voice_id: str, language: str, sapi: list[SapiVoice] | None = None) -> Voice | None:
    """La voz ``voice_id`` si habla ``language``; si no (o con ``"auto"``), la predeterminada del idioma."""
    options = voices_for(language, sapi)
    for v in options:
        if v.id == voice_id:
            return v
    return options[0] if options else None


# -- voces de Windows (SAPI) -----------------------------------------------------------------------------

_LIST_PS = (
    "Add-Type -AssemblyName System.Speech;"
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
    "ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture.Name + '|' + $_.VoiceInfo.Gender }"
)
_sapi_cache: list[SapiVoice] | None = None
_sapi_lock = threading.Lock()


def parse_sapi_list(output: str) -> list[SapiVoice]:
    voices = []
    for line in output.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[0]:
            gender = MUJER if parts[2].lower().startswith("f") else HOMBRE
            voices.append(SapiVoice(parts[0], parts[1], gender))
    return voices


def installed_sapi_voices(refresh: bool = False) -> list[SapiVoice]:
    """Voces de Windows instaladas (se consulta una vez; tarda ~1 s). Vacío fuera de Windows o si falla."""
    global _sapi_cache
    with _sapi_lock:
        if _sapi_cache is not None and not refresh:
            return _sapi_cache
        voices: list[SapiVoice] = []
        if os.name == "nt":
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", _LIST_PS],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20,
                    creationflags=CREATE_NO_WINDOW,
                )
                voices = parse_sapi_list(out.stdout)
            except Exception:  # noqa: BLE001 - sin voces de Windows no pasa nada: quedan las demás
                log.warning("No se pudieron consultar las voces de Windows", exc_info=True)
        _sapi_cache = voices
        return voices


class SapiWorker:
    """Un PowerShell persistente que habla con una voz de Windows y devuelve el audio (síntesis en ~20 ms)."""

    _SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
[Console]::OutputEncoding = [Text.Encoding]::UTF8
while (($line = [Console]::In.ReadLine()) -ne $null) {
  $p = $line.Split("|")
  try {
    if ($p[0] -eq "VOICE") { $s.SelectVoice($p[1]); "ok"; continue }
    $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p[1]))
    $s.SetOutputToWaveFile($p[0]); $s.Speak($text); $s.SetOutputToNull(); "ok"
  } catch { "error: " + $_.Exception.Message }
}
"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._voice = ""

    def _start(self) -> None:
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", self._SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        self._lines = queue.Queue()
        self._voice = ""
        proc = self._proc

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._lines.put(line.strip())
            self._lines.put("")  # el proceso terminó

        threading.Thread(target=reader, name="sapi-lector", daemon=True).start()

    def _ask(self, command: str, timeout: float = 15.0) -> str:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(command + "\n")
        self._proc.stdin.flush()
        reply = self._lines.get(timeout=timeout)
        if reply != "ok":
            raise RuntimeError(f"La voz de Windows falló: {reply or 'el proceso terminó'}")
        return reply

    def speak(self, voice: str, text: str) -> tuple[np.ndarray, int]:
        import soundfile as sf

        with self._lock:
            for attempt in (1, 2):  # si el PowerShell murió, se reinicia una vez
                try:
                    if self._proc is None or self._proc.poll() is not None:
                        self._start()
                    if self._voice != voice:
                        self._ask(f"VOICE|{voice}")
                        self._voice = voice
                    fd, path = tempfile.mkstemp(suffix=".wav", prefix="interpreter-sapi-")
                    os.close(fd)
                    try:
                        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
                        self._ask(f"{path}|{payload}")
                        audio, rate = sf.read(path, dtype="float32")
                    finally:
                        with contextlib.suppress(OSError):
                            os.remove(path)
                    return (audio if audio.ndim == 1 else audio.mean(axis=1)), int(rate)
                except (queue.Empty, RuntimeError, OSError):
                    self.close()
                    if attempt == 2:
                        raise
            raise RuntimeError("unreachable")  # pragma: no cover

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.kill()


# -- el motor de voces genéricas ---------------------------------------------------------------------------


class VoiceBank(TTSBackend):
    """Voz genérica con la voz elegida (``set_voice``); carga cada motor solo cuando hace falta."""

    def __init__(self, voice: str = "auto") -> None:
        self.voice = voice
        self.sample_rate = 16000
        self._mms: Any = None
        self._yourtts: Any = None
        self._coqui: dict[str, Any] = {}
        self._sapi: SapiWorker | None = None
        self._lock = threading.Lock()  # un solo hilo carga modelos a la vez

    def set_voice(self, voice_id: str) -> None:
        self.voice = voice_id

    def _voice_for(self, language: str) -> Voice:
        v = resolve_voice(self.voice, language, installed_sapi_voices())
        if v is None:
            raise ValueError(f"No hay voz genérica para «{language}».")
        return v

    # -- carga de cada motor -----------------------------------------------------------------------------

    def _get_mms(self) -> Any:
        from .stages.backends import MMSTTS

        with self._lock:
            if self._mms is None:
                self._mms = MMSTTS()
            return self._mms

    def _get_yourtts(self) -> Any:
        import warnings

        from .stages.backends import app_home, use_local_model_cache

        with self._lock:
            if self._yourtts is None:
                use_local_model_cache()
                os.environ.setdefault("COQUI_TTS_HOME", str(app_home() / "models" / "tts"))
                warnings.filterwarnings("ignore")
                from TTS.api import TTS  # type: ignore[import-not-found]

                self._yourtts = TTS("tts_models/multilingual/multi-dataset/your_tts").to("cpu")
            return self._yourtts

    def _get_coqui(self, model: str) -> Any:
        import warnings

        from .stages.backends import app_home, use_local_model_cache

        with self._lock:
            if model not in self._coqui:
                use_local_model_cache()
                os.environ.setdefault("COQUI_TTS_HOME", str(app_home() / "models" / "tts"))
                warnings.filterwarnings("ignore")
                from TTS.api import TTS  # type: ignore[import-not-found]

                self._coqui[model] = TTS(model).to("cpu")
            return self._coqui[model]

    def prepare(self, language: str) -> None:
        """Carga por adelantado el motor de la voz elegida, para que la 1.ª frase no espere."""
        v = self._voice_for(language)
        if v.engine == "mms":
            self._get_mms()._load(language)
        elif v.engine == "yourtts":
            self._get_yourtts()
        elif v.engine == "coqui":
            self._get_coqui(v.ref)
        # SAPI arranca su PowerShell en la primera frase (rápido)

    # -- síntesis ------------------------------------------------------------------------------------------

    def synthesize(self, text: str, language: str) -> Iterator[np.ndarray]:
        v = self._voice_for(language)
        if v.engine == "mms":
            mms = self._get_mms()
            for wav in mms.synthesize(text, language):
                self.sample_rate = (
                    mms.sample_rate
                )  # antes de entregar el trozo: la etapa lee la frecuencia al recibirlo
                yield wav
        elif v.engine == "yourtts":
            from .stages.backends import split_sentences

            tts = self._get_yourtts()
            names = tts.synthesizer.tts_model.speaker_manager.name_to_id
            speaker = next(k for k in names if k.strip() == v.ref)  # algunos nombres traen un salto de línea
            self.sample_rate = int(tts.synthesizer.output_sample_rate)
            for sentence in split_sentences(text):
                wav = tts.tts(text=sentence, speaker=speaker, language=YOURTTS_CODES[language])
                yield np.asarray(wav, dtype=np.float32)
        elif v.engine == "coqui":
            from .stages.backends import split_sentences

            tts = self._get_coqui(v.ref)
            self.sample_rate = int(tts.synthesizer.output_sample_rate)
            for sentence in split_sentences(text):
                yield np.asarray(tts.tts(text=sentence), dtype=np.float32)
        else:  # sapi
            if self._sapi is None:
                self._sapi = SapiWorker()
            audio, rate = self._sapi.speak(v.ref, text)
            self.sample_rate = rate
            yield audio.astype(np.float32)

    def close(self) -> None:
        if self._sapi is not None:
            self._sapi.close()
