"""Diálogo guiado para grabar la muestra de voz del perfil clonado."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..audio.voice_sample import GOOD_SECONDS, PROMPT_TEXT, SampleReport, assess_sample, record, save_sample

log = logging.getLogger(__name__)

Recorder = Callable[..., tuple[np.ndarray, int]]


class _Signals(QObject):
    level = Signal(float)
    done = Signal(object)
    failed = Signal(str)


class VoiceSampleDialog(QDialog):
    """Lee un texto durante ~25 s, evalúa la grabación y la guarda si es buena."""

    def __init__(
        self,
        parent: QWidget | None,
        device: int | None,
        save_path: Path,
        recorder: Recorder = record,
        seconds: float = GOOD_SECONDS + 5,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Grabar mi voz")
        self.setMinimumWidth(560)
        self._device, self._save_path, self._recorder, self._seconds = device, save_path, recorder, seconds
        self._audio: np.ndarray | None = None
        self._sr = 16000
        self._elapsed = 0.0
        self.saved_path: Path | None = None
        self.report: SampleReport | None = None

        self._sig = _Signals()
        self._sig.level.connect(self._on_level)
        self._sig.done.connect(self._on_done)
        self._sig.failed.connect(self._on_failed)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel(
            "Lee el texto de abajo en voz natural, como en una reunión, a unos 20 cm del micrófono y en un "
            "lugar silencioso. Dura unos 25 segundos."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.text = QPlainTextEdit(PROMPT_TEXT)
        self.text.setReadOnly(True)
        self.text.setFixedHeight(140)
        lay.addWidget(self.text)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        lay.addWidget(self.level)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p %")
        lay.addWidget(self.progress)
        self.lbl = QLabel("Pulsa «Grabar» cuando estés listo.")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)

        row = QHBoxLayout()
        self.btn_record = QPushButton("Grabar")
        self.btn_record.setObjectName("primary")
        self.btn_record.clicked.connect(self._start)
        self.btn_use = QPushButton("Usar esta muestra")
        self.btn_use.setEnabled(False)
        self.btn_use.clicked.connect(self._accept)
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_record, 1)
        row.addWidget(self.btn_use)
        row.addWidget(btn_cancel)
        lay.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    # -- grabación ---------------------------------------------------------------------------------

    def _start(self) -> None:
        self.btn_record.setEnabled(False)
        self.btn_use.setEnabled(False)
        self._elapsed = 0.0
        self.lbl.setText("Grabando… lee el texto.")
        self._timer.start()

        def work() -> None:
            try:
                audio, sr = self._recorder(self._device, self._seconds, self._sig.level.emit)
                self._sig.done.emit((audio, sr))
            except Exception as exc:  # noqa: BLE001 - se informa al usuario
                log.exception("Fallo al grabar la muestra")
                self._sig.failed.emit(str(exc))

        threading.Thread(target=work, name="record-sample", daemon=True).start()

    def _tick(self) -> None:
        self._elapsed += 0.1
        self.progress.setValue(int(min(1.0, self._elapsed / self._seconds) * 1000))

    def _on_level(self, rms: float) -> None:
        db = 20 * np.log10(max(rms, 1e-6))
        self.level.setValue(int(max(0.0, min(1.0, (db + 60.0) / 60.0)) * 100))

    def _on_done(self, payload: Any) -> None:
        self._timer.stop()
        self.progress.setValue(1000)
        self.level.setValue(0)
        self._audio, self._sr = payload
        self.report = assess_sample(self._audio, self._sr)
        r = self.report
        lines = [
            f"Duración {r.seconds:.0f} s · voz {r.level_dbfs:.0f} dBFS · ruido {r.noise_floor_dbfs:.0f} dBFS"
        ]
        lines += [f"⚠ {w}" for w in r.warnings]
        if r.ok and not r.warnings:
            lines.append("✓ Muestra buena.")
        elif not r.ok:
            lines.append("No se puede usar esta muestra: corrige lo indicado y graba de nuevo.")
        self.lbl.setText("\n".join(lines))
        self.btn_record.setEnabled(True)
        self.btn_record.setText("Grabar de nuevo")
        self.btn_use.setEnabled(r.ok)

    def _on_failed(self, message: str) -> None:
        self._timer.stop()
        self.lbl.setText(f"⚠ No se pudo grabar: {message}")
        self.btn_record.setEnabled(True)

    def _accept(self) -> None:
        if self._audio is None:
            return
        self.saved_path = save_sample(self._audio, self._sr, self._save_path)
        self.accept()
