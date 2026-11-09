# ruff: noqa: E501
"""Interruptor ON / OFF: un ``QCheckBox`` que se dibuja como interruptor (solo cambia el aspecto, no el comportamiento)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QWidget

from . import theme

TRACK_W, TRACK_H, GAP = 46, 24, 10


class Toggle(QCheckBox):
    """Se usa igual que un ``QCheckBox`` (``isChecked``, ``setChecked``, ``toggled``…); se ve como un interruptor."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802
        text_w = self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0
        return QSize(
            TRACK_W + (GAP + text_w if text_w else 0) + 2, max(TRACK_H + 4, self.fontMetrics().height() + 4)
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def hitButton(self, pos) -> bool:  # noqa: ANN001, N802
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, enabled = self.isChecked(), self.isEnabled()
        track = QRectF(1, (self.height() - TRACK_H) / 2, TRACK_W, TRACK_H)
        if on:
            color = QColor(theme.ACCENT if enabled else theme.LAVENDER)
        else:
            color = QColor(theme.BORDER_STRONG if enabled else theme.SURFACE_3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(track, TRACK_H / 2, TRACK_H / 2)

        knob = TRACK_H - 6
        kx = track.right() - 3 - knob if on else track.left() + 3
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(kx, track.top() + 3, knob, knob))

        font = QFont(self.font())
        font.setPixelSize(9)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#FFFFFF"))
        label_box = QRectF(track.left() + (8 if on else TRACK_H), track.top(), TRACK_W - TRACK_H - 2, TRACK_H)
        p.drawText(label_box, Qt.AlignmentFlag.AlignCenter, "ON" if on else "OFF")

        if self.text():
            p.setFont(self.font())
            p.setPen(QColor(theme.TEXT if enabled else theme.MUTED))
            p.drawText(
                QRectF(TRACK_W + GAP, 0, self.width() - TRACK_W - GAP, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.ACCENT), 1, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), TRACK_H / 2 + 2, TRACK_H / 2 + 2)
