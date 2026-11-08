"""Los .bat dicen qué versiones lanzan: si cambian la app o la extensión, hay que actualizarlos."""

import json
import re
from pathlib import Path

import pytest

from interpreter import __version__

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = ["Iniciar Interpreter.bat", "Iniciar Interpreter (consola).bat"]


@pytest.mark.parametrize("name", LAUNCHERS)
def test_el_bat_declara_las_versiones_vigentes(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    m = re.search(r"rem VERSIONES: app (\S+) \| extension del navegador (\S+)", text)
    assert m, f"{name}: falta la línea «rem VERSIONES: …»"
    extension = json.loads((ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))[
        "version"
    ]
    assert m.group(1) == __version__, f"{name}: dice app {m.group(1)} pero es la {__version__}"
    assert m.group(2) == extension, f"{name}: dice extensión {m.group(2)} pero es la {extension}"


def test_la_version_del_paquete_coincide_con_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(__version__)}"', text, re.MULTILINE)
