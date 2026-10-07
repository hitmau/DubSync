from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    __tablename__ = "capture_sessions"
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), default="replaced", nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_start: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    requested_duration: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="preparing", nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventRecord(Base):
    __tablename__ = "dialogue_events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("capture_sessions.session_id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    processed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_observations_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    bbox_json: Mapped[list] = mapped_column(JSON, nullable=False)
    crop_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_frame_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_frame_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    best_frame_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    end_frame_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    assets_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    processing_variants_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    text_phash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_phash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    character: Mapped[str | None] = mapped_column(String(120), nullable=True)
    character_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    region_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    emotion: Mapped[str | None] = mapped_column(String(80), nullable=True)
    review_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timing_quality: Mapped[str] = mapped_column(String(40), default="exact_pts", nullable=False)
    timing_error_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    captured_at: Mapped[str | None] = mapped_column(String(48), nullable=True)
    frame_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    merged_into: Mapped[str | None] = mapped_column(ForeignKey("dialogue_events.event_id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewRecord(Base):
    __tablename__ = "review_history"
    review_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("dialogue_events.event_id"), index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FineSweepRun(Base):
    __tablename__ = "fine_sweep_runs"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("capture_sessions.session_id"), index=True)
    state: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_source_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    checked_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ocr_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    findings_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimelineMarker(Base):
    __tablename__ = "timeline_markers"
    marker_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("capture_sessions.session_id"), index=True)
    source_time: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="audio_start", nullable=False)
    label: Mapped[str] = mapped_column(String(160), default="Início de áudio (futuro)", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RoiCharacterRecord(Base):
    __tablename__ = "roi_characters"
    character_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), default="replaced", nullable=False, index=True)
    roi_id: Mapped[str] = mapped_column(String(96), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    alias: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    color: Mapped[str] = mapped_column(String(7), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sample_mp4_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_mp4_name: Mapped[str | None] = mapped_column(String(260), nullable=True)
    sample_mp4_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sample_mp3_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_mp3_name: Mapped[str | None] = mapped_column(String(260), nullable=True)
    sample_mp3_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    photo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_name: Mapped[str | None] = mapped_column(String(260), nullable=True)
    photo_mime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    photo_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DubbingProject(Base):
    __tablename__ = "projects"
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    game_title: Mapped[str] = mapped_column(String(160), nullable=False)
    game_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_language: Mapped[str] = mapped_column(String(16), default="pt", nullable=False)
    dub_language: Mapped[str] = mapped_column(String(16), default="pt-BR", nullable=False)
    original_language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    capture_language: Mapped[str] = mapped_column(String(16), default="pt", nullable=False)
    platform: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    narrative_type: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, index=True)
    cover_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectTextReplacementSettings(Base):
    __tablename__ = "project_text_replacement_settings"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), primary_key=True)
    rules_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GameBuild(Base):
    __tablename__ = "game_builds"
    game_build_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    platform: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GameProfile(Base):
    __tablename__ = "game_profiles"
    game_profile_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    profile_key: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    game_build_id: Mapped[str | None] = mapped_column(ForeignKey("game_builds.game_build_id"), nullable=True)
    engine: Mapped[str] = mapped_column(String(80), default="unknown", nullable=False)
    executable_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    install_root: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_profile_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "profile_key", name="uq_game_profiles_project_key"),)


class IntegrationPlugin(Base):
    __tablename__ = "integration_plugins"
    plugin_record_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(96), nullable=False)
    version: Mapped[str] = mapped_column(String(48), nullable=False)
    api_version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    engines_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    games_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    capabilities_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), default="builtin", nullable=False)
    trust_status: Mapped[str] = mapped_column(String(24), default="builtin", nullable=False)
    install_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="available", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("plugin_id", "version", name="uq_integration_plugins_id_version"),)


class GamePluginBinding(Base):
    __tablename__ = "game_plugin_bindings"
    binding_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    game_profile_id: Mapped[str] = mapped_column(ForeignKey("game_profiles.game_profile_id"), index=True, nullable=False)
    plugin_record_id: Mapped[str] = mapped_column(ForeignKey("integration_plugins.plugin_record_id"), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="configured", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index(
        "uq_game_plugin_bindings_active_profile", "game_profile_id", unique=True,
        sqlite_where=text("status != 'inactive'"),
    ),)


class RuntimePackage(Base):
    __tablename__ = "runtime_packages"
    package_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    game_profile_id: Mapped[str] = mapped_column(ForeignKey("game_profiles.game_profile_id"), index=True, nullable=False)
    binding_id: Mapped[str] = mapped_column(ForeignKey("game_plugin_bindings.binding_id"), nullable=False)
    package_version: Mapped[str] = mapped_column(String(48), nullable=False)
    package_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    audio_format: Mapped[str] = mapped_column(String(16), nullable=False)
    mapped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ready", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeTestRun(Base):
    __tablename__ = "runtime_test_runs"
    test_run_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    game_profile_id: Mapped[str] = mapped_column(ForeignKey("game_profiles.game_profile_id"), index=True, nullable=False)
    package_id: Mapped[str | None] = mapped_column(ForeignKey("runtime_packages.package_id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    expected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuntimeTestItem(Base):
    __tablename__ = "runtime_test_items"
    test_item_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    test_run_id: Mapped[str] = mapped_column(ForeignKey("runtime_test_runs.test_run_id"), index=True, nullable=False)
    occurrence_id: Mapped[str | None] = mapped_column(ForeignKey("dialogue_occurrences.occurrence_id"), nullable=True)
    expected_trigger_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    received_trigger_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    expected_take_id: Mapped[str | None] = mapped_column(ForeignKey("audio_takes.take_id"), nullable=True)
    played_take_id: Mapped[str | None] = mapped_column(ForeignKey("audio_takes.take_id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), default="dialogue_start", nullable=False)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diagnostic_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DubbingCharacter(Base):
    __tablename__ = "characters"
    character_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    legacy_character_profile_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    default_voice_profile_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    name_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    voice_profile_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.character_id"), index=True, nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="omnivoice", nullable=False)
    provider_voice_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    preset_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    voice_mode: Mapped[str] = mapped_column(String(20), default="clone", nullable=False)
    reference_asset_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reference_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    default_speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    default_instruct: Mapped[str] = mapped_column(Text, default="", nullable=False)
    generation_settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    use_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="missing_reference", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VoiceReferenceAsset(Base):
    __tablename__ = "voice_reference_assets"
    reference_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    voice_profile_id: Mapped[str] = mapped_column(ForeignKey("voice_profiles.voice_profile_id"), index=True, nullable=False)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_path: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_rate: Mapped[int] = mapped_column(Integer, default=24000, nullable=False)
    channels: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    codec: Mapped[str] = mapped_column(String(40), default="pcm_s16le", nullable=False)
    reference_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(24), default="uploaded", nullable=False)
    source_uri_or_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_status: Mapped[str] = mapped_column(String(24), default="needs_review", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DubbingScene(Base):
    __tablename__ = "scenes"
    scene_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_scenes_project_key"),)


class DubbingRoute(Base):
    __tablename__ = "routes"
    route_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    game_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    choices_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SceneEdge(Base):
    __tablename__ = "scene_edges"
    edge_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    from_scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.scene_id"), index=True, nullable=False)
    to_scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.scene_id"), index=True, nullable=False)
    choice_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    condition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaptureTraversal(Base):
    __tablename__ = "capture_traversals"
    traversal_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(ForeignKey("capture_sessions.session_id"), index=True, nullable=False)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("routes.route_id"), nullable=True)
    game_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    choices_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DialogueLine(Base):
    __tablename__ = "dialogue_lines"
    line_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    character_id: Mapped[str | None] = mapped_column(ForeignKey("characters.character_id"), index=True, nullable=True)
    source_language: Mapped[str] = mapped_column(String(16), nullable=False)
    dub_language: Mapped[str] = mapped_column(String(16), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    dub_text: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, default="", nullable=False)
    emotion: Mapped[str | None] = mapped_column(String(80), nullable=True)
    emotion_intensity: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    editorial_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DialogueOccurrence(Base):
    __tablename__ = "dialogue_occurrences"
    occurrence_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    line_id: Mapped[str] = mapped_column(ForeignKey("dialogue_lines.line_id"), index=True, nullable=False)
    scene_id: Mapped[str | None] = mapped_column(ForeignKey("scenes.scene_id"), index=True, nullable=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("routes.route_id"), index=True, nullable=True)
    local_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    condition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    target_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_trigger_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    runtime_trigger_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="unmapped", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaptureLink(Base):
    __tablename__ = "capture_links"
    capture_link_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("dialogue_occurrences.occurrence_id"), index=True, nullable=False)
    traversal_id: Mapped[str | None] = mapped_column(ForeignKey("capture_traversals.traversal_id"), nullable=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("capture_sessions.session_id"), index=True, nullable=False)
    event_id: Mapped[str] = mapped_column(ForeignKey("dialogue_events.event_id"), index=True, nullable=False)
    source_start_time: Mapped[float] = mapped_column(Float, nullable=False)
    source_end_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("event_id", "occurrence_id", name="uq_capture_link_event_occurrence"),)


class AudioTake(Base):
    __tablename__ = "audio_takes"
    take_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    line_id: Mapped[str] = mapped_column(ForeignKey("dialogue_lines.line_id"), index=True, nullable=False)
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("dialogue_occurrences.occurrence_id"), index=True, nullable=False)
    voice_profile_id: Mapped[str] = mapped_column(ForeignKey("voice_profiles.voice_profile_id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="omnivoice", nullable=False)
    provider_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_name: Mapped[str] = mapped_column(String(120), default="k2-fsa/OmniVoice", nullable=False)
    model_revision: Mapped[str | None] = mapped_column(String(120), nullable=True)
    text_used: Mapped[str] = mapped_column(Text, nullable=False)
    instruct_used: Mapped[str] = mapped_column(Text, default="", nullable=False)
    speed_used: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    generation_settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    target_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_delta_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_delta_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_master_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TtsJob(Base):
    __tablename__ = "tts_jobs"
    job_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    take_id: Mapped[str] = mapped_column(ForeignKey("audio_takes.take_id"), index=True, nullable=False)
    provider_job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(48), index=True, nullable=True)
    batch_mode: Mapped[str] = mapped_column(String(20), default="individual", nullable=False)


class AudioTakeReview(Base):
    __tablename__ = "audio_take_review_history"
    review_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    take_id: Mapped[str] = mapped_column(ForeignKey("audio_takes.take_id"), index=True, nullable=False)
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("dialogue_occurrences.occurrence_id"), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    new_status: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InterpretationPreset(Base):
    __tablename__ = "interpretation_presets"
    preset_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    emotion: Mapped[str] = mapped_column(String(80), nullable=False)
    intensity: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    instruct: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_dialogue_lines_character", DialogueLine.character_id)
Index("ix_dialogue_occurrences_line", DialogueOccurrence.line_id)
Index("ix_dialogue_occurrences_scene", DialogueOccurrence.scene_id)
Index("ix_capture_links_event", CaptureLink.event_id)
Index("ix_capture_links_occurrence", CaptureLink.occurrence_id)
Index("ix_audio_takes_occurrence", AudioTake.occurrence_id)
Index("ix_audio_takes_line", AudioTake.line_id)
Index("ix_audio_takes_voice", AudioTake.voice_profile_id)
Index("ix_tts_jobs_take", TtsJob.take_id)
