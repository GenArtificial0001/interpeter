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
