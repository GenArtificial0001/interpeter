// Service worker: mantiene el WebSocket con Interpreter (127.0.0.1) y reparte el audio a las pestañas.
// El WebSocket lo abre la extensión (no la página) para evitar el CSP de Teams y los permisos de
// "red local" que los navegadores piden a las páginas web.

const WS_URL = "ws://127.0.0.1:8765";
const ports = new Set();
let ws = null;
let retryMs = 1000;
let connecting = false;
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

function setConnected(v) {
  if (connected === v) return;
  connected = v;
  broadcast({ t: "status", connected: v });
}

function scheduleReconnect() {
  setTimeout(connect, retryMs);
  retryMs = Math.min(retryMs * 2, 10000);
}

function connect() {
  if (connecting || (ws && ws.readyState <= 1)) return;
  connecting = true;
  try {
    ws = new WebSocket(WS_URL);
  } catch (_) {
    connecting = false;
    scheduleReconnect();
    return;
  }
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { connecting = false; retryMs = 1000; setConnected(true); };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      try {
        const m = JSON.parse(ev.data);
        if (m.type === "flush") broadcast({ t: "flush" });
      } catch (_) { /* mensaje de texto no válido: se ignora */ }
      return;
    }
    const u8 = new Uint8Array(ev.data);
    if (u8.length < 6) return;
    const rate = new DataView(ev.data).getUint32(0, true);
    broadcast({ t: "pcm", r: rate, b: bytesToBase64(u8.subarray(4)) });
  };
  ws.onclose = () => { connecting = false; setConnected(false); scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch (_) { /* ya cerrado */ } };
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "interpreter") return;
  ports.add(port);
  port.postMessage({ t: "status", connected });
  port.onMessage.addListener((m) => {
    if (m && m.t === "ping") connect(); // el latido de la pestaña también despierta/reconecta el worker
  });
  port.onDisconnect.addListener(() => ports.delete(port));
  connect();
});

connect();
