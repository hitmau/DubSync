from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import LayerFinding
from game_scanner.detectors.common import evidence_from_tokens, no_evidence


TOKENS = ("articy", "dialoguefragment", "externalid", "shortid", "dialogueid", "subtitleid",
          "yarnspinner", "yarnproject", "inkle", "inkruntime", "inkstory")


def detect(context: ScanContext) -> LayerFinding:
    evidence = evidence_from_tokens(context, TOKENS, "binary_string",
                                    "Identificador narrativo encontrado como string em arquivo do jogo.")
    matches = {match.casefold() for item in evidence for match in item.matches}
    if evidence:
        strong = "dialoguefragment" in matches and ("externalid" in matches or "shortid" in matches)
        vendor = "articy" in matches
        summary = "Articy e estrutura de diálogo detectados." if vendor else "Sinais de diálogo/narrativa encontrados."
        return LayerFinding("dialogue", "Diálogo / Narrativa", "detected" if strong or vendor else "probable",
                            "high" if strong or vendor else "medium", 92 if strong or vendor else 72,
                            summary, evidence,
                            ["IDs encontrados em arquivos não provam que o jogo os exponha durante a fala.",
                             "Acesso e estabilidade do gatilho precisam de observação em runtime."], True)
    return no_evidence("dialogue", "Diálogo / Narrativa", "Sistema de diálogo não identificado.",
                       ["Nenhum nome conhecido de sistema narrativo ou chave de fala apareceu nos arquivos amostrados.",
                        "Strings ofuscadas ou em pacotes não amostrados podem não aparecer."])
