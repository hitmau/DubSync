from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass(slots=True)
class Evidence:
    kind: str
    path: str
    description: str
    matches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LayerFinding:
    layer: str
    title: str
    status: str
    confidence: str
    confidence_percent: int | None
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    runtime_validation_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "title": self.title,
            "status": self.status,
            "confidence": self.confidence,
            "confidence_percent": self.confidence_percent,
            "summary": self.summary,
            "evidence": [item.to_dict() for item in self.evidence],
            "details": list(self.details),
            "runtime_validation_required": self.runtime_validation_required,
        }


@dataclass(slots=True)
class DiagnosticReport:
    game_name: str
    root_path: str
    executable_path: str | None
    executable_file_version: str | None = None
    executable_product_version: str | None = None
    scanned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    report_id: str = field(default_factory=lambda: f"SCAN_{uuid.uuid4().hex[:12].upper()}")
    scanner_version: str = "0.1.0"
    schema_version: int = 1
    duration_seconds: float | None = None
    findings: list[LayerFinding] = field(default_factory=list)
    recommendation: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    inventory_summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scanner_version": self.scanner_version,
            "report_id": self.report_id,
            "scanned_at": self.scanned_at,
            "game": {
                "name": self.game_name,
                "root_path": self.root_path,
                "executable_path": self.executable_path,
                "executable_file_version": self.executable_file_version,
                "executable_product_version": self.executable_product_version,
            },
            "duration_seconds": self.duration_seconds,
            "diagnostics": {item.layer: item.to_dict() for item in self.findings},
            "recommendation": self.recommendation,
            "inventory_summary": self.inventory_summary,
            "warnings": list(self.warnings),
        }
