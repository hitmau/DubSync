from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    """Comparison form only; original OCR and reviewer text stay untouched."""
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())
