"""Genera ``assets/interpreter.ico`` a partir de ``interpeter-icono.jpg``.

El JPG original es un círculo rojo sobre fondo blanco. Aquí se recolorea al azul Ford de la app (``#0000FF``)
y el blanco de FUERA del círculo pasa a transparente; el blanco del dibujo (auricular y globo) se conserva.

Uso (desde la raíz del proyecto, con el venv que tiene PySide6)::

    .venv\\Scripts\\python scripts\\make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "interpeter-icono.jpg"
TARGET = ROOT / "assets" / "interpreter.ico"
PREVIEW = ROOT / "assets" / "interpreter-256.png"
SIZES = (16, 24, 32, 48, 64, 128, 256)
ACCENT = np.array([0, 0, 255], dtype=np.float32)  # Ford Blue de la app (theme.ACCENT)
WHITE = np.array([255, 255, 255], dtype=np.float32)
RED = np.array([231, 56, 71], dtype=np.float32)  # rojo del círculo original (muestreado del JPG)


def recolor(img: QImage) -> QImage:
    """Círculo azul Ford + dibujo blanco + exterior transparente (con borde suavizado)."""
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.bytesPerLine() * h).reshape(h, img.bytesPerLine())
    rgb = arr[:, : w * 3].reshape(h, w, 3).astype(np.float32)

    # 0 = rojo del círculo, 1 = blanco del dibujo (proyección sobre el segmento rojo→blanco)
    d = WHITE - RED
    t = np.clip(((rgb - RED) @ d) / float(d @ d), 0.0, 1.0)
    out_rgb = ACCENT + (WHITE - ACCENT) * t[..., None]

    # Máscara circular (el JPG ocupa todo el cuadrado): borde antialias de ~1,5 px
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.hypot(xx - (w - 1) / 2, yy - (h - 1) / 2)
    radius = min(w, h) / 2 - 1.0
    alpha = np.clip((radius - r) / 1.5 + 0.5, 0.0, 1.0)

    rgba = np.dstack([out_rgb, alpha[..., None] * 255]).astype(np.uint8)
    rgba = np.ascontiguousarray(rgba)
    out = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return out.copy()  # copia: ``rgba`` se libera al salir


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(img: QImage, path: Path) -> None:
    """ICO con una imagen PNG por tamaño (Windows Vista+; 256 px se guarda como 0)."""
    frames = [
        png_bytes(
            img.scaled(s, s, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )
        for s in SIZES
    ]
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, body = b"", b""
    for s, data in zip(SIZES, frames, strict=True):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + body)


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    src = QImage(str(SOURCE))
    if src.isNull():
        print(f"No se pudo leer {SOURCE}")
        return 1
    out = recolor(src)
    write_ico(out, TARGET)
    out.scaled(
        256, 256, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    ).save(str(PREVIEW))
    print(f"ok: {TARGET} ({TARGET.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
