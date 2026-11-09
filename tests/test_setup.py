"""Pruebas del instalador (``interpreter_setup``): todo con falsos, sin red ni instalar nada de verdad."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from interpreter_setup import core


def _zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path


def test_probe_python_acepta_el_python_actual_y_rechaza_lo_que_no_arranca(tmp_path):
    info = core.probe_python(sys.executable)
    assert info is not None and info.version[:2] == sys.version_info[:2]
    assert core.probe_python(tmp_path / "no_existe.exe") is None


def _con_pythons(monkeypatch, versiones):
    monkeypatch.setattr(core, "_candidate_pythons", lambda: list(versiones))
    monkeypatch.setattr(core, "probe_python", lambda exe: versiones[str(exe)])


def test_find_python_acepta_3_11_o_superior_y_prefiere_el_que_admite_voz_clonada(monkeypatch):
    versiones = {
        "a": core.PythonInfo(Path("a"), (3, 10, 0)),  # muy viejo: no sirve
        "b": core.PythonInfo(Path("b"), (3, 14, 0)),
        "c": core.PythonInfo(Path("c"), (3, 12, 4)),
        "d": core.PythonInfo(Path("d"), (3, 13, 1)),
    }
    _con_pythons(monkeypatch, versiones)
    assert core.find_python().text == "3.13.1"  # el más nuevo de 3.11–3.13 (admite voz clonada)
    _con_pythons(monkeypatch, {"a": versiones["a"], "b": versiones["b"]})
    assert core.find_python().text == "3.14.0"  # sin tope: si solo hay 3.14, se usa
    _con_pythons(monkeypatch, {"a": versiones["a"], "e": core.PythonInfo(Path("e"), (3, 15, 0))})
    assert core.find_python().text == "3.15.0"
    _con_pythons(monkeypatch, {"a": versiones["a"]})
    assert core.find_python() is None


def test_con_python_3_14_la_voz_clonada_se_intenta_y_solo_se_deja_si_funciona(tmp_path, monkeypatch):
    py = core.PythonInfo(Path("py.exe"), (3, 14, 0))
    monkeypatch.setattr(core, "find_python", lambda forced=None: py)
    monkeypatch.setattr(core, "free_gb", lambda p: 100.0)
    monkeypatch.setattr(core, "url_reachable", lambda *a, **k: (True, "HTTP 200"))
    monkeypatch.setattr(core, "pip_index_reachable", lambda *a, **k: (True, "ok"))
    opts = core.Options(install_dir=tmp_path / "x", clone=True)
    checks = core.check_system(opts)
    nota = next(c for c in checks if c.name == "Voz clonada")
    assert not nota.ok and not nota.blocking and "experimental" in nota.detail  # avisa, no impide instalar
    assert core.blocking_failures(checks) == [] or sys.platform != "win32"

    visto: dict[str, bool] = {}
    monkeypatch.setattr(core, "check_system", lambda o, log=None: [])
    for name in ("extract_app", "create_venv", "download_models", "create_shortcuts"):
        monkeypatch.setattr(core, name, lambda *a, **k: [])

    def instalar(vpy, dest, o, log):
        visto["instalar"] = o.clone

    def verificar(vpy, o, log):
        visto["verificar"] = o.clone
        return []

    monkeypatch.setattr(core, "install_packages", instalar)
    monkeypatch.setattr(core, "verify_install", verificar)

    # 1) el intento experimental FALLA: se sigue sin voz clonada, lo demás queda instalado
    monkeypatch.setattr(core, "install_clone_experimental", lambda *a, **k: False)
    lineas: list[str] = []
    core.run_install(opts, lineas.append)
    assert visto["instalar"] is False  # el paquete base se instala SIN el extra «clone» (pide torch<2.8)
    assert visto["verificar"] is False and any("voz clonada no funciona" in ln for ln in lineas)

    # 2) el intento FUNCIONA: se mantiene y se comprueba TTS al final
    visto.clear()
    monkeypatch.setattr(core, "install_clone_experimental", lambda *a, **k: True)
    core.run_install(opts, lambda _m: None)
    assert visto["instalar"] is False and visto["verificar"] is True

    # 3) con 3.13 la voz clonada se instala por el camino normal (extra «clone»)
    visto.clear()
    py313 = core.PythonInfo(Path("py.exe"), (3, 13, 5))
    monkeypatch.setattr(core, "find_python", lambda forced=None: py313)
    monkeypatch.setattr(
        core, "install_clone_experimental", lambda *a, **k: pytest.fail("no debe intentarse en 3.13")
    )
    core.run_install(core.Options(install_dir=tmp_path / "y", clone=True), lambda _m: None)
    assert visto["instalar"] is True and visto["verificar"] is True


def test_comprobacion_de_voz_clonada_y_torchcodec_de_respaldo(tmp_path, monkeypatch):
    pasos: list[str] = []
    resultados = iter([False, True])  # la primera comprobación falla; con torchcodec funciona

    def fake_run(cmd, log, env, cwd=None, timeout=0):
        pasos.append(cmd[-1])
        return 0

    monkeypatch.setattr(core, "run_streaming", fake_run)
    monkeypatch.setattr(core, "_clone_works", lambda vpy, env: next(resultados))
    ok = core.install_clone_experimental(
        Path("v.exe"), tmp_path, core.Options(install_dir=tmp_path), lambda _m: None
    )
    assert ok and pasos == ["transformers>=4.56,<5", "torchcodec"]
    # si pip falla, no se intenta nada más
    monkeypatch.setattr(core, "run_streaming", lambda *a, **k: 1)
    assert not core.install_clone_experimental(
        Path("v.exe"), tmp_path, core.Options(install_dir=tmp_path), lambda _m: None
    )


def test_extract_app_copia_sin_pisar_modelos_ni_salir_de_la_carpeta(tmp_path):
    dest = tmp_path / "app"
    (dest / "models").mkdir(parents=True)
    (dest / "models" / "dato.bin").write_text("no tocar")
    z = _zip(tmp_path / "p.zip", {"VERSION": "9.9.9", "src/interpreter/__init__.py": "x = 1"})
    assert core.extract_app(dest, lambda _m: None, z) == 2
    assert (dest / "src" / "interpreter" / "__init__.py").read_text() == "x = 1"
    assert (dest / "models" / "dato.bin").read_text() == "no tocar"
    assert core.payload_version(z) == "9.9.9"
    mala = _zip(tmp_path / "mala.zip", {"../../fuera.txt": "x"})
    with pytest.raises(ValueError):
        core.extract_app(dest, lambda _m: None, mala)


def test_pip_install_usa_los_extras_y_nunca_cambia_el_indice():
    cmd = core.pip_install_command(Path("v/python.exe"), clone=False)
    assert cmd[-1] == ".[ml,ui,ov]" and "-e" in cmd
    assert core.pip_install_command(Path("v/python.exe"), clone=True)[-1] == ".[ml,ui,ov,clone]"
    assert not any("index-url" in part for part in cmd)  # se usa el índice que ya tenga configurado el equipo


def test_el_proxy_solo_se_aplica_si_se_indica(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    assert "HTTPS_PROXY" not in core.env_with_proxy("")
    assert core.env_with_proxy(" http://p:83 ")["HTTPS_PROXY"] == "http://p:83"


def test_check_system_sin_python_no_bloquea_pero_sin_espacio_si(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "find_python", lambda forced=None: None)
    monkeypatch.setattr(core, "url_reachable", lambda *a, **k: (True, "HTTP 200"))
    opts = core.Options(install_dir=tmp_path / "x", models=True)
    monkeypatch.setattr(core, "free_gb", lambda p: 100.0)
    checks = core.check_system(opts)
    py = next(c for c in checks if c.name.startswith("Python"))
    assert not py.ok and not py.blocking  # el instalador lo instala solo
    monkeypatch.setattr(core, "free_gb", lambda p: 1.0)
    assert any(c.name == "Espacio en disco" for c in core.blocking_failures(core.check_system(opts)))


def test_el_espacio_necesario_crece_con_modelos_y_voz_clonada(tmp_path):
    base = core.needed_gb(core.Options(install_dir=tmp_path, models=False))
    assert core.needed_gb(core.Options(install_dir=tmp_path, models=True)) > base
    assert core.needed_gb(core.Options(install_dir=tmp_path, models=False, clone=True)) > base


def test_copy_models_exige_una_carpeta_de_modelos_valida(tmp_path):
    with pytest.raises(FileNotFoundError):
        core.copy_models(tmp_path / "vacia", tmp_path / "dest", lambda _m: None)
    origen = tmp_path / "models"
    (origen / "hf" / "hub").mkdir(parents=True)
    (origen / "hf" / "hub" / "a.json").write_text("{}")
    (origen / ".ready").write_text("")
    core.copy_models(origen, tmp_path / "dest", lambda _m: None)
    assert (tmp_path / "dest" / "models" / "hf" / "hub" / "a.json").is_file()
    assert not (tmp_path / "dest" / "models" / ".ready").exists()  # la app lo crea al cargar bien una vez


def test_run_install_se_detiene_con_motivo_si_no_hay_python(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "check_system", lambda opts, log=None: [])
    monkeypatch.setattr(core, "find_python", lambda forced=None: None)
    monkeypatch.setattr(core, "install_python", lambda *a, **k: None)
    lines: list[str] = []
    res = core.run_install(core.Options(install_dir=tmp_path / "app"), lines.append)
    assert not res.ok
    assert any("No hay Python 3.11 o superior" in ln for ln in lines)
    assert res.log_file is not None and res.log_file.is_file()


def test_run_install_completo_con_pasos_simulados(tmp_path, monkeypatch):
    llamadas: list[str] = []
    py = core.PythonInfo(Path("py.exe"), (3, 12, 0))
    monkeypatch.setattr(core, "check_system", lambda opts, log=None: [core.Check("Python", True, "ok")])
    monkeypatch.setattr(core, "find_python", lambda forced=None: py)
    monkeypatch.setattr(core, "extract_app", lambda d, log, z=None: llamadas.append("extraer") or 1)
    monkeypatch.setattr(core, "create_venv", lambda p, d, log: llamadas.append("venv") or Path("v.exe"))
    monkeypatch.setattr(core, "install_packages", lambda *a, **k: llamadas.append("pip"))
    monkeypatch.setattr(core, "download_models", lambda *a, **k: llamadas.append("modelos") or [])
    monkeypatch.setattr(core, "verify_install", lambda *a, **k: [core.Check("import torch", True)])
    monkeypatch.setattr(core, "create_shortcuts", lambda d, log: llamadas.append("accesos") or True)
    pasos: list[str] = []
    res = core.run_install(core.Options(install_dir=tmp_path / "app"), lambda _m: None, pasos.append)
    assert res.ok and llamadas == ["extraer", "venv", "pip", "modelos", "accesos"]
    assert len(pasos) == 6 and pasos[0].startswith("1/6")


def test_una_importacion_fallida_marca_la_instalacion_como_incompleta(tmp_path, monkeypatch):
    py = core.PythonInfo(Path("py.exe"), (3, 12, 0))
    monkeypatch.setattr(core, "check_system", lambda opts, log=None: [])
    monkeypatch.setattr(core, "find_python", lambda forced=None: py)
    for name in ("extract_app", "create_venv", "install_packages", "download_models", "create_shortcuts"):
        monkeypatch.setattr(core, name, lambda *a, **k: [])
    fallo = [core.Check("import torch", False, "ModuleNotFoundError")]
    monkeypatch.setattr(core, "verify_install", lambda *a, **k: fallo)
    assert not core.run_install(core.Options(install_dir=tmp_path / "a"), lambda _m: None).ok


def test_verify_install_informa_cada_modulo_por_separado(tmp_path):
    res = core.verify_install(Path(sys.executable), core.Options(install_dir=tmp_path), lambda _m: None)
    assert [c.name for c in res][:2] == ["import numpy", "import scipy"]
    assert {c.name: c.ok for c in res}["import numpy"] is True


def test_cli_verificar_devuelve_codigo_y_no_instala(tmp_path, monkeypatch, capsys):
    from interpreter_setup import __main__ as m

    monkeypatch.setattr(core, "check_system", lambda opts, log=None: [core.Check("Python", True, "3.12")])
    assert m.main(["--cli", "--verificar", "--dir", str(tmp_path)]) == 0
    assert "Python" in capsys.readouterr().out
    monkeypatch.setattr(core, "check_system", lambda opts, log=None: [core.Check("Disco", False, "poco")])
    assert m.main(["--cli", "--verificar", "--dir", str(tmp_path)]) == 1


def test_python_m_interpreter_setup_muestra_la_ayuda():
    out = subprocess.run(
        [sys.executable, "-m", "interpreter_setup", "--help"], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0 and "--cli" in out.stdout
