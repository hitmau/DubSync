from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import LayerFinding
from game_scanner.detectors.common import evidence_from_files, no_evidence


LOADER_NAMES = {"doorstop_config.ini", "winhttp.dll", "version.dll", "dinput8.dll"}


def detect(context: ScanContext) -> LayerFinding:
    evidence = []
    loaders = []
    for item in context.files:
        rel = item.relative_path.casefold().replace("\\", "/")
        name = item.name.casefold()
        explicit_marker = (
            name == "doorstop_config.ini"
            or rel.startswith(("bepinex/core/", "bepinex/plugins/", "melonloader/", "ue4ss/"))
            and item.suffix in {".dll", ".cfg", ".json"}
        )
        if explicit_marker:
            loaders.append(item)
    evidence += evidence_from_files(loaders, "loader", "Loader ou estrutura de modding instalada.")
    if not evidence:
        proxies = [item for item in context.files if item.name.casefold() in LOADER_NAMES]
        evidence += evidence_from_files(proxies, "proxy_dll", "DLL proxy pode estar associada a loader; também pode ser legítima do jogo.")
    if evidence:
        probable_only = all(item.kind == "proxy_dll" for item in evidence)
        return LayerFinding("modding", "Modding / Injeção", "probable" if probable_only else "detected",
                            "low" if probable_only else "high", 52 if probable_only else 94,
                            "Loader/indicador de modding encontrado." if not probable_only
                            else "DLL proxy encontrada; sua finalidade não foi confirmada.", evidence,
                            ["O scanner apenas registra arquivos existentes.",
                             "Nenhum loader é instalado e nenhuma configuração do jogo é alterada."], True)
    return no_evidence("modding", "Modding / Injeção", "Loader compatível não encontrado.",
                       ["Isso não prova que modding seja impossível; apenas não havia loader reconhecido instalado.",
                        "Compatibilidade de loader exige validação específica do jogo e da build."],
                       status="not_confirmed")
