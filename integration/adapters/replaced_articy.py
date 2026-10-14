from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


EXTERNAL_ID_TYPE = "articy_external_id"
OBJECT_ID_TYPE = "articy_object_id"
CANONICAL_EVENTS = {
    "dialogue_start", "dialogue_end", "pause", "resume", "scene_changed",
    "bridge_connected", "bridge_disconnected", "error",
}


def _read(value: Any, *names: str) -> Any:
    if isinstance(value, dict):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        result = getattr(value, name, None)
        if result is not None:
            return result
    return None


def _text(value: Any, *names: str, limit: int = 500) -> str | None:
    result = _read(value, *names)
    if result is None:
        return None
    return " ".join(str(result).split())[:limit] or None


class ReplacedArticyAdapter:
    """Normalize observed REPLACED callbacks; this class does not install game hooks.

    Prefer ExternalId. When the resolved DialogueFragment has no ExternalId,
    accept its full unsigned Articy Object Id. ShortId, speaker, text, frame number,
    and video timestamp are diagnostic fields and are never promoted to a trigger.
    """

    def __init__(self, game_profile_id: str, game_build_id: str, language: str):
        self.game_profile_id = str(game_profile_id)
        self.game_build_id = str(game_build_id)
        self.language = str(language)
        self._active: dict | None = None
        self._paused = False

    def _event(self, event_type: str, fragment: Any = None,
               trigger: tuple[str, str] | None = None, details: dict | None = None) -> dict:
        return {
            "event_id": f"EVT_{uuid.uuid4().hex[:16].upper()}",
            "event_type": event_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "game_profile_id": self.game_profile_id,
            "game_build_id": self.game_build_id,
            "language": self.language,
            "trigger": {"type": trigger[0], "id": trigger[1]} if trigger else None,
            "dialogue": {
                "short_id": _text(fragment, "ShortId", "shortId", "short_id", limit=160),
                "speaker": _text(fragment, "speaker", "Speaker", "character", "Character", limit=160),
                "text": _text(fragment, "text", "Text", limit=500),
            } if fragment is not None else None,
            "details": details or {},
        }

    def _external_id(self, fragment: Any) -> str | None:
        value = _read(fragment, "ExternalId", "externalId", "external_id")
        if value is None:
            return None
        result = str(value).strip()
        if not result or len(result) > 160 or any(ord(char) < 32 for char in result):
            return None
        return result

    def _trigger(self, fragment: Any) -> tuple[str, str] | None:
        external_id = self._external_id(fragment)
        if external_id:
            return EXTERNAL_ID_TYPE, external_id
        object_id = _read(fragment, "Id", "id")
        if isinstance(object_id, int) and not isinstance(object_id, bool) and 0 < object_id <= 2**64 - 1:
            return OBJECT_ID_TYPE, str(object_id)
        return None

    def handle(self, callback: str, fragment: Any = None) -> list[dict]:
        """Return zero or more canonical events for a game callback."""
        name = str(callback or "").strip().casefold().replace("_", "")
        if name in {"initline", "dialoguestart"}:
            trigger = self._trigger(fragment)
            if not trigger:
                return [self._event("error", details={
                    "code": "missing_articy_trigger",
                    "message": "InitLine did not provide a verified Articy identifier.",
                })]
            if self._active and self._active["trigger"] == trigger:
                return []
            events = []
            if self._active:
                events.append(self._event("dialogue_end", trigger=self._active["trigger"],
                                          details={"reason": "next_dialogue_started"}))
            self._active = {"trigger": trigger, "fragment": fragment}
            self._paused = False
            events.append(self._event("dialogue_start", fragment, trigger))
            return events

        if name in {"releaseline", "dialogueend"}:
            if not self._active:
                return []
            trigger = self._trigger(fragment) if fragment is not None else None
            if trigger and trigger != self._active["trigger"]:
                return [self._event("error", fragment, trigger, {
                    "code": "release_id_mismatch",
                    "active_trigger_id": self._active["trigger"][1],
                })]
            event = self._event("dialogue_end", self._active["fragment"], self._active["trigger"])
            self._active = None
            self._paused = False
            return [event]

        if name in {"pause", "onpause"}:
            if not self._active or self._paused:
                return []
            self._paused = True
            return [self._event("pause", self._active["fragment"], self._active["trigger"])]

        if name in {"resume", "onresume", "unpause"}:
            if not self._active or not self._paused:
                return []
            self._paused = False
            return [self._event("resume", self._active["fragment"], self._active["trigger"])]

        if name in {"scenechanged", "onscenechanged"}:
            scene_id = _text(fragment, "scene_id", "sceneId", "name", "Name", limit=160)
            return [self._event("scene_changed", details={"scene_id": scene_id})]

        return [self._event("error", details={
            "code": "unsupported_callback", "callback": str(callback or "")[:120],
        })]

    @property
    def active_trigger_id(self) -> str | None:
        return self._active["trigger"][1] if self._active else None
