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
from interpreter.ui.hud import BARS, FULL, LIGHT, NONE, VU_STEPS, Mood, OrbWindow  # noqa: E402
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
    for name in w.BUTTONS:
        assert w.hit_test(rects[name].center()) == name
    assert w.hit_test(QPointF(c.x() + w._unit() * 0.7, c.y() - w._unit() * 0.5)) == "drag"
    assert w.hit_test(QPointF(1, 1)) == "none"  # fuera del círculo: los clics pasan al escritorio


def test_los_clics_emiten_las_senales(app):
    w = OrbWindow(500)
    w.show()
    visto = []
    w.toggle_requested.connect(lambda: visto.append("orb"))
    for nombre in ("settings", "input", "output", "speak", "listen", "voice", "mic"):
        senal = {
            "settings": w.settings_requested,
            "input": w.input_requested,
            "output": w.output_requested,
            "speak": w.speak_requested,
            "listen": w.listen_requested,
            "voice": w.voice_requested,
            "mic": w.mic_requested,
        }[nombre]
        senal.connect(lambda n=nombre: visto.append(n))
    r = w.button_rects()
    esperado = ["orb"]
    QTest.mouseClick(w, Qt.MouseButton.LeftButton, pos=w._center().toPoint())
    for nombre in ("settings", "input", "output", "speak", "listen", "voice", "mic"):
        QTest.mouseClick(w, Qt.MouseButton.LeftButton, pos=r[nombre].center().toPoint())
        esperado.append(nombre)
    assert visto == esperado
    w.close()


def test_la_voz_mueve_las_barras_y_en_reposo_se_apagan(app):
    t = [0.0]
    w = OrbWindow(400, now=lambda: t[0])
    w.set_animation(FULL)
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


# -- animación: el rendimiento del intérprete manda ---------------------------------------------------------


def test_por_defecto_la_animacion_es_la_ligera(app):
    w = OrbWindow(400)
    assert w.animation == LIGHT


def test_en_modo_ligero_solo_se_anima_la_barra_circular_de_escucha(app):
    w = OrbWindow(400)
    w.show()
    w.set_mood(Mood.RUNNING)
    fase = w._phase
    for _ in range(10):
        w.push_level(0.12)
        w._tick()
    assert w._vu > 0  # la barra de escucha reacciona al micrófono
    assert max(w._bars) == 0 and w._phase == fase  # nada más se anima: ni barras ni giros
    assert w._timer.interval() == 80  # ~12 fps como máximo
    w.close()


def test_la_barra_de_escucha_es_un_anillo_alrededor_del_ojo_que_se_enciende_de_arriba_abajo(app):
    from PySide6.QtGui import QPainter

    w = OrbWindow(500)
    u, c = w._unit(), w._center()
    img = QImage(w.size(), QImage.Format.Format_ARGB32_Premultiplied)

    def lit_pixels(lit: int) -> list[tuple[int, int]]:
        img.fill(0)
        p = QPainter(img)
        w._draw_vu(p, c, u, w_accent, 1.0, lit)
        p.end()
        return [
            (x, y)
            for y in range(0, img.height(), 2)
            for x in range(0, img.width(), 2)
            if img.pixelColor(x, y).alpha() > 0
        ]

    from interpreter.ui.hud import PALETTE

    w_accent = PALETTE[Mood.RUNNING][1]
    poco, mucho = lit_pixels(4), lit_pixels(VU_STEPS)
    assert 0 < len(poco) < len(mucho)
    for x, y in mucho:  # todo cae en el anillo que rodea al orbe (entre 0,24 y 0,30 del radio)
        d = ((x - c.x()) ** 2 + (y - c.y()) ** 2) ** 0.5
        assert u * 0.24 <= d <= u * 0.30
    assert all(y < c.y() + 3 for _, y in poco)  # con poco volumen solo se enciende la parte alta


def test_sin_voz_o_silenciado_la_barra_no_se_enciende(app):
    w = OrbWindow(400)
    w.show()
    w.set_mood(Mood.RUNNING)
    w.muted = True
    for _ in range(10):
        w.push_level(0.2)
        w._tick()
    assert w._vu == 0
    w.close()


def test_en_modo_ligero_solo_se_repinta_si_cambia_el_paso_de_la_barra(app):
    w = OrbWindow(400)
    w.show()
    w.set_mood(Mood.RUNNING)
    pintados = []
    original = w.update
    w.update = lambda *a: (pintados.append(1), original(*a))[1]
    w._level = 0.0
    for _ in range(30):  # en silencio y sin cambios: ningún repintado
        w._tick()
    assert pintados == []
    w.push_level(0.2)
    w._tick()
    assert len(pintados) == 1
    w.close()


def test_sin_animacion_no_se_mueve_nada(app):
    w = OrbWindow(400)
    w.show()
    w.set_animation(NONE)
    w.set_mood(Mood.RUNNING)
    for _ in range(10):
        w.push_level(0.3)
        w._tick()
    assert w._vu == 0 and max(w._bars) == 0
    w.close()


def test_la_animacion_completa_se_apaga_sola_si_pintar_es_lento(app):
    w = OrbWindow(400)
    w.set_animation(FULL)
    cambios = []
    w.animation_changed.connect(cambios.append)
    for _ in range(40):
        w._watch_paint_cost(80.0)  # cada cuadro tarda 80 ms: la CPU está saturada
    assert w.animation == LIGHT and cambios[-1] == LIGHT
    w.set_animation(FULL)
    for _ in range(40):
        w._watch_paint_cost(4.0)
    assert w.animation == FULL  # si pinta rápido se queda
    w.close()


def test_el_menu_del_orbe_ofrece_los_tres_modos(app):
    from interpreter.ui.hud import ANIMATIONS

    assert set(ANIMATIONS) == {LIGHT, FULL, NONE}


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
    assert win._orb is None and win.btn_orb.text() == "Ir al orbe"
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


class FalsaAccion:
    def __init__(self, texto):
        self.texto, self.marcada, self.habilitada, self._slots = texto, False, True, []
        self.triggered = self

    def setCheckable(self, _v):  # noqa: N802
        pass

    def setChecked(self, v):  # noqa: N802
        self.marcada = v

    def setEnabled(self, v):  # noqa: N802
        self.habilitada = v

    def connect(self, fn):
        self._slots.append(fn)

    def disparar(self):
        for fn in self._slots:
            fn()


class FalsoMenu:  # el menú real es modal y bloquearía la prueba
    ultimo = None

    def __init__(self, *_a):
        self.acciones, self.estilo = [], ""
        FalsoMenu.ultimo = self

    def setStyleSheet(self, estilo):  # noqa: N802
        self.estilo = estilo

    def addAction(self, texto):  # noqa: N802
        self.acciones.append(FalsaAccion(texto))
        return self.acciones[-1]

    def addSeparator(self):  # noqa: N802
        pass

    def exec(self, _pos):
        return None

    @property
    def titulo(self):
        return self.acciones[0].texto

    @property
    def opciones(self):
        return [a for a in self.acciones if a.habilitada and a is not self.acciones[0]]


def test_los_botones_del_orbe_controlan_la_aplicacion(app, settings, monkeypatch):
    monkeypatch.setattr("interpreter.ui.window.QMenu", FalsoMenu)
    win = _ventana(settings)
    win.open_orb()
    win._orb.mic_requested.emit()
    assert win.engine.muted
    win._orb.settings_requested.emit()
    assert win.isVisible()  # «Ajustes» trae la ventana al frente
    win._orb.settings_requested.emit()
    assert not win.isVisible()  # y pulsarlo otra vez la oculta (sin apagar nada)
    assert win._orb is not None and win.engine is not None
    win._orb.classic_requested.emit()
    assert win._orb is None and win.isVisible()
    assert settings.value("ui_mode", "", type=str) == "classic"
    win.close()


def test_cerrar_la_ventana_de_ajustes_con_el_orbe_abierto_solo_la_oculta(app, settings):
    from PySide6.QtGui import QCloseEvent

    win = _ventana(settings)
    win.open_orb()
    win.show()
    evento = QCloseEvent()
    monkey_spont = type("E", (), {"spontaneous": lambda self: True, "ignore": lambda self: evento.ignore()})()
    win.closeEvent(monkey_spont)  # la X de la ventana (evento espontáneo)
    assert not win.isVisible() and win._orb is not None and win._orb.isVisible()
    win.close()  # cierre programático (salir de verdad)
    assert win._closing


def test_el_boton_cerrar_del_orbe_cierra_toda_la_aplicacion(app, settings):
    win = _ventana(settings)
    win.open_orb()
    win._orb.quit_requested.emit()
    assert win._closing  # orbe cerrado -> ventana y motor cerrados (no quedan puertos abiertos)


def test_los_menus_del_orbe_ofrecen_las_opciones_de_ajustes_y_las_aplican(app, settings, monkeypatch):
    monkeypatch.setattr("interpreter.ui.window.QMenu", FalsoMenu)
    win = _ventana(settings)
    win.open_orb()
    casos = (
        (win._orb.input_requested, win.cmb_input, "Micrófono de entrada"),
        (win._orb.output_requested, win.cmb_output, "Salida de audio"),
        (win._orb.speak_requested, win.cmb_source, "Yo hablo"),
        (win._orb.listen_requested, win.cmb_target, "Ellos escuchan"),
        (win._orb.voice_requested, win.cmb_profile, "Perfil de voz"),
    )
    for senal, combo, titulo in casos:
        FalsoMenu.ultimo = None
        senal.emit()
        menu = FalsoMenu.ultimo
        assert menu is not None and menu.titulo == titulo
        assert "background: #FFFFFF" in menu.estilo  # legible: fondo claro y letra oscura
        assert [a.texto for a in menu.opciones][: combo.count()] == [
            combo.itemText(i) for i in range(combo.count())
        ]
        assert [a.marcada for a in menu.opciones[: combo.count()]].count(True) == 1
        if combo.count() > 1 and combo is not win.cmb_profile:  # el perfil puede pedir la licencia (modal)
            otro = 1 if combo.currentIndex() == 0 else 0
            menu.opciones[otro].disparar()
            assert combo.currentIndex() == otro  # elegir en el menú = elegir en Ajustes
    win.close()


def test_el_orbe_muestra_los_idiomas_actuales_bajo_hablo_y_escuchan(app):
    w = OrbWindow(500)
    w.set_languages("Español", "English")
    assert (w.src_name, w.dst_name) == ("Español", "English")
    assert set(w.button_rects()) == set(w.BUTTONS)
    assert {"speak", "listen", "voice", "input", "mic", "level", "output", "settings", "close"} == set(
        w.BUTTONS
    )
    w.close()


def test_los_botones_del_orbe_no_se_pisan(app):
    w = OrbWindow(640)
    rects = list(w.button_rects().values())
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            assert not a.intersects(b), (a, b)
    w.close()


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


def test_el_modo_de_animacion_se_recuerda(app, settings):
    win = _ventana(settings)
    win.open_orb()
    assert win._orb.animation == LIGHT
    win._orb.set_animation(FULL)
    assert settings.value("orb_animation", "", type=str) == FULL
    win.close()
    win2 = _ventana(settings)
    win2.open_orb()
    assert win2._orb.animation == FULL
    win2.close()


def test_cerrar_la_aplicacion_libera_el_puerto_del_servidor(app, settings):
    """El botón de cerrar debe terminar TODO: el servidor del micrófono virtual no puede quedar escuchando."""
    import socket
    import time

    from interpreter.audio.webmic import WebMicServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    def build(config, events):
        return Engine(
            config,
            events,
            lambda c: (FakeSTT(), FakeMT(), FakeTTS(chunks=1)),
            _Cap,
            _Ply,
            webmic_factory=lambda cb: WebMicServer(port=port, on_clients_changed=cb, dev=True),
        )

    win = MainWindow(build, settings=settings, load_on_start=False, voice_lister=lambda: [])
    server = win.engine.ensure_webmic()
    assert server.running
    with socket.socket() as c:
        c.settimeout(1)
        assert c.connect_ex(("127.0.0.1", port)) == 0  # el puerto está abierto
    win.close()
    time.sleep(0.3)
    with socket.socket() as c:
        c.settimeout(1)
        assert c.connect_ex(("127.0.0.1", port)) != 0  # cerrada la ventana, nadie escucha ya


def test_cerrar_el_orbe_con_la_clasica_oculta_tambien_libera_el_puerto(app, settings):
    import socket
    import time

    from interpreter.audio.webmic import WebMicServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    def build(config, events):
        return Engine(
            config,
            events,
            lambda c: (FakeSTT(), FakeMT(), FakeTTS(chunks=1)),
            _Cap,
            _Ply,
            webmic_factory=lambda cb: WebMicServer(port=port, on_clients_changed=cb, dev=True),
        )

    win = MainWindow(build, settings=settings, load_on_start=False, voice_lister=lambda: [])
    win.engine.ensure_webmic()
    win.open_orb()
    win._orb.close()  # «Salir de Interpreter» en el orbe
    time.sleep(0.3)
    with socket.socket() as c:
        c.settimeout(1)
        assert c.connect_ex(("127.0.0.1", port)) != 0


# -- micrófono de Windows: activar y volumen -----------------------------------------------------------------


def test_el_microfono_de_windows_se_muestra_y_se_controla_desde_la_ventana(app, settings, mic_falso):
    win = _ventana(settings)
    assert not win.mic_row.isHidden()  # el micrófono falso es controlable
    assert win.chk_mic_on.isChecked() and win.sld_mic.value() == 80 and win.lbl_mic_vol.text() == "80 %"
    win.chk_mic_on.setChecked(False)  # «silenciar»
    assert mic_falso.muted and mic_falso.volume == 80
    win.chk_mic_on.setChecked(True)
    assert not mic_falso.muted
    win.sld_mic.setValue(35)
    assert win.lbl_mic_vol.text() == "35 %"
    win._mic_timer.stop()
    win._apply_mic_volume()  # lo que dispara el temporizador tras 200 ms
    assert mic_falso.volume == 35 and ("volume", 35) in mic_falso.writes
    win.close()


def test_el_volumen_se_aplica_una_vez_aunque_se_mueva_el_control_muchas_veces(app, settings, mic_falso):
    win = _ventana(settings)
    mic_falso.writes.clear()
    for v in range(10, 60, 5):
        win.sld_mic.setValue(v)
    assert mic_falso.writes == []  # mientras se arrastra no se escribe nada
    win._mic_timer.stop()
    win._apply_mic_volume()
    assert mic_falso.writes.count(("volume", 55)) == 1
    win.close()


def test_si_el_microfono_no_se_puede_controlar_la_fila_no_aparece(app, settings, mic_falso):
    mic_falso.controllable = False
    win = _ventana(settings)
    assert win.mic_row.isHidden()
    win.close()


def test_un_fallo_de_windows_se_avisa_y_la_ventana_sigue(app, settings, mic_falso):
    win = _ventana(settings)
    mic_falso.ignore_writes = True
    win.chk_mic_on.setChecked(False)
    assert "no aplicó" in win.lbl_warning.text()
    assert win.chk_mic_on.isChecked()  # se vuelve a mostrar el estado real
    win.close()


def test_la_rueda_sobre_microfono_en_el_orbe_cambia_el_volumen_y_en_otro_lado_el_tamano(
    app, settings, mic_falso
):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    win = _ventana(settings)
    win.open_orb()
    orb = win._orb
    orb.resize(500, 500)
    pasos = []
    orb.volume_step.connect(pasos.append)
    mic = orb.button_rects()["mic"].center()

    def rueda(pos, dy):
        ev = QWheelEvent(
            QPointF(pos),
            QPointF(pos),
            QPoint(0, 0),
            QPoint(0, dy),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        orb.wheelEvent(ev)

    rueda(mic, 120)
    rueda(mic, -120)
    assert pasos == [5, -5]
    assert mic_falso.volume == 80 + 5 - 5  # +5 y luego -5
    assert "Volumen del micrófono" in orb.caption
    ancho = orb.width()
    rueda(orb._center(), 120)  # fuera de «Micrófono»: cambia el tamaño
    assert orb.width() == ancho + 24
    win.close()


def test_el_menu_del_orbe_activa_o_silencia_el_microfono_de_windows(app, settings, mic_falso):
    win = _ventana(settings)
    win.open_orb()
    assert win._orb.sys_mic_muted is False
    win._orb.sys_mic_toggle_requested.emit()  # estaba activo: se silencia
    assert mic_falso.muted and win._orb.sys_mic_muted is True
    win._orb.sys_mic_toggle_requested.emit()
    assert not mic_falso.muted
    win.close()


# -- arrancar siempre en orbe (cuando lo llama Visión) ----------------------------------------------------


def _esperar(app, cond, seg=2.0):
    import time

    end = time.time() + seg
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def test_con_orb_forzado_arranca_en_orbe_aunque_la_ultima_vista_fuera_la_clasica(app, settings):
    settings.setValue("ui_mode", "classic")
    win = MainWindow(_builder, settings=settings, load_on_start=False, voice_lister=lambda: [], ui_mode="orb")
    assert win.starts_in_orb
    assert _esperar(app, lambda: win._orb is not None and win._orb.isVisible())
    assert not win.isVisible()
    win.close()


def test_con_clasica_forzada_no_se_abre_el_orbe_aunque_era_la_ultima(app, settings):
    settings.setValue("ui_mode", "orb")
    win = MainWindow(
        _builder, settings=settings, load_on_start=False, voice_lister=lambda: [], ui_mode="classic"
    )
    assert not win.starts_in_orb
    app.processEvents()
    assert win._orb is None
    win.close()


def test_si_el_orbe_falla_al_arrancar_se_muestra_la_ventana_clasica(app, settings, monkeypatch):
    win = MainWindow(_builder, settings=settings, load_on_start=False, voice_lister=lambda: [], ui_mode="orb")

    def roto():
        raise RuntimeError("el orbe no pudo abrirse")

    monkeypatch.setattr(win, "open_orb", roto)
    win._open_orb_at_start()
    assert win.isVisible() and win._orb is None  # nunca queda la aplicación invisible
    win.close()


def test_el_orbe_cabe_dentro_del_anillo_grande(app):
    w = OrbWindow()
    u, c = w._unit(), w._center()
    for name, rect in w.button_rects().items():
        for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            d = ((corner.x() - c.x()) ** 2 + (corner.y() - c.y()) ** 2) ** 0.5
            assert d <= u * 0.70, (
                name,
                d / u,
            )  # iconos y letras dentro del círculo (el anillo grande mide 0,70)
    assert w.width() == 640  # tamaño inicial (se agrandó un poco para que quepan los 8 botones)


# -- spec A: micrófono unificado, «encender» forzado y vigilante ---------------------------------------------


class _CapSpy(_Cap):
    abiertas = 0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        type(self).abiertas += 1


def _ventana_en_marcha(settings):
    """Ventana con el motor en marcha (captura falsa que cuenta cuántas veces se abre)."""
    _CapSpy.abiertas = 0
    t = [1000.0]

    def build(config, events):
        return Engine(config, events, lambda c: (FakeSTT(), FakeMT(), FakeTTS(chunks=1)), _CapSpy, _Ply)

    win = MainWindow(build, settings=settings, load_on_start=False, voice_lister=lambda: [])
    win._clock = lambda: t[0]
    win.engine.load()
    win._toggle()  # inicia (RUNNING)
    return win, t


def test_apagar_y_encender_son_el_mismo_interruptor_en_los_tres_lugares(app, settings, mic_falso):
    win = _ventana(settings)
    win.open_orb()
    win._orb.mic_requested.emit()  # clic en «Micrófono» del orbe
    assert win.engine.muted and mic_falso.muted and not win.chk_mic_on.isChecked()
    assert win.btn_mute.isChecked() and "APAGADO" in win.btn_mute.text()
    win.chk_mic_on.setChecked(True)  # la casilla lo vuelve a encender
    assert not win.engine.muted and not mic_falso.muted and not win.btn_mute.isChecked()
    win.btn_mute.setChecked(True)  # y el botón de la ventana
    assert mic_falso.muted and not win.chk_mic_on.isChecked()
    win.close()


def test_encender_fuerza_el_ciclo_aunque_windows_diga_que_ya_estaba_activo(app, settings, mic_falso):
    win = _ventana(settings)
    mic_falso.writes.clear()
    win.btn_mute.setChecked(True)
    win.btn_mute.setChecked(False)
    assert mic_falso.writes.count(("mute", True)) == 2 and mic_falso.writes[-1] == ("mute", False)
    mic_falso.writes.clear()
    win.reconnect_mic()  # «Reconectar» también cicla aunque ya estuviera activo
    assert mic_falso.writes == [("mute", True), ("mute", False)]
    win.close()


def test_apagar_no_toca_el_volumen_y_encender_devuelve_el_mismo(app, settings, mic_falso):
    win = _ventana(settings)
    win.btn_mute.setChecked(True)
    win.btn_mute.setChecked(False)
    assert mic_falso.volume == 80
    win.close()


def test_encender_mientras_se_interpreta_reinicia_la_captura(app, settings, mic_falso):
    win, _ = _ventana_en_marcha(settings)
    assert _CapSpy.abiertas == 1
    win.btn_mute.setChecked(True)
    assert _CapSpy.abiertas == 1  # apagar no reabre nada
    win.btn_mute.setChecked(False)
    assert _CapSpy.abiertas == 2  # encender reabre la captura (sin recargar modelos)
    win.engine.stop()
    win.close()


def test_el_vigilante_reactiva_un_microfono_silenciado_por_otro(app, settings, mic_falso):
    win, _ = _ventana_en_marcha(settings)
    mic_falso.external_mute(True)  # el auricular o Windows lo silencian solos
    win._mic_watch()
    assert not mic_falso.muted and win.mic_events == 1
    assert "se había silenciado solo" in win.log.toPlainText()
    win.engine.stop()
    win.close()


def test_el_vigilante_no_hace_nada_si_la_persona_apago_el_microfono(app, settings, mic_falso):
    win, _ = _ventana_en_marcha(settings)
    win.btn_mute.setChecked(True)
    mic_falso.writes.clear()
    win._mic_watch()
    assert mic_falso.muted and mic_falso.writes == [] and win.mic_events == 0
    win.engine.stop()
    win.close()


def test_el_vigilante_se_puede_desactivar(app, settings, mic_falso):
    win, _ = _ventana_en_marcha(settings)
    win.chk_keep_mic.setChecked(False)
    mic_falso.external_mute(True)
    win._mic_watch()
    assert mic_falso.muted and win.mic_events == 0
    win.engine.stop()
    win.close()


def test_sin_audio_durante_6_s_reinicia_la_captura_con_tope_de_uno_cada_30_s(app, settings, mic_falso):
    win, t = _ventana_en_marcha(settings)
    t[0] += 5
    win._mic_watch()
    assert _CapSpy.abiertas == 1  # todavía no pasaron 6 s sin señal
    t[0] += 2
    win._mic_watch()
    assert _CapSpy.abiertas == 2 and "reinicié el micrófono" in win.log.toPlainText()
    t[0] += 10  # otra vez sin señal, pero dentro de los 30 s: no reinicia de nuevo, avisa
    win._mic_watch()
    assert _CapSpy.abiertas == 2 and "No llega audio" in win.log.toPlainText()
    t[0] += 40
    win._mic_watch()
    assert _CapSpy.abiertas == 3  # pasado el enfriamiento, vuelve a intentarlo
    win.engine.stop()
    win.close()


def test_si_llega_audio_el_vigilante_no_reinicia_nada(app, settings, mic_falso):
    win, t = _ventana_en_marcha(settings)
    for _ in range(10):
        t[0] += 3
        win._on_level(0.05)  # llega señal real
        win._mic_watch()
    assert _CapSpy.abiertas == 1 and win.mic_events == 0
    win.engine.stop()
    win.close()


def test_los_cambios_del_microfono_quedan_en_el_registro(app, settings, mic_falso, caplog):
    import logging

    win = _ventana(settings)
    with caplog.at_level(logging.INFO, logger="interpreter.ui.window"):
        win.btn_mute.setChecked(True)
    assert any(
        "Micrófono: silenciado=True" in r.message and "origen=persona" in r.message for r in caplog.records
    )
    win.close()


# -- spec B: carga en el ojo, sonido de «listo» y retraso en el orbe ---------------------------------


def test_el_porcentaje_sube_sin_bajar_y_no_llega_a_100_antes_de_tiempo(app):
    w = OrbWindow(400)
    w.show()
    w.set_mood(Mood.LOADING)
    vistos = []
    for fraccion in (0.02, 0.30, 0.30, 0.60, 0.85):
        w.set_progress(fraccion, "etapa")
        for _ in range(60):
            w._tick()
        vistos.append(w.progress_percent)
    assert vistos == sorted(vistos)  # nunca baja
    assert all(v <= 93 for v in vistos)  # «repta» como mucho +8 puntos sobre la etapa informada
    w.set_progress(0.5)  # un valor menor que el ya informado no hace retroceder
    w._tick()
    assert w.progress_percent >= vistos[-1]
    w.set_progress(1.0, "Listo")
    assert w.progress_percent == 100
    w.close()


def test_el_anillo_y_el_numero_se_dibujan_durante_la_carga(app):
    w = OrbWindow(400)
    w.show()
    w.set_mood(Mood.LOADING)
    w.set_progress(0.5, "Cargando el traductor…")
    for _ in range(80):
        w._tick()
    assert w._vu > 0 and w.stage_label == "Cargando el traductor…"
    assert render(w).pixelColor(w.width() // 2, w.height() // 2).alpha() > 200
    w.close()


def test_el_retraso_se_muestra_con_su_color_de_umbral(app):
    w = OrbWindow(400)
    assert w.latency is None
    w.set_latency(1.2, 1.3)
    assert w.latency == (1.2, 1.3)
    w.set_latency(None)
    assert w.latency is None


def test_el_orbe_muestra_el_retraso_de_cada_frase(app, settings):
    from interpreter.pipeline import UtteranceMetrics

    win = _ventana(settings)
    win.open_orb()
    m = UtteranceMetrics(stt=0.5, mt=0.3, first_audio_from_speech_end=1.8)
    win._on_metrics(m)
    assert win._orb.latency == (1.8, 1.8)
    win.close()


def _ventana_con_sonido(settings, sonidos):
    return MainWindow(
        _builder,
        settings=settings,
        load_on_start=False,
        voice_lister=lambda: [],
        sound_player=sonidos.append,
    )


def test_suena_una_sola_vez_al_terminar_de_cargar_y_no_al_detener(app, settings):
    sonidos = []
    win = _ventana_con_sonido(settings, sonidos)
    win._on_state(EngineState.LOADING, "")
    assert sonidos == []
    win._on_state(EngineState.READY, "")
    assert len(sonidos) == 1 and sonidos[0][:4] == b"RIFF"  # el WAV de «listo»
    win._on_state(EngineState.RUNNING, "")
    win._on_state(EngineState.READY, "")  # detener la interpretación NO suena
    assert len(sonidos) == 1
    win.close()


def test_el_sonido_se_puede_apagar_y_el_ajuste_se_recuerda(app, settings):
    sonidos = []
    win = _ventana_con_sonido(settings, sonidos)
    win.chk_sound.setChecked(False)
    win._on_state(EngineState.LOADING, "")
    win._on_state(EngineState.READY, "")
    assert sonidos == []
    assert settings.value("sound_on_ready", True, type=bool) is False
    win.close()


def test_un_fallo_del_sonido_no_rompe_la_aplicacion(app, settings):
    def roto(_data):
        raise OSError("sin dispositivo de audio")

    win = MainWindow(
        _builder, settings=settings, load_on_start=False, voice_lister=lambda: [], sound_player=roto
    )
    win._on_state(EngineState.LOADING, "")
    win._on_state(EngineState.READY, "")  # no debe lanzar
    win.close()


def test_el_avance_del_motor_llega_al_orbe(app, settings):
    win = _ventana(settings)
    win.open_orb()
    win._orb.set_mood(Mood.LOADING)
    win._on_progress(0.6, "Cargando la voz…")
    assert win._orb.stage_label == "Cargando la voz…"
    win.close()


# -- diseño: interruptores, diálogos legibles, tooltips y posición de «Cerrar» -------------------------------


def test_las_casillas_de_ajustes_son_interruptores_on_off(app, settings):
    from interpreter.ui.toggle import Toggle

    win = _ventana(settings)
    for chk in (win.chk_mic_on, win.chk_keep_mic, win.chk_echo, win.chk_sound):
        assert isinstance(chk, Toggle)
        antes = chk.isChecked()
        chk.setChecked(not antes)  # mismo comportamiento que un QCheckBox
        assert chk.isChecked() is (not antes)
        chk.setChecked(antes)
    win.close()


def test_el_interruptor_se_ve_distinto_encendido_y_apagado(app):
    from interpreter.ui.toggle import Toggle

    t = Toggle("Anti-eco")
    t.resize(t.sizeHint())
    t.setChecked(False)
    off = t.grab().toImage()
    t.setChecked(True)
    on = t.grab().toImage()
    assert off != on
    azul = on.pixelColor(4, on.height() // 2)  # la pista encendida es azul Ford
    assert azul.blue() > 200 and azul.red() < 80
    t.setEnabled(False)
    assert t.grab().toImage() != on


def _luminosidad(c):
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def test_los_dialogos_se_leen_aunque_windows_este_en_modo_oscuro(app, settings, tmp_path):
    from PySide6.QtGui import QColor, QPalette

    from interpreter.glossary import Glossary
    from interpreter.ui.glossary_dialog import GlossaryDialog
    from interpreter.ui.voice_dialog import VoiceSampleDialog

    paleta_anterior = app.palette()
    oscura = QPalette()
    for rol, color in (
        (QPalette.ColorRole.Window, "#202020"),
        (QPalette.ColorRole.Base, "#2b2b2b"),
        (QPalette.ColorRole.AlternateBase, "#333333"),
        (QPalette.ColorRole.Button, "#2d2d2d"),
        (QPalette.ColorRole.WindowText, "#f0f0f0"),
        (QPalette.ColorRole.Text, "#f0f0f0"),
        (QPalette.ColorRole.ButtonText, "#f0f0f0"),
    ):
        oscura.setColor(rol, QColor(color))
    app.setPalette(oscura)
    win = _ventana(settings)
    try:
        glos = GlossaryDialog(win, Glossary.defaults(), tmp_path / "g.json")
        voz = VoiceSampleDialog(win, None, tmp_path / "mi_voz.wav")
        for dlg in (glos, voz):
            dlg.show()
            app.processEvents()
            img = dlg.grab().toImage()
            assert _luminosidad(img.pixelColor(6, img.height() // 2)) > 200, dlg.windowTitle()  # fondo claro
        tabla = glos.table
        fila = tabla.grab().toImage()
        assert _luminosidad(fila.pixelColor(fila.width() - 30, fila.height() // 2)) > 200  # celdas claras
        for dlg in (glos, voz):
            dlg.close()
    finally:
        app.setPalette(paleta_anterior)
        win.close()


def test_cerrar_esta_centrado_abajo_y_el_microfono_arriba_a_la_derecha(app):
    w = OrbWindow(640)
    r = w.button_rects()
    c = w._center()
    assert abs(r["close"].center().x() - c.x()) < 1  # centrado
    assert r["close"].center().y() > c.y()  # debajo del ojo
    assert r["mic"].center().x() > c.x() and r["mic"].center().y() < c.y()
    w.close()


def test_cada_boton_del_orbe_tiene_su_tooltip(app, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent

    w = OrbWindow(640)
    w.set_languages("Español", "English")
    vistos = []
    monkeypatch.setattr("interpreter.ui.hud.QToolTip.showText", lambda pos, texto, *_a: vistos.append(texto))
    for nombre, rect in w.button_rects().items():
        punto = rect.center().toPoint()
        w.event(QHelpEvent(QEvent.Type.ToolTip, punto, QPoint(10, 10)))
        assert vistos and vistos[-1] == w.tooltip_for(nombre) != "", nombre
    assert "Español" in w.tooltip_for("speak") and "English" in w.tooltip_for("listen")
    assert "cierra toda la aplicación" in w.tooltip_for("close")
    w.close()


def test_el_boton_nivel_ajusta_el_volumen_del_microfono(app, settings, monkeypatch, mic_falso):
    monkeypatch.setattr("interpreter.ui.window.QMenu", FalsoMenu)
    win = _ventana(settings)
    win.open_orb()
    assert win._sys_mic is not None and win._orb.mic_volume == 80  # el orbe conoce el volumen actual
    r = win._orb.button_rects()
    assert r["level"].center().x() < win._orb._center().x()  # a la izquierda, equilibrando a «Micrófono»
    FalsoMenu.ultimo = None
    win._orb.level_requested.emit()
    menu = FalsoMenu.ultimo
    assert menu.titulo == "Volumen del micrófono"
    textos = [a.texto for a in menu.opciones]
    assert textos[:2] == ["Subir 10 %", "Bajar 10 %"] and "50 %" in textos
    elegir = {a.texto: a for a in menu.opciones}
    elegir["50 %"].disparar()
    assert mic_falso.volume == 50 and win._orb.mic_volume == 50
    FalsoMenu.ultimo = None
    win._orb.level_requested.emit()
    {a.texto: a for a in FalsoMenu.ultimo.opciones}["Subir 10 %"].disparar()
    assert mic_falso.volume == 60
    assert "60 %" in win._orb.tooltip_for("level")
    win.close()


def test_el_icono_de_nivel_enciende_barras_segun_el_volumen(app):
    w = OrbWindow(400)
    imagenes = []
    for vol in (0, 30, 60, 100):
        w.mic_volume = vol
        imagenes.append(w.grab().toImage())
    assert imagenes[0] != imagenes[1] != imagenes[2] != imagenes[3]
    w.close()
