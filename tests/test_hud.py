"""El orbe (interfaz alternativa): transparencia, clics, animación y su unión con la ventana clásica."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from interpreter.engine import Engine, EngineState  # noqa: E402
from interpreter.stages.fakes import FakeMT, FakeSTT, FakeTTS  # noqa: E402
from interpreter.ui.hud import BARS, Mood, OrbWindow  # noqa: E402
from interpreter.ui.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "ajustes.ini"), QSettings.Format.IniFormat)


def render(w: OrbWindow) -> QImage:
    img = QImage(w.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    w.render(img)
    return img


def test_el_orbe_es_transparente_donde_no_se_dibuja(app):
    w = OrbWindow(400)
    w.set_mood(Mood.RUNNING)
    img = render(w)
    for x, y in ((1, 1), (img.width() - 2, 1), (1, img.height() - 2), (img.width() - 2, img.height() - 2)):
        assert img.pixelColor(x, y).alpha() == 0  # las esquinas no tapan el escritorio
    assert img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 200  # el orbe sí se ve
    assert w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert w.windowFlags() & Qt.WindowType.FramelessWindowHint


def test_zonas_de_clic(app):
    w = OrbWindow(500)
    c = w._center()
    assert w.hit_test(c) == "orb"
    rects = w.button_rects()
    for name in ("settings", "history", "mic"):
        assert w.hit_test(rects[name].center()) == name
    assert w.hit_test(QPointF(c.x() + w._unit() * 0.7, c.y() - w._unit() * 0.5)) == "drag"
    assert w.hit_test(QPointF(1, 1)) == "none"  # fuera del círculo: los clics pasan al escritorio


def test_los_clics_emiten_las_senales(app):
    w = OrbWindow(500)
    w.show()
    visto = []
    w.toggle_requested.connect(lambda: visto.append("orb"))
    w.settings_requested.connect(lambda: visto.append("ajustes"))
    w.history_requested.connect(lambda: visto.append("historial"))
    w.mic_requested.connect(lambda: visto.append("mic"))
    r = w.button_rects()
    for pos in (w._center(), r["settings"].center(), r["history"].center(), r["mic"].center()):
        QTest.mouseClick(w, Qt.MouseButton.LeftButton, pos=pos.toPoint())
    assert visto == ["orb", "ajustes", "historial", "mic"]
    w.close()


def test_la_voz_mueve_las_barras_y_en_reposo_se_apagan(app):
    t = [0.0]
    w = OrbWindow(400, now=lambda: t[0])
    w.show()
    w.set_mood(Mood.RUNNING)
    for _ in range(20):
        t[0] += 0.033
        w.push_level(0.2)
        w._tick()
    assert max(w._bars) > 0.2 and len(w._bars) == BARS
    for _ in range(120):
        t[0] += 0.033
        w._tick()
    assert max(w._bars) < 0.03  # sin voz vuelven a cero (y la animación baja de ritmo)
    assert w._timer.interval() == 120
    w.close()


def test_en_pausa_la_animacion_no_gasta_cpu(app):
    w = OrbWindow(400)
    assert not w.isVisible()
    fase = w._phase
    w._tick()  # oculta: no hace nada
    assert w._phase == fase


def test_hablar_activa_el_pulso(app):
    t = [0.0]
    w = OrbWindow(400, now=lambda: t[0])
    w.speak(2.0)
    assert w._active()
    t[0] = 3.0
    assert not w._active()


# -- unión con la ventana clásica -------------------------------------------------------------------------


class _Cap:
    def __init__(self, device, segmenter, on_segment, on_level, on_error=None):
        self.on_level = on_level

    def start(self): ...
    def stop(self): ...


class _Ply:
    def __init__(self, device, get_audio, on_error=None): ...
    def start(self): ...
    def stop(self): ...


def _builder(config, events):
    return Engine(config, events, lambda c: (FakeSTT(), FakeMT(), FakeTTS(chunks=1)), _Cap, _Ply)


def _ventana(settings):
    return MainWindow(_builder, settings=settings, load_on_start=False, voice_lister=lambda: [])


def test_la_ventana_clasica_sigue_siendo_la_predeterminada(app, settings):
    win = _ventana(settings)
    assert win._orb is None and win.btn_orb.text() == "Modo orbe"
    assert settings.value("ui_mode", "classic", type=str) == "classic"
    win.close()


def test_abrir_el_orbe_esconde_la_clasica_y_refleja_el_estado(app, settings):
    win = _ventana(settings)
    win.show()
    win.open_orb()
    assert win._orb is not None and win._orb.isVisible() and not win.isVisible()
    assert (
        win._orb.src_name == "Español" and win._orb.dst_name == "English"
    )  # nombres nativos, como el diseño
    win._on_state(EngineState.RUNNING, "")
    assert win._orb.mood is Mood.RUNNING
    win.btn_mute.setChecked(True)
    assert win._orb.mood is Mood.MUTED and win._orb.muted
    win.btn_mute.setChecked(False)
    win._on_state(EngineState.ERROR, "x")
    assert win._orb.mood is Mood.ERROR
    assert settings.value("ui_mode", "", type=str) == "orb"
    win.close()


def test_los_botones_del_orbe_controlan_la_aplicacion(app, settings):
    win = _ventana(settings)
    win.open_orb()
    win._orb.mic_requested.emit()
    assert win.engine.muted
    win._orb.settings_requested.emit()
    assert win.isVisible()  # «Ajustes» trae la ventana clásica al frente
    win.hide()
    win._orb.history_requested.emit()
    assert win.isVisible()
    win._orb.classic_requested.emit()
    assert win._orb is None and win.isVisible()
    assert settings.value("ui_mode", "", type=str) == "classic"
    win.close()


def test_la_traduccion_aparece_como_subtitulo_y_el_nivel_mueve_el_orbe(app, settings):
    win = _ventana(settings)
    win.open_orb()
    win._on_original_text("Hola a todos")
    win._on_translation_text("Hello everyone")
    assert "Hola a todos" in win._orb.caption and "Hello everyone" in win._orb.caption
    assert win._orb._now() < win._orb._speak_until or win._orb._speak_until > 0
    win._on_level(0.2)
    assert win._orb._level > 0
    win.close()


def test_cerrar_el_orbe_con_la_clasica_oculta_sale_de_la_aplicacion(app, settings):
    win = _ventana(settings)
    win.open_orb()
    win._orb.close()  # Alt+F4 en el orbe
    assert win._closing and win._orb is None
    win.close()


def test_cerrar_la_clasica_cierra_tambien_el_orbe(app, settings):
    win = _ventana(settings)
    win.open_orb()
    orb = win._orb
    win.close()
    assert not orb.isVisible() and win._orb is None


def test_si_la_ultima_vez_se_uso_el_orbe_se_abre_solo(app, settings):
    import time

    settings.setValue("ui_mode", "orb")
    win = _ventana(settings)
    end = time.time() + 2
    while win._orb is None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert win._orb is not None and win._orb.isVisible()
    win.close()
