import numpy as np
import pytest

from interpreter import voices
from interpreter.voices import SapiVoice, VoiceBank, parse_sapi_list, resolve_voice, voices_for

SAPI = [
    SapiVoice("Microsoft Helena Desktop", "es-ES", "mujer"),
    SapiVoice("Microsoft Sabina Desktop", "es-MX", "mujer"),
    SapiVoice("Microsoft David Desktop", "en-US", "hombre"),
    SapiVoice("Microsoft Zira Desktop", "en-US", "mujer"),
]


def test_cada_idioma_tiene_hombres_y_mujeres():
    for lang in ("es", "en", "pt", "fr"):
        generos = {v.gender for v in voices_for(lang, SAPI)}
        assert generos == {"hombre", "mujer"}, (lang, generos)


def test_la_de_siempre_es_la_primera_en_espanol_e_ingles():
    assert voices_for("es", SAPI)[0].id == "mms" and voices_for("en", SAPI)[0].id == "mms"
    assert voices_for("pt", SAPI)[0].gender == "hombre"  # MMS de portugués no está descargado


def test_las_voces_de_windows_solo_aparecen_en_su_idioma_con_el_genero_que_informa_windows():
    es = {v.id: v for v in voices_for("es", SAPI)}
    sabina = es["sapi:Microsoft Sabina Desktop"]
    assert sabina.gender == "mujer" and sabina.label.startswith("Sabina")
    assert "sapi:Microsoft Zira Desktop" not in es
    assert "sapi:Microsoft Zira Desktop" in {v.id for v in voices_for("en", SAPI)}


def test_una_voz_que_no_habla_el_idioma_cae_a_la_predeterminada():
    assert resolve_voice("sapi:Microsoft Zira Desktop", "es", SAPI).id == "mms"
    assert resolve_voice("auto", "pt", SAPI).gender == "hombre"
    assert resolve_voice("yourtts:female-pt-4", "pt", SAPI).gender == "mujer"
    assert resolve_voice("mms", "fr", SAPI).id.startswith("coqui")  # no hay MMS de francés: usa la de Coqui


def test_las_voces_de_yourtts_piden_su_licencia_y_las_demas_no():
    todas = [v for lang in ("es", "en", "pt", "fr") for v in voices_for(lang, SAPI)]
    assert {v.license for v in todas if v.engine == "yourtts"} == {"clone_fast"}
    assert all(v.license is None for v in todas if v.engine != "yourtts")


def test_lee_la_lista_de_voces_de_windows():
    texto = "Microsoft David Desktop|en-US|Male\r\nMicrosoft Zira Desktop|en-US|Female\r\n\r\nbasura\r\n"
    assert parse_sapi_list(texto) == [
        SapiVoice("Microsoft David Desktop", "en-US", "hombre"),
        SapiVoice("Microsoft Zira Desktop", "en-US", "mujer"),
    ]


class _FakeMms:
    sample_rate = 16000

    def synthesize(self, text, language):
        yield np.zeros(10, dtype=np.float32)


def test_el_banco_de_voces_despacha_al_motor_de_la_voz_elegida(monkeypatch):
    monkeypatch.setattr(voices, "installed_sapi_voices", lambda refresh=False: SAPI)
    llamadas = []
    banco = VoiceBank("sapi:Microsoft Sabina Desktop")

    class Worker:
        def speak(self, voice, text):
            llamadas.append((voice, text))
            return np.zeros(2205, dtype=np.float32), 22050

        def close(self):
            llamadas.append("cerrado")

    banco._sapi = Worker()
    out = list(banco.synthesize("Hola.", "es"))
    assert llamadas == [("Microsoft Sabina Desktop", "Hola.")]
    assert banco.sample_rate == 22050 and len(out) == 1  # la frecuencia es la de ESTA voz
    banco.set_voice("auto")  # vuelve a la predeterminada: MMS
    monkeypatch.setattr(banco, "_get_mms", lambda: _FakeMms())
    list(banco.synthesize("Hola.", "es"))
    assert banco.sample_rate == 16000
    banco.close()
    assert llamadas[-1] == "cerrado"


def test_sin_voz_para_un_idioma_da_un_error_claro(monkeypatch):
    monkeypatch.setattr(voices, "installed_sapi_voices", lambda refresh=False: [])
    with pytest.raises(ValueError, match="No hay voz genérica"):
        list(VoiceBank().synthesize("x", "zz"))
