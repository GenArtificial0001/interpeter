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
    def __init__(self, device, get_audio, on_error=None):
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


# -- nuevas funciones: pausa, bucle y muestra de voz ----------------------------------------------


def test_la_pausa_elegida_llega_al_motor_y_se_recuerda(app, settings):
    win = MainWindow(_builder, settings=settings)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.cmb_pause.setCurrentIndex(win.cmb_pause.findData(320))
    win.btn_toggle.click()
    assert win.engine.config.pause_ms == 320
    win.btn_toggle.click()
    win.close()
    win2 = MainWindow(_builder, load_on_start=False, settings=settings)
    assert win2.cmb_pause.currentData() == 320
    win2.close()


def test_prueba_del_bucle_muestra_el_resultado(app, settings):
    from interpreter.audio.calibrate import LoopbackReport

    llamadas = []

    def runner(out, inp):
        llamadas.append((out, inp))
        return LoopbackReport(-18.0, -70.0, 52.0, 0.2, False, "ok", "Nivel correcto para Teams.")

    win = MainWindow(_builder, settings=settings, loop_runner=runner)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.btn_loop.click()
    assert pump(app, lambda: "Nivel correcto" in win.lbl_loop.text())
    assert win.btn_loop.isEnabled() and len(llamadas) == 1
    win.close()


def test_prueba_del_bucle_error_no_deja_el_boton_bloqueado(app, settings):
    def runner(out, inp):
        raise OSError("dispositivo no disponible")

    win = MainWindow(_builder, settings=settings, loop_runner=runner)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.btn_loop.click()
    assert pump(app, lambda: win.btn_loop.isEnabled())
    assert "no disponible" in win.lbl_warning.text()
    win.close()


def test_grabar_muestra_de_voz_guarda_y_fija_la_referencia(app, settings, tmp_path, monkeypatch):
    from interpreter.ui.voice_dialog import VoiceSampleDialog

    monkeypatch.setenv("INTERPRETER_HOME", str(tmp_path))
    rng = np.random.default_rng(0)
    t = np.arange(16000 * 25) / 16000
    voz = (0.1 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 1.5 * t) > -0.3)).astype(np.float32)
    voz += rng.normal(0, 0.0003, voz.size).astype(np.float32)

    def fake_record(device, seconds, on_level=None):
        return voz, 16000

    dlg = VoiceSampleDialog(None, None, tmp_path / "voices" / "mi_voz.wav", recorder=fake_record, seconds=1.0)
    dlg.btn_record.click()
    assert pump(app, lambda: dlg.report is not None)
    assert dlg.report.ok and dlg.btn_use.isEnabled()
    dlg.btn_use.click()
    assert dlg.saved_path is not None and dlg.saved_path.is_file()

    win = MainWindow(_builder, settings=settings, recorder=fake_record)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win._set_reference(str(dlg.saved_path))
    assert win.engine.config.reference_wav == str(dlg.saved_path)
    assert "mi_voz.wav" in win.lbl_ref.text()
    assert not win.lbl_ref.isHidden()
    win.close()


# -- detección de dispositivos conectados con la app abierta -----------------------------------------


def _dev(i, name):
    from interpreter.audio.devices import AudioDevice

    return AudioDevice(i, name, "Windows WASAPI", 1, 1, 48000.0)


def _scanner(estado):
    """Escáner falso: ``estado['in']`` y ``estado['out']`` son las listas actuales; cuenta las llamadas."""

    def scan():
        estado["llamadas"] = estado.get("llamadas", 0) + 1
        return list(estado["in"]), list(estado["out"])

    return scan


def test_detecta_un_auricular_conectado_con_la_app_abierta(app, settings):
    estado = {"in": [_dev(1, "Micrófono")], "out": [_dev(2, "Altavoces")]}
    win = MainWindow(_builder, settings=settings, device_scanner=_scanner(estado))
    pump(app, lambda: win.btn_toggle.isEnabled())
    assert win.cmb_input.count() == 1 and win.cmb_output.count() == 1

    estado["in"].append(_dev(3, "Headset (WL5024)"))
    estado["out"].append(_dev(4, "Headphones (WL5024)"))
    win._poll_devices()

    assert [win.cmb_input.itemText(i) for i in range(win.cmb_input.count())] == [
        "Micrófono",
        "Headset (WL5024)",
    ]
    assert win.cmb_output.findText("Headphones (WL5024)") >= 0
    assert "Dispositivo conectado: Headset (WL5024)" in win.log.toPlainText()
    win.close()


def test_conserva_la_seleccion_al_cambiar_la_lista(app, settings):
    estado = {"in": [_dev(1, "Micrófono"), _dev(3, "Headset (WL5024)")], "out": [_dev(2, "Altavoces")]}
    win = MainWindow(_builder, settings=settings, device_scanner=_scanner(estado))
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.cmb_input.setCurrentIndex(win.cmb_input.findText("Headset (WL5024)"))
    estado["out"].append(_dev(4, "Headphones (WL5024)"))
    win._poll_devices()
    assert win.cmb_input.currentText() == "Headset (WL5024)"
    win.close()


def test_aviso_si_se_desconecta_el_dispositivo_elegido(app, settings):
    estado = {"in": [_dev(1, "Micrófono"), _dev(3, "Headset (WL5024)")], "out": [_dev(2, "Altavoces")]}
    win = MainWindow(_builder, settings=settings, device_scanner=_scanner(estado))
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.cmb_input.setCurrentIndex(win.cmb_input.findText("Headset (WL5024)"))
    estado["in"] = [_dev(1, "Micrófono")]
    win._poll_devices()
    assert win.cmb_input.currentText() == "Micrófono"
    assert not win.lbl_warning.isHidden() and "Headset (WL5024)" in win.lbl_warning.text()
    assert "Dispositivo desconectado: Headset (WL5024)" in win.log.toPlainText()
    win.close()


def test_no_refresca_los_dispositivos_mientras_interpreta(app, settings):
    estado = {"in": [_dev(1, "Micrófono")], "out": [_dev(2, "Altavoces")]}
    win = MainWindow(_builder, settings=settings, device_scanner=_scanner(estado))
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.btn_toggle.click()
    assert win.engine.state is EngineState.RUNNING
    llamadas = estado["llamadas"]
    estado["in"].append(_dev(3, "Headset nuevo"))
    win._poll_devices()
    assert (
        estado["llamadas"] == llamadas and win.cmb_input.count() == 1
    )  # reiniciar PortAudio rompería el stream
    win.btn_toggle.click()
    win._poll_devices()
    assert win.cmb_input.count() == 2
    win.close()


def test_ventana_chica_usa_scroll_y_no_aplasta_el_contenido(app, settings):
    win = MainWindow(_builder, settings=settings)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.resize(700, 500)
    win.show()  # el área con scroll solo calcula su rango al mostrarse
    app.processEvents()
    contenido = win.centralWidget().widget()
    assert contenido.minimumSizeHint().height() > 500  # no cabe: debe desplazarse
    assert win.centralWidget().verticalScrollBar().maximum() > 0
    win.close()


# -- el silencio no puede pasar desapercibido ---------------------------------------------------------


def test_silenciar_cambia_el_texto_la_pastilla_y_avisa(app, settings):
    win = MainWindow(_builder, settings=settings)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.btn_toggle.click()
    assert "Interpretando" in win.state_pill.text()
    win.btn_mute.click()
    assert win.engine.muted and "SILENCIADO" in win.btn_mute.text()
    assert "Silenciado" in win.state_pill.text()
    assert "silenciado" in win.log.toPlainText().lower()
    win.btn_mute.click()
    assert not win.engine.muted and win.btn_mute.text() == "Silenciar"
    assert "Interpretando" in win.state_pill.text()
    win.btn_toggle.click()
    win.close()


def test_una_frase_ignorada_por_silencio_se_ve_en_la_transcripcion(app, settings):
    win = MainWindow(_builder, settings=settings)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.btn_toggle.click()
    win.btn_mute.click()
    _Cap.last.on_segment(SpeechSegment(np.zeros(16000, dtype=np.float32)))
    assert pump(app, lambda: "ignorada" in win.log.toPlainText())
    assert "reanudar" in win.log.toPlainText()
    win.btn_toggle.click()
    win.close()


def test_iniciar_estando_silenciado_muestra_un_aviso(app, settings):
    win = MainWindow(_builder, settings=settings)
    pump(app, lambda: win.btn_toggle.isEnabled())
    win.btn_mute.click()
    win.btn_toggle.click()
    assert not win.lbl_warning.isHidden() and "SILENCIADO" in win.lbl_warning.text()
    win.btn_toggle.click()
    win.close()
