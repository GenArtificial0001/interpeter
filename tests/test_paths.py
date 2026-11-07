import sys
from pathlib import Path

from interpreter.stages import backends


def test_interpreter_home_tiene_prioridad(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERPRETER_HOME", str(tmp_path))
    assert backends.app_home() == tmp_path


def test_empaquetado_usa_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("INTERPRETER_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert backends.app_home() == tmp_path / "Interpreter"


def test_desarrollo_usa_la_raiz_del_proyecto(monkeypatch):
    monkeypatch.delenv("INTERPRETER_HOME", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert (backends.app_home() / "pyproject.toml").is_file()


def test_cache_de_modelos_en_carpeta_de_datos(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERPRETER_HOME", str(tmp_path))
    monkeypatch.setenv("HF_HOME", "x")  # registra el valor original para restaurarlo al terminar
    monkeypatch.delenv("HF_HOME")
    cache = backends.use_local_model_cache()
    assert cache == Path(tmp_path) / "models" / "hf" and cache.is_dir()
