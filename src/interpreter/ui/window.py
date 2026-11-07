"""Ventana principal. Habla con el motor solo por ``Engine`` y señales de Qt."""

from __future__ import annotations

import logging
import math
import statistics
import threading
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..audio.devices import AudioDevice, list_devices
from ..engine import (
    LANGUAGES,
    Engine,
    EngineConfig,
    EngineError,
    EngineEvents,
    EngineState,
    check_devices,
)
from ..pipeline import UtteranceMetrics
from . import theme

log = logging.getLogger(__name__)

STATE_TEXT = {
    EngineState.IDLE: "Sin cargar",
    EngineState.LOADING: "Cargando modelos…",
    EngineState.READY: "Listo",
    EngineState.RUNNING: "Interpretando",
    EngineState.ERROR: "Error",
}

PROFILES = {"fast": "Rápido (sin clonar)", "clone": "Clonado (XTTSv2, más lento)"}

EngineBuilder = Callable[[EngineConfig, EngineEvents], Engine]


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

    def events(self) -> EngineEvents:
        return EngineEvents(
            on_state=self.state.emit,
            on_level=self.level.emit,
            on_metrics=self.metrics.emit,
            on_original=self.original.emit,
            on_translation=self.translation.emit,
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
    ) -> None:
        super().__init__()
        self.setWindowTitle("Interpreter")
        self.resize(760, 760)
        self.setMinimumSize(640, 640)
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

        self.engine = engine_builder(EngineConfig(), self._bridge.events())
        self._build_ui()
        self._load_devices()
        self._restore_settings()

        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._tick_meter)
        self._meter_timer.start(40)

        self._on_state(EngineState.IDLE, "")
        if load_on_start:
            self.reload_models()

    # -- construcción ------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
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
        self.lbl_warning = QLabel()
        self.lbl_warning.setObjectName("warning")
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.hide()
        audio_lay.addWidget(self.lbl_warning)
        self.cmb_input.currentIndexChanged.connect(self._check_devices)
        self.cmb_output.currentIndexChanged.connect(self._check_devices)
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        audio_lay.addWidget(self.level_bar)
        outer.addWidget(audio_card)

        # Control
        ctrl = QHBoxLayout()
        self.btn_toggle = QPushButton("Iniciar interpretación")
        self.btn_toggle.setObjectName("primary")
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.clicked.connect(self._toggle)
        self.btn_mute = QPushButton("Silenciar")
        self.btn_mute.setCheckable(True)
        self.btn_mute.toggled.connect(self.engine.set_muted)
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
        log_lay.addWidget(self.log, 1)
        outer.addWidget(log_card, 1)

        # Avanzado
        adv = QHBoxLayout()
        self.cmb_profile = QComboBox()
        for code, name in PROFILES.items():
            self.cmb_profile.addItem(name, code)
        self.btn_ref = QPushButton("Muestra de voz…")
        self.btn_ref.setToolTip("Archivo .wav de 20–30 s con tu voz, para el perfil clonado.")
        self.btn_ref.clicked.connect(self._choose_reference)
        self.lbl_ref = QLabel("")
        self.lbl_ref.setObjectName("muted")
        adv.addWidget(self._muted_label("Perfil de voz"))
        adv.addWidget(self.cmb_profile)
        adv.addWidget(self.btn_ref)
        adv.addWidget(self.lbl_ref, 1)
        outer.addLayout(adv)
        self.cmb_profile.currentIndexChanged.connect(self._on_profile_changed)
        self._sync_profile_widgets()

    @staticmethod
    def _muted_label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("muted")
        return lab

    # -- dispositivos y ajustes --------------------------------------------------------------------

    def _load_devices(self) -> None:
        try:
            self._inputs = list_devices("input")
            self._outputs = list_devices("output")
        except Exception as exc:  # noqa: BLE001 - sin sounddevice o sin dispositivos
            log.warning("No se pudieron listar los dispositivos: %s", exc)
            self._inputs, self._outputs = [], []
        for combo, devices in ((self.cmb_input, self._inputs), (self.cmb_output, self._outputs)):
            combo.clear()
            for d in devices:
                combo.addItem(d.name, d.index)
        if not self._inputs or not self._outputs:
            self._show_warning("No se encontraron dispositivos de audio.")

    def _restore_settings(self) -> None:
        s = self._settings
        for combo, key in (
            (self.cmb_source, "source"),
            (self.cmb_target, "target"),
            (self.cmb_profile, "profile"),
        ):
            idx = combo.findData(s.value(key, type=str))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        for combo, key in ((self.cmb_input, "input"), (self.cmb_output, "output")):
            idx = combo.findText(str(s.value(key, "", type=str)))
            if idx >= 0:
                combo.setCurrentIndex(idx)
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
        s.setValue("input", self.cmb_input.currentText())
        s.setValue("output", self.cmb_output.currentText())
        s.setValue("reference", self.engine.config.reference_wav or "")

    def _check_devices(self) -> None:
        res = check_devices(self.cmb_input.currentText() or None, self.cmb_output.currentText() or None)
        if res.warnings:
            self._show_warning(" ".join(res.warnings))
        else:
            self.lbl_warning.hide()

    def _show_warning(self, text: str) -> None:
        self.lbl_warning.setText("⚠ " + text)
        self.lbl_warning.show()

    # -- carga de modelos --------------------------------------------------------------------------

    def reload_models(self) -> None:
        cfg = self.engine.config
        cfg.tts_profile = self.cmb_profile.currentData()

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
        cfg.output_device = self.cmb_output.currentData()

    def _toggle(self) -> None:
        if self.engine.state is EngineState.RUNNING:
            self.engine.stop()
            return
        self._apply_config()
        self._save_settings()
        try:
            self.engine.start()
        except EngineError as exc:
            self._on_error(str(exc))

    def _on_target_changed(self) -> None:
        if self.engine.state is EngineState.RUNNING:
            self.engine.set_target_language(self.cmb_target.currentData())

    def _on_profile_changed(self) -> None:
        self._sync_profile_widgets()
        if self.engine.state in (EngineState.READY, EngineState.ERROR, EngineState.IDLE):
            self.reload_models()

    def _sync_profile_widgets(self) -> None:
        clone = self.cmb_profile.currentData() == "clone"
        self.btn_ref.setVisible(clone)
        self.lbl_ref.setVisible(clone)

    def _choose_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Muestra de tu voz", "", "Audio WAV (*.wav)")
        if path:
            self.engine.config.reference_wav = path
            self.lbl_ref.setText(path.replace("\\", "/").rsplit("/", 1)[-1])
            self._save_settings()
            self.reload_models()

    # -- eventos del motor -------------------------------------------------------------------------

    def _on_state(self, state: EngineState, detail: str) -> None:
        text = detail or STATE_TEXT[state]
        color = theme.STATE_COLORS[state.value]
        self.state_pill.setText("● " + text)
        self.state_pill.setStyleSheet(
            f"color:{color}; background:{theme.SURFACE}; border:1px solid {theme.BORDER};"
            "border-radius:14px; padding:6px 12px; font-weight:600;"
        )
        running = state is EngineState.RUNNING
        ready = state is EngineState.READY
        self.btn_toggle.setEnabled(ready or running)
        self.btn_toggle.setText("Detener" if running else "Iniciar interpretación")
        self.btn_toggle.setObjectName("primary stop" if running else "primary")
        self.btn_toggle.setProperty("class", "stop" if running else "")
        self.btn_toggle.setStyleSheet(f"background:{theme.DANGER}; color:#2a0a0a;" if running else "")
        for w in (self.cmb_source, self.cmb_input, self.cmb_output, self.cmb_profile, self.btn_ref):
            w.setEnabled(not running and state is not EngineState.LOADING)
        if not running:
            self._level = 0.0

    def _on_level(self, rms: float) -> None:
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
