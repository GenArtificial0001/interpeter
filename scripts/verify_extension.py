"""Prueba de extremo a extremo de la extensión «micrófono virtual» con un navegador REAL.

Demuestra que la extensión de ``browser-extension/`` entrega el audio de ``WebMicServer`` a una página como
si fuera un micrófono, **sin** depender de que la página permita conectarse a ``127.0.0.1`` (la página de
prueba lleva un CSP estricto, parecido al de Teams, con ``connect-src 'self'``).

Cómo funciona
- Un servidor HTTP local sirve una página de prueba con CSP estricto. La página llama a
  ``getUserMedia({audio:true})``, conecta el track a un ``AnalyserNode`` y mide RMS y frecuencia dominante.
- Se hace una copia TEMPORAL de la extensión y solo a ella se le añade ``http://127.0.0.1/*`` y
  ``http://localhost/*`` a ``content_scripts.matches`` (``browser-extension/manifest.json`` no se toca).
- Se lanza Edge (o Chrome) con perfil temporal, ``--mute-audio`` (nada suena por altavoces) y un puerto de
  depuración CDP; se controla con ``websockets`` (sin Playwright ni Selenium).
- Se arranca un ``WebMicServer`` real en el puerto 8765 y se envía un tono de 440 Hz.

Comprobaciones: (a) silencio antes del tono, (b) energía y ~440 Hz durante el tono, (c) ``enumerateDevices``
incluye el dispositivo virtual, (d) silencio tras ``flush()``, (e) el micrófono real nunca se abre.

Uso::

    .venv-clone\\Scripts\\python.exe scripts\\verify_extension.py            # Edge y, si falla, Chrome
    .venv-clone\\Scripts\\python.exe scripts\\verify_extension.py --browser chrome --headed

Código de salida: 0 si todo pasa, 1 si alguna comprobación falla, 2 si ningún navegador pudo cargar la extensión.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from websockets.sync.client import connect as ws_connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interpreter.audio.webmic import DEFAULT_PORT, WebMicServer  # noqa: E402

EXTENSION_DIR = ROOT / "browser-extension"
VIRTUAL_LABEL = "Interpreter (voz traducida)"
TONE_HZ = 440.0
TONE_SECONDS = 1.5
TONE_RATE = 24000

BROWSERS = {
    "edge": Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    "chrome": Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
}

# CSP estricto parecido al de Teams: connect-src 'self' impide que la PÁGINA abra ws://127.0.0.1.
CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:"

PAGE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Prueba micrófono virtual</title></head>
<body><p>Prueba de micrófono virtual</p>
<script>
const R = window.__resultado = { listo: false, error: null, dispositivos: [], etiquetaTrack: null, ctxRate: 0 };
let analizador = null, tBuf = null, fBuf = null, pico = { rms: 0, freq: 0 };

function medir() {
  analizador.getFloatTimeDomainData(tBuf);
  let s = 0; for (let i = 0; i < tBuf.length; i++) s += tBuf[i] * tBuf[i];
  const rms = Math.sqrt(s / tBuf.length);
  if (rms > pico.rms) {
    analizador.getFloatFrequencyData(fBuf);
    let k = 1; for (let i = 2; i < fBuf.length - 1; i++) if (fBuf[i] > fBuf[k]) k = i;
    // interpolación parabólica del pico para afinar la frecuencia
    const a = fBuf[k - 1], b = fBuf[k], c = fBuf[k + 1];
    const d = (a - 2 * b + c) !== 0 ? 0.5 * (a - c) / (a - 2 * b + c) : 0;
    pico = { rms, freq: (k + d) * R.ctxRate / analizador.fftSize };
  }
}
window.__reiniciar = () => { pico = { rms: 0, freq: 0 }; };
window.__pico = () => pico;
window.__ahora = () => { analizador.getFloatTimeDomainData(tBuf); let s = 0; for (let i = 0; i < tBuf.length; i++) s += tBuf[i] * tBuf[i]; return Math.sqrt(s / tBuf.length); };
window.__estado = () => (window.__interpreterVirtualMicInfo ? window.__interpreterVirtualMicInfo() : null);

(async () => {
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    R.dispositivos = devs.map(d => ({ kind: d.kind, label: d.label, deviceId: d.deviceId }));
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const track = stream.getAudioTracks()[0];
    R.etiquetaTrack = track ? track.label : null;
    const ctx = new AudioContext();
    R.ctxRate = ctx.sampleRate;
    if (ctx.state === 'suspended') await ctx.resume();
    analizador = ctx.createAnalyser();
    analizador.fftSize = 8192;
    analizador.smoothingTimeConstant = 0;
    tBuf = new Float32Array(analizador.fftSize);
    fBuf = new Float32Array(analizador.frequencyBinCount);
    const src = ctx.createMediaStreamSource(stream);
    const mudo = ctx.createGain(); mudo.gain.value = 0;      // nada llega a los altavoces
    src.connect(analizador); analizador.connect(mudo); mudo.connect(ctx.destination);
    setInterval(medir, 30);
    R.listo = true;
  } catch (e) { R.error = String(e && e.name ? e.name + ': ' + e.message : e); }
})();
</script></body></html>
"""


ESPIA_JS = r"""
// Espía de getUserMedia en el prototipo: cuenta las llamadas que llegan a la implementación REAL.
// Se inyecta por CDP antes que cualquier script (el bind de la extensión captura ESTA función).
(() => {
  const proto = MediaDevices.prototype;
  const orig = proto.getUserMedia;
  window.__espia = { llamadasReales: 0, propiaAlInstalar: Object.prototype.hasOwnProperty.call(navigator.mediaDevices, 'getUserMedia') };
  proto.getUserMedia = function () { window.__espia.llamadasReales++; return orig.apply(this, arguments); };
})();
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - nombre impuesto por http.server
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:  # silencio
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def http_json(url: str, timeout: float = 2.0) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # sin proxy corporativo
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# -- CDP mínimo sobre websockets ---------------------------------------------------------------------


class CDP:
    def __init__(self, ws_url: str) -> None:
        self._stack = contextlib.ExitStack()
        self.ws = self._stack.enter_context(ws_connect(ws_url, max_size=None, open_timeout=10))
        self._id = 0
        self.events: list[dict] = []

    def send(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 15) -> dict:
        self._id += 1
        msg: dict = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"CDP {method}: sin respuesta")
            data = json.loads(self.ws.recv(timeout=left))
            if data.get("id") == self._id:
                if "error" in data:
                    raise RuntimeError(f"CDP {method}: {data['error']}")
                return data.get("result", {})
            self.events.append(data)

    def pump(self, seconds: float = 0.0) -> None:
        """Lee eventos pendientes (para recoger errores de consola/excepciones)."""
        deadline = time.time() + seconds
        while True:
            try:
                data = json.loads(self.ws.recv(timeout=max(0.01, deadline - time.time())))
                self.events.append(data)
            except TimeoutError:
                return
            if time.time() >= deadline:
                return

    def evaluate(self, expr: str, session: str, timeout: float = 15) -> object:
        r = self.send(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
            session,
            timeout,
        )
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS: {r['exceptionDetails'].get('exception', {}).get('description')}")
        return r["result"].get("value")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._stack.close()


# -- Preparación -------------------------------------------------------------------------------------


def make_test_extension(dest: Path) -> Path:
    """Copia temporal de la extensión con las páginas locales añadidas al manifest (solo en la copia)."""
    shutil.copytree(EXTENSION_DIR, dest)
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for cs in manifest["content_scripts"]:
        cs["matches"] = [*cs["matches"], "http://localhost/*", "http://127.0.0.1/*"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    browser: str
    loaded: bool = False
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))
        log(f"  [{'OK ' if ok else 'FALLA'}] {name}: {detail}")

    @property
    def passed(self) -> bool:
        return self.loaded and bool(self.checks) and all(c.ok for c in self.checks)


def kill_tree(proc: subprocess.Popen) -> None:
    with contextlib.suppress(Exception):
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15, check=False
        )
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


def wait_for(cond, timeout: float, step: float = 0.1) -> bool:  # noqa: ANN001
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


# -- Una ejecución contra un navegador ---------------------------------------------------------------


def run_browser(name: str, exe: Path, page_url: str, ext_dir: Path, tmp: Path, headed: bool, keep: bool) -> Report:
    rep = Report(browser=name)
    profile = tmp / f"perfil-{name}"
    cdp_port = free_port()
    args = [
        str(exe),
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={cdp_port}",
        f"--load-extension={ext_dir}",
        # En Chrome/Edge recientes --load-extension está desactivado salvo con esta característica.
        "--disable-features=DisableLoadExtensionCommandLineSwitch",
        # Chrome 137+ ignora --load-extension en builds de marca: se carga por CDP (Extensions.loadUnpacked).
        "--enable-unsafe-extension-debugging",
        "--deny-permission-prompts",  # cualquier petición de micrófono real se deniega sola, sin ventana
        "--mute-audio",
        "--autoplay-policy=no-user-gesture-required",
        # Dispositivo de captura FALSO: si algo abriera el micrófono real se vería la etiqueta "Fake...".
        "--use-fake-device-for-media-stream",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-component-update",
        "--no-proxy-server",
        "about:blank",
    ]
    if not headed:
        args.insert(1, "--headless=new")
    log(f"Lanzando {name} ({'con ventana' if headed else 'headless=new'}), CDP en {cdp_port}")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp: CDP | None = None
    server = WebMicServer(port=DEFAULT_PORT)
    try:
        # 1) Esperar el endpoint de depuración
        ws_url = None
        for _ in range(100):
            if proc.poll() is not None:
                rep.notes.append(f"{name} terminó al arrancar (código {proc.returncode}).")
                return rep
            try:
                ws_url = http_json(f"http://127.0.0.1:{cdp_port}/json/version")["webSocketDebuggerUrl"]
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        if not ws_url:
            rep.notes.append("No apareció el puerto de depuración CDP.")
            return rep
        cdp = CDP(ws_url)
        version = cdp.send("Browser.getVersion")
        log(f"  Navegador: {version.get('product')}")
        rep.notes.append(f"Versión: {version.get('product')}")

        # 2) ¿Se cargó la extensión? Aparece su service worker como target.
        def sw_target() -> dict | None:
            for t in cdp.send("Target.getTargets")["targetInfos"]:
                if t["type"] == "service_worker" and t["url"].startswith("chrome-extension://") and t["url"].endswith("/background.js"):
                    return t
            return None

        if not wait_for(lambda: sw_target() is not None, 8):
            # Plan B: cargar vía CDP (solo funciona si el navegador se lanzó con --enable-unsafe-extension-debugging)
            rep.notes.append("El service worker no apareció con --load-extension; se intenta Extensions.loadUnpacked.")
            try:
                cdp.send("Extensions.loadUnpacked", {"path": str(ext_dir)})
            except Exception as e:  # noqa: BLE001
                rep.notes.append(f"Extensions.loadUnpacked no disponible: {e}")
            if not wait_for(lambda: sw_target() is not None, 5):
                rep.notes.append("La extensión NO se cargó en este navegador.")
                return rep
        sw = sw_target()
        assert sw is not None
        rep.loaded = True
        log(f"  Extensión cargada: {sw['url']}")

        # Sesión sobre el service worker para recoger sus errores
        sw_sess = cdp.send("Target.attachToTarget", {"targetId": sw["targetId"], "flatten": True})["sessionId"]
        with contextlib.suppress(Exception):
            cdp.send("Runtime.enable", session=sw_sess)

        # 3) Servidor real de micrófono virtual
        server.start()
        log(f"  WebMicServer escuchando en ws://127.0.0.1:{server.port}")

        # 4) Abrir la página de prueba
        tid = cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        sess = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        cdp.send("Runtime.enable", session=sess)
        cdp.send("Page.enable", session=sess)
        # Si algo intentara abrir el micrófono real, el permiso denegado haría fallar la llamada.
        with contextlib.suppress(Exception):
            cdp.send(
                "Browser.setPermission",
                {"permission": {"name": "audioCapture"}, "setting": "denied", "origin": page_url.rstrip("/")},
            )
        # Espía de getUserMedia ANTES de cualquier script de la página.
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": ESPIA_JS}, session=sess)
        cdp.send("Page.navigate", {"url": page_url}, session=sess)

        ready = wait_for(lambda: bool(cdp.evaluate("!!(window.__resultado && (window.__resultado.listo || window.__resultado.error))", sess)), 15, 0.25)
        res = cdp.evaluate("JSON.stringify(window.__resultado)", sess)
        res = json.loads(res) if res else {}
        if not ready or res.get("error"):
            rep.add("página inicia captura", False, f"error={res.get('error')} (listo={res.get('listo')})")
            return rep
        log(f"  Página lista. Track: {res['etiquetaTrack']!r}, AudioContext {res['ctxRate']} Hz")

        # 5) Esperar a que la extensión conecte con el servidor y a que el puente de la pestaña esté conectado
        got_ws = wait_for(lambda: server.clients >= 1, 15)
        rep.add("extensión conectada al WebSocket (server.clients >= 1)", got_ws, f"clients={server.clients}")
        if not got_ws:
            return rep
        got_bridge = wait_for(lambda: bool((cdp.evaluate("window.__estado()", sess) or {}).get("conectado")), 10)
        estado = cdp.evaluate("window.__estado()", sess)
        rep.add("pestaña recibe el estado «conectado» del puente", got_bridge, f"estado={estado}")
        if not got_bridge:
            return rep

        # (c) enumerateDevices
        labels = [d["label"] for d in res["dispositivos"]]
        rep.add("(c) enumerateDevices incluye el dispositivo virtual", VIRTUAL_LABEL in labels, f"dispositivos={labels}")

        # (a) silencio antes del tono
        cdp.evaluate("window.__reiniciar()", sess)
        time.sleep(1.0)
        p0 = cdp.evaluate("window.__pico()", sess)
        rep.add("(a) silencio antes del tono", p0["rms"] < 1e-4, f"RMS={p0['rms']:.2e}")

        # (b) tono de 440 Hz: se mide la latencia de llegada y cuánto dura sonando (esperado ~1,5 s)
        n = int(TONE_RATE * TONE_SECONDS)
        t = np.arange(n) / TONE_RATE
        tone = (0.5 * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.float32)
        cdp.evaluate("window.__reiniciar()", sess)
        t_push = time.time()
        delivered = server.push(tone, TONE_RATE)
        log(f"  Tono de {TONE_HZ:.0f} Hz / {TONE_SECONDS}s enviado a {delivered} cliente(s)")
        sonando = wait_for(lambda: cdp.evaluate("window.__ahora()", sess) > 0.05, 5, 0.02)
        latencia = time.time() - t_push
        t_on = time.time()
        wait_for(lambda: cdp.evaluate("window.__ahora()", sess) < 1e-3, 5, 0.02)
        duracion = time.time() - t_on
        p1 = cdp.evaluate("window.__pico()", sess)
        estado = cdp.evaluate("window.__estado()", sess)
        ok_b = sonando and p1["rms"] > 0.05 and abs(p1["freq"] - TONE_HZ) < 15
        rep.add(
            "(b) durante el tono hay energía y ~440 Hz",
            ok_b,
            f"RMS pico={p1['rms']:.3f} (esperado ~0.35), frecuencia dominante={p1['freq']:.1f} Hz, "
            f"latencia de llegada={latencia * 1000:.0f} ms, estado={estado}",
        )
        rep.add(
            "el tono dura ~1,5 s y se corta (sin cola ni bucle)",
            TONE_SECONDS - 0.3 < duracion < TONE_SECONDS + 0.4,
            f"sonó {duracion:.2f} s",
        )

        # silencio estable tras acabar el tono (el track sigue vivo, solo emite ceros)
        cdp.evaluate("window.__reiniciar()", sess)
        time.sleep(0.6)
        p2 = cdp.evaluate("window.__pico()", sess)
        rep.add("silencio al terminar el tono", p2["rms"] < 1e-3, f"RMS={p2['rms']:.2e}")

        # (d) flush: se envía otro tono y se corta a mitad
        cdp.evaluate("window.__reiniciar()", sess)
        server.push(tone, TONE_RATE)
        sonando = wait_for(lambda: cdp.evaluate("window.__pico()", sess)["rms"] > 0.05, 3, 0.05)
        server.flush()
        time.sleep(0.35)  # vacía la ventana del analizador (8192 muestras) y la latencia
        cdp.evaluate("window.__reiniciar()", sess)
        time.sleep(0.6)  # el tono habría seguido sonando ~1 s más sin flush
        p3 = cdp.evaluate("window.__pico()", sess)
        rep.add(
            "(d) tras flush() vuelve el silencio",
            sonando and p3["rms"] < 1e-3,
            f"sonó antes del flush={sonando}, RMS tras flush={p3['rms']:.2e}",
        )

        # (e) micrófono real: ninguna ruta de AUDIO debe llegar al método real; solo el vídeo pasa
        espia = cdp.evaluate("window.__espia", sess)
        control = None
        with contextlib.suppress(Exception):
            control = cdp.evaluate(
                "(async () => { const antes = window.__espia.llamadasReales;"
                " const etiqueta = (s) => s.getAudioTracks()[0].label;"
                " const viaProto = await MediaDevices.prototype.getUserMedia.call(navigator.mediaDevices, {audio:true})"
                "   .then(etiqueta, e => 'ERROR:' + e.name);"
                " const viaLegacy = await new Promise(r => navigator.getUserMedia({audio:true}, s => r(etiqueta(s)),"
                "   e => r('ERROR:' + e.name)));"
                " const trasAudio = window.__espia.llamadasReales;"
                " const lim = new Promise(r => setTimeout(() => r('sin respuesta en 4 s'), 4000));"
                " const video = await Promise.race([navigator.mediaDevices.getUserMedia({video:true})"
                "   .then(() => null, e => e.name), lim]);"
                " return {antes, trasAudio, viaProto, viaLegacy, despuesVideo: window.__espia.llamadasReales, video}; })()",
                sess,
                timeout=25,
            )
        label = res["etiquetaTrack"] or ""
        virtual = "Interpreter (voz traducida)"
        no_real_label = label == virtual
        calls_ok = (
            control is not None
            and control["antes"] == 0
            and control["trasAudio"] == 0  # ni por el prototipo ni por la API antigua
            and control["viaProto"] == virtual
            and control["viaLegacy"] == virtual
            and control["despuesVideo"] == 1  # control: el espía sí cuenta las llamadas reales (vídeo)
        )
        detail = (
            f"etiqueta del track={label!r}; control (audio por prototipo y por API antigua devuelven el "
            f"virtual sin tocar el real; solo el vídeo llega al método real y el espía lo cuenta)={control}; "
            f"espía instalado antes de la extensión={not espia['propiaAlInstalar']}"
        )
        rep.add("(e) el micrófono real nunca se abrió", no_real_label and calls_ok, detail)

        # Errores del service worker / de la página
        cdp.pump(0.2)
        errs = [
            e
            for e in cdp.events
            if e.get("method") in ("Runtime.exceptionThrown",)
        ]
        rep.add("sin excepciones JS en extensión/página", not errs, f"{len(errs)} excepción(es)" + (f": {errs[0]}" if errs else ""))
        return rep
    finally:
        with contextlib.suppress(Exception):
            server.stop()
        if cdp:
            cdp.close()
        if not keep:
            kill_tree(proc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser", choices=["auto", "edge", "chrome"], default="auto")
    ap.add_argument("--headed", action="store_true", help="con ventana (por defecto --headless=new)")
    ap.add_argument("--keep", action="store_true", help="no cerrar el navegador al terminar (depuración)")
    opts = ap.parse_args()

    # Puerto 8765 libre
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", DEFAULT_PORT))
        except OSError:
            print(f"El puerto {DEFAULT_PORT} está ocupado (¿Interpreter en marcha?). Ciérralo y repite.")
            return 2

    http_port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", http_port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{http_port}/"
    log(f"Página de prueba: {page_url} (CSP: {CSP})")

    tmp = Path(tempfile.mkdtemp(prefix="interpreter-ext-"))
    ext_dir = make_test_extension(tmp / "extension")
    names = ["edge", "chrome"] if opts.browser == "auto" else [opts.browser]
    final: Report | None = None
    try:
        for name in names:
            exe = BROWSERS[name]
            if not exe.exists():
                log(f"{name}: no instalado ({exe})")
                continue
            rep = run_browser(name, exe, page_url, ext_dir, tmp, opts.headed, opts.keep)
            for n in rep.notes:
                log(f"  nota: {n}")
            final = rep
            if rep.loaded:
                break  # la extensión cargó: el resultado (pase o falle) es el de este navegador
            log(f"{name}: no cargó la extensión; se prueba el siguiente navegador.")
    finally:
        httpd.shutdown()
        if not opts.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if final is None:
        print("RESULTADO: ningún navegador disponible.")
        return 2
    if not final.loaded:
        print(f"RESULTADO: BLOQUEADO. {final.browser} no cargó la extensión: {'; '.join(final.notes)}")
        return 2
    print(f"RESULTADO en {final.browser}: {'PASA' if final.passed else 'FALLA'} ({sum(c.ok for c in final.checks)}/{len(final.checks)} comprobaciones)")
    for c in final.checks:
        print(f"  {'OK   ' if c.ok else 'FALLA'} {c.name}")
    return 0 if final.passed else 1


if __name__ == "__main__":
    sys.exit(main())
