# ruff: noqa: E501
"""Tema visual de la aplicación: el tema claro «Ford Blue» de Visión (una sola fuente de verdad para colores y estilos).

Los valores salen del frontend de Visión (``[data-theme="light"]``): azul Ford puro ``#0000FF`` en el menú lateral y los botones
primarios, tarjetas blancas y grises muy claros para el contenido. Ver ``docs/specs/C-ajustes-rediseno.md``.
"""

from __future__ import annotations

# Acento y azules
ACCENT = "#0000FF"  # Ford Blue
ACCENT_2 = "#3D3DFF"  # hover / brillo
ACCENT_SOFT = "#E3E3FF"  # fondo de selección sobre superficies claras
ACCENT_DARK = "#00008F"  # borde del elemento activo
LAVENDER = "#D6D6FF"

# Superficies y texto
BG = "#FFFFFF"
SURFACE = "#F4F6FA"
SURFACE_2 = "#E9EDF3"
SURFACE_3 = "#DDE3EC"
BORDER = "#D7DCE4"
BORDER_STRONG = "#BCC4D0"
TEXT = "#0B0E14"
TEXT_SOFT = "#454C58"
MUTED = "#757E8C"
CHROME = "#6B7480"

# Señales (más oscuras que las del tema oscuro para leerse sobre fondo claro)
OK = "#1A9E5C"
WARN = "#B7791F"
WARN_BG = "#FFF4D6"
DANGER = "#C62828"

STATE_COLORS = {
    "idle": MUTED,
    "loading": WARN,
    "ready": OK,
    "running": OK,
    "error": DANGER,
}

MENU_STYLE = f"""
QMenu {{ background: #FFFFFF; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ background: transparent; color: {TEXT}; padding: 7px 26px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {ACCENT}; font-weight: 700; }}
QMenu::item:checked {{ font-weight: 700; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
"""

STYLESHEET = (
    f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 18px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#sectionLabel {{ color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#metric {{ font-size: 30px; font-weight: 600; font-family: "Cascadia Mono", "Consolas", monospace; color: {ACCENT}; }}
QLabel#mono {{ font-family: "Cascadia Mono", "Consolas", monospace; color: {TEXT_SOFT}; }}
QLabel#warning {{ color: {WARN}; background: {WARN_BG}; border-radius: 8px; padding: 6px 10px; }}
QLabel#pageTitle {{ color: #FFFFFF; font-size: 16px; font-weight: 600; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#topbar {{ background: {ACCENT}; }}
QFrame#sidebar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 #0A0AD6);
}}
QLabel#brand {{ color: #FFFFFF; font-size: 17px; font-weight: 700; background: transparent; }}
QLabel#brandSub {{ color: #C9D3FF; font-size: 11px; background: transparent; }}
QPushButton#nav {{
    background: #FFFFFF; color: {TEXT}; border: none; border-left: 4px solid transparent; border-radius: 8px;
    padding: 9px 10px; text-align: left; font-weight: 500;
}}
QPushButton#nav:hover {{ background: {ACCENT_SOFT}; }}
QPushButton#nav:checked {{ background: #FFFFFF; border-left: 4px solid {ACCENT_DARK}; font-weight: 700; color: {ACCENT}; }}
QPushButton#swap {{ min-width: 34px; max-width: 34px; padding: 6px 0; font-size: 16px; color: {ACCENT}; }}
QFrame#footer {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QComboBox {{
    background: #FFFFFF; border: 1px solid {BORDER_STRONG}; border-radius: 8px;
    padding: 6px 10px; min-height: 20px;
}}
QComboBox:hover, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {MUTED}; background: {SURFACE_2}; }}
QComboBox QAbstractItemView {{
    background: #FFFFFF; border: 1px solid {BORDER_STRONG}; selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QPushButton {{
    background: #FFFFFF; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE_2}; }}
QPushButton#primary {{
    background: {ACCENT}; color: #FFFFFF; border: none; font-size: 14px; font-weight: 600;
    padding: 10px 18px; border-radius: 10px;
}}
QPushButton#primary:hover {{ background: {ACCENT_2}; }}
QPushButton#primary:disabled {{ background: {SURFACE_3}; color: {MUTED}; }}
QPushButton#primary[stop="true"] {{ background: {DANGER}; color: #FFFFFF; }}
QPushButton#primary[stop="true"]:hover {{ background: #E04848; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER_STRONG}; border-radius: 4px; background: #FFFFFF; }}
QCheckBox::indicator:checked {{ background: #FFFFFF; border: 5px solid {ACCENT}; }}  /* marcada = anillo azul grueso */
QSlider::groove:horizontal {{ height: 6px; background: {SURFACE_3}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; border: 2px solid {ACCENT}; width: 14px; margin: -5px 0; border-radius: 8px; }}
QProgressBar {{
    background: {SURFACE_3}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QPlainTextEdit {{
    background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
}}
QScrollArea {{ background: {BG}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QScrollBar:vertical {{ background: {SURFACE_2}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {ACCENT}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QToolTip {{ background: #FFFFFF; color: {TEXT}; border: 1px solid {BORDER_STRONG}; }}
"""
    + MENU_STYLE
)
