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


@n2w
@pytest.mark.parametrize(
    "texto,lang,esperado",
    [
        (
            "Die Zahlen sind 1, 2 und 3, 50% um 10:30",
            "de",
            "Die Zahlen sind eins, zwei und drei, fünfzig Prozent um zehn Uhr dreißig",
        ),
        (
            "1.234 Einheiten und 3,5",
            "de",
            "eintausendzweihundertvierunddreißig Einheiten und drei Komma fünf",
        ),
        ("现在是 3 点", "zh", "现在是 三 点"),
        ("1、2", "ja", "一、二"),
    ],
)
def test_aleman_chino_y_japones(texto, lang, esperado):
    assert spell_numbers(texto, lang) == esperado
