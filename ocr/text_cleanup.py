from __future__ import annotations

import re
import unicodedata


_SHORT_HUD_CODE = re.compile(r"(?<=[.!?…])\s+[A-Za-z0-9]{2}$")


def strip_trailing_hud_artifacts(text: str) -> str:
    """Trim likely HUD glyphs at the end while preserving the source OCR elsewhere."""
    value = str(text or "").rstrip()
    # Remove trailing symbols and their emoji join/variation characters, but
    # retain sentence punctuation such as periods, question marks and exclamation marks.
    while value:
        character = value[-1]
        category = unicodedata.category(character)
        if category.startswith("S") or character in "\ufe0e\ufe0f\u200d\u200b\ufffd":
            value = value[:-1].rstrip()
            continue
        break
    # Short mixed alphanumeric tags after a complete sentence are commonly HUD
    # fragments (for example, an icon read as 5D); avoid touching words or codes
    # embedded in the dialogue. Apply this after symbol cleanup in case an icon
    # followed the OCR fragment.
    if _SHORT_HUD_CODE.search(value):
        suffix = value[-2:]
        if any(character.isdigit() for character in suffix) and any(character.isalpha() for character in suffix):
            value = value[:-2].rstrip()
    return value
