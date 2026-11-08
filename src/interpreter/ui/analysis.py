"""Página «Análisis» de Ajustes: retraso, descartes, recursos del proceso y la transcripción.

Solo lee: no toca el motor. CPU y RAM (``psutil``) se refrescan cada 1 s SOLO con la página visible,
para que mirar el análisis no le quite rendimiento al intérprete.
"""

from __future__ import annotations

import statistics
from collections import Counter, deque
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

HISTORY = 50  # frases que cuentan para mediana / p90 / mínimo / máximo
BARS = 12  # barras del gráfico (las últimas frases)
GOOD_S, SLOW_S = 1.5, 2.5  # umbrales de color del retraso
REASONS = {
    "idioma_distinto": "idioma distinto",
    "eco": "eco",
    "poca_confianza": "poca confianza",
    "cola": "cola llena",
    "silenciado": "silenciado",
    "sin_texto": "sin texto",
    "traduccion_vacia": "traducción vacía",
    "sin_audio": "sin audio",
}


def latency_color(seconds: float) -> QColor:
    return (
        QColor(theme.ACCENT)
        if seconds <= GOOD_S
        else QColor(theme.WARN)
        if seconds <= SLOW_S
        else QColor(theme.DANGER)
    )


def percentile(values: list[float], q: float) -> float:
    """Percentil ``q`` (0..1) por interpolación; 0 si no hay datos."""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


class LatencyBars(QWidget):
    """Barras de las últimas frases (alto = retraso; color según el umbral)."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[float] = []
        self.setMinimumHeight(54)

    def set_values(self, values: list[float]) -> None:
        self.values = values[-BARS:]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.values:
            return
        top = max(max(self.values), SLOW_S * 1.2)
        slot = self.width() / BARS
        for i, v in enumerate(self.values):
            h = max(3.0, (self.height() - 4) * v / top)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(latency_color(v))
            p.drawRoundedRect(QRectF(i * slot + slot * 0.18, self.height() - h, slot * 0.64, h), 3, 3)


class AnalysisPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.latencies: deque[float] = deque(maxlen=HISTORY)
        self.drops: Counter[str] = Counter()
        self.translated = 0
        self.connection_text = ""
        self.mic_events = 0
        self._proc = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        card = self._card()
        row = QHBoxLayout()
        self.lbl_delay = QLabel("—")
        self.lbl_delay.setObjectName("metric")
        self.lbl_detail = QLabel("Sin datos todavía")
        self.lbl_detail.setObjectName("muted")
        row.addWidget(self.lbl_delay)
        row.addSpacing(12)
        row.addWidget(self.lbl_detail, 1)
        card.layout().addWidget(self._section("Retraso desde que terminas de hablar"))
        card.layout().addLayout(row)
        self.lbl_stats = QLabel("")
        self.lbl_stats.setObjectName("mono")
        card.layout().addWidget(self.lbl_stats)
        self.bars = LatencyBars()
        card.layout().addWidget(self.bars)
        lay.addWidget(card)

        card2 = self._card()
        card2.layout().addWidget(self._section("Sesión"))
        self.lbl_counts = QLabel("")
        self.lbl_counts.setObjectName("mono")
        self.lbl_counts.setWordWrap(True)
        self.lbl_res = QLabel("")
        self.lbl_res.setObjectName("mono")
        self.lbl_conn = QLabel("")
        self.lbl_conn.setObjectName("mono")
        self.lbl_conn.setWordWrap(True)
        for w in (self.lbl_counts, self.lbl_res, self.lbl_conn):
            card2.layout().addWidget(w)
        lay.addWidget(card2)

        card3 = self._card()
        head = QHBoxLayout()
        head.addWidget(self._section("Transcripción"), 1)
        for text, slot in (
            ("Copiar", self.copy_log),
            ("Guardar…", self.save_log),
            ("Limpiar", self.clear_log),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            head.addWidget(b)
        card3.layout().addLayout(head)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMinimumHeight(120)
        card3.layout().addWidget(self.log, 1)
        lay.addWidget(card3, 1)

        self._timer = QTimer(self)  # CPU/RAM: solo con la página visible
        self._timer.timeout.connect(self.refresh_resources)
        self._render()

    @staticmethod
    def _card() -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)
        return frame

    @staticmethod
    def _section(text: str) -> QLabel:
        lab = QLabel(text.upper())
        lab.setObjectName("sectionLabel")
        return lab

    # -- datos ----------------------------------------------------------------------------------------------

    def add_latency(self, seconds: float, stt: float | None = None, mt: float | None = None) -> None:
        self.latencies.append(seconds)
        parts = [
            f"voz→texto {stt:.1f} s" if stt is not None else "",
            f"traducción {mt:.1f} s" if mt is not None else "",
        ]
        parts.append(f"mediana {statistics.median(self.latencies):.1f} s")
        self.lbl_delay.setText(f"{seconds:.1f} s")
        self.lbl_detail.setText("  ·  ".join(p for p in parts if p))
        self._render()

    def count_translated(self) -> None:
        self.translated += 1
        self._render()

    def count_drop(self, reason: str) -> None:
        self.drops[reason] += 1
        self._render()

    def set_connection(self, text: str) -> None:
        self.connection_text = text
        self._render()

    def stats(self) -> dict[str, float]:
        v = list(self.latencies)
        if not v:
            return {}
        return {
            "mediana": statistics.median(v),
            "p90": percentile(v, 0.9),
            "min": min(v),
            "max": max(v),
            "n": len(v),
        }

    def _render(self) -> None:
        st = self.stats()
        self.lbl_stats.setText(
            f"mediana {st['mediana']:.1f} s · p90 {st['p90']:.1f} s · mín {st['min']:.1f} s · "
            f"máx {st['max']:.1f} s  ({int(st['n'])} frases)"
            if st
            else "Aún no hay frases traducidas."
        )
        self.bars.set_values(list(self.latencies))
        total_drops = sum(self.drops.values())
        detail = ", ".join(f"{REASONS.get(k, k)} {n}" for k, n in self.drops.most_common()) or "ninguna"
        pct = (
            f" ({100 * total_drops / (total_drops + self.translated):.0f} %)"
            if self.translated + total_drops
            else ""
        )
        self.lbl_counts.setText(f"Traducidas: {self.translated}\nDescartadas: {total_drops}{pct} · {detail}")
        extra = f"\nVigilante del micrófono: {self.mic_events} intervenciones" if self.mic_events else ""
        self.lbl_conn.setText((self.connection_text or "Navegador: sin datos") + extra)

    # -- recursos (solo con la página visible) --------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self.refresh_resources()
        self._timer.start(1000)

    def hideEvent(self, event) -> None:  # noqa: ANN001, N802
        self._timer.stop()
        super().hideEvent(event)

    def refresh_resources(self) -> None:
        try:
            import psutil

            if self._proc is None:
                self._proc = psutil.Process()
                self._proc.cpu_percent(None)
            cpu = self._proc.cpu_percent(None) / max(1, psutil.cpu_count() or 1)
            ram = self._proc.memory_info().rss / 1024**3
            self.lbl_res.setText(f"Interpreter: CPU {cpu:.0f} % del equipo · memoria {ram:.1f} GB")
        except Exception:  # noqa: BLE001 - sin psutil no hay este dato y nada más se afecta
            self.lbl_res.setText("Recursos: no disponibles")

    # -- transcripción -----------------------------------------------------------------------

    def copy_log(self) -> None:
        QGuiApplication.clipboard().setText(self.log.toPlainText())

    def clear_log(self) -> None:
        self.log.clear()

    def save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar transcripción", "transcripcion.txt", "Texto (*.txt)"
        )
        if path:
            Path(path).write_text(self.log.toPlainText(), encoding="utf-8")
