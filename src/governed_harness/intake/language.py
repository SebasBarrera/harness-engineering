"""The language of a task at intake: detection, tokenization and the Spanish vocabularies.

``intake.language`` (``auto``, ``en`` or ``es``) chooses the vocabularies and the question
texts of the deterministic INTENT rules. Without the key the rules keep their original form:
ASCII tokens, English vocabularies and English questions.

With the key, words are Unicode tokens (``contraseñas`` is one word, not ``contrase`` and
``as``) and are compared without diacritics, so ``rapido`` and ``rápido`` are the same word.

Detection (``auto``) is a fixed count, no language model: each word of the task (title,
intent, criteria and their hints) that is a Spanish function word, a common word of short
Spanish requests or a word of the Spanish vocabularies below counts for Spanish, each English function word counts for English, and
each ``ñ``, ``¿``, ``¡`` or accented vowel counts for Spanish. Words that are also English
(``a``, ``general``, ``once``, ...) count for neither. The task is Spanish when Spanish counts
more, otherwise English (a tie is English).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

Language = Literal["en", "es"]
"""A resolved intake language."""

LANGUAGE_SETTINGS = ("auto", "en", "es")
"""The values of ``intake.language``."""

# A word starts with a letter or digit (any script) and goes on with letters, digits, "_",
# "'" and "-": for ASCII text this is the original pattern.
UNICODE_WORD = re.compile(r"[^\W_][\w'-]*")

_SPANISH_MARKS = frozenset("ñ¿¡áéíóú")
_SPANISH_FUNCTION_WORDS = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "del",
        "al",
        "de",
        "en",
        "y",
        "que",
        "por",
        "para",
        "con",
        "un",
        "una",
        "unos",
        "unas",
        "es",
        "son",
        "se",
        "su",
        "sus",
        "lo",
        "como",
        "pero",
        "cuando",
        "debe",
        "deben",
        "esta",
        "este",
        "esto",
        "estos",
        "estas",
        "cada",
        "si",
        "sin",
        "muy",
        "ya",
        "hay",
        "todo",
        "todos",
    }
)
_ENGLISH_FUNCTION_WORDS = frozenset(
    {
        "the",
        "an",
        "and",
        "of",
        "to",
        "is",
        "are",
        "it",
        "that",
        "for",
        "with",
        "on",
        "be",
        "should",
        "must",
        "when",
        "this",
        "by",
        "from",
        "at",
        "or",
        "not",
        "if",
        "each",
        "will",
        "has",
        "have",
        "which",
        "its",
        "all",
        "any",
        "only",
        "into",
        "than",
    }
)


def fold(word: str) -> str:
    """A lower-case word without diacritics (``Contraseñas`` -> ``contrasenas``)."""
    decomposed = unicodedata.normalize("NFD", word.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def unicode_words(text: str) -> list[str]:
    """The folded Unicode words of a text."""
    return [fold(word) for word in UNICODE_WORD.findall(text.lower())]


def detect_language(text: str) -> Language:
    """``es`` when the text has more Spanish than English signals, otherwise ``en``."""
    lowered = text.lower()
    words = unicode_words(lowered)
    spanish = sum(1 for word in words if word in _SPANISH_SIGNALS)
    spanish += sum(1 for char in lowered if char in _SPANISH_MARKS)
    english = sum(1 for word in words if word in _ENGLISH_FUNCTION_WORDS)
    return "es" if spanish > english else "en"


# ----- Spanish vocabularies (folded: no diacritics) ---------------------------------------
# Applied together with the English ones, because Spanish tasks keep English code words.

VAGUE_PHRASES_ES = (
    "como se espera",
    "como se esperaba",
    "como es debido",
    "facil de usar",
    "amigable",
)
VAGUE_WORDS_ES = frozenset(
    {
        "funciona",
        "funcionan",
        "funcione",
        "funcionen",
        "funcionar",
        "funcionando",
        "funciono",
        "correctamente",
        "correcto",
        "correcta",
        "adecuadamente",
        "apropiadamente",
        "perfectamente",
        "bien",
        "bueno",
        "buena",
        "buenos",
        "buenas",
        "limpio",
        "limpia",
        "robusto",
        "robusta",
    }
)
FILLER_WORDS_ES = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "lo",
        "un",
        "una",
        "unos",
        "unas",
        "esto",
        "eso",
        "este",
        "esta",
        "estos",
        "estas",
        "ese",
        "esa",
        "todo",
        "toda",
        "todos",
        "todas",
        "debe",
        "deben",
        "deberia",
        "deberian",
        "tiene",
        "tienen",
        "que",
        "ser",
        "es",
        "son",
        "estan",
        "estar",
        "sea",
        "sean",
        "fue",
        "y",
        "e",
        "o",
        "u",
        "pero",
        "tambien",
        "solo",
        "muy",
        "realmente",
        "asi",
        "siempre",
        "ya",
        "ahora",
        "de",
        "del",
        "en",
        "a",
        "al",
        "para",
        "por",
        "con",
        "se",
        "vale",
        "funcion",
        "funcionalidad",
        "codigo",
        "sistema",
        "aplicacion",
        "solucion",
        "implementacion",
        "programa",
        "cambio",
        "cambios",
        "cosa",
        "cosas",
        "comportamiento",
        "resultado",
        "resultados",
        "salida",
        "general",
    }
)
RESULT_VERBS_ES = frozenset(
    {
        "devuelve",
        "devuelven",
        "devolver",
        "devuelto",
        "devuelta",
        "retorna",
        "retornan",
        "retornar",
        "lanza",
        "lanzan",
        "lanzar",
        "lanzado",
        "rechaza",
        "rechazan",
        "rechazar",
        "rechazado",
        "rechazada",
        "acepta",
        "aceptan",
        "aceptar",
        "aceptado",
        "aceptada",
        "igual",
        "iguales",
        "contiene",
        "contienen",
        "contener",
        "lista",
        "listan",
        "listar",
        "listado",
        "guarda",
        "guardan",
        "guardar",
        "guardado",
        "almacena",
        "almacenan",
        "almacenar",
        "almacenado",
        "imprime",
        "imprimen",
        "imprimir",
        "impreso",
        "muestra",
        "muestran",
        "mostrar",
        "termina",
        "terminan",
        "terminar",
        "responde",
        "responden",
        "responder",
        "crea",
        "crean",
        "crear",
        "creado",
        "creada",
        "elimina",
        "eliminan",
        "eliminar",
        "eliminado",
        "eliminada",
        "borra",
        "borran",
        "borrar",
        "borrado",
        "coincide",
        "coinciden",
        "coincidir",
        "pasa",
        "pasan",
        "pasar",
        "falla",
        "fallan",
        "fallar",
        "dentro",
        "antes",
        "despues",
    }
)
RESULT_PHRASES_ES = ("como maximo", "como minimo", "a lo sumo", "al menos", "como mucho")
_RAPIDO_ES = "rápido"
QUALITY_WORDS_ES = {
    "rapido": _RAPIDO_ES,
    "rapida": _RAPIDO_ES,
    "rapidos": _RAPIDO_ES,
    "rapidas": _RAPIDO_ES,
    "rapidamente": _RAPIDO_ES,
    "veloz": "veloz",
    "eficiente": "eficiente",
    "eficientes": "eficiente",
    "eficientemente": "eficiente",
    "escalable": "escalable",
    "escalables": "escalable",
    "seguro": "seguro",
    "segura": "seguro",
    "seguros": "seguro",
    "seguras": "seguro",
    "fiable": "fiable",
    "fiables": "fiable",
    "confiable": "confiable",
    "confiables": "confiable",
    "responsivo": "responsivo",
    "responsiva": "responsivo",
    "performante": "performante",
}
# "un" and "una" are left out: in Spanish they are mostly articles.
NUMBER_WORDS_ES = frozenset(
    {
        "cero",
        "uno",
        "dos",
        "tres",
        "cuatro",
        "cinco",
        "seis",
        "siete",
        "ocho",
        "nueve",
        "diez",
        "once",
        "doce",
        "veinte",
        "treinta",
        "cincuenta",
        "cien",
        "ciento",
        "mil",
        "millon",
        "millones",
        "medio",
        "mitad",
        "doble",
    }
)

QUESTION_TEMPLATES_ES: dict[str, str] = {
    "C0a": (
        "La tarea '{title}' no tiene criterios de aceptación. ¿Qué resultados observables "
        "muestran que está terminada y cómo se comprueba cada uno (entrada o acción -> "
        "resultado exacto esperado)? Un resultado por línea."
    ),
    "C0b": (
        "¿Cuáles son las entradas y salidas principales de la tarea '{title}' y en qué formatos "
        "(por ejemplo campos JSON, una cabecera CSV o la firma de una función)?"
    ),
    "C0c": (
        "¿Qué límites debe respetar la tarea '{title}' (números, tamaños, tiempos) y qué pasa "
        "exactamente en cada límite?"
    ),
    "C0d": (
        "¿Qué errores o rechazos debe producir la tarea '{title}' ante una entrada no válida y "
        "qué devuelve, lanza o imprime exactamente cada uno?"
    ),
    "C0e": "¿Qué comportamientos entran en el alcance de la tarea '{title}'? Uno por línea.",
    "C0f": "¿Qué queda explícitamente fuera del alcance de la tarea '{title}'? Uno por línea.",
    "C0g": (
        "¿Qué restricciones no funcionales se aplican a la tarea '{title}' (rendimiento, "
        "seguridad, persistencia) y con qué medida se comprueba cada una?"
    ),
    "C1": (
        "El criterio {target} ('{text}') no dice qué se puede observar. ¿Para qué entrada o "
        "acción, y qué resultado exacto muestra que se cumple?"
    ),
    "C2": (
        "El criterio {target} ('{text}') pide una cualidad ('{term}') sin una medida. ¿Qué "
        "número o umbral decide si se cumple (por ejemplo un tiempo, un tamaño o una tasa)?"
    ),
    "C3": (
        "El criterio {target} ('{text}') repite el criterio {other}. ¿Qué resultado distinto "
        "debe comprobar, o qué criterio debe sustituirlo?"
    ),
    "T1": (
        "La intención ('{text}') es corta, no tiene requisitos y tiene un solo criterio de "
        "aceptación sin nada concreto que comprobar. ¿Qué comportamientos separados debe "
        "entregar la tarea y qué queda fuera del alcance?"
    ),
}
"""Spanish question templates, with the same keys and placeholders as the English ones."""

RECOMMENDATION_TEMPLATES: dict[Language, str] = {
    "en": (
        "Answer {question_id} with harness task clarify, or set intake.criteriaPolicy to "
        "enforce to block INTENT until it is answered."
    ),
    "es": (
        "Responde {question_id} con harness task clarify, o pon intake.criteriaPolicy en "
        "enforce para que INTENT se bloquee hasta que se responda."
    ),
}
"""The next step a ``warn`` finding of a clarification question recommends."""

# Words of short Spanish requests ("agrega pruebas unitarias") that carry no function word.
_SPANISH_TASK_WORDS = frozenset(
    {
        "agrega",
        "agregar",
        "anade",
        "anadir",
        "implementa",
        "implementar",
        "corrige",
        "corregir",
        "arregla",
        "arreglar",
        "haz",
        "hacer",
        "escribe",
        "escribir",
        "cambia",
        "cambiar",
        "quiero",
        "necesito",
        "prueba",
        "pruebas",
        "unitarias",
        "archivo",
        "datos",
        "usuario",
        "usuarios",
    }
)
# Spanish words that are also common English words count for neither language.
_SHARED_WORDS = frozenset({"a", "e", "o", "u", "general", "once", "mil", "no"})
_SPANISH_SIGNALS = (
    (
        _SPANISH_FUNCTION_WORDS
        | _SPANISH_TASK_WORDS
        | VAGUE_WORDS_ES
        | FILLER_WORDS_ES
        | RESULT_VERBS_ES
        | frozenset(QUALITY_WORDS_ES)
        | NUMBER_WORDS_ES
    )
    - _SHARED_WORDS
    - _ENGLISH_FUNCTION_WORDS
)
