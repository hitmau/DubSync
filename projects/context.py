from __future__ import annotations

from contextvars import ContextVar, Token


_active_project_id: ContextVar[str | None] = ContextVar("dubsync_active_project_id", default=None)


def current_project_id(default: str = "replaced") -> str:
    """Return request project, retaining the legacy project for startup jobs and old callers."""
    return _active_project_id.get() or default


def scoped_project_id() -> str | None:
    """Return an explicit request scope, or None for internal/background operations."""
    return _active_project_id.get()


def set_project_context(project_id: str | None) -> Token:
    return _active_project_id.set(project_id)


def reset_project_context(token: Token) -> None:
    _active_project_id.reset(token)
