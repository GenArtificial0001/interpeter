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
BASE_NAME = "Instalar Interpreter"
BAT_SOURCE = ROOT / "Instalar Interpreter (prueba).bat"
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


def version_file(version: str) -> Path:
    """Recurso de versión de Windows: «Propiedades» del .exe muestra el número."""
    nums = [int(n) for n in re.findall(r"\d+", version)[:3]] + [0, 0, 0]
    t = tuple(nums[:3]) + (0,)
    text = (
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,"
        " fileType=0x1, subtype=0x0, date=(0, 0)), kids=[StringFileInfo([StringTable('040904B0', ["
        "StringStruct('FileDescription', 'Instalador de Interpreter'), "
        f"StringStruct('FileVersion', '{version}'), StringStruct('ProductName', 'Interpreter'), "
        f"StringStruct('ProductVersion', '{version}')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])"
    )
    path = BUILD / "version_info.txt"
    path.write_text(text, encoding="utf-8")
    return path


def build_exe(version: str) -> int:
    sep = ";"  # separador de --add-data en Windows
    name = f"{BASE_NAME} v{version}"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", name, "--icon", str(ICON), "--version-file", str(version_file(version)),
        "--paths", str(ROOT / "src"),
        "--add-data", f"{PAYLOAD}{sep}.",
        "--add-data", f"{ICON}{sep}assets",
        "--distpath", str(ROOT / "dist"), "--workpath", str(BUILD / "work-setup"), "--specpath", str(BUILD),
        "--exclude-module", "numpy", "--exclude-module", "torch", "--exclude-module", "PySide6",
        str(ROOT / "scripts" / "setup_entry.py"),
    ]  # fmt: skip
    return subprocess.run(cmd, cwd=ROOT).returncode  # noqa: S603


def write_test_bat(version: str) -> Path:
    """Copia del .bat de prueba con la versión en el nombre; vive en ``dist`` y trabaja sobre la carpeta madre."""
    text = BAT_SOURCE.read_text(encoding="utf-8")
    text = text.replace('cd /d "%~dp0"', 'cd /d "%~dp0.."')
    text = text.replace('set "PYTHONPATH=%~dp0src"', 'set "PYTHONPATH=%CD%\\src"')
    path = ROOT / "dist" / f"{BASE_NAME} v{version} (prueba).bat"
    crlf = "\r\n".join(text.splitlines()) + "\r\n"  # los .bat de Windows van con CRLF
    path.write_bytes(crlf.encode("utf-8"))
    return path


def clean_old(version: str) -> None:
    """Quita instaladores de otras versiones para que no se confundan (solo archivos que genera este script)."""
    keep = {f"{BASE_NAME} v{version}.exe", f"{BASE_NAME} v{version} (prueba).bat"}
    for old in (ROOT / "dist").glob(f"{BASE_NAME}*"):
        if old.name not in keep and old.suffix in {".exe", ".bat"}:
            old.unlink()


def main() -> int:
    if not ICON.is_file():
        print("Falta assets/interpreter.ico: ejecuta antes scripts/make_icon.py")
        return 1
    version = app_version()
    if "--solo-payload" in sys.argv:  # para probar el instalador con el .bat, sin compilar el .exe
        print(f"payload: {build_payload()} archivos (versión {version})")
        return 0
    print(f"payload: {build_payload()} archivos (versión {version})")
    code = build_exe(version)
    exe = ROOT / "dist" / f"{BASE_NAME} v{version}.exe"
    if code == 0 and exe.is_file():
        bat = write_test_bat(version)
        clean_old(version)
        print(f"ok: {exe} ({exe.stat().st_size / 1024**2:.1f} MB)")
        print(f"ok: {bat}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
