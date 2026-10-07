"""Literal, case-insensitive OCR text replacements shared by analysis and review."""

from __future__ import annotations

import re
import unicodedata


MAX_REPLACEMENT_RULES = 100
MAX_REPLACEMENT_TEXT_LENGTH = 4000


def clean_replacement_rules(rules: object) -> list[dict[str, str]]:
    if not isinstance(rules, list):
        raise ValueError("A lista de regras de substituição é inválida.")
    if len(rules) > MAX_REPLACEMENT_RULES:
        raise ValueError(f"Cadastre no máximo {MAX_REPLACEMENT_RULES} regras por projeto.")

    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(rules, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"A regra {index} está inválida.")
        find_text = str(item.get("find_text") or "").strip()
        replace_text = str(item.get("replace_text") or "").strip()
        if not find_text and not replace_text:
            continue
        if not find_text:
            raise ValueError(f"Preencha o texto a localizar na regra {index}.")
        if len(find_text) > MAX_REPLACEMENT_TEXT_LENGTH or len(replace_text) > MAX_REPLACEMENT_TEXT_LENGTH:
            raise ValueError(f"Cada texto pode ter até {MAX_REPLACEMENT_TEXT_LENGTH} caracteres.")
        key = unicodedata.normalize("NFKC", find_text).casefold()
        if key in seen:
            raise ValueError(f"A regra {index} repete um texto de busca já cadastrado.")
        seen.add(key)
        cleaned.append({"find_text": find_text, "replace_text": replace_text})
    return cleaned


def apply_text_replacements(value: str, rules: list[dict[str, str]] | None) -> str:
    result = str(value or "")
    for rule in rules or []:
        find_text = str(rule.get("find_text") or "").strip()
        if not find_text:
            continue
        replacement = str(rule.get("replace_text") or "")
        result = re.sub(re.escape(find_text), lambda _match: replacement, result, flags=re.IGNORECASE)
    return result
