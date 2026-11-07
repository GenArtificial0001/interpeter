"""Micrófono virtual para el navegador: envía el audio traducido a la extensión de Teams/Meet web.

Un micrófono virtual en Windows es un driver (exige administrador). Dentro del navegador no hace falta:
una extensión presenta a la página un ``MediaStream`` como si fuera el micrófono, y este servidor le
entrega el audio por un WebSocket local (solo ``127.0.0.1``).

Protocolo (versión 1)
- Servidor -> extensión, **binario**: ``uint32`` little-endian con la frecuencia de muestreo, seguido de
  muestras ``int16`` mono little-endian.
- Servidor -> extensión, **texto JSON**: ``{"type": "hello", "version": 1}`` al conectar y
  ``{"type": "flush"}`` cuando hay que vaciar lo pendiente (se interrumpió la interpretación).
- La extensión no envía nada que el servidor use (solo se acepta y se ignora lo que mande).

Seguridad: solo se aceptan conexiones cuyo ``Origin`` sea de una página de reuniones conocida o de una
extensión; cualquier otra página web abierta en el navegador es rechazada.
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

# Páginas de reuniones donde se inyecta la extensión (los subdominios se aceptan con el sufijo).
ALLOWED_ORIGIN_SUFFIXES = (
    "teams.microsoft.com",
    "teams.cloud.microsoft",
    "teams.live.com",
    "meet.google.com",
    "zoom.us",
    "localhost",  # pruebas locales
    "127.0.0.1",
)
ALLOWED_ORIGIN_SCHEMES = ("chrome-extension://", "moz-extension://")


def origin_allowed(origin: str | None) -> bool:
    """¿Puede esta página (o extensión) recibir el audio? Un origen ausente (no navegador) se acepta."""
    if not origin:
        return True  # clientes que no son navegadores (pruebas, herramientas locales)
    if origin.startswith(ALLOWED_ORIGIN_SCHEMES):
        return True
    for scheme in ("https://", "http://"):
        if origin.startswith(scheme):
            host = origin[len(scheme) :].split("/")[0].split(":")[0].lower()
            return any(host == s or host.endswith("." + s) for s in ALLOWED_ORIGIN_SUFFIXES)
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
    """Servidor WebSocket local en su propio hilo. ``push()`` es seguro desde cualquier hilo."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        host: str = HOST,
        on_clients_changed: Callable[[int], None] | None = None,
    ) -> None:
        self.port = port
        self.host = host
        self.on_clients_changed = on_clients_changed
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._clients: set[Any] = set()
        self._started = threading.Event()
        self._error: BaseException | None = None
        self.frames_sent = 0

    # -- ciclo de vida -----------------------------------------------------------------------------

    @property
    def clients(self) -> int:
        return len(self._clients)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._error is None

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
            raise RuntimeError(f"No se pudo abrir el puerto {self.port}: {self._error}") from self._error

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            with contextlib.suppress(Exception):
                fut.result(timeout=3)
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self._loop = None

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:  # noqa: BLE001 - se informa a start()
            self._error = exc
            self._started.set()
            log.exception("Fallo del servidor del micrófono virtual")
        finally:
            loop.close()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve

        async def process_request(connection: Any, request: Any) -> Any:
            origin = request.headers.get("Origin")
            if not origin_allowed(origin):
                log.warning("Conexión rechazada: origen no permitido (%s)", origin)
                return connection.respond(403, "Origen no permitido\n")
            return None

        self._server = await serve(
            self._handler, self.host, self.port, process_request=process_request, max_size=2**16
        )
        self._started.set()
        log.info("Micrófono virtual escuchando en ws://%s:%s", self.host, self.port)
        await self._server.wait_closed()

    async def _shutdown(self) -> None:
        for ws in list(self._clients):
            with contextlib.suppress(Exception):
                await ws.close()
        if self._server is not None:
            self._server.close()

    async def _handler(self, ws: Any) -> None:
        self._clients.add(ws)
        self._notify()
        try:
            await ws.send(json.dumps({"type": "hello", "version": PROTOCOL_VERSION}))
            async for _ in ws:  # lo que envíe la extensión se ignora
                pass
        finally:
            self._clients.discard(ws)
            self._notify()

    def _notify(self) -> None:
        if self.on_clients_changed is not None:
            try:
                self.on_clients_changed(len(self._clients))
            except Exception:  # noqa: BLE001 - un fallo de la interfaz no debe tumbar el servidor
                log.exception("Error al notificar clientes")

    # -- envío -------------------------------------------------------------------------------------

    def push(self, audio: np.ndarray, sample_rate: int) -> int:
        """Envía un trozo a todos los clientes. Devuelve cuántos lo recibirán (0 = nadie conectado)."""
        return self._broadcast(encode_pcm(audio, sample_rate))

    def flush(self) -> int:
        """Pide a las extensiones que descarten lo pendiente (p. ej. al detener)."""
        return self._broadcast(json.dumps({"type": "flush"}))

    def _broadcast(self, message: bytes | str) -> int:
        loop = self._loop
        clients = list(self._clients)
        if loop is None or not loop.is_running() or not clients:
            return 0

        async def send_all() -> None:
            for ws in clients:
                with contextlib.suppress(Exception):
                    await ws.send(message)

        asyncio.run_coroutine_threadsafe(send_all(), loop)
        self.frames_sent += 1
        return len(clients)


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
        self.undelivered = 0  # trozos que se generaron sin ningún navegador conectado
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
                        "La traducción está lista pero no hay ningún navegador conectado: abre Teams web "
                        "con la extensión de Interpreter instalada."
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
