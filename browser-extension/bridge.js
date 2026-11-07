// Content script (mundo aislado): recibe el audio del service worker y se lo pasa a la página
// (inject.js, mundo principal) con window.postMessage.

(() => {
  if (window.__interpreterBridge) return;
  window.__interpreterBridge = true;

  let port = null;
  let heartbeat = null;

  function toPage(msg, transfer) {
    window.postMessage({ __interpreter: 1, ...msg }, "*", transfer || []);
  }

  function base64ToBuffer(b64) {
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return u8.buffer;
  }

  function connect() {
    try {
      port = chrome.runtime.connect({ name: "interpreter" });
    } catch (_) {
      setTimeout(connect, 2000); // la extensión se recargó: se reintenta
      return;
    }
    port.onMessage.addListener((m) => {
      if (m.t === "pcm") {
        const buffer = base64ToBuffer(m.b);
        toPage({ type: "pcm", rate: m.r, buffer }, [buffer]);
      } else if (m.t === "flush") {
        toPage({ type: "flush" });
      } else if (m.t === "status") {
        toPage({ type: "status", connected: m.connected });
      }
    });
    port.onDisconnect.addListener(() => {
      port = null;
      clearInterval(heartbeat);
      toPage({ type: "status", connected: false });
      setTimeout(connect, 1000);
    });
    clearInterval(heartbeat);
    heartbeat = setInterval(() => {
      try { port && port.postMessage({ t: "ping" }); } catch (_) { /* se reconecta en onDisconnect */ }
    }, 15000);
  }

  connect();
})();
