from __future__ import annotations

from rapidfuzz.fuzz import ratio

from .normalize import normalize_text


def equivalent_observation(text_a: str, text_b: str, box_a: tuple[int, ...], box_b: tuple[int, ...],
                           hash_a: str, hash_b: str, gap_seconds: float) -> bool:
    if gap_seconds < 0 or gap_seconds > 0.8:
        return False
    similarity = ratio(normalize_text(text_a), normalize_text(text_b))
    if similarity < 91:
        return False
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b
    same_place = abs(ax - bx) <= max(28, aw * 0.12) and abs(ay - by) <= max(18, ah * 0.2)
    return same_place and ((hash_a == hash_b) or similarity >= 97)
