from __future__ import annotations

import json
from pathlib import Path
from game_scanner.core.models import DiagnosticReport


def export_json(report: DiagnosticReport, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def render_markdown(report: DiagnosticReport) -> str:
    lines = [f"# Diagnóstico — {report.game_name}", "",
             f"- Scan: `{report.report_id}`", f"- Data: {report.scanned_at}",
             f"- Pasta: `{report.root_path}`",
             f"- Executável: `{report.executable_path or 'não selecionado'}`", "",
             f"- FileVersion do executável: `{report.executable_file_version or 'não encontrada'}`",
             f"- ProductVersion do executável: `{report.executable_product_version or 'não encontrada'}`",
             f"- Duração: `{report.duration_seconds if report.duration_seconds is not None else 'n/d'} s`", "",
             "## Recomendação", "", f"**{report.recommendation.get('title', 'Sem recomendação')}**",
             "", *[f"- {item}" for item in report.recommendation.get("reasons", [])], "",
             "## Oito camadas", ""]
    for item in report.findings:
        lines.extend([f"### {item.title}", "", f"- Estado: `{item.status}`",
                      f"- Confiança: {item.confidence}"
                      + (f" ({item.confidence_percent}%)" if item.confidence_percent is not None else ""),
                      f"- Resultado: {item.summary}"])
        if item.evidence:
            lines.append("- Evidências:")
            lines.extend(f"  - `{entry.path}` — {entry.description}"
                         + (f" ({', '.join(entry.matches)})" if entry.matches else "")
                         for entry in item.evidence)
        lines.extend(f"- Observação: {detail}" for detail in item.details)
        if item.runtime_validation_required:
            lines.append("- Requer validação em runtime: sim")
        lines.append("")
    if report.warnings:
        lines.extend(["## Limitações da varredura", "", *[f"- {item}" for item in report.warnings], ""])
    return "\n".join(lines)
