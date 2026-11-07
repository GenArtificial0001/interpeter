"""Pruebas de la ventana en modo offscreen, con el motor real pero backends y audio falsos."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import numpy as np  # noqa: E402
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication  # noqa: E402

from interpreter.engine import Engine, EngineState  # noqa: E402
from interpreter.messages import SpeechSegment  # noqa: E402
from interpreter.stages.fakes import FakeMT, FakeSTT, FakeTTS  # noqa: E402
from interpreter.ui.window import MainWindow  # noqa: E402


class _Cap:
    def __init__(self, device, segmenter, on_segment, on_level):
        _Cap.last = self
        self.on_segment, self.on_level = on_segment, on_level

    def start(self): ...
    def stop(self): ...


class _Ply:
    def __init__(self, device, get_audio):
        self.get_audio = get_audio

    def start(self): ...
    def stop(self): ...


def _builder(config, events):
    return Engine(config, events, lambda c: (FakeSTT(), FakeMT(), FakeTTS(chunks=1)), _Cap, _Ply)


@pytest.fixture
def settings(tmp_path):
    """Ajustes aislados: las pruebas nunca tocan los del usuario."""
    return QSettings(str(tmp_path / "ajustes.ini"), QSettings.Format.IniFormat)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_ventana_carga_y_queda_lista(app, settings):
    win = MainWindow(_builder, settings=settings)
    assert pump(app, lambda: win.btn_toggle.isEnabled())
    assert win.btn_toggle.isEnabled()
    assert "Listo" in win.state_pill.text()
    win.close()


def test_iniciar_detener_y_transcripcion(app, settings):
    win = MainWindow(_builder, settings=settings)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.cmb_source.setCurrentIndex(win.cmb_source.findData("es"))
    win.cmb_target.setCurrentIndex(win.cmb_target.findData("en"))
    win.btn_toggle.click()
    assert win.engine.state is EngineState.RUNNING
    assert win.btn_toggle.text() == "Detener"
    assert not win.cmb_source.isEnabled()  # no se cambia el idioma de entrada en marcha
    _Cap.last.on_segment(SpeechSegment(np.zeros(16000, dtype=np.float32)))
    assert pump(app, lambda: "hello world" in win.log.toPlainText(), 3)
    assert "hola mundo" in win.log.toPlainText()
    win.btn_toggle.click()
    assert win.engine.state is EngineState.READY
    assert win.btn_toggle.text() == "Iniciar interpretación"
    win.close()


def test_mismo_idioma_muestra_aviso(app, settings):
    win = MainWindow(_builder, settings=settings)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.cmb_source.setCurrentIndex(win.cmb_source.findData("en"))
    win.cmb_target.setCurrentIndex(win.cmb_target.findData("en"))
    win.btn_toggle.click()
    assert win.engine.state is EngineState.READY
    assert not win.lbl_warning.isHidden()
    win.close()


def test_nivel_del_microfono_mueve_el_medidor(app, settings):
    win = MainWindow(_builder, settings=settings)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win._on_level(0.2)
    win._tick_meter()
    assert win.level_bar.value() > 50
    win.close()
