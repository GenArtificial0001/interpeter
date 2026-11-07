"""Glosario: términos que se dicen mezclando idiomas (SharePoint, Ford, SAP, KPI…).

Un reconocedor entrenado en español «oye» mal las palabras en inglés («Sherpoin», «fort»), y un traductor
puede estropearlas o traducirlas. El glosario resuelve cuatro cosas, cada una en su etapa:

1. **Reconocimiento (STT):** ``initial_prompt()`` sesga a Whisper hacia esos términos y ``correct()`` arregla
   lo que aun así «oyó» mal (variantes explícitas y, en términos largos, parecido de escritura).
2. **Traducción (MT):** ``protect()``/``restore()`` esconden el término tras una marca que el traductor
   copia sin tocar (medido con NLLB: 4/4) y ponen una traducción fija si la entrada la tiene.
3. **Voz (TTS):** ``speakable()`` sustituye el término por cómo debe sonar (``SAP`` -> ``S A P``).
4. **Filtro de idioma:** una frase con términos del glosario no se descarta como «idioma distinto» solo
   porque lleve palabras en inglés.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

WORD_RE = re.compile(r"[\w'’]+", re.UNICODE)
FUZZY_MIN_TERM_LEN = 8  # solo términos largos: con los cortos ("foro" ~ "Ford") habría falsos positivos
FUZZY_MIN_RATIO = 0.76
MAX_PROMPT_TERMS = 40
# Palabras cortas que Whisper a veces pega al término («a SharePoint» -> «asarePoint»).
GLUED_PREFIXES = frozenset({"a", "e", "o", "y", "de", "el", "la", "en", "un", "al"})
PLACEHOLDER = "QZ{n}Q"
_PLACEHOLDER_RE = re.compile(r"Q\s?Z\s?(\d+)\s?Q", re.IGNORECASE)


def norm(text: str) -> str:
    """Minúsculas, sin tildes ni signos: para comparar cómo suena, no cómo se escribe."""
    s = unicodedata.normalize("NFKD", text.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", "", s).strip()


@dataclass
class GlossaryEntry:
    term: str  # forma correcta, tal como debe escribirse: "SharePoint"
    aliases: list[str] = field(default_factory=list)  # cómo suele «oírlo» Whisper: "sherpoin", "ese a pe"
    spoken: str = ""  # cómo debe pronunciarlo la voz sintética: "S A P" (vacío = tal cual)
    translations: dict[str, str] = field(default_factory=dict)  # traducción fija por idioma destino
    fuzzy: bool = True  # permitir corrección por parecido (solo aplica a términos largos)


class Glossary:
    def __init__(self, entries: list[GlossaryEntry] | None = None) -> None:
        self.entries: list[GlossaryEntry] = list(entries or [])
        self._index: dict[str, GlossaryEntry] = {}
        self._max_words = 1
        self.rebuild()

    # -- construcción ----------------------------------------------------------------------------

    def rebuild(self) -> None:
        """Recalcula el índice de variantes (llamar tras editar ``entries``)."""
        index: dict[str, GlossaryEntry] = {}
        max_words = 1
        for e in self.entries:
            for form in [e.term, *e.aliases]:
                key = norm(form)
                if key:
                    index.setdefault(key, e)
                    max_words = max(max_words, len(key.split()))
        # Se publican de una vez: otro hilo (reconocimiento) puede estar leyendo mientras se edita.
        self._index, self._max_words = index, max_words

    def replace_with(self, other: Glossary) -> None:
        """Toma el contenido de ``other`` conservando este objeto (las etapas ya lo referencian)."""
        self.entries = list(other.entries)
        self.rebuild()

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    @classmethod
    def defaults(cls) -> Glossary:
        return cls(
            [
                GlossaryEntry(
                    "SharePoint",
                    ["sherpoin", "sharepoin", "sherpoint", "share point", "charpoint"],
                    "Share Point",
                ),
                GlossaryEntry("Ford", ["fort", "forth", "ford motor"]),
                GlossaryEntry("Teams", ["tims", "tiims", "timz"]),
                GlossaryEntry("Excel", ["ecsel", "exel"]),
                GlossaryEntry("Outlook", ["autluk", "aut luk"]),
                GlossaryEntry("PowerPoint", ["power point", "pauer point"]),
                GlossaryEntry("OneDrive", ["one drive", "guan drive"]),
                GlossaryEntry("Power BI", ["pauer bi", "power bi"]),
                GlossaryEntry("SAP", ["ese a pe", "esape"], "S A P"),
                GlossaryEntry("EWM", ["e doble u eme"], "E W M"),
                GlossaryEntry("WMS", ["doble u eme ese"], "W M S"),
                GlossaryEntry("KPI", ["ka pi ai", "kapiai", "ká pi ái"], "K P I"),
                GlossaryEntry("ERP", ["e erre pe"], "E R P"),
                GlossaryEntry("API", ["a pe i"], "A P I"),
                GlossaryEntry("BigQuery", ["big query", "bigcuery"]),
                GlossaryEntry("Python", ["paiton", "pitón"]),
            ]
        )

    @classmethod
    def load(cls, path: Path) -> Glossary:
        """Lee el JSON del usuario; si no existe, usa los términos de ejemplo (sin crear el archivo)."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls([GlossaryEntry(**e) for e in data.get("entries", [])])
        except FileNotFoundError:
            return cls.defaults()
        except Exception:  # noqa: BLE001 - un archivo roto no debe impedir arrancar
            log.warning(
                "No se pudo leer el glosario %s; se usan los términos de ejemplo", path, exc_info=True
            )
            return cls.defaults()

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": [asdict(e) for e in self.entries]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # -- reconocimiento ----------------------------------------------------------------------------

    def initial_prompt(self) -> str:
        """Texto para sesgar a Whisper hacia los términos (``prompt_ids``). Vacío si no hay glosario."""
        terms = [e.term for e in self.entries[:MAX_PROMPT_TERMS]]
        return f"Glosario: {', '.join(terms)}." if terms else ""

    def correct(self, text: str) -> tuple[str, list[str]]:
        """Arregla cómo se «oyeron» los términos. Devuelve ``(texto, términos encontrados)``."""
        if not self.entries or not text:
            return text, []
        words = list(WORD_RE.finditer(text))
        keys = [norm(w.group()) for w in words]
        out: list[str] = []
        found: list[str] = []
        pos = 0
        i = 0
        while i < len(words):
            entry, n = self._match_at(keys, i)
            glued = 0
            if entry is None:
                entry, n = self._fuzzy_at(keys, i)
            if entry is None:
                entry, glued = self._glued_at(keys, i)
                n = 1
            if entry is None:
                i += 1
                continue
            start, end = words[i].start(), words[i + n - 1].end()
            out.append(text[pos:start])
            if glued:
                out.append(words[i].group()[:glued] + " ")  # la palabra pegada («a») se conserva
            out.append(entry.term)
            pos = end
            if entry.term not in found:
                found.append(entry.term)
            i += n
        out.append(text[pos:])
        return "".join(out), found

    def _match_at(self, keys: list[str], i: int) -> tuple[GlossaryEntry | None, int]:
        for n in range(min(self._max_words, len(keys) - i), 0, -1):
            entry = self._index.get(" ".join(keys[i : i + n]))
            if entry is not None:
                return entry, n
        return None, 0

    def _fuzzy_at(self, keys: list[str], i: int) -> tuple[GlossaryEntry | None, int]:
        tok = keys[i]
        if len(tok) < 5:
            return None, 0
        best: tuple[float, GlossaryEntry | None] = (0.0, None)
        for e in self.entries:
            term = norm(e.term).replace(" ", "")
            if (
                not e.fuzzy
                or len(term) < FUZZY_MIN_TERM_LEN
                or tok[0] != term[0]
                or abs(len(tok) - len(term)) > 3
            ):
                continue
            ratio = difflib.SequenceMatcher(None, tok, term).ratio()
            if ratio >= FUZZY_MIN_RATIO and ratio > best[0]:
                best = (ratio, e)
        return (best[1], 1) if best[1] is not None else (None, 0)

    def _glued_at(self, keys: list[str], i: int) -> tuple[GlossaryEntry | None, int]:
        """Término pegado a una palabra corta: «asarepoint» = «a» + «SharePoint». Da (entrada, largo)."""
        tok = keys[i]
        for k in (1, 2):
            if len(tok) - k >= 6 and tok[:k] in GLUED_PREFIXES:
                entry, _ = self._fuzzy_at([tok[k:]], 0)
                if entry is not None:
                    return entry, k
        return None, 0

    def contains_term(self, text: str) -> bool:
        return bool(self.correct(text)[1])

    # -- traducción --------------------------------------------------------------------------------

    def protect(self, text: str, target: str) -> tuple[str, dict[int, str]]:
        """Cambia cada término por una marca que el traductor no toca. ``mapping``: número -> texto final."""
        mapping: dict[int, str] = {}
        masked = text
        for e in sorted(self.entries, key=lambda x: -len(x.term)):
            pattern = re.compile(rf"(?<!\w){re.escape(e.term)}(?!\w)", re.IGNORECASE)
            replacement = e.translations.get(target) or e.term

            def sub(_m: re.Match[str], replacement: str = replacement) -> str:
                n = len(mapping)
                mapping[n] = replacement
                return PLACEHOLDER.format(n=n)

            masked = pattern.sub(sub, masked)
        return masked, mapping

    @staticmethod
    def restore(text: str, mapping: dict[int, str]) -> str | None:
        """Devuelve el texto con los términos puestos; ``None`` si el traductor perdió alguna marca."""
        seen: set[int] = set()

        def sub(m: re.Match[str]) -> str:
            n = int(m.group(1))
            if n in mapping:
                seen.add(n)
                return mapping[n]
            return m.group(0)

        restored = _PLACEHOLDER_RE.sub(sub, text)
        return restored if seen == set(mapping) else None

    # -- voz ---------------------------------------------------------------------------------------

    def speakable(self, text: str) -> str:
        """Texto para la voz sintética: cada término con su pronunciación (si tiene una)."""
        for e in self.entries:
            if e.spoken:
                text = re.sub(rf"(?<!\w){re.escape(e.term)}(?!\w)", e.spoken, text, flags=re.IGNORECASE)
        return text
