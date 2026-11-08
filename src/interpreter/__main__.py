"""Punto de entrada: ``python -m interpreter`` abre la aplicación de escritorio."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from types import TracebackType


def setup_logging(home: Path) -> Path:
    """Registra en ``<datos>/logs/interpreter.log`` y evita fallos cuando no hay consola.

    Empaquetado como ventana (sin consola), ``sys.stdout``/``sys.stderr`` son ``None`` y librerías como
    tqdm (barras de descarga de modelos) fallarían al escribir: se redirigen a ``console.log``.
    """
    logdir = home / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        console = open(logdir / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        if sys.stdout is None:
            sys.stdout = console
        if sys.stderr is None:
            sys.stderr = console
    logfile = logdir / "interpreter.log"
    handlers: list[logging.Handler] = [logging.FileHandler(logfile, encoding="utf-8")]
    if sys.stderr is not None and getattr(sys.stderr, "isatty", lambda: False)():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def _hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        logging.getLogger("interpreter").critical("Excepción no controlada", exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook
    return logfile


def ui_mode_from_args(argv: list[str]) -> str | None:
    """``--orb`` abre siempre en modo orbe (lo usa Visión); ``--clasica`` fuerza la ventana clásica."""
    if "--orb" in argv:
        return "orb"
    if "--clasica" in argv:
        return "classic"
    return None


def shutdown(engine: object, code: int = 0, grace_s: float = 5.0) -> None:
    """Cierra TODO al salir: el motor, el servidor del micrófono virtual (puerto 8765) y los auxiliares.

    El botón de cerrar debe terminar la aplicación completa y no dejar puertos abiertos. Si algo se queda
    colgado, un temporizador (que no impide salir) fuerza el fin del proceso pasados ``grace_s`` segundos.
    """
    watchdog = threading.Timer(grace_s, os._exit, args=(code,))
    watchdog.daemon = True
    watchdog.start()
    try:
        engine.close()  # type: ignore[attr-defined]  # idempotente: ya lo hizo closeEvent
    except Exception:  # noqa: BLE001 - al salir no hay nada más que hacer que registrarlo
        logging.getLogger("interpreter").exception("Error al cerrar el motor")


def main() -> int:
    from .stages.backends import app_home, use_local_model_cache

    use_local_model_cache()  # antes de importar torch/transformers: fija la caché y el modo sin red
    setup_logging(app_home())
    from . import __version__
    from .audio.webmic import expected_extension_version

    logging.getLogger("interpreter").info(
        "Iniciando Interpreter %s (extensión del proyecto %s; datos en %s)",
        __version__,
        expected_extension_version() or "?",
        app_home(),
    )

    from PySide6.QtWidgets import QApplication

    from .ui.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Interpreter")
    mode = ui_mode_from_args(sys.argv[1:])
    win = MainWindow(ui_mode=mode)
    if not win.starts_in_orb:
        win.show()  # en modo orbe se muestra solo el orbe (sin parpadeo de la ventana clásica)
    code = app.exec()
    shutdown(win.engine, code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
