import pytest

from interpreter import numbers
from interpreter.numbers import spell_numbers

n2w = pytest.mark.skipif(numbers._num2words is None, reason="num2words no está instalado")


def test_sin_cifras_no_cambia_nada():
    assert spell_numbers("Hello, world.", "en") == "Hello, world."


@n2w
@pytest.mark.parametrize(
    "texto,lang,esperado",
    [
        # el caso real que se oyó mutilado: «The numbers are and»
        (
            "The numbers are 1, 2, 3, 4, 5 and 6.",
            "en",
            "The numbers are one, two, three, four, five and six.",
        ),
        ("1,234 units", "en", "one thousand, two hundred and thirty four units"),
        ("50% off", "en", "fifty percent off"),
        ("3.14 and 2.50", "en", "three point one four and two point five zero"),
        ("in 2026", "en", "in twenty twenty six"),
        ("the 1st and 22nd", "en", "the first and twenty second"),
        ("at 10:30, 9:05 and 8:00", "en", "at ten thirty, nine oh five and eight o'clock"),
        ("from 1-5", "en", "from one to five"),
        ("-5 degrees and $20", "en", "minus five degrees and twenty dollars"),
        ("B2B and A4 stay, ok 7", "en", "B2B and A4 stay, ok seven"),
        (
            "Os números são 1, 2 e 3, 15% e 1.500",
            "pt",
            "Os números são um, dois e três, quinze por cento e mil e quinhentos",
        ),
        ("1,5 e 2,05 do 1º", "pt", "um vírgula cinco e dois vírgula zero cinco do primeiro"),
        (
            "Les chiffres sont 1, 2 et 3 à 10:30",
            "fr",
            "Les chiffres sont un, deux et trois à dix heures trente",
        ),
    ],
)
def test_cifras_a_palabras(texto, lang, esperado):
    assert spell_numbers(texto, lang) == esperado


@n2w
def test_un_telefono_o_id_largo_se_lee_cifra_a_cifra():
    assert spell_numbers("Call 549115555", "en") == "Call five four nine one one five five five five"
    largo = spell_numbers("ID 5491155551234", "en")
    assert largo.startswith("ID five four nine") and "thousand" not in largo


def test_sin_num2words_se_deletrean_las_cifras(monkeypatch):
    monkeypatch.setattr(numbers, "_num2words", None)
    assert spell_numbers("are 1, 2 and 10", "en") == "are one, two and one zero"


def test_la_voz_recibe_las_cifras_ya_escritas():
    from interpreter.messages import Translation
    from interpreter.stages.base import TTSStage
    from interpreter.stages.fakes import FakeTTS

    class Rec(FakeTTS):
        spoken: list[str] = []

        def synthesize(self, text, language):
            self.spoken.append(text)
            yield from super().synthesize(text, language)

    tts = Rec(chunks=1)
    tl = Translation("The numbers are 1, 2, 3.", "es", "en")
    list(TTSStage(tts).process(tl))
    assert "1" not in tts.spoken[0] and "one" in tts.spoken[0]


@pytest.mark.parametrize(
    "texto,lang,esperado",
    [
        ("Uno.", "es", True),
        ("¡Cuatro!", "es", True),
        ("4, 5, 6.", "es", True),
        ("Cuarenta y dos", "es", True),
        ("treinta", "es", True),
        ("Veintiséis", "es", True),
        ("seven, eight", "en", True),
        ("quatre-vingt", "fr", True),
        ("Voy a listarte los números.", "es", False),
        ("Uno de los mejores", "es", False),
        ("Glos.", "es", False),
        ("y", "es", False),
        ("", "es", False),
    ],
)
def test_frases_que_son_solo_numeros(texto, lang, esperado):
    from interpreter.numbers import is_numeric_phrase

    assert is_numeric_phrase(texto, lang) is esperado


def test_solo_cifras_no_pasan_por_el_traductor_y_la_voz_las_lee():
    from interpreter.numbers import is_digits_only
    from interpreter.pipeline import Pipeline
    from interpreter.stages.fakes import FakeMT, FakeSTT, FakeTTS

    assert is_digits_only("11.23.53.22.") and is_digits_only("4, 5, 6") and not is_digits_only("cuatro 5")
    assert not is_digits_only("hola")

    visto: list[str] = []
    traducciones: list[str] = []

    class Mt(FakeMT):
        def translate(self, text, source, target):
            visto.append(text)
            return "The Commission has not yet adopted a decision."  # lo que inventaba NLLB

    p = Pipeline(
        FakeSTT("11.23.53.22."), Mt(), FakeTTS(chunks=1), "es", "en", on_translation=traducciones.append
    )
    p.start()
    try:
        import numpy as np

        from interpreter.messages import SpeechSegment

        p.submit(SpeechSegment(np.zeros(16000, dtype=np.float32)))
        import time

        end = time.time() + 3
        while not traducciones and time.time() < end:
            time.sleep(0.02)
    finally:
        p.stop()
    assert visto == [] and traducciones == ["11.23.53.22."]
