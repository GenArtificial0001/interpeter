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

from ..audio.calibrate import LoopbackReport, run_loopback_test
from ..audio.devices import AudioDevice, list_devices
from ..audio.voice_sample import record
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
from ..stages.backends import app_home
from . import theme
from .voice_dialog import VoiceSampleDialog

log = logging.getLogger(__name__)

STATE_TEXT = {
    EngineState.IDLE: "Sin cargar",
    EngineState.LOADING: "Cargando modelos…",
    EngineState.READY: "Listo",
    EngineState.RUNNING: "Interpretando",
    EngineState.ERROR: "Error",
}

PROFILES = {"fast": "Rápido (sin clonar)", "clone": "Clonado (XTTSv2, más lento)"}

STT_MODELS = [
    ("Rápido · Whisper base", "openai/whisper-base"),
    ("Preciso · Whisper small", "openai/whisper-small"),
]
ACCELERATORS = [("Automática (GPU Intel si hay)", "auto"), ("Solo CPU", "cpu")]

# (texto, milisegundos de silencio que cierran una frase)
PAUSES = [("Rápida · 320 ms", 320), ("Normal · 480 ms", 480), ("Lenta · 700 ms", 700)]

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
    loop = Signal(object)

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
        recorder: Callable[..., tuple] = record,
        loop_runner: Callable[..., LoopbackReport] = run_loopback_test,
    ) -> None:
        super().__init__()
        self._recorder = recorder
        self._loop_runner = loop_runner
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
        self._bridge.loop.connect(self._on_loop_result)

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
        audio_lay.addWidget(self.lbl_loop)
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
        outer.addLayout(rec)
        self.cmb_stt.currentIndexChanged.connect(self._on_profile_changed)
        self.cmb_accel.currentIndexChanged.connect(self._on_profile_changed)
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
            (self.cmb_pause, "pause"),
            (self.cmb_stt, "stt"),
            (self.cmb_accel, "accel"),
        ):
            value = str(s.value(key, "", type=str))
            idx = combo.findData(value)
            if idx < 0 and value.isdigit():  # datos numéricos (p. ej. la pausa) se guardan como texto
                idx = combo.findData(int(value))
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
        s.setValue("pause", self.cmb_pause.currentData())
        s.setValue("stt", self.cmb_stt.currentData())
        s.setValue("accel", self.cmb_accel.currentData())
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
        cfg.output_device = self.cmb_output.currentData()
        cfg.pause_ms = int(self.cmb_pause.currentData())

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
        # Grabar la muestra se puede hacer siempre; elegir un .wav y su nombre solo importan al clonar.
        clone = self.cmb_profile.currentData() == "clone"
        self.btn_ref.setVisible(clone)
        self.lbl_ref.setVisible(clone or bool(self.engine.config.reference_wav))

    def _record_voice(self) -> None:
        dlg = VoiceSampleDialog(
            self,
            self.cmb_input.currentData(),
            app_home() / "voices" / "mi_voz.wav",
            recorder=self._recorder,
        )
        if dlg.exec() and dlg.saved_path is not None:
            self._set_reference(str(dlg.saved_path))

    def _set_reference(self, path: str) -> None:
        self.engine.config.reference_wav = path
        self.lbl_ref.setText(path.replace("\\", "/").rsplit("/", 1)[-1])
        self._sync_profile_widgets()
        self._save_settings()
        if self.cmb_profile.currentData() == "clone":
            self.reload_models()

    def _run_loop_test(self) -> None:
        if self.engine.state is EngineState.RUNNING:
            self.lbl_loop.setText("Detén la interpretación antes de probar el bucle.")
            return
        out, inp = self.cmb_output.currentData(), self.cmb_input.currentData()
        self.btn_loop.setEnabled(False)
        self.lbl_loop.setText("Sonará un tono breve por la salida elegida…")

        def work() -> None:
            try:
                self._bridge.loop.emit(self._loop_runner(out, inp))
            except Exception as exc:  # noqa: BLE001 - se informa en la interfaz
                log.exception("Fallo en la prueba del bucle")
                self._bridge.error.emit(f"La prueba del bucle falló: {exc}")
                self._bridge.loop.emit(None)

        threading.Thread(target=work, name="loop-test", daemon=True).start()

    def _on_loop_result(self, report: object) -> None:
        self.btn_loop.setEnabled(True)
        if report is None:
            self.lbl_loop.setText("")
            return
        r: LoopbackReport = report  # type: ignore[assignment]
        mark = "✓" if r.verdict == "ok" else "⚠"
        self.lbl_loop.setText(
            f"{mark} {r.advice}  (tono {r.tone_dbfs:.0f} dBFS · relación señal/ruido {r.snr_db:.0f} dB)"
        )

    def _choose_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Muestra de tu voz", "", "Audio WAV (*.wav)")
        if path:
            self._set_reference(path)

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
        editable = not running and state is not EngineState.LOADING
        for w in (
            self.cmb_source,
            self.cmb_input,
            self.cmb_output,
            self.cmb_profile,
            self.cmb_pause,
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
