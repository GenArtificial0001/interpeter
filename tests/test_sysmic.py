import os

import pytest

from interpreter.audio import sysmic
from interpreter.audio.sysmic import MicControlError, Microphone, match_device

MIC = "{fake}.{mic-1}"


def test_lista_el_microfono_con_su_estado(mic_falso):
    [m] = sysmic.list_microphones()
    assert m.id == MIC and m.is_default and m.muted is False and m.volume == 80


def test_silenciar_no_toca_el_volumen_y_activar_lo_devuelve(mic_falso):
    sysmic.set_mute(MIC, True)
    assert mic_falso.muted and mic_falso.volume == 80  # el volumen queda como estaba
    sysmic.set_mute(MIC, False)
    assert not mic_falso.muted and mic_falso.volume == 80


def test_volumen_de_1_a_100_fija_el_nivel_y_quita_el_silencio(mic_falso):
    mic_falso.muted = True
    after = sysmic.set_volume(MIC, 55)
    assert after.volume == 55 and after.muted is False


def test_volumen_0_silencia_y_conserva_el_nivel_anterior(mic_falso):
    after = sysmic.set_volume(MIC, 0)
    assert after.muted is True and mic_falso.volume == 80  # «activar» después recupera el 80
    assert ("volume", 0) not in mic_falso.writes


@pytest.mark.parametrize("pedido,esperado", [(-10, 0), (250, 100), (49.7, 49)])
def test_el_volumen_se_acota_entre_0_y_100(mic_falso, pedido, esperado):
    after = sysmic.set_volume(MIC, pedido)
    assert (after.volume, after.muted) == ((80, True) if esperado == 0 else (esperado, False))


def test_si_windows_no_aplica_el_cambio_se_avisa_sin_afirmar_nada(mic_falso):
    mic_falso.ignore_writes = True
    with pytest.raises(MicControlError, match="no aplicó"):
        sysmic.set_volume(MIC, 30)
    with pytest.raises(MicControlError, match="no aplicó"):
        sysmic.set_mute(MIC, True)


def test_un_microfono_que_ya_no_esta_da_un_mensaje_claro(mic_falso):
    with pytest.raises(MicControlError, match="ya no está conectado"):
        sysmic.set_volume("{otro}", 50)


def test_un_microfono_sin_control_de_volumen_sale_como_desconocido_nunca_como_silenciado(mic_falso):
    mic_falso.controllable = False
    [m] = sysmic.list_microphones()
    assert m.muted is None and m.volume is None


def test_una_falla_del_sistema_se_convierte_en_un_mensaje_para_personas(mic_falso):
    def boom():
        raise OSError("HRESULT 0x80070005")

    mic_falso.list_microphones = boom
    with pytest.raises(MicControlError, match="Windows no dejó leer los micrófonos"):
        sysmic.list_microphones()


MICS = [
    Microphone("a", "Microphone Array on SoundWire Device (9- Cirrus Logic XU)", True, False, 94),
    Microphone("b", "Headset (WL5024)", False, False, 70),
]


@pytest.mark.parametrize(
    "nombre,esperado",
    [
        ("Microphone Array on SoundWire D", "a"),  # PortAudio (MME) recorta a 31 caracteres
        ("microphone array on soundwire device (9- cirrus logic xu)", "a"),
        ("Headset (WL5024)", "b"),
        ("  headset (wl5024) ", "b"),
        ("", "a"),  # sin nombre: el predeterminado
        ("Un micrófono que no existe", None),
    ],
)
def test_une_el_dispositivo_de_la_aplicacion_con_el_microfono_de_windows(nombre, esperado):
    m = match_device(nombre, MICS)
    assert (m.id if m else None) == esperado


def test_sin_microfonos_no_hay_coincidencia():
    assert match_device("lo que sea", []) is None


@pytest.mark.skipif(os.name != "nt", reason="Core Audio solo existe en Windows")
def test_el_backend_real_se_puede_crear_en_windows():
    from interpreter.audio.sysmic import WindowsCoreAudio

    assert WindowsCoreAudio() is not None  # solo se crea: las pruebas NO leen ni escriben el micrófono real


@pytest.mark.skipif(os.name == "nt", reason="solo fuera de Windows")
def test_fuera_de_windows_se_dice_que_no_esta_disponible():
    from interpreter.audio.sysmic import MicUnsupported, WindowsCoreAudio

    with pytest.raises(MicUnsupported):
        WindowsCoreAudio()
