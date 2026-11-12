"""Slugs para las URLs."""

import re
import unicodedata


def slugify(text: str) -> str:
    """El texto en minúsculas, sin acentos, con guiones en lugar de espacios."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9 ]", "", ascii_text.lower())
    return cleaned.strip().replace(" ", "-")
