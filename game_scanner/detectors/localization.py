from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import LayerFinding
from game_scanner.detectors.common import evidence_from_files, evidence_from_tokens, no_evidence


TOKENS = ("subtitle", "subtitlesmanager", "currentlineduration", "localization",
          "localisation", "stringtable", "localizedtext", "initline", "releaseline")
PATH_HINTS = ("subtitle", "localization", "localisation", "locale", "language", "stringtable")


def detect(context: ScanContext) -> LayerFinding:
    file_hits = context.matching_files(*PATH_HINTS)
    token_hits = evidence_from_tokens(context, TOKENS, "binary_string",
                                      "Nome de legenda/localização encontrado em conteúdo estático.")
    evidence = evidence_from_files(file_hits, "path", "Nome do caminho sugere texto ou localização.") + token_hits
    if evidence:
        has_manager = any("subtitlesmanager" in m.casefold() or "currentlineduration" in m.casefold()
                          for item in evidence for m in item.matches)
        return LayerFinding("localization", "Legendas / Localização", "detected" if has_manager else "probable",
                            "high" if has_manager else "medium", 88 if has_manager else 70,
                            "Componentes de legenda/localização encontrados." if has_manager
                            else "Arquivos ou strings associados a legendas/localização encontrados.",
                            evidence[:16],
                            ["O texto e o tempo de duração podem depender da cena e do idioma ativo.",
                             "A troca de legenda precisa ser confirmada com o jogo em execução."], True)
    return no_evidence("localization", "Legendas / Localização", "Sistema de legendas não localizado.",
                       ["Nenhum caminho ou nome de classe conhecido apareceu na amostra estática."])

