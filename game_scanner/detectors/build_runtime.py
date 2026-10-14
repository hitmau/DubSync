from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import LayerFinding
from game_scanner.detectors.common import evidence_from_files, no_evidence


def detect(context: ScanContext) -> LayerFinding:
    game_assembly = context.named_files("GameAssembly.dll")
    metadata = [item for item in context.files if item.name.casefold() == "global-metadata.dat"]
    if game_assembly and metadata:
        return LayerFinding("build_runtime", "Build / Runtime", "detected", "high", 99,
                            "Unity IL2CPP identificado.",
                            evidence_from_files(game_assembly, "file", "Binário nativo IL2CPP.")
                            + evidence_from_files(metadata, "file", "Metadados IL2CPP."),
                            ["O scanner não executa nem modifica os binários."], False)
    managed = [item for item in context.files if item.name.casefold() in {"assembly-csharp.dll", "mono.dll", "mono-2.0-bdwgc.dll"}]
    if managed and not game_assembly:
        return LayerFinding("build_runtime", "Build / Runtime", "probable", "high", 88,
                            "Assemblies Mono/.NET encontrados; build Unity Mono é provável.",
                            evidence_from_files(managed, "file", "Assembly/runtime gerenciado."),
                            ["Se a engine detectada não for Unity, este resultado indica apenas assemblies gerenciados."], True)
    if any(item.name.casefold().endswith(".pck") for item in context.files):
        pck = [item for item in context.files if item.suffix == ".pck"]
        return LayerFinding("build_runtime", "Build / Runtime", "probable", "medium", 70,
                            "Pacote de recursos do Godot encontrado; tipo de exportação não determinado.",
                            evidence_from_files(pck, "file", "Pacote PCK."),
                            ["O runtime não pode ser confirmado apenas pelo pacote."], True)
    return no_evidence("build_runtime", "Build / Runtime", "Build e runtime não confirmados.",
                       ["Não foi possível distinguir runtime ou formato de build com as assinaturas disponíveis."],
                       status="not_confirmed")

