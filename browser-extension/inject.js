// Se ejecuta en el mundo PRINCIPAL de la página (Teams/Meet web), antes que su código.
// Sustituye el micrófono: getUserMedia({audio}) devuelve un MediaStream alimentado con la voz traducida
// de Interpreter. El micrófono real nunca se abre ni se envía a la llamada.

(() => {
  if (window.__interpreterVirtualMic) return;
  window.__interpreterVirtualMic = true;

  const md = navigator.mediaDevices;
  if (!md || !md.getUserMedia) return;

  const origGetUserMedia = md.getUserMedia.bind(md);
  const origEnumerateDevices = md.enumerateDevices ? md.enumerateDevices.bind(md) : null;

  const VIRTUAL_ID = "interpreter-virtual-mic";
  const VIRTUAL_LABEL = "Interpreter (voz traducida)";
  const JITTER_S = 0.06; // colchón al empezar a sonar, para absorber variaciones de llegada

  let ctx = null;
  let dest = null;
  let playHead = 0;
  const active = new Set();
  const state = { connected: false, chunks: 0 };

  function ensureStream() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      ctx = new AC({ latencyHint: "interactive" });
      dest = ctx.createMediaStreamDestination();
      // Fuente muda y continua: el track siempre está "vivo" y emite silencio entre frases.
      const osc = ctx.createOscillator();
      const mute = ctx.createGain();
      mute.gain.value = 0;
      osc.connect(mute).connect(dest);
      osc.start();
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    return dest.stream;
  }

  function enqueue(rate, int16) {
    ensureStream();
    const f32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
    const buf = ctx.createBuffer(1, f32.length, rate);
    buf.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(dest);
    const now = ctx.currentTime;
    if (playHead < now + 0.02) playHead = now + JITTER_S; // la cola se vació: nuevo colchón
    src.start(playHead);
    playHead += buf.duration;
    active.add(src);
    src.onended = () => active.delete(src);
    state.chunks++;
  }

  function flush() {
    for (const s of active) { try { s.stop(); } catch (_) { /* ya terminó */ } }
    active.clear();
    playHead = 0;
  }

  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (!d || d.__interpreter !== 1) return;
    if (d.type === "pcm" && d.buffer) enqueue(d.rate, new Int16Array(d.buffer));
    else if (d.type === "flush") flush();
    else if (d.type === "status") state.connected = !!d.connected;
  });

  // Reanuda el audio con cualquier gesto del usuario (las políticas de autoplay lo pueden suspender).
  for (const evName of ["click", "keydown", "touchstart"]) {
    window.addEventListener(evName, () => { if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {}); }, true);
  }

  md.getUserMedia = async function (constraints) {
    if (!constraints || !constraints.audio) return origGetUserMedia(constraints);
    const out = new MediaStream();
    if (constraints.video) { // la cámara real se deja pasar; solo se sustituye el audio
      const v = await origGetUserMedia({ video: constraints.video });
      v.getVideoTracks().forEach((t) => out.addTrack(t));
    }
    const track = ensureStream().getAudioTracks()[0].clone(); // clon: parar uno no apaga el origen
    out.addTrack(track);
    return out;
  };

  if (origEnumerateDevices) {
    md.enumerateDevices = async function () {
      const list = await origEnumerateDevices();
      const virtual = {
        deviceId: VIRTUAL_ID,
        kind: "audioinput",
        label: VIRTUAL_LABEL,
        groupId: VIRTUAL_ID,
        toJSON() { return { deviceId: VIRTUAL_ID, kind: "audioinput", label: VIRTUAL_LABEL, groupId: VIRTUAL_ID }; },
      };
      Object.setPrototypeOf(virtual, MediaDeviceInfo.prototype);
      return [virtual, ...list];
    };
  }

  // Diagnóstico desde la consola de la página: window.__interpreterVirtualMicInfo()
  window.__interpreterVirtualMicInfo = () => ({
    conectado: state.connected,
    trozosRecibidos: state.chunks,
    audioContext: ctx ? ctx.state : "sin crear",
  });
})();
