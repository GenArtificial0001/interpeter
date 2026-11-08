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
- Se arranca un ``WebMicServer`` real (puerto libre al azar, ver «Seguridad de la prueba») y se envía un tono de 440 Hz.

Comprobaciones base (10): (a) silencio antes del tono, (b) energía y ~440 Hz durante el tono, (c)
``enumerateDevices`` incluye el dispositivo virtual, (d) silencio tras ``flush()``, (e) el micrófono real nunca
se abre, más conexión WebSocket, estado del puente, duración del tono, silencio posterior y ausencia de
excepciones JS.

Comprobación (0): el navegador acepta el ``manifest.json`` real. Chrome/Edge se niegan a cargar la extensión
entera si un script con ``match_origin_as_fallback`` tiene un patrón con ruta distinta de ``/*`` (p. ej.
``https://*.zoom.us/wc/*``). Si ocurre, (0) falla con el motivo, y en la COPIA temporal se quitan esos patrones
para que los demás escenarios se puedan seguir ejecutando.

Escenarios «Meet y Zoom web» (cada uno aporta comprobaciones con nombre propio en el informe; se ejecutan
en pestañas nuevas de una página de laboratorio ``/lab`` con el mismo CSP estricto, ampliado con
``frame-src`` para poder probar un iframe de otro origen):

1. **Restricciones estilo Meet** (1a-1d): ``getUserMedia({audio:{deviceId:{exact:'default'}, echoCancellation,
   noiseSuppression, autoGainControl: true, channelCount:{ideal:1}}, video:{width/height ideal, facingMode}})``.
   Debe devolver una pista de audio virtual + una de vídeo (cámara FALSA del navegador), ``getSettings()`` del
   audio con eco/ruido/ganancia en ``false``, y el espía de llamadas reales debe ver pasar solo el vídeo
   (nunca ``audio``). El audio llega a la página.
2. **Re-solicitud y paradas** (2a-2d): pedir el micrófono, parar sus pistas, pedirlo otra vez (con el
   ``deviceId`` del virtual, como hace Meet al cambiar de dispositivo). Con TODAS las pistas paradas, un tono NO
   se reproduce en la pestaña (``trozosRecibidos`` no sube, ``trozosDescartados`` sí, ``pistasVivas`` = 0) y al
   pedir de nuevo el micrófono el siguiente tono suena. 2d: pedir la pista nueva ANTES de parar la vieja.
   2e y 2f son INFORMATIVAS (no cuentan para PASA/FALLA, salen como AVISO si no se cumplen): la página clona la
   pista (``track.clone()``) o el stream (``stream.clone()``) y para la original; la copia debería seguir
   recibiendo el tono.
3. **Iframes** (3a-3d): la página incrusta un iframe ``srcdoc``, uno ``about:blank`` (creado por script), uno de
   la misma origen y uno de OTRO origen (``localhost`` frente a ``127.0.0.1``) que piden ``getUserMedia({audio:
   true})`` nada más cargar; la pista debe ser la virtual y el tono se mide DENTRO de cada iframe.
4. **Cambio de dispositivo por deviceId** (4a-4c): ``enumerateDevices`` lista primero el virtual; pedir
   ``deviceId:{exact:'interpreter-virtual-mic'}`` devuelve el virtual y suena; un ``deviceId`` real inexistente
   (o uno real tomado de la lista) devuelve igualmente el virtual y nunca se abre el real.
5. **Dos pestañas** (5a-5b): una segunda pestaña de la misma página SIN pedir micrófono no reproduce nada
   (``trozosRecibidos`` 0 y ``pistasVivas`` 0), la primera sí. 5c (informativa): Interpreter cuenta las dos
   pestañas como receptoras.

Seguridad de la prueba
- Los tonos solo van a ESTE navegador de prueba: por defecto el WebSocket usa un puerto libre al azar y en la copia
  temporal de la extensión se cambia esa única constante (``background.js`` y ``host_permissions``). Así tu
  Edge/Chrome habitual, con la extensión instalada y reintentando conectar con el 8765, no recibe los tonos (si
  estuvieras en una reunión, se oirían en la llamada). ``--puerto 8765`` usa la extensión sin tocar, pero entonces
  SÍ pueden engancharse tus navegadores.
- Nada suena por los altavoces (``--mute-audio``), no se abre ningún sitio real (todo es ``127.0.0.1`` /
  ``localhost``) y no se inicia sesión en nada.
- Para el vídeo del escenario 1 se usa ``--use-fake-ui-for-media-stream`` junto a
  ``--use-fake-device-for-media-stream``. Esos flags son globales al navegador, así que la garantía de que el
  audio real no se abre NO descansa en el permiso sino en el espía de ``getUserMedia`` (registra las
  restricciones de cada llamada real y si alguna devolvió una pista de audio que no es la virtual).
- Se identifica el service worker de la extensión por su ID fijo (``lodhkanpadafbgdfgelmeaadelgongkg``): Edge
  y Chrome recientes traen extensiones propias con un ``background.js`` que no son la que se prueba.

Uso::

    .venv-clone\\Scripts\\python.exe scripts\\verify_extension.py                    # Edge y, si no carga, Chrome
    .venv-clone\\Scripts\\python.exe scripts\\verify_extension.py --browser edge
    .venv-clone\\Scripts\\python.exe scripts\\verify_extension.py --browser chrome --headed
    .venv-clone\\Scripts\\python.exe scripts\\verify_extension.py --extension-dir C:\\ruta\\copia   # probar una corrección sin tocar el proyecto

``--keep`` deja el navegador abierto para depurar. En el informe, «AVISO»/«INFO» son comprobaciones informativas.

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
EXTENSION_ID = "lodhkanpadafbgdfgelmeaadelgongkg"  # el que fija la clave del manifest (ver EXTENSION_ID.txt)
VIRTUAL_LABEL = "Interpreter (voz traducida)"
VIRTUAL_ID = "interpreter-virtual-mic"
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
// Además de contar, anota las restricciones de cada llamada real y cuántas devolvieron una pista de AUDIO
// que no es la virtual (``audioReal``): es la prueba de que el micrófono real nunca se abrió, aunque el
// navegador tenga concedidos los permisos (--use-fake-ui-for-media-stream).
(() => {
  const proto = MediaDevices.prototype;
  const orig = proto.getUserMedia;
  const VIRTUAL = 'Interpreter (voz traducida)';
  const E = window.__espia = {
    llamadasReales: 0, audioReal: 0, restricciones: [],
    propiaAlInstalar: Object.prototype.hasOwnProperty.call(navigator.mediaDevices, 'getUserMedia'),
  };
  proto.getUserMedia = function (c) {
    E.llamadasReales++;
    try { E.restricciones.push(JSON.stringify(c)); } catch (_) { E.restricciones.push('?'); }
    const p = orig.apply(this, arguments);
    p.then((s) => { if (s.getAudioTracks().some((t) => t.label !== VIRTUAL)) E.audioReal++; }, () => {});
    return p;
  };
})();
"""

# Diagnóstico dentro del service worker (no altera la extensión): historial de puertos de pestañas.
TRAZA_SW_JS = r"""
(() => {
  if (self.__trazaPuertos) return true;
  const T = self.__trazaPuertos = [];
  const t0 = Date.now();
  chrome.runtime.onConnect.addListener((p) => {
    const d = { t: Date.now(), ev: 'connect', tab: p.sender && p.sender.tab ? p.sender.tab.id : null,
                frame: p.sender ? p.sender.frameId : null, url: p.sender ? String(p.sender.url).slice(0, 60) : null };
    T.push(d);
    p.onDisconnect.addListener(() => T.push({ ...d, t: Date.now(), ev: 'disconnect' }));
  });
  return true;
})()
"""

# Medidor de audio reutilizable (laboratorio e iframes): RMS y frecuencia dominante del pico.
MEDIDOR_JS = r"""
window.__medidor = async function (stream) {
  const ctx = new AudioContext();
  if (ctx.state === 'suspended') await ctx.resume();
  const an = ctx.createAnalyser(); an.fftSize = 8192; an.smoothingTimeConstant = 0;
  const tBuf = new Float32Array(an.fftSize), fBuf = new Float32Array(an.frequencyBinCount);
  const src = ctx.createMediaStreamSource(stream);
  const mudo = ctx.createGain(); mudo.gain.value = 0;       // nada llega a los altavoces
  src.connect(an); an.connect(mudo); mudo.connect(ctx.destination);
  let pico = { rms: 0, freq: 0 };
  const ahora = () => {
    an.getFloatTimeDomainData(tBuf);
    let s = 0; for (let i = 0; i < tBuf.length; i++) s += tBuf[i] * tBuf[i];
    return Math.sqrt(s / tBuf.length);
  };
  setInterval(() => {
    const rms = ahora();
    if (rms > pico.rms) {
      an.getFloatFrequencyData(fBuf);
      let k = 1; for (let i = 2; i < fBuf.length - 1; i++) if (fBuf[i] > fBuf[k]) k = i;
      const a = fBuf[k - 1], b = fBuf[k], c = fBuf[k + 1];
      const d = (a - 2 * b + c) !== 0 ? 0.5 * (a - c) / (a - 2 * b + c) : 0;
      pico = { rms, freq: (k + d) * ctx.sampleRate / an.fftSize };
    }
  }, 30);
  // Grabadora (escenario 6): guarda lo que llega a la pista, para buscar cortes en una voz real.
  let rec = null;
  const sp = ctx.createScriptProcessor(2048, 1, 1);
  src.connect(sp); sp.connect(mudo);
  sp.onaudioprocess = (e) => { if (rec) rec.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
  const iniciar = () => { rec = []; return true; };
  const terminar = () => {
    const parts = rec || []; rec = null;
    let n = 0; parts.forEach((q) => { n += q.length; });
    const out = new Int16Array(n); let o = 0;
    parts.forEach((q) => { for (let i = 0; i < q.length; i++) out[o++] = Math.max(-32768, Math.min(32767, Math.round(q[i] * 32767))); });
    return { rate: ctx.sampleRate, samples: Array.from(out) };
  };
  return { ahora, pico: () => pico, reiniciar: () => { pico = { rms: 0, freq: 0 }; }, rate: ctx.sampleRate, iniciar, terminar };
};
"""

# Código que corre DENTRO de cada iframe (srcdoc, about:blank, misma origen, otro origen): pide el micrófono
# nada más cargar (como Zoom/Teams), mide el audio y atiende órdenes del padre por postMessage.
FRAME_JS = r"""
(() => {
  const nombre = window.name || 'sin-nombre';
  const R = { nombre, listo: false, error: null, etiqueta: null, ext: !!window.__interpreterVirtualMic, origen: location.origin };
  let med = null;
  const info = () => (window.__interpreterVirtualMicInfo ? window.__interpreterVirtualMicInfo() : null);
  window.addEventListener('message', async (e) => {
    const d = e.data;
    if (!d || d.__frameCmd == null || !e.source) return;
    let valor = null;
    try {
      if (d.cmd === 'resultado') valor = { ...R, espia: window.__espia ? { llamadasReales: window.__espia.llamadasReales, audioReal: window.__espia.audioReal } : null };
      else if (d.cmd === 'reiniciar') { med.reiniciar(); valor = true; }
      else if (d.cmd === 'pico') valor = med.pico();
      else if (d.cmd === 'info') valor = info();
    } catch (err) { valor = { error: String(err) }; }
    e.source.postMessage({ __frameReply: d.__frameCmd, nombre, valor }, '*');
  });
  (async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      R.etiqueta = stream.getAudioTracks()[0] ? stream.getAudioTracks()[0].label : null;
      med = await window.__medidor(stream);
      R.listo = true;
    } catch (e) { R.error = String(e && e.name ? e.name + ': ' + e.message : e); }
  })();
})();
"""
FRAME_CODE = MEDIDOR_JS + FRAME_JS

# Página de laboratorio: no pide el micrófono por sí sola; los escenarios la manejan por CDP.
LAB_PAGE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Laboratorio micrófono virtual</title></head>
<body><p>Laboratorio de micrófono virtual</p>
<script>
%%MEDIDOR%%
const FRAME_CODE = %%FRAMECODE%%;
window.__lab = {
  streams: [], meds: [],
  async pedir(c, medir) {
    const s = await navigator.mediaDevices.getUserMedia(c);
    const i = this.streams.push(s) - 1;
    if (medir && s.getAudioTracks().length) this.meds[i] = await window.__medidor(s);
    const fmt = (t) => ({ label: t.label, estado: t.readyState, kind: t.kind, settings: t.getSettings() });
    return { i, audio: s.getAudioTracks().map(fmt), video: s.getVideoTracks().map(fmt) };
  },
  async pedirSeguro(c, medir) {
    try { return await this.pedir(c, medir); } catch (e) { return { error: e.name + ': ' + e.message }; }
  },
  parar(i) { this.streams[i].getTracks().forEach((t) => t.stop()); return this.streams[i].getTracks().map((t) => t.readyState); },
  async clonarYParar(i) {   // la página clona la pista (vista previa) y detiene la original
    const t = this.streams[i].getAudioTracks()[0];
    const s = new MediaStream([t.clone()]);
    const j = this.streams.push(s) - 1;
    this.meds[j] = await window.__medidor(s);
    t.stop();
    return j;
  },
  async clonarStreamYParar(i) {   // la página clona el MediaStream entero y detiene el original
    const s = this.streams[i].clone();
    const j = this.streams.push(s) - 1;
    this.meds[j] = await window.__medidor(s);
    this.streams[i].getTracks().forEach((t) => t.stop());
    return j;
  },
  pico(i) { return this.meds[i].pico(); },
  reiniciar(i) { this.meds[i].reiniciar(); return true; },
  async loopback(i, dtx) {   // «llamada»: la pista virtual viaja por WebRTC (Opus) y se mide lo que llega al otro lado
    const track = this.streams[i].getAudioTracks()[0];
    const a = new RTCPeerConnection(), b = new RTCPeerConnection();
    a.onicecandidate = (e) => e.candidate && b.addIceCandidate(e.candidate);
    b.onicecandidate = (e) => e.candidate && a.addIceCandidate(e.candidate);
    const remoto = new Promise((ok) => { b.ontrack = (e) => ok(new MediaStream([e.track])); });
    a.addTrack(track, this.streams[i]);
    const dtxSdp = (sdp) => (dtx ? sdp.replace('minptime=10', 'minptime=10;usedtx=1') : sdp);
    const offer = await a.createOffer(); offer.sdp = dtxSdp(offer.sdp);
    await a.setLocalDescription(offer); await b.setRemoteDescription(offer);
    const ans = await b.createAnswer(); ans.sdp = dtxSdp(ans.sdp);
    await b.setLocalDescription(ans); await a.setRemoteDescription(ans);
    const rs = await remoto;
    const el = document.createElement('audio'); el.autoplay = true; el.srcObject = rs; document.body.appendChild(el);
    const j = this.streams.push(rs) - 1;
    this.meds[j] = await window.__medidor(rs);
    (this.pcs = this.pcs || []).push(a, b);
    return j;
  },
  iniciarGrab(i) { return this.meds[i].iniciar(); },
  terminarGrab(i) { return this.meds[i].terminar(); },
};
window.__info = () => (window.__interpreterVirtualMicInfo ? window.__interpreterVirtualMicInfo() : null);

// -- iframes ---------------------------------------------------------------------------------------
window.__frames = {};
let seq = 0;
window.__llamar = (nombre, cmd, tmo = 4000) => new Promise((ok, ko) => {
  const id = ++seq;
  const h = (e) => { if (e.data && e.data.__frameReply === id) { removeEventListener('message', h); clearTimeout(t); ok(e.data.valor); } };
  const t = setTimeout(() => { removeEventListener('message', h); ko(new Error('sin respuesta del iframe ' + nombre)); }, tmo);
  addEventListener('message', h);
  window.__frames[nombre].contentWindow.postMessage({ __frameCmd: id, cmd }, '*');
});
window.__crearFrame = (nombre, tipo, url) => {
  const f = document.createElement('iframe');
  f.name = nombre; f.style.cssText = 'width:200px;height:50px';
  window.__frames[nombre] = f;
  const info = { tipo };
  if (tipo === 'srcdoc') {
    f.srcdoc = '<!doctype html><html><body><scr' + 'ipt>' + FRAME_CODE + '</scr' + 'ipt></body></html>';
    document.body.appendChild(f);
  } else if (tipo === 'blank') {
    document.body.appendChild(f);
    // ¿Ya está parcheado el iframe about:blank en el mismo turno en que se crea?
    info.extSincrona = !!f.contentWindow.__interpreterVirtualMic;
    const d = f.contentDocument, sc = d.createElement('script');
    sc.textContent = FRAME_CODE;
    (d.body || d.documentElement).appendChild(sc);
  } else {
    f.src = url;
    document.body.appendChild(f);
  }
  return info;
};
</script></body></html>
"""
LAB_PAGE = LAB_PAGE.replace("%%MEDIDOR%%", MEDIDOR_JS).replace("%%FRAMECODE%%", json.dumps(FRAME_CODE))
FRAME_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"><title>iframe</title></head><body><script>'
    + FRAME_CODE
    + "</script></body></html>"
)
# Igual de estricto, pero deja incrustar iframes propios y de localhost (otro origen que 127.0.0.1).
CSP_LAB = CSP + "; frame-src 'self' http://localhost:*"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - nombre impuesto por http.server
        ruta = self.path.split("?")[0]
        if ruta == "/lab":
            body, csp = LAB_PAGE.encode("utf-8"), CSP_LAB
        elif ruta == "/frame":
            body, csp = FRAME_PAGE.encode("utf-8"), CSP_LAB
        else:
            body, csp = PAGE.encode("utf-8"), CSP
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", csp)
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

    def send(
        self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 15
    ) -> dict:
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


def patrones_invalidos(manifest: dict) -> list[str]:
    """Patrones que Chrome/Edge rechazan al CARGAR la extensión (y con ello la desactivan entera).

    Con ``match_origin_as_fallback: true`` el componente de ruta de cada patrón ``matches`` debe ser ``/*``:
    ``https://*.zoom.us/wc/*`` hace que el navegador responda «The path component for scripts with
    'match_origin_as_fallback' must be '*'» y no cargue ninguna parte de la extensión.
    """
    malos: list[str] = []
    for i, cs in enumerate(manifest.get("content_scripts", [])):
        if not cs.get("match_origin_as_fallback"):
            continue
        for m in cs.get("matches", []):
            resto = m.split("://", 1)[-1]
            ruta = "/" + resto.split("/", 1)[1] if "/" in resto else ""
            if ruta != "/*":
                malos.append(f"content_scripts[{i}] ({', '.join(cs.get('js', []))}): {m}")
    return malos


def make_test_extension(src: Path, dest: Path, ws_port: int) -> tuple[Path, list[str]]:
    """Copia temporal de la extensión con las páginas locales añadidas al manifest (solo en la copia).

    Si ``ws_port`` no es el 8765 real, en la copia también se cambia el puerto del WebSocket (una constante en
    ``background.js`` y el permiso de host del manifest). Así la extensión CARGADA EN EL NAVEGADOR HABITUAL del
    usuario, que reintenta sin parar conectar con el 8765, no puede engancharse al servidor de la prueba ni
    recibir sus tonos (en una reunión real se oirían en la llamada).

    Si el manifest tiene patrones que el navegador rechazaría (``patrones_invalidos``), se devuelven en la
    segunda posición y en la COPIA se quitan para que el resto de escenarios pueda ejecutarse; el informe
    marca la comprobación (0) como FALLA.
    """
    shutil.copytree(src, dest)
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    malos = patrones_invalidos(manifest)
    for cs in manifest["content_scripts"]:
        if cs.get("match_origin_as_fallback"):
            cs["matches"] = [m for m in cs["matches"] if not any(m in x for x in malos)]
        cs["matches"] = [*cs["matches"], "http://localhost/*", "http://127.0.0.1/*"]
    if ws_port != DEFAULT_PORT:
        manifest["host_permissions"] = [
            h.replace(f":{DEFAULT_PORT}/", f":{ws_port}/") for h in manifest["host_permissions"]
        ]
        bg = dest / "background.js"
        texto = bg.read_text(encoding="utf-8")
        if texto.count(f"ws://127.0.0.1:{DEFAULT_PORT}") != 1:
            raise RuntimeError(
                "background.js ya no tiene la forma esperada (una sola URL ws://127.0.0.1:8765)"
            )
        bg.write_text(
            texto.replace(f"ws://127.0.0.1:{DEFAULT_PORT}", f"ws://127.0.0.1:{ws_port}"), encoding="utf-8"
        )
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest, malos


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    info: bool = False  # informativa: se muestra en el informe pero no cuenta para PASA/FALLA


@dataclass
class Report:
    browser: str
    loaded: bool = False
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, info: bool = False) -> None:
        self.checks.append(Check(name, ok, detail, info))
        marca = ("INFO" if ok else "AVISO") if info else ("OK " if ok else "FALLA")
        log(f"  [{marca}] {name}: {detail}")

    @property
    def counted(self) -> list[Check]:
        return [c for c in self.checks if not c.info]

    @property
    def passed(self) -> bool:
        return self.loaded and bool(self.counted) and all(c.ok for c in self.counted)


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


# -- Escenarios estilo Meet / Zoom web ---------------------------------------------------------------


@dataclass
class Lab:
    """Todo lo que necesita un escenario: navegador (CDP), servidor de audio, informe y URLs."""

    cdp: CDP
    server: WebMicServer
    rep: Report
    origin: str
    lab_url: str
    frame_url: str
    xframe_url: str  # mismo servidor pero con host «localhost»: otro origen que 127.0.0.1
    tone: np.ndarray
    trazas: list = field(default_factory=list)  # (instante, mensaje) que la extensión envía al servidor
    sw_sess: str = ""  # sesión CDP sobre el service worker de la extensión (diagnóstico)
    llegada: float | None = None  # segundos que tardó el último tono en llegar a la pestaña medida

    def estado_sw(self) -> object:
        try:
            return self.cdp.evaluate(
                "({puertos: ports.size, pestañas: tabCount(), ws: ws ? ws.readyState : null, conectado: connected,"
                " activoMs: Math.round(performance.now()), historial: (self.__trazaPuertos || []).slice(-8).map("
                "(d) => [Date.now() - d.t, d.ev, d.tab, d.frame])})",
                self.sw_sess,
                5,
            )
        except Exception as e:  # noqa: BLE001
            return f"no disponible: {e}"


@dataclass
class Tab:
    cdp: CDP
    tid: str
    sess: str

    def ev(self, expr: str, timeout: float = 15) -> object:
        return self.cdp.evaluate(expr, self.sess, timeout)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.cdp.send("Target.closeTarget", {"targetId": self.tid})


def open_tab(lab: Lab, url: str) -> Tab:
    """Pestaña nueva con el espía de getUserMedia instalado antes de cualquier script de la página."""
    cdp = lab.cdp
    tid = cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
    sess = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
    tab = Tab(cdp, tid, sess)
    cdp.send("Runtime.enable", session=sess)
    cdp.send("Page.enable", session=sess)
    with contextlib.suppress(
        Exception
    ):  # que la pestaña se comporte como enfocada (sin estrangular temporizadores)
        cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, session=sess)
    for permiso, valor in (("audioCapture", "denied"), ("videoCapture", "granted")):
        with contextlib.suppress(Exception):
            cdp.send(
                "Browser.setPermission",
                {"permission": {"name": permiso}, "setting": valor, "origin": lab.origin},
            )
    cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": ESPIA_JS}, session=sess)
    cdp.send("Page.navigate", {"url": url}, session=sess)

    def cargada() -> bool:
        try:
            return bool(tab.ev("!!window.__lab"))
        except Exception:  # noqa: BLE001 - la navegación aún destruye el contexto
            return False

    if not wait_for(cargada, 15, 0.25):
        raise RuntimeError(f"la página {url} no cargó")
    return tab


def esperar_puente(tab: Tab, timeout: float = 20) -> dict | None:
    """Espera a que el puente de la pestaña (content script) esté conectado al service worker."""

    def conectado() -> bool:
        try:
            return bool((tab.ev("__info()") or {}).get("conectado"))
        except Exception:  # noqa: BLE001
            return False

    t0 = time.time()
    ok = wait_for(conectado, timeout)
    log(
        f"    puente de la pestaña {'conectado' if ok else 'SIN conectar'} a los {time.time() - t0:.2f} s de cargar"
    )
    return tab.ev("__info()")  # type: ignore[return-value]


def enviar_tono(lab: Lab, tab: Tab | None = None, espera: float = TONE_SECONDS + 0.9) -> int:
    """Envía el tono. Con ``tab``, espera a que el trozo LLEGUE a esa pestaña (hasta 8 s: un navegador cargado
    puede tardar) y mide desde ahí; la latencia queda en ``lab.llegada`` (None = no llegó en 8 s = pérdida)."""
    lab.llegada = None
    antes = 0
    # La extensión informa al servidor de sus pestañas con un mensaje «tabs»; justo tras abrir/cerrar una pestaña
    # el servidor puede creer unos cientos de ms que no hay receptores y no enviaría nada.
    wait_for(lambda: lab.server.receivers >= 1, 5, 0.02)
    if tab is not None:
        i = tab.ev("__info()")
        antes = i["trozosRecibidos"] + i["trozosDescartados"]  # type: ignore[index]
    t_push = time.time()
    n = lab.server.push(lab.tone, TONE_RATE)
    if tab is None or not n:
        time.sleep(espera)  # el tono dura 1,5 s; el resto cubre latencia y la ventana del analizador
        return n

    def llego() -> bool:
        i = tab.ev("__info()")
        return i["trozosRecibidos"] + i["trozosDescartados"] > antes  # type: ignore[index]

    if wait_for(llego, 8, 0.05):
        lab.llegada = time.time() - t_push
    time.sleep(TONE_SECONDS + 0.7)
    return n


def tono_medido(lab: Lab, tab: Tab, i: int) -> dict:
    """Envía un tono y devuelve el pico (rms, freq) medido en la pista i de la pestaña."""
    tab.ev(f"__lab.reiniciar({i})")
    t0 = time.time()
    entregado = enviar_tono(lab, tab)
    traza = [(round(t - t0, 2), m) for t, m in lab.trazas if t > t0 - 8]
    pico = tab.ev(f"__lab.pico({i})")
    return {  # type: ignore[return-value]
        **pico,  # type: ignore[dict-item]
        "entregado": entregado,
        "llegada": lab.llegada,
        "info": tab.ev("__info()"),
        "traza": traza,
        "sw": lab.estado_sw(),
    }


def suena(p: dict) -> bool:
    return p["rms"] > 0.05 and abs(p["freq"] - TONE_HZ) < 15


def fmt_pico(p: dict) -> str:
    extra = ""
    if "entregado" in p:
        ll = "NO llegó en 8 s" if p["llegada"] is None else f"{p['llegada'] * 1000:.0f} ms"
        extra = f", entregado a {p['entregado']} cliente(s), llegada a la pestaña={ll}, estado={p['info']}"
        extra += f", service worker={p['sw']}"
        if not p["entregado"]:
            extra += f", mensajes de la extensión (s respecto al envío)={p['traza']}"
    return f"RMS pico={p['rms']:.3f}, frecuencia={p['freq']:.1f} Hz{extra}"


def escenario(lab: Lab, nombre: str, fn) -> None:  # noqa: ANN001
    """Ejecuta un escenario; una excepción de infraestructura se informa como FALLA, sin tumbar el resto."""
    log(f"Escenario: {nombre}")
    try:
        fn(lab)
    except Exception as e:  # noqa: BLE001
        lab.rep.add(f"{nombre} (error de ejecución)", False, f"{type(e).__name__}: {e}")


def esc1_restricciones_meet(lab: Lab) -> None:
    rep = lab.rep
    tab = open_tab(lab, lab.lab_url)
    try:
        esperar_puente(tab)
        restricciones = {
            "audio": {
                "deviceId": {"exact": "default"},
                "echoCancellation": True,
                "noiseSuppression": True,
                "autoGainControl": True,
                "channelCount": {"ideal": 1},
            },
            "video": {"width": {"ideal": 1280}, "height": {"ideal": 720}, "facingMode": "user"},
        }
        r = tab.ev(f"__lab.pedirSeguro({json.dumps(restricciones)}, true)")
        if r.get("error"):  # type: ignore[union-attr]
            rep.add(
                "(1a) Meet: getUserMedia(audio+vídeo con restricciones) devuelve audio virtual y vídeo",
                False,
                f"error={r['error']}",
            )  # type: ignore[index]
            return
        audio, video = r["audio"], r["video"]  # type: ignore[index]
        ok_a = (
            len(audio) == 1
            and audio[0]["label"] == VIRTUAL_LABEL
            and audio[0]["estado"] == "live"
            and len(video) == 1
            and video[0]["kind"] == "video"
            and video[0]["estado"] == "live"
            and VIRTUAL_LABEL not in video[0]["label"]
        )
        rep.add(
            "(1a) Meet: getUserMedia(audio+vídeo con restricciones) devuelve audio virtual y vídeo",
            ok_a,
            f"audio={[a['label'] for a in audio]}, vídeo={[(v['label'], v['settings'].get('width'), v['settings'].get('height')) for v in video]}",
        )
        st = audio[0]["settings"]
        ok_b = all(st.get(k) is False for k in ("echoCancellation", "noiseSuppression", "autoGainControl"))
        rep.add(
            "(1b) Meet: getSettings() del audio declara echoCancellation/noiseSuppression/autoGainControl = false",
            ok_b,
            f"echoCancellation={st.get('echoCancellation')}, noiseSuppression={st.get('noiseSuppression')}, "
            f"autoGainControl={st.get('autoGainControl')}, deviceId={st.get('deviceId')!r}",
        )
        espia = tab.ev("window.__espia")
        reales = [json.loads(x) for x in espia["restricciones"]]  # type: ignore[index]
        con_audio = [x for x in reales if isinstance(x, dict) and x.get("audio")]
        ok_c = espia["audioReal"] == 0 and not con_audio and len(reales) == 1 and "video" in reales[0]  # type: ignore[index]
        rep.add(
            "(1c) Meet: el micrófono real no se abre (al método real solo llega el vídeo, sin «audio»)",
            ok_c,
            f"llamadas reales={reales}, pistas de audio reales devueltas={espia['audioReal']}",  # type: ignore[index]
        )
        p = tono_medido(lab, tab, r["i"])  # type: ignore[index]
        rep.add(
            "(1d) Meet: el tono llega a la pista de audio virtual del stream audio+vídeo",
            suena(p),
            fmt_pico(p),
        )
    finally:
        tab.close()


def esc2_repeticion_y_paradas(lab: Lab) -> None:
    rep = lab.rep
    tab = open_tab(lab, lab.lab_url)
    try:
        esperar_puente(tab)
        r1 = tab.ev("__lab.pedirSeguro({audio:true}, true)")
        if r1.get("error"):  # type: ignore[union-attr]
            rep.add("(2a) primera solicitud del micrófono", False, f"error={r1['error']}")  # type: ignore[index]
            return
        i1 = r1["i"]  # type: ignore[index]
        p1 = tono_medido(lab, tab, i1)
        rep.add(
            "(2a) primera solicitud: la pista virtual recibe el tono",
            suena(p1),
            f"{fmt_pico(p1)}, estado={tab.ev('__info()')}, pestañas informadas={list(lab.server._clients.values())}",
        )

        # Se detienen TODAS las pistas: la extensión no debe reproducir nada en esta pestaña.
        estados = tab.ev(f"__lab.parar({i1})")
        antes = tab.ev("__info()")
        entregado = enviar_tono(lab, tab)  # espera a que el trozo llegue (y se descarte) en la pestaña
        despues = tab.ev("__info()")
        ok_b = (
            estados == ["ended"]
            and antes["pistasVivas"] == 0  # type: ignore[index]
            and despues["pistasVivas"] == 0  # type: ignore[index]
            and despues["trozosRecibidos"] == antes["trozosRecibidos"]  # type: ignore[index]
            and despues["trozosDescartados"] > antes["trozosDescartados"]  # type: ignore[index]
        )
        rep.add(
            "(2b) con TODAS las pistas detenidas el tono NO se reproduce en la pestaña",
            ok_b,
            f"estado de la pista={estados}, tono entregado a {entregado} cliente(s); antes={antes}; después={despues}",
        )

        # Meet pide el micrófono otra vez (p. ej. al cambiar de dispositivo, con el deviceId del virtual).
        r2 = tab.ev("__lab.pedirSeguro({audio:{deviceId:{exact:'interpreter-virtual-mic'}}}, true)")
        if r2.get("error"):  # type: ignore[union-attr]
            rep.add("(2c) re-solicitud tras parar", False, f"error={r2['error']}")  # type: ignore[index]
            return
        i2 = r2["i"]  # type: ignore[index]
        p2 = tono_medido(lab, tab, i2)
        info2 = tab.ev("__info()")
        rep.add(
            "(2c) re-solicitud tras parar: la segunda pista vuelve a recibir el tono",
            suena(p2) and r2["audio"][0]["label"] == VIRTUAL_LABEL and info2["pistasVivas"] == 1,  # type: ignore[index]
            f"{fmt_pico(p2)}, etiqueta={r2['audio'][0]['label']!r}, estado={info2}",  # type: ignore[index]
        )

        # Orden habitual de Meet al cambiar de dispositivo: pedir la nueva ANTES de parar la vieja.
        r3 = tab.ev("__lab.pedirSeguro({audio:true}, true)")
        i3 = r3["i"]  # type: ignore[index]
        tab.ev(f"__lab.parar({i2})")
        info3 = tab.ev("__info()")
        p3 = tono_medido(lab, tab, i3)
        rep.add(
            "(2d) pedir la pista nueva ANTES de parar la vieja: parar la vieja no silencia a la nueva",
            suena(p3) and info3["pistasVivas"] == 1,  # type: ignore[index]
            f"{fmt_pico(p3)}, estado tras parar la vieja={info3}",
        )

        # Informativa: una página que clona la pista (p. ej. para una vista previa) y para la original.
        tab.ev(f"__lab.parar({i3})")
        r4 = tab.ev("__lab.pedirSeguro({audio:true}, false)")
        j = tab.ev(f"__lab.clonarYParar({r4['i']})")  # type: ignore[index]
        p4 = tono_medido(lab, tab, j)  # type: ignore[arg-type]
        info4 = tab.ev("__info()")
        rep.add(
            "(2e) [informativa] la página clona la pista y para la original: la copia sigue recibiendo el tono",
            suena(p4),
            f"{fmt_pico(p4)}, estado={info4}",
            info=True,
        )
        tab.ev(f"__lab.parar({j})")
        r5 = tab.ev("__lab.pedirSeguro({audio:true}, false)")
        k = tab.ev(f"__lab.clonarStreamYParar({r5['i']})")  # type: ignore[index]
        p5 = tono_medido(lab, tab, k)  # type: ignore[arg-type]
        rep.add(
            "(2f) [informativa] la página clona el MediaStream y para el original: la copia sigue recibiendo el tono",
            suena(p5),
            fmt_pico(p5),
            info=True,
        )
    finally:
        tab.close()


def esc3_iframes(lab: Lab) -> None:
    rep = lab.rep
    tab = open_tab(lab, lab.lab_url)
    try:
        esperar_puente(tab)
        casos = [
            ("(3a)", "srcdoc", "srcdoc", "", "iframe srcdoc"),
            ("(3b)", "blank", "blank", "", "iframe about:blank creado por script"),
            ("(3c)", "mismo", "src", lab.frame_url, "iframe de la misma origen"),
            ("(3d)", "otro", "src", lab.xframe_url, "iframe de OTRO origen (localhost)"),
        ]
        creados = {
            n: tab.ev(f"__crearFrame({json.dumps(n)}, {json.dumps(t)}, {json.dumps(u)})")
            for _, n, t, u, _ in casos
        }

        def estado_frame(n: str) -> dict | None:
            try:
                return tab.ev(f"__llamar({json.dumps(n)}, 'resultado', 700)")  # type: ignore[return-value]
            except Exception:  # noqa: BLE001 - el iframe aún no carga
                return None

        resultados: dict[str, dict | None] = {}
        for _, n, *_ in casos:
            wait_for(
                lambda n=n: (lambda r: bool(r and (r.get("listo") or r.get("error"))))(estado_frame(n)),
                15,
                0.2,
            )
            resultados[n] = estado_frame(n)

        def puente_frame(n: str) -> bool:
            try:
                return bool((tab.ev(f"__llamar({json.dumps(n)}, 'info', 700)") or {}).get("conectado"))
            except Exception:  # noqa: BLE001
                return False

        for _, n, *_ in casos:
            if resultados[n] and resultados[n].get("listo"):  # type: ignore[union-attr]
                wait_for(lambda n=n: puente_frame(n), 10, 0.2)

        # Un solo tono: lo reciben todos los frames a la vez y cada uno lo mide por dentro.
        listos = [n for _, n, *_ in casos if resultados[n] and resultados[n].get("listo")]  # type: ignore[union-attr]
        for n in listos:
            tab.ev(f"__llamar({json.dumps(n)}, 'reiniciar')")
        enviar_tono(lab, tab)
        time.sleep(0.6)  # los iframes pueden ir por detrás de la página superior
        picos = {n: tab.ev(f"__llamar({json.dumps(n)}, 'pico')") for n in listos}
        for prefijo, n, _t, _u, desc in casos:
            r = resultados[n]
            nombre = f"{prefijo} {desc}: getUserMedia devuelve la pista virtual y el tono se mide dentro del iframe"
            if not r or not r.get("listo"):
                rep.add(nombre, False, f"el iframe no llegó a abrir el micrófono: {r}; creado={creados[n]}")
                continue
            p = picos[n]
            espia = r.get("espia")
            sin_real = (espia is None) or espia.get("audioReal") == 0
            ok = r["etiqueta"] == VIRTUAL_LABEL and r["ext"] and sin_real and suena(p)  # type: ignore[arg-type]
            rep.add(
                nombre,
                bool(ok),
                f"etiqueta={r['etiqueta']!r}, extensión inyectada={r['ext']}, origen={r['origen']}, "
                f"espía del iframe={espia}, {fmt_pico(p)}, creación={creados[n]}",  # type: ignore[arg-type]
            )
    finally:
        tab.close()


def esc4_cambio_de_dispositivo(lab: Lab) -> None:
    rep = lab.rep
    tab = open_tab(lab, lab.lab_url)
    try:
        esperar_puente(tab)
        devs = tab.ev(
            "navigator.mediaDevices.enumerateDevices().then(l => l.map(d => ({kind: d.kind, label: d.label,"
            " deviceId: d.deviceId, esInfo: d instanceof MediaDeviceInfo})))"
        )
        entradas = [d for d in devs if d["kind"] == "audioinput"]  # type: ignore[union-attr]
        rep.add(
            "(4a) enumerateDevices lista PRIMERO el dispositivo virtual (y es el primer audioinput)",
            bool(devs)
            and devs[0]["deviceId"] == VIRTUAL_ID  # type: ignore[index]
            and devs[0]["label"] == VIRTUAL_LABEL  # type: ignore[index]
            and devs[0]["esInfo"]  # type: ignore[index]
            and bool(entradas)
            and entradas[0]["deviceId"] == VIRTUAL_ID,
            f"dispositivos={[(d['kind'], d['label'], d['deviceId']) for d in devs]}",  # type: ignore[union-attr]
        )

        r = tab.ev(f"__lab.pedirSeguro({json.dumps({'audio': {'deviceId': {'exact': VIRTUAL_ID}}})}, true)")
        if r.get("error"):  # type: ignore[union-attr]
            rep.add("(4b) deviceId exact del virtual", False, f"error={r['error']}")  # type: ignore[index]
        else:
            p = tono_medido(lab, tab, r["i"])  # type: ignore[index]
            st = r["audio"][0]["settings"]  # type: ignore[index]
            rep.add(
                "(4b) getUserMedia({deviceId:{exact:'interpreter-virtual-mic'}}) devuelve el virtual y suena",
                r["audio"][0]["label"] == VIRTUAL_LABEL and st.get("deviceId") == VIRTUAL_ID and suena(p),  # type: ignore[index]
                f"etiqueta={r['audio'][0]['label']!r}, deviceId={st.get('deviceId')!r}, {fmt_pico(p)}",  # type: ignore[index]
            )

        # Dispositivos «reales»: uno inventado que no existe y los que enumera el navegador (falsos aquí).
        reales_enum = [d["deviceId"] for d in entradas if d["deviceId"] and d["deviceId"] != VIRTUAL_ID][:2]
        ids = ["dispositivo-real-que-no-existe-0123456789abcdef", *reales_enum]
        detalle = []
        ok_c = True
        for ident in ids:
            for forma in ({"exact": ident}, ident):
                resp = tab.ev(f"__lab.pedirSeguro({json.dumps({'audio': {'deviceId': forma}})}, false)")
                etiqueta = None if resp.get("error") else resp["audio"][0]["label"]  # type: ignore[union-attr,index]
                detalle.append(
                    (
                        ident[:18],
                        "exact" if isinstance(forma, dict) else "plano",
                        resp.get("error") or etiqueta,
                    )
                )  # type: ignore[union-attr]
                ok_c &= etiqueta == VIRTUAL_LABEL
        espia = tab.ev("window.__espia")
        ok_c &= espia["llamadasReales"] == 0 and espia["audioReal"] == 0  # type: ignore[index]
        rep.add(
            "(4c) un deviceId de dispositivo real (inexistente o enumerado) devuelve igualmente el virtual, sin abrir el real",
            bool(ok_c),
            f"resultados={detalle}; llamadas reales={espia['llamadasReales']}, audio real={espia['audioReal']}",  # type: ignore[index]
        )
    finally:
        tab.close()


def esc5_dos_pestanas(lab: Lab) -> None:
    rep = lab.rep
    a = open_tab(lab, lab.lab_url)
    b = None
    try:
        b = open_tab(lab, lab.lab_url)  # misma página, pero esta NO pide el micrófono
        esperar_puente(a)
        esperar_puente(b)
        ra = a.ev("__lab.pedirSeguro({audio:true}, true)")
        if ra.get("error"):  # type: ignore[union-attr]
            rep.add("(5a) pestaña con micrófono", False, f"error={ra['error']}")  # type: ignore[index]
            return
        wait_for(
            lambda: all(t == 2 for t in lab.server._clients.values()), 5
        )  # el servicio ya ve las 2 pestañas
        tabs_vistas = list(lab.server._clients.values())
        with contextlib.suppress(Exception):
            lab.cdp.send("Target.activateTarget", {"targetId": a.tid})
        a.ev(f"__lab.reiniciar({ra['i']})")  # type: ignore[index]
        antes_b = b.ev("__info()")
        entregado = enviar_tono(lab, a)
        wait_for(lambda: b.ev("__info()")["trozosDescartados"] > antes_b["trozosDescartados"], 5)  # type: ignore[index]
        pico_a = a.ev(f"__lab.pico({ra['i']})")  # type: ignore[index]
        info_a = a.ev("__info()")
        info_b = b.ev("__info()")
        rep.add(
            "(5a) pestaña CON micrófono: reproduce el tono",
            suena(pico_a) and info_a["trozosRecibidos"] > 0 and info_a["pistasVivas"] == 1,  # type: ignore[index,arg-type]
            f"{fmt_pico(pico_a)}, estado={info_a}",  # type: ignore[arg-type]
        )
        rep.add(
            "(5b) pestaña SIN micrófono (misma página): no reproduce nada",
            info_b["trozosRecibidos"] == 0
            and info_b["pistasVivas"] == 0
            and info_b["audioContext"] == "sin crear",  # type: ignore[index]
            f"antes={antes_b}, después={info_b}",
        )
        rep.add(
            "(5c) [informativa] Interpreter cuenta ambas pestañas como receptoras aunque una no tenga micrófono",
            tabs_vistas == [2],
            f"pestañas informadas por la extensión={tabs_vistas}, tono entregado a {entregado} cliente(s)",
            info=True,
        )
    finally:
        a.close()
        if b:
            b.close()


CLIP_VOZ = Path(__file__).resolve().parents[1] / "data" / "muestras_voz" / "en_yourtts_CLON_mi_voz.wav"


def analizar_continuidad(ref: np.ndarray, ref_rate: int, rec: np.ndarray, rec_rate: int) -> dict:
    """Compara la voz enviada con la que llegó a la página: cortes que NO estaban en la original.

    Se alinean las envolventes (RMS cada 5 ms) y se cuentan los tramos donde la original suena (RMS > 0,01) y lo
    recibido casi no (< 10 % de la original). Devuelve milisegundos de hueco total, hueco más largo y correlación.
    """
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(ref_rate, rec_rate)
    ref_r = resample_poly(ref, rec_rate // g, ref_rate // g).astype(np.float32)
    frame = max(1, int(rec_rate * 0.005))

    def env(x: np.ndarray) -> np.ndarray:
        n = len(x) // frame
        return np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))

    e_ref, e_rec = env(ref_r), env(rec)
    if len(e_rec) < len(e_ref) or e_ref.max() == 0:
        return {"error": f"grabación corta ({len(e_rec)} < {len(e_ref)} tramos de 5 ms)"}
    a = (e_ref - e_ref.mean()) / (e_ref.std() + 1e-9)
    best, lag = -2.0, 0
    for k in range(0, len(e_rec) - len(e_ref) + 1):
        w = e_rec[k : k + len(e_ref)]
        c = float(np.dot(a, (w - w.mean()) / (w.std() + 1e-9)) / len(a))
        if c > best:
            best, lag = c, k
    w = e_rec[lag : lag + len(e_ref)]
    suena = e_ref > 0.01
    hueco = suena & (w < 0.1 * e_ref)
    # racha más larga de tramos consecutivos con hueco
    mejor = racha = 0
    for h in hueco:
        racha = racha + 1 if h else 0
        mejor = max(mejor, racha)
    return {
        "correlacion": round(best, 3),
        "retardo_ms": round(lag * 5),
        "hueco_total_ms": int(hueco.sum() * 5),
        "hueco_max_ms": mejor * 5,
        "tramos_con_voz_ms": int(suena.sum() * 5),
        "ganancia": round(float(w[suena].mean() / e_ref[suena].mean()), 3) if suena.any() else None,
    }


def esc6_continuidad(lab: Lab) -> None:
    """Una frase de voz REAL (YourTTS clonado, la misma que Interpreter envía) llega sin cortes a la página."""
    import wave

    rep = lab.rep
    if not CLIP_VOZ.exists():
        rep.add("(6) continuidad con voz real", False, f"falta la muestra {CLIP_VOZ}")
        return
    with wave.open(str(CLIP_VOZ)) as w:
        rate = w.getframerate()
        ref = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    tab = open_tab(lab, lab.lab_url)
    try:
        esperar_puente(tab)
        r = tab.ev("__lab.pedirSeguro({audio:true}, true)")
        if r.get("error"):  # type: ignore[union-attr]
            rep.add("(6) continuidad con voz real", False, f"error={r['error']}")  # type: ignore[index]
            return
        i = r["i"]  # type: ignore[index]
        resultados = []
        for repeticion in range(3):
            wait_for(lambda: lab.server.receivers >= 1, 5, 0.02)
            tab.ev(f"__lab.iniciarGrab({i})")
            entregado = lab.server.push(ref, rate)  # un solo trozo, como hace YourTTS en la app
            time.sleep(len(ref) / rate + 1.5)
            data = tab.ev(f"__lab.terminarGrab({i})", timeout=30)
            rec = np.asarray(data["samples"], dtype=np.float32) / 32767  # type: ignore[index]
            res = analizar_continuidad(ref, rate, rec, int(data["rate"]))  # type: ignore[index]
            res["entregado"] = entregado
            resultados.append(res)
            lab.server.flush()
            time.sleep(0.5)
        malos = [x for x in resultados if "error" in x or x["hueco_total_ms"] > 20 or x["correlacion"] < 0.8]
        rep.add(
            "(6) voz real (YourTTS) llega sin cortes a la página (3 repeticiones)",
            not malos,
            f"resultados={resultados}",
        )
    finally:
        tab.close()


def esc7_llamada_webrtc(lab: Lab) -> None:
    """La voz real pasa por una «llamada» WebRTC/Opus (con y sin supresión de silencios, DTX) sin perder el inicio."""
    import wave

    rep = lab.rep
    with wave.open(str(CLIP_VOZ)) as w:
        rate = w.getframerate()
        ref = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    for dtx in (False, True):
        tab = open_tab(lab, lab.lab_url)
        try:
            esperar_puente(tab)
            r = tab.ev("__lab.pedirSeguro({audio:true}, false)")
            if r.get("error"):  # type: ignore[union-attr]
                rep.add(f"(7) llamada WebRTC (DTX={dtx})", False, f"error={r['error']}", info=True)  # type: ignore[index]
                continue
            j = tab.ev(f"__lab.loopback({r['i']}, {str(dtx).lower()})", timeout=30)  # type: ignore[index]
            time.sleep(2.0)  # que la «llamada» se estabilice (con DTX, el silencio inicial ya está en modo cómodo)
            resultados = []
            for _ in range(3):
                wait_for(lambda: lab.server.receivers >= 1, 5, 0.02)
                tab.ev(f"__lab.iniciarGrab({j})")
                lab.server.push(ref, rate)
                time.sleep(len(ref) / rate + 2.5)
                data = tab.ev(f"__lab.terminarGrab({j})", timeout=30)
                rec = np.asarray(data["samples"], dtype=np.float32) / 32767  # type: ignore[index]
                resultados.append(analizar_continuidad(ref, rate, rec, int(data["rate"])))  # type: ignore[index]
                lab.server.flush()
                time.sleep(3.0)  # silencio entre frases: con DTX la «llamada» vuelve a ruido de confort
            malos = [x for x in resultados if "error" in x or x["hueco_total_ms"] > 40]
            rep.add(
                f"(7) llamada WebRTC/Opus con DTX={'sí' if dtx else 'no'}: la voz llega sin huecos",
                not malos,
                f"resultados={resultados}",
                info=True,  # informativa: mide, no condiciona PASA/FALLA
            )
        finally:
            tab.close()


# -- Una ejecución contra un navegador ---------------------------------------------------------------


def run_browser(
    name: str,
    exe: Path,
    page_url: str,
    ext_dir: Path,
    tmp: Path,
    headed: bool,
    keep: bool,
    ws_port: int,
    manifest_malos: list[str],
) -> Report:
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
        # Concede getUserMedia sin ventana: SOLO se necesita para el vídeo falso del escenario 1 (Meet pide
        # cámara+micro). El audio real sigue vigilado por el espía (ver ESPIA_JS), no por el permiso.
        "--use-fake-ui-for-media-stream",
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
    server = WebMicServer(port=ws_port)
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
            # Se exige el ID de NUESTRA extensión (fijado por la clave del manifest): Edge/Chrome recientes traen
            # extensiones propias con un background.js (p. ej. la de Gemini) que no son la que se prueba.
            for t in cdp.send("Target.getTargets")["targetInfos"]:
                if t["type"] == "service_worker" and t["url"].startswith(
                    f"chrome-extension://{EXTENSION_ID}/background.js"
                ):
                    return t
            return None

        if not wait_for(lambda: sw_target() is not None, 8):
            # Plan B: cargar vía CDP (solo funciona si el navegador se lanzó con --enable-unsafe-extension-debugging)
            rep.notes.append(
                "El service worker no apareció con --load-extension; se intenta Extensions.loadUnpacked."
            )
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
        rep.add(
            "(0) el manifest de browser-extension/ lo acepta el navegador (patrones válidos con match_origin_as_fallback)",
            not manifest_malos,
            "sin patrones inválidos"
            if not manifest_malos
            else "el navegador se niega a cargar la extensión con estos patrones (error «The path component for scripts "
            "with 'match_origin_as_fallback' must be '*'»); en la copia de la prueba se quitaron para poder seguir: "
            + "; ".join(manifest_malos),
        )

        # Sesión sobre el service worker para recoger sus errores
        sw_sess = cdp.send("Target.attachToTarget", {"targetId": sw["targetId"], "flatten": True})[
            "sessionId"
        ]
        with contextlib.suppress(Exception):
            cdp.send("Runtime.enable", session=sw_sess)
        # Diagnóstico (solo observa): registra cuándo se conectan/desconectan los puertos de las pestañas.
        with contextlib.suppress(Exception):
            cdp.evaluate(TRAZA_SW_JS, sw_sess)

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

        ready = wait_for(
            lambda: bool(
                cdp.evaluate(
                    "!!(window.__resultado && (window.__resultado.listo || window.__resultado.error))", sess
                )
            ),
            15,
            0.25,
        )
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
        got_bridge = wait_for(
            lambda: bool((cdp.evaluate("window.__estado()", sess) or {}).get("conectado")), 10
        )
        estado = cdp.evaluate("window.__estado()", sess)
        rep.add("pestaña recibe el estado «conectado» del puente", got_bridge, f"estado={estado}")
        if not got_bridge:
            return rep

        # (c) enumerateDevices
        labels = [d["label"] for d in res["dispositivos"]]
        rep.add(
            "(c) enumerateDevices incluye el dispositivo virtual",
            VIRTUAL_LABEL in labels,
            f"dispositivos={labels}",
        )

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
        info_d0 = cdp.evaluate("window.__estado()", sess)
        t_d = time.time()
        entregado_d = server.push(tone, TONE_RATE)
        sonando = wait_for(lambda: cdp.evaluate("window.__pico()", sess)["rms"] > 0.05, 8, 0.05)
        t_sonando = time.time() - t_d
        server.flush()
        time.sleep(0.35)  # vacía la ventana del analizador (8192 muestras) y la latencia
        cdp.evaluate("window.__reiniciar()", sess)
        time.sleep(0.6)  # el tono habría seguido sonando ~1 s más sin flush
        p3 = cdp.evaluate("window.__pico()", sess)
        rep.add(
            "(d) tras flush() vuelve el silencio",
            sonando and p3["rms"] < 1e-3,
            f"sonó antes del flush={sonando} (a los {t_sonando * 1000:.0f} ms de enviarlo; entregado a {entregado_d} "
            f"cliente(s)), RMS tras flush={p3['rms']:.2e}; estado antes={info_d0}, después={cdp.evaluate('window.__estado()', sess)}",
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

        # Escenarios estilo Meet / Zoom web, en pestañas nuevas (la de la prueba base se cierra antes).
        with contextlib.suppress(Exception):
            cdp.send("Target.closeTarget", {"targetId": tid})
        origin = page_url.rstrip("/")
        lab = Lab(
            cdp=cdp,
            server=server,
            rep=rep,
            origin=origin,
            lab_url=page_url + "lab",
            frame_url=page_url + "frame",
            xframe_url=page_url.replace("127.0.0.1", "localhost") + "frame",
            tone=tone,
            sw_sess=sw_sess,
        )
        original = server._on_client_message

        def registrar(ws, message):  # noqa: ANN001 - solo diagnóstico: qué pestañas informa la extensión y cuándo
            lab.trazas.append((time.time(), message))
            original(ws, message)

        server._on_client_message = registrar  # type: ignore[method-assign]
        escenario(lab, "1) restricciones estilo Meet (audio+vídeo)", esc1_restricciones_meet)
        escenario(lab, "2) re-solicitud y paradas", esc2_repeticion_y_paradas)
        escenario(lab, "3) iframes (srcdoc, about:blank, misma y otra origen)", esc3_iframes)
        escenario(lab, "4) cambio de dispositivo por deviceId", esc4_cambio_de_dispositivo)
        escenario(lab, "5) dos pestañas", esc5_dos_pestanas)
        escenario(lab, "6) continuidad con voz real", esc6_continuidad)
        escenario(lab, "7) llamada WebRTC con voz real", esc7_llamada_webrtc)

        # Errores del service worker / de la página
        cdp.pump(0.2)
        errs = [e for e in cdp.events if e.get("method") in ("Runtime.exceptionThrown",)]
        rep.add(
            "sin excepciones JS en extensión/página",
            not errs,
            f"{len(errs)} excepción(es)" + (f": {errs[0]}" if errs else ""),
        )
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
    ap.add_argument(
        "--extension-dir",
        default=str(EXTENSION_DIR),
        help="carpeta de la extensión a probar (por defecto browser-extension/); sirve para probar una corrección "
        "propuesta en una copia sin tocar el proyecto",
    )
    ap.add_argument("--keep", action="store_true", help="no cerrar el navegador al terminar (depuración)")
    ap.add_argument(
        "--puerto",
        type=int,
        default=0,
        help=f"puerto del WebSocket de la prueba. 0 (por defecto) = uno libre al azar, aislado del navegador habitual; "
        f"{DEFAULT_PORT} = el real (la extensión queda SIN modificar, pero cualquier navegador tuyo con la extensión "
        "instalada se conectaría al servidor de la prueba y recibiría sus tonos)",
    )
    opts = ap.parse_args()

    ws_port = opts.puerto or free_port()
    if ws_port == DEFAULT_PORT:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", DEFAULT_PORT))
            except OSError:
                print(f"El puerto {DEFAULT_PORT} está ocupado (¿Interpreter en marcha?). Ciérralo y repite.")
                return 2
        log(
            f"AVISO: puerto real {DEFAULT_PORT}: tu navegador habitual con la extensión puede recibir los tonos."
        )

    http_port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", http_port), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{http_port}/"
    log(f"Página de prueba: {page_url} (CSP: {CSP}); WebSocket de la prueba en el puerto {ws_port}")

    tmp = Path(tempfile.mkdtemp(prefix="interpreter-ext-"))
    ext_dir, manifest_malos = make_test_extension(
        Path(opts.extension_dir).resolve(), tmp / "extension", ws_port
    )
    names = ["edge", "chrome"] if opts.browser == "auto" else [opts.browser]
    final: Report | None = None
    try:
        for name in names:
            exe = BROWSERS[name]
            if not exe.exists():
                log(f"{name}: no instalado ({exe})")
                continue
            rep = run_browser(
                name, exe, page_url, ext_dir, tmp, opts.headed, opts.keep, ws_port, manifest_malos
            )
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
    cont = final.counted
    print(
        f"RESULTADO en {final.browser}: {'PASA' if final.passed else 'FALLA'} ({sum(c.ok for c in cont)}/{len(cont)} comprobaciones)"
    )
    for c in final.checks:
        marca = ("INFO " if c.ok else "AVISO") if c.info else ("OK   " if c.ok else "FALLA")
        print(f"  {marca} {c.name}")
    return 0 if final.passed else 1


if __name__ == "__main__":
    sys.exit(main())
