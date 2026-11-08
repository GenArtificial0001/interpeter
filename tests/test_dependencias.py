"""Toda librería de terceros que importa la app debe estar declarada en pyproject.toml.

Si no, funciona en el entorno de desarrollo (donde otra dependencia la trajo por casualidad) pero NO en una
instalación limpia hecha por el instalador (ejemplo real: ``websockets`` faltaba y el puerto 8765 no abría).
"""

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# nombre con el que se importa -> nombre de la distribución en pip
DIST = {"yaml": "PyYAML", "TTS": "coqui-tts", "PySide6": "PySide6", "sounddevice": "sounddevice"}
IGNORAR = {"interpreter", "interpreter_setup", "tests"}


def _declaradas() -> set[str]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = list(cfg["project"]["dependencies"])
    for extra in cfg["project"]["optional-dependencies"].values():
        specs += extra
    return {re.split(r"[<>=!~\[ ]", s, maxsplit=1)[0].lower().replace("_", "-") for s in specs}


def _importadas() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (ROOT / "src" / "interpreter").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n not in sys.stdlib_module_names and n not in IGNORAR:
                    out.setdefault(n, path.name)
    return out


def test_todo_import_de_terceros_esta_declarado_en_pyproject():
    declaradas = _declaradas()
    faltan = {
        mod: archivo
        for mod, archivo in _importadas().items()
        if DIST.get(mod, mod).lower().replace("_", "-") not in declaradas
    }
    assert not faltan, f"Importadas pero no declaradas en pyproject.toml: {faltan}"


def test_el_instalador_verifica_websockets():
    from interpreter_setup import core

    assert "websockets" in core.CHECK_IMPORTS
