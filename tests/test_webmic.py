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


EXT_ID = "lodhkanpadafbgdfgelmeaadelgongkg"
EXT_ORIGIN = f"chrome-extension://{EXT_ID}"


@pytest.fixture
def server():
    srv = WebMicServer(port=free_port(), dev=True)  # dev: acepta clientes de prueba sin Origin
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
        (EXT_ORIGIN, True),
        (f"{EXT_ORIGIN}/", True),
        ("chrome-extension://otraextensioncualquiera", False),  # otra extensión instalada: no
        ("https://teams.microsoft.com", False),  # las páginas nunca se conectan: solo la extensión
        ("https://meet.google.com", False),
        ("http://localhost:8000", False),
        ("http://x.localhost:18080", False),
        ("null", False),
        ("", False),
        (None, False),
        ("https://evil.com", False),
        ("https://teams.microsoft.com.evil.com", False),
        ("file://", False),
    ],
)
def test_origenes_en_produccion_solo_la_extension(origin, esperado):
    assert origin_allowed(origin) is esperado


@pytest.mark.parametrize(
    "origin,esperado",
    [
        (None, True),
        ("http://localhost:8000", True),
        ("http://127.0.0.1:1234", True),
        ("https://localhost", True),
        ("chrome-extension://cualquiera", True),
        ("https://evil.com", False),
        ("http://x.localhost:18080", False),
        ("http://localhost.evil.com", False),
    ],
)
def test_origenes_en_modo_desarrollo(origin, esperado):
    assert origin_allowed(origin, dev=True) is esperado


def test_codificacion_pcm_ida_y_vuelta():
    x = np.array([0.0, 0.5, -0.5, 1.0, -1.0, 2.0, -3.0], dtype=np.float32)  # 2.0 y -3.0 se recortan
    rate, y = decode_pcm(encode_pcm(x, 24000))
    assert rate == 24000
    assert np.allclose(y, np.clip(x, -1, 1), atol=1e-3)


# -- servidor ---------------------------------------------------------------------------------------------


def test_el_cliente_recibe_el_saludo_y_el_audio(server):
    with connect(url(server), origin=EXT_ORIGIN) as ws:
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
        with pytest.raises(RuntimeError, match="ocupado por otro programa"):
            srv.start()


def test_se_puede_parar_y_volver_a_arrancar():
    srv = WebMicServer(port=free_port(), dev=True)
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


# -- endurecimiento ----------------------------------------------------------------------------------------


def _raw_client(port, rcvbuf=512):
    """Cliente crudo que hace el handshake y NUNCA vuelve a leer (simula una pestaña colgada)."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    s.connect(("127.0.0.1", port))
    s.sendall(
        b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    s.settimeout(3)
    assert b"101" in s.recv(512)
    return s


def test_produccion_rechaza_al_que_no_es_la_extension():
    from websockets.exceptions import InvalidStatus

    srv = WebMicServer(port=free_port())  # dev=False
    srv.start()
    try:
        for origin in ("https://teams.microsoft.com", "chrome-extension://otraextensioncualquiera", None):
            with pytest.raises(InvalidStatus) as exc:
                connect(url(srv), origin=origin)
            assert exc.value.response.status_code == 403
        with connect(url(srv), origin=EXT_ORIGIN) as ws:  # la extensión propia sí
            assert json.loads(ws.recv(timeout=3))["type"] == "hello"
    finally:
        srv.stop()


def test_un_cliente_que_no_lee_es_expulsado_y_no_atasca_a_los_demas(server):
    server.send_timeout_s = 0.3
    lento = _raw_client(server.port)
    try:
        with connect(url(server), origin=EXT_ORIGIN) as sano:
            sano.recv(timeout=3)
            assert wait_for(lambda: server.clients == 2)
            grande = 0.1 * np.ones(30000, dtype=np.float32)
            for _ in range(60):
                server.push(grande, 24000)
            recibidos = 0
            t_fin = time.time() + 8
            while time.time() < t_fin and recibidos < 60:
                try:
                    sano.recv(timeout=0.5)
                    recibidos += 1
                except TimeoutError:
                    break
            assert recibidos >= 55  # el cliente sano recibió casi todo pese al atascado
            assert wait_for(lambda: server.clients == 1) and server.dropped_slow >= 1
    finally:
        lento.close()


def test_el_tope_de_conexiones_responde_503():
    from websockets.exceptions import InvalidStatus

    srv = WebMicServer(port=free_port(), dev=True, max_clients=2)
    srv.start()
    try:
        with connect(url(srv)) as a, connect(url(srv)) as b:
            a.recv(timeout=3)
            b.recv(timeout=3)
            assert wait_for(lambda: srv.clients == 2)
            with pytest.raises(InvalidStatus) as exc:
                connect(url(srv))
            assert exc.value.response.status_code == 503
    finally:
        srv.stop()


def test_stop_no_se_cuelga_con_un_cliente_que_no_responde_y_libera_el_puerto():
    srv = WebMicServer(port=free_port(), dev=True)
    srv.start()
    muerto = _raw_client(srv.port)
    try:
        assert wait_for(lambda: srv.clients == 1)
        t0 = time.time()
        srv.stop()
        assert time.time() - t0 < 3.0
        with socket.socket() as s:  # el puerto quedó libre: no hay servidor zombi
            s.bind(("127.0.0.1", srv.port))
    finally:
        muerto.close()


def test_las_pestanas_deciden_si_hay_quien_reproduzca_el_audio(server):
    avisos = []
    server.on_clients_changed = avisos.append
    with connect(url(server), origin=EXT_ORIGIN) as ws:
        ws.recv(timeout=3)
        assert wait_for(lambda: server.clients == 1)
        assert server.receivers == 1  # sin informar se considera receptor (herramientas de prueba)
        ws.send(json.dumps({"type": "tabs", "n": 0}))
        assert wait_for(lambda: server.receivers == 0)
        assert server.clients == 1  # la extensión sigue conectada, pero nadie oiría el audio
        assert server.push(np.zeros(100, dtype=np.float32), 24000) == 0
        ws.send(json.dumps({"type": "tabs", "n": 2}))
        assert wait_for(lambda: server.receivers == 1)
        assert server.push(np.zeros(100, dtype=np.float32), 24000) == 1
    assert wait_for(lambda: avisos and avisos[-1] == 0)


def test_los_mensajes_mal_formados_se_ignoran(server):
    with connect(url(server), origin=EXT_ORIGIN) as ws:
        ws.recv(timeout=3)
        for malo in (
            "no es json",
            json.dumps({"type": "tabs", "n": -5}),
            json.dumps({"type": "tabs", "n": "x"}),
            json.dumps([1, 2]),
            json.dumps({"type": "otra"}),
        ):
            ws.send(malo)
        ws.send(b"\x00\x01\x02")
        time.sleep(0.2)
        assert server.clients == 1 and server.receivers == 1


def test_un_mensaje_enorme_cierra_la_conexion_sin_romper_el_servidor(server):
    from websockets.exceptions import ConnectionClosed

    with connect(url(server), origin=EXT_ORIGIN) as ws:
        ws.recv(timeout=3)
        ws.send("x" * 10000)
        with pytest.raises(ConnectionClosed):
            ws.recv(timeout=3)
    with connect(url(server), origin=EXT_ORIGIN) as ws2:  # el servidor sigue atendiendo
        assert json.loads(ws2.recv(timeout=3))["type"] == "hello"


def test_el_latido_mantiene_vivo_al_service_worker():
    srv = WebMicServer(port=free_port(), dev=True, heartbeat_s=0.1)
    srv.start()
    try:
        with connect(url(srv), origin=EXT_ORIGIN) as ws:
            assert json.loads(ws.recv(timeout=3))["type"] == "hello"
            assert json.loads(ws.recv(timeout=3)) == {"type": "ping"}
            assert json.loads(ws.recv(timeout=3)) == {"type": "ping"}
    finally:
        srv.stop()
