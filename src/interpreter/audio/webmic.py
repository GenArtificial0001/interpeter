"""Micrófono virtual para el navegador: envía el audio traducido a la extensión de Teams/Meet web.

Un micrófono virtual en Windows es un driver (exige administrador). Dentro del navegador no hace falta:
una extensión presenta a la página un ``MediaStream`` como si fuera el micrófono, y este servidor le
entrega el audio por un WebSocket local (solo ``127.0.0.1``).

Protocolo (versión 1)
- Servidor -> extensión, **binario**: ``uint32`` little-endian con la frecuencia de muestreo, seguido de
  muestras ``int16`` mono little-endian.
- Servidor -> extensión, **texto JSON**: ``{"type": "hello", "version": 1}`` al conectar,
  ``{"type": "flush"}`` cuando hay que vaciar lo pendiente (se interrumpió la interpretación) y
  ``{"type": "ping"}`` cada 20 s (mantiene vivo el service worker MV3, que se duerme a los 30 s).
- Extensión -> servidor, **texto JSON**: ``{"type": "tabs", "n": N}`` con las pestañas de reunión abiertas.

Seguridad: la conexión la abre el service worker de la extensión, cuyo ``Origin`` es
``chrome-extension://<ID>``; el ID está fijado por la clave del manifest y es el único origen aceptado (las
páginas web, incluida Teams, nunca se conectan). Hay tope de clientes y de tamaño de mensaje, y un cliente
que no lee es expulsado en medio segundo en lugar de atascar a los demás.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_PORT = 8765
HOST = "127.0.0.1"
HEARTBEAT_S = 20.0  # el service worker MV3 se duerme tras 30 s sin mensajes: este latido lo mantiene vivo
SEND_TIMEOUT_S = 0.5  # un cliente que tarda más en aceptar un trozo se considera atascado y se desconecta
MAX_CLIENTS = 8
MAX_MESSAGE_BYTES = 2**12

# Solo se conecta la extensión (su service worker), con el ID fijo que da la clave de su manifest.
EXTENSION_IDS = frozenset({"lodhkanpadafbgdfgelmeaadelgongkg"})
DEV_HOSTS = ("localhost", "127.0.0.1")


def origin_allowed(
    origin: str | None,
    extension_ids: frozenset[str] | set[str] = EXTENSION_IDS,
    dev: bool = False,
) -> bool:
    """¿Puede este origen recibir el audio?

    En producción solo la extensión de Interpreter (ID exacto). Los clientes sin ``Origin`` (herramientas
    que no son navegadores) y las páginas locales solo se aceptan en modo de desarrollo/pruebas.
    """
    if not origin:
        return dev
    if origin.startswith("chrome-extension://"):
        ext = origin[len("chrome-extension://") :].split("/")[0]
        return ext in extension_ids or dev
    if dev:
        for scheme in ("http://", "https://"):
            if origin.startswith(scheme):
                host = origin[len(scheme) :].split("/")[0].split(":")[0].lower()
                return host in DEV_HOSTS
    return False


def encode_pcm(audio: np.ndarray, sample_rate: int) -> bytes:
    """Cabecera de frecuencia + muestras int16 con recorte a [-1, 1]."""
    x = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2")
    return struct.pack("<I", int(sample_rate)) + pcm.tobytes()


def decode_pcm(frame: bytes) -> tuple[int, np.ndarray]:
    """Inversa de ``encode_pcm`` (para pruebas y diagnóstico)."""
    (rate,) = struct.unpack("<I", frame[:4])
    return rate, np.frombuffer(frame[4:], dtype="<i2").astype(np.float32) / 32767.0


class WebMicServer:
    """Servidor WebSocket local en su propio hilo. ``push()`` es seguro desde cualquier hilo.

    Cada cliente informa cuántas pestañas de reunión tiene abiertas (``{"type": "tabs", "n": N}``). Un cliente
    sin pestañas no cuenta como receptor: así Interpreter avisa en lugar de dar por entregado un audio que
    nadie oirá. Los clientes que no informan (herramientas de prueba) se consideran receptores.
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        host: str = HOST,
        on_clients_changed: Callable[[int], None] | None = None,
        dev: bool = False,
        max_clients: int = MAX_CLIENTS,
        heartbeat_s: float = HEARTBEAT_S,
        send_timeout_s: float = SEND_TIMEOUT_S,
        extension_ids: frozenset[str] | set[str] = EXTENSION_IDS,
    ) -> None:
        self.port = port
        self.host = host
        self.on_clients_changed = on_clients_changed
        self.dev = dev
        self.max_clients = max_clients
        self.heartbeat_s = heartbeat_s
        self.send_timeout_s = send_timeout_s
        self.extension_ids = extension_ids
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._clients: dict[Any, int | None] = {}  # conexión -> pestañas de reunión (None = no informa)
        self._started = threading.Event()
        self._error: BaseException | None = None
        self.frames_sent = 0
        self.dropped_slow = 0

    # -- estado ------------------------------------------------------------------------------------

    @property
    def clients(self) -> int:
        """Conexiones abiertas (extensiones), tengan o no pestañas de reunión."""
        return len(self._clients)

    @property
    def receivers(self) -> int:
        """Conexiones que reproducirán el audio (con al menos una pestaña, o que no informan)."""
        return sum(1 for tabs in self._clients.values() if tabs is None or tabs > 0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._error is None

    # -- ciclo de vida -----------------------------------------------------------------------------

    def start(self, timeout: float = 5.0) -> None:
        if self.running:
            return
        self._started.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="webmic-server", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout):
            raise RuntimeError("El servidor del micrófono virtual no arrancó a tiempo.")
        if self._error is not None:
            raise RuntimeError(
                f"El puerto {self.port} está ocupado por otro programa o no se puede abrir: ciérralo e "
                f"inténtalo de nuevo ({self._error})."
            ) from self._error

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            with contextlib.suppress(Exception):
                fut.result(timeout=2)
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        self._loop = None
        self._clients.clear()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:  # noqa: BLE001 - se informa a start()
            self._error = exc
            self._started.set()
            log.warning("El servidor del micrófono virtual no pudo arrancar: %s", exc)
        finally:
            loop.close()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve

        async def process_request(connection: Any, request: Any) -> Any:
            origin = request.headers.get("Origin")
            if not origin_allowed(origin, self.extension_ids, self.dev):
                log.warning("Conexión rechazada: origen no permitido (%s)", origin)
                return connection.respond(403, "Origen no permitido\n")
            if len(self._clients) >= self.max_clients:
                return connection.respond(503, "Demasiadas conexiones\n")
            return None

        self._server = await serve(
            self._handler,
            self.host,
            self.port,
            process_request=process_request,
            max_size=MAX_MESSAGE_BYTES,
            close_timeout=1,
            ping_interval=None,  # el latido es el propio mensaje JSON de _heartbeat
        )
        self._started.set()
        log.info("Micrófono virtual escuchando en ws://%s:%s", self.host, self.port)
        await self._server.wait_closed()

    async def _shutdown(self) -> None:
        for ws in list(self._clients):
            self._abort(ws)  # sin esperar el cierre limpio: un cliente colgado no debe bloquear
        if self._server is not None:
            self._server.close()

    @staticmethod
    def _abort(ws: Any) -> None:
        with contextlib.suppress(Exception):
            ws.transport.abort()

    async def _heartbeat(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_s)
            await ws.send(json.dumps({"type": "ping"}))

    async def _handler(self, ws: Any) -> None:
        from websockets.exceptions import ConnectionClosed

        self._clients[ws] = None
        self._notify()
        beat = asyncio.ensure_future(self._heartbeat(ws))
        try:
            await ws.send(json.dumps({"type": "hello", "version": PROTOCOL_VERSION}))
            async for message in ws:
                self._on_client_message(ws, message)
        except ConnectionClosed:
            pass
        finally:
            beat.cancel()
            self._clients.pop(ws, None)
            self._notify()

    def _on_client_message(self, ws: Any, message: Any) -> None:
        if not isinstance(message, str):
            return
        try:
            data = json.loads(message)
        except ValueError:
            return
        if isinstance(data, dict) and data.get("type") == "tabs":
            n = data.get("n")
            if isinstance(n, int) and 0 <= n <= 1000:
                self._clients[ws] = n
                self._notify()

    def _notify(self) -> None:
        if self.on_clients_changed is not None:
            try:
                self.on_clients_changed(self.receivers)
            except Exception:  # noqa: BLE001 - un fallo de la interfaz no debe tumbar el servidor
                log.exception("Error al notificar clientes")

    # -- envío -------------------------------------------------------------------------------------

    def push(self, audio: np.ndarray, sample_rate: int) -> int:
        """Envía un trozo a los receptores. Devuelve cuántos lo recibirán (0 = nadie lo oirá)."""
        return self._broadcast(encode_pcm(audio, sample_rate), only_receivers=True)

    def flush(self) -> int:
        """Pide a las extensiones que descarten lo pendiente (p. ej. al detener)."""
        return self._broadcast(json.dumps({"type": "flush"}), only_receivers=False)

    async def _send_one(self, ws: Any, message: bytes | str) -> None:
        try:
            await asyncio.wait_for(ws.send(message), timeout=self.send_timeout_s)
        except Exception:  # noqa: BLE001 - lento, cerrado o roto: se expulsa para no atascar a los demás
            self.dropped_slow += 1
            log.warning("Cliente lento o caído: se desconecta")
            self._abort(ws)

    def _broadcast(self, message: bytes | str, only_receivers: bool) -> int:
        loop = self._loop
        targets = [
            ws for ws, tabs in list(self._clients.items()) if not only_receivers or tabs is None or tabs > 0
        ]
        if loop is None or not loop.is_running() or not targets:
            return 0

        async def send_all() -> None:
            await asyncio.gather(*(self._send_one(ws, message) for ws in targets))

        asyncio.run_coroutine_threadsafe(send_all(), loop)  # en paralelo: un cliente lento no frena al resto
        self.frames_sent += 1
        return len(targets)


class WebMicPlayer:
    """Hace de ``Player``, pero envía cada trozo al micrófono virtual del navegador y no a un dispositivo.

    La salida no suena en la sala, así que no hay eco que evitar (``busy_until`` queda en 0).
    """

    def __init__(
        self,
        server: WebMicServer,
        get_audio: Callable[[float], object | None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._server = server
        self._get_audio = get_audio
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.played_chunks = 0
        self.undelivered = 0  # trozos que se generaron sin ningún navegador que los reproduzca
        self.busy_until = 0.0
        self._warned = False

    def _run(self) -> None:
        while not self._stop.is_set():
            chunk = self._get_audio(0.1)
            if chunk is None:
                continue
            delivered = self._server.push(chunk.audio, chunk.sample_rate)  # type: ignore[attr-defined]
            if delivered:
                self.played_chunks += 1
                self._warned = False
            else:
                self.undelivered += 1
                if not self._warned and self._on_error is not None:
                    self._warned = True  # un aviso por racha, no uno por trozo
                    self._on_error(
                        "La traducción está lista pero ninguna pestaña de Teams/Meet la recibe: abre la "
                        "reunión en el navegador con la extensión de Interpreter instalada."
                    )

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="webmic-player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
        self._server.flush()
