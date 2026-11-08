"""Ventana principal. Habla con el motor solo por ``Engine`` y señales de Qt."""

from __future__ import annotations

import importlib.util
import logging
import math
import os
import statistics
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
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
from . import theme
from .glossary_dialog import GlossaryDialog
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

MUTE_OFF_TEXT = "Silenciar"
MUTE_ON_TEXT = "🔇 SILENCIADO · reanudar"

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
            on_error=self.error.emit,
        )


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
    ) -> None:
        super().__init__()
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
        self._bridge.original.connect(lambda t: self._log("Tú", t))
        self._bridge.translation.connect(lambda t: self._log("→", t))
        self._bridge.error.connect(self._on_error)
        self._bridge.loop.connect(self._on_loop_result)
        self._bridge.dropped.connect(self._on_dropped)
        self._bridge.webmic.connect(self._on_webmic_clients)
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

        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._tick_meter)
        self._meter_timer.start(40)

        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self._poll_devices)
        self._device_timer.start(DEVICE_POLL_MS)

        self._on_state(EngineState.IDLE, "")
        if load_on_start:
            self.reload_models()

    # -- construcción ------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Interpreter")
        title.setObjectName("title")
        sub = QLabel("Intérprete de voz local")
        sub.setObjectName("muted")
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(title)
        left.addWidget(sub)
        head.addLayout(left)
        head.addStretch()
        self.state_pill = QLabel()
        self.state_pill.setMinimumWidth(150)
        self.state_pill.setMaximumWidth(360)
        self.state_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self.state_pill)
        outer.addLayout(head)

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
        arrow = QLabel("→")
        arrow.setObjectName("muted")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        outer.addWidget(lang_card)
        self.cmb_target.currentIndexChanged.connect(self._on_target_changed)

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
        self.cmb_output.currentIndexChanged.connect(self._on_output_changed)
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        audio_lay.addWidget(self.level_bar)

        tools = QHBoxLayout()
        tools.addWidget(self._muted_label("Pausa para cerrar frase"))
        self.cmb_pause = QComboBox()
        for label, ms in PAUSES:
            self.cmb_pause.addItem(label, ms)
        self.cmb_pause.setCurrentIndex(self.cmb_pause.findData(480))
        self.cmb_pause.setToolTip(
            "Rápida responde antes pero corta las frases en trozos más cortos; lenta traduce frases más "
            "completas pero tarda más."
        )
        tools.addWidget(self.cmb_pause)
        self.chk_echo = QCheckBox("Anti-eco")
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
        outer.addWidget(audio_card)

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
        ctrl.addWidget(self.btn_toggle, 1)
        ctrl.addWidget(self.btn_mute)
        outer.addLayout(ctrl)

        # Latencia
        lat_card, lat_lay = _card()
        lat_lay.addWidget(_section("Retraso desde que terminas de hablar"))
        lrow = QHBoxLayout()
        self.lbl_delay = QLabel("—")
        self.lbl_delay.setObjectName("metric")
        self.lbl_detail = QLabel("Sin datos todavía")
        self.lbl_detail.setObjectName("muted")
        lrow.addWidget(self.lbl_delay)
        lrow.addSpacing(12)
        lrow.addWidget(self.lbl_detail, 1)
        lat_lay.addLayout(lrow)
        outer.addWidget(lat_card)

        # Transcripción
        log_card, log_lay = _card()
        log_lay.addWidget(_section("Transcripción"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMinimumHeight(140)
        log_lay.addWidget(self.log, 1)
        outer.addWidget(log_card, 1)

        # Avanzado
        adv = QHBoxLayout()
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
        adv.addWidget(self._muted_label("Perfil de voz"))
        adv.addWidget(self.cmb_profile)
        adv.addWidget(self.btn_rec)
        adv.addWidget(self.btn_ref)
        adv.addWidget(self.lbl_ref, 1)
        outer.addLayout(adv)

        rec = QHBoxLayout()
        self.cmb_stt = QComboBox()
        for label, model in STT_MODELS:
            self.cmb_stt.addItem(label, model)
        self.cmb_accel = QComboBox()
        for label, code in ACCELERATORS:
            self.cmb_accel.addItem(label, code)
        self.cmb_accel.setToolTip(
            "Con OpenVINO, el reconocimiento usa la GPU de Intel (más rápido). Si falla, cae a la CPU."
        )
        rec.addWidget(self._muted_label("Reconocimiento"))
        rec.addWidget(self.cmb_stt, 1)
        rec.addWidget(self._muted_label("Aceleración"))
        rec.addWidget(self.cmb_accel, 1)
        self.btn_glossary = QPushButton("Glosario…")
        self.btn_glossary.setToolTip(
            "Palabras en inglés, siglas y nombres (SharePoint, Ford, SAP…) que no hay que traducir."
        )
        self.btn_glossary.clicked.connect(self._edit_glossary)
        rec.addWidget(self.btn_glossary)
        outer.addLayout(rec)
        self.cmb_stt.currentIndexChanged.connect(self._on_profile_changed)
        self.cmb_accel.currentIndexChanged.connect(self._on_profile_changed)
        self.cmb_profile.currentIndexChanged.connect(self._on_profile_changed)
        self._sync_profile_widgets()

    def _fit_initial_size(self) -> None:
        """Abre con todo el contenido visible si la pantalla lo permite; si no, con scroll."""
        content = self.centralWidget().widget().sizeHint().height() + 8  # type: ignore[attr-defined]
        screen = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(780, max(480, min(content, screen - 80)))

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
        if self.engine.state is EngineState.RUNNING:
            try:
                self.engine.set_target_language(self.cmb_target.currentData())
            except EngineError as exc:  # esa voz no habla ese idioma: se mantiene el anterior
                self.cmb_target.blockSignals(True)
                self.cmb_target.setCurrentIndex(self.cmb_target.findData(self.engine.config.target))
                self.cmb_target.blockSignals(False)
                self._show_warning(str(exc))
                return
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

    def _sync_profile_widgets(self) -> None:
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

    def _on_level(self, rms: float) -> None:
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
        self._save_settings()
        self.engine.close()
        super().closeEvent(event)
