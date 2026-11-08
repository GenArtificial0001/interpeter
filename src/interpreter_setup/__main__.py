# ruff: noqa: E501
"""``python -m interpreter_setup``: ventana del instalador, o ``--cli`` para instalar sin interfaz (pruebas)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="interpreter_setup", description=__doc__)
    ap.add_argument("--cli", action="store_true", help="instalar por consola, sin ventana")
    ap.add_argument("--verificar", action="store_true", help="con --cli: solo verificar, no instalar")
    ap.add_argument("--dir", type=Path, default=None, help="carpeta de instalación")
    ap.add_argument("--clone", action="store_true", help="incluir voz clonada")
    ap.add_argument("--sin-modelos", action="store_true", help="no descargar modelos ahora")
    ap.add_argument("--modelos-desde", type=Path, default=None, help="copiar modelos desde esta carpeta")
    ap.add_argument("--proxy", default="", help="proxy HTTP(S), p. ej. http://internet.ford.com:83")
    args = ap.parse_args(argv)
    if not args.cli:
        from .gui import DEFAULT_DIR
        from .gui import main as gui_main

        return gui_main() if args.dir is None else _gui_with_dir(args.dir or DEFAULT_DIR)
    opts = core.Options(
        install_dir=args.dir or Path.home() / "Interpreter",
        clone=args.clone,
        models=not args.sin_modelos,
        models_from=args.modelos_desde,
        proxy=args.proxy,
    )
    if args.verificar:
        checks = core.check_system(opts)
        for c in checks:
            print(c.line())
        return 1 if core.blocking_failures(checks) else 0
    return 0 if core.run_install(opts, print).ok else 1


def _gui_with_dir(path: Path) -> int:
    from .gui import SetupWindow

    win = SetupWindow()
    win.var_dir.set(str(path))
    win.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
