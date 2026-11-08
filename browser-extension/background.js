// Service worker: mantiene el WebSocket con Interpreter (127.0.0.1) y reparte el audio a las pestañas.
// El WebSocket lo abre la extensión (no la página) para evitar el CSP de Teams y los permisos de
// "red local" que los navegadores piden a las páginas web.

const WS_URL = "ws://127.0.0.1:8765";
const ports = new Set();
let ws = null;
let retryMs = 250;
let connected = false;

function broadcast(msg) {
  for (const p of ports) {
    try { p.postMessage(msg); } catch (_) { ports.delete(p); }
  }
}

function bytesToBase64(bytes) {
  let s = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  return btoa(s);
}

// Pestañas distintas con un content script activo (una pestaña puede tener varios frames, y cada uno un puerto).
function tabCount() {
  const ids = new Set();
  for (const p of ports) {
    const id = p.sender && p.sender.tab ? p.sender.tab.id : null;
    if (id !== null && id !== undefined) ids.add(id);
  }
  return ids.size;
}

// Interpreter usa este número para saber si alguien reproducirá el audio.
function reportTabs() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify({ type: "tabs", n: tabCount(), v: chrome.runtime.getManifest().version })); } catch (_) { /* se reintenta al reconectar */ }
  }
}

function setConnected(v) {
  if (connected === v) return;
  connected = v;
  broadcast({ t: "status", connected: v });
}

function scheduleReconnect() {
  setTimeout(connect, retryMs);
  retryMs = Math.min(retryMs * 2, 3000);
}

function connect() {
  if (ws && ws.readyState <= 1) return; // ya conectado o conectando
  let me;
  try {
    me = ws = new WebSocket(WS_URL);
  } catch (_) {
    scheduleReconnect();
    return;
  }
  me.binaryType = "arraybuffer";
  me.onopen = () => { if (me !== ws) return; retryMs = 250; setConnected(true); reportTabs(); };
  me.onmessage = (ev) => {
    if (me !== ws) return;
    if (typeof ev.data === "string") {
      try {
        const m = JSON.parse(ev.data);
        if (m.type === "flush") broadcast({ t: "flush" });
        // "hello" y "ping" no requieren respuesta: recibirlos ya reinicia el temporizador del service worker
      } catch (_) { /* mensaje de texto no válido: se ignora */ }
      return;
    }
    const u8 = new Uint8Array(ev.data);
    if (u8.length < 6) return;
    const rate = new DataView(ev.data).getUint32(0, true);
    broadcast({ t: "pcm", r: rate, b: bytesToBase64(u8.subarray(4)) });
  };
  me.onclose = () => {
    if (me !== ws) return; // un socket antiguo no debe pisar el estado del nuevo
    ws = null;
    setConnected(false);
    scheduleReconnect();
  };
  me.onerror = () => { try { me.close(); } catch (_) { /* ya cerrado */ } };
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "interpreter") return;
  ports.add(port);
  port.postMessage({ t: "status", connected });
  port.onMessage.addListener((m) => {
    if (m && m.t === "ping") connect(); // el latido de la pestaña también despierta/reconecta el worker
  });
  port.onDisconnect.addListener(() => { ports.delete(port); reportTabs(); });
  connect();
  reportTabs();
});

connect();
