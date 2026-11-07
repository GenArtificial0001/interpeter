import json
import socket
import time

import numpy as np
import pytest

from interpreter.audio.webmic import (
    PROTOCOL_VERSION,
    WebMicPlayer,
    WebMicServer,
    decode_pcm,
    encode_pcm,
    origin_allowed,
)
from interpreter.messages import AudioChunk

websockets = pytest.importorskip("websockets")
from websockets.sync.client import connect  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    srv = WebMicServer(port=free_port())
    srv.start()
    yield srv
    srv.stop()


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def url(srv):
    return f"ws://127.0.0.1:{srv.port}"


# -- origen y formato ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin,esperado",
    [
        ("https://teams.microsoft.com", True),
        ("https://teams.cloud.microsoft", True),
        ("https://meet.google.com", True),
        ("https://us04web.zoom.us", True),
        ("chrome-extension://abcdefghijklmnop", True),
        ("http://localhost:8000", True),
        (None, True),
        ("https://evil.com", False),
        ("https://teams.microsoft.com.evil.com", False),
        ("https://evilteams.microsoft.com.attacker.io", False),
        ("https://notzoom.us", False),
        ("file://", False),
    ],
)
def test_origenes_permitidos(origin, esperado):
    assert origin_allowed(origin) is esperado


def test_codificacion_pcm_ida_y_vuelta():
    x = np.array([0.0, 0.5, -0.5, 1.0, -1.0, 2.0, -3.0], dtype=np.float32)  # 2.0 y -3.0 se recortan
    rate, y = decode_pcm(encode_pcm(x, 24000))
    assert rate == 24000
    assert np.allclose(y, np.clip(x, -1, 1), atol=1e-3)


# -- servidor ---------------------------------------------------------------------------------------------


def test_el_cliente_recibe_el_saludo_y_el_audio(server):
    with connect(url(server), origin="https://teams.microsoft.com") as ws:
        hola = json.loads(ws.recv(timeout=3))
        assert hola == {"type": "hello", "version": PROTOCOL_VERSION}
        assert wait_for(lambda: server.clients == 1)
        tono = (0.3 * np.sin(2 * np.pi * 440 * np.arange(2400) / 24000)).astype(np.float32)
        assert server.push(tono, 24000) == 1
        rate, rx = decode_pcm(ws.recv(timeout=3))
        assert rate == 24000 and len(rx) == 2400 and np.allclose(rx, tono, atol=1e-3)


def test_flush_llega_como_mensaje_de_texto(server):
    with connect(url(server)) as ws:
        ws.recv(timeout=3)
        assert wait_for(lambda: server.clients == 1)
        server.flush()
        assert json.loads(ws.recv(timeout=3)) == {"type": "flush"}


def test_sin_clientes_push_devuelve_cero(server):
    assert server.push(np.zeros(100, dtype=np.float32), 24000) == 0


def test_se_reparte_a_varios_clientes_y_se_cuentan(server):
    cambios = []
    server.on_clients_changed = cambios.append
    with connect(url(server)) as a, connect(url(server)) as b:
        a.recv(timeout=3)
        b.recv(timeout=3)
        assert wait_for(lambda: server.clients == 2)
        assert server.push(np.ones(10, dtype=np.float32) * 0.1, 16000) == 2
        assert decode_pcm(a.recv(timeout=3))[0] == 16000 and decode_pcm(b.recv(timeout=3))[0] == 16000
    assert wait_for(lambda: server.clients == 0)
    assert max(cambios) == 2 and cambios[-1] == 0


def test_un_origen_no_permitido_es_rechazado(server):
    from websockets.exceptions import InvalidStatus

    with pytest.raises(InvalidStatus) as exc:
        connect(url(server), origin="https://evil.com")
    assert exc.value.response.status_code == 403
    assert server.clients == 0


def test_el_puerto_ocupado_da_un_error_claro():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        srv = WebMicServer(port=s.getsockname()[1])
        with pytest.raises(RuntimeError, match="puerto"):
            srv.start()


def test_se_puede_parar_y_volver_a_arrancar():
    srv = WebMicServer(port=free_port())
    srv.start()
    assert srv.running
    srv.stop()
    assert not srv.running
    srv.start()
    with connect(url(srv)) as ws:
        assert json.loads(ws.recv(timeout=3))["type"] == "hello"
    srv.stop()


# -- reproductor del navegador ------------------------------------------------------------------------------


class FakeServer:
    def __init__(self, entregados=1):
        self.entregados, self.pushes, self.flushes = entregados, [], 0

    def push(self, audio, rate):
        self.pushes.append((len(audio), rate))
        return self.entregados

    def flush(self):
        self.flushes += 1


def feeder(chunks):
    items = list(chunks)

    def get(timeout):
        if items:
            return items.pop(0)
        time.sleep(min(timeout, 0.02))
        return None

    return get


def chunk(n=2400, rate=24000):
    return AudioChunk(np.zeros(n, dtype=np.float32), rate)


def test_el_reproductor_del_navegador_envia_cada_trozo_y_vacia_al_parar():
    srv = FakeServer()
    p = WebMicPlayer(srv, feeder([chunk(), chunk(4800)]))
    p.start()
    assert wait_for(lambda: p.played_chunks == 2)
    p.stop()
    assert srv.pushes == [(2400, 24000), (4800, 24000)] and srv.flushes == 1
    assert p.busy_until == 0.0  # no suena en la sala: no hay eco que evitar


def test_avisa_una_sola_vez_si_no_hay_navegador_conectado():
    errores = []
    srv = FakeServer(entregados=0)
    p = WebMicPlayer(srv, feeder([chunk(), chunk(), chunk()]), on_error=errores.append)
    p.start()
    assert wait_for(lambda: p.undelivered == 3)
    p.stop()
    assert len(errores) == 1 and "navegador" in errores[0]


def test_vuelve_a_avisar_tras_reconectar_y_perder_otra_vez():
    errores, srv = [], FakeServer(entregados=0)
    p = WebMicPlayer(srv, feeder([chunk()]), on_error=errores.append)
    p.start()
    assert wait_for(lambda: len(errores) == 1)
    srv.entregados = 1
    p._get_audio = feeder([chunk()])
    assert wait_for(lambda: p.played_chunks == 1)
    srv.entregados = 0
    p._get_audio = feeder([chunk()])
    assert wait_for(lambda: len(errores) == 2)
    p.stop()
