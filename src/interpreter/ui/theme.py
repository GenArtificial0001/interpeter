"""Tema visual oscuro de la aplicación (una sola fuente de verdad para colores y estilos)."""

from __future__ import annotations

BG = "#0e1117"
SURFACE = "#161b24"
SURFACE_2 = "#1d2430"
BORDER = "#2a3342"
TEXT = "#e8ecf3"
MUTED = "#8b95a8"
ACCENT = "#2dd4bf"
ACCENT_DARK = "#0f766e"
WARN = "#fbbf24"
DANGER = "#f87171"

STATE_COLORS = {
    "idle": MUTED,
    "loading": WARN,
    "ready": ACCENT,
    "running": ACCENT,
    "error": DANGER,
}

STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QLabel#title {{ font-size: 20px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#sectionLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#metric {{ font-size: 30px; font-weight: 600; }}
QLabel#warning {{ color: {WARN}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QComboBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 10px; min-height: 20px;
}}
QComboBox:hover {{ border-color: {ACCENT_DARK}; }}
QComboBox:disabled {{ color: {MUTED}; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DARK};
}}
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT_DARK}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{
    background: {ACCENT}; color: #04211e; border: none; font-size: 15px; font-weight: 600;
    padding: 14px 18px; border-radius: 10px;
}}
QPushButton#primary:hover {{ background: #5eead4; }}
QPushButton#primary:disabled {{ background: {SURFACE_2}; color: {MUTED}; }}
QPushButton#stop {{ background: {DANGER}; color: #2a0a0a; }}
QPushButton#stop:hover {{ background: #fca5a5; }}
QPushButton:checked {{ background: {ACCENT_DARK}; border-color: {ACCENT}; }}
QProgressBar {{
    background: {SURFACE_2}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QPlainTextEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px;
    selection-background-color: {ACCENT_DARK};
}}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""
