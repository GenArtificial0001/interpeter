# ruff: noqa: E501
"""Genera ``dist/Instalar Interpreter.exe``: un instalador de un solo archivo con la app dentro.

Pasos: (1) empaqueta la app (código, extensión, lanzadores, icono) en ``build/payload.zip``; (2) PyInstaller crea el
``.exe`` con su propio Python (la máquina destino no necesita Python para ABRIR el instalador) y el icono de la app.

Uso (desde la raíz del proyecto, con el venv de desarrollo)::

    .venv\Scripts\python scripts\build_installer.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
PAYLOAD = BUILD / "payload.zip"
ICON = ROOT / "assets" / "interpreter.ico"
NAME = "Instalar Interpreter"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache"}
INCLUDE_FILES = (
    "pyproject.toml",
    "README.md",
    "Iniciar Interpreter.bat",
    "Iniciar Interpreter (consola).bat",
)
INCLUDE_TREES = ("src/interpreter", "browser-extension", "assets")


def app_version() -> str:
    text = (ROOT / "src" / "interpreter" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m[1] if m else "?"


def build_payload() -> int:
    BUILD.mkdir(exist_ok=True)
    count = 0
    with zipfile.ZipFile(PAYLOAD, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("VERSION", app_version())
        for rel in INCLUDE_FILES:
            if (ROOT / rel).is_file():
                z.write(ROOT / rel, rel)
                count += 1
        for tree in INCLUDE_TREES:
            for path in sorted((ROOT / tree).rglob("*")):
                if path.is_file() and not (set(path.parts) & SKIP_DIRS) and path.suffix != ".pyc":
                    z.write(path, path.relative_to(ROOT).as_posix())
                    count += 1
    return count


def build_exe() -> int:
    sep = ";"  # separador de --add-data en Windows
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", NAME, "--icon", str(ICON),
        "--paths", str(ROOT / "src"),
        "--add-data", f"{PAYLOAD}{sep}.",
        "--add-data", f"{ICON}{sep}assets",
        "--distpath", str(ROOT / "dist"), "--workpath", str(BUILD / "work-setup"), "--specpath", str(BUILD),
        "--exclude-module", "numpy", "--exclude-module", "torch", "--exclude-module", "PySide6",
        str(ROOT / "scripts" / "setup_entry.py"),
    ]  # fmt: skip
    return subprocess.run(cmd, cwd=ROOT).returncode  # noqa: S603


def main() -> int:
    if "--solo-payload" in sys.argv:  # para probar el instalador con el .bat, sin compilar el .exe
        print(f"payload: {build_payload()} archivos (versión {app_version()})")
        return 0
    if not ICON.is_file():
        print("Falta assets/interpreter.ico: ejecuta antes scripts/make_icon.py")
        return 1
    print(f"payload: {build_payload()} archivos (versión {app_version()})")
    code = build_exe()
    exe = ROOT / "dist" / f"{NAME}.exe"
    if code == 0 and exe.is_file():
        print(f"ok: {exe} ({exe.stat().st_size / 1024**2:.1f} MB)")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
