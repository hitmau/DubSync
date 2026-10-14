from __future__ import annotations

from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import Evidence, LayerFinding


def evidence_from_files(records, kind: str, description: str, limit: int = 8) -> list[Evidence]:
    return [Evidence(kind, item.relative_path, description) for item in records[:limit]]


def evidence_from_tokens(context: ScanContext, tokens: tuple[str, ...], kind: str,
                         description: str, limit: int = 12) -> list[Evidence]:
    result = []
    for path, matches in context.token_evidence(*tokens)[:limit]:
        result.append(Evidence(kind, path, description, matches))
    return result


def no_evidence(layer: str, title: str, summary: str, details: list[str] | None = None,
                status: str = "not_found") -> LayerFinding:
    return LayerFinding(layer, title, status, "unknown", None, summary, [],
                        details or ["Nenhuma assinatura estática conhecida foi localizada."],
                        runtime_validation_required=False)

