from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GenerationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration: float | None = Field(default=None, ge=0.1, le=60)
    denoise: bool = True
    num_step: int = Field(default=32, ge=16, le=32)
    guidance_scale: float = Field(default=2, ge=0, le=10)
    t_shift: float = Field(default=0.1, ge=0, le=2)
    position_temperature: float = Field(default=5, ge=0, le=10)
    class_temperature: float = Field(default=0, ge=0, le=10)
    layer_penalty_factor: float = Field(default=5, ge=0, le=10)
    normalize_text: bool = False
    preprocess_prompt: bool = True
    postprocess_output: bool = True
    pad_duration: float = Field(default=0.1, ge=0, le=10)
    fade_duration: float = Field(default=0.1, ge=0, le=10)
    audio_chunk_duration: float = Field(default=15, ge=0.5, le=120)
    audio_chunk_threshold: float = Field(default=30, ge=0.5, le=600)


class PrepareVoiceBody(BaseModel):
    voice_profile_id: str = Field(min_length=1, max_length=48)
    reference_path: str = Field(min_length=1, max_length=500)
    reference_text: str | None = Field(default=None, max_length=4000)
    language: str = Field(default="pt", min_length=2, max_length=16)


class GenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    take_id: str = Field(pattern=r"^TAKE_[A-F0-9]{12}$")
    voice_profile_id: str = Field(min_length=1, max_length=48)
    voice_mode: str = Field(default="clone", pattern=r"^(clone|design)$")
    text: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="pt", min_length=2, max_length=16)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    instruct: str = Field(default="", max_length=2000)
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)
    output_relative_path: str = Field(min_length=1, max_length=500)
