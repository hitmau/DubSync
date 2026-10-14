"""Attributes accepted by OmniVoice 0.2.1 voice design for PT-BR speech."""

VOICE_DESIGN_GROUPS = (
    ("male", "female"),
    ("child", "teenager", "young adult", "middle-aged", "elderly"),
    ("very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"),
    ("whisper",),
)


def validate_voice_design(instruct: str) -> str:
    """Return a canonical prompt or raise ValueError with a useful message."""
    tokens = [part.strip().casefold() for part in str(instruct).replace("，", ",").split(",") if part.strip()]
    if not tokens:
        raise ValueError("Escolha ao menos um atributo de voz documentado.")
    unknown = [token for token in tokens if not any(token in group for group in VOICE_DESIGN_GROUPS)]
    if unknown:
        raise ValueError("Atributo de voz não aceito pelo OmniVoice: " + ", ".join(unknown) + ". Use gênero, idade, pitch ou sussurro.")
    for group in VOICE_DESIGN_GROUPS:
        selected = [token for token in tokens if token in group]
        if len(selected) > 1:
            raise ValueError("Escolha apenas um atributo por categoria de voz: " + ", ".join(selected) + ".")
    return ", ".join(tokens)
