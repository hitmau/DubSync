from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import LayerFinding
from game_scanner.detectors.common import evidence_from_files, no_evidence


def detect(context: ScanContext) -> LayerFinding:
    unity = context.named_files("UnityPlayer.dll", "GameAssembly.dll")
    unity_data = [item for item in context.files
                  if any(part.casefold().endswith("_data")
                         for part in item.relative_path.split("/"))]
    if unity or unity_data:
        evidence = evidence_from_files(unity, "file", "Binário característico do Unity.")
        evidence += evidence_from_files(unity_data, "path_signature",
                                        "Arquivo localizado sob o diretório de dados característico do Unity.")
        return LayerFinding("engine", "Engine", "detected", "high", 98,
                            "Unity identificado pela estrutura de instalação.", evidence,
                            ["A identificação da engine é estática; não confirma compatibilidade de um adapter."], False)

    unreal = [item for item in context.files if "/content/paks/" in f"/{item.relative_path.casefold()}/"
              and item.suffix in {".pak", ".utoc", ".ucas"}]
    unreal += [item for item in context.files if "engine/binaries/" in item.relative_path.casefold()]
    if unreal:
        return LayerFinding("engine", "Engine", "detected", "high", 92,
                            "Estrutura compatível com Unreal Engine encontrada.",
                            evidence_from_files(unreal, "file", "Pacote ou binário característico do Unreal."),
                            ["A build pode omitir arquivos-fonte como .uproject; a classificação usa os arquivos distribuídos."], False)

    godot = [item for item in context.files if item.suffix == ".pck"]
    if godot:
        return LayerFinding("engine", "Engine", "probable", "medium", 78,
                            "Pacote PCK encontrado; Godot é provável.",
                            evidence_from_files(godot, "file", "Pacote PCK associado com frequência ao Godot."),
                            ["Confirme a engine com metadados ou inspeção do executável."], True)
    return no_evidence("engine", "Engine", "Engine desconhecida.",
                       ["A busca estática não encontrou uma estrutura Unity, Unreal ou Godot conhecida."])
