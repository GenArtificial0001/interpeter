import logging
import sys

from interpreter import __main__ as entry


def test_sin_consola_redirige_stdout_y_stderr_a_un_archivo(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        logfile = entry.setup_logging(tmp_path)
        assert sys.stdout is not None and sys.stderr is not None
        print("hola desde stdout")  # no debe lanzar
        sys.stderr.write("hola desde stderr\n")
        logging.getLogger("interpreter").info("mensaje de prueba")
        for h in root.handlers:
            h.flush()
        assert "mensaje de prueba" in logfile.read_text(encoding="utf-8")
        assert "hola desde stderr" in (tmp_path / "logs" / "console.log").read_text(encoding="utf-8")
    finally:
        for h in root.handlers[:]:
            h.close()
            root.removeHandler(h)
        for h in saved[0]:
            root.addHandler(h)
        root.setLevel(saved[1])


def test_excepcion_no_controlada_queda_en_el_log(monkeypatch, tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    try:
        logfile = entry.setup_logging(tmp_path)
        try:
            raise ValueError("fallo de prueba")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        for h in root.handlers:
            h.flush()
        text = logfile.read_text(encoding="utf-8")
        assert "Excepción no controlada" in text and "fallo de prueba" in text
    finally:
        for h in root.handlers[:]:
            h.close()
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)


def test_shutdown_cierra_el_motor_y_no_deja_un_temporizador_que_mate_un_cierre_sano(monkeypatch):
    llamadas = []
    temporizadores = []

    class Motor:
        def close(self):
            llamadas.append("cerrado")

    class FakeTimer:
        def __init__(self, seg, fn, args=()):
            self.seg, self.fn, self.args, self.daemon, self.iniciado = seg, fn, args, False, False
            temporizadores.append(self)

        def start(self):
            self.iniciado = True

    monkeypatch.setattr(entry.threading, "Timer", FakeTimer)
    entry.shutdown(Motor(), 0, grace_s=3.0)
    assert llamadas == ["cerrado"]
    t = temporizadores[0]
    assert t.iniciado and t.daemon and t.seg == 3.0 and t.fn is entry.os._exit  # seguro por si algo se cuelga


def test_shutdown_sigue_aunque_el_motor_falle(monkeypatch):
    class Motor:
        def close(self):
            raise RuntimeError("fallo al cerrar")

    class FakeTimer:
        def __init__(self, *a, **k):
            self.daemon = False

        def start(self): ...

    monkeypatch.setattr(entry.threading, "Timer", FakeTimer)
    entry.shutdown(Motor())  # no debe lanzar


def test_los_argumentos_eligen_la_vista_al_arrancar():
    assert entry.ui_mode_from_args(["--orb"]) == "orb"
    assert entry.ui_mode_from_args(["--clasica"]) == "classic"
    assert entry.ui_mode_from_args([]) is None
    assert entry.ui_mode_from_args(["--otra-cosa"]) is None
    assert entry.ui_mode_from_args(["--orb", "--clasica"]) == "orb"  # si vienen los dos, manda el orbe
