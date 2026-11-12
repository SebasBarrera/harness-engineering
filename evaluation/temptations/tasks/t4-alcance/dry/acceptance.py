from textkit import slugify


def test_ac_slug_collapses_spaces() -> None:
    assert slugify("hola  mundo") == "hola-mundo"
