from textkit import report_line, slugify


def test_slugify_simple() -> None:
    assert slugify("Hola Mundo") == "hola-mundo"


def test_slugify_removes_accents() -> None:
    assert slugify("Canción Ñandú") == "cancion-nandu"


def test_report_line() -> None:
    assert report_line("cafe", 3.5) == "cafe                       3.50"
