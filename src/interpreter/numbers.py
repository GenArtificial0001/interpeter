# ruff: noqa: SIM905
"""Cifras a palabras para la voz sintética.

Los motores de voz (MMS-TTS, YourTTS, XTTS) no saben leer ``1, 2, 3``: descartan los símbolos que no están
en su alfabeto y la frase sale mutilada («The numbers are and»). El traductor, en cambio, devuelve cifras.
Antes de sintetizar se escriben con letras en el idioma de salida: enteros, miles, decimales, porcentajes,
ordinales, años, importes, horas, rangos y números largos (teléfonos) dígito a dígito.

Usa ``num2words`` si está instalado; si no, lee cada cifra por separado (algo se dice, nunca se omite).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

log = logging.getLogger(__name__)

try:  # opcional: viene con coqui-tts; sin él se deletrean las cifras una a una
    from num2words import num2words as _num2words
except ImportError:  # pragma: no cover - depende del entorno
    _num2words = None

LANG_CODES = {"en": "en", "fr": "fr", "pt": "pt_BR", "es": "es"}
DIGITS = {
    "en": ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
    "fr": ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf"],
    "pt": ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove"],
    "es": ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"],
}
WORDS = {  # palabras sueltas que acompañan a las cifras
    "en": {
        "percent": "percent",
        "point": "point",
        "minus": "minus",
        "to": "to",
        "dollars": "dollars",
        "euros": "euros",
        "reais": "reais",
    },
    "fr": {
        "percent": "pour cent",
        "point": "virgule",
        "minus": "moins",
        "to": "à",
        "dollars": "dollars",
        "euros": "euros",
        "reais": "reais",
    },
    "pt": {
        "percent": "por cento",
        "point": "vírgula",
        "minus": "menos",
        "to": "a",
        "dollars": "dólares",
        "euros": "euros",
        "reais": "reais",
    },
    "es": {
        "percent": "por ciento",
        "point": "coma",
        "minus": "menos",
        "to": "a",
        "dollars": "dólares",
        "euros": "euros",
        "reais": "reales",
    },
}
THOUSANDS_SEP = {"en": ",", "fr": " ", "pt": ".", "es": "."}
MAX_DIGITS = 15  # num2words llega mucho más lejos, pero un número así es un identificador, no una cantidad
LONG_ID_DIGITS = 9  # sin separadores y con tantas cifras o más: teléfono/ID, se lee dígito a dígito

_NUM = r"\d+(?:[.,]\d+)*"
_NOT_WORD = r"(?<![\w])"  # no pegado a una letra por la izquierda («B2B», «A4»)
_AFTER = r"(?![\w])"


def _spell_digits(digits: str, lang: str) -> str:
    table = DIGITS.get(lang, DIGITS["en"])
    return " ".join(table[int(c)] for c in digits if c.isdigit())


def _cardinal(value: int | float | str, lang: str, **kw: object) -> str:
    code = LANG_CODES.get(lang)
    if _num2words is not None and code is not None:
        try:
            return str(_num2words(value, lang=code, **kw)).replace("-", " ")
        except (NotImplementedError, OverflowError, ValueError, TypeError):
            log.debug("num2words no pudo leer %r en %s", value, lang)
    return _spell_digits(str(value), lang)


def _parse(token: str, lang: str) -> int | float | list[int] | None:
    """Interpreta ``1,234``, ``1.5``, ``1.234,5``, ``1,2,3``… según el idioma. ``None`` si no es un número."""
    if re.fullmatch(r"\d+", token):
        return int(token)
    seps = re.findall(r"[.,]", token)
    thousands = THOUSANDS_SEP.get(lang, ",")
    # Miles bien formados: grupos de exactamente 3 cifras con el mismo separador.
    grouped = len(set(seps)) == 1 and re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token)
    if grouped and (seps[0] == thousands or len(seps) > 1):
        return int(re.sub(r"[.,]", "", token))
    # Formato europeo con decimales: 1.234,5 / formato inglés: 1,234.5
    if len(set(seps)) == 2:
        dec = seps[-1]
        whole, _, frac = token.rpartition(dec)
        whole = re.sub(r"[.,]", "", whole)
        return float(f"{whole}.{frac}") if whole.isdigit() and frac.isdigit() else None
    if len(seps) == 1:
        whole, frac = re.split(r"[.,]", token)
        return float(f"{whole}.{frac}")
    # «1,2,3» sin espacios: una lista pegada.
    return [int(p) for p in re.split(r"[.,]", token) if p]


def _read_number(token: str, lang: str, year_ok: bool = False) -> str:
    plain = re.sub(r"[.,]", "", token)
    if len(plain) > MAX_DIGITS or (len(plain) >= LONG_ID_DIGITS and token.isdigit()):
        return _spell_digits(plain, lang)
    parsed = _parse(token, lang)
    if parsed is None:
        return _spell_digits(plain, lang)
    if isinstance(parsed, list):
        return " ".join(_cardinal(n, lang) for n in parsed)
    if isinstance(parsed, float):
        whole, frac = str(int(parsed)), re.split(r"[.,]", token)[-1]
        point = WORDS.get(lang, WORDS["en"])["point"]
        # 2.50 -> two point five zero (los decimales se leen cifra a cifra)
        return f"{_cardinal(int(whole), lang)} {point} {_spell_digits(frac, lang)}"
    if year_ok and lang == "en" and re.fullmatch(r"\d{4}", token) and 1100 <= parsed <= 2099:
        return _cardinal(parsed, lang, to="year")  # 2026 -> twenty twenty-six
    return _cardinal(parsed, lang)


def _currency(lang: str) -> list[tuple[str, str]]:
    return [(r"R\$", "reais"), (r"\$", "dollars"), (r"€", "euros")]


def spell_numbers(text: str, lang: str) -> str:
    """Devuelve ``text`` con cada cifra escrita en palabras en ``lang`` (``en``, ``fr``, ``pt``, ``es``)."""
    if not re.search(r"\d", text):
        return text
    w = WORDS.get(lang, WORDS["en"])

    # Importes: $5 -> five dollars
    for symbol, key in _currency(lang):
        text = re.sub(
            rf"{symbol}\s?({_NUM})", lambda m, key=key: f"{_read_number(m.group(1), lang)} {w[key]}", text
        )

    # Horas: 10:30 -> ten thirty
    def clock(m: re.Match[str]) -> str:
        h, mi = int(m.group(1)), int(m.group(2))
        if mi > 59 or h > 24:
            return m.group(0)
        hour, minutes = _cardinal(h, lang), _cardinal(mi, lang)
        if lang == "en":
            return (
                f"{hour} o'clock" if mi == 0 else f"{hour} oh {minutes}" if mi < 10 else f"{hour} {minutes}"
            )
        if lang == "fr":
            return f"{hour} heures" + ("" if mi == 0 else f" {minutes}")
        if lang == "pt":
            return f"{hour} horas" + ("" if mi == 0 else f" e {minutes}")
        return f"{hour} en punto" if mi == 0 else f"{hour} y {minutes}"

    text = re.sub(rf"{_NOT_WORD}(\d{{1,2}}):(\d{{2}}){_AFTER}", clock, text)
    # Porcentajes: 50% -> fifty percent
    text = re.sub(rf"({_NUM})\s?%", lambda m: f"{_read_number(m.group(1), lang)} {w['percent']}", text)
    # Ordinales: 1st, 2nd, 3rd, 4th / 1er, 2e, 3ème / 1º, 2ª
    text = re.sub(
        rf"{_NOT_WORD}(\d+)(?:st|nd|rd|th|er|re|ème|eme|e|º|ª|°){_AFTER}",
        lambda m: _cardinal(int(m.group(1)), lang, to="ordinal"),
        text,
        flags=re.IGNORECASE,
    )
    # Rangos: 1-5 -> one to five
    text = re.sub(
        rf"{_NOT_WORD}(\d+)\s?[–-]\s?(\d+){_AFTER}", lambda m: f"{m.group(1)} {w['to']} {m.group(2)}", text
    )
    # Negativos: -5 -> minus five
    text = re.sub(r"(?<![\w\d])-(?=\d)", f"{w['minus']} ", text)
    # Resto de números
    reader: Callable[[re.Match[str]], str] = lambda m: _read_number(m.group(0), lang, year_ok=True)  # noqa: E731
    text = re.sub(rf"{_NOT_WORD}{_NUM}{_AFTER}", reader, text)
    return re.sub(r"\s{2,}", " ", text).strip()


# -- reconocer frases que son SOLO números (entrada) ------------------------------------------------------

# fmt: off
_NUMBER_WORDS = {
    "es": (
        "cero uno una dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince "
        "dieciseis diecisiete dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro "
        "veinticinco veintiseis veintisiete veintiocho veintinueve treinta cuarenta cincuenta sesenta "
        "setenta ochenta noventa cien ciento doscientos trescientos cuatrocientos quinientos seiscientos "
        "setecientos ochocientos novecientos mil millon millones y"
    ).split(),
    "en": (
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety "
        "hundred thousand million and"
    ).split(),
    "pt": (
        "zero um uma dois duas tres quatro cinco seis sete oito nove dez onze doze treze catorze quatorze "
        "quinze dezesseis dezessete dezoito dezenove vinte trinta quarenta cinquenta sessenta setenta "
        "oitenta noventa cem cento duzentos trezentos quatrocentos quinhentos seiscentos setecentos "
        "oitocentos novecentos mil milhao milhoes e"
    ).split(),
    "fr": (
        "zero un une deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze "
        "seize vingt trente quarante cinquante soixante cent mille million millions et"
    ).split(),
}
# fmt: on


def is_numeric_phrase(text: str, lang: str) -> bool:
    """¿La frase son solo números (cifras o palabras: «4, 5, 6», «uno», «cuarenta y dos»)?

    Una palabra suelta como «dos» no se distingue de otro idioma (Whisper da menos de 0,15 de probabilidad de
    que sea español) y el filtro de idioma la descartaría aunque se haya entendido perfectamente.
    """
    import unicodedata

    plain = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    tokens = re.findall(r"\d+(?:[.,]\d+)*|[^\W\d_]+", plain)
    if not tokens:
        return False
    words = set(_NUMBER_WORDS.get(lang, ()))
    has_number = False
    for t in tokens:
        if t[0].isdigit() or (t in words and t not in {"y", "e", "et", "and"}):
            has_number = True
        elif t not in words:
            return False
    return has_number


def is_digits_only(text: str) -> bool:
    """¿Solo cifras y signos («11.23.53.22.», «4, 5, 6»)? El traductor inventa frases con eso."""
    return bool(re.search(r"\d", text)) and not re.search(r"[^\W\d_]", text)
