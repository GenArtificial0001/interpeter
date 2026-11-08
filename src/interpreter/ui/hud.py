"""«Orbe»: la interfaz alternativa de Interpreter, una ventana SIN marco y con fondo transparente.

Es opcional y vive aparte de la ventana clásica (``window.py``): usa el mismo motor y no cambia el audio.
Lo que no se dibuja es transparente (Windows deja pasar los clics al escritorio en esos píxeles).

Se dibuja con ``QPainter`` (sin imágenes ni desenfoques, que gastarían la CPU que necesitan los modelos):
anillos con brillo, marcas, barras de ecualizador y un orbe central cuyo punto cambia de color con el
estado. La animación va a ~30 fps con voz y a ~8 fps en reposo, y se detiene si la ventana está oculta.
"""

from __future__ import annotations

import math
import random
import time
from collections import deque
from collections.abc import Callable
from enum import Enum

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QMenu, QWidget

SIZE = 480  # tamaño inicial (la rueda del ratón lo cambia entre 320 y 900)
BARS = 96
TICKS = 90
VU_STEPS = 24  # pasos de la barra de escucha (animación «ligera»: solo se repinta si cambia el paso)

# Animación: lo que MÁS importa es que el intérprete no pierda rendimiento.
LIGHT, FULL, NONE = "ligera", "completa", "ninguna"
ANIMATIONS = {LIGHT: "Ligera (solo la barra de escucha)", FULL: "Completa", NONE: "Sin animación"}
SLOW_PAINT_MS = 30.0  # si pintar tarda más que esto de media, la animación completa se apaga sola
SLOW_PAINT_FRAMES = 30


class Mood(Enum):
    """Qué muestra el orbe (independiente de ``EngineState`` para poder probarlo sin motor)."""

    LOADING = "loading"
    READY = "ready"
    RUNNING = "running"
    MUTED = "muted"
    ERROR = "error"


# Color del punto central y del brillo según el estado: (punto, acento)
# Nombre de cada idioma en el propio idioma (como en el diseño: «Español → English»)
NATIVE = {"es": "Español", "en": "English", "pt": "Português", "fr": "Français"}

PALETTE = {
    Mood.LOADING: (QColor("#f5a623"), QColor("#5aa9ff")),
    Mood.READY: (QColor("#9fb4d6"), QColor("#4a90e2")),
    Mood.RUNNING: (QColor("#ff2d55"), QColor("#35c8ff")),
    Mood.MUTED: (QColor("#6b7280"), QColor("#3b5b86")),
    Mood.ERROR: (QColor("#ff453a"), QColor("#c0392b")),
}
TITLES = {
    Mood.LOADING: "CARGANDO MODELOS…",
    Mood.READY: "LISTO",
    Mood.RUNNING: "INTERPRETANDO…",
    Mood.MUTED: "SILENCIADO",
    Mood.ERROR: "ERROR",
}


def orb_icon(size: int = 64) -> QIcon:
    """Ícono de la aplicación (el orbe), para la barra de tareas."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(size / 2, size / 2)
    g = QRadialGradient(c, size * 0.48)
    g.setColorAt(0.0, QColor("#0b1f66"))
    g.setColorAt(0.55, QColor("#1f5bff"))
    g.setColorAt(1.0, QColor("#7fd0ff"))
    p.setBrush(QBrush(g))
    p.setPen(QPen(QColor("#bfe6ff"), size * 0.04))
    p.drawEllipse(c, size * 0.42, size * 0.42)
    p.setBrush(QColor("#ff2d55"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(c, size * 0.06, size * 0.06)
    p.end()
    return QIcon(pm)


class OrbWindow(QWidget):
    """Ventana flotante sin marco. Emite señales; quien la usa decide qué hacer (ver ``MainWindow``)."""

    toggle_requested = Signal()  # clic en el orbe: iniciar / detener
    mic_requested = Signal()  # clic en «Micrófono»: silenciar / reanudar
    settings_requested = Signal()
    input_requested = Signal()  # clic en «Entrada»: elegir el micrófono
    output_requested = Signal()  # clic en «Salida»: elegir dónde suena la voz traducida
    classic_requested = Signal()  # menú: volver a la ventana clásica
    closed = Signal()  # la ventana se cerró (Alt+F4 o menú «Salir»)
    volume_step = Signal(int)  # rueda sobre «Micrófono»: +5 / -5 (volumen de Windows)
    sys_mic_toggle_requested = Signal()  # menú: encender o apagar el micrófono
    reconnect_requested = Signal()  # menú: reiniciar el micrófono
    animation_changed = Signal(str)  # el modo de animación cambió (menú o apagado automático por lentitud)

    BUTTONS = ("settings", "input", "mic", "output")  # orden de hit-test

    def __init__(self, size: int = SIZE, now: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self.setWindowTitle("Interpreter")
        self.setWindowIcon(orb_icon())
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(320, 320)
        self.resize(size, size)
        self._now = now
        self.mood = Mood.LOADING
        self.src_name, self.dst_name = "Español", "English"
        self.muted = False
        self.caption = ""
        self._phase = 0.0
        self._last_tick = now()
        self._level = 0.0  # nivel suavizado del micrófono (0..1)
        self._speak_until = 0.0
        self._history: deque[float] = deque(maxlen=BARS)
        rng = random.Random(7)  # perfil fijo por barra: la voz real modula la altura, no inventa movimiento
        self._profile = [0.35 + 0.65 * rng.random() for _ in range(BARS)]
        self._bars = [0.0] * BARS
        self._press_pos: QPointF | None = None
        self._cache: dict[str, tuple[tuple, QPixmap]] = {}  # capas ya dibujadas (ver _layer)
        self.animation = LIGHT  # por defecto la más barata
        self.sys_mic_muted: bool | None = (
            None  # estado del micrófono de Windows (None = no se puede controlar)
        )
        self._vu = 0  # pasos encendidos de la barra de escucha
        self._prog_target = 0.0  # avance real (0..1) informado por el motor
        self._prog_shown = 0.0  # avance que se muestra (suavizado)
        self.stage_label = ""
        self.latency: tuple[float, float] | None = None  # (último, mediana) en segundos
        self._paint_ms: deque[float] = deque(maxlen=SLOW_PAINT_FRAMES)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(80)

    # -- API que usa MainWindow -----------------------------------------------------------------------------

    def set_mood(self, mood: Mood) -> None:
        self.mood = mood
        self.update()

    def set_languages(self, source: str, target: str) -> None:
        self.src_name, self.dst_name = source, target
        self.update()

    def set_progress(self, fraction: float, label: str = "") -> None:
        """Avance de la carga (estimado por etapas): el número nunca baja ni llega a 100 % antes de tiempo."""
        fraction = max(0.0, min(1.0, fraction))
        if fraction >= self._prog_target:
            self._prog_target = fraction
        if fraction >= 1.0:
            self._prog_shown = 1.0
        self.stage_label = label
        self.update()

    def set_latency(self, last: float | None, median: float | None = None) -> None:
        self.latency = None if last is None else (last, median if median is not None else last)
        self.update()

    @property
    def progress_percent(self) -> int:
        return int(self._prog_shown * 100)

    def set_animation(self, mode: str) -> None:
        """``ligera`` (solo la barra de escucha), ``completa`` o ``ninguna``."""
        if mode not in ANIMATIONS or mode == self.animation:
            return
        self.animation = mode
        self._paint_ms.clear()
        self._timer.setInterval(80)
        self.animation_changed.emit(mode)
        self.update()

    def push_level(self, rms: float) -> None:
        """Nivel del micrófono (RMS 0..~0,3): mueve las barras. Barato: solo guarda el valor."""
        self._level = max(self._level * 0.6, min(1.0, rms * 6.0))

    def speak(self, seconds: float) -> None:
        """La traducción está sonando durante ~``seconds``: el orbe lo muestra con un pulso."""
        self._speak_until = self._now() + max(0.3, seconds)

    def set_caption(self, text: str) -> None:
        self.caption = text.strip()[:90]
        self.update()

    # -- geometría -------------------------------------------------------------

    def _center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def _unit(self) -> float:
        """Radio de referencia: el mayor círculo que cabe con margen para las barras."""
        return min(self.width(), self.height()) / 2

    def button_rects(self) -> dict[str, QRectF]:
        u, c = self._unit(), self._center()
        w, h = u * 0.26, u * 0.22
        return {  # dentro del anillo grande (radio 0,52): a los lados y debajo del orbe, como en el diseño
            "settings": QRectF(c.x() - u * 0.41 - w / 2, c.y() - h / 2, w, h),
            "input": QRectF(c.x() + u * 0.41 - w / 2, c.y() - h / 2, w, h),
            "mic": QRectF(c.x() - u * 0.15 - w / 2, c.y() + u * 0.255, w, h),
            "output": QRectF(c.x() + u * 0.15 - w / 2, c.y() + u * 0.255, w, h),
        }

    def hit_test(self, pos: QPointF) -> str:
        """``orb`` | ``settings`` | ``input`` | ``mic`` | ``output`` | ``drag`` (resto) | ``none``."""
        c, u = self._center(), self._unit()
        d = math.hypot(pos.x() - c.x(), pos.y() - c.y())
        if d <= u * 0.17:
            return "orb"
        for name, rect in self.button_rects().items():
            if rect.contains(pos):
                return name
        return "drag" if d <= u * 0.98 else "none"

    # -- animación -------------------------------------------------------------

    def _active(self) -> bool:
        return self.mood is Mood.RUNNING and self._level > 0.02 or self._now() < self._speak_until

    def _tick(self) -> None:
        if not self.isVisible():
            return
        if self.mood is Mood.LOADING:
            self._tick_loading()
            return
        if self.animation != FULL:
            self._tick_light()
            return
        now = self._now()
        dt = max(0.0, min(0.5, now - self._last_tick))
        self._last_tick = now
        active = self._active()
        self._phase = (self._phase + dt * (0.5 if active else 0.12)) % 1.0
        self._history.append(self._level)
        speaking = now < self._speak_until
        for i in range(BARS):
            lvl = self._level if self.mood is Mood.RUNNING else 0.0
            if speaking:
                lvl = max(lvl, 0.55 + 0.25 * math.sin(self._phase * 40 + i * 0.7))
            target = lvl * self._profile[i]
            self._bars[i] += (target - self._bars[i]) * (0.55 if target > self._bars[i] else 0.25)
        self._level *= 0.7
        self._timer.setInterval(33 if active or any(b > 0.03 for b in self._bars) else 120)
        self.update()

    def _tick_loading(self) -> None:
        """Carga: el número sigue al avance real y «repta» hasta +8 puntos para no parecer detenido."""
        cap = 1.0 if self._prog_target >= 1.0 else min(0.99, self._prog_target + 0.08)
        goal = self._prog_target if self._prog_shown >= self._prog_target else self._prog_target
        if self._prog_shown < goal:
            self._prog_shown = min(goal, self._prog_shown + max(0.004, (goal - self._prog_shown) * 0.25))
        elif self._prog_shown < cap:
            self._prog_shown = min(cap, self._prog_shown + 0.0015)
        steps = min(VU_STEPS, int(self._prog_shown * VU_STEPS))
        if steps != self._vu or self.progress_percent != getattr(self, "_last_percent", -1):
            self._vu = steps
            self._last_percent = self.progress_percent
            self.update()

    def _tick_light(self) -> None:
        """Animación ligera: solo la barra de escucha; se repinta ÚNICAMENTE si cambia su paso (≤12 fps)."""
        self._last_tick = self._now()
        steps = 0
        if self.animation == LIGHT and self.mood is Mood.RUNNING and not self.muted:
            steps = min(VU_STEPS, int(self._level * VU_STEPS + 0.5))
        self._level *= 0.6
        if steps != self._vu:
            self._vu = steps
            self.update()

    # -- dibujo ----------------------------------------------------------------

    def _layer(self, name: str, key: tuple, paint: Callable[[QPainter], None]) -> QPixmap:
        """Capa ya dibujada (se rehace solo si cambia ``key``): lo costoso no se repite en cada cuadro."""
        cached = self._cache.get(name)
        if cached is not None and cached[0] == key:
            return cached[1]
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint(p)
        p.end()
        self._cache[name] = (key, pm)
        return pm

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        started = time.perf_counter()
        dot, accent = PALETTE[self.mood]
        c, u = self._center(), self._unit()
        dim = 1.0 if self.mood in (Mood.RUNNING, Mood.LOADING) else 0.7
        geom = (self.width(), self.height(), self.devicePixelRatioF(), self.mood)

        def static(q: QPainter) -> None:
            self._draw_glow(q, c, u, accent, dim)
            self._draw_thin_rings(q, c, u, accent, dim)
            self._draw_ticks(q, c, u, accent, dim, lit=None)
            self._draw_texts(q, c, u, accent)
            self._draw_latency(q, c, u)
            self._draw_buttons(q, accent)

        text_key = (self.src_name, self.dst_name, self.muted, self.caption, self.stage_label, self.latency)
        base = self._layer("static", (*geom, *text_key), static)
        arc = self._layer("arc", geom, lambda q: self._draw_arc(q, c, u, accent, dim))
        orb = self._layer("orb", geom, lambda q: self._draw_orb(q, c, u, accent, dot))

        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)  # todo transparente: lo que no se dibuja no se ve
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.drawPixmap(0, 0, base)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        full = self.animation == FULL and self.mood is not Mood.LOADING
        if self.mood is Mood.LOADING and self._vu:
            self._draw_vu(p, c, u, accent, dim, lit=self._vu)
        p.save()  # el anillo brillante gira (solo en la animación completa): se gira la capa ya dibujada
        p.translate(c)
        p.rotate(-self._phase * 360.0 if full else 0.0)
        p.translate(-c)
        p.drawPixmap(0, 0, arc)
        p.restore()
        if full:
            self._draw_ticks(p, c, u, accent, dim, lit=int(self._phase * TICKS))
            self._draw_bars(p, c, u, accent, dim)
        elif self._vu:
            self._draw_vu(p, c, u, accent, dim, lit=self._vu)
        pulse = 1.0
        if full:
            pulse += 0.04 * math.sin(self._phase * 2 * math.pi) if self.mood is Mood.RUNNING else 0.0
            if self._now() < self._speak_until:
                pulse += 0.05
        p.save()  # el orbe «respira»: se escala la capa ya dibujada alrededor del centro
        p.translate(c)
        p.scale(pulse, pulse)
        p.translate(-c)
        p.drawPixmap(0, 0, orb)
        p.restore()
        if self.mood is Mood.LOADING:
            self._draw_percent(p, c, u)
        p.end()
        self._watch_paint_cost((time.perf_counter() - started) * 1000.0)

    def _watch_paint_cost(self, ms: float) -> None:
        """Si pintar es lento (CPU saturada), la animación completa se apaga sola: el intérprete manda."""
        if self.animation != FULL:
            return
        self._paint_ms.append(ms)
        if (
            len(self._paint_ms) == SLOW_PAINT_FRAMES
            and sum(self._paint_ms) / SLOW_PAINT_FRAMES > SLOW_PAINT_MS
        ):
            self.set_animation(LIGHT)

    def _draw_vu(self, p: QPainter, c: QPointF, u: float, accent: QColor, dim: float, lit: int) -> None:
        """Barra de escucha CIRCULAR alrededor del ojo: las marcas se encienden desde arriba a los lados."""
        r_in, r_out = u * 0.255, u * 0.285
        per_side = lit / VU_STEPS * (TICKS / 2)  # marcas encendidas a cada lado del punto más alto
        lines: list[QLineF] = []
        for i in range(TICKS):
            if min(i, TICKS - i) >= per_side:
                continue
            ang = 2 * math.pi * i / TICKS - math.pi / 2
            lines.append(
                QLineF(
                    c.x() + r_in * math.cos(ang),
                    c.y() + r_in * math.sin(ang),
                    c.x() + r_out * math.cos(ang),
                    c.y() + r_out * math.sin(ang),
                )
            )
        col = QColor("#ffffff")
        col.setAlphaF(0.95 * dim)
        p.setPen(QPen(col, max(2.0, u * 0.014), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLines(lines)

    def _draw_glow(self, p: QPainter, c: QPointF, u: float, accent: QColor, dim: float) -> None:
        g = QRadialGradient(c, u * 0.95)
        a = QColor(accent)
        a.setAlphaF(0.20 * dim)
        g.setColorAt(0.0, a)
        a2 = QColor(accent)
        a2.setAlphaF(0.07 * dim)
        g.setColorAt(0.55, a2)
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, u * 0.95, u * 0.95)

    def _draw_thin_rings(self, p: QPainter, c: QPointF, u: float, accent: QColor, dim: float) -> None:
        for r, alpha, width in ((u * 0.33, 0.35, 1.2), (u * 0.36, 0.25, 1.0)):
            col = QColor(accent)
            col.setAlphaF(alpha * dim)
            p.setPen(QPen(col, width))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r, r)

    def _draw_arc(self, p: QPainter, c: QPointF, u: float, accent: QColor, dim: float) -> None:
        """El gran anillo brillante abierto, dibujado en reposo (ángulo 0); gira al pintarlo."""
        r = u * 0.52
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        start = -60 * 16
        for width, alpha in ((16, 0.10), (10, 0.18), (6, 0.35), (3, 0.9)):
            grad = QConicalGradient(c, 60.0)
            bright = QColor("#e8f8ff")
            bright.setAlphaF(min(1.0, alpha * 1.4) * dim)
            tail = QColor(accent)
            tail.setAlphaF(0.0)
            mid = QColor(accent)
            mid.setAlphaF(alpha * dim)
            grad.setColorAt(0.0, bright)
            grad.setColorAt(0.45, mid)
            grad.setColorAt(0.75, tail)
            grad.setColorAt(1.0, bright)
            pen = QPen(QBrush(grad), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, start, 270 * 16)

    def _draw_ticks(
        self, p: QPainter, c: QPointF, u: float, accent: QColor, dim: float, lit: int | None
    ) -> None:
        """Marcas del anillo interior: todas tenues (``lit=None``) o solo las encendidas que giran."""
        r_in, r_out = u * 0.255, u * 0.285
        for i in range(TICKS):
            if lit is None:
                col, width = QColor(accent), 1.2
                col.setAlphaF(0.35 * dim)
            else:
                if ((i - lit) % TICKS) >= TICKS * 0.35:
                    continue
                col, width = QColor("#ffffff"), 2.0
                col.setAlphaF(0.9 * dim)
            ang = 2 * math.pi * i / TICKS - math.pi / 2
            p.setPen(QPen(col, width))
            p.drawLine(
                QPointF(c.x() + r_in * math.cos(ang), c.y() + r_in * math.sin(ang)),
                QPointF(c.x() + r_out * math.cos(ang), c.y() + r_out * math.sin(ang)),
            )

    def _draw_bars(self, p: QPainter, c: QPointF, u: float, accent: QColor, dim: float) -> None:
        """Barras del ecualizador, agrupadas por intensidad (un lápiz por grupo, no uno por barra)."""
        r0 = u * 0.60
        width = max(1.5, u * 0.012)
        groups: dict[int, list[QLineF]] = {}
        for i, v in enumerate(self._bars):
            if v < 0.004 and self.mood is not Mood.RUNNING:
                continue
            ang = 2 * math.pi * i / BARS - math.pi / 2
            length = u * (0.015 + 0.28 * v)
            line = QLineF(
                c.x() + r0 * math.cos(ang),
                c.y() + r0 * math.sin(ang),
                c.x() + (r0 + length) * math.cos(ang),
                c.y() + (r0 + length) * math.sin(ang),
            )
            groups.setdefault(min(5, int(v * 8)), []).append(line)
        for level, lines in groups.items():
            v = (level + 0.5) / 8
            col = QColor(accent).lighter(100 + int(60 * v))
            col.setAlphaF((0.35 + 0.65 * min(1.0, v * 1.5)) * dim)
            p.setPen(QPen(col, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLines(lines)

    def _draw_orb(self, p: QPainter, c: QPointF, u: float, accent: QColor, dot: QColor) -> None:
        r = u * 0.17
        for k, alpha in ((1.55, 0.10), (1.3, 0.16), (1.12, 0.28)):  # halo
            col = QColor(accent)
            col.setAlphaF(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(c, r * k, r * k)
        g = QRadialGradient(c, r)
        g.setColorAt(0.0, QColor("#06104a"))
        g.setColorAt(0.5, QColor("#1636c8"))
        g.setColorAt(0.85, QColor("#2f7bff"))
        g.setColorAt(1.0, QColor("#8fd8ff"))
        p.setBrush(QBrush(g))
        p.setPen(QPen(QColor("#bfe6ff"), max(1.5, u * 0.012)))
        p.drawEllipse(c, r, r)
        inner = QRadialGradient(c, r * 0.55)
        inner.setColorAt(0.0, QColor("#02061f"))
        inner.setColorAt(1.0, QColor(2, 6, 31, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(inner))
        p.drawEllipse(c, r * 0.55, r * 0.55)
        if self.mood is Mood.LOADING:
            return  # el centro lo ocupa el porcentaje
        glow = QColor(dot)
        glow.setAlphaF(0.35)
        p.setBrush(glow)
        p.drawEllipse(c, r * 0.16, r * 0.16)
        p.setBrush(dot)
        p.drawEllipse(c, r * 0.07, r * 0.07)

    @staticmethod
    def _font(px: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        """Fuente de ``px`` píxeles (proporcional al orbe: no crece con el escalado de pantalla)."""
        f = QFont("Segoe UI")
        f.setPixelSize(max(8, int(px)))
        f.setWeight(weight)
        return f

    def _draw_texts(self, p: QPainter, c: QPointF, u: float, accent: QColor) -> None:
        title = self._font(u * (0.05 if self.mood is Mood.LOADING else 0.066), QFont.Weight.Bold)
        p.setFont(title)
        p.setPen(QColor("#ffffff"))
        p.drawText(
            QRectF(c.x() - u * 0.5, c.y() - u * 0.44, u * 1.0, u * 0.08),
            Qt.AlignmentFlag.AlignCenter,
            TITLES[self.mood],
        )
        sub = self._font(u * 0.055, QFont.Weight.DemiBold)
        p.setFont(sub)
        p.setPen(QColor(accent).lighter(135))
        p.drawText(
            QRectF(c.x() - u * 0.5, c.y() - u * 0.365, u * 1.0, u * 0.07),
            Qt.AlignmentFlag.AlignCenter,
            self.stage_label
            if self.mood is Mood.LOADING and self.stage_label
            else f"{self.src_name} → {self.dst_name}",
        )
        if self.caption:
            cap = self._font(u * 0.058)
            p.setFont(cap)
            box = QRectF(c.x() - u * 0.62, c.y() + u * 0.70, u * 1.24, u * 0.17)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(4, 12, 40, 150))  # fondo para que el texto se lea sobre las barras
            p.drawRoundedRect(box, u * 0.04, u * 0.04)
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(
                box.adjusted(8, 2, -8, -2),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.caption,
            )

    def _draw_percent(self, p: QPainter, c: QPointF, u: float) -> None:
        """«NN %» en el centro del ojo mientras cargan los modelos."""
        p.setFont(self._font(u * 0.085, QFont.Weight.Bold))
        p.setPen(QColor("#ffffff"))
        p.drawText(
            QRectF(c.x() - u * 0.17, c.y() - u * 0.07, u * 0.34, u * 0.14),
            Qt.AlignmentFlag.AlignCenter,
            f"{self.progress_percent} %",
        )

    def _draw_latency(self, p: QPainter, c: QPointF, u: float) -> None:
        """Retraso de la última frase y mediana, abajo; azul / ámbar / rojo según qué tan lento sea."""
        if self.latency is None:
            return
        last, med = self.latency
        color = QColor("#7fb0ff") if last <= 1.5 else QColor("#ffc857") if last <= 2.5 else QColor("#ff6b7a")
        p.setFont(self._font(u * 0.048, QFont.Weight.DemiBold))
        p.setPen(color)
        p.drawText(
            QRectF(c.x() - u * 0.6, c.y() + u * 0.885, u * 1.2, u * 0.09),
            Qt.AlignmentFlag.AlignCenter,
            f"Retraso {last:.1f} s · mediana {med:.1f} s".replace(".", ","),
        )

    def _draw_buttons(self, p: QPainter, accent: QColor) -> None:
        rects = self.button_rects()
        labels = {"settings": "Ajustes", "input": "Entrada", "mic": "Micrófono", "output": "Salida"}
        font = self._font(self._unit() * 0.056, QFont.Weight.DemiBold)
        for name, rect in rects.items():
            col = QColor("#ffffff")
            if name == "mic" and self.muted:
                col = QColor("#ff6b6b")
            pen = QPen(col, max(1.6, self._unit() * 0.012), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            icon_area = QRectF(rect.x(), rect.y(), rect.width(), rect.height() * 0.58)
            icons = {
                "settings": self._icon_gear,
                "input": self._icon_wave,
                "mic": self._icon_mic,
                "output": self._icon_speaker,
            }
            icons[name](p, icon_area)
            p.setFont(font)
            p.setPen(col)
            p.drawText(
                QRectF(
                    rect.x() - 12, rect.y() + rect.height() * 0.58, rect.width() + 24, rect.height() * 0.42
                ),
                Qt.AlignmentFlag.AlignCenter,
                labels[name],
            )

    @staticmethod
    def _icon_gear(p: QPainter, r: QRectF) -> None:
        c = r.center()
        rad = min(r.width(), r.height()) * 0.36
        path = QPainterPath()
        for i in range(8):
            ang = math.pi * 2 * i / 8
            path.moveTo(c.x() + rad * 0.85 * math.cos(ang), c.y() + rad * 0.85 * math.sin(ang))
            path.lineTo(c.x() + rad * 1.2 * math.cos(ang), c.y() + rad * 1.2 * math.sin(ang))
        p.drawPath(path)
        p.drawEllipse(c, rad * 0.85, rad * 0.85)
        p.drawEllipse(c, rad * 0.35, rad * 0.35)

    @staticmethod
    def _icon_wave(p: QPainter, r: QRectF) -> None:
        """Barras de voz (entrada): el sonido que llega del micrófono."""
        c = r.center()
        h = min(r.width(), r.height())
        for i, k in enumerate((0.22, 0.5, 0.78, 0.5, 0.22)):
            x = c.x() + (i - 2) * h * 0.17
            p.drawLine(QPointF(x, c.y() - h * 0.36 * k), QPointF(x, c.y() + h * 0.36 * k))

    @staticmethod
    def _icon_speaker(p: QPainter, r: QRectF) -> None:
        """Altavoz con ondas (salida)."""
        c = r.center()
        h = min(r.width(), r.height())
        x0 = c.x() - h * 0.30
        body = QPainterPath()
        body.moveTo(x0, c.y() - h * 0.12)
        body.lineTo(x0 + h * 0.14, c.y() - h * 0.12)
        body.lineTo(x0 + h * 0.34, c.y() - h * 0.30)
        body.lineTo(x0 + h * 0.34, c.y() + h * 0.30)
        body.lineTo(x0 + h * 0.14, c.y() + h * 0.12)
        body.lineTo(x0, c.y() + h * 0.12)
        body.closeSubpath()
        p.drawPath(body)
        for k in (0.16, 0.30):
            arc = QRectF(x0 + h * 0.34 - h * k, c.y() - h * k, h * k * 2, h * k * 2)
            p.drawArc(arc, -45 * 16, 90 * 16)

    @staticmethod
    def _icon_mic(p: QPainter, r: QRectF) -> None:
        c = r.center()
        h = min(r.width(), r.height())
        body = QRectF(c.x() - h * 0.14, c.y() - h * 0.46, h * 0.28, h * 0.56)
        p.drawRoundedRect(body, h * 0.14, h * 0.14)
        arc = QRectF(c.x() - h * 0.26, c.y() - h * 0.28, h * 0.52, h * 0.5)
        p.drawArc(arc, 200 * 16, 140 * 16)
        p.drawLine(QPointF(c.x(), c.y() + h * 0.22), QPointF(c.x(), c.y() + h * 0.38))
        p.drawLine(QPointF(c.x() - h * 0.14, c.y() + h * 0.38), QPointF(c.x() + h * 0.14, c.y() + h * 0.38))

    # -- ratón -----------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
            zone = self.hit_test(event.position())
            if zone == "drag" and self.windowHandle() is not None:
                self.windowHandle().startSystemMove()  # arrastrar desde cualquier zona libre del círculo

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        moved = (event.position() - self._press_pos).manhattanLength()
        zone = self.hit_test(event.position())
        self._press_pos = None
        if moved > 6:
            return
        signals = {
            "orb": self.toggle_requested,
            "settings": self.settings_requested,
            "input": self.input_requested,
            "output": self.output_requested,
            "mic": self.mic_requested,
        }
        if zone in signals:
            signals[zone].emit()

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001, N802
        menu = QMenu(self)
        top = QAction("Siempre visible", menu, checkable=True)
        top.setChecked(bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        top.toggled.connect(self.set_always_on_top)
        menu.addAction(top)
        anim = menu.addMenu("Animación")
        for mode, label in ANIMATIONS.items():
            act = QAction(label, anim, checkable=True)
            act.setChecked(mode == self.animation)
            act.triggered.connect(lambda _=False, m=mode: self.set_animation(m))
            anim.addAction(act)
        menu.addAction("Encender micrófono" if self.muted else "Apagar micrófono").triggered.connect(
            self.sys_mic_toggle_requested.emit
        )
        menu.addAction("Reconectar micrófono").triggered.connect(self.reconnect_requested.emit)
        classic = menu.addAction("Volver a la ventana clásica")
        classic.triggered.connect(self.classic_requested.emit)
        menu.addSeparator()
        menu.addAction("Salir de Interpreter").triggered.connect(self.close)
        menu.exec(event.globalPos())

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self._timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    def set_always_on_top(self, on: bool) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()  # cambiar las banderas oculta la ventana

    def wheelEvent(self, event) -> None:  # noqa: ANN001, N802
        """Rueda: sobre «Micrófono» cambia su volumen (±5); en otra zona, el tamaño del orbe."""
        up = event.angleDelta().y() > 0
        if self.hit_test(event.position()) == "mic":
            self.volume_step.emit(5 if up else -5)
            return
        step = 24 if up else -24
        side = max(320, min(900, self.width() + step))
        self.resize(side, side)
