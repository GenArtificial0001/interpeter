"""Ventana principal. Habla con el motor solo por ``Engine`` y señales de Qt."""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import math
import os
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, sound
from ..audio import sysmic
from ..audio.calibrate import LoopbackReport, make_tone, run_loopback_test
from ..audio.devices import AudioDevice, device_signature, scan_devices
from ..audio.voice_sample import record
from ..audio.webmic import expected_extension_version
from ..engine import (
    LANGUAGES,
    Engine,
    EngineConfig,
    EngineError,
    EngineEvents,
    EngineState,
    check_devices,
    voice_problem,
)
from ..glossary import Glossary
from ..pipeline import UtteranceMetrics
from ..stages.backends import app_home
from ..voices import SapiVoice, installed_sapi_voices, voices_for
from . import theme
from .analysis import AnalysisPage
from .glossary_dialog import GlossaryDialog
from .hud import NATIVE, Mood, OrbWindow
from .toggle import Toggle
from .voice_dialog import VoiceSampleDialog

log = logging.getLogger(__name__)

STATE_TEXT = {
    EngineState.IDLE: "Sin cargar",
    EngineState.LOADING: "Cargando modelos…",
    EngineState.READY: "Listo",
    EngineState.RUNNING: "Interpretando",
    EngineState.ERROR: "Error",
}

BROWSER_OUTPUT = "browser"  # dato del elemento especial de la lista de salidas
BROWSER_LABEL = "Micrófono virtual del navegador (Teams web)"

PAGES = [("idiomas", "Idiomas"), ("audio", "Audio"), ("motor", "Voces y motor"), ("analisis", "Análisis")]
MUTE_OFF_TEXT = "Apagar micrófono"
MUTE_ON_TEXT = "🔇 APAGADO · encender"

PROFILES = {
    "fast": "Rápido (voz genérica)",
    "clone_fast": "Clonado rápido (YourTTS)",
    "clone": "Clonado alta calidad (XTTSv2, lento)",
}
CLONE_PROFILES = ("clone_fast", "clone")

STT_MODELS = [
    ("Rápido · Whisper base", "openai/whisper-base"),
    ("Preciso · Whisper small", "openai/whisper-small"),
]
ACCELERATORS = [("Automática (GPU Intel si hay)", "auto"), ("Solo CPU", "cpu")]

# (texto, milisegundos de silencio que cierran una frase)
DEVICE_POLL_MS = 3000  # cada cuánto se revisan los dispositivos de audio

PAUSES = [
    ("Rápida · 320 ms", 320),
    ("Normal · 480 ms", 480),
    ("Lenta · 700 ms", 700),
    ("Muy lenta · 1000 ms", 1000),
]

EngineBuilder = Callable[[EngineConfig, EngineEvents], Engine]


def clone_available() -> bool:
    """¿Está instalado ``coqui-tts`` (XTTSv2) en este entorno?"""
    return importlib.util.find_spec("TTS") is not None


LICENSE_TEXTS = {
    "clone_fast": (
        "Licencia de YourTTS",
        "El perfil «Clonado rápido» usa YourTTS, con licencia CC BY-NC-ND 4.0: solo uso NO comercial y "
        "sin modificar el modelo.\n\n¿Aceptas esos términos? Solo clona voces propias o de personas "
        "que lo hayan consentido.",
    ),
    "clone": (
        "Licencia de XTTSv2",
        "El perfil «Clonado alta calidad» usa XTTSv2, con la Coqui Public Model License: solo uso NO "
        "comercial.\n\n¿Aceptas esos términos? Solo clona voces propias o de personas que lo hayan "
        "consentido.",
    ),
}


def _ask_license(parent: QWidget, profile: str = "clone") -> bool:
    title, text = LICENSE_TEXTS[profile]
    answer = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def _default_engine(config: EngineConfig, events: EngineEvents) -> Engine:
    return Engine(config, events)


class Bridge(QObject):
    """Reenvía los eventos del motor (hilos de trabajo) al hilo de la interfaz."""

    state = Signal(object, str)
    level = Signal(float)
    metrics = Signal(object)
    original = Signal(str)
    translation = Signal(str)
    error = Signal(str)
    loop = Signal(object)
    segment = Signal(float)
    dropped = Signal(str, float)
    webmic = Signal(int)
    voices_ready = Signal()
    progress = Signal(float, str)

    def events(self) -> EngineEvents:
        return EngineEvents(
            on_state=self.state.emit,
            on_level=self.level.emit,
            on_metrics=self.metrics.emit,
            on_original=self.original.emit,
            on_translation=self.translation.emit,
            on_segment=self.segment.emit,
            on_dropped=self.dropped.emit,
            on_webmic_clients=self.webmic.emit,
            on_progress=self.progress.emit,
            on_error=self.error.emit,
        )


LOGO_PATH = Path(__file__).parent / "assets" / "ford-logo.png"
LOGO_HEIGHT = 30  # px lógicos en la barra superior (el PNG tiene el doble para pantallas nítidas)


def _ford_logo() -> QLabel | None:
    """Óvalo Ford para la esquina de la barra superior; ``None`` si el archivo no está (no es crítico)."""
    pix = QPixmap(str(LOGO_PATH))
    if pix.isNull():
        return None
    pix = pix.scaledToHeight(int(LOGO_HEIGHT * 2), Qt.TransformationMode.SmoothTransformation)
    pix.setDevicePixelRatio(2.0)
    label = QLabel()
    label.setObjectName("fordLogo")
    label.setPixmap(pix)
    label.setToolTip("Ford Motor Company")
    return label


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    return frame, lay


def _section(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("sectionLabel")
    return lab


class MainWindow(QMainWindow):
    def __init__(
        self,
        engine_builder: EngineBuilder = _default_engine,
        load_on_start: bool = True,
        settings: QSettings | None = None,
        recorder: Callable[..., tuple] = record,
        loop_runner: Callable[..., LoopbackReport] = run_loopback_test,
        device_scanner: Callable[[], tuple[list[AudioDevice], list[AudioDevice]]] = scan_devices,
        clone_is_available: bool | None = None,
        license_prompt: Callable[..., bool] = _ask_license,
        glossary_path: Path | None = None,
        voice_lister: Callable[[], list[SapiVoice]] = installed_sapi_voices,
        mic_control: Any = sysmic,
        ui_mode: str | None = None,
        sound_player: Callable[[bytes], None] = sound.play_wav,
    ) -> None:
        super().__init__()
        self._sound_player = sound_player  # suena al terminar de cargar (las pruebas lo reemplazan)
        self._mic_control = mic_control  # lista, activa/silencia y sube/baja el micrófono de Windows
        self._sys_mic: sysmic.Microphone | None = None
        self._clock = time.monotonic  # reloj del vigilante (las pruebas lo reemplazan)
        self._syncing_mic = False  # evita bucles entre la casilla y el botón de apagar
        self._last_signal = time.monotonic()  # última vez que llegó audio real del micrófono
        self._last_restart = float("-inf")
        self._silence_warned = False
        self.mic_events = 0  # cuántas veces el vigilante tuvo que intervenir (para el análisis)
        self._orb: OrbWindow | None = None
        self._sapi_voices: list[SapiVoice] = []
        self._active_voice = "auto"
        self._glossary_path = glossary_path or app_home() / "glossary.json"
        self._clone_available = clone_available() if clone_is_available is None else clone_is_available
        self._license_prompt = license_prompt
        self._device_scanner = device_scanner
        self._devices_initialized = False
        self._audio_busy = False  # prueba de bucle o grabación en curso: no tocar PortAudio
        self._pill_state: tuple[EngineState, str] = (EngineState.IDLE, "")
        self._recorder = recorder
        self._loop_runner = loop_runner
        self.setWindowTitle(f"Interpreter {__version__}")
        self.setMinimumSize(620, 420)  # el contenido hace scroll si no cabe; nada se aplasta
        self.setStyleSheet(theme.STYLESHEET)

        self._settings = settings or QSettings("Interpreter", "Interpreter")
        self._level = 0.0
        self._lat: deque[float] = deque(maxlen=10)
        self._inputs: list[AudioDevice] = []
        self._outputs: list[AudioDevice] = []

        self._bridge = Bridge()
        self._bridge.state.connect(self._on_state)
        self._bridge.level.connect(self._on_level)
        self._bridge.metrics.connect(self._on_metrics)
        self._bridge.original.connect(self._on_original_text)
        self._bridge.translation.connect(self._on_translation_text)
        self._bridge.error.connect(self._on_error)
        self._bridge.loop.connect(self._on_loop_result)
        self._bridge.dropped.connect(self._on_dropped)
        self._bridge.webmic.connect(self._on_webmic_clients)
        self._bridge.voices_ready.connect(self._fill_voices)
        self._bridge.progress.connect(self._on_progress)
        self._bridge.segment.connect(
            lambda sec: self._log("🎙", f"frase detectada ({sec:.1f} s), traduciendo…")
        )

        self.engine = engine_builder(EngineConfig(), self._bridge.events())
        if hasattr(self.engine, "glossary"):
            self.engine.glossary.replace_with(Glossary.load(self._glossary_path))
        self._build_ui()
        self._fit_initial_size()
        self._load_devices()
        self._restore_settings()
        self._fill_voices()
        self._refresh_mic()
        # Las voces de Windows se consultan en segundo plano (~1 s) para no demorar la ventana.
        threading.Thread(
            target=self._load_sapi_voices, args=(voice_lister,), name="voces-windows", daemon=True
        ).start()

        self._mic_watch_timer = QTimer(self)
        self._mic_watch_timer.timeout.connect(self._mic_watch)
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._tick_meter)
        self._meter_timer.start(40)

        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self._poll_devices)
        self._device_timer.start(DEVICE_POLL_MS)

        self._closing = False
        self._last_original = ""
        self._on_state(EngineState.IDLE, "")
        if load_on_start:
            self.reload_models()
        # ``ui_mode`` fuerza la vista («orb» o «classic», p. ej. desde Visión); sin él, la última que se usó.
        mode = ui_mode or str(self._settings.value("ui_mode", "classic", type=str))
        self.starts_in_orb = mode == "orb"
        if self.starts_in_orb:
            QTimer.singleShot(0, self._open_orb_at_start)

    # -- construcción ------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        # Menú lateral en azul Ford (como Visión): cada categoría es una tarjeta blanca.
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(158)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(10, 14, 10, 10)
        side_lay.setSpacing(6)
        brand = QLabel("Interpreter")
        brand.setObjectName("brand")
        brand_sub = QLabel("Ajustes")
        brand_sub.setObjectName("brandSub")
        side_lay.addWidget(brand)
        side_lay.addWidget(brand_sub)
        side_lay.addSpacing(10)
        self._stack = QStackedWidget()
        self._nav: dict[str, QPushButton] = {}
        self._page_index: dict[str, int] = {}
        nav_group = QButtonGroup(self)
        nav_group.setExclusive(True)
        pages: dict[str, QVBoxLayout] = {}
        for n, (key, label) in enumerate(PAGES):
            btn = QPushButton(label)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setShortcut(f"Ctrl+{n + 1}")
            btn.clicked.connect(lambda _=False, k=key: self.show_page(k))
            nav_group.addButton(btn)
            side_lay.addWidget(btn)
            self._nav[key] = btn
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.setSpacing(10)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self._stack.addWidget(scroll)
            pages[key] = lay
            self._page_index[key] = n
        side_lay.addStretch(1)
        version = QLabel(f"v{__version__}")
        version.setObjectName("brandSub")
        side_lay.addWidget(version)
        shell.addWidget(side)

        right = QWidget()
        outer = QVBoxLayout(right)  # barra superior · avisos · páginas · pie
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        shell.addWidget(right, 1)
        topbar = QFrame()
        topbar.setObjectName("topbar")
        top = QHBoxLayout(topbar)
        top.setContentsMargins(16, 8, 12, 8)
        self.lbl_page = QLabel("")
        self.lbl_page.setObjectName("pageTitle")
        self.state_pill = QLabel()
        self.state_pill.setMinimumWidth(130)
        self.state_pill.setMaximumWidth(320)
        self.state_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.lbl_page)
        top.addStretch(1)
        top.addWidget(self.state_pill)
        self.lbl_logo = _ford_logo()
        if self.lbl_logo is not None:
            top.addSpacing(10)
            top.addWidget(self.lbl_logo)
        outer.addWidget(topbar)

        # Idiomas
        lang_card, lang_lay = _card()
        lang_lay.addWidget(_section("Idiomas"))
        row = QHBoxLayout()
        self.cmb_source = QComboBox()
        self.cmb_target = QComboBox()
        for code, name in LANGUAGES.items():
            self.cmb_source.addItem(name, code)
            self.cmb_target.addItem(name, code)
        self.cmb_target.setCurrentIndex(self.cmb_target.findData("en"))
        arrow = QPushButton("⇄")
        arrow.setObjectName("swap")
        arrow.setToolTip("Invertir los idiomas")
        arrow.clicked.connect(self._swap_languages)
        self.btn_swap = arrow
        for label, combo in (("Yo hablo", self.cmb_source), ("Ellos escuchan", self.cmb_target)):
            col = QVBoxLayout()
            lab = QLabel(label)
            lab.setObjectName("muted")
            col.addWidget(lab)
            col.addWidget(combo)
            row.addLayout(col, 1)
            if combo is self.cmb_source:
                row.addWidget(arrow)
        lang_lay.addLayout(row)
        pages["idiomas"].addWidget(lang_card)
        self.cmb_target.currentIndexChanged.connect(self._on_target_changed)
        self.cmb_source.currentIndexChanged.connect(self._check_devices)  # borra avisos viejos de idiomas
        self.cmb_source.currentIndexChanged.connect(lambda *_: self._sync_orb())
        self.cmb_target.currentIndexChanged.connect(lambda *_: self._sync_orb())

        # Audio
        audio_card, audio_lay = _card()
        audio_lay.addWidget(_section("Audio"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.cmb_input = QComboBox()
        self.cmb_output = QComboBox()
        grid.addWidget(self._muted_label("Micrófono (tu voz)"), 0, 0)
        grid.addWidget(self._muted_label("Salida (hacia Teams o auriculares)"), 0, 1)
        grid.addWidget(self.cmb_input, 1, 0)
        grid.addWidget(self.cmb_output, 1, 1)
        audio_lay.addLayout(grid)
        # Micrófono de Windows: activar y volumen (como la configuración de audio de Visión).
        self.mic_row = QWidget()
        mic_lay = QHBoxLayout(self.mic_row)
        mic_lay.setContentsMargins(0, 0, 0, 0)
        self.chk_mic_on = Toggle("Micrófono activado")
        self.chk_mic_on.setToolTip(
            "Enciende o apaga el micrófono (en Interpreter y en Windows, como Panel de control > Sonido)."
        )
        self.sld_mic = QSlider(Qt.Orientation.Horizontal)
        self.sld_mic.setRange(0, 100)
        self.sld_mic.setToolTip("Volumen del micrófono en Windows (0 lo silencia).")
        self.lbl_mic_vol = QLabel("")
        self.lbl_mic_vol.setObjectName("muted")
        self.lbl_mic_vol.setMinimumWidth(44)
        mic_lay.addWidget(self.chk_mic_on)
        mic_lay.addWidget(self.sld_mic, 1)
        mic_lay.addWidget(self.lbl_mic_vol)
        self.btn_mic_reconnect = QPushButton("Reconectar")
        self.btn_mic_reconnect.setToolTip("Reinicia el micrófono (Windows y captura) si dejó de escucharte.")
        self.btn_mic_reconnect.clicked.connect(self.reconnect_mic)
        mic_lay.addWidget(self.btn_mic_reconnect)
        self.mic_row.hide()  # solo se ve si Windows deja controlar este micrófono
        audio_lay.addWidget(self.mic_row)
        self._mic_timer = QTimer(self)  # el volumen se aplica 200 ms después de soltar/mover el control
        self._mic_timer.setSingleShot(True)
        self._mic_timer.timeout.connect(self._apply_mic_volume)
        self.chk_mic_on.toggled.connect(self._on_mic_toggled)
        self.sld_mic.valueChanged.connect(self._on_mic_slider)
        self.chk_sound = Toggle("Sonido al terminar de cargar")
        self.chk_sound.setChecked(bool(self._settings.value("sound_on_ready", True, type=bool)))
        self.chk_sound.setToolTip(
            "Avisa con un sonido corto cuando los modelos terminaron de cargar y ya puedes interpretar."
        )
        self.chk_sound.toggled.connect(lambda on: self._settings.setValue("sound_on_ready", on))
        self._ready_chime = sound.make_ready_chime()
        self.chk_keep_mic = Toggle("Mantener el micrófono activo mientras interpreto")
        self.chk_keep_mic.setChecked(bool(self._settings.value("keep_mic_alive", True, type=bool)))
        self.chk_keep_mic.setToolTip(
            "Si Windows o el auricular lo silencian solo, se reactiva en segundos; si deja de llegar audio, "
            "se reinicia la captura."
        )
        self.chk_keep_mic.toggled.connect(lambda on: self._settings.setValue("keep_mic_alive", on))
        audio_lay.addWidget(self.chk_keep_mic)
        self.lbl_browser = QLabel()
        self.lbl_browser.setWordWrap(True)
        self.lbl_browser.hide()
        audio_lay.addWidget(self.lbl_browser)
        self.lbl_warning = QLabel()
        self.lbl_warning.setObjectName("warning")
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.hide()
        audio_lay.addWidget(self.lbl_warning)
        self.cmb_input.currentIndexChanged.connect(self._check_devices)
        self.cmb_input.currentIndexChanged.connect(lambda *_: self._refresh_mic())
        self.cmb_output.currentIndexChanged.connect(self._on_output_changed)
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        audio_lay.addWidget(self.level_bar)

        tools = QHBoxLayout()
        self.cmb_pause = QComboBox()
        for label, ms in PAUSES:
            self.cmb_pause.addItem(label, ms)
        self.cmb_pause.setCurrentIndex(self.cmb_pause.findData(480))
        self.cmb_pause.setToolTip(
            "Rápida responde antes pero corta las frases en trozos más cortos; lenta traduce frases más "
            "completas pero tarda más."
        )
        self.chk_echo = Toggle("Anti-eco")
        self.chk_echo.setChecked(True)
        self.chk_echo.setToolTip(
            "Ignora lo que el micrófono capte mientras suena la traducción. Evita que la app se escuche a "
            "sí misma y entre en bucle. Si la salida va por cable a Teams (sin sonido en tu sala), puedes "
            "desactivarlo para poder hablar encima de la traducción."
        )
        tools.addWidget(self.chk_echo)
        tools.addStretch()
        self.btn_loop = QPushButton("Probar bucle de audio")
        self.btn_loop.setToolTip(
            "Reproduce un tono breve por la salida elegida y lo graba por el micrófono elegido, para "
            "ajustar el nivel hacia Teams."
        )
        self.btn_loop.clicked.connect(self._run_loop_test)
        tools.addWidget(self.btn_loop)
        audio_lay.addLayout(tools)
        self.lbl_loop = QLabel("")
        self.lbl_loop.setObjectName("muted")
        self.lbl_loop.setWordWrap(True)
        self.lbl_loop.hide()
        audio_lay.addWidget(self.lbl_loop)
        pages["audio"].addWidget(audio_card)

        self.mute_banner = QLabel("🔇  MICRÓFONO SILENCIADO — lo que digas NO se traduce. Pulsa «reanudar».")
        self.mute_banner.setStyleSheet(
            f"background:{theme.WARN}; color:#2a1a00; font-weight:700; border-radius:8px; padding:10px 14px;"
        )
        self.mute_banner.setWordWrap(True)
        self.mute_banner.hide()
        outer.addWidget(self.mute_banner)

        # Control
        ctrl = QHBoxLayout()
        self.btn_toggle = QPushButton("Iniciar interpretación")
        self.btn_toggle.setObjectName("primary")
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.clicked.connect(self._toggle)
        self.btn_mute = QPushButton(MUTE_OFF_TEXT)
        self.btn_mute.setCheckable(True)
        self.btn_mute.setToolTip("Mientras esté activo, lo que digas NO se traduce ni se envía a la llamada.")
        self.btn_mute.toggled.connect(self._on_mute_toggled)
        self.btn_orb = QPushButton("Ir al orbe")
        self.btn_orb.setToolTip(
            "Interfaz alternativa: una ventana transparente con un orbe (clic derecho para volver)."
        )
        self.btn_orb.clicked.connect(self.open_orb)
        ctrl.addWidget(self.btn_toggle, 1)
        ctrl.addWidget(self.btn_mute)
        ctrl.addWidget(self.btn_orb)
        outer.addWidget(self._stack, 1)
        footer = QFrame()
        footer.setObjectName("footer")
        foot_lay = QHBoxLayout(footer)
        foot_lay.setContentsMargins(16, 8, 16, 8)
        foot_lay.addLayout(ctrl)
        outer.addWidget(footer)

        # Análisis: retraso, descartes, recursos y transcripción
        self._analysis = AnalysisPage()
        self.lbl_delay = self._analysis.lbl_delay
        self.lbl_detail = self._analysis.lbl_detail
        self.log = self._analysis.log
        pages["analisis"].addWidget(self._analysis, 1)

        # Avanzado
        self.cmb_profile = QComboBox()
        for code, name in PROFILES.items():
            self.cmb_profile.addItem(name, code)
        if not self._clone_available:
            for profile in CLONE_PROFILES:
                idx = self.cmb_profile.findData(profile)
                self.cmb_profile.setItemText(idx, PROFILES[profile].split(" (")[0] + " (no instalado)")
                self.cmb_profile.model().item(idx).setEnabled(False)  # type: ignore[attr-defined]
                self.cmb_profile.setItemData(
                    idx,
                    "Necesita el paquete coqui-tts en el entorno de la aplicación.",
                    Qt.ItemDataRole.ToolTipRole,
                )
        self.btn_ref = QPushButton("Elegir .wav…")
        self.btn_ref.setToolTip("Archivo .wav de 20–30 s con tu voz, para el perfil clonado.")
        self.btn_ref.clicked.connect(self._choose_reference)
        self.btn_rec = QPushButton("Grabar mi voz…")
        self.btn_rec.setToolTip("Graba una muestra guiada de unos 25 s con el micrófono elegido.")
        self.btn_rec.clicked.connect(self._record_voice)
        self.lbl_ref = QLabel("")
        self.lbl_ref.setObjectName("muted")
        self.lbl_voice = self._muted_label("Voz")
        self.cmb_voice = QComboBox()
        self.cmb_voice.setToolTip("Voz de hombre o de mujer para el perfil «Rápido (voz genérica)».")
        self.cmb_voice.setMinimumWidth(190)
        self.cmb_voice.activated.connect(self._on_voice_changed)

        self.cmb_stt = QComboBox()
        for label, model in STT_MODELS:
            self.cmb_stt.addItem(label, model)
        self.cmb_accel = QComboBox()
        for label, code in ACCELERATORS:
            self.cmb_accel.addItem(label, code)
        self.cmb_accel.setToolTip(
            "Con OpenVINO, el reconocimiento usa la GPU de Intel (más rápido). Si falla, cae a la CPU."
        )
        self.btn_glossary = QPushButton("Glosario…")
        self.btn_glossary.setToolTip(
            "Palabras en inglés, siglas y nombres (SharePoint, Ford, SAP…) que no hay que traducir."
        )
        self.btn_glossary.clicked.connect(self._edit_glossary)
        voz_card, voz = _card()
        voz.addWidget(_section("Voz"))
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        g.addWidget(self._muted_label("Perfil de voz"), 0, 0)
        g.addWidget(self.cmb_profile, 0, 1, 1, 3)
        g.addWidget(self.lbl_voice, 1, 0)
        g.addWidget(self.cmb_voice, 1, 1, 1, 3)
        g.addWidget(self.btn_rec, 2, 1)
        g.addWidget(self.btn_ref, 2, 2)
        g.addWidget(self.lbl_ref, 2, 3)
        g.setColumnStretch(3, 1)
        voz.addLayout(g)
        pages["motor"].addWidget(voz_card)
        motor_card, mot = _card()
        mot.addWidget(_section("Reconocimiento y velocidad"))
        g2 = QGridLayout()
        g2.setHorizontalSpacing(10)
        g2.setVerticalSpacing(8)
        g2.addWidget(self._muted_label("Reconocimiento"), 0, 0)
        g2.addWidget(self.cmb_stt, 0, 1)
        g2.addWidget(self._muted_label("Aceleración"), 1, 0)
        g2.addWidget(self.cmb_accel, 1, 1)
        g2.addWidget(self._muted_label("Pausa para cerrar frase"), 2, 0)
        g2.addWidget(self.cmb_pause, 2, 1)
        g2.setColumnStretch(1, 1)
        mot.addLayout(g2)
        fila = QHBoxLayout()
        fila.addWidget(self.btn_glossary)
        fila.addStretch(1)
        mot.addLayout(fila)
        mot.addWidget(self.chk_sound)
        pages["motor"].addWidget(motor_card)
        for key, lay in pages.items():
            if key != "analisis":
                lay.addStretch(1)
        self.cmb_stt.currentIndexChanged.connect(self._on_profile_changed)
        self.cmb_accel.currentIndexChanged.connect(self._on_profile_changed)
        self.cmb_profile.currentIndexChanged.connect(self._on_profile_changed)
        self._sync_profile_widgets()
        self.show_page(str(self._settings.value("settings_page", "idiomas", type=str)))

    def _fit_initial_size(self) -> None:
        """Ventana pequeña (los ajustes no son la pantalla principal: esa es el orbe)."""
        self.resize(700, 500)

    def show_page(self, key: str) -> None:
        """Muestra la categoría ``key`` del menú lateral y recuerda cuál fue."""
        if key not in self._page_index:
            key = PAGES[0][0]
        self._stack.setCurrentIndex(self._page_index[key])
        self._nav[key].setChecked(True)
        self.lbl_page.setText(dict(PAGES)[key])
        self._settings.setValue("settings_page", key)

    def _swap_languages(self) -> None:
        """⇄: intercambia «Yo hablo» y «Ellos escuchan» (si la voz no habla el nuevo destino, avisa)."""
        src, dst = self.cmb_source.currentData(), self.cmb_target.currentData()
        problem = voice_problem(self.cmb_profile.currentData(), src)
        if problem:
            self._show_warning(problem)
            return
        for combo in (self.cmb_source, self.cmb_target):
            combo.blockSignals(True)
        self.cmb_source.setCurrentIndex(self.cmb_source.findData(dst))
        self.cmb_target.setCurrentIndex(self.cmb_target.findData(src))
        for combo in (self.cmb_source, self.cmb_target):
            combo.blockSignals(False)
        self._on_target_changed()
        self._sync_orb()

    @staticmethod
    def _muted_label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("muted")
        return lab

    # -- dispositivos y ajustes --------------------------------------------------------------------

    def _load_devices(self) -> None:
        try:
            self._inputs, self._outputs = self._device_scanner()
        except Exception as exc:  # noqa: BLE001 - sin sounddevice o sin dispositivos
            log.warning("No se pudieron listar los dispositivos: %s", exc)
            self._inputs, self._outputs = [], []
        self._fill_device_combos()
        if not self._inputs or not self._outputs:
            self._show_warning("No se encontraron dispositivos de audio.")

    def _fill_device_combos(self) -> None:
        """Rellena las listas conservando, por nombre, lo que el usuario tenía elegido."""
        for combo, devices in ((self.cmb_input, self._inputs), (self.cmb_output, self._outputs)):
            keep = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            for d in devices:
                combo.addItem(d.name, d.index)
            if combo is self.cmb_output:  # al final: solo se usa si el usuario la elige expresamente
                combo.addItem(BROWSER_LABEL, BROWSER_OUTPUT)
            idx = combo.findText(keep) if keep else -1
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        if not self._devices_initialized:
            self._devices_initialized = True
            self._prefer_distinct_output()
        self._check_devices()

    def _prefer_distinct_output(self) -> None:
        """En el primer arranque, evita que micrófono y salida sean del mismo equipo (eco/Bluetooth)."""
        inp, out = self.cmb_input.currentText(), self.cmb_output.currentText()
        if not inp or check_devices(inp, out).ok:
            return
        for i in range(self.cmb_output.count()):
            if self.cmb_output.itemData(i) == BROWSER_OUTPUT:
                continue  # la salida al navegador nunca se elige sola
            if check_devices(inp, self.cmb_output.itemText(i)).ok:
                self.cmb_output.setCurrentIndex(i)
                return

    def _poll_devices(self) -> None:
        """Detecta dispositivos conectados o desconectados mientras la app está abierta."""
        busy = self._audio_busy or self.engine.state in (EngineState.RUNNING, EngineState.LOADING)
        if busy:  # reiniciar PortAudio con un stream abierto lo rompería
            return
        try:
            inputs, outputs = self._device_scanner()
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo refrescar la lista de dispositivos: %s", exc)
            return
        before = set(device_signature(self._inputs)) | set(device_signature(self._outputs))
        after = set(device_signature(inputs)) | set(device_signature(outputs))
        if device_signature(inputs) == device_signature(self._inputs) and device_signature(
            outputs
        ) == device_signature(self._outputs):
            return
        chosen_in, chosen_out = self.cmb_input.currentText(), self.cmb_output.currentText()
        self._inputs, self._outputs = inputs, outputs
        self._fill_device_combos()
        for name in sorted(after - before):
            self._log("🔌", f"Dispositivo conectado: {name}")
        for name in sorted(before - after):
            self._log("🔌", f"Dispositivo desconectado: {name}")
        for label, chosen, combo in (
            ("micrófono", chosen_in, self.cmb_input),
            ("salida", chosen_out, self.cmb_output),
        ):
            if chosen and combo.findText(chosen) >= 0 and combo.currentText() != chosen:
                combo.setCurrentIndex(combo.findText(chosen))
            elif chosen and combo.currentText() != chosen:
                self._show_warning(f"Se desconectó el {label} «{chosen}»; se eligió «{combo.currentText()}».")

    def _restore_settings(self) -> None:
        s = self._settings
        for combo, key in (
            (self.cmb_source, "source"),
            (self.cmb_target, "target"),
            (self.cmb_profile, "profile"),
            (self.cmb_pause, "pause"),
            (self.cmb_stt, "stt"),
            (self.cmb_accel, "accel"),
        ):
            value = str(s.value(key, "", type=str))
            if key == "profile" and value in CLONE_PROFILES and not self._clone_available:
                continue  # el último perfil guardado ya no se puede usar: se queda en el rápido
            idx = combo.findData(value)
            if idx < 0 and value.isdigit():  # datos numéricos (p. ej. la pausa) se guardan como texto
                idx = combo.findData(int(value))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        for combo, key in ((self.cmb_input, "input"), (self.cmb_output, "output")):
            idx = combo.findText(str(s.value(key, "", type=str)))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self.chk_echo.setChecked(bool(s.value("anti_echo", True, type=bool)))
        ref = str(s.value("reference", "", type=str))
        if ref:
            self.engine.config.reference_wav = ref
            self.lbl_ref.setText(ref.replace("\\", "/").rsplit("/", 1)[-1])
        self._check_devices()
        self._sync_profile_widgets()

    def _save_settings(self) -> None:
        s = self._settings
        s.setValue("source", self.cmb_source.currentData())
        s.setValue("target", self.cmb_target.currentData())
        s.setValue("profile", self.cmb_profile.currentData())
        s.setValue("pause", self.cmb_pause.currentData())
        s.setValue("anti_echo", self.chk_echo.isChecked())
        s.setValue("stt", self.cmb_stt.currentData())
        s.setValue("accel", self.cmb_accel.currentData())
        s.setValue("input", self.cmb_input.currentText())
        s.setValue("output", self.cmb_output.currentText())
        s.setValue("reference", self.engine.config.reference_wav or "")

    def _output_is_browser(self) -> bool:
        return self.cmb_output.currentData() == BROWSER_OUTPUT

    def _on_output_changed(self) -> None:
        self._check_devices()
        self._refresh_browser_status()

    def _refresh_browser_status(self) -> None:
        """Arranca el micrófono virtual al elegirlo y muestra si hay un navegador conectado."""
        if not self._output_is_browser():
            self.lbl_browser.hide()
            self.btn_loop.setText("Probar bucle de audio")
            return
        self.btn_loop.setText("Enviar tono al navegador")
        try:
            srv = self.engine.ensure_webmic()
        except EngineError as exc:
            self.lbl_browser.setText("⚠ " + str(exc))
            self.lbl_browser.show()
            return
        self._on_webmic_clients(srv.clients)

    def _on_webmic_clients(self, n: int) -> None:
        if not self._output_is_browser():
            return
        srv = self.engine.webmic
        connected = srv.clients if srv is not None else 0
        versions = srv.extension_versions if srv is not None else []
        suffix = ""
        if connected:
            suffix = " · extensión " + (", ".join(f"v{v}" for v in versions) or "anterior a la 0.3.1")
            latest = expected_extension_version()
            if latest and versions != [latest]:
                suffix += f" (la última es v{latest}: recárgala en edge://extensions)"
        if n:
            text = f"✓ Navegador conectado ({n}): Teams, Meet o Zoom recibirá la traducción.{suffix}"
        elif connected:
            text = f"La extensión está conectada, pero no hay ninguna pestaña de reunión abierta.{suffix}"
        else:
            text = "Esperando la extensión: abre Teams web con la extensión instalada."
        if n:  # ya hay una pestaña que recibe: los avisos de «no hay pestaña» que quedaron son viejos
            if "ninguna pestaña" in self.lbl_warning.text():
                self._check_devices()
            if "No hay ningún navegador" in self.lbl_loop.text():
                self._set_loop_text("")
        self.lbl_browser.setText(text)
        self._analysis.set_connection("Navegador: " + text.lstrip("✓ "))
        self.lbl_browser.setStyleSheet(f"color:{theme.ACCENT};" if n else f"color:{theme.WARN};")
        self.lbl_browser.show()

    def _check_devices(self) -> None:
        out_name = None if self._output_is_browser() else (self.cmb_output.currentText() or None)
        res = check_devices(self.cmb_input.currentText() or None, out_name)
        warnings = list(res.warnings)
        profile, target = self.cmb_profile.currentData(), self.cmb_target.currentData()
        problem = voice_problem(profile, target) if profile and target else None
        if problem:
            warnings.append(problem)
        if warnings:
            self._show_warning(" ".join(warnings))
        else:
            self.lbl_warning.hide()

    def _show_warning(self, text: str) -> None:
        self.lbl_warning.setText("⚠ " + text)
        self.lbl_warning.show()

    # -- carga de modelos --------------------------------------------------------------------------

    def reload_models(self) -> None:
        cfg = self.engine.config
        cfg.tts_profile = self.cmb_profile.currentData()
        cfg.stt_model = self.cmb_stt.currentData()
        cfg.accelerator = self.cmb_accel.currentData()

        def work() -> None:
            try:
                self.engine.close()
                self.engine.load()
            except Exception:  # noqa: BLE001 - el motor ya emitió el error a la interfaz
                log.exception("Fallo al cargar los modelos")

        threading.Thread(target=work, name="load-models", daemon=True).start()

    # -- acciones ----------------------------------------------------------------------------------

    def _apply_config(self) -> None:
        cfg = self.engine.config
        cfg.source = self.cmb_source.currentData()
        cfg.target = self.cmb_target.currentData()
        cfg.input_device = self.cmb_input.currentData()
        browser = self.cmb_output.currentData() == BROWSER_OUTPUT
        cfg.output_mode = "browser" if browser else "device"
        cfg.output_device = None if browser else self.cmb_output.currentData()
        cfg.pause_ms = int(self.cmb_pause.currentData())
        cfg.anti_echo = self.chk_echo.isChecked()
        cfg.voice = self.cmb_voice.currentData() or "auto"

    def _toggle(self) -> None:
        if self.engine.state is EngineState.RUNNING:
            self.engine.stop()
            return
        self._apply_config()
        self._save_settings()
        if self.btn_mute.isChecked():
            # Una sesión nueva debe interpretar: se quita el silencio y se informa en la transcripción.
            self.btn_mute.setChecked(False)
            self._log("🎙", "Micrófono reactivado al iniciar la interpretación.")
        try:
            self.engine.start()
        except EngineError as exc:
            self._on_error(str(exc))

    def _on_target_changed(self) -> None:
        self._fill_voices()  # cada idioma tiene sus voces (y recuerda la última que elegiste en él)
        if self.engine.state is EngineState.RUNNING:
            try:
                self.engine.set_target_language(self.cmb_target.currentData())
            except EngineError as exc:  # esa voz no habla ese idioma: se mantiene el anterior
                self.cmb_target.blockSignals(True)
                self.cmb_target.setCurrentIndex(self.cmb_target.findData(self.engine.config.target))
                self.cmb_target.blockSignals(False)
                self._fill_voices()
                self._show_warning(str(exc))
                return
            self.engine.set_voice(self.cmb_voice.currentData() or "auto")
        self._check_devices()

    def _accept_license_if_needed(self, profile: str = "clone") -> bool:
        """Pide una sola vez aceptar la licencia no comercial del modelo del perfil y lo recuerda."""
        key = "xtts_license_accepted" if profile == "clone" else f"license_accepted_{profile}"
        accepted = self._settings.value(key, False, type=bool)
        if not accepted:
            if not self._license_prompt(self, profile):
                return False
            self._settings.setValue(key, True)
        if profile == "clone":  # XTTSv2 exige además esta variable de entorno
            os.environ["COQUI_TOS_AGREED"] = "1"
        return True

    def _set_profile_combo(self, profile: str) -> None:
        idx = self.cmb_profile.findData(profile)
        if idx >= 0 and self.cmb_profile.currentIndex() != idx:
            self.cmb_profile.blockSignals(True)
            self.cmb_profile.setCurrentIndex(idx)
            self.cmb_profile.blockSignals(False)
        self._sync_profile_widgets()

    def _on_profile_changed(self) -> None:
        profile = self.cmb_profile.currentData()
        if profile in CLONE_PROFILES and not self._accept_license_if_needed(profile):
            self._set_profile_combo("fast")  # no aceptó: se queda sin clonar
            return
        self._sync_profile_widgets()
        self._check_devices()
        if self.engine.state in (EngineState.READY, EngineState.ERROR, EngineState.IDLE):
            self.reload_models()

    # -- micrófono de Windows: activar y volumen ------------------------------------------------

    def _refresh_mic(self) -> None:
        """Lee el micrófono de Windows que corresponde al dispositivo elegido y muestra su estado real."""
        try:
            mics = self._mic_control.list_microphones()
            mic = self._mic_control.match_device(self.cmb_input.currentText(), mics)
        except Exception:  # noqa: BLE001 - sin Core Audio no hay controles, y la aplicación sigue igual
            log.debug("No se pudo leer el micrófono de Windows", exc_info=True)
            mic = None
        self._sys_mic = mic
        known = mic is not None and mic.muted is not None and mic.volume is not None
        self.mic_row.setVisible(bool(known))
        if known:
            assert mic is not None and mic.volume is not None
            for w in (self.chk_mic_on, self.sld_mic):
                w.blockSignals(True)
            self.chk_mic_on.setChecked(not mic.muted)
            self.sld_mic.setValue(mic.volume)
            for w in (self.chk_mic_on, self.sld_mic):
                w.blockSignals(False)
            self.lbl_mic_vol.setText(f"{mic.volume} %")
        if self._orb is not None:
            self._orb.sys_mic_muted = mic.muted if known and mic is not None else None
            self._orb.mic_volume = (0 if mic.muted else mic.volume) if known and mic is not None else None
            self._orb.update()

    def _on_mic_toggled(self, on: bool) -> None:
        """La casilla y el botón «apagar micrófono» son el MISMO interruptor: la casilla mueve al botón."""
        if self._syncing_mic:
            return
        self.btn_mute.setChecked(not on)

    def _apply_sys_mic(self, enabled: bool, origin: str) -> None:
        """Windows sigue a la persona: apagar lo silencia; encender FUERZA un ciclo y reabre la captura."""
        if self._sys_mic is not None and self._sys_mic.muted is not None:
            try:
                if enabled:
                    self._sys_mic = self._mic_control.force_cycle(self._sys_mic.id)
                else:
                    self._sys_mic = self._mic_control.set_mute(self._sys_mic.id, True)
            except Exception as exc:  # noqa: BLE001 - la aplicación sigue igual aunque Windows no obedezca
                self._show_warning(str(exc))
        if enabled and self.engine.state is EngineState.RUNNING:
            self.engine.restart_capture()
            self._last_signal = self._clock()
        self._log_mic(origin)
        self._refresh_mic()

    def _log_mic(self, origin: str) -> None:
        m = self._sys_mic
        log.info(
            "Micrófono: silenciado=%s volumen=%s dispositivo=%s origen=%s",
            None if m is None else m.muted,
            None if m is None else m.volume,
            "-" if m is None else m.name,
            origin,
        )

    def reconnect_mic(self) -> None:
        """Acción manual «Reconectar micrófono»: ciclo de Windows y reinicio de la captura."""
        self._syncing_mic = True
        self.btn_mute.setChecked(False)
        self._syncing_mic = False
        self.engine.set_muted(False)
        self._apply_sys_mic(True, "persona")
        self._notice("Micrófono reconectado")

    def _notice(self, text: str) -> None:
        """Mensaje breve para la persona (en el orbe si está abierto; siempre en la transcripción)."""
        self._log("🎙", text)
        if self._orb is not None:
            self._orb.set_caption(text)

    # -- vigilante: mantiene el micrófono vivo mientras se interpreta (spec A) ---------------------------
    SILENCE_S = 6.0  # sin nada de señal durante tanto tiempo = el flujo de audio se colgó
    RESTART_COOLDOWN_S = 30.0

    def _mic_watch(self) -> None:
        """Cada 2 s al interpretar: reactiva el micrófono silenciado solo; sin audio, reinicia la captura."""
        if (
            self.engine.state is not EngineState.RUNNING
            or self.engine.muted
            or not self.chk_keep_mic.isChecked()
        ):
            return
        self._refresh_mic()
        if self._sys_mic is not None and self._sys_mic.muted:
            try:
                self._sys_mic = self._mic_control.set_mute(self._sys_mic.id, False)
                self.mic_events += 1
                self._analysis.mic_events = self.mic_events
                self._log_mic("vigilante")
                self._notice("El micrófono se había silenciado solo; lo reactivé")
                self._last_signal = self._clock()
            except Exception as exc:  # noqa: BLE001
                self._show_warning(str(exc))
            self._refresh_mic()
            return
        now = self._clock()
        if now - self._last_signal >= self.SILENCE_S:
            if now - self._last_restart >= self.RESTART_COOLDOWN_S:
                self._last_restart = now
                self._last_signal = now
                self._silence_warned = False
                self.mic_events += 1
                self._analysis.mic_events = self.mic_events
                self._log_mic("vigilante: sin audio")
                if self.engine.restart_capture():
                    self._notice("No llegaba audio; reinicié el micrófono")
            elif not self._silence_warned:
                self._silence_warned = True
                self._notice("No llega audio del micrófono (¿botón de silencio del auricular?)")

    def _on_mic_slider(self, value: int) -> None:
        self.lbl_mic_vol.setText(f"{value} %")
        self._mic_timer.start(200)

    def _apply_mic_volume(self) -> None:
        if self._sys_mic is None:
            return
        try:
            self._sys_mic = self._mic_control.set_volume(self._sys_mic.id, self.sld_mic.value())
        except Exception as exc:  # noqa: BLE001
            self._show_warning(str(exc))
        self._refresh_mic()

    def _orb_volume_step(self, delta: int) -> None:
        """Rueda del ratón sobre «Micrófono» en el orbe: sube o baja el volumen de Windows de 5 en 5."""
        self._refresh_mic()
        if self._sys_mic is None or self._sys_mic.volume is None:
            if self._orb is not None:
                self._orb.set_caption("No se puede controlar el volumen de este micrófono")
            return
        try:
            self._sys_mic = self._mic_control.set_volume(self._sys_mic.id, self._sys_mic.volume + delta)
        except Exception as exc:  # noqa: BLE001
            if self._orb is not None:
                self._orb.set_caption(str(exc))
            return
        self._refresh_mic()
        if self._orb is not None and self._sys_mic is not None:
            vol = self._sys_mic.volume if not self._sys_mic.muted else 0
            self._orb.set_caption(f"Volumen del micrófono: {vol} %")

    # -- voz genérica: hombre o mujer ---------------------------------------------------------------------

    def _load_sapi_voices(self, lister: Callable[[], list[SapiVoice]]) -> None:
        try:
            self._sapi_voices = lister()
        except Exception:  # noqa: BLE001 - sin voces de Windows quedan las demás
            log.warning("No se pudieron listar las voces de Windows", exc_info=True)
            return
        self._bridge.voices_ready.emit()

    def _fill_voices(self, prefer: str | None = None) -> None:
        """Lista las voces (hombre/mujer) del idioma de salida y recuerda la última elegida en cada idioma."""
        target = self.cmb_target.currentData()
        if not target:
            return
        saved = str(self._settings.value(f"voice_{target}", "", type=str))
        keep = prefer or (saved if saved else self.cmb_voice.currentData() if self.cmb_voice.count() else "")
        self.cmb_voice.blockSignals(True)
        self.cmb_voice.clear()
        for v in voices_for(target, self._sapi_voices):
            self.cmb_voice.addItem(v.title, v.id)
        idx = self.cmb_voice.findData(keep) if keep else -1
        self.cmb_voice.setCurrentIndex(max(idx, 0))
        self.cmb_voice.blockSignals(False)
        self.cmb_voice.setEnabled(self.cmb_voice.count() > 1)
        self._active_voice = self.cmb_voice.currentData() or "auto"
        self.engine.config.voice = self._active_voice

    def _on_voice_changed(self) -> None:
        voice_id = self.cmb_voice.currentData()
        voices = {v.id: v for v in voices_for(self.cmb_target.currentData(), self._sapi_voices)}
        voice = voices.get(voice_id)
        if voice is not None and voice.license and not self._accept_license_if_needed(voice.license):
            self._fill_voices(
                prefer=self._active_voice
            )  # no aceptó la licencia de esa voz: vuelve a la anterior
            return
        self._settings.setValue(f"voice_{self.cmb_target.currentData()}", voice_id)
        self._active_voice = voice_id
        self.engine.config.voice = voice_id
        if self.engine.state in (EngineState.READY, EngineState.RUNNING) and hasattr(
            self.engine, "set_voice"
        ):
            self.engine.set_voice(voice_id)

    def _sync_profile_widgets(self) -> None:
        generic = self.cmb_profile.currentData() == "fast"
        self.lbl_voice.setVisible(generic)
        self.cmb_voice.setVisible(generic)
        # Grabar la muestra se puede hacer siempre; elegir un .wav y su nombre solo importan al clonar.
        clone = self.cmb_profile.currentData() in CLONE_PROFILES
        self.btn_ref.setVisible(clone)
        self.lbl_ref.setVisible(clone or bool(self.engine.config.reference_wav))

    def _edit_glossary(self) -> None:
        glossary = getattr(self.engine, "glossary", None)
        if glossary is None:
            return
        dlg = GlossaryDialog(self, glossary, self._glossary_path)
        dlg.exec()
        if dlg.saved:
            self._log("ℹ", f"Glosario guardado: {len(glossary)} términos (se aplica ya).")

    def _record_voice(self) -> None:
        dlg = VoiceSampleDialog(
            self,
            self.cmb_input.currentData(),
            app_home() / "voices" / "mi_voz.wav",
            recorder=self._recorder,
        )
        self._audio_busy = True
        try:
            accepted = dlg.exec()
        finally:
            self._audio_busy = False
        if accepted and dlg.saved_path is not None:
            self._set_reference(str(dlg.saved_path))

    def _set_reference(self, path: str) -> None:
        self.engine.config.reference_wav = path
        self.lbl_ref.setText(path.replace("\\", "/").rsplit("/", 1)[-1])
        self._sync_profile_widgets()
        self._save_settings()
        if self.cmb_profile.currentData() in CLONE_PROFILES:
            self.reload_models()

    def _run_loop_test(self) -> None:
        if self.engine.state is EngineState.RUNNING:
            self._set_loop_text("Detén la interpretación antes de probar el bucle.")
            return
        if self._output_is_browser():
            self._send_test_tone_to_browser()
            return
        out, inp = self.cmb_output.currentData(), self.cmb_input.currentData()
        self._audio_busy = True
        self.btn_loop.setEnabled(False)
        self._set_loop_text("Sonará un tono breve por la salida elegida…")

        def work() -> None:
            try:
                self._bridge.loop.emit(self._loop_runner(out, inp))
            except Exception as exc:  # noqa: BLE001 - se informa en la interfaz
                log.exception("Fallo en la prueba del bucle")
                self._bridge.error.emit(f"La prueba del bucle falló: {exc}")
                self._bridge.loop.emit(None)

        threading.Thread(target=work, name="loop-test", daemon=True).start()

    def _send_test_tone_to_browser(self) -> None:
        """Manda 1,5 s de tono al micrófono virtual: se oye en Teams web si todo está bien conectado."""
        try:
            srv = self.engine.ensure_webmic()
        except EngineError as exc:
            self._set_loop_text(str(exc))
            return
        tone = make_tone(24000, 1.5, level_dbfs=-12.0)
        n = srv.push(tone, 24000)
        if n:
            self._set_loop_text(
                f"✓ Tono enviado a {n} navegador(es): en una llamada de prueba de Teams web se oye."
            )
        else:
            self._set_loop_text(
                "⚠ No hay ningún navegador conectado. Instala la extensión y abre Teams web "
                "(ver browser-extension/README.md)."
            )

    def _set_loop_text(self, text: str) -> None:
        self.lbl_loop.setText(text)
        self.lbl_loop.setVisible(bool(text))

    def _on_loop_result(self, report: object) -> None:
        self._audio_busy = False
        self.btn_loop.setEnabled(True)
        if report is None:
            self._set_loop_text("")
            return
        r: LoopbackReport = report  # type: ignore[assignment]
        mark = "✓" if r.verdict == "ok" else "⚠"
        self._set_loop_text(
            f"{mark} {r.advice}  (tono {r.tone_dbfs:.0f} dBFS · relación señal/ruido {r.snr_db:.0f} dB)"
        )

    def _choose_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Muestra de tu voz", "", "Audio WAV (*.wav)")
        if path:
            self._set_reference(path)

    # -- eventos del motor -------------------------------------------------------------------------

    def _render_pill(self) -> None:
        state, detail = self._pill_state
        text = detail or STATE_TEXT[state]
        color = theme.STATE_COLORS[state.value]
        if self.engine.muted and state in (EngineState.RUNNING, EngineState.READY):
            text, color = "Silenciado: no se traduce", theme.WARN
        self.state_pill.setText("● " + text)
        self.state_pill.setStyleSheet(
            f"color:{color}; background:{theme.SURFACE}; border:1px solid {theme.BORDER};"
            "border-radius:14px; padding:6px 12px; font-weight:600;"
        )

    def _on_mute_toggled(self, muted: bool) -> None:
        self.engine.set_muted(muted)
        if not self._syncing_mic:
            self._syncing_mic = True
            self.chk_mic_on.blockSignals(True)
            self.chk_mic_on.setChecked(not muted)
            self.chk_mic_on.blockSignals(False)
            self._syncing_mic = False
            self._apply_sys_mic(not muted, "persona")
        self._sync_orb()
        self.btn_mute.setText(MUTE_ON_TEXT if muted else MUTE_OFF_TEXT)
        self.btn_mute.setStyleSheet(
            f"background:{theme.WARN}; color:#2a1a00; border:1px solid {theme.WARN}; font-weight:700;"
            if muted
            else ""
        )
        self._render_pill()
        self.mute_banner.setVisible(muted)
        self.setWindowTitle(
            f"Interpreter {__version__} — SILENCIADO" if muted else f"Interpreter {__version__}"
        )
        if muted:
            self._log("🔇", "Micrófono silenciado: lo que digas no se traducirá hasta que pulses «reanudar».")

    def _on_dropped(self, reason: str, seconds: float) -> None:
        self._analysis.count_drop(reason)
        textos = {
            "silenciado": (
                "🔇",
                f"frase de {seconds:.1f} s ignorada: el micrófono está silenciado. Pulsa «reanudar».",
            ),
            "sin_texto": ("⚠", "no se entendió nada en esa frase (¿micrófono lejos o volumen bajo?)."),
            "traduccion_vacia": ("⚠", "la traducción salió vacía; esa frase no se dirá."),
            "sin_audio": ("⚠", "se tradujo pero no se generó audio para esa frase."),
            "cola": ("⚠", "voy atrasado: descarté una frase anterior (el reconocimiento no da abasto)."),
            "idioma_distinto": (
                "🔁",
                "ignoré un sonido que no es voz en tu idioma (eco de la traducción o ruido).",
            ),
            "poca_confianza": ("🔁", "ignoré un sonido poco claro (ruido o murmullo), no parecía una frase."),
            "eco": ("🔁", f"ignoré {seconds:.1f} s de audio captado mientras sonaba la traducción (eco)."),
        }
        icono, texto = textos.get(reason, ("⚠", f"frase descartada ({reason})."))
        self._log(icono, texto)

    def _on_state(self, state: EngineState, detail: str) -> None:
        was_loading = self._pill_state[0] is EngineState.LOADING
        if state is EngineState.READY:  # si el motor cayó al perfil rápido, el selector debe reflejarlo
            self._set_profile_combo(self.engine.config.tts_profile)
        self._pill_state = (state, detail)
        self._render_pill()
        running = state is EngineState.RUNNING
        ready = state is EngineState.READY
        self.btn_toggle.setEnabled(ready or running)
        self.btn_toggle.setText("Detener" if running else "Iniciar interpretación")
        self.btn_toggle.setProperty("stop", running)  # el estilo sale de theme.py: #primary[stop="true"]
        self.btn_toggle.style().unpolish(self.btn_toggle)
        self.btn_toggle.style().polish(self.btn_toggle)
        editable = not running and state is not EngineState.LOADING
        for w in (
            self.cmb_source,
            self.cmb_input,
            self.cmb_output,
            self.cmb_profile,
            self.cmb_pause,
            self.chk_echo,
            self.cmb_stt,
            self.cmb_accel,
            self.btn_ref,
            self.btn_rec,
            self.btn_loop,
        ):
            w.setEnabled(editable)
        if not running:
            self._level = 0.0
        if state is EngineState.READY and was_loading and self.chk_sound.isChecked():
            self._play_ready_sound()
        if running:
            self._last_signal = self._clock()
            self._silence_warned = False
            self._mic_watch_timer.start(2000)
        else:
            self._mic_watch_timer.stop()
        self._sync_orb()

    # -- orbe (interfaz alternativa) -------------------------------------------------------------------------

    def _on_progress(self, fraction: float, label: str) -> None:
        if self._orb is not None:
            self._orb.set_progress(fraction, label)

    def _play_ready_sound(self) -> None:
        """Sonido de «ya puedes usarlo»: una vez, al terminar de cargar los modelos."""
        try:
            self._sound_player(self._ready_chime)
        except Exception:  # noqa: BLE001 - un sonido nunca debe romper la aplicación
            log.debug("No se pudo reproducir el sonido de listo", exc_info=True)

    def _on_original_text(self, text: str) -> None:
        self._log("Tú", text)
        self._last_original = text

    def _on_translation_text(self, text: str) -> None:
        self._log("→", text)
        self._analysis.count_translated()
        if self._orb is not None:
            self._orb.set_caption(f"{self._last_original}  →  {text}" if self._last_original else text)
            self._orb.speak(len(text) * 0.065)  # estimado: la voz tarda ~65 ms por carácter

    def _sync_orb(self) -> None:
        """Lleva al orbe el estado del motor, el silencio y los idiomas (si está abierto)."""
        if self._orb is None:
            return
        state = self._pill_state[0]
        if state is EngineState.ERROR:
            mood = Mood.ERROR
        elif state is EngineState.RUNNING:
            mood = Mood.MUTED if self.engine.muted else Mood.RUNNING
        elif state is EngineState.READY:
            mood = Mood.MUTED if self.engine.muted else Mood.READY
        else:
            mood = Mood.LOADING
        self._orb.muted = self.engine.muted
        self._orb.set_mood(mood)
        src, dst = self.cmb_source.currentData(), self.cmb_target.currentData()
        self._orb.set_languages(NATIVE.get(src, str(src)), NATIVE.get(dst, str(dst)))

    def open_orb(self) -> None:
        """Abre el orbe y esconde esta ventana (se vuelve con «Ajustes» o el menú del orbe)."""
        if self._orb is None:
            orb = OrbWindow()
            orb.toggle_requested.connect(self._orb_toggle)
            orb.mic_requested.connect(self.btn_mute.toggle)
            orb.settings_requested.connect(self._toggle_classic)
            orb.input_requested.connect(lambda: self._pick_from(self.cmb_input, "Micrófono de entrada"))
            orb.output_requested.connect(lambda: self._pick_from(self.cmb_output, "Salida de audio"))
            orb.speak_requested.connect(lambda: self._pick_from(self.cmb_source, "Yo hablo"))
            orb.listen_requested.connect(lambda: self._pick_from(self.cmb_target, "Ellos escuchan"))
            orb.voice_requested.connect(self._pick_voice)
            orb.level_requested.connect(self._pick_level)
            orb.swap_requested.connect(self._swap_languages)
            orb.glossary_requested.connect(self._edit_glossary)
            orb.echo_toggle_requested.connect(self.chk_echo.toggle)
            orb.pause_requested.connect(lambda: self._pick_from(self.cmb_pause, "Pausa para cerrar frase"))
            orb.classic_requested.connect(self.close_orb)
            orb.closed.connect(self._orb_closed)
            orb.volume_step.connect(self._orb_volume_step)
            orb.sys_mic_toggle_requested.connect(self.btn_mute.toggle)
            orb.reconnect_requested.connect(self.reconnect_mic)
            saved = str(self._settings.value("orb_animation", "", type=str))
            if saved:
                orb.set_animation(saved)
            orb.animation_changed.connect(lambda mode: self._settings.setValue("orb_animation", mode))
            self._orb = orb
        self._sync_orb()
        self._refresh_mic()
        self._orb.show()
        self._orb.raise_()
        self.hide()
        self._settings.setValue("ui_mode", "orb")

    def _open_orb_at_start(self) -> None:
        """Abre el orbe al arrancar; si falla, la aplicación NO queda invisible: se muestra la clásica."""
        try:
            self.open_orb()
        except Exception:  # noqa: BLE001
            log.exception("No se pudo abrir el orbe: se usa la ventana clásica")
            self._orb = None
            self.show()

    def close_orb(self) -> None:
        orb, self._orb = self._orb, None
        if orb is not None:
            with contextlib.suppress(Exception):
                orb.closed.disconnect(self._orb_closed)
            orb.close()
        self._settings.setValue("ui_mode", "classic")
        self.show()
        self.raise_()

    def _orb_toggle(self) -> None:
        if self.btn_toggle.isEnabled():
            self._toggle()

    def _orb_closed(self) -> None:
        """Cerraron el orbe (Alt+F4 o «Salir de Interpreter»): se sale de la aplicación completa."""
        self._orb = None
        if not self._closing:
            self.close()

    def _show_classic(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _toggle_classic(self) -> None:
        """«Ajustes» del orbe: muestra la ventana y, si ya se ve, la oculta (sin apagar nada)."""
        if self.isVisible():
            self.hide()
        else:
            self._show_classic()

    def _menu(self, title: str) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_STYLE)  # sin esto el menú sale con letra negra sobre fondo oscuro
        menu.addAction(title).setEnabled(False)  # título (se ve en azul, no se puede elegir)
        menu.addSeparator()
        return menu

    @staticmethod
    def _short(text: str, limit: int = 60) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _add_combo_items(self, menu: QMenu, combo: QComboBox) -> None:
        for i in range(combo.count()):
            action = menu.addAction(self._short(combo.itemText(i)))
            action.setCheckable(True)
            action.setChecked(i == combo.currentIndex())
            action.triggered.connect(lambda _=False, idx=i: combo.setCurrentIndex(idx))

    def _pick_from(self, combo: QComboBox, title: str) -> None:
        """Menú junto al cursor con las opciones del desplegable (elegir una = elegirla en Ajustes)."""
        menu = self._menu(title)
        self._add_combo_items(menu, combo)
        menu.exec(QCursor.pos())

    def _pick_level(self) -> None:
        """Menú del volumen del micrófono: sube o baja de 10 en 10, o lo fija en 25/50/75/100 %."""
        menu = self._menu("Volumen del micrófono")
        actual = (
            self._sys_mic.volume if self._sys_mic is not None and self._sys_mic.volume is not None else None
        )
        for texto, delta in (("Subir 10 %", 10), ("Bajar 10 %", -10)):
            menu.addAction(texto).triggered.connect(lambda _=False, d=delta: self._orb_volume_step(d))
        menu.addSeparator()
        for pct in (25, 50, 75, 100):
            action = menu.addAction(f"{pct} %")
            action.setCheckable(True)
            action.setChecked(actual == pct)
            action.triggered.connect(
                lambda _=False, p=pct: self._orb_volume_step(p - (actual if actual is not None else p))
            )
        menu.exec(QCursor.pos())

    def _pick_voice(self) -> None:
        """Perfil de voz y, si el perfil lo permite, la voz concreta."""
        menu = self._menu("Perfil de voz")
        self._add_combo_items(menu, self.cmb_profile)
        if self.cmb_voice.count() > 1 and self.cmb_voice.isEnabled():
            menu.addSeparator()
            menu.addAction("Voz").setEnabled(False)
            self._add_combo_items(menu, self.cmb_voice)
        menu.exec(QCursor.pos())

    def _on_level(self, rms: float) -> None:
        if rms > 1e-6:
            self._last_signal = self._clock()
        if self._orb is not None and not self.engine.muted:
            self._orb.push_level(rms)
        if self.engine.muted:  # silenciado: el medidor quieto evita pensar que se está traduciendo
            self._level = 0.0
            return
        self._level = max(self._level * 0.6, rms)  # caída suave, subida inmediata

    def _tick_meter(self) -> None:
        db = 20 * math.log10(self._level) if self._level > 1e-6 else -90.0
        self.level_bar.setValue(int(max(0.0, min(1.0, (db + 60.0) / 60.0)) * 100))
        self._level *= 0.85

    def _on_metrics(self, m: UtteranceMetrics) -> None:
        if m.first_audio_from_speech_end is None:
            return
        self._lat.append(m.first_audio_from_speech_end)
        self._analysis.add_latency(m.first_audio_from_speech_end, m.stt, m.mt)
        if self._orb is not None:
            self._orb.set_latency(m.first_audio_from_speech_end, statistics.median(self._lat))
        self.lbl_delay.setText(f"{m.first_audio_from_speech_end:.1f} s")
        med = statistics.median(self._lat)
        parts = [
            f"voz→texto {m.stt:.1f} s" if m.stt is not None else "",
            f"traducción {m.mt:.1f} s" if m.mt is not None else "",
            f"mediana {med:.1f} s",
        ]
        self.lbl_detail.setText("  ·  ".join(p for p in parts if p))

    def _on_error(self, message: str) -> None:
        self._log("⚠", message)
        self._show_warning(message)

    def _log(self, who: str, text: str) -> None:
        self.log.appendPlainText(f"{who}  {text}")

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._orb is not None and not self._closing and event.spontaneous():
            event.ignore()  # la X de Ajustes con el orbe abierto solo la oculta (como al arrancar)
            self.hide()
            return
        self._closing = True
        orb, self._orb = self._orb, None
        if orb is not None:
            with contextlib.suppress(Exception):
                orb.closed.disconnect(self._orb_closed)
            orb.close()
        self._save_settings()
        self.engine.close()
        super().closeEvent(event)
