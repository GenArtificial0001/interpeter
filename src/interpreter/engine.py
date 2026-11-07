"""Motor del intérprete: une captura, pipeline y reproducción. No depende de Qt.

La interfaz (o la línea de comandos) solo habla con ``Engine``: configura, ``load()`` los modelos (lento,
en un hilo aparte), ``start()``/``stop()`` la interpretación y recibe eventos por ``EngineEvents``.
Todos los eventos se emiten desde hilos de trabajo: la interfaz debe reenviarlos a su hilo principal.
"""

from __future__ import annotations

import enum
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .audio.io import MicCapture, Player
from .messages import SpeechSegment
from .pipeline import Pipeline, UtteranceMetrics
from .stages.base import MTBackend, STTBackend, TTSBackend
from .vad import AdaptiveEnergyVAD, EnergyVAD, Segmenter, VadConfig

log = logging.getLogger(__name__)

LANGUAGES = {"es": "Español", "en": "Inglés", "pt": "Portugués", "fr": "Francés"}


class EngineState(enum.Enum):
    IDLE = "idle"  # modelos sin cargar
    LOADING = "loading"
    READY = "ready"  # modelos cargados, interpretación apagada
    RUNNING = "running"
    ERROR = "error"


class EngineError(Exception):
    """Error de configuración o de estado con un mensaje apto para mostrar al usuario."""


@dataclass(slots=True)
class EngineConfig:
    input_device: int | None = None
    output_device: int | None = None
    source: str = "es"
    target: str = "en"
    stt_model: str = "openai/whisper-base"
    mt_model: str = "facebook/nllb-200-distilled-600M"
    quantize: bool = True  # int8 en la traducción
    stt_quantize: bool = False  # int8 en Whisper cambió alguna palabra en las pruebas: desactivado
    accelerator: str = "auto"  # "auto" (GPU Intel con OpenVINO si existe) | "cpu"
    beams: int = 1
    tts_profile: str = "fast"  # "fast" (sin clonar) | "clone" (XTTSv2)
    reference_wav: str | None = None
    vad_mode: str = "auto"  # "auto" (umbral adaptativo al ruido del micrófono) | "fixed"
    vad_threshold_db: float = -42.0  # solo se usa con vad_mode="fixed"
    pause_ms: int = 480  # pausa que cierra una frase: menor = menos retraso, pero frases más cortas
    threads: int | None = None


def _noop(*_a: Any, **_k: Any) -> None:
    return None


@dataclass(slots=True)
class EngineEvents:
    on_state: Callable[[EngineState, str], None] = _noop
    on_level: Callable[[float], None] = _noop
    on_metrics: Callable[[UtteranceMetrics], None] = _noop
    on_original: Callable[[str], None] = _noop
    on_translation: Callable[[str], None] = _noop
    on_segment: Callable[[float], None] = _noop  # duración (s) de cada frase que el detector de voz cerró
    on_dropped: Callable[[str, float], None] = _noop  # (motivo, duración s) de una frase que NO se tradujo
    on_error: Callable[[str], None] = _noop


Backends = tuple[STTBackend, MTBackend, TTSBackend]


def make_stt(cfg: EngineConfig) -> STTBackend:
    """Whisper con el codificador en la GPU Intel (OpenVINO) si está; si no, o si falla, en CPU."""
    from .stages.backends import OVWhisperSTT, WhisperSTT, openvino_devices

    if cfg.accelerator != "cpu" and "GPU" in openvino_devices():
        try:
            return OVWhisperSTT(cfg.stt_model, cfg.threads, device="GPU")
        except Exception:  # noqa: BLE001 - cualquier fallo de OpenVINO cae a PyTorch
            log.warning("OpenVINO no pudo iniciar; se usa PyTorch en CPU", exc_info=True)
    return WhisperSTT(cfg.stt_model, cfg.threads, cfg.stt_quantize)


def default_backends(cfg: EngineConfig) -> Backends:
    """Carga los modelos reales (lento: descarga la primera vez)."""
    from .stages.backends import (
        MMSTTS,
        NLLBTranslator,
        XTTSClone,
        models_ready_marker,
    )

    stt = make_stt(cfg)
    mt = NLLBTranslator(cfg.mt_model, cfg.threads, cfg.quantize, cfg.beams)
    if cfg.tts_profile == "clone":
        if not cfg.reference_wav:
            raise EngineError("El perfil clonado necesita una muestra de tu voz (wav).")
        tts: TTSBackend = XTTSClone(cfg.reference_wav)
    else:
        tts = MMSTTS()
    # Cargaron bien: a partir de ahora el arranque no necesita red.
    marker = models_ready_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return stt, mt, tts


class Engine:
    def __init__(
        self,
        config: EngineConfig | None = None,
        events: EngineEvents | None = None,
        backend_factory: Callable[[EngineConfig], Backends] = default_backends,
        capture_factory: Callable[..., Any] = MicCapture,
        player_factory: Callable[..., Any] = Player,
    ) -> None:
        self.config = config or EngineConfig()
        self.events = events or EngineEvents()
        self._backend_factory = backend_factory
        self._capture_factory = capture_factory
        self._player_factory = player_factory
        self._lock = threading.RLock()
        self._state = EngineState.IDLE
        self._backends: Backends | None = None
        self._pipeline: Pipeline | None = None
        self._capture: Any = None
        self._player: Any = None
        self.muted = False
        self.segments_sent = 0
        self.segments_dropped = 0

    # -- estado ------------------------------------------------------------------------------------

    @property
    def state(self) -> EngineState:
        return self._state

    def _set_state(self, state: EngineState, detail: str = "") -> None:
        self._state = state
        log.info("Estado del motor: %s %s", state.value, detail)
        self.events.on_state(state, detail)

    def _fail(self, message: str, exc: BaseException | None = None) -> None:
        if exc is not None:
            log.error("%s", message, exc_info=exc)
        self.events.on_error(message)

    # -- ciclo de vida -----------------------------------------------------------------------------

    def load(self) -> None:
        """Carga los modelos. Bloqueante: llamar desde un hilo de trabajo."""
        with self._lock:
            if self._state in (EngineState.READY, EngineState.RUNNING):
                return
            self._set_state(EngineState.LOADING, "Cargando modelos…")
            try:
                self._backends = self._backend_factory(self.config)
                self._warm_up(self._backends)
            except Exception as exc:
                self._set_state(EngineState.ERROR, "No se pudieron cargar los modelos")
                self._fail(f"No se pudieron cargar los modelos: {exc}", exc)
                raise
            device = getattr(self._backends[0], "device", None)
            self._set_state(EngineState.READY, f"Listo · reconocimiento en {device}" if device else "Listo")

    def _warm_up(self, backends: Backends) -> None:
        """Una pasada de práctica por cada modelo: la primera inferencia real es mucho más lenta.

        Un fallo aquí no impide usar el motor (solo se pierde el calentamiento), pero se registra.
        """
        import numpy as np

        stt, mt, tts = backends
        cfg = self.config
        try:
            stt.transcribe(np.zeros(16000, dtype=np.float32), 16000, cfg.source)
            mt.translate("Hola, buenos días.", cfg.source, cfg.target)
            for _ in tts.synthesize("Hello, good morning.", cfg.target):
                pass
        except Exception:  # noqa: BLE001
            log.warning("El calentamiento de los modelos falló; se continúa sin él", exc_info=True)

    def start(self) -> None:
        with self._lock:
            if self._state is not EngineState.READY or self._backends is None:
                raise EngineError("Carga los modelos antes de iniciar la interpretación.")
            cfg = self.config
            if cfg.source == cfg.target:
                raise EngineError("El idioma de entrada y el de salida no pueden ser el mismo.")
            stt, mt, tts = self._backends
            self._pipeline = Pipeline(
                stt,
                mt,
                tts,
                cfg.source,
                cfg.target,
                on_error=lambda stage, exc: self._fail(f"Error en {stage}: {exc}", exc),
                on_metrics=self._on_metrics,
                on_transcript=self._on_original,
                on_translation=self._on_translation,
                on_drop=self._on_pipeline_drop,
            )
            classifier = AdaptiveEnergyVAD() if cfg.vad_mode == "auto" else EnergyVAD(cfg.vad_threshold_db)
            segmenter = Segmenter(classifier, VadConfig(end_silence_ms=max(160, cfg.pause_ms)))
            self._player = self._player_factory(
                cfg.output_device, self._pipeline.get_audio, self.events.on_error
            )
            self._capture = self._capture_factory(
                cfg.input_device,
                segmenter,
                self._on_segment,
                self.events.on_level,
                on_error=self.events.on_error,
            )
            self._pipeline.start()
            self._player.start()
            try:
                self._capture.start()
            except Exception as exc:
                self._teardown()
                self._set_state(EngineState.READY, "Listo")
                raise EngineError(f"No se pudo abrir el micrófono: {exc}") from exc
            self._set_state(EngineState.RUNNING, "Interpretando")

    def stop(self) -> None:
        with self._lock:
            if self._state is not EngineState.RUNNING:
                return
            self._teardown()
            self._set_state(EngineState.READY, "Listo")

    def close(self) -> None:
        self.stop()
        self._backends = None
        self._state = EngineState.IDLE

    def _teardown(self) -> None:
        if self._capture is not None:
            self._capture.stop()
        if self._pipeline is not None:
            self._pipeline.interrupt()  # descarta lo que iba en vuelo
        if self._player is not None:
            self._player.stop()
        if self._pipeline is not None:
            self._pipeline.stop()
        self._capture = self._player = self._pipeline = None

    # -- controles en caliente ---------------------------------------------------------------------

    def set_target_language(self, language: str) -> None:
        self.config.target = language
        if self._pipeline is not None:
            self._pipeline.set_target_language(language)

    def set_muted(self, muted: bool) -> None:
        self.muted = muted

    def _on_pipeline_drop(self, reason: str) -> None:
        log.info("Frase sin resultado: %s", reason)
        self.events.on_dropped(reason, 0.0)

    def _on_original(self, text: str) -> None:
        log.info("Reconocido: %s", text)
        self.events.on_original(text)

    def _on_translation(self, text: str) -> None:
        log.info("Traducido: %s", text)
        self.events.on_translation(text)

    def _on_metrics(self, m: UtteranceMetrics) -> None:
        log.info("Retraso al primer audio: %s s", m.first_audio_from_speech_end)
        self.events.on_metrics(m)

    def _on_segment(self, seg: SpeechSegment) -> None:
        if self._pipeline is None:
            return
        seconds = seg.audio.size / seg.sample_rate
        if self.muted:
            # Antes se descartaba sin avisar: ahora se registra y se informa a la interfaz.
            self.segments_dropped += 1
            log.info("Frase de %.1f s ignorada: micrófono silenciado", seconds)
            self.events.on_dropped("silenciado", seconds)
            return
        self.segments_sent += 1
        log.info("Frase detectada: %.1f s", seconds)
        self.events.on_segment(seconds)
        if self._pipeline.submit(seg):  # cola llena: se descartó la frase más vieja
            log.warning("Cola de entrada llena: se descartó la frase más vieja")
            self.events.on_dropped("cola", 0.0)


# -- utilidades de configuración ---------------------------------------------------------------------


@dataclass(slots=True)
class DeviceCheck:
    ok: bool
    warnings: list[str] = field(default_factory=list)


def _device_tag(name: str) -> str | None:
    """Etiqueta entre paréntesis al final del nombre (p. ej. ``WL5024``), sin el prefijo numérico."""
    m = re.search(r"\(([^()]*)\)\s*$", name)
    if not m:
        return None
    tag = re.sub(r"^\d+\s*-\s*", "", m.group(1)).strip().lower()
    return tag or None


def check_devices(input_name: str | None, output_name: str | None) -> DeviceCheck:
    """Avisos de configuración de audio para mostrar en la interfaz."""
    warnings: list[str] = []
    if input_name and output_name:
        same_name = input_name == output_name
        tag_in, tag_out = _device_tag(input_name), _device_tag(output_name)
        same_gear = same_name or (tag_in is not None and tag_in == tag_out)
        if same_gear:
            warnings.append(
                "El micrófono y la salida son del mismo equipo: si es un auricular Bluetooth, Windows lo "
                "pasa a modo manos libres al abrir el micrófono y puede dejar la salida muda o con mala "
                "calidad; si son los altavoces de la laptop, habrá eco. Usa dispositivos distintos."
            )
    return DeviceCheck(ok=not warnings, warnings=warnings)
