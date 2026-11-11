"""intake.language: Unicode words, language detection and the rules in Spanish and English."""

from __future__ import annotations

import pytest

from governed_harness.domain.errors import ConfigurationError
from governed_harness.domain.models import AcceptanceCriterion, Task
from governed_harness.intake import (
    QUESTION_TEMPLATES,
    QUESTION_TEMPLATES_ES,
    assess_intent,
    detect_language,
    no_observable_result,
    resolve_language,
    scope_without_breakdown,
    unmeasured_quality,
)
from governed_harness.intake.clarification import _ascii_words
from governed_harness.intake.language import fold, unicode_words

LONG_INTENT_EN = (
    "Apply a percentage discount to the order subtotal only when the subtotal reaches the "
    "configured threshold, leaving every other order untouched and keeping the public "
    "signature of the pricing function."
)
LONG_INTENT_ES = (
    "Aplicar un descuento porcentual al subtotal del pedido solo cuando el subtotal alcanza el "
    "umbral configurado, sin tocar los demás pedidos y manteniendo la firma pública de la "
    "función de precios que ya usan los clientes."
)


def criterion(text: str, criterion_id: str = "AC-1") -> AcceptanceCriterion:
    return AcceptanceCriterion(criterion_id=criterion_id, text=text)


def task(*criteria: AcceptanceCriterion, intent: str, title: str = "Discount") -> Task:
    return Task(
        task_id="task_1",
        project_id="project_1",
        title=title,
        intent=intent,
        acceptance_criteria=criteria,
    )


# ----- tokenization ----------------------------------------------------------------------


def test_unicode_words_keep_accents_enye_and_dieresis_inside_a_word() -> None:
    assert unicode_words("Las contraseñas, el pingüino y la canción.") == [
        "las",
        "contrasenas",
        "el",
        "pinguino",
        "y",
        "la",
        "cancion",
    ]


def test_the_original_tokenizer_splits_non_ascii_words() -> None:
    assert _ascii_words("contraseñas") == ["contrase", "as"]


@pytest.mark.parametrize(
    "text",
    ["It works.", "Isn't it fine's?", "snake_case and kebab-case 42 x1", "a--b _c 'd'"],
)
def test_unicode_words_equal_the_original_ones_on_ascii_text(text: str) -> None:
    assert unicode_words(text) == _ascii_words(text)


def test_fold_removes_diacritics() -> None:
    assert fold("RÁPIDO") == "rapido"
    assert fold("Después") == "despues"


# ----- detection -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Funciona.", "es"),
        ("Agrega un descuento al pedido cuando el total supera el umbral.", "es"),
        ("¿Qué pasa?", "es"),
        ("It works.", "en"),
        ("Add a discount to the order when the total exceeds the threshold.", "en"),
        ("apply_discount(100, 100, 0.1)", "en"),
        ("", "en"),
    ],
)
def test_detect_language(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_resolve_language_reads_the_whole_task() -> None:
    spanish = task(criterion("Funciona."), intent="Agrega un descuento.", title="Descuento")
    assert resolve_language(spanish, "auto") == "es"
    assert resolve_language(spanish, "en") == "en"
    assert resolve_language(spanish, None) is None
    with pytest.raises(ConfigurationError):
        resolve_language(spanish, "fr")


# ----- the rules in Spanish and English --------------------------------------------------

# Each pair says the same thing; the rule must judge both the same way.
C1_PAIRS = [
    ("It works.", "Funciona."),
    ("Done properly", "Hecho adecuadamente"),
    ("It should work correctly and as expected.", "Debe funcionar correctamente y como se espera."),
    ("Everything works well and the code is clean.", "Todo funciona bien y el código es limpio."),
    ("The output is good.", "La salida es buena."),
]
ANCHORED_PAIRS = [
    ("It returns the total", "Devuelve el total"),
    ("Rejects an empty cart", "Rechaza un carrito vacío"),
    ("At most a second", "Como máximo un segundo"),
    ("Responds before the timeout", "Responde antes del límite"),
    ("Lists the open orders", "Lista los pedidos abiertos"),
]
C2_PAIRS = [
    ("The endpoint must be fast.", "El endpoint debe ser rápido.", "fast", "rápido"),
    (
        "Passwords are stored securely.",
        "Las contraseñas se guardan de forma segura.",
        "secure",
        "seguro",
    ),
    ("The import is efficient.", "La importación es eficiente.", "efficient", "eficiente"),
    ("The service is reliable.", "El servicio es fiable.", "reliable", "fiable"),
]


@pytest.mark.parametrize(("english", "spanish"), C1_PAIRS)
def test_c1_judges_a_vague_criterion_the_same_in_both_languages(english: str, spanish: str) -> None:
    assert no_observable_result(criterion(english), language="en")
    assert no_observable_result(criterion(spanish), language="es")


@pytest.mark.parametrize(("english", "spanish"), ANCHORED_PAIRS)
def test_c1_accepts_a_result_verb_in_both_languages(english: str, spanish: str) -> None:
    assert not no_observable_result(criterion(english), language="en")
    assert not no_observable_result(criterion(spanish), language="es")


def test_without_the_language_a_spanish_result_verb_is_not_an_anchor() -> None:
    """The original rule: "Devuelve el total" has three words and no English anchor."""
    assert no_observable_result(criterion("Devuelve el total"))


def test_unaccented_spanish_words_count_as_the_accented_ones() -> None:
    assert unmeasured_quality(criterion("Debe ser rapido"), language="es") == "rápido"
    assert not no_observable_result(criterion("Responde despues del pago"), language="es")


@pytest.mark.parametrize(("english", "spanish", "term_en", "term_es"), C2_PAIRS)
def test_c2_finds_an_unmeasured_quality_in_both_languages(
    english: str, spanish: str, term_en: str, term_es: str
) -> None:
    assert unmeasured_quality(criterion(english), language="en") == term_en
    assert unmeasured_quality(criterion(spanish), language="es") == term_es


@pytest.mark.parametrize(
    "text",
    ["Debe ser rápido: menos de 200 ms", "Debe ser rápido con dos réplicas", "Rápido en 1 s"],
)
def test_c2_accepts_a_number_in_spanish(text: str) -> None:
    assert unmeasured_quality(criterion(text), language="es") is None


def test_c2_does_not_read_the_spanish_article_una_as_a_number() -> None:
    assert unmeasured_quality(criterion("Debe ser una API rápida"), language="es") == "rápido"


def test_spanish_tasks_keep_the_english_vocabulary() -> None:
    assert unmeasured_quality(criterion("El endpoint debe ser fast"), language="es") == "fast"
    assert not no_observable_result(criterion("El endpoint returns 200"), language="es")


def test_t1_in_both_languages() -> None:
    english = task(criterion("It works."), intent="Add a discount.")
    spanish = task(criterion("Funciona."), intent="Agrega un descuento.")
    assert scope_without_breakdown(english, language="en")
    assert scope_without_breakdown(spanish, language="es")
    anchored = task(criterion("Devuelve el total con descuento"), intent="Agrega un descuento.")
    assert not scope_without_breakdown(anchored, language="es")
    assert scope_without_breakdown(anchored)


def test_c3_folds_duplicates_in_spanish() -> None:
    spanish = task(
        criterion("Devuelve el total.", "AC-1"),
        criterion("devuelve  el total", "AC-2"),
        intent=LONG_INTENT_ES,
    )
    questions = assess_intent(spanish, language="es")
    assert [(q.rule_id, q.target) for q in questions] == [("C3", "AC-2")]
    assert questions[0].text.startswith("El criterio AC-2")


# ----- questions -------------------------------------------------------------------------


def test_spanish_templates_have_the_english_keys_and_placeholders() -> None:
    assert set(QUESTION_TEMPLATES_ES) == set(QUESTION_TEMPLATES)
    for key, text in QUESTION_TEMPLATES.items():
        placeholders = {part.split("}")[0] for part in text.split("{")[1:]}
        spanish = {part.split("}")[0] for part in QUESTION_TEMPLATES_ES[key].split("{")[1:]}
        assert spanish == placeholders, key


def test_a_spanish_casual_task_gets_spanish_questions_with_the_same_ids() -> None:
    english = task(criterion("It works."), intent="Add a discount.")
    spanish = task(criterion("Funciona."), intent="Agrega un descuento.", title="Descuento")
    en_questions = assess_intent(english, language="auto")
    es_questions = assess_intent(spanish, language="auto")
    ids = [(q.question_id, q.rule_id, q.target) for q in en_questions]
    assert ids == [("Q-1", "C1", "AC-1"), ("Q-2", "T1", "task")]
    assert [(q.question_id, q.rule_id, q.target) for q in es_questions] == ids
    assert es_questions[0].text == QUESTION_TEMPLATES_ES["C1"].format(
        target="AC-1", text="Funciona."
    )
    assert es_questions[1].text.startswith("La intención ('Agrega un descuento.')")


def test_c0_questions_in_spanish() -> None:
    spanish = Task(
        task_id="task_1",
        project_id="project_1",
        title="Descuento",
        intent="Agrega un descuento al pedido.",
        acceptance_criteria=(),
        criteria_pending=True,
    )
    questions = assess_intent(spanish, language="es")
    assert [q.rule_id for q in questions] == ["C0"] * 7
    assert questions[0].text.startswith("La tarea 'Descuento' no tiene criterios")


def test_english_questions_are_unchanged_under_the_key() -> None:
    english = task(
        criterion("It works.", "AC-1"),
        criterion("The endpoint must be fast.", "AC-2"),
        criterion("It works.", "AC-3"),
        intent=LONG_INTENT_EN,
    )
    assert assess_intent(english, language="auto") == assess_intent(english)
    assert assess_intent(english, language="en") == assess_intent(english)
