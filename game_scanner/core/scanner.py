from __future__ import annotations

from pathlib import Path
import re
import time

from game_scanner.core.inventory import ScanContext, build_inventory, resolve_target, scan_binary_tokens
from game_scanner.core.models import DiagnosticReport, LayerFinding
from game_scanner.core.plugin_registry import find_game_plugins
from game_scanner.core.file_version import read_file_versions
from game_scanner.detectors import (audio, build_runtime, dialogue, engine, localization,
                                    modding, runtime_events, visual_fallback)


SIGNATURE_TOKENS = set(
    dialogue.TOKENS + localization.TOKENS + audio.TOKENS + runtime_events.TOKENS
)
DETECTORS = (engine.detect, build_runtime.detect, dialogue.detect, localization.detect,
             audio.detect, runtime_events.detect, modding.detect, visual_fallback.detect)


def _recommend(findings: list[LayerFinding], game_name: str, plugins: list[dict]) -> dict:
    by_layer = {item.layer: item for item in findings}
    dialogue_finding = by_layer["dialogue"]
    runtime_finding = by_layer["runtime_events"]
    build = by_layer["build_runtime"]
    engine_finding = by_layer["engine"]
    evidence_text = " ".join(
        " ".join(evidence.matches) + " " + evidence.description
        for item in findings for evidence in item.evidence
    ).casefold()
    if plugins:
        plugin = plugins[0]
        code = "GAME_SPECIFIC_ADAPTER"
        title = plugin.get("name") or f"{game_name} — adapter específico candidato"
        reasons = [f"O registry local do DubSync contém o candidato {plugin.get('plugin_id') or title}.",
                   "A associação vem do nome do jogo; compare a build deste scan com os requisitos do plugin."]
        if plugin.get("runtime_callback_observed"):
            reasons.append("O manifesto registra callbacks observados em um piloto anterior.")
        if not plugin.get("runtime_hook_confirmed"):
            reasons.append("O manifesto ainda não marca o hook como confirmado; este scan também não valida runtime.")
    elif any(word in evidence_text for word in ("articy", "dialoguefragment", "externalid")):
        code = "GAME_SPECIFIC_ADAPTER"
        title = f"{game_name} — adapter específico candidato"
        reasons = ["Sinais de narrativa/diálogo específicos foram encontrados.",
                   "A disponibilidade do identificador em runtime ainda precisa ser provada."]
    elif "unity" in (engine_finding.summary + " " + build.summary).casefold():
        code = "GENERIC_UNITY_ADAPTER"
        title = "Adapter genérico Unity como opção a investigar"
        reasons = ["Estrutura Unity identificada.", "A engine por si só não confirma um hook compatível."]
    elif "unreal" in engine_finding.summary.casefold():
        code = "GENERIC_UNREAL_ADAPTER"
        title = "Adapter genérico Unreal como opção a investigar"
        reasons = ["Estrutura compatível com Unreal encontrada.", "Confirme o método de integração por build."]
    elif dialogue_finding.status not in {"detected", "probable"} and runtime_finding.status != "probable":
        code = "VISUAL_RUNTIME"
        title = "Visual Runtime como caminho inicial de fallback"
        reasons = ["A varredura não confirmou um ID ou evento interno.",
                   "A viabilidade visual ainda depende de OCR/ROI no jogo em execução."]
    else:
        code = "HYBRID_RUNTIME"
        title = "Investigar integração híbrida"
        reasons = ["Há sinais internos parciais, mas nenhum gatilho foi validado em runtime."]
    return {
        "integration_type": code,
        "adapter_candidate": (
            plugins[0].get("plugin_id") if plugins else f"{game_name.casefold().replace(' ', '_')}_adapter"
        ) if code == "GAME_SPECIFIC_ADAPTER" else None,
        "title": title,
        "status": "candidate_requires_runtime_validation",
        "reasons": reasons,
        "evidence_layers": [item.layer for item in findings
                            if item.layer in {"engine", "build_runtime", "dialogue", "runtime_events"}
                            and item.evidence],
        "registered_plugin_candidates": plugins,
        "static_analysis_is_not_runtime_confirmation": True,
    }


def run_scan(target: str | Path, progress=None) -> DiagnosticReport:
    scan_started = time.monotonic()
    root, executable = resolve_target(target)
    name = executable.stem if executable else root.name
    name = re.sub(r"[_-]+", " ", name).strip() or "Jogo desconhecido"
    context = ScanContext(root=root, executable=executable, game_name=name)
    if progress:
        progress(0, "Inventariando arquivos do jogo…")
    build_inventory(context)
    if progress:
        progress(1, "Procurando assinaturas em metadados e assemblies…")
    scan_binary_tokens(context, SIGNATURE_TOKENS,
                       progress=lambda done, total: progress(1 + done / max(total, 1),
                                                              "Lendo nomes de classes e eventos…") if progress else None)
    findings: list[LayerFinding] = []
    for index, detector in enumerate(DETECTORS, 1):
        if progress:
            progress(1 + index, f"Camada {index}/8: preparando diagnóstico…")
        findings.append(detector(context))
        if progress:
            progress(1 + index, f"Camada {index}/8: {findings[-1].title}")
    file_versions = read_file_versions(executable)
    report = DiagnosticReport(
        game_name=name,
        root_path=str(root),
        executable_path=str(executable) if executable else None,
        executable_file_version=file_versions["file_version"],
        executable_product_version=file_versions["product_version"],
        duration_seconds=round(time.monotonic() - scan_started, 3),
        findings=findings,
        recommendation=_recommend(findings, name, find_game_plugins(name)),
        warnings=list(context.warnings),
        inventory_summary={"files_seen": len(context.files),
                           "binary_files_with_signatures": len({path for hits in context.token_matches.values()
                                                                 for path, _ in hits})},
    )
    if progress:
        progress(10, "Diagnóstico concluído.")
    return report
