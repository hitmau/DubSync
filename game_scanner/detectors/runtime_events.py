from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import LayerFinding
from game_scanner.detectors.common import evidence_from_tokens, no_evidence


TOKENS = ("initline", "releaseline", "dialogue_start", "dialogue_end",
          "speakerchanged", "subtitlechanged")


def detect(context: ScanContext) -> LayerFinding:
    evidence = evidence_from_tokens(context, TOKENS, "binary_string",
                                    "Nome de callback ou evento candidato encontrado estaticamente.")
    if evidence:
        matches = sorted({match for item in evidence for match in item.matches}, key=str.casefold)
        summary = "Candidatos a eventos/ciclo de fala encontrados; ainda não validados em runtime."
        return LayerFinding("runtime_events", "Eventos / Runtime", "probable", "medium", 76,
                            summary, evidence,
                            ["Strings em assemblies ou metadata não provam que o callback pode ser interceptado.",
                             f"Candidatos: {', '.join(matches[:8])}.",
                             "Confirme início, fim, pause, skip e troca de cena com o jogo em execução."], True)
    return no_evidence("runtime_events", "Eventos / Runtime", "Eventos de fala não confirmados.",
                       ["A análise estática não encontrou nomes de callback conhecidos.",
                        "Ausência de string não descarta eventos com nomes diferentes ou ofuscados."],
                       status="not_confirmed")
