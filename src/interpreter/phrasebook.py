"""Frases cortas de uso diario («Gracias», «Sí», «Hola», «Un momento») traducidas SIN el modelo.

El traductor neuronal (NLLB) falla con enunciados de una o dos palabras porque no tiene contexto:
«Hello.» -> «¿Qué pasa?», «Thanks.» -> «Gracias, señor.», «Sí.» -> «Yes, you did.»,
«Gracias.» -> «Thank you. - Thank you.». Son justo las frases que más se dicen en una reunión, así que se
resuelven con un diccionario exacto: respuesta correcta, sin errores y sin esperar al modelo.

Cada concepto tiene las formas aceptadas al ESCUCHAR en cada idioma y la forma al HABLAR (la de ``say``).
Solo coincide si TODO el enunciado es esa frase (sin signos, mayúsculas ni tildes): «Gracias por venir» no.
"""

from __future__ import annotations

import re
import unicodedata

# concepto -> idioma -> (formas que se reconocen, cómo se dice)
_CONCEPTS: list[dict[str, tuple[list[str], str]]] = [
    {  # gracias
        "es": (["gracias"], "Gracias."),
        "en": (["thanks", "thank you", "thank u", "thx"], "Thank you."),
        "pt": (["obrigado", "obrigada", "valeu"], "Obrigado."),
        "fr": (["merci"], "Merci."),
    },
    {  # muchas gracias
        "es": (["muchas gracias", "muchisimas gracias", "mil gracias"], "Muchas gracias."),
        "en": (
            ["thank you very much", "thanks a lot", "many thanks", "thanks so much"],
            "Thank you very much.",
        ),
        "pt": (["muito obrigado", "muito obrigada", "obrigadao"], "Muito obrigado."),
        "fr": (["merci beaucoup", "merci bien"], "Merci beaucoup."),
    },
    {  # de nada
        "es": (["de nada", "no hay de que"], "De nada."),
        "en": (["you're welcome", "you are welcome", "youre welcome"], "You're welcome."),
        "pt": (["de nada", "por nada"], "De nada."),
        "fr": (["de rien", "je vous en prie"], "De rien."),
    },
    {  # sí
        "es": (["si", "sip"], "Sí."),
        "en": (["yes", "yeah", "yep", "yup"], "Yes."),
        "pt": (["sim"], "Sim."),
        "fr": (["oui"], "Oui."),
    },
    {  # no
        "es": (["no", "nop"], "No."),
        "en": (["no", "nope"], "No."),
        "pt": (["nao"], "Não."),
        "fr": (["non"], "Non."),
    },
    {  # de acuerdo
        "es": (["vale", "ok", "okay", "de acuerdo", "esta bien", "listo"], "De acuerdo."),
        "en": (["ok", "okay", "all right", "alright", "fine", "sounds good"], "Okay."),
        "pt": (["ok", "okay", "tudo bem", "certo", "ta bom", "combinado"], "Tudo bem."),
        "fr": (["d'accord", "ok", "okay", "entendu", "ca marche"], "D'accord."),
    },
    {  # hola
        "es": (["hola", "holaa"], "Hola."),
        "en": (["hello", "hi", "hey", "hiya"], "Hello."),
        "pt": (["ola", "oi"], "Olá."),
        "fr": (["bonjour", "salut", "allo"], "Bonjour."),
    },
    {  # buenos días
        "es": (["buenos dias", "buen dia", "buenas"], "Buenos días."),
        "en": (["good morning", "morning"], "Good morning."),
        "pt": (["bom dia"], "Bom dia."),
        "fr": (["bon matin"], "Bonjour."),
    },
    {  # buenas tardes
        "es": (["buenas tardes"], "Buenas tardes."),
        "en": (["good afternoon"], "Good afternoon."),
        "pt": (["boa tarde"], "Boa tarde."),
        "fr": (["bon apres-midi", "bon apres midi"], "Bon après-midi."),
    },
    {  # adiós
        "es": (["adios", "chau", "chao", "hasta la vista"], "Adiós."),
        "en": (["bye", "goodbye", "good bye", "bye bye", "bye-bye"], "Goodbye."),
        "pt": (["tchau", "adeus"], "Tchau."),
        "fr": (["au revoir", "adieu"], "Au revoir."),
    },
    {  # hasta luego
        "es": (["hasta luego", "hasta pronto", "nos vemos", "hasta mañana", "hasta manana"], "Hasta luego."),
        "en": (["see you later", "see you", "see you soon", "talk to you later"], "See you later."),
        "pt": (["ate logo", "ate mais", "ate breve"], "Até logo."),
        "fr": (["a bientot", "a plus tard", "a plus", "a tout a l'heure"], "À bientôt."),
    },
    {  # por favor
        "es": (["por favor", "porfa", "porfavor"], "Por favor."),
        "en": (["please"], "Please."),
        "pt": (["por favor", "por gentileza"], "Por favor."),
        "fr": (["s'il vous plait", "s'il te plait", "svp"], "S'il vous plaît."),
    },
    {  # lo siento
        "es": (["lo siento", "perdon", "perdona", "perdone"], "Lo siento."),
        "en": (["sorry", "i'm sorry", "im sorry", "i am sorry", "my bad"], "I'm sorry."),
        "pt": (["desculpa", "desculpe", "sinto muito", "perdao"], "Desculpe."),
        "fr": (["desole", "desolee", "pardon", "je suis desole"], "Désolé."),
    },
    {  # disculpa (para llamar la atención)
        "es": (["disculpa", "disculpe", "con permiso", "permiso"], "Disculpe."),
        "en": (["excuse me", "pardon me"], "Excuse me."),
        "pt": (["com licenca", "licenca"], "Com licença."),
        "fr": (["excusez-moi", "excuse-moi", "excusez moi"], "Excusez-moi."),
    },
    {  # bienvenido
        "es": (["bienvenido", "bienvenida", "bienvenidos", "bienvenidas"], "Bienvenido."),
        "en": (["welcome"], "Welcome."),
        "pt": (["bem-vindo", "bem vindo", "bem-vinda", "bem-vindos", "bem vindos"], "Bem-vindo."),
        "fr": (["bienvenue"], "Bienvenue."),
    },
    {  # claro
        "es": (["claro", "por supuesto", "claro que si", "desde luego"], "Claro."),
        "en": (["of course", "sure", "certainly", "absolutely", "definitely"], "Of course."),
        "pt": (["claro", "com certeza", "e claro", "certamente", "lógico", "logico"], "Claro."),
        "fr": (["bien sur", "evidemment", "certainement", "absolument"], "Bien sûr."),
    },
    {  # entendido
        "es": (["entendido", "entiendo", "comprendido", "ya entendi", "entendi"], "Entendido."),
        "en": (["understood", "i understand", "got it", "i see", "i got it"], "Understood."),
        "pt": (["entendido", "entendi", "compreendo", "compreendido", "saquei"], "Entendido."),
        "fr": (["compris", "j'ai compris", "je comprends", "je vois"], "Compris."),
    },
    {  # un momento
        "es": (["un momento", "un momentito", "un segundo", "espera", "esperame", "espere"], "Un momento."),
        "en": (
            ["one moment", "just a moment", "one second", "hold on", "wait", "just a second"],
            "One moment.",
        ),
        "pt": (["um momento", "um segundo", "espera", "aguarde", "so um minuto"], "Um momento."),
        "fr": (["un instant", "un moment", "une seconde", "attendez", "attends"], "Un instant."),
    },
    {  # perfecto
        "es": (["perfecto", "perfecta"], "Perfecto."),
        "en": (["perfect"], "Perfect."),
        "pt": (["perfeito", "perfeita"], "Perfeito."),
        "fr": (["parfait", "parfaite"], "Parfait."),
    },
    {  # genial
        "es": (["genial", "excelente", "fantastico", "buenisimo", "buenisima"], "Genial."),
        "en": (["great", "excellent", "awesome", "fantastic", "wonderful"], "Great."),
        "pt": (["otimo", "excelente", "fantastico", "maravilha"], "Ótimo."),
        "fr": (["genial", "excellent", "super", "formidable"], "Génial."),
    },
    {  # ¿qué?
        "es": (["que", "como"], "¿Qué?"),
        "en": (["what", "pardon", "come again"], "What?"),
        "pt": (["o que", "como"], "O quê?"),
        "fr": (["quoi", "comment", "pardon"], "Quoi ?"),
    },
    {  # ¿verdad?
        "es": (["de verdad", "en serio", "de veras"], "¿En serio?"),
        "en": (["really", "seriously", "for real", "no way"], "Really?"),
        "pt": (["serio", "de verdade", "mesmo", "sério"], "Sério?"),
        "fr": (["vraiment", "sérieusement", "serieusement", "pour de vrai"], "Vraiment ?"),
    },
]

_INDEX: dict[tuple[str, str], dict[str, str]] = {}  # (idioma, forma normalizada) -> {idioma destino: texto}


def normalize(text: str) -> str:
    """Minúsculas, sin tildes, sin signos de puntuación ni espacios repetidos (se conservan ``'`` y ``-``)."""
    s = unicodedata.normalize("NFKD", text.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s'\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -'")


def _build() -> None:
    for concept in _CONCEPTS:
        outputs = {lang: say for lang, (_, say) in concept.items()}
        for lang, (forms, _) in concept.items():
            for form in forms:
                _INDEX.setdefault((lang, normalize(form)), outputs)


_build()


def lookup(text: str, source: str, target: str) -> str | None:
    """Traducción exacta si TODO ``text`` es una frase del diccionario; ``None`` si no (usar el traductor)."""
    if not text or len(text) > 40:
        return None
    outputs = _INDEX.get((source, normalize(text)))
    return outputs.get(target) if outputs else None
