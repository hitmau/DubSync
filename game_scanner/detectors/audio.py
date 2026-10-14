from game_scanner.core.inventory import ScanContext
from game_scanner.core.models import LayerFinding
from game_scanner.detectors.common import evidence_from_files, evidence_from_tokens, no_evidence


TOKENS = ("fmod", "fmodstudio", "wwise", "aksoundengine", "audiokinetic")


def detect(context: ScanContext) -> LayerFinding:
    names = [item for item in context.files if any(token in item.name.casefold()
             for token in ("fmod", "wwise", "audiokinetic", "soundengine"))]
    banks = [item for item in context.files if item.suffix in {".bank", ".bnk", ".wem", ".pck"}
             and any(word in item.relative_path.casefold() for word in ("audio", "sound", "streamingassets", "wwise", "fmod"))]
    tokens = evidence_from_tokens(context, TOKENS, "binary_string", "Assinatura de middleware de áudio.")
    evidence = evidence_from_files(names, "file", "Biblioteca com nome de middleware.")
    evidence += evidence_from_files(banks, "audio_bank", "Banco ou mídia de áudio encontrado.")
    evidence += tokens
    if evidence:
        all_text = " ".join(item.description + " " + " ".join(item.matches) + " " + item.path for item in evidence).casefold()
        technologies = [label for needle, label in (("fmod", "FMOD"), ("wwise", "Wwise")) if needle in all_text]
        summary = f"{' / '.join(technologies)} detectado." if technologies else "Arquivos/bancos de áudio encontrados."
        status = "detected" if technologies else "probable"
        return LayerFinding("audio", "Áudio", status, "high" if technologies else "medium",
                            90 if technologies else 68, summary, evidence[:18],
                            ["A presença do middleware não significa que os bancos devam ser alterados.",
                             "O scanner não abre, extrai nem substitui áudio."], False)
    audio_files = [item for item in context.files if item.suffix in {".ogg", ".wav", ".mp3", ".flac", ".opus"}]
    if audio_files:
        return LayerFinding("audio", "Áudio", "probable", "low", 45,
                            f"{len(audio_files)} arquivos de áudio encontrados; middleware não identificado.",
                            evidence_from_files(audio_files, "audio_file", "Arquivo de áudio no jogo."),
                            ["Arquivos de áudio isolados não identificam a tecnologia de reprodução."], False)
    return no_evidence("audio", "Áudio", "Tecnologia de áudio não identificada.",
                       ["Nenhum middleware ou formato de áudio conhecido foi localizado na varredura."])

