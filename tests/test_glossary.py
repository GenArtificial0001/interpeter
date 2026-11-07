import json

import pytest

from interpreter.glossary import Glossary, GlossaryEntry, norm


def glos():
    return Glossary(
        [
            GlossaryEntry("SharePoint", ["sherpoin", "share point"], "Share Point"),
            GlossaryEntry("Ford", ["fort", "forth"]),
            GlossaryEntry("SAP", ["ese a pe"], "S A P"),
            GlossaryEntry("KPI", ["ka pi ai"], "K P I"),
            GlossaryEntry(
                "patio de contenedores", [], "", {"en": "container yard", "pt": "pátio de contêineres"}
            ),
        ]
    )


def test_norm_ignora_tildes_mayusculas_y_signos():
    assert norm("¡Ká Pi ÁI!") == "ka pi ai"


# -- reconocimiento ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "oido,esperado,terminos",
    [
        (
            "Les dimos conexiones a listas de Sherpoin.",
            "Les dimos conexiones a listas de SharePoint.",
            ["SharePoint"],
        ),
        (
            "con la llegada de la inteligencia artificial en un fort",
            "con la llegada de la inteligencia artificial en un Ford",
            ["Ford"],
        ),
        ("revisa el ese a pe antes del cierre", "revisa el SAP antes del cierre", ["SAP"]),
        ("el Ká Pi Ái subió", "el KPI subió", ["KPI"]),
        ("¿Sherpoin, verdad?", "¿SharePoint, verdad?", ["SharePoint"]),
        (
            "Sarepoint y fort",
            "SharePoint y Ford",
            ["SharePoint", "Ford"],
        ),  # difuso para el largo, variante para el corto
        ("subí el archivo a SharePoint", "subí el archivo a SharePoint", ["SharePoint"]),  # ya estaba bien
    ],
)
def test_corrige_lo_que_whisper_oyo_mal(oido, esperado, terminos):
    texto, encontrados = glos().correct(oido)
    assert texto == esperado and encontrados == terminos


@pytest.mark.parametrize(
    "frase",
    [
        "el foro de la fortaleza estuvo lleno",  # "foro" se parece a "Ford", pero no se toca
        "fuerte fortuna forma",
        "la compañía sapo y el kilo",
        "buenísima idea para el patio",
    ],
)
def test_no_toca_palabras_normales_del_espanol(frase):
    texto, encontrados = glos().correct(frase)
    assert texto == frase and encontrados == []


def test_sin_glosario_no_cambia_nada():
    assert Glossary().correct("Sherpoin") == ("Sherpoin", [])
    assert Glossary().initial_prompt() == ""


def test_el_prompt_lista_los_terminos():
    assert glos().initial_prompt() == "Glosario: SharePoint, Ford, SAP, KPI, patio de contenedores."


# -- traducción --------------------------------------------------------------------------------------


def test_protege_y_restaura_terminos():
    g = glos()
    masked, mapping = g.protect("Abre SharePoint con el Ford y el SAP.", "en")
    assert "SharePoint" not in masked and "Ford" not in masked and "SAP" not in masked
    traducido = masked.replace("Abre", "Open").replace("con el", "with the").replace("y el", "and the")
    assert g.restore(traducido, mapping) == "Open SharePoint with the Ford and the SAP."


def test_traduccion_fija_por_idioma_destino():
    g = glos()
    masked, mapping = g.protect("Revisa el patio de contenedores hoy", "en")
    assert g.restore(masked, mapping) == "Revisa el container yard hoy"
    masked_pt, mapping_pt = g.protect("Revisa el patio de contenedores hoy", "pt")
    assert g.restore(masked_pt, mapping_pt) == "Revisa el pátio de contêineres hoy"
    masked_fr, mapping_fr = g.protect(
        "Revisa el patio de contenedores hoy", "fr"
    )  # sin traducción fija: se conserva
    assert g.restore(masked_fr, mapping_fr) == "Revisa el patio de contenedores hoy"


def test_restore_devuelve_none_si_el_traductor_perdio_una_marca():
    g = glos()
    masked, mapping = g.protect("SharePoint y Ford", "en")
    assert g.restore("Only one QZ0Q here", mapping) is None


def test_restore_tolera_espacios_y_mayusculas_que_el_traductor_pueda_meter():
    g = glos()
    masked, mapping = g.protect("SharePoint y Ford", "en")
    assert g.restore("see qz 0 q and QZ1Q", mapping) == "see SharePoint and Ford"


def test_sin_terminos_no_hay_marcas():
    masked, mapping = glos().protect("una frase cualquiera", "en")
    assert masked == "una frase cualquiera" and mapping == {}


def test_un_termino_no_se_confunde_con_parte_de_otra_palabra():
    masked, mapping = glos().protect("la SAPiencia y el Fordismo", "en")
    assert mapping == {} and masked == "la SAPiencia y el Fordismo"


# -- voz ---------------------------------------------------------------------------


def test_pronunciacion_para_la_voz_sintetica():
    g = glos()
    assert g.speakable("Open SharePoint and the SAP and KPI") == "Open Share Point and the S A P and K P I"
    assert g.speakable("Ford has no spoken form") == "Ford has no spoken form"


# -- archivo ------------------------------------------------------------------------


def test_guardar_y_cargar(tmp_path):
    ruta = tmp_path / "g" / "glossary.json"
    glos().save(ruta)
    g2 = Glossary.load(ruta)
    assert [e.term for e in g2.entries] == [e.term for e in glos().entries]
    assert g2.entries[-1].translations == {"en": "container yard", "pt": "pátio de contêineres"}
    assert g2.correct("en un fort")[0] == "en un Ford"
    assert json.loads(ruta.read_text(encoding="utf-8"))["version"] == 1


def test_sin_archivo_se_usan_los_terminos_de_ejemplo(tmp_path):
    g = Glossary.load(tmp_path / "no_existe.json")
    assert len(g) > 5 and "SharePoint" in [e.term for e in g.entries]
    assert not (tmp_path / "no_existe.json").exists()  # cargar no crea nada


def test_un_archivo_roto_no_impide_arrancar(tmp_path):
    ruta = tmp_path / "roto.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    assert len(Glossary.load(ruta)) > 5
    ruta.write_text(json.dumps({"entries": [{"campo_raro": 1}]}), encoding="utf-8")
    assert len(Glossary.load(ruta)) > 5


def test_los_ejemplos_corrigen_el_caso_real_del_usuario():
    g = Glossary.defaults()
    assert g.correct("Les dimos conexiones a las listas de Sherpoin.")[0].endswith("SharePoint.")
    assert g.correct("la inteligencia artificial que tenemos hoy en un fort.")[0].endswith("en un Ford.")


def test_corrige_un_termino_pegado_a_una_palabra_corta():
    g = glos()
    assert (
        g.correct("Subí el archivo asarePoint para el equipo")[0]
        == "Subí el archivo a SharePoint para el equipo"
    )
    assert g.correct("la lista deSherpoin")[0] == "la lista de SharePoint"


def test_no_toca_palabras_largas_que_empiezan_con_una_preposicion():
    for frase in ("enseñanza deportiva", "alumbrado público", "elefante grande", "ayudante", "oficial"):
        assert glos().correct(frase) == (frase, [])
