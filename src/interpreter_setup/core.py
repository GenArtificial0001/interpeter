# ruff: noqa: E501
"""Lógica del instalador de Interpreter (sin interfaz): verifica el equipo, instala y comprueba.

El instalador es un ``.exe`` que **lleva su propio Python** (PyInstaller): él mismo no necesita nada instalado.
Lo que sí necesita la APLICACIÓN es un Python 3.11–3.13 para crear su entorno (``.venv``); este módulo lo busca y,
si no hay, intenta instalarlo solo para el usuario (sin administrador). Nunca se cambia el índice de pip: se usa el
que ya tenga configurado el equipo (en Ford, el espejo corporativo), y los paquetes internos no se tocan.

Todo se hace con funciones pequeñas que reciben un ``log`` (``Callable[[str], None]``) para poder probarlas sin red.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

Log = Callable[[str], None]

PY_MIN = (3, 11)
PY_MAX = (3, 13)  # torch/coqui-tts aún no publican ruedas para 3.14
PY_AUTOINSTALL = "3.12.10"  # versión que se instala si el equipo no tiene ninguna compatible
PY_AUTOINSTALL_URL = "https://www.python.org/ftp/python/{v}/python-{v}-amd64.exe"
FORD_PROXY = "http://internet.ford.com:83"  # proxy corporativo habitual (ver la guía de red de Ford)
DISK_NEED_GB = {"base": 6.0, "modelos": 9.0, "clone": 5.0}  # estimaciones (torch, modelos, voz clonada)

# Modelos que la app descarga sola en el primer uso; aquí se bajan antes para que funcione sin esperar.
HF_REPOS = (
    "openai/whisper-base",
    "openai/whisper-small",
    "facebook/nllb-200-distilled-600M",
    "facebook/mms-tts-spa",
    "facebook/mms-tts-eng",
    "facebook/mms-tts-por",
    "facebook/mms-tts-fra",
)
EXTRAS_BASE = ("ml", "ui", "ov")
EXTRAS_CLONE = ("clone",)
CHECK_IMPORTS = ("numpy", "scipy", "sounddevice", "torch", "transformers", "PySide6", "openvino")
CHECK_IMPORTS_CLONE = ("TTS",)

# Script que corre DENTRO del venv para bajar un repo de Hugging Face sin duplicar pesos (safetensors o, si no hay, .bin).
_HF_SCRIPT = r"""
import sys
from pathlib import Path
from huggingface_hub import snapshot_download
repo = sys.argv[1]
base = ["*.json", "*.txt", "*.model", "*.spm", "merges.txt", "vocab*", "*.md"]
path = Path(snapshot_download(repo, allow_patterns=base + ["*.safetensors"]))
if not any(path.glob("*.safetensors")):
    path = Path(snapshot_download(repo, allow_patterns=base + ["pytorch_model.bin"]))
print("ok", path)
"""
_YOURTTS_SCRIPT = r"""
import os, sys, warnings
warnings.filterwarnings("ignore")
os.environ.setdefault("COQUI_TTS_HOME", sys.argv[1])
from TTS.api import TTS
TTS("tts_models/multilingual/multi-dataset/your_tts")
print("ok yourtts")
"""


@dataclass
class Options:
    install_dir: Path
    clone: bool = False  # voz clonada (coqui-tts): +~3 GB
    models: bool = True  # descargar los modelos ahora
    models_from: Path | None = None  # carpeta ``models`` ya copiada (USB/red): se copia en vez de descargar
    shortcuts: bool = True
    proxy: str = ""
    python: Path | None = None  # forzar un intérprete concreto (si no, se busca)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    blocking: bool = True  # si falla y es bloqueante, no se puede instalar

    def line(self) -> str:
        mark = "✓" if self.ok else ("✗" if self.blocking else "!")
        text = f"{mark} {self.name}: {self.detail}".rstrip(": ")
        return text + (f"\n    → {self.fix}" if not self.ok and self.fix else "")


@dataclass
class PythonInfo:
    path: Path
    version: tuple[int, int, int]

    @property
    def text(self) -> str:
        return ".".join(map(str, self.version))


@dataclass
class Result:
    ok: bool
    steps: list[str] = field(default_factory=list)
    log_file: Path | None = None


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def env_with_proxy(proxy: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    if proxy.strip():
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[key] = proxy.strip()
    return env


# -- Python ----------------------------------------------------------------------------------------------------------


def probe_python(exe: str | Path) -> PythonInfo | None:
    """Devuelve la versión de ese ``python`` si arranca y trae ``venv``/``ensurepip``; ``None`` si no sirve."""
    try:
        out = subprocess.run(  # noqa: S603 - ruta de un intérprete candidato, sin shell
            [str(exe), "-c", "import sys, venv, ensurepip; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", out.stdout.strip()) if out.returncode == 0 else None
    return PythonInfo(Path(str(exe)), (int(m[1]), int(m[2]), int(m[3]))) if m else None


def _candidate_pythons() -> list[str]:
    cands: list[str] = []
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roots = [local / "Programs" / "Python", Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))]
    for root in roots:
        if root.is_dir():
            cands += [str(p / "python.exe") for p in sorted(root.glob("Python3*"), reverse=True)]
    for name in ("py", "python", "python3"):
        found = shutil.which(name)
        if found and "WindowsApps" not in found:  # el «python.exe» de WindowsApps solo abre la tienda
            cands.append(found)
    return cands


def find_python(forced: Path | None = None) -> PythonInfo | None:
    """El mejor Python compatible (3.11–3.13) ya instalado, o ``None``."""
    seen: set[str] = set()
    best: PythonInfo | None = None
    for cand in [str(forced)] if forced else _candidate_pythons():
        exe = cand
        if Path(cand).name.lower() == "py.exe":  # lanzador de Windows: se le pide el intérprete real
            exe = _py_launcher_path(cand) or cand
        if exe.lower() in seen:
            continue
        seen.add(exe.lower())
        info = probe_python(exe)
        if info and PY_MIN <= info.version[:2] <= PY_MAX and (best is None or info.version > best.version):
            best = info
    return best


def _py_launcher_path(py: str) -> str | None:
    for minor in (13, 12, 11):
        try:
            out = subprocess.run(  # noqa: S603
                [py, f"-3.{minor}", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=_no_window(),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return None


def install_python(dest_dir: Path, log: Log, proxy: str = "") -> PythonInfo | None:
    """Instala Python solo para el usuario (sin administrador): primero ``winget``, luego el instalador de python.org."""
    env = env_with_proxy(proxy)
    winget = shutil.which("winget")
    if winget:
        log("Instalando Python con winget (solo para tu usuario)…")
        subprocess.run(  # noqa: S603
            [winget, "install", "-e", "--id", "Python.Python.3.12", "--scope", "user", "--silent",
             "--accept-package-agreements", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=900, env=env, creationflags=_no_window(),
        )  # fmt: skip
        info = find_python()
        if info:
            return info
        log("winget no pudo instalarlo; pruebo con el instalador de python.org…")
    url = PY_AUTOINSTALL_URL.format(v=PY_AUTOINSTALL)
    exe = dest_dir / f"python-{PY_AUTOINSTALL}-amd64.exe"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        log(f"Descargando {url} …")
        _download(url, exe, proxy)
        log("Instalando Python (solo para tu usuario)…")
        subprocess.run(  # noqa: S603
            [str(exe), "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_test=0", "Include_launcher=0",
             "Include_doc=0", "SimpleInstall=1"],
            capture_output=True, timeout=900, creationflags=_no_window(),
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - red bloqueada, sin permisos…: se informa y se sigue con el mensaje de ayuda
        log(f"No se pudo instalar Python automáticamente: {exc}")
        return None
    finally:
        with contextlib.suppress(OSError):
            exe.unlink()
    return find_python()


def _download(url: str, dest: Path, proxy: str = "") -> None:
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy.strip() else []
    opener = urllib.request.build_opener(*handlers)
    with opener.open(url, timeout=60) as r, open(dest, "wb") as f:  # noqa: S310 - URL fija (https) de este módulo
        shutil.copyfileobj(r, f)


# -- verificaciones --------------------------------------------------------------------------------------------------


def free_gb(path: Path) -> float:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free / 1024**3


def needed_gb(opts: Options) -> float:
    need = DISK_NEED_GB["base"]
    if opts.models or opts.models_from:
        need += DISK_NEED_GB["modelos"]
    if opts.clone:
        need += DISK_NEED_GB["clone"]
    return need


def url_reachable(url: str, proxy: str = "", timeout: float = 12.0) -> tuple[bool, str]:
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy.strip() else []
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "interpreter-setup"})  # noqa: S310
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:  # el servidor respondió: hay red (403/405 en HEAD es normal)
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(getattr(exc, "reason", exc))


def pip_index_reachable(py: Path, proxy: str = "") -> tuple[bool, str]:
    """Pregunta a pip (con SU configuración, p. ej. el espejo de Ford) por las versiones de un paquete pequeño."""
    try:
        out = subprocess.run(  # noqa: S603
            [str(py), "-m", "pip", "index", "versions", "sounddevice"],
            capture_output=True, text=True, timeout=90, env=env_with_proxy(proxy), creationflags=_no_window(),
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if out.returncode == 0:
        return True, "el índice de pip responde"
    return False, (out.stderr.strip().splitlines() or ["sin respuesta"])[-1]


def check_system(opts: Options, log: Log | None = None) -> list[Check]:
    """Revisa lo necesario ANTES de instalar (no cambia nada en el equipo)."""
    checks: list[Check] = []
    is_win64 = sys.platform == "win32" and sys.maxsize > 2**32
    checks.append(Check("Windows de 64 bits", is_win64, "" if is_win64 else "solo Windows x64"))

    need = needed_gb(opts)
    free = free_gb(opts.install_dir)
    checks.append(
        Check(
            "Espacio en disco",
            free >= need,
            f"{free:.0f} GB libres (hacen falta ~{need:.0f} GB)",
            "Elige otra carpeta o libera espacio.",
        )
    )

    py = find_python(opts.python)
    if py:
        checks.append(Check("Python 3.11–3.13", True, f"{py.text} en {py.path}"))
    else:
        checks.append(
            Check(
                "Python 3.11–3.13",
                False,
                "no encontrado",
                "El instalador intentará instalarlo solo para tu usuario. Si la red lo bloquea, instálalo desde "
                "el Portal de Empresa/Centro de software (Python 3.12) y vuelve a ejecutar.",
                blocking=False,
            )
        )

    if py:
        ok, det = pip_index_reachable(py.path, opts.proxy)
        checks.append(
            Check(
                "Índice de paquetes (pip)",
                ok,
                det,
                f"Conéctate a la VPN de Ford o indica el proxy ({FORD_PROXY}). No se cambia el índice de pip.",
            )
        )
    hf_ok, hf_det = url_reachable("https://huggingface.co", opts.proxy)
    if opts.models and not opts.models_from:
        checks.append(
            Check(
                "Hugging Face (modelos)",
                hf_ok,
                hf_det,
                "Sin acceso, copia la carpeta «models» de otra PC y elígela en «Modelos ya copiados».",
                blocking=False,
            )
        )
    return checks


def blocking_failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.blocking and not c.ok]


# -- instalación -----------------------------------------------------------------------------------------------------


def payload_zip() -> Path | None:
    """El zip con la aplicación que lleva el .exe (o ``build/payload.zip`` al probar desde el código)."""
    bundled = getattr(sys, "_MEIPASS", None)
    for cand in ([Path(bundled) / "payload.zip"] if bundled else []) + [
        Path(__file__).resolve().parents[2] / "build" / "payload.zip"
    ]:
        if cand.is_file():
            return cand
    return None


def payload_version(zip_path: Path | None = None) -> str:
    zip_path = zip_path or payload_zip()
    if not zip_path:
        return "?"
    with zipfile.ZipFile(zip_path) as z:
        try:
            return z.read("VERSION").decode("utf-8").strip()
        except KeyError:
            return "?"


def extract_app(dest: Path, log: Log, zip_path: Path | None = None) -> int:
    """Copia la aplicación a ``dest`` (sobrescribe el código; no toca ``models``, ``.venv``, ``voices``, ``data``)."""
    zip_path = zip_path or payload_zip()
    if not zip_path:
        raise FileNotFoundError("el instalador no trae la aplicación (falta payload.zip)")
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise ValueError(f"ruta fuera de la carpeta de instalación: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            count += 1
    log(f"Aplicación copiada ({count} archivos) en {dest}")
    return count


def venv_python(dest: Path) -> Path:
    return dest / ".venv" / "Scripts" / "python.exe"


def create_venv(py: PythonInfo, dest: Path, log: Log) -> Path:
    vpy = venv_python(dest)
    if vpy.is_file() and probe_python(vpy):
        log("Entorno .venv ya existente: se reutiliza.")
        return vpy
    log(f"Creando entorno virtual con Python {py.text}…")
    out = subprocess.run(  # noqa: S603
        [str(py.path), "-m", "venv", str(dest / ".venv")],
        capture_output=True, text=True, timeout=300, creationflags=_no_window(),
    )  # fmt: skip
    if out.returncode != 0 or not vpy.is_file():
        raise RuntimeError(f"No se pudo crear el entorno virtual: {out.stderr.strip() or out.stdout.strip()}")
    return vpy


def run_streaming(
    cmd: list[str], log: Log, env: dict[str, str], cwd: Path | None = None, timeout: int = 7200
) -> int:
    """Ejecuta ``cmd`` mostrando su salida línea a línea (pip puede tardar muchos minutos)."""
    proc = subprocess.Popen(  # noqa: S603
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", creationflags=_no_window(),
    )  # fmt: skip
    assert proc.stdout is not None  # noqa: S101
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(line)
        return proc.wait(timeout=timeout)
    except Exception:
        proc.kill()
        raise


def pip_install_command(vpy: Path, clone: bool) -> list[str]:
    extras = ",".join(EXTRAS_BASE + (EXTRAS_CLONE if clone else ()))
    return [str(vpy), "-m", "pip", "install", "--no-input", "-e", f".[{extras}]"]


def install_packages(vpy: Path, dest: Path, opts: Options, log: Log) -> None:
    env = env_with_proxy(opts.proxy)
    log("Actualizando pip…")
    run_streaming(
        [str(vpy), "-m", "pip", "install", "--upgrade", "pip"], log, env, dest, timeout=600
    )  # no es crítico
    log("Instalando dependencias (torch, transformers, PySide6, OpenVINO…). Puede tardar 10–30 min.")
    code = run_streaming(pip_install_command(vpy, opts.clone), log, env, dest)
    if code != 0:
        raise RuntimeError(
            "pip no pudo instalar las dependencias. Revisa la VPN de Ford y el proxy "
            f"({FORD_PROXY}); el detalle está en el registro."
        )


def copy_models(src: Path, dest: Path, log: Log) -> None:
    """Copia una carpeta ``models`` (o la que contiene ``hf``/``ov``) a ``dest/models``."""
    root = src / "models" if (src / "models").is_dir() else src
    if not (root / "hf").is_dir():
        raise FileNotFoundError(f"«{src}» no parece una carpeta de modelos de Interpreter (falta «hf»).")
    log(f"Copiando modelos desde {root} …")
    shutil.copytree(root, dest / "models", dirs_exist_ok=True, ignore=shutil.ignore_patterns(".ready"))


def download_models(vpy: Path, dest: Path, opts: Options, log: Log) -> list[str]:
    """Descarga los modelos base con el Python del venv. Devuelve los que NO se pudieron bajar."""
    env = env_with_proxy(opts.proxy)
    env["HF_HOME"] = str(dest / "models" / "hf")
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    (dest / "models" / "hf").mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for repo in HF_REPOS:
        log(f"Descargando modelo {repo} …")
        if run_streaming([str(vpy), "-c", _HF_SCRIPT, repo], log, env, dest, timeout=3600) != 0:
            failed.append(repo)
    if opts.clone:
        log("Descargando voz clonada (YourTTS)…")
        tts_home = str(dest / "models" / "tts")
        if run_streaming([str(vpy), "-c", _YOURTTS_SCRIPT, tts_home], log, env, dest, timeout=3600) != 0:
            failed.append("YourTTS")
    return failed


def verify_install(vpy: Path, opts: Options, log: Log) -> list[Check]:
    """Comprueba que cada paquete importa en el venv (lo único que de verdad dice «funciona»)."""
    mods = CHECK_IMPORTS + (CHECK_IMPORTS_CLONE if opts.clone else ())
    checks: list[Check] = []
    for mod in mods:
        out = subprocess.run(  # noqa: S603
            [str(vpy), "-c", f"import {mod}"], capture_output=True, text=True, timeout=180,
            env=env_with_proxy(opts.proxy), creationflags=_no_window(),
        )  # fmt: skip
        err = (out.stderr.strip().splitlines() or [""])[-1]
        checks.append(Check(f"import {mod}", out.returncode == 0, "" if out.returncode == 0 else err))
    return checks


def _powershell(script: str, env_extra: dict[str, str]) -> bool:
    env = dict(os.environ) | env_extra
    out = subprocess.run(  # noqa: S603
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],  # noqa: S607
        capture_output=True, text=True, timeout=60, env=env, creationflags=_no_window(),
    )  # fmt: skip
    return out.returncode == 0


def create_shortcuts(dest: Path, log: Log) -> bool:
    """Accesos directos (escritorio y menú Inicio) al ``.bat`` de arranque, con el icono de Interpreter."""
    bat = dest / "Iniciar Interpreter.bat"
    ico = dest / "assets" / "interpreter.ico"
    if not bat.is_file():
        return False
    script = (
        "$s = New-Object -ComObject WScript.Shell;"
        "foreach ($d in @([Environment]::GetFolderPath('Desktop'), "
        "(Join-Path ([Environment]::GetFolderPath('Programs')) ''))) {"
        "  $l = $s.CreateShortcut((Join-Path $d 'Interpreter.lnk'));"
        "  $l.TargetPath = $env:IP_BAT; $l.WorkingDirectory = $env:IP_DIR; $l.IconLocation = $env:IP_ICO;"
        "  $l.WindowStyle = 7; $l.Description = 'Interprete de voz en tiempo real'; $l.Save() }"
    )
    ok = _powershell(script, {"IP_BAT": str(bat), "IP_DIR": str(dest), "IP_ICO": str(ico)})
    log(
        "Accesos directos creados (escritorio y menú Inicio)."
        if ok
        else "No se pudieron crear los accesos directos."
    )
    return ok


def open_log_file(dest: Path) -> Path:
    logs = dest / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / f"instalacion_{dt.datetime.now():%Y%m%d_%H%M%S}.log"


def run_install(opts: Options, log: Log, step: Callable[[str], None] | None = None) -> Result:
    """Instalación completa. Cada paso se informa; si uno crítico falla se detiene con el motivo."""
    dest = opts.install_dir
    result = Result(ok=False)
    log_path = open_log_file(dest)
    log_lines: list[str] = []

    def both(msg: str) -> None:
        log_lines.append(f"{dt.datetime.now():%H:%M:%S} {msg}")
        log(msg)

    def mark(title: str) -> None:
        result.steps.append(title)
        both(f"\n=== {title} ===")
        if step:
            step(title)

    try:
        mark("1/6 Verificando el equipo")
        checks = check_system(opts, both)
        for c in checks:
            both(c.line())
        if blocking_failures(checks):
            raise RuntimeError("Hay requisitos sin cumplir (ver arriba).")

        mark("2/6 Python")
        py = find_python(opts.python)
        if not py:
            py = install_python(dest / "tmp", both, opts.proxy)
        if not py:
            raise RuntimeError(
                "No hay Python 3.11–3.13 y no se pudo instalar solo. Instala Python 3.12 desde el Portal de "
                "Empresa o python.org (opción «solo para mí») y vuelve a ejecutar el instalador."
            )
        both(f"Usando Python {py.text} ({py.path})")

        mark("3/6 Copiando la aplicación")
        extract_app(dest, both)
        vpy = create_venv(py, dest, both)

        mark("4/6 Instalando dependencias")
        install_packages(vpy, dest, opts, both)

        mark("5/6 Modelos")
        if opts.models_from:
            copy_models(opts.models_from, dest, both)
        elif opts.models:
            failed = download_models(vpy, dest, opts, both)
            if failed:
                both(
                    f"! No se pudieron bajar: {', '.join(failed)}. La app los intentará bajar en el primer uso."
                )
        else:
            both("Modelos omitidos: la app los descargará en el primer uso (necesita red).")

        mark("6/6 Comprobando la instalación")
        post = verify_install(vpy, opts, both)
        for c in post:
            both(c.line())
        if opts.shortcuts:
            create_shortcuts(dest, both)
        result.ok = not blocking_failures(post)
        both(
            "\nListo. Abre «Interpreter» desde el escritorio."
            if result.ok
            else "\nFaltan componentes (ver arriba)."
        )
    except Exception as exc:  # noqa: BLE001 - cualquier fallo se muestra con su motivo, no se pierde
        both(f"\n✗ {exc}")
        result.ok = False
    finally:
        with contextlib.suppress(OSError):
            log_path.write_text("\n".join(log_lines), encoding="utf-8")
            result.log_file = log_path
    return result
