import pytest

from interpreter.phrasebook import lookup, normalize
from interpreter.stages.backends import clean_translation, is_known_hallucination


@pytest.mark.parametrize(
    "texto,origen,destino,esperado",
    [
        ("Thanks.", "en", "es", "Gracias."),  # el caso real: el modelo decía «Gracias. - ¿Qué?»
        ("Thank you.", "en", "es", "Gracias."),
        ("Hello.", "en", "es", "Hola."),  # el modelo decía «¿Qué pasa?»
        ("Bye.", "en", "es", "Adiós."),
        ("Yes.", "en", "es", "Sí."),
        ("Sí.", "es", "en", "Yes."),  # el modelo decía «Yes, you did.»
        ("No.", "es", "en", "No."),  # el modelo decía «No, I did not.»
        ("¡Gracias!", "es", "en", "Thank you."),
        ("Muchas gracias", "es", "en", "Thank you very much."),
        ("Buenos días.", "es", "en", "Good morning."),
        ("  HOLA  ", "es", "fr", "Bonjour."),
        ("Un momento, por favor", "es", "en", None),  # frase compuesta: la traduce el modelo
        ("Gracias por venir.", "es", "en", None),
        ("Hello everyone.", "en", "es", None),
        ("", "es", "en", None),
    ],
)
def test_frases_cortas_exactas(texto, origen, destino, esperado):
    assert lookup(texto, origen, destino) == esperado


def test_todas_las_frases_tienen_salida_en_los_cuatro_idiomas():
    from interpreter import phrasebook

    for concept in phrasebook._CONCEPTS:
        assert set(concept) == {"es", "en", "pt", "fr"}
        for forms, say in concept.values():
            assert forms and say


def test_normalize_quita_tildes_signos_y_mayusculas():
    assert normalize("¿Qué?!") == "que"
    assert normalize("S'il vous plaît.") == "s'il vous plait"


@pytest.mark.parametrize(
    "traduccion,origen,esperado",
    [
        ("Thank you. - Thank you.", "Gracias.", "Thank you."),
        ("What? - What?", "¿Qué?", "What?"),
        ("Gracias. - ¿Qué?", "Thanks.", "Gracias."),
        ("- Bye, then.", "Chao.", "Bye, then."),  # guion inicial
        ("Hello. - Hi!", "Hola. - Hola", "Hello. - Hi!"),  # el original sí lleva diálogo: se respeta
        ("Esto es 1 - 2 y sigue.", "This is 1 - 2.", "Esto es 1 - 2 y sigue."),
        ("Una sola frase.", "One sentence.", "Una sola frase."),
    ],
)
def test_limpia_el_dialogo_inventado_por_el_traductor(traduccion, origen, esperado):
    assert clean_translation(traduccion, origen) == esperado


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("Thanks for watching.", True),
        ("¡Gracias por ver el vídeo!", True),
        ("Subtítulos realizados por la comunidad de Amara.org", True),
        ("Please subscribe to my channel", True),
        ("Thanks.", False),  # puede ser real
        ("Gracias por venir a la reunión.", False),
    ],
)
def test_frases_que_whisper_inventa_con_ruido(texto, esperado):
    assert is_known_hallucination(texto) is esperado


def test_el_traductor_no_se_usa_con_frases_del_diccionario():
    from interpreter.stages.base import MTStage
    from interpreter.stages.fakes import FakeMT

    visto: list[str] = []

    class Mt(FakeMT):
        def translate(self, text, source, target):
            visto.append(text)
            return "Gracias, señor."  # lo que inventaba el modelo

    stage = MTStage(Mt(), "es")
    assert stage._translate("Thanks.", "en", "es") == "Gracias."
    assert visto == []
    assert stage._translate("Thanks for the update.", "en", "es") == "Gracias, señor."  # esta sí va al modelo
    assert visto == ["Thanks for the update."]
