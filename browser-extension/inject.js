// Se ejecuta en el mundo PRINCIPAL de la página (Teams/Meet web), antes que su código.
// Sustituye el micrófono: getUserMedia({audio}) devuelve un MediaStream alimentado con la voz traducida
// de Interpreter. El micrófono real nunca se abre ni se envía a la llamada.

(() => {
  if (window.__interpreterVirtualMic) return;
  window.__interpreterVirtualMic = true;

  const md = navigator.mediaDevices;
  const proto = window.MediaDevices && MediaDevices.prototype;
  if (!md || !proto || !proto.getUserMedia) return;

  const origGetUserMedia = proto.getUserMedia;
  const origEnumerateDevices = proto.enumerateDevices;

  const VIRTUAL_ID = "interpreter-virtual-mic";
  const VIRTUAL_LABEL = "Interpreter (voz traducida)";
  const JITTER_S = 0.06; // colchón al empezar a sonar, para absorber variaciones de llegada
  const MAX_LAG_S = 3; // si el audio acumula más retraso que esto, se descarta lo que llegue (no se reproduce tarde)

  let ctx = null;
  let dest = null;
  let playHead = 0;
  const active = new Set();
  const live = new Set(); // pistas entregadas a la página que siguen vivas: sin ellas, no se reproduce nada
  const state = { connected: false, chunks: 0, dropped: 0 };

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
    if (!live.size || !ctx || ctx.state !== "running") { state.dropped++; return; } // nadie escucha en esta pestaña
    const now = ctx.currentTime;
    if (playHead - now > MAX_LAG_S) { state.dropped++; return; } // demasiado atrasado: se descarta
    const f32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
    const buf = ctx.createBuffer(1, f32.length, rate);
    buf.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(dest);
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

  function validPcm(d) {
    return d.buffer instanceof ArrayBuffer && d.buffer.byteLength > 0 && d.buffer.byteLength % 2 === 0 &&
      d.buffer.byteLength <= (1 << 20) && Number.isFinite(d.rate) && d.rate >= 8000 && d.rate <= 96000;
  }

  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (!d || d.__interpreter !== 1) return;
    try {
      if (d.type === "pcm" && validPcm(d)) enqueue(d.rate, new Int16Array(d.buffer));
      else if (d.type === "flush") flush();
      else if (d.type === "status") state.connected = !!d.connected;
    } catch (_) { /* un mensaje mal formado no debe romper la página */ }
  });

  // Reanuda el audio con cualquier gesto del usuario (las políticas de autoplay lo pueden suspender).
  for (const evName of ["click", "keydown", "touchstart"]) {
    window.addEventListener(evName, () => { if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {}); }, true);
  }

  function makeVirtualTrack() {
    const track = ensureStream().getAudioTracks()[0].clone(); // clon: parar uno no apaga el origen
    const release = () => live.delete(track);
    live.add(track);
    const stop = track.stop.bind(track);
    track.stop = () => { release(); stop(); };
    track.addEventListener("ended", release);
    const settings = track.getSettings.bind(track);
    // Un micrófono real sin procesado: Teams no debe asumir que ya se aplicó cancelación de eco o ruido.
    track.getSettings = () => ({
      ...settings(), deviceId: VIRTUAL_ID, groupId: VIRTUAL_ID,
      echoCancellation: false, noiseSuppression: false, autoGainControl: false,
    });
    try { Object.defineProperty(track, "label", { value: VIRTUAL_LABEL }); } catch (_) { /* solo lectura */ }
    return track;
  }

  async function virtualGetUserMedia(constraints) {
    if (!constraints || !constraints.audio) return origGetUserMedia.call(md, constraints);
    const audioTrack = makeVirtualTrack(); // primero el audio: si la cámara falla no queda una pista huérfana
    const out = new MediaStream();
    if (constraints.video) { // la cámara real se deja pasar; solo se sustituye el audio
      try {
        const v = await origGetUserMedia.call(md, { video: constraints.video });
        v.getVideoTracks().forEach((t) => out.addTrack(t));
      } catch (e) {
        audioTrack.stop();
        throw e;
      }
    }
    out.addTrack(audioTrack);
    return out;
  }

  // Se parchea el PROTOTIPO (también cubre MediaDevices.prototype.getUserMedia.call(...)) y la API antigua.
  Object.defineProperty(proto, "getUserMedia", { value: virtualGetUserMedia, configurable: true, writable: true });
  const legacy = function (constraints, ok, fail) {
    virtualGetUserMedia(constraints).then(ok, fail);
  };
  for (const name of ["getUserMedia", "webkitGetUserMedia"]) {
    try { Object.defineProperty(navigator, name, { value: legacy, configurable: true, writable: true }); } catch (_) { /* no existe */ }
  }

  if (origEnumerateDevices) {
    Object.defineProperty(proto, "enumerateDevices", {
      configurable: true,
      writable: true,
      value: async function () {
        const list = await origEnumerateDevices.call(md);
        const virtual = {
          deviceId: VIRTUAL_ID,
          kind: "audioinput",
          label: VIRTUAL_LABEL,
          groupId: VIRTUAL_ID,
          toJSON() { return { deviceId: VIRTUAL_ID, kind: "audioinput", label: VIRTUAL_LABEL, groupId: VIRTUAL_ID }; },
        };
        Object.setPrototypeOf(virtual, MediaDeviceInfo.prototype);
        return [virtual, ...list];
      },
    });
  }

  // Diagnóstico desde la consola de la página: window.__interpreterVirtualMicInfo()
  window.__interpreterVirtualMicInfo = () => ({
    conectado: state.connected,
    trozosRecibidos: state.chunks,
    trozosDescartados: state.dropped,
    pistasVivas: live.size,
    audioContext: ctx ? ctx.state : "sin crear",
  });
})();
