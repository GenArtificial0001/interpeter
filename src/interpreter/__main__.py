"""Punto de entrada: ``python -m interpreter`` abre la aplicación de escritorio."""

from __future__ import annotations

import logging
import sys


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from PySide6.QtWidgets import QApplication

    from .ui.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Interpreter")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
