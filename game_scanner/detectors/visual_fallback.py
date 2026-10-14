from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import Evidence, LayerFinding


def detect(context: ScanContext) -> LayerFinding:
    executable = context.executable
    evidence = []
    if executable and executable.is_file():
        evidence.append(Evidence("executable", executable.name,
                                 "Executável selecionado como referência do jogo."))
    return LayerFinding("visual_fallback", "Fallback Visual", "probable", "medium", 72,
                        "Visual Runtime é um caminho de contingência disponível no DubSync.", evidence,
                        ["O scanner não inicia captura, OCR ou interação com o jogo.",
                         "Região de legenda, OCR e sincronização precisam ser validados em runtime."], True)

