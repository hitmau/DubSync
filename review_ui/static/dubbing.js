(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const state = {
    overview: null, lines: [], playbackLines: [], playbackLinesSessionId: "", characters: [], selectedLine: null, selectedCharacterId: "",
    lineSort: { key: "start", direction: "asc" },
    blockPlan: null, blockPlanSessionId: "", blockPlanCharacterId: "", blockPlanRootId: "", videoBatchId: "", videoBatchRootId: "",
    selectedTakeId: "", selectedSessionId: "", provider: null, queue: null, poll: 0,
    loaded: false, loading: false, refreshRequested: false, referenceUploadUrl: "", profilePreviewUrl: "", profilePreviewProfileId: "", profilePresetKey: "", editingProfileId: "",
    roiSyncAttempted: new Set(), roiAudioRevision: 0, selectedOccurrenceIds: new Set(), timelineSelectedOccurrenceIds: new Set(), selectionBatchId: "",
    voiceAssignmentOptionsDirty: false,
    referenceRemoveRequested: false,
    selectionBatchOccurrenceIds: new Set(), selectionBatchStatuses: new Map(),
    batchGeneratedOccurrenceIds: new Set(), designCandidates: [], designCandidateNodes: new Map(), candidateCache: new Map(),
    candidateProfileId: "", candidateLoadedProfileId: "", candidateItems: [], candidatePollTimer: 0, candidateRefreshInFlight: false,
    designLegacyTokens: [], candidateGenerating: false, pendingBatch: null,
    timelinePlaybackToken: 0, timelinePreviousTime: null, autoPlaybackOccurrenceId: "",
    autoPlaybackPendingId: "", autoPlaybackTakeId: "", autoPlaybackWarned: false,
    actionCharacterId: "",
  };
  let lineAutosaveTimer = 0;
  let lineAutosaveQueue = Promise.resolve(true);
  let lineEditRevision = 0;
  let lineDirty = false;
  let profileDraftDirty = false;
  let dubbingPollGeneration = 0;
  let lineTableAudio = null;
  let lineTablePlayingTakeId = "";
  const waveformCache = new Map();
  const waveformPending = new Map();
  const lineRowByOccurrence = new Map();
  const timelinePinByOccurrence = new Map();
  let waveformRenderRevision = 0;
  const DUB_TIMELINE_VIEW_KEY = "dubsync.dub.timelineViews.v1";
  let pendingSharedNavigation = null;
  let dubTimelineViews = {};
  let dubTimelineRange = { start: 0, end: 1, span: 1 };
  let dubTimelineMarquee = null, dubTimelinePinDrag = null, dubTimelineIgnoreClick = false, dubTimelineAnchor = "";
  try { dubTimelineViews = JSON.parse(localStorage.getItem(DUB_TIMELINE_VIEW_KEY) || "{}"); } catch { dubTimelineViews = {}; }
  const statusNames = {
    accepted: "aprovada", corrected: "corrigida", pending: "pendente", discarded: "descartada",
    queued: "na fila", generating: "gerando", review: "sincronizando", approved: "pronta no jogo",
    rejected: "rejeitado", error: "erro", cancelled: "cancelado", generated: "sincronizando",
    publishing: "sincronizando com o jogo", publish_error: "falha ao enviar ao jogo",
    ready: "pronta no jogo", superseded: "substituída",
  };
  // OmniVoice 0.2.1 rejects free-form rhythm/texture/direction tokens in voice design.
  const DESIGN_PROMPT_CHOICES = [];

  function toast(message, bad = false) {
    const node = $("#toast");
    if (!node) return;
    node.textContent = message;
    node.style.background = bad ? "#f1abb0" : "#c3f2da";
    node.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove("show"), 3200);
  }

  async function request(path, options = {}) {
    const response = await fetch(`/api/dubbing${path}`, {
      ...options,
      headers: options.body instanceof Blob ? options.headers : {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const text = await response.text();
    let result = null;
    try { result = text ? JSON.parse(text) : {}; } catch { result = { message: text }; }
    if (!response.ok) {
      const detail = result?.detail;
      const message = typeof detail === "string" ? detail : detail?.message || result?.message || `HTTP ${response.status}`;
      throw new Error(message);
    }
    return result;
  }

  async function requestCapture(path, options = {}) {
    const response = await fetch(path, options);
    const text = await response.text();
    let result = null;
    try { result = text ? JSON.parse(text) : {}; } catch { result = { message: text }; }
    if (!response.ok) {
      const detail = result?.detail;
      throw new Error(typeof detail === "string" ? detail : detail?.message || result?.message || `HTTP ${response.status}`);
    }
    return result;
  }

  async function loadDubbingRuntimeVolume() {
    const slider = $("#dubRuntimeVolume");
    if (!slider) return;
    slider.disabled = true;
    try {
      const result = await requestCapture("/api/integration/profile");
      const profile = result.profile;
      if (!profile) throw new Error("Configure o perfil do jogo na Integração para controlar o volume.");
      const volumePercent = Math.max(0, Math.min(3162.27766, Number(profile.audio_profile_json?.volume_percent ?? 100)));
      const db = volumePercent <= 0 ? -60 : Math.max(-59.9, Math.min(30, 20 * Math.log10(volumePercent / 100)));
      slider.value = String(Math.round(db));
      renderDubbingRuntimeVolume();
      slider.disabled = false;
    } catch (error) {
      setDubbingRuntimeVolumeStatus(error.message);
    }
  }

  function setDubbingRuntimeVolumeStatus(message) {
    const status = $("#dubRuntimeVolumeStatus");
    const value = $("#dubRuntimeVolumeValue");
    if (status) status.textContent = message;
    if (value) value.title = message;
  }

  function renderDubbingRuntimeVolume() {
    const slider = $("#dubRuntimeVolume");
    if (!slider) return;
    const db = Number(slider.value);
    $("#dubRuntimeVolumeValue").textContent = db <= -60 ? "Mudo" : `${db > 0 ? "+" : ""}${db.toFixed(1).replace(/\.0$/, "")} dB`;
  }

  async function saveDubbingRuntimeVolume() {
    const slider = $("#dubRuntimeVolume");
    if (!slider || slider.disabled) return;
    const db = Number(slider.value);
    const volume = db <= -60 ? 0 : Number((100 * 10 ** (db / 20)).toFixed(6));
    const label = db <= -60 ? "mudo" : `${db > 0 ? "+" : ""}${db.toFixed(1).replace(/\.0$/, "")} dB`;
    slider.disabled = true;
    setDubbingRuntimeVolumeStatus("Salvando volume das dublagens…");
    try {
      const result = await requestCapture("/api/integration/profile/volume", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ volume_percent: volume }),
      });
      const applied = result.volume_runtime_status === "applied";
      setDubbingRuntimeVolumeStatus(applied
        ? `Ganho ${label} salvo; será aplicado às próximas falas sem regravar os áudios.`
        : `Ganho ${label} salvo no perfil. O runtime do jogo ainda não aplicou a mudança (${result.volume_runtime_status}).`);
    } catch (error) {
      setDubbingRuntimeVolumeStatus(error.message);
      toast(`Não foi possível salvar o volume: ${error.message}`, true);
    } finally {
      slider.disabled = false;
    }
  }

  function initializeDubbingRuntimeVolume() {
    const slider = $("#dubRuntimeVolume");
    if (!slider) return;
    slider.addEventListener("input", () => {
      renderDubbingRuntimeVolume();
    });
    slider.addEventListener("change", () => void saveDubbingRuntimeVolume());
    void loadDubbingRuntimeVolume();
  }

  function setLineSaveStatus(message, status = "saved", detail = "") {
    const node = $("#dubLineSaveStatus");
    if (!node) return;
    node.textContent = message;
    node.className = `dub-autosave-status ${status}`;
    node.title = detail;
  }

  function updateProfileSaveState() {
    const button = $("#dubSaveProfile"), status = $("#dubProfileSaveStatus");
    if (!button) return;
    const owner = state.characters.find((item) => item.character_id === $("#dubVoiceCharacterId").value);
    const canSave = Boolean($("#dubProfileName").value.trim() && $("#dubVoiceCharacterId").value && owner?.name_verified);
    if (!button.classList.contains("saving")) button.disabled = !canSave;
    if (status && !button.classList.contains("saved")) {
      status.textContent = profileDraftDirty ? "Alterações pendentes. Clique em Salvar perfil para gravá-las." : "Alterações do perfil só são gravadas ao salvar.";
      status.className = profileDraftDirty ? "pending" : "";
    }
    button.title = owner?.name_verified ? "Salva no perfil selecionado os ajustes das seções 2 e 3." : "Confira a identidade do personagem antes de salvar o perfil vocal.";
  }

  function markProfileDraftDirty() {
    profileDraftDirty = true;
    updateProfileSaveState();
  }

  function confirmDiscardProfileDraft() {
    if (!profileDraftDirty) return true;
    return confirm("Há alterações do perfil vocal ainda não salvas. Descartar essas alterações?");
  }

  function scheduleLineAutosave(delay = 450) {
    if (!state.selectedLine) return Promise.resolve(false);
    lineDirty = true;
    lineEditRevision += 1;
    setLineSaveStatus("Alterações pendentes…", "pending");
    clearTimeout(lineAutosaveTimer);
    lineAutosaveTimer = 0;
    if (delay <= 0) return saveLine();
    lineAutosaveTimer = window.setTimeout(() => { lineAutosaveTimer = 0; void saveLine(); }, delay);
    return Promise.resolve(true);
  }

  function clearProfilePreview() {
    const audio = $("#dubProfilePreviewAudio");
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      audio.classList.add("hidden");
    }
    if (state.profilePreviewUrl) URL.revokeObjectURL(state.profilePreviewUrl);
    state.profilePreviewUrl = "";
    state.profilePreviewProfileId = "";
  }

  function canReplaceProfilePreview(profile) {
    const design = $("#dubLocalProfileMode").value === "design";
    const modeMatches = profile?.voice_mode === (design ? "design" : "clone");
    const designMatches = !design || profile?.default_instruct === voiceDesignInstruction();
    const authorized = $("#dubProfileAuthorized").checked;
    const regularPreview = Boolean(profile?.ready && modeMatches && designMatches && authorized);
    const designPreview = Boolean(design && profile?.active && profile.use_authorized
      && modeMatches && designMatches && authorized);
    return Boolean(profile?.voice_profile_id === state.profilePreviewProfileId && (regularPreview || designPreview));
  }

  function fmtTime(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
    const value = Math.max(0, Math.floor(Number(seconds)));
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }
  function fmtSourceTime(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
    const milliseconds = Math.round(Math.max(0, Number(seconds)) * 1000);
    const hours = Math.floor(milliseconds / 3600000);
    const minutes = Math.floor(milliseconds / 60000) % 60;
    const wholeSeconds = Math.floor(milliseconds / 1000) % 60;
    const fraction = milliseconds % 1000;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}.${String(fraction).padStart(3, "0")}`;
  }
  function fmtClock(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
    const value = Math.max(0, Math.floor(Number(seconds))), hours = Math.floor(value / 3600);
    const minutes = Math.floor(value / 60) % 60, remaining = value % 60;
    return hours ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
      : `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
  }
  function fmtDuration(ms) {
    const value = Math.max(0, Number(ms) || 0);
    return `${(value / 1000).toFixed(2)} s`;
  }
  function labelStatus(status) { return statusNames[status] || status || "—"; }
  function make(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }
  function setText(selector, value) { const node = $(selector); if (node) node.textContent = value ?? "—"; }
  function selectedCharacter() { return state.characters.find((item) => item.character_id === state.selectedCharacterId); }
  function allProfiles() { return state.characters.flatMap((character) => (character.voice_profiles || []).map((profile) => ({ ...profile, owner_name: character.display_name || character.name }))); }
  function findProfile(profileId) { return allProfiles().find((profile) => profile.voice_profile_id === profileId) || null; }
  function characterVoiceStatus(character) {
    return (character.voice_profiles || []).some((profile) => profile.ready) ? "ready"
      : !character.name_verified ? "needs_review" : "no_voice";
  }
  function fillCharacterVoiceProfileSelect(select, character, activeProfiles = allProfiles().filter((profile) => profile.active)) {
    const selectedId = character.default_voice_profile_id || "";
    select.replaceChildren(make("option", "", "Sem perfil"));
    for (const profile of activeProfiles) {
      const option = make("option", "", `${profile.name} · ${profile.owner_name}`);
      option.value = profile.voice_profile_id;
      select.append(option);
    }
    select.value = selectedId;
    select.disabled = !character.name_verified;
    if (!character.name_verified) select.title = "Confira a identidade do personagem antes de associar uma voz.";
    else select.removeAttribute("title");
  }
  function refreshCharacterVoiceProfileControls(characterId = "") {
    const activeProfiles = allProfiles().filter((profile) => profile.active);
    for (const row of document.querySelectorAll(".dub-character-card[data-character-id]")) {
      if (characterId && row.dataset.characterId !== characterId) continue;
      const character = state.characters.find((item) => item.character_id === row.dataset.characterId);
      if (!character) continue;
      const status = row.querySelector(".dub-character-state");
      if (status) {
        character.voice_status = characterVoiceStatus(character);
        status.className = `dub-character-state ${character.voice_status}`;
        status.textContent = character.voice_status === "ready" ? "READY"
          : character.voice_status === "needs_review" ? "REVISAR" : "SEM VOZ";
      }
      const note = row.querySelector(".dub-character-info small");
      if (note) note.textContent = `${(character.voice_profiles || []).length} perfil(is) · ${character.name_verified ? "nome conferido" : "conferir nome"}`;
      const select = row.querySelector(".dub-character-voice-select");
      if (select) fillCharacterVoiceProfileSelect(select, character, activeProfiles);
    }
  }
  function refreshCharacterVoiceStatus(character) {
    if (!character) return;
    character.voice_status = characterVoiceStatus(character);
    const row = [...document.querySelectorAll(".dub-character-card[data-character-id]")]
      .find((item) => item.dataset.characterId === character.character_id);
    if (!row) return;
    const status = row.querySelector(".dub-character-state");
    if (status) {
      status.className = `dub-character-state ${character.voice_status}`;
      status.textContent = character.voice_status === "ready" ? "READY"
        : character.voice_status === "needs_review" ? "REVISAR" : "SEM VOZ";
    }
    const note = row.querySelector(".dub-character-info small");
    if (note) note.textContent = `${(character.voice_profiles || []).length} perfil(is) · ${character.name_verified ? "nome conferido" : "conferir nome"}`;
  }
  function refreshStoredVoiceProfileControls(character) {
    if (state.voiceAssignmentOptionsDirty) {
      refreshCharacterVoiceProfileControls();
      state.voiceAssignmentOptionsDirty = false;
    } else refreshCharacterVoiceStatus(character);
  }
  function storeVoiceProfile(profile, { refreshViews = true } = {}) {
    const character = state.characters.find((item) => item.character_id === profile?.character_id);
    if (!character || !profile?.voice_profile_id) return null;
    character.voice_profiles ||= [];
    const index = character.voice_profiles.findIndex((item) => item.voice_profile_id === profile.voice_profile_id);
    const previous = index >= 0 ? character.voice_profiles[index] : null;
    const saved = { ...(previous || {}), ...profile };
    if (!previous || previous.name !== saved.name || previous.active !== saved.active) state.voiceAssignmentOptionsDirty = true;
    if (index >= 0) character.voice_profiles[index] = saved;
    else character.voice_profiles.push(saved);
    character.voice_profiles.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    character.voice_status = characterVoiceStatus(character);
    const boundCharacters = new Set([character.character_id]);
    for (const line of state.lines) if (line.voice_profile_id === saved.voice_profile_id) boundCharacters.add(line.character_id);
    for (const line of state.playbackLines) if (line.voice_profile_id === saved.voice_profile_id) boundCharacters.add(line.character_id);
    for (const owner of state.characters) {
      const profileIsDefault = owner.default_voice_profile_id === saved.voice_profile_id;
      const isImplicitDefault = owner.character_id === character.character_id && !owner.default_voice_profile_id;
      if (profileIsDefault || boundCharacters.has(owner.character_id) || isImplicitDefault) {
        applyCharacterVoiceAssignment(owner, owner.default_voice_profile_id);
      }
    }
    if (refreshViews) {
      refreshStoredVoiceProfileControls(character);
      if (character.character_id === state.selectedCharacterId) renderCharacterDetail(character);
    }
    return saved;
  }
  function storeCharacter(character) {
    if (!character?.character_id) return null;
    const index = state.characters.findIndex((item) => item.character_id === character.character_id);
    if (index < 0) return null;
    Object.assign(state.characters[index], character);
    const current = state.characters[index];
    current.voice_status ||= characterVoiceStatus(current);
    refreshCharacterVoiceProfileControls(current.character_id);
    if (current.character_id === state.selectedCharacterId) renderCharacterDetail(current);
    return current;
  }
  function removeVoiceProfileFromState(profileId, deactivated = false) {
    const profile = findProfile(profileId);
    if (!profile) return null;
    const character = state.characters.find((item) => item.character_id === profile.character_id);
    if (!character) return null;
    if (deactivated) {
      const stored = character.voice_profiles.find((item) => item.voice_profile_id === profileId);
      if (stored) { stored.active = false; stored.status = "inactive"; }
    } else {
      character.voice_profiles = character.voice_profiles.filter((item) => item.voice_profile_id !== profileId);
      for (const owner of state.characters) {
        if (owner.default_voice_profile_id === profileId) owner.default_voice_profile_id = null;
      }
    }
    character.voice_status = characterVoiceStatus(character);
    state.voiceAssignmentOptionsDirty = true;
    const syncLineProfile = (item) => {
      if (item.voice_profile_id !== profileId) return;
      const owner = state.characters.find((entry) => entry.character_id === item.character_id);
      const fallback = (owner?.voice_profiles || []).find((entry) => entry.voice_profile_id === owner.default_voice_profile_id && entry.active)
        || (owner?.voice_profiles || []).find((entry) => entry.active) || null;
      item.voice_profile_id = fallback?.voice_profile_id || null;
      item.voice_profile_name = fallback?.name || null;
      item.voice_ready = Boolean(fallback?.ready);
    };
    state.lines.forEach(syncLineProfile); state.playbackLines.forEach(syncLineProfile); syncLineProfile(state.selectedLine || {});
    refreshStoredVoiceProfileControls(character);
    if (character.character_id === state.selectedCharacterId) renderCharacterDetail(character);
    return character;
  }
  function applyCharacterVoiceAssignment(character, voiceProfileId) {
    const selected = voiceProfileId ? findProfile(voiceProfileId) : null;
    const profile = (selected?.active ? selected : null)
      || (character.voice_profiles || []).find((item) => item.active)
      || null;
    const updateLine = (item) => {
      if (item.character_id !== character.character_id) return;
      item.voice_profile_id = profile?.voice_profile_id || null;
      item.voice_profile_name = profile?.name || null;
      item.voice_ready = Boolean(profile?.ready);
    };
    state.lines.forEach(updateLine);
    state.playbackLines.forEach(updateLine);
    if (state.selectedLine?.character_id === character.character_id) updateLine(state.selectedLine);
    return profile;
  }
  function hasRoiAudio(character) { return Boolean(character && (character.sample_audio_available ?? character.sample_mp3_available)); }
  function roiAudioName(character) { return character?.sample_audio_name || character?.sample_mp3_name || "áudio anexado no personagem"; }
  function profileReferenceName(profile) {
    const note = profile?.reference?.source_uri_or_note || "";
    const sourceFile = note.match(/(?:^|;)\s*source_file=([^;]+)/)?.[1];
    if (sourceFile) return sourceFile.trim();
    const uploadedName = note.match(/Arquivo selecionado no Studio de Dublagem:\s*([^;]+)/)?.[1];
    if (uploadedName) return uploadedName.trim();
    const pathName = profile?.reference?.original_path?.split(/[\\/]/).pop();
    return pathName || (profile?.reference ? "áudio salvo no perfil" : "");
  }
  function setVoiceMode(mode) {
    $("#dubLocalProfileMode").value = mode;
    document.querySelectorAll('input[name="dubVoiceSource"]').forEach((radio) => { radio.checked = radio.value === mode; });
  }
  function profileUsesCurrentRoiAudio(profile, character) {
    const reference = profile?.reference;
    if (!reference || !["roi_character_mp3", "roi_character_audio"].includes(reference.source_type)) return false;
    const note = reference.source_uri_or_note || "";
    const name = note.match(/(?:^|;)\s*source_file=([^;]+)/)?.[1]?.trim();
    const bytes = note.match(/(?:^|;)\s*source_bytes=(\d+)/)?.[1];
    return name === roiAudioName(character) && (!bytes || Number(bytes) === Number(character.sample_audio_bytes ?? character.sample_mp3_bytes));
  }
  async function syncRoiVoiceReference(profile, character, { force = false } = {}) {
    if (!profile?.voice_profile_id || profile.voice_mode !== "clone" || profile.provider_voice_key === "dubsync:design-candidate" || !hasRoiAudio(character)) return true;
    if (!force && profileUsesCurrentRoiAudio(profile, character)) return true;
    const profileId = profile.voice_profile_id;
    const key = `${profileId}|${character.character_id}|${roiAudioName(character)}|${character.sample_audio_bytes ?? character.sample_mp3_bytes ?? ""}|${state.roiAudioRevision}`;
    if (state.roiSyncAttempted.has(key)) return true;
    state.roiSyncAttempted.add(key);
    const status = $("#dubProfileReferenceStatus");
    if (status) status.replaceChildren(make("span", "", `Preparando automaticamente ${roiAudioName(character)}…`));
    try {
      const synced = await request(`/voice-profiles/${encodeURIComponent(profileId)}/sync-roi-audio`, { method: "POST" });
      const keepEditor = state.editingProfileId === profileId && $("#dubVoiceCharacterId").value === character.character_id;
      const latest = storeVoiceProfile(synced, { refreshViews: false }) || findProfile(profileId);
      if (!keepEditor || state.editingProfileId !== profileId) {
        refreshVoiceProfileStatus(profileId);
        return true;
      }
      if (latest) refreshVoiceProfileStatus(profileId, { updateModeControls: true });
      return true;
    } catch (error) {
      if (state.editingProfileId === profileId && status) status.replaceChildren(make("span", "", `Não foi possível preparar ${roiAudioName(character)}: ${error.message}`));
      toast(`Falha ao usar o áudio da ROI: ${error.message}`, true);
      return false;
    }
  }
  function generationSettings(profile = null) {
    const durationValue = Number.parseFloat($("#dubDuration").value);
    return {
      duration: Number.isFinite(durationValue) ? durationValue : null,
      denoise: $("#dubDenoise").checked,
      num_step: Number($("#dubNumStep").value || 32),
      guidance_scale: Number($("#dubGuidanceScale").value || 2),
      t_shift: Number($("#dubTShift").value || 0.1),
      position_temperature: Number($("#dubPositionTemperature").value || 5),
      class_temperature: Number($("#dubClassTemperature").value || 0),
      layer_penalty_factor: Number($("#dubLayerPenalty").value || 5),
      normalize_text: $("#dubNormalizeText").checked,
      preprocess_prompt: $("#dubPreprocessPrompt").checked,
      postprocess_output: $("#dubPostprocessOutput").checked,
      pad_duration: Number($("#dubPadDuration").value || 0),
      fade_duration: Number($("#dubFadeDuration").value || 0),
      audio_chunk_duration: Number($("#dubAudioChunkDuration").value || 15),
      audio_chunk_threshold: Number($("#dubAudioChunkThreshold").value || 30),
    };
  }
  function applyGenerationSettings(settings = {}) {
    $("#dubDuration").value = settings.duration ?? "";
    $("#dubDenoise").checked = settings.denoise ?? true;
    $("#dubNumStep").value = settings.num_step ?? 32;
    $("#dubGuidanceScale").value = settings.guidance_scale ?? 2;
    $("#dubTShift").value = settings.t_shift ?? 0.1;
    $("#dubPositionTemperature").value = settings.position_temperature ?? 5;
    $("#dubClassTemperature").value = settings.class_temperature ?? 0;
    $("#dubLayerPenalty").value = settings.layer_penalty_factor ?? 5;
    $("#dubNormalizeText").checked = Boolean(settings.normalize_text);
    $("#dubPreprocessPrompt").checked = settings.preprocess_prompt ?? true;
    $("#dubPostprocessOutput").checked = settings.postprocess_output ?? true;
    $("#dubPadDuration").value = settings.pad_duration ?? 0.1;
    $("#dubFadeDuration").value = settings.fade_duration ?? 0.1;
    $("#dubAudioChunkDuration").value = settings.audio_chunk_duration ?? 15;
    $("#dubAudioChunkThreshold").value = settings.audio_chunk_threshold ?? 30;
    $("#dubVoiceEffect").value = ["none", "raspy", "robotic"].includes(settings.voice_effect)
      ? settings.voice_effect : "none";
  }
  function voiceDesignInstruction() {
    const values = [$("#dubDesignGender").value, $("#dubDesignAge").value,
      $("#dubDesignPitch").value, $("#dubDesignStyle").value];
    return values.filter(Boolean).join(", ");
  }
  function updateDesignPromptOptions() {
    const selects = [...$("#dubDesignExtraOptions").querySelectorAll("select")];
    const selectedValues = new Set(selects.map((select) => select.value).filter(Boolean));
    for (const select of selects) {
      for (const option of select.options) {
        option.disabled = Boolean(option.value && option.value !== select.value && selectedValues.has(option.value));
      }
    }
    $("#dubDesignAddOption").disabled = selects.length >= DESIGN_PROMPT_CHOICES.length;
  }
  function addDesignPromptField(value = "") {
    const container = $("#dubDesignExtraOptions");
    const row = make("div", "dub-design-extra-row");
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Complemento experimental de voz");
    const placeholder = make("option", "", "Escolha um complemento…");
    placeholder.value = ""; select.append(placeholder);
    let group = "";
    let groupNode = null;
    for (const choice of DESIGN_PROMPT_CHOICES) {
      if (choice.group !== group) {
        group = choice.group;
        groupNode = document.createElement("optgroup"); groupNode.label = group; select.append(groupNode);
      }
      const option = make("option", "", choice.label); option.value = choice.value; groupNode.append(option);
    }
    select.value = value;
    const remove = make("button", "button quiet dub-design-remove-option", "−");
    remove.type = "button"; remove.setAttribute("aria-label", "Remover este complemento"); remove.title = "Remover complemento";
    remove.addEventListener("click", () => {
      row.remove(); updateDesignPromptOptions(); state.profilePresetKey = "";
      updateProfileModeControls();
      markProfileDraftDirty();
    });
    select.addEventListener("change", () => {
      updateDesignPromptOptions(); state.profilePresetKey = ""; clearDesignCandidates(); updateProfileModeControls();
    });
    row.append(select, remove); container.append(row); updateDesignPromptOptions();
    return select;
  }
  function renderLegacyDesignTokens() {
    const container = $("#dubDesignLegacy"); container.replaceChildren();
    if (!state.designLegacyTokens.length) { container.classList.add("hidden"); return; }
    container.classList.remove("hidden");
    container.append(make("small", "", "Atributos antigos sem suporte encontrados. Eles não serão enviados ao OmniVoice; salve o perfil para limpar a instrução:"));
    state.designLegacyTokens.forEach((token, index) => {
      const chip = make("span", "dub-design-legacy-chip", token);
      const remove = make("button", "", "×"); remove.type = "button"; remove.title = "Remover instrução antiga";
      remove.addEventListener("click", () => {
        state.designLegacyTokens.splice(index, 1); renderLegacyDesignTokens();
        clearDesignCandidates(); updateProfileModeControls();
        markProfileDraftDirty();
      });
      chip.append(remove); container.append(chip);
    });
  }
  function applyVoiceDesignInstruction(value = "") {
    const originalTokens = String(value).split(/[,，]/).map((token) => token.trim()).filter(Boolean);
    const tokens = originalTokens.map((token) => token.toLowerCase());
    const optionsBySelector = [
      ["#dubDesignGender", ["male", "female"]],
      ["#dubDesignAge", ["child", "teenager", "young adult", "middle-aged", "elderly"]],
      ["#dubDesignPitch", ["very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"]],
      ["#dubDesignStyle", ["whisper"]],
    ];
    const recognized = new Set(optionsBySelector.flatMap(([, options]) => options));
    for (const [selector, options] of optionsBySelector) $(selector).value = options.find((option) => tokens.includes(option)) || "";
    const recognizedPrompts = new Set(DESIGN_PROMPT_CHOICES.map((choice) => choice.value));
    $("#dubDesignExtraOptions").replaceChildren();
    state.designLegacyTokens = originalTokens.filter((token) =>
      !recognized.has(token.toLowerCase()) && !recognizedPrompts.has(token.toLowerCase()));
    renderLegacyDesignTokens(); updateDesignPromptOptions();
  }
  function clearDesignCandidates() {
    for (const candidate of state.designCandidates) URL.revokeObjectURL(candidate.url);
    state.designCandidates = [];
  }
  function renderDesignCandidates(items) {
    const container = $("#dubCandidateList"), history = $("#dubCandidateHistory");
    history.classList.toggle("hidden", !items.length);
    const keep = new Set(items.map((candidate) => candidate.candidate_id));
    for (const [candidateId, card] of state.designCandidateNodes) {
      if (!keep.has(candidateId)) { card.remove(); state.designCandidateNodes.delete(candidateId); }
    }
    const profile = findProfile(state.candidateProfileId);
    for (const candidate of [...items].reverse()) {
      let card = state.designCandidateNodes.get(candidate.candidate_id);
      if (!card) {
        card = make("article", "dub-candidate-card");
        const heading = make("div", "dub-candidate-heading");
        const title = make("b", ""); const status = make("small", "dub-candidate-state");
        const text = make("small", "dub-candidate-text");
        const audio = make("audio", "dub-candidate-audio"); audio.controls = true; audio.preload = "metadata";
        const error = make("small", "dub-candidate-error");
        const choose = make("button", "button quiet", "✓ Definir voz oficial"); choose.type = "button";
        choose.addEventListener("click", () => void selectDesignCandidate(candidate.candidate_id, choose));
        heading.append(title, status); card.append(heading, text, audio, error, choose);
        card._candidateNodes = { title, status, text, audio, error, choose };
        state.designCandidateNodes.set(candidate.candidate_id, card); container.append(card);
      }
      const nodes = card._candidateNodes;
      nodes.title.textContent = `Candidato ${candidate.candidate_index} · ${candidate.candidate_id.slice(-4)}`;
      const statusNames = { queued: "Na fila", generating: "Gerando…", completed: "Áudio pronto", error: "Falha" };
      nodes.status.textContent = statusNames[candidate.status] || candidate.status;
      nodes.status.className = `dub-candidate-state ${candidate.status}`;
      nodes.text.textContent = `Texto: ${candidate.text_used}`;
      nodes.error.textContent = candidate.error_message || "";
      nodes.audio.classList.toggle("hidden", candidate.status !== "completed");
      if (candidate.status === "completed" && nodes.audio.dataset.candidateId !== candidate.candidate_id) {
        const query = new URLSearchParams({ project_id: window.DUBSYNC_PROJECT_ID || "" });
        nodes.audio.src = `/api/dubbing/voice-profiles/${encodeURIComponent(candidate.voice_profile_id)}/design-candidates/${encodeURIComponent(candidate.candidate_id)}/audio?${query}`;
        nodes.audio.dataset.candidateId = candidate.candidate_id;
      }
      const isOfficial = profile?.provider_voice_key === "dubsync:design-candidate"
        && profile?.reference?.source_uri_or_note?.includes(candidate.candidate_id);
      nodes.choose.disabled = candidate.status !== "completed" || !profile || profile.voice_mode !== "design"
        || !profile.active || !profile.use_authorized || state.candidateGenerating || isOfficial;
      nodes.choose.textContent = isOfficial ? "✓ Voz oficial" : "✓ Definir voz oficial";
      nodes.choose.title = isOfficial ? "Este candidato é a voz oficial ativa do perfil."
        : nodes.choose.disabled && candidate.status === "completed"
          ? "Volte ao modo Gerar voz para escolher este candidato." : "Usar este áudio como referência oficial do perfil.";
    }
  }
  async function loadDesignCandidates(profileId = state.editingProfileId) {
    if (!profileId) {
      if (state.candidatePollTimer) clearInterval(state.candidatePollTimer);
      state.candidatePollTimer = 0; state.candidateProfileId = ""; state.candidateLoadedProfileId = "";
      state.candidateItems = []; state.designCandidateNodes.clear();
      $("#dubCandidateList").replaceChildren(); $("#dubCandidateHistory").classList.add("hidden"); return;
    }
    state.candidateProfileId = profileId;
    if (state.candidateRefreshInFlight) return;
    state.candidateRefreshInFlight = true;
    try {
      const result = await request(`/voice-profiles/${encodeURIComponent(profileId)}/design-candidates?limit=200`);
      const items = result.items || [];
      state.candidateCache.set(profileId, items);
      while (state.candidateCache.size > 12) state.candidateCache.delete(state.candidateCache.keys().next().value);
      if (state.candidateProfileId !== profileId || state.editingProfileId !== profileId) return;
      state.candidateItems = items;
      state.candidateLoadedProfileId = profileId;
      renderDesignCandidates(items);
      const pending = items.some((candidate) => candidate.status === "queued" || candidate.status === "generating");
      if (pending && !state.candidatePollTimer) {
        state.candidatePollTimer = window.setInterval(() => void loadDesignCandidates(profileId), 1800);
      } else if (!pending && state.candidatePollTimer) {
        clearInterval(state.candidatePollTimer); state.candidatePollTimer = 0;
      }
    } catch (error) {
      if (state.editingProfileId === profileId) console.warn("Candidatos do perfil:", error);
    } finally {
      state.candidateRefreshInFlight = false;
      if (state.candidateProfileId && state.candidateProfileId !== profileId) void loadDesignCandidates(state.candidateProfileId);
    }
  }
  async function generateDesignCandidates() {
    const profile = findProfile(state.editingProfileId);
    const text = $("#dubPreviewText").value.trim();
    const instruction = voiceDesignInstruction();
    if (!profile || profile.voice_mode !== "design" || !profile.use_authorized || profile.default_instruct !== instruction) {
      toast("Salve e autorize este perfil com os atributos atuais antes de gerar candidatos.", true); return;
    }
    if (!text) { toast("Digite uma frase de 3 a 10 segundos para comparar as vozes.", true); return; }
    const count = Math.max(1, Math.min(10, Math.trunc(Number($("#dubCandidateCount").value) || 3)));
    $("#dubCandidateCount").value = count;
    state.candidateGenerating = true;
    const button = $("#dubGenerateCandidates");
    button.disabled = true; button.innerHTML = '<span class="dub-inline-spinner" aria-hidden="true"></span> Gerando variações…';
    try {
      const batch = await request(`/voice-profiles/${encodeURIComponent(profile.voice_profile_id)}/design-candidates`, {
        method: "POST", body: JSON.stringify({ text, instruct: instruction, count,
          speed: Number($("#dubSpeed").value || 1), generation_settings: generationSettings() }),
      });
      toast(`${batch.count} candidato(s) enfileirado(s). A fila segue no servidor se você sair desta tela.`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.candidateGenerating = false;
      button.textContent = "✦ Gerar candidatos";
      updateProfileModeControls();
      void loadDesignCandidates(profile.voice_profile_id);
    }
  }
  async function selectDesignCandidate(candidate, button) {
    const profileId = state.candidateProfileId;
    const profile = findProfile(profileId);
    if (!profile) { toast("Salve o perfil antes de selecionar uma voz oficial.", true); return; }
    button.disabled = true; button.textContent = "Salvando voz…";
    try {
      const saved = await request(`/voice-profiles/${encodeURIComponent(profile.voice_profile_id)}/design-candidates/${encodeURIComponent(candidate)}/select`, { method: "POST" });
      state.selectedCharacterId = profile.character_id;
      const latest = storeVoiceProfile(saved.profile, { refreshViews: false }) || saved.profile;
      refreshVoiceProfileStatus(latest.voice_profile_id, { refreshCandidates: true });
      toast(`“${latest.name}” foi escolhida como voz oficial. O perfil Gerar voz está pronto para produzir os takes com os atributos salvos.`);
    } catch (error) {
      button.disabled = false; button.textContent = "✓ Definir voz oficial";
      toast(error.message, true);
    }
  }
  function updateProfileModeControls() {
    const design = $("#dubLocalProfileMode").value === "design";
    if (!design) state.profilePresetKey = "";
    const character = state.characters.find((entry) => entry.character_id === $("#dubVoiceCharacterId").value) || currentStudioCharacter();
    const profile = findProfile(state.editingProfileId);
    const roiAudio = hasRoiAudio(character);
    const roiCharacterActive = Boolean(character?.active && character?.roi_id);
    const officialCandidate = profile?.provider_voice_key === "dubsync:design-candidate";
    const chosenFile = $("#dubReferenceFile").files?.[0];
    const sourceName = profile?.provider_voice_key === "dubsync:design-candidate" ? "voz oficial escolhida"
      : roiAudio ? roiAudioName(character) : chosenFile?.name || profileReferenceName(profile) || "áudio autorizado";
    document.querySelectorAll('input[name="dubVoiceSource"]').forEach((radio) => { radio.checked = radio.value === (design ? "design" : "clone"); });
    $("#dubCloneSourceLabel").textContent = `Clonar voz com ${sourceName}`;
    $("#dubRoiAudioSource").classList.toggle("hidden", !roiCharacterActive);
    $("#dubRoiAudioSourceName").textContent = roiAudio ? roiAudioName(character) : "Nenhum áudio anexado";
    const roiPlayer = $("#dubRoiAudioPlayer");
    roiPlayer.classList.toggle("hidden", !roiAudio);
    const roiCharacterId = roiAudio ? character.character_id : "";
    const roiAudioKey = roiAudio ? `${roiCharacterId}|${roiAudioName(character)}|${character.sample_audio_bytes ?? character.sample_mp3_bytes ?? ""}|${state.roiAudioRevision}` : "";
    if (roiPlayer.dataset.audioKey !== roiAudioKey) {
      roiPlayer.pause();
      roiPlayer.dataset.audioKey = roiAudioKey;
      roiPlayer.dataset.loadError = "";
      if (roiCharacterId) {
        // Native media requests cannot use the X-DubSync-Project header added
        // to fetch(), so include the active project in the URL for middleware.
        const audioParams = new URLSearchParams();
        if (window.DUBSYNC_PROJECT_ID) audioParams.set("project_id", window.DUBSYNC_PROJECT_ID);
        audioParams.set("v", String(state.roiAudioRevision));
        roiPlayer.src = `/api/dubbing/characters/${encodeURIComponent(roiCharacterId)}/roi-audio?${audioParams}`;
        // Changing src alone does not consistently start a metadata request in
        // the native audio control. load() refreshes duration and enables play.
        roiPlayer.load();
      }
      else { roiPlayer.removeAttribute("src"); roiPlayer.load(); }
    }
    const replaceRoiAudioButton = $("#dubReplaceRoiAudio");
    if (replaceRoiAudioButton) {
      replaceRoiAudioButton.disabled = !roiCharacterActive;
      replaceRoiAudioButton.textContent = roiAudio ? "Trocar áudio no ROI" : "Anexar áudio ao ROI";
      replaceRoiAudioButton.title = "Escolha um MP3 ou WAV para atualizar o áudio deste personagem também na tela Captura.";
    }
    $("#dubReferenceUpload").classList.toggle("hidden", roiAudio);
    $("#dubReferenceFile").disabled = roiAudio;
    $("#dubRemoveReference").classList.toggle("hidden", design || (roiAudio && !officialCandidate));
    $("#dubRemoveReference").disabled = !profile?.reference || (roiAudio && !officialCandidate);
    $("#dubUseRoiReference").classList.toggle("hidden", !officialCandidate || !roiAudio || design);
    $("#dubCloneCard").classList.toggle("hidden", design);
    $("#dubDesignVoiceCard").classList.toggle("hidden", !design);
    $("#dubBrowseModels").disabled = !design;
    $("#dubProfileModeHint").textContent = design
      ? "Defina a voz, salve e autorize. Depois gere prévias e escolha a identidade que será usada nos takes."
      : officialCandidate ? "Este perfil usa a voz oficial escolhida. O áudio da ROI pode ser ouvido e selecionado como outra fonte."
        : roiAudio ? "A referência anexada ao personagem será preparada e usada automaticamente. Você pode voltar para Gerar voz quando quiser."
        : "Anexe uma amostra autorizada em WAV ou MP3. Você pode voltar para Gerar voz quando quiser.";
    const roiSourceNote = $("#dubRoiAudioSourceNote");
    if (roiSourceNote) roiSourceNote.textContent = officialCandidate
      ? "Esta referência está disponível para comparação; a voz oficial escolhida continua ativa até você selecionar este áudio."
        : roiAudio ? "Trocar o arquivo aqui também atualiza o áudio anexado ao personagem na Captura. O perfil em modo clonagem será sincronizado com a nova referência."
          : "Nenhum áudio está anexado. Escolha um MP3 ou WAV para adicioná-lo ao cadastro do personagem na ROI.";
    if (roiSourceNote && roiPlayer.dataset.loadError === roiAudioKey && roiAudioKey) {
      roiSourceNote.textContent = "Não foi possível carregar o áudio. Confira se o arquivo ainda está anexado ao personagem na Captura.";
      roiSourceNote.classList.add("is-error");
    } else roiSourceNote?.classList.remove("is-error");
    $("#dubPreprocessPrompt").disabled = design;
    $("#dubPreprocessPrompt").title = design ? "Só se aplica ao áudio de clonagem." : "Remove silêncios longos da amostra e prepara o texto de referência.";
    $("#dubClearVoiceModel").classList.toggle("hidden", !design || !state.profilePresetKey);
    for (const selector of ["#dubDesignGender", "#dubDesignAge", "#dubDesignPitch", "#dubDesignStyle"]) {
      $(selector).disabled = !design || Boolean(state.profilePresetKey);
    }
    $("#dubDesignAddOption").disabled = !design || Boolean(state.profilePresetKey)
      || $("#dubDesignExtraOptions").querySelectorAll("select").length >= DESIGN_PROMPT_CHOICES.length;
    $("#dubDesignExtraOptions").querySelectorAll("select, button").forEach((control) => {
      control.disabled = !design || Boolean(state.profilePresetKey);
    });
    $("#dubCandidateCount").disabled = !design;
    for (const card of document.querySelectorAll(".dub-voice-model-card")) {
      const selected = card.dataset.voiceModel === state.profilePresetKey;
      card.classList.toggle("selected", selected);
      const button = card.querySelector(".dub-select-model");
      if (button) button.textContent = selected ? "Desmarcar" : "Aplicar atributos";
    }
    const savedModeMatches = profile?.voice_mode === (design ? "design" : "clone");
    const savedDesignMatches = !design || profile?.default_instruct === voiceDesignInstruction();
    const previewableDesign = Boolean(design && profile?.active && profile?.use_authorized
      && savedModeMatches && savedDesignMatches && $("#dubProfileAuthorized").checked);
    const replaceablePreview = canReplaceProfilePreview(profile);
    $("#dubTestProfile").disabled = !(profile?.ready && savedModeMatches && savedDesignMatches && $("#dubProfileAuthorized").checked) && !previewableDesign && !replaceablePreview;
    $("#dubGenerateCandidates").disabled = state.candidateGenerating || !previewableDesign;
    renderVoiceReady(profile, character);
    if (design && profile?.voice_profile_id && state.candidateProfileId === profile.voice_profile_id
        && state.candidateLoadedProfileId !== profile.voice_profile_id && !state.candidateRefreshInFlight) {
      void loadDesignCandidates(profile.voice_profile_id);
    }
  }
  function renderProfileOptions(selectedId = "") {
    const select = $("#dubVoiceSelect");
    const placeholder = make("option", "", "Selecione ou crie um perfil");
    placeholder.value = "";
    select.replaceChildren(placeholder);
    for (const profile of allProfiles().sort((a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name, "pt-BR"))) {
      const option = make("option", "", `${profile.name} · ${profile.owner_name}${profile.active ? (profile.ready ? " · pronto" : " · incompleto") : " · inativo"}`);
      option.value = profile.voice_profile_id;
      select.append(option);
    }
    select.value = allProfiles().some((profile) => profile.voice_profile_id === selectedId) ? selectedId : "";
  }

  function renderProfileEditor(profile = null, owner = null) {
    if (state.profilePreviewProfileId && state.profilePreviewProfileId !== (profile?.voice_profile_id || "")) clearProfilePreview();
    const profileOwner = profile ? state.characters.find((item) => item.character_id === profile.character_id) || owner : owner;
    const nextProfileId = profile?.voice_profile_id || "";
    const profileChanged = state.candidateProfileId !== nextProfileId;
    state.referenceRemoveRequested = false;
    if (profileChanged) {
      if (state.candidatePollTimer) clearInterval(state.candidatePollTimer);
      state.candidatePollTimer = 0;
      state.candidateItems = state.candidateCache.get(nextProfileId) || [];
      state.candidateLoadedProfileId = nextProfileId && state.candidateCache.has(nextProfileId) ? nextProfileId : "";
      state.designCandidateNodes.clear(); $("#dubCandidateList").replaceChildren();
      state.candidateProfileId = nextProfileId;
    }
    state.editingProfileId = nextProfileId;
    profileDraftDirty = false;
    $("#dubVoiceProfileId").value = state.editingProfileId;
    $("#dubVoiceCharacterId").value = profile?.character_id || profileOwner?.character_id || "";
    $("#dubProfileName").value = profile?.name || (profileOwner ? `${profileOwner.display_name || profileOwner.name} PT-BR` : "");
    $("#dubProfileLanguage").value = "pt-BR";
    const attachedRoiAudio = hasRoiAudio(profileOwner);
    setVoiceMode(profile?.voice_mode || (attachedRoiAudio ? "clone" : "design"));
    $("#dubProfileReferenceText").value = profile?.reference?.reference_text || profile?.reference_text || "";
    $("#dubPreviewText").value = profile?.preview_text || "";
    $("#dubProfileAuthorized").checked = Boolean(profile?.use_authorized);
    $("#dubSpeed").value = profile?.default_speed ?? 1;
    $("#dubInstruction").value = profile?.default_direction || "";
    applyVoiceDesignInstruction(profile?.default_instruct || "");
    applyGenerationSettings(profile?.generation_settings_json || {});
    if (profileChanged) clearDesignCandidates();
    state.profilePresetKey = profile?.preset_key || "";
    $("#dubReferenceFile").value = "";
    $("#dubReferencePreview").classList.add("hidden");
    $("#dubProfileOwner").textContent = `Personagem: ${profile?.owner_name || profileOwner?.display_name || profileOwner?.name || "selecione um personagem"}${profileOwner?.name_verified ? " · conferido" : profileOwner ? " · precisa conferir" : ""}`;
    $("#dubEditVoice").disabled = !profile;
    $("#dubToggleVoice").disabled = !profile;
    $("#dubDeleteVoice").disabled = !profile;
    $("#dubToggleVoice").textContent = profile?.active === false ? "Ativar" : "Inativar";
    const fileNote = $(".dub-clone-copy small");
    if (fileNote) fileNote.textContent = profile?.provider_voice_key === "dubsync:design-candidate"
      ? "A amostra oficial foi salva. Em Gerar voz, cada fala usa os atributos vocais salvos no perfil."
      : attachedRoiAudio
      ? "Este áudio do personagem é reconhecido e preparado automaticamente ao escolher Clonar voz."
      : profile?.reference ? `${fmtDuration(profile.reference.duration_ms)} · ${profile.reference.sample_rate} Hz · referência local${profile.reference.quality_status === "needs_review" ? " · aguarda revisão" : ""}`
        : "Prefira uma fala limpa de 3 a 10 s em WAV ou MP3.";
    renderVoiceReady(profile, profileOwner || (state.selectedLine && state.characters.find((entry) => entry.character_id === state.selectedLine.character_id)));
    const authorized = Boolean(profile?.ready);
    $("#dubTestProfile").disabled = !authorized;
    updateProfileSaveState();
    updateProfileModeControls();
    updateGenerateEnabled();
    if (nextProfileId && state.candidateLoadedProfileId === nextProfileId) renderDesignCandidates(state.candidateItems);
    if (profileChanged && nextProfileId && profile?.voice_mode === "design") void loadDesignCandidates(nextProfileId);
  }

  function refreshVoiceProfilePanel(profileId, { select = false } = {}) {
    const profile = findProfile(profileId);
    const owner = profile ? state.characters.find((item) => item.character_id === profile.character_id) : null;
    renderProfileOptions(select ? profileId : $("#dubVoiceSelect").value);
    refreshStoredVoiceProfileControls(owner);
    if (owner?.character_id === state.selectedCharacterId) renderCharacterDetail(owner);
    if (profile && (select || state.editingProfileId === profileId)) {
      renderProfileEditor(profile, owner);
    } else if (!profile && state.editingProfileId === profileId) {
      renderProfileEditor(null, currentStudioCharacter());
    }
    updateGenerateEnabled();
    return profile;
  }

  function refreshVoiceProfileStatus(profileId, { updateModeControls = false, refreshCandidates = false } = {}) {
    const profile = findProfile(profileId);
    if (!profile) return null;
    const owner = state.characters.find((item) => item.character_id === profile.character_id);
    const selectedId = $("#dubVoiceSelect").value;
    // This selector alone depends on a profile's name, active state, and readiness.
    renderProfileOptions(selectedId);
    refreshStoredVoiceProfileControls(owner);
    if (owner?.character_id === state.selectedCharacterId) renderCharacterDetail(owner);
    if (state.editingProfileId === profileId) {
      if (updateModeControls) updateProfileModeControls();
      else renderVoiceReady(profile, owner);
    }
    if (refreshCandidates && state.candidateProfileId === profileId) renderDesignCandidates(state.candidateItems);
    updateGenerateEnabled();
    return profile;
  }

  function currentStudioCharacter() {
    return state.characters.find((entry) => entry.character_id === $("#dubStudioCharacter").value)
      || selectedCharacter() || null;
  }
  function applyStudioCharacterColor(character = currentStudioCharacter()) {
    const select = $("#dubStudioCharacter");
    select.style.color = /^#[0-9a-f]{6}$/i.test(String(character?.color || "")) ? character.color : "";
  }

  function dubbingBlockChain(plan = state.blockPlan) {
    const sessions = plan?.sessions || [];
    const byId = new Map(sessions.map((item) => [item.session_id, item]));
    let current = byId.get(state.selectedSessionId);
    if (!current) return [];
    const ancestors = [], seen = new Set();
    while (current && !seen.has(current.session_id)) {
      ancestors.push(current); seen.add(current.session_id);
      current = byId.get(current.config_json?.previous_session_id);
    }
    const chain = ancestors.reverse();
    current = chain.at(-1);
    while (current && chain.length < 5000) {
      const children = sessions.filter((item) => item.config_json?.previous_session_id === current.session_id
        && !seen.has(item.session_id)).sort((a, b) =>
        String(b.created_at || "").localeCompare(String(a.created_at || ""))
        || Number(b.config_json?.block_index || 0) - Number(a.config_json?.block_index || 0));
      current = children[0];
      if (!current) break;
      chain.push(current); seen.add(current.session_id);
    }
    return chain;
  }

  async function refreshDubbingBlockPlan(force = false) {
    const sessionId = state.selectedSessionId;
    const characterId = currentStudioCharacter()?.character_id || "";
    if (!sessionId) {
      state.blockPlan = null; state.blockPlanSessionId = ""; state.blockPlanCharacterId = "";
      state.blockPlanRootId = ""; state.videoBatchId = ""; state.videoBatchRootId = "";
      renderDubbingBlockPlan(); return null;
    }
    if (!force && state.blockPlan && state.blockPlanSessionId === sessionId
        && state.blockPlanCharacterId === characterId) return state.blockPlan;
    const params = new URLSearchParams(); if (characterId) params.set("character_id", characterId);
    const key = `${sessionId}|${characterId}`;
    try {
      const plan = await requestCapture(`/api/sessions/${encodeURIComponent(sessionId)}/video-plan?${params}`);
      if (state.selectedSessionId !== sessionId || `${state.selectedSessionId}|${currentStudioCharacter()?.character_id || ""}` !== key) return null;
      state.blockPlan = plan; state.blockPlanSessionId = sessionId; state.blockPlanCharacterId = characterId;
      const chain = dubbingBlockChain(plan), rootId = chain[0]?.session_id || "";
      if (state.blockPlanRootId && rootId && state.blockPlanRootId !== rootId) {
        state.videoBatchId = ""; state.videoBatchRootId = "";
      }
      state.blockPlanRootId = rootId;
      if (state.videoBatchRootId && rootId && state.videoBatchRootId !== rootId) {
        state.videoBatchId = ""; state.videoBatchRootId = "";
      }
      renderDubbingBlockPlan(); return plan;
    } catch (error) {
      console.warn("Progresso de dublagem por bloco:", error);
      if (state.selectedSessionId === sessionId) {
        state.blockPlan = null; state.blockPlanSessionId = sessionId; state.blockPlanCharacterId = characterId;
        renderDubbingBlockPlan(error.message);
      }
      return null;
    }
  }

  function updateDubbingBlockCursor() {
    const cursor = $("#dubOverviewCursor"), video = $("#dubPreviewVideo");
    const total = Number(state.blockPlan?.video_duration) || Number(video?.duration) || 0;
    if (!cursor) return;
    if (!(total > 0) || !Number.isFinite(video?.currentTime)) { cursor.hidden = true; return; }
    cursor.hidden = false;
    const current = state.blockPlan?.sessions?.find((item) => item.session_id === state.selectedSessionId);
    const offset = Number(state.blockPlan?.sectors?.find((item) => item.index === Number(current?.sector_index || 1))?.offset) || 0;
    cursor.style.left = `${Math.max(0, Math.min(100, (offset + video.currentTime) / total * 100))}%`;
  }

  function renderDubbingBlockPlan(error = "") {
    const blocksNode = $("#dubOverviewBlocks"), ruler = $("#dubOverviewRuler");
    if (!blocksNode || !ruler) return;
    const chain = dubbingBlockChain(), player = $("#dubPreviewVideo");
    const character = currentStudioCharacter();
    const total = Number(state.blockPlan?.video_duration) || Number(player?.duration) || 0;
    if (!chain.length) {
      blocksNode.replaceChildren(); ruler.replaceChildren();
      $("#dubOverviewCurrentLabel").textContent = error || "Selecione uma sessão com blocos de captura";
      $("#dubOverviewProgressLabel").textContent = "—";
      updateDubbingBlockCursor(); return;
    }
    const root = chain[0], rootStart = Number(root.source_start) || 0;
    const sectors = state.blockPlan?.sectors?.length ? state.blockPlan.sectors
      : [{ index: 1, offset: 0, duration: total, block_duration_seconds: 0 }];
    const plan = []; let nextIndex = 1;
    for (const sector of sectors) {
      const offset = Number(sector.offset) || 0, endOfSector = offset + Number(sector.duration || 0);
      const captured = chain.filter((item) => Number(item.sector_index || item.config_json?.sector_index || 1) === Number(sector.index));
      let cursor = offset + (Number(sector.index) === 1 ? rootStart : 0);
      for (const session of captured) {
        const config = session.config_json || {}, start = offset + (Number(session.source_start) || 0);
        const duration = Number(config.duration || session.requested_duration) || 0;
        const end = Math.min(endOfSector || Infinity, start + duration);
        if (duration > 0 && end > start) {
          const index = Math.max(1, Number(config.block_index || nextIndex));
          plan.push({ index, start, end, session, sector }); cursor = Math.max(cursor, end);
          nextIndex = Math.max(nextIndex, index + 1);
        }
      }
      const step = Number(sector.block_duration_seconds)
        || Number([...captured].reverse().map((item) => item.config_json?.block_duration_requested).find((value) => Number(value) > 0))
        || Number(root.config_json?.duration || root.requested_duration) || 1200;
      for (let count = 0; endOfSector > 0 && cursor < endOfSector - .01 && count < 5000; count++) {
        const end = Math.min(endOfSector, cursor + step);
        plan.push({ index: nextIndex++, start: cursor, end, session: null, sector }); cursor = end;
      }
    }
    const actualTotal = total || Math.max(plan.at(-1)?.end || 0, 1);
    ruler.replaceChildren(); blocksNode.replaceChildren();
    for (let tickIndex = 0; tickIndex <= 4; tickIndex++) {
      const tick = make("span", "", fmtClock(actualTotal * tickIndex / 4)); ruler.append(tick);
    }
    let eligible = 0, finished = 0, eligibleBlocks = 0, completedBlocks = 0;
    for (const item of plan) {
      const stats = item.session?.dubbing_progress || {}, count = Number(stats.eligible) || 0, done = Number(stats.finished) || 0;
      eligible += count; finished += done;
      if (item.session && count > 0) {
        eligibleBlocks++;
        if (done >= count) completedBlocks++;
      }
      const block = document.createElement("button"), fill = document.createElement("span");
      const active = Boolean((Number(stats.queued) || 0) + (Number(stats.generating) || 0));
      const errored = (Number(stats.errors) || 0) > 0;
      const complete = count > 0 && done >= count;
      block.type = "button";
      block.className = `overview-block ${complete ? "complete" : count === 0 ? "pending no-speech" : done > 0 || active ? "active partial" : "pending"}${errored ? " error" : ""}${item.session?.session_id === state.selectedSessionId ? " selected" : ""}`;
      block.style.left = `${Math.max(0, item.start / actualTotal * 100)}%`;
      block.style.width = `${Math.max(.12, (item.end - item.start) / actualTotal * 100)}%`;
      fill.className = "overview-block-fill";
      fill.style.width = `${count ? Math.max(0, Math.min(100, done / count * 100)) : 0}%`;
      block.append(fill);
      const label = complete ? "Dublagem concluída" : count === 0 ? (item.session ? "Sem falas elegíveis" : "Aguardando captura")
        : active ? "Na fila de dublagem" : errored ? "Com erros" : done ? "Parcialmente dublado" : "Aguardando dublagem";
      const offset = Number(item.sector?.offset) || 0;
      block.title = `Vídeo ${item.sector?.index || 1} · Bloco ${item.index} · ${fmtClock(item.start - offset)}–${fmtClock(item.end - offset)} · ${label} · ${done}/${count} falas${stats.errors ? ` · ${stats.errors} erros` : ""}`;
      block.setAttribute("aria-label", block.title);
      block.disabled = !item.session;
      if (item.session) block.addEventListener("click", () => void selectDubbingBlock(item.session.session_id));
      blocksNode.append(block);
    }
    const percent = eligible ? finished * 100 / eligible : 0;
    const progressLabel = $("#dubOverviewProgressLabel");
    progressLabel.textContent = eligible
      ? `${percent.toFixed(1)}% · ${completedBlocks}/${eligibleBlocks} blocos · ${finished}/${eligible} falas`
      : "0% · sem falas elegíveis";
    progressLabel.title = "O progresso conta falas aprovadas ou corrigidas do personagem selecionado; um bloco fica concluído quando todas têm take. Blocos cinza aguardam captura ou não têm falas elegíveis.";
    const current = plan.find((item) => item.session?.session_id === state.selectedSessionId);
    const charLabel = character ? `Personagem: ${character.display_name || character.name}` : "Todos os personagens";
    $("#dubOverviewCurrentLabel").textContent = current
      ? `Vídeo ${current.sector?.index || 1} · Bloco ${current.index}/${plan.length} · ${fmtClock(current.start - Number(current.sector?.offset || 0))}–${fmtClock(current.end - Number(current.sector?.offset || 0))} · ${charLabel}`
      : `${charLabel} · ${chain.length} bloco(s) capturado(s); os próximos blocos cinza aguardam captura`;
    updateDubbingBlockCursor();
  }

  async function selectDubbingBlock(sessionId) {
    if (!sessionId || sessionId === state.selectedSessionId) return;
    const picker = $("#dubSessionFilter");
    if (![...picker.options].some((item) => item.value === sessionId)) {
      const planSession = state.blockPlan?.sessions?.find((item) => item.session_id === sessionId);
      if (planSession) { picker.add(make("option", "", `Bloco ${planSession.config_json?.block_index || ""} · ${fmtTime(planSession.source_start)} · sessão capturada`)); picker.lastElementChild.value = sessionId; }
    }
    picker.value = sessionId; state.selectedSessionId = sessionId; state.timelineSelectedOccurrenceIds.clear(); resetBatchSelection();
    await loadAll(false);
  }

  async function loadAll(preserveSelection = true) {
    if (state.loading) { state.refreshRequested = true; return; }
    state.loading = true;
    try {
      const previousSessionId = state.selectedSessionId;
      const id = state.selectedSessionId || "";
      const overview = await request(`/overview${id ? `?session_id=${encodeURIComponent(id)}` : ""}`);
      state.overview = overview;
      state.selectedSessionId = overview.session_id || "";
      if (previousSessionId !== state.selectedSessionId) state.timelineSelectedOccurrenceIds.clear();
      state.characters = overview.characters || [];
      populateSessions(overview.sessions || []);
      renderSummary(overview.summary || {});
      renderCharacters();
      renderCharacterFilters();
      renderPresets(overview.presets || []);
      await loadLines(preserveSelection);
      await loadPlaybackLines();
      await refreshDubbingBlockPlan(true);
      await refreshProvider();
      await refreshQueue(false);
      state.loaded = true;
    } catch (error) {
      toast(`Não foi possível abrir a Dublagem: ${error.message}`, true);
      setText("#dubListFooter", error.message);
    } finally {
      state.loading = false;
      if (state.refreshRequested) {
        state.refreshRequested = false;
        if (pendingSharedNavigation?.sessionId) state.selectedSessionId = pendingSharedNavigation.sessionId;
        window.setTimeout(() => void loadAll(preserveSelection), 0);
      } else if (pendingSharedNavigation) {
        const context = pendingSharedNavigation;
        pendingSharedNavigation = null;
        window.setTimeout(() => void applySharedNavigation(context), 0);
      }
    }
  }

  function populateSessions(items) {
    const select = $("#dubSessionFilter");
    const previous = state.selectedSessionId || select.value;
    select.replaceChildren();
    for (const [position, item] of items.entries()) {
      const index = Number(item.block_index) || position + 1;
      const total = Number(item.block_total) || Math.max(index, items.length);
      const blockLabel = `Bloco ${index}/${total}`;
      const dateLabel = item.created_at ? new Date(item.created_at).toLocaleString("pt-BR") : "sessão";
      const option = make("option", "", `${item.state === "complete" ? "✓" : "·"} ${blockLabel} · ${dateLabel} · ${item.line_count} ocorrências`);
      option.value = item.session_id;
      select.append(option);
    }
    if (!items.length) {
      const option = make("option", "", "Sem sessão capturada");
      option.value = "";
      select.append(option);
    }
    select.value = items.some((item) => item.session_id === previous) ? previous : state.selectedSessionId;
    state.selectedSessionId = select.value;
    updateDeleteBlockButton(items);
  }

  function updateDeleteBlockButton(items = state.overview?.sessions || []) {
    const button = $("#dubDeleteBlock"), select = $("#dubSessionFilter");
    if (!button || !select) return;
    const item = items.find((candidate) => candidate.session_id === select.value);
    const deletable = Boolean(item && ["complete", "failed", "stopped"].includes(item.state));
    button.disabled = !deletable;
    if (!item) button.title = "Selecione um bloco capturado para excluí-lo";
    else if (!deletable) button.title = "Este bloco ainda está em andamento e não pode ser excluído";
    else button.title = `Excluir permanentemente o Bloco ${Number(item.block_index) || 1}/${Number(item.block_total) || items.length || 1}`;
  }

  async function deleteSelectedBlock() {
    const sessionId = $("#dubSessionFilter").value;
    const sessions = state.overview?.sessions || [];
    const selectedIndex = sessions.findIndex((item) => item.session_id === sessionId);
    const item = sessions[selectedIndex];
    if (!item || !["complete", "failed", "stopped"].includes(item.state)) return;
    const blockIndex = Number(item.block_index) || selectedIndex + 1;
    const blockTotal = Number(item.block_total) || Math.max(blockIndex, sessions.length);
    const confirmed = window.confirm(
      `Excluir permanentemente o Bloco ${blockIndex}/${blockTotal}?\n\n` +
      `Serão removidos os diálogos, capturas e takes exclusivos deste bloco. Falas compartilhadas com outros blocos serão preservadas. O vídeo de origem será mantido. Esta ação não pode ser desfeita.`,
    );
    if (!confirmed) return;

    const button = $("#dubDeleteBlock");
    button.disabled = true;
    try {
      const params = new URLSearchParams({ project_id: window.DUBSYNC_PROJECT_ID || "" });
      const result = await requestCapture(
        `/api/sessions/${encodeURIComponent(sessionId)}/block?${params}`,
        { method: "DELETE" },
      );
      const remaining = sessions.filter((candidate) => candidate.session_id !== sessionId);
      state.selectedSessionId = (remaining[selectedIndex] || remaining[selectedIndex - 1])?.session_id || "";
      state.blockPlan = null;
      state.blockPlanSessionId = "";
      state.timelineSelectedOccurrenceIds.clear();
      resetBatchSelection();
      await loadAll(false);
      const syncNote = result.game_sync?.status === "queued"
        ? " Sincronização do jogo enfileirada."
        : result.game_sync?.status === "not_ready"
          ? ` O jogo não foi atualizado: ${result.game_sync.message}` : "";
      const sharedNote = result.preserved_shared_occurrences
        ? ` ${result.preserved_shared_occurrences} fala(s) compartilhada(s) preservada(s).` : "";
      toast(`Bloco ${blockIndex}/${blockTotal} excluído.${sharedNote}${syncNote}`);
    } catch (error) {
      toast(`Não foi possível excluir o bloco: ${error.message}`, true);
    } finally {
      updateDeleteBlockButton();
    }
  }

  function renderSummary(summary) {
    setText("#dubStatEligible", summary.eligible ?? 0);
    setText("#dubStatGenerated", summary.generated ?? 0);
    setText("#dubStatReview", summary.review ?? 0);
    setText("#dubStatApproved", summary.approved ?? 0);
    setText("#dubStatQueued", summary.queued ?? 0);
    setText("#dubStatErrors", summary.errors ?? 0);
    setText("#dubBatchGenerated", summary.generated ?? 0);
    setText("#dubBatchErrors", summary.errors ?? 0);
    setText("#dubBatchQueued", summary.queued ?? 0);
    setText("#dubBatchReview", summary.review ?? 0);
  }

  function renderCharacterFilters() {
    const filter = $("#dubCharacterFilter"), current = filter.value;
    const studio = $("#dubStudioCharacter"), selected = state.selectedLine?.character_id || "";
    filter.replaceChildren(make("option", "", "Todos os personagens"));
    studio.replaceChildren(make("option", "", "Sem personagem associado"));
    for (const character of state.characters.filter((item) => item.active)) {
      const a = make("option", "", character.display_name || character.name); a.value = character.character_id; filter.append(a);
      const b = make("option", "", character.display_name || character.name); b.value = character.character_id; studio.append(b);
    }
    filter.value = state.characters.some((item) => item.character_id === current) ? current : "";
    studio.value = selected;
  }

  function renderPresets(items) {
    const select = $("#dubPresetSelect");
    select.replaceChildren(make("option", "", "Aplicar sugestão pela emoção"));
    for (const preset of items) {
      const option = make("option", "", `${preset.name} · ${preset.emotion}`);
      option.value = preset.preset_id;
      option.dataset.instruct = preset.instruct;
      option.dataset.emotion = preset.emotion;
      option.dataset.intensity = preset.intensity;
      select.append(option);
    }
  }

  async function loadLines(preserveSelection = true) {
    if (lineDirty && !(await saveLine())) return;
    await lineAutosaveQueue;
    const params = new URLSearchParams({ session_id: state.selectedSessionId, status: $("#dubStatusFilter").value });
    const character = $("#dubCharacterFilter").value;
    const query = $("#dubLineSearch").value.trim();
    if (character) params.set("character_id", character);
    if (query) params.set("q", query);
    const previousId = preserveSelection ? state.selectedLine?.occurrence_id : "";
    const result = await request(`/lines?${params}`);
    state.lines = result.items || [];
    renderLines();
    const timelineOwnsSelection = $("#dubAutoFollowTimeline")?.checked && !$("#dubPreviewVideo")?.paused;
    if (!timelineOwnsSelection) {
      if (previousId && state.lines.some((item) => item.occurrence_id === previousId)) await selectLine(previousId, false);
      else if (state.lines.length) await selectLine(state.lines[0].occurrence_id, false);
      else clearStudio();
    }
  }

  async function loadPlaybackLines() {
    const sessionId = state.selectedSessionId;
    if (!sessionId) {
      state.playbackLines = [];
      state.playbackLinesSessionId = "";
      return;
    }
    try {
      const result = await request(`/lines?session_id=${encodeURIComponent(sessionId)}&status=all`);
      if (sessionId !== state.selectedSessionId) return;
      state.playbackLines = (result.items || []).slice().sort((a, b) =>
        Number(a.source_start_time) - Number(b.source_start_time)
        || String(a.occurrence_id).localeCompare(String(b.occurrence_id)));
      state.playbackLinesSessionId = sessionId;
      renderDubDialogueTimeline();
    } catch (error) {
      state.playbackLines = [];
      state.playbackLinesSessionId = "";
      console.warn("A lista completa de falas para o acompanhamento da timeline não foi carregada.", error);
      renderDubDialogueTimeline();
    }
  }

  function lineStageStatus(item) {
    const take = item.takes?.[0];
    if (take?.status === "generating") return { key: "generating", label: "◌ Gerando…", take, title: "O OmniVoice está criando o áudio. O arquivo ainda pode não estar disponível para reprodução." };
    if (take?.status === "queued") return { key: "queued", label: "◷ Na fila", take, title: "Esta fala aguarda sua vez na fila de geração." };
    if (take?.status === "error") return { key: "error", label: "⚠ Erro na geração", take, title: take.error_message || "A geração do áudio falhou. Abra o Take Studio para ver os detalhes e tentar novamente." };
    if (take?.status === "generated") return { key: "publishing", label: "◌ Aguardando envio ao jogo", take, title: "O áudio já foi gerado. DubSync ainda vai montar e ativar o pacote de falas no jogo." };
    if (take?.status === "review") return { key: "review", label: "◌ Em revisão", take, title: "O áudio foi gerado e aguarda revisão/aprovação antes de ser publicado no jogo." };
    if (take?.status === "publishing") return { key: "publishing", label: "◌ Sincronizando com o jogo…", take, title: "DubSync está incluindo este áudio no pacote do jogo e tentando ativá-lo. Ao concluir, o estado muda para pronto ou mostra a falha de envio." };
    if (take?.status === "publish_error") return { key: "error", label: "⚠ Falha ao enviar ao jogo", take, title: take.error_message ? `O áudio foi gerado, mas não entrou no jogo. Motivo: ${take.error_message}` : "O áudio foi gerado, mas não foi possível incluí-lo/ativá-lo no jogo. Abra o Take Studio para ver o diagnóstico." };
    if (take?.status === "ready") return { key: "ready", label: "✓ Pronta no jogo", take, title: "Take incluído no pacote ativo do jogo. A confirmação de reprodução depende de o jogo reconhecer a fala e iniciar o áudio." };
    if (take?.status === "approved") return { key: "ready", label: "✓ Take aprovado", take, title: "Take aprovado no DubSync. A aprovação, sozinha, não confirma que ele já foi ativado no pacote do jogo." };
    if (take?.status === "cancelled") return { key: "cancelled", label: "○ Geração cancelada", take, title: take.error_message || "A geração foi cancelada antes de terminar; nenhum áudio novo foi produzido." };
    if (take?.status === "superseded") return { key: "superseded", label: "↻ Substituída", take, title: "Uma versão mais recente substituiu este take." };
    if (take?.status === "rejected") return { key: "rejected", label: "↻ Take rejeitado · pronto para refazer", take, title: "Este take foi rejeitado e não será usado no jogo. Gere outro para substituí-lo." };
    if (item.editorial_status === "accepted") return { key: "ready-to-dub", label: "✓ Captura concluída · pronto para dublar", take, title: "A fala foi confirmada na Captura e pode ser enviada para geração." };
    if (item.editorial_status === "corrected") return { key: "corrected", label: "✎ Corrigido · pronto para dublar", take, title: "O texto foi corrigido e pode ser enviado para geração." };
    if (item.editorial_status === "discarded") return { key: "discarded", label: "○ Descartada", take, title: "Esta fala foi descartada na Captura e não entra na geração normal." };
    return { key: "pending", label: "○ Pendente na Captura", take, title: "Esta fala ainda precisa ser confirmada ou corrigida na Captura antes da geração." };
  }

  function syncLineTableAudioButtons() {
    const isPlaying = Boolean(lineTableAudio && !lineTableAudio.paused && lineTablePlayingTakeId);
    document.querySelectorAll(".dub-line-audio-button").forEach((button) => {
      const active = isPlaying && button.dataset.takeId === lineTablePlayingTakeId;
      button.textContent = active ? "❚❚" : "▶";
      button.setAttribute("aria-label", active ? "Pausar áudio da fala" : "Reproduzir áudio da fala");
      button.title = active ? "Pausar este áudio" : button.dataset.takeVersion ? `Ouvir take T${button.dataset.takeVersion}` : "Ouvir áudio gerado";
    });
  }

  function createLineTableAudioCell(item) {
    const cell = make("td", "dub-line-audio-cell");
    const takes = item.takes || [];
    const take = takes.find((entry) => entry.audio_master_path && !["superseded", "rejected"].includes(entry.status))
      || takes.find((entry) => entry.audio_master_path);
    const button = make("button", "button quiet dub-line-audio-button", "▶");
    button.type = "button";
    button.disabled = !take?.take_id;
    if (take?.take_id) {
      button.dataset.takeId = take.take_id;
      button.dataset.takeVersion = String(take.version || "");
      button.title = `Ouvir take T${take.version || "—"}`;
      button.setAttribute("aria-label", `Reproduzir áudio do take ${take.version || ""}`);
    } else {
      button.title = "Ainda não há áudio gerado disponível para esta fala.";
      button.setAttribute("aria-label", "Áudio ainda não disponível");
    }
    button.addEventListener("click", async (event) => {
      event.preventDefault(); event.stopPropagation();
      if (!take?.take_id) return;
      if (!lineTableAudio) {
        lineTableAudio = new Audio();
        lineTableAudio.preload = "none";
        lineTableAudio.addEventListener("ended", () => { lineTablePlayingTakeId = ""; syncLineTableAudioButtons(); });
        lineTableAudio.addEventListener("error", () => {
          lineTablePlayingTakeId = ""; syncLineTableAudioButtons();
          toast("Não foi possível carregar o áudio deste take. Confira se o arquivo ainda existe no projeto.", true);
        });
      }
      if (lineTablePlayingTakeId === take.take_id && !lineTableAudio.paused) {
        lineTableAudio.pause();
        syncLineTableAudioButtons();
        return;
      }
      if (lineTablePlayingTakeId !== take.take_id) {
        lineTableAudio.pause();
        lineTablePlayingTakeId = take.take_id;
        lineTableAudio.src = takeAudioUrl(take.take_id);
        lineTableAudio.load();
      }
      try {
        await lineTableAudio.play();
        syncLineTableAudioButtons();
      } catch (error) {
        lineTablePlayingTakeId = ""; syncLineTableAudioButtons();
        toast(`Não foi possível reproduzir o áudio: ${error.message}`, true);
      }
    });
    button.addEventListener("keydown", (event) => event.stopPropagation());
    cell.append(button);
    return cell;
  }
  function updateSelectionCount() {
    const selectedCount = state.selectedOccurrenceIds.size;
    const generatingCount = state.selectionBatchOccurrenceIds.size;
    setText("#dubSelectedCount", selectedCount
      ? `${selectedCount} ${selectedCount === 1 ? "fala marcada" : "falas marcadas"}${generatingCount ? ` · ${generatingCount} gerando ou sincronizando` : ""}`
      : "0 falas selecionadas");
    const visible = state.lines.map((item) => item.occurrence_id);
    const selectedVisible = visible.filter((id) => state.selectedOccurrenceIds.has(id)).length;
    const selectAll = $("#dubSelectAll");
    selectAll.checked = visible.length > 0 && selectedVisible === visible.length;
    selectAll.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
    const generateSelected = $("#dubGenerateSelected");
    generateSelected.classList.toggle("batch-ready", selectedCount > 0);
    generateSelected.disabled = selectedCount === 0 || generatingCount > 0;
    generateSelected.setAttribute("aria-busy", String(generatingCount > 0));
    generateSelected.title = generatingCount
      ? `${generatingCount} fala(s) selecionada(s) estão sendo geradas; cada uma será desmarcada ao concluir.`
      : selectedCount ? "Gerar as falas marcadas" : "Marque uma ou mais falas para gerar";
    $("#dubGenerateCharacter").disabled = !currentStudioCharacter()?.character_id;
    $("#dubGenerateVideoCharacter").disabled = !currentStudioCharacter()?.character_id || !state.selectedSessionId;
  }
  function resetBatchSelection() {
    state.selectedOccurrenceIds.clear();
    state.selectionBatchId = "";
    state.selectionBatchOccurrenceIds.clear();
    state.selectionBatchStatuses.clear();
    state.batchGeneratedOccurrenceIds.clear();
  }
  function applySelectionBatchProgress(queue) {
    if (!state.selectionBatchId || queue?.batch?.batch_id !== state.selectionBatchId) return false;
    let changed = false;
    for (const item of queue.batch.occurrences || []) {
      const occurrenceId = item.occurrence_id;
      if (!state.selectionBatchOccurrenceIds.has(occurrenceId)) continue;
      const jobStatus = String(item.status || "");
      const takeStatus = String(item.take_status || "");
      const status = `${jobStatus}:${takeStatus}`;
      if (state.selectionBatchStatuses.get(occurrenceId) !== status) {
        state.selectionBatchStatuses.set(occurrenceId, status);
        changed = true;
      }
      if (["ready", "approved"].includes(takeStatus)) {
        state.selectionBatchOccurrenceIds.delete(occurrenceId);
        state.selectedOccurrenceIds.delete(occurrenceId);
        state.batchGeneratedOccurrenceIds.add(occurrenceId);
      } else if (["error", "cancelled"].includes(jobStatus) || takeStatus === "publish_error") {
        // Keep failed lines selected so they can be retried explicitly.
        state.selectionBatchOccurrenceIds.delete(occurrenceId);
      }
    }
    if (!state.selectionBatchOccurrenceIds.size) {
      state.selectionBatchId = "";
      state.selectionBatchStatuses.clear();
    }
    return changed;
  }
  function colorForLineItem(item) {
    const profileId = String(item.event?.character_profile_id || "");
    let character = profileId ? state.characters.find((entry) => entry.legacy_character_profile_id === profileId) : null;
    if (!character) {
      const name = String(item.character_name || "").trim().toLocaleLowerCase("pt-BR");
      character = state.characters.find((entry) => [entry.display_name, entry.roi_alias, entry.name]
        .some((value) => String(value || "").trim().toLocaleLowerCase("pt-BR") === name));
    }
    return /^#[0-9a-f]{6}$/i.test(String(character?.color || "")) ? character.color : "";
  }
  function updateSelectedLineRow(occurrenceId) {
    const body = $("#dubLinesBody");
    const timelineFollows = $("#dubAutoFollowTimeline")?.checked && !$("#dubPreviewVideo")?.paused;
    const nextRow = lineRowByOccurrence.get(occurrenceId);
    if (body.querySelector(".timeline-followed") || (timelineFollows && !nextRow)) {
      renderLines();
      return;
    }
    body.querySelector(".dub-line-row.selected")?.classList.remove("selected");
    nextRow?.classList.add("selected");
  }
  function updateLineRowFromState(item, changedFields) {
    const row = lineRowByOccurrence.get(item?.occurrence_id);
    const characterChanged = ["character_id", "character_name"]
      .some((field) => changedFields.has(field));
    const textChanged = changedFields.has("dub_text");
    const characterCell = characterChanged ? row?.querySelector(".dub-line-character-cell") : null;
    if (characterCell) {
      characterCell.replaceChildren();
      const color = colorForLineItem(item);
      if (color) {
        characterCell.style.color = color;
        const dot = make("i", "event-character-color-dot");
        dot.style.backgroundColor = color; dot.setAttribute("aria-hidden", "true");
        characterCell.append(dot, document.createTextNode(String(item.character_name || "Sem personagem")));
      } else {
        characterCell.style.removeProperty("color");
        characterCell.textContent = item.character_name || "Sem personagem";
      }
    }
    const textCell = textChanged ? row?.querySelector(".dub-line-text-cell") : null;
    if (textCell) textCell.textContent = item.dub_text || item.source_text || "—";
    const pin = characterChanged || textChanged ? timelinePinByOccurrence.get(item.occurrence_id) : null;
    if (pin) {
      const color = colorForLineItem(item) || "#65a7ff";
      pin.style.setProperty("--character-color", color);
      pin.title = `#${item.event?.sequence ?? "—"} · ${item.character_name || "Sem personagem"} · ${fmtTime(item.source_start_time)}\n${item.dub_text || item.source_text || ""}\nClique para selecionar; duplo clique para ir ao início.`;
      pin.setAttribute("aria-label", `Diálogo ${item.event?.sequence ?? ""}, ${item.character_name || "sem personagem"}, ${fmtTime(item.source_start_time)}`);
    }
  }
  function updateSavedLineInViews(lineId, saved) {
    const fields = ["dub_text", "emotion", "emotion_intensity", "character_id", "character_name",
      "character_verified", "voice_profile_id", "voice_profile_name", "voice_ready"];
    const apply = (item) => {
      if (item.line_id !== lineId) return;
      const changedFields = new Set();
      for (const field of fields) {
        if (!Object.hasOwn(saved, field)) continue;
        if (!Object.is(item[field], saved[field])) changedFields.add(field);
        item[field] = saved[field];
      }
      if (changedFields.size) updateLineRowFromState(item, changedFields);
    };
    state.lines.forEach(apply);
    state.playbackLines.forEach(apply);
    if (state.selectedLine?.line_id === lineId) apply(state.selectedLine);
  }
  function updateLineRowStatus(item) {
    const row = lineRowByOccurrence.get(item?.occurrence_id), cell = row?.querySelector(".dub-line-status-cell");
    if (!cell) return;
    cell.replaceChildren();
    const stage = lineStageStatus(item);
    const badge = make("span", `dub-row-status ${stage.key}`, stage.label);
    badge.title = stage.title || "Passe o mouse sobre este estado para entender o que está acontecendo.";
    cell.append(badge);
    if (stage.take?.version) cell.append(make("small", `dub-take-status ${stage.take.status}`, ` T${stage.take.version}`));
    if (item.conflict_occurrence_ids.length) cell.append(make("i", "dub-conflict-mark", " ⚠"));
  }
  function refreshLineReviewMetric() {
    setText("#dubMetricReview", state.lines.filter((item) => ["generated", "publishing", "review"].includes(item.takes?.[0]?.status)).length);
    updateSelectionCount();
  }
  function renderLines() {
    const body = $("#dubLinesBody"); body.replaceChildren(); lineRowByOccurrence.clear();
    const items = state.lines.slice();
    const timelineFollows = $("#dubAutoFollowTimeline")?.checked && !$("#dubPreviewVideo")?.paused;
    const timelineLine = timelineFollows
      ? state.playbackLines.find((item) => item.occurrence_id === state.selectedLine?.occurrence_id)
      : null;
    const timelineLineOutsideFilters = Boolean(timelineLine && !items.some((item) => item.occurrence_id === timelineLine.occurrence_id));
    if (timelineLineOutsideFilters) items.push(timelineLine);
    items.sort(compareLineItems);
    setText("#dubLineCount", items.length);
    setText("#dubMetricEligible", items.filter((item) => ["accepted", "corrected"].includes(item.editorial_status)).length);
    setText("#dubMetricWithoutTake", items.filter((item) => !item.take_count).length);
    setText("#dubMetricReview", items.filter((item) => ["generated", "publishing", "review"].includes(item.takes?.[0]?.status)).length);
    setText("#dubMetricConflicts", items.filter((item) => item.conflict_occurrence_ids.length).length);
    setText("#dubListFooter", `${items.length} ocorrência(s) exibida(s). Eventos de sessões repetidas permanecem separados para revisão humana.${timelineLineOutsideFilters ? " A fala ativa foi incluída fora dos filtros para acompanhar a timeline." : ""}`);
    if (!items.length) {
      const row = make("tr"); const cell = make("td", "dub-table-empty", "Nenhuma fala corresponde a estes filtros."); cell.colSpan = 9; row.append(cell); body.append(row); updateSelectionCount(); renderDubDialogueTimeline(); return;
    }
    for (const item of items) {
      const isTimelineOnly = timelineLineOutsideFilters && item.occurrence_id === timelineLine.occurrence_id;
      const generated = state.batchGeneratedOccurrenceIds.has(item.occurrence_id)
        || ["ready", "approved"].includes(item.takes?.[0]?.status);
      const row = make("tr", `dub-line-row${state.selectedLine?.occurrence_id === item.occurrence_id ? " selected" : ""}${state.selectedOccurrenceIds.has(item.occurrence_id) ? " batch-selected" : ""}${generated ? " batch-generated" : ""}${isTimelineOnly ? " timeline-followed" : ""}`);
      row.dataset.occurrenceId = item.occurrence_id;
      lineRowByOccurrence.set(item.occurrence_id, row);
      if (isTimelineOnly) row.title = "Fala ativa na timeline; exibida mesmo fora dos filtros atuais.";
      row.tabIndex = 0;
      row.addEventListener("click", () => { setDubTimelineSelection([item.occurrence_id], item.occurrence_id); void selectLine(item.occurrence_id); });
      row.addEventListener("keydown", (event) => { if (event.key === "Enter") { setDubTimelineSelection([item.occurrence_id], item.occurrence_id); void selectLine(item.occurrence_id); } });
      const numberCell = make("td", "dub-line-select-cell");
      const checkbox = make("input", "dub-line-select"); checkbox.type = "checkbox";
      checkbox.disabled = isTimelineOnly;
      if (isTimelineOnly) checkbox.title = "Esta fala está fora dos filtros atuais; altere os filtros para incluí-la em uma seleção de lote.";
      checkbox.checked = state.selectedOccurrenceIds.has(item.occurrence_id);
      checkbox.setAttribute("aria-label", `Selecionar fala ${item.event?.sequence ?? item.occurrence_id}`);
      checkbox.addEventListener("click", (event) => event.stopPropagation());
      checkbox.addEventListener("change", (event) => {
        event.stopPropagation();
        if (checkbox.checked) state.selectedOccurrenceIds.add(item.occurrence_id);
        else state.selectedOccurrenceIds.delete(item.occurrence_id);
        if (checkbox.checked) state.batchGeneratedOccurrenceIds.delete(item.occurrence_id);
        row.classList.toggle("batch-selected", checkbox.checked);
        row.classList.toggle("batch-generated", state.batchGeneratedOccurrenceIds.has(item.occurrence_id)
          || ["ready", "approved"].includes(item.takes?.[0]?.status));
        updateSelectionCount();
      });
      const checkboxTarget = make("label", "dub-line-select-target");
      checkboxTarget.title = isTimelineOnly ? checkbox.title : "Marque para incluir esta fala no lote; gerar só começa ao clicar em Gerar selecionadas.";
      checkboxTarget.addEventListener("click", (event) => { if (!checkbox.disabled) event.stopPropagation(); });
      checkboxTarget.append(checkbox, document.createTextNode(String(item.event?.sequence ?? "—")));
      numberCell.append(checkboxTarget);
      row.append(numberCell);
      const cells = [
        item.occurrence_id.slice(-8).toUpperCase(), fmtSourceTime(item.source_start_time),
        fmtSourceTime(item.source_end_time),
        item.character_name, item.dub_text || item.source_text || "—", fmtDuration(item.target_duration_ms),
      ];
      const lineColor = colorForLineItem(item);
      for (const [index, value] of cells.entries()) {
        const cell = make("td", `${index === 1 || index === 2 ? "dub-time-cell " : ""}${index === 3 ? "dub-line-character-cell" : ""}${index === 4 ? "dub-line-text-cell" : ""}`.trim());
        if (index === 3 && lineColor) {
          cell.style.color = lineColor;
          const dot = make("i", "event-character-color-dot"); dot.style.backgroundColor = lineColor; dot.setAttribute("aria-hidden", "true");
          cell.append(dot, document.createTextNode(String(value)));
        } else cell.textContent = String(value);
        row.append(cell);
      }
      row.append(createLineTableAudioCell(item));
      const stateCell = make("td", "dub-line-status-cell");
      const stage = lineStageStatus(item);
      const pill = make("span", `dub-row-status ${stage.key}`, stage.label);
      pill.title = stage.title || "Passe o mouse sobre este estado para entender o que está acontecendo.";
      stateCell.append(pill);
      const take = stage.take;
      if (take?.version) stateCell.append(make("small", `dub-take-status ${take.status}`, ` T${take.version}`));
      if (item.conflict_occurrence_ids.length) stateCell.append(make("i", "dub-conflict-mark", " ⚠"));
      row.append(stateCell); body.append(row);
    }
    updateSelectionCount(); syncLineTableAudioButtons(); renderDubDialogueTimeline();
  }

  function initializeLineColumnResizers() {
    const table = $(".dub-lines-table"), scroll = $(".dub-table-scroll");
    if (!table || !scroll) return;
    const headers = [...table.tHead.rows[0].cells];
    const defaults = [72, 96, 112, 112, 150, 340, 78, 54, 175];
    const minimums = [66, 68, 105, 105, 80, 150, 55, 46, 90];
    const maximums = [120, 300, 220, 220, 360, 1000, 180, 76, 350];
    const storageKey = "dubsync.dub.lineColumns.v1";
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(storageKey) || "[]"); } catch { /* use the readable defaults */ }
    if (Array.isArray(saved) && saved.length === 7) saved.splice(3, 0, 112);
    if (Array.isArray(saved) && saved.length === 8) saved.splice(7, 0, defaults[7]);
    const widths = defaults.map((value, index) => {
      const candidate = Number(saved[index]);
      return Number.isFinite(candidate) ? Math.max(minimums[index], Math.min(maximums[index], candidate)) : value;
    });
    let columns = table.querySelector(":scope > colgroup");
    if (!columns) {
      columns = document.createElement("colgroup");
      headers.forEach(() => columns.append(document.createElement("col")));
      table.insertBefore(columns, table.tHead);
    }
    const columnNodes = [...columns.children];
    function applyWidths() {
      widths.forEach((width, index) => { if (columnNodes[index]) columnNodes[index].style.width = `${width}px`; });
      table.style.width = `${Math.max(scroll.clientWidth, widths.reduce((sum, width) => sum + width, 0))}px`;
    }
    function persistWidths() {
      try { localStorage.setItem(storageKey, JSON.stringify(widths)); } catch { /* layout preference is optional */ }
    }
    headers.forEach((header, index) => {
      const grip = document.createElement("button");
      grip.type = "button";
      grip.className = "dub-column-resizer";
      grip.setAttribute("aria-label", `Redimensionar coluna ${header.querySelector(".dub-sort-label")?.textContent.trim() || header.textContent.trim() || index + 1}`);
      grip.setAttribute("aria-valuemin", String(minimums[index]));
      grip.setAttribute("aria-valuemax", String(maximums[index]));
      grip.setAttribute("aria-valuenow", String(widths[index]));
      grip.title = "Arraste para ajustar a largura desta coluna. A largura fica salva.";
      let drag = null;
      grip.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault(); event.stopPropagation();
        drag = { x: event.clientX, width: widths[index] };
        grip.setPointerCapture(event.pointerId);
        grip.classList.add("dragging"); document.body.classList.add("dub-column-resizing");
      });
      grip.addEventListener("pointermove", (event) => {
        if (!drag) return;
        widths[index] = Math.max(minimums[index], Math.min(maximums[index], drag.width + event.clientX - drag.x));
        grip.setAttribute("aria-valuenow", String(Math.round(widths[index])));
        applyWidths();
      });
      const stopResize = () => {
        if (!drag) return;
        drag = null; grip.classList.remove("dragging"); document.body.classList.remove("dub-column-resizing");
        persistWidths();
      };
      grip.addEventListener("pointerup", stopResize);
      grip.addEventListener("pointercancel", stopResize);
      grip.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        widths[index] = Math.max(minimums[index], Math.min(maximums[index], widths[index] + (event.key === "ArrowRight" ? 12 : -12)));
        grip.setAttribute("aria-valuenow", String(Math.round(widths[index])));
        applyWidths(); persistWidths();
      });
      header.append(grip);
    });
    applyWidths();
    if (typeof ResizeObserver === "function") new ResizeObserver(applyWidths).observe(scroll);
  }

  const lineSortValue = {
    sequence: (item) => Number(item.event?.sequence ?? 0),
    occurrence: (item) => item.occurrence_id || "",
    start: (item) => Number(item.source_start_time),
    end: (item) => Number(item.source_end_time),
    character: (item) => item.character_name || "",
    text: (item) => item.dub_text || item.source_text || "",
    target: (item) => Number(item.target_duration_ms),
    status: (item) => lineStageStatus(item).label || "",
  };

  function compareLineItems(a, b) {
    const getter = lineSortValue[state.lineSort.key] || lineSortValue.start;
    const left = getter(a), right = getter(b);
    let result = typeof left === "number" && typeof right === "number"
      ? (Number.isFinite(left) && Number.isFinite(right) ? left - right : Number.isFinite(left) ? -1 : Number.isFinite(right) ? 1 : 0)
      : String(left).localeCompare(String(right), "pt-BR", { numeric: true, sensitivity: "base" });
    if (result) return result * (state.lineSort.direction === "desc" ? -1 : 1);
    return (Number(a.source_start_time) - Number(b.source_start_time))
      || String(a.occurrence_id || "").localeCompare(String(b.occurrence_id || ""), "pt-BR", { numeric: true });
  }

  function updateLineSortIndicators() {
    document.querySelectorAll(".dub-lines-table th[data-sort-key]").forEach((header) => {
      const button = header.querySelector(".dub-sort-button");
      const indicator = header.querySelector(".dub-sort-indicator");
      if (!button || !indicator) return;
      const active = header.dataset.sortKey === state.lineSort.key;
      const ascending = state.lineSort.direction === "asc";
      indicator.textContent = active ? (ascending ? "↑" : "↓") : "↕";
      header.setAttribute("aria-sort", active ? (ascending ? "ascending" : "descending") : "none");
      const label = header.querySelector(".dub-sort-label")?.textContent.trim() || "coluna";
      button.title = active
        ? `Ordenação por ${label}: ${ascending ? "crescente" : "decrescente"}. Clique para inverter.`
        : `Ordenar por ${label} em ordem crescente.`;
      button.setAttribute("aria-label", button.title);
    });
  }

  function initializeLineSortControls() {
    document.querySelectorAll(".dub-lines-table th[data-sort-key] .dub-sort-button").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.closest("th")?.dataset.sortKey;
        if (!key || !lineSortValue[key]) return;
        if (state.lineSort.key === key) state.lineSort.direction = state.lineSort.direction === "asc" ? "desc" : "asc";
        else state.lineSort = { key, direction: "asc" };
        updateLineSortIndicators();
        renderLines();
      });
    });
    updateLineSortIndicators();
  }

  async function selectLine(occurrenceId, rerender = true, playbackToken = null) {
    if (playbackToken == null) {
      state.timelinePlaybackToken += 1;
      state.autoPlaybackPendingId = "";
      state.autoPlaybackOccurrenceId = "";
      state.autoPlaybackTakeId = "";
      if ($("#dubAutoFollowTimeline")?.checked) $("#dubPreviewAudio")?.pause();
    }
    if (lineDirty) {
      const saved = await saveLine();
      if (!saved) return false;
    } else await lineAutosaveQueue;
    if (playbackToken != null && playbackToken !== state.timelinePlaybackToken) return false;
    const item = state.lines.find((line) => line.occurrence_id === occurrenceId)
      || state.playbackLines.find((line) => line.occurrence_id === occurrenceId);
    if (!item) return false;
    try {
      // The full-session list already contains the fields needed by Take Studio.
      // Reuse it for playhead-driven changes so audio can start at the dialogue boundary.
      const detail = playbackToken != null ? item
        : await request(`/lines/${encodeURIComponent(item.line_id)}?occurrence_id=${encodeURIComponent(occurrenceId)}`);
      if (playbackToken != null && playbackToken !== state.timelinePlaybackToken) return false;
      state.selectedLine = detail;
      state.selectedTakeId = "";
      if (rerender) updateSelectedLineRow(occurrenceId);
      fillStudio(detail);
      await renderTakes(detail.takes || []);
      setupVideo(detail);
      return true;
    } catch (error) { toast(error.message, true); return false; }
  }

  function clearStudio() {
    if (lineDirty) void saveLine();
    clearTimeout(lineAutosaveTimer); lineAutosaveTimer = 0;
    state.selectedLine = null; state.selectedTakeId = "";
    $("#dubSourceText").value = ""; $("#dubText").value = ""; setText("#dubSourceEvidence", "");
    setText("#dubSelectedOccurrence", "Selecione uma fala");
    lineDirty = false; lineEditRevision += 1;
    setLineSaveStatus("Selecione uma fala", "saved");
    $("#dubGenerateTake").disabled = true;
    resetSingleTakeSpeed(null);
    $("#dubTakeList").replaceChildren(make("div", "dub-empty-mini", "Selecione uma ocorrência para revisar os takes."));
  }

  function resetSingleTakeSpeed(item) {
    const input = $("#dubSingleTakeSpeed"), suggest = $("#dubSuggestTakeSpeed"), hint = $("#dubSingleTakeSpeedHint");
    if (!input || !suggest || !hint) return;
    input.value = "";
    input.disabled = !item;
    const hasMeasuredTake = (item?.takes || []).some((take) => Number(take.actual_duration_ms) > 0);
    suggest.disabled = !item || !hasMeasuredTake;
    hint.textContent = "Ajuste vale apenas para o próximo take desta fala; o perfil e as demais falas não mudam. Ao usar a velocidade pontual, a duração-alvo do perfil será ignorada nesta geração.";
  }

  function suggestSingleTakeSpeed() {
    const current = state.selectedLine;
    const item = current && (state.lines.find((line) => line.occurrence_id === current.occurrence_id) || current);
    const hint = $("#dubSingleTakeSpeedHint"), input = $("#dubSingleTakeSpeed");
    const take = (item?.takes || []).filter((entry) => Number(entry.actual_duration_ms) > 0)
      .sort((a, b) => Number(b.version || 0) - Number(a.version || 0))[0];
    if (!item || !take) {
      hint.textContent = "Gere ao menos um take para calcular uma sugestão; também pode informar a velocidade manualmente.";
      return;
    }
    const start = Number(item.source_start_time);
    const sourceEnd = Number(item.source_end_time);
    const fallbackEnd = Number(item.target_duration_ms) > 0
      ? start + Number(item.target_duration_ms) / 1000 : NaN;
    const capturedEnd = Number.isFinite(sourceEnd) && sourceEnd > start ? sourceEnd : fallbackEnd;
    const sessionItems = [...state.playbackLines, ...state.lines];
    const nextStart = sessionItems.reduce((next, entry) => {
      if (entry.occurrence_id === item.occurrence_id || entry.session_id !== item.session_id) return next;
      const time = Number(entry.source_start_time);
      return Number.isFinite(time) && time > start + 0.001 ? Math.min(next, time) : next;
    }, Infinity);
    const windowEnd = Math.min(capturedEnd, nextStart);
    const windowMs = Math.round((windowEnd - start) * 1000);
    const actualMs = Number(take.actual_duration_ms);
    if (!Number.isFinite(windowMs) || windowMs < 100 || !Number.isFinite(actualMs) || actualMs <= 0) {
      hint.textContent = "Não consegui estimar a janela desta fala. Defina a velocidade manualmente entre 0,5× e 2×.";
      return;
    }
    if (actualMs <= windowMs * 0.97) {
      hint.textContent = `O take atual (${(actualMs / 1000).toFixed(2)} s) já cabe na janela estimada (${(windowMs / 1000).toFixed(2)} s). Se ainda houver corte, tente um pequeno ajuste manual.`;
      return;
    }
    const originalSpeed = Number(take.speed_used) || Number($("#dubSpeed").value) || 1;
    const requested = originalSpeed * actualMs / (windowMs * 0.95);
    const suggested = Math.min(2, Math.max(0.5, Math.ceil(requested * 20) / 20));
    input.value = suggested.toFixed(2);
    hint.textContent = requested > 2
      ? `A estimativa pede ${requested.toFixed(2)}×, acima do limite. Coloquei o máximo de 2×; talvez ainda corte.`
      : `Sugestão ${suggested.toFixed(2)}× para a janela de ${(windowMs / 1000).toFixed(2)} s. É uma estimativa; ajuste ouvindo o novo take.`;
  }

  function fillStudio(item) {
    clearTimeout(lineAutosaveTimer); lineAutosaveTimer = 0;
    lineDirty = false; lineEditRevision += 1;
    setLineSaveStatus("Salvo automaticamente", "saved");
    $("#dubSourceText").value = item.source_text || "";
    $("#dubText").value = item.dub_text || item.source_text || "";
    resetSingleTakeSpeed(item);
    $("#dubStudioCharacter").value = item.character_id || "";
    applyStudioCharacterColor(state.characters.find((entry) => entry.character_id === item.character_id));
    $("#dubEmotion").value = item.emotion || "normal";
    $("#dubIntensity").value = item.emotion_intensity ?? 0.7;
    setText("#dubIntensityValue", `${Math.round(Number($("#dubIntensity").value) * 100)}%`);
    setText("#dubSelectedOccurrence", `#${item.event?.sequence ?? "—"} · ${fmtTime(item.source_start_time)}`);
    const editorial = $("#dubSelectedEditorial"); editorial.textContent = labelStatus(item.editorial_status); editorial.className = `dub-status-pill ${item.editorial_status}`;
    const event = item.event || {};
    setText("#dubSourceEvidence", `OCR: ${Math.round((Number(event.confidence) || 0) * 100)}% · ${event.segment_id === "manual" ? "anotação manual" : "captura Fase 1"} · ${fmtTime(item.source_start_time)}–${fmtTime(item.source_end_time)} · ${item.session_id.slice(0, 8)}`);
    const conflicts = item.conflict_occurrence_ids || [];
    const notice = $("#dubConflictNotice");
    notice.textContent = conflicts.length ? `Possível repetição temporal em ${conflicts.length} outra(s) ocorrência(s). Revise o vínculo; nada foi mesclado.` : "";
    notice.classList.toggle("hidden", !conflicts.length);
    const character = state.characters.find((entry) => entry.character_id === item.character_id);
    const requestedProfileId = item.voice_profile_id || "";
    const profileSelect = $("#dubVoiceSelect");
    const optionExists = [...profileSelect.options].some((option) => option.value === requestedProfileId);
    const chosenProfile = findProfile(requestedProfileId);
    const editorOwnerId = chosenProfile?.character_id || character?.character_id || "";
    const editorContextChanged = state.editingProfileId !== (chosenProfile?.voice_profile_id || "")
      || $("#dubVoiceCharacterId").value !== editorOwnerId;
    if (profileDraftDirty && editorContextChanged) {
      renderProfileOptions(state.editingProfileId);
    } else {
      if (profileSelect.value !== requestedProfileId || !optionExists) renderProfileOptions(requestedProfileId);
      if (editorContextChanged) renderProfileEditor(chosenProfile, character);
    }
    updateGenerateEnabled();
  }

  function renderVoiceReady(profile, character) {
    const dot = $("#dubVoiceReadyDot");
    const mode = $("#dubLocalProfileMode")?.value || profile?.voice_mode || "clone";
    const designSaved = Boolean(profile?.voice_mode === "design" && profile.default_instruct === voiceDesignInstruction());
    const designPreviewable = Boolean(mode === "design" && designSaved && profile?.active
      && profile?.use_authorized && $("#dubProfileAuthorized").checked && character?.name_verified);
    const ready = Boolean(profile?.ready && profile.voice_mode === mode
      && (mode !== "design" || designSaved)
      && $("#dubProfileAuthorized").checked && character?.name_verified);
    dot.className = `state-dot ${ready ? "ready" : "failed"}`;
    const message = !character ? "Selecione um personagem para criar ou usar um perfil."
      : !character.name_verified ? "Confirme o nome do personagem primeiro."
        : !profile ? mode === "design" ? "Defina atributos, salve o perfil e confirme a autorização." : hasRoiAudio(character) ? "O áudio da ROI será preparado ao salvar o perfil; confirme a autorização." : "Anexe um áudio autorizado e confirme a autorização ao salvar."
          : profile.active === false ? "Este perfil está inativo."
            : ready && profile.provider_voice_key === "dubsync:design-candidate" ? `${profile.name} · voz oficial escolhida; perfil Gerar voz pronto.`
              : ready ? `${profile.name} · áudio autorizado e aprovado.`
                : mode === "design" && profile.voice_mode !== "design" ? "Salve o perfil no modo Gerar voz antes de criar candidatos."
                  : mode === "design" && !$("#dubProfileAuthorized").checked ? "Confirme a autorização antes de gerar candidatos."
                    : mode === "design" && designPreviewable ? "Perfil salvo. Gere prévias e escolha uma como voz oficial para habilitar os takes."
                      : mode === "design" ? voiceDesignInstruction() ? "Salve os atributos atuais para gerar candidatos." : "Escolha ao menos um atributo ou descreva a voz."
                        : profile.reference ? "Revise e aprove a qualidade do áudio para habilitar a voz." : hasRoiAudio(character) ? "O áudio anexado será preparado automaticamente; confirme a autorização." : "Perfil salvo; anexe um WAV/MP3 e confirme a autorização.";
    setText("#dubVoiceReadyText", message);
    const quality = $("#dubProfileReferenceStatus");
    if (!quality) return;
    quality.replaceChildren();
    const reference = profile?.reference;
    quality.append(make("span", "", reference
      ? `${fmtDuration(reference.duration_ms)} · ${reference.sample_rate} Hz · ${reference.channels} canal(is) · ${labelStatus(reference.quality_status)}${reference.quality_status === "needs_review" ? " · prefira 3–10 s" : ""}`
      : "Ainda não há áudio de referência neste perfil."));
    if (mode === "clone" && hasRoiAudio(character) && profile?.provider_voice_key !== "dubsync:design-candidate") {
      quality.append(make("small", "dub-roi-auto-note", `Fonte automática: ${roiAudioName(character)}${profileUsesCurrentRoiAudio(profile, character) ? " · sincronizada" : " · será preparada ao salvar"}`));
    }
    if (reference && reference.quality_status !== "ready") {
      const approve = make("button", "button quiet", "Confirmar qualidade do áudio");
      approve.type = "button";
      approve.addEventListener("click", async () => {
        try {
          const approved = await request(`/voice-references/${encodeURIComponent(reference.reference_id)}/approve`, { method: "POST", body: JSON.stringify({ approved: true }) });
          const latest = storeVoiceProfile({ ...profile, reference: approved,
            ready: Boolean(profile.active && profile.use_authorized && profile.voice_mode !== "design" && approved.normalized_path) }, { refreshViews: false });
          if (latest) refreshVoiceProfileStatus(latest.voice_profile_id);
          toast("Áudio aprovado. O perfil está pronto para teste e geração.");
        } catch (error) { toast(error.message, true); }
      });
      quality.append(approve);
    }
    const legacyQuality = $("#dubReferenceQuality");
    if (legacyQuality) legacyQuality.textContent = profile?.provider_voice_key === "dubsync:design-candidate"
      ? `Voz oficial escolhida · ${fmtDuration(reference?.duration_ms || 0)} · identidade vocal salva localmente.`
      : reference ? `${fmtDuration(reference.duration_ms)} · ${labelStatus(reference.quality_status)}` : "Use um arquivo WAV ou MP3 autorizado.";
    $("#dubRemoveReference").disabled = !reference;
    $("#dubTestProfile").disabled = !profile?.ready && !designPreviewable;
  }

  function updateGenerateEnabled() {
    const item = state.selectedLine, profileId = $("#dubVoiceSelect").value;
    const character = state.characters.find((entry) => entry.character_id === $("#dubStudioCharacter").value);
    const profile = findProfile(profileId);
    const editorialReady = item && ["accepted", "corrected"].includes(item.editorial_status);
    const activeTake = item?.takes?.[0]?.status;
    const busy = ["queued", "generating", "generated", "publishing", "review"].includes(activeTake);
    const button = $("#dubGenerateTake");
    button.disabled = Boolean(busy || !(item && profile?.ready && character?.name_verified && (editorialReady || $("#dubManualOverride").checked)));
    button.innerHTML = busy ? `<span class="dub-inline-spinner" aria-hidden="true"></span> ${activeTake === "queued" ? "Na fila" : ["generated", "publishing", "review"].includes(activeTake) ? "Sincronizando…" : "Gerando…"}` : "✦ Gerar take";
  }

  function ensureDubbingVideoSession(sessionId) {
    const video = $("#dubPreviewVideo"), url = `/api/sessions/${encodeURIComponent(sessionId)}/video?project_id=${encodeURIComponent(window.DUBSYNC_PROJECT_ID||"")}`;
    if (video.dataset.sessionId !== sessionId) {
      video.pause(); video.src = url; video.dataset.sessionId = sessionId; video.load();
    }
    $("#dubVideoPlaceholder").classList.add("hidden"); video.classList.remove("hidden");
    return video;
  }

  function setupVideo(item) {
    const video = ensureDubbingVideoSession(item.session_id);
    video.dataset.targetTime = item.source_start_time;
    setText("#dubTimeStart", fmtTime(item.source_start_time));
    setText("#dubTimeEnd", fmtTime(item.source_end_time));
    setText("#dubDurationDelta", `Alvo ${fmtDuration(item.target_duration_ms)}`);
    const duration = Number(item.target_duration_ms) || 0;
    $("#dubTargetWindow").style.width = `${Math.min(100, Math.max(5, duration / Math.max(1, Number(item.source_end_time - item.source_start_time) * 1000) * 100))}%`;
    renderDubDialogueTimeline();
  }

  function dubTimelineItems() {
    const source = state.playbackLinesSessionId === state.selectedSessionId ? state.playbackLines : state.lines;
    return source.filter((item) => item.session_id === state.selectedSessionId)
      .slice().sort((a, b) => Number(a.source_start_time) - Number(b.source_start_time));
  }

  function getDubTimelineBounds() {
    const first = dubTimelineItems()[0], session = first?.session || state.overview?.sessions?.find((item) => item.session_id === state.selectedSessionId) || {};
    const start = Math.max(0, Number(session.source_start) || 0);
    const duration = Number(session.duration || session.requested_duration) || 0;
    const videoDuration = Number($("#dubPreviewVideo")?.duration);
    let end = duration > 0 ? start + duration : Number.isFinite(videoDuration) && videoDuration > start ? videoDuration : 0;
    const latest = Math.max(start + 1, ...dubTimelineItems().map((item) => Number(item.source_end_time || item.source_start_time) || 0));
    if (!(end > start)) end = latest;
    if (Number.isFinite(videoDuration) && videoDuration > start) end = Math.min(end, videoDuration);
    if (!(end > start)) end = start + 1;
    return { sessionId: state.selectedSessionId, start, end, span: end - start };
  }

  function clampDubTimelineView(start, span, bounds) {
    const nextSpan = Math.max(.25, Math.min(bounds.span, Number(span) || bounds.span));
    const nextStart = Math.max(bounds.start, Math.min(bounds.end - nextSpan, Number(start) || bounds.start));
    return { start: nextStart, span: nextSpan, end: nextStart + nextSpan };
  }

  function saveDubTimelineView(view) {
    if (!state.selectedSessionId) return;
    dubTimelineViews[state.selectedSessionId] = { start: view.start, span: view.span };
    try { localStorage.setItem(DUB_TIMELINE_VIEW_KEY, JSON.stringify(dubTimelineViews)); } catch { /* view persistence is optional */ }
  }

  function prepareNavigation(context) {
    if (context?.sessionId) {
      const sessionId = String(context.sessionId);
      if (state.selectedSessionId && state.selectedSessionId !== sessionId) {
        state.timelineSelectedOccurrenceIds.clear(); resetBatchSelection();
      }
      pendingSharedNavigation = { ...context, sessionId };
    }
  }

  function getNavigationState() {
    const sessionId = state.selectedSessionId || $("#dubSessionFilter")?.value || "";
    if (!sessionId) return null;
    const video = $("#dubPreviewVideo"), session = state.overview?.sessions?.find((item) => item.session_id === sessionId);
    const bounds = getDubTimelineBounds(), currentTime = Number(video?.currentTime);
    const videoMatches = video?.dataset.sessionId === sessionId && Number.isFinite(currentTime)
      && currentTime >= bounds.start - .5 && currentTime <= bounds.end + .5;
    const playhead = videoMatches ? currentTime : Number(state.selectedLine?.source_start_time ?? session?.source_start ?? bounds.start);
    return { sessionId, playhead, timelineStart: dubTimelineRange.start, timelineSpan: dubTimelineRange.span };
  }

  function waitForDubbingVideo(video) {
    if (video.readyState >= 1 && Number.isFinite(video.duration)) return Promise.resolve(true);
    return new Promise((resolve) => {
      let timer;
      const finish = (ready) => { clearTimeout(timer); video.removeEventListener("loadedmetadata", onReady); video.removeEventListener("error", onError); resolve(ready); };
      const onReady = () => finish(true), onError = () => finish(false);
      video.addEventListener("loadedmetadata", onReady, { once: true }); video.addEventListener("error", onError, { once: true });
      timer = window.setTimeout(() => finish(false), 12000);
    });
  }

  async function applySharedNavigation(context) {
    const sessionId = String(context?.sessionId || "");
    if (!sessionId || sessionId !== state.selectedSessionId) return;
    const requestedTime = Number(context.playhead);
    const source = state.playbackLinesSessionId === sessionId ? state.playbackLines : state.lines;
    const items = source.filter((item) => item.session_id === sessionId && item.editorial_status !== "discarded");
    const nearest = Number.isFinite(requestedTime) ? items.reduce((best, item) => {
      const start = Number(item.source_start_time) || 0, end = Number(item.source_end_time) || start;
      const distance = requestedTime < start ? start - requestedTime : requestedTime > end ? requestedTime - end : 0;
      if (!best || distance < best.distance) return { item, distance };
      return best;
    }, null)?.item : null;
    if (nearest && nearest.occurrence_id !== state.selectedLine?.occurrence_id) await selectLine(nearest.occurrence_id, false);
    const video = ensureDubbingVideoSession(sessionId);
    if (!await waitForDubbingVideo(video)) { toast("O bloco foi selecionado, mas o vídeo não carregou para sincronizar o tempo.", true); return; }
    const bounds = getDubTimelineBounds();
    if (Number.isFinite(Number(context.timelineStart)) && Number.isFinite(Number(context.timelineSpan))) {
      saveDubTimelineView(clampDubTimelineView(Number(context.timelineStart), Number(context.timelineSpan), bounds));
    }
    if (Number.isFinite(requestedTime)) {
      const target = Math.max(bounds.start, Math.min(bounds.end, Math.min(video.duration, requestedTime)));
      if (Math.abs(video.currentTime - target) > .025) {
        await new Promise((resolve) => {
          let timer;
          const finish = () => { clearTimeout(timer); video.removeEventListener("seeked", finish); resolve(); };
          video.addEventListener("seeked", finish, { once: true });
          timer = window.setTimeout(finish, 2500); video.currentTime = target;
        });
      }
    }
    renderDubDialogueTimeline(); updateDubbingBlockCursor(); updateDubTimelineCursor();
  }

  function updateDubTimelineCursor() {
    const cursor = $("#dubDialogueCursor"), video = $("#dubPreviewVideo");
    if (!cursor) return;
    const time = Number(video?.currentTime);
    cursor.hidden = !Number.isFinite(time);
    if (!Number.isFinite(time)) return;
    if (time < dubTimelineRange.start || time > dubTimelineRange.end) {
      const left = time < dubTimelineRange.start ? time : time - dubTimelineRange.span * .8;
      const next = clampDubTimelineView(left, dubTimelineRange.span, getDubTimelineBounds());
      dubTimelineRange = next; saveDubTimelineView(next); renderDubDialogueTimeline(); return;
    }
    cursor.style.left = `${Math.max(0, Math.min(100, (time - dubTimelineRange.start) / dubTimelineRange.span * 100))}%`;
  }

  function renderDubDialogueTimeline() {
    const track = $("#dubDialogueTrack"), ticks = $("#dubDialogueTicks");
    if (!track || !ticks) return;
    const bounds = getDubTimelineBounds(), saved = dubTimelineViews[bounds.sessionId] || { start: bounds.start, span: bounds.span };
    const view = clampDubTimelineView(saved.start, saved.span, bounds);
    dubTimelineRange = view;
    ticks.replaceChildren();
    for (let index = 0; index <= 5; index++) {
      const label = make("span", "", fmtTime(view.start + view.span * index / 5));
      ticks.append(label);
    }
    track.querySelectorAll(".dub-dialogue-pin,.dub-timeline-selection-rect").forEach((node) => node.remove());
    timelinePinByOccurrence.clear();
    const items = dubTimelineItems();
    for (const item of items) {
      const time = Number(item.source_start_time);
      if (time < view.start || time > view.end) continue;
      const pin = make("button", `dub-dialogue-pin${item.occurrence_id === state.selectedLine?.occurrence_id ? " selected" : ""}${state.timelineSelectedOccurrenceIds.has(item.occurrence_id) ? " multi-selected" : ""}${item.editorial_status === "discarded" ? " discarded" : ""}`);
      pin.type = "button"; pin.dataset.occurrenceId = item.occurrence_id; pin.dataset.eventId = item.event?.event_id || "";
      pin.style.left = `${Math.max(0, Math.min(100, (time - view.start) / view.span * 100))}%`;
      const color = colorForLineItem(item) || "#65a7ff"; pin.style.setProperty("--character-color", color);
      pin.innerHTML = '<svg viewBox="0 0 16 30" aria-hidden="true"><rect class="pin-head" x="4" y="1" width="8" height="7" rx="1"/><path class="pin-stem" d="M8 8v19"/></svg>';
      pin.title = `#${item.event?.sequence ?? "—"} · ${item.character_name || "Sem personagem"} · ${fmtTime(time)}\n${item.dub_text || item.source_text || ""}\nClique para selecionar; duplo clique para ir ao início.`;
      pin.setAttribute("aria-label", `Diálogo ${item.event?.sequence ?? ""}, ${item.character_name || "sem personagem"}, ${fmtTime(time)}`);
      track.append(pin);
      timelinePinByOccurrence.set(item.occurrence_id, pin);
    }
    const video = $("#dubPreviewVideo"), cursor = $("#dubDialogueCursor");
    if (Number.isFinite(Number(video?.currentTime))) { cursor.hidden = false; cursor.style.left = `${Math.max(0, Math.min(100, (Number(video.currentTime) - view.start) / view.span * 100))}%`; }
    else cursor.hidden = true;
    const allSelected = items.length > 0 && items.every((item) => state.timelineSelectedOccurrenceIds.has(item.occurrence_id));
    const allButton = $("#dubTimelineSelectAll");
    if (allButton) { allButton.classList.toggle("active", allSelected); allButton.title = allSelected ? "Limpar seleção de pinos" : "Selecionar todos os pinos da sessão"; }
    const hasSelection = selectedDubTimelineItems().length > 0;
    for (const selector of ["#dubTimelineMoveLeft", "#dubTimelineMoveRight"]) { const button = $(selector); if (button) button.disabled = !hasSelection; }
    const zoomIn = $("#dubTimelineZoomIn"), zoomOut = $("#dubTimelineZoomOut"), fit = $("#dubTimelineZoomFit");
    if (zoomIn) zoomIn.disabled = view.span <= .251;
    if (zoomOut) zoomOut.disabled = view.span >= bounds.span - .001;
    if (fit) fit.disabled = view.span >= bounds.span - .001;
  }

  function changeDubTimelineZoom(factor, anchorRatio = .5) {
    const bounds = getDubTimelineBounds(), ratio = Math.max(0, Math.min(1, anchorRatio));
    const span = Math.max(.25, Math.min(bounds.span, dubTimelineRange.span * factor));
    const anchor = dubTimelineRange.start + dubTimelineRange.span * ratio;
    const view = clampDubTimelineView(anchor - span * ratio, span, bounds);
    saveDubTimelineView(view); renderDubDialogueTimeline();
  }

  function fitDubTimeline() {
    if (!state.selectedSessionId) return;
    delete dubTimelineViews[state.selectedSessionId];
    try { localStorage.setItem(DUB_TIMELINE_VIEW_KEY, JSON.stringify(dubTimelineViews)); } catch { /* optional */ }
    renderDubDialogueTimeline();
  }

  function dubTimelineMoveStep() {
    const value = Number($("#dubTimelineMoveStep")?.value);
    return Number.isFinite(value) && value > 0 ? Math.min(3600, value) : 1;
  }

  function selectedDubTimelineItems() {
    return dubTimelineItems().filter((item) => state.timelineSelectedOccurrenceIds.has(item.occurrence_id));
  }

  function takeAudioUrl(takeId) {
    const params = new URLSearchParams({ project_id: window.DUBSYNC_PROJECT_ID || "" });
    return `/api/dubbing/takes/${encodeURIComponent(takeId)}/audio?${params}`;
  }

  function setDubTimelineSelection(ids, anchor = "") {
    state.timelineSelectedOccurrenceIds = new Set(ids); dubTimelineAnchor = anchor || [...state.timelineSelectedOccurrenceIds].at(-1) || "";
    renderDubDialogueTimeline();
  }

  function selectDubTimelinePin(occurrenceId, modifiers = {}) {
    const items = dubTimelineItems(), index = items.findIndex((item) => item.occurrence_id === occurrenceId); if (index < 0) return;
    let ids = new Set(state.timelineSelectedOccurrenceIds);
    if (modifiers.shift && dubTimelineAnchor) {
      const anchor = items.findIndex((item) => item.occurrence_id === dubTimelineAnchor);
      ids = anchor >= 0 ? new Set(items.slice(Math.min(anchor, index), Math.max(anchor, index) + 1).map((item) => item.occurrence_id)) : new Set([occurrenceId]);
    } else if (modifiers.ctrl || modifiers.meta) { if (ids.has(occurrenceId)) ids.delete(occurrenceId); else ids.add(occurrenceId); }
    else ids = new Set([occurrenceId]);
    if (!ids.size) ids.add(occurrenceId);
    setDubTimelineSelection(ids, occurrenceId); void selectLine(occurrenceId);
  }

  function seekDubTimeline(time) {
    const video = $("#dubPreviewVideo"), bounds = getDubTimelineBounds();
    if (!state.selectedLine || video.dataset.sessionId !== state.selectedSessionId) {
      const nearest = dubTimelineItems().reduce((best, item) => Math.abs(Number(item.source_start_time) - time) < Math.abs(Number(best?.source_start_time ?? Infinity) - time) ? item : best, null);
      if (nearest) void selectLine(nearest.occurrence_id);
    }
    video.currentTime = Math.max(0, Math.min(Number.isFinite(Number(video.duration)) ? Number(video.duration) : bounds.end, time));
  }

  function previewDubPinDrag(drag, delta) {
    const bounds = getDubTimelineBounds(), minStart = Math.min(...drag.items.map((item) => Number(item.source_start_time))), maxEnd = Math.max(...drag.items.map((item) => Number(item.source_end_time || item.source_start_time)));
    drag.delta = Math.max(bounds.start - minStart, Math.min(bounds.end - maxEnd, delta));
    for (const item of drag.items) { const pin = $(`.dub-dialogue-pin[data-occurrence-id="${CSS.escape(item.occurrence_id)}"]`); if (pin) pin.style.left = `${Math.max(0, Math.min(100, (Number(item.source_start_time) + drag.delta - dubTimelineRange.start) / dubTimelineRange.span * 100))}%`; }
  }

  async function moveDubTimelineSelection(delta) {
    const items = selectedDubTimelineItems(); if (!items.length) { toast("Selecione um pino de diálogo para mover.", true); return; }
    const bounds = getDubTimelineBounds(), minStart = Math.min(...items.map((item) => Number(item.source_start_time))), maxEnd = Math.max(...items.map((item) => Number(item.source_end_time || item.source_start_time)));
    const shift = Math.max(bounds.start - minStart, Math.min(bounds.end - maxEnd, Number(delta) || 0)); if (Math.abs(shift) < .0005) return;
    const ids = new Set(items.map((item) => item.occurrence_id));
    try {
      const eventIds = new Set();
      for (const item of items) {
        const eventId = item.event?.event_id; if (!eventId || eventIds.has(eventId)) continue; eventIds.add(eventId);
        const body = { start_time: Number((Number(item.source_start_time) + shift).toFixed(3)) };
        if (item.source_end_time != null) body.end_time = Number((Number(item.source_end_time) + shift).toFixed(3));
        await requestCapture(`/api/events/${encodeURIComponent(eventId)}/review/update`, { method: "POST", body: JSON.stringify(body) });
      }
      state.timelineSelectedOccurrenceIds = ids; await loadLines(true); await loadPlaybackLines(); state.timelineSelectedOccurrenceIds = ids;
      renderDubDialogueTimeline(); await window.DubSyncCaptureRefreshTimeline?.(); toast(`${items.length} pino(s) movido(s) ${shift < 0 ? "para a esquerda" : "para a direita"}.`);
    } catch (error) { state.timelineSelectedOccurrenceIds = ids; await loadLines(true); await loadPlaybackLines(); state.timelineSelectedOccurrenceIds = ids; renderLines(); toast(error.message, true); }
  }

  function navigateDubTimeline(direction) {
    const items = dubTimelineItems().filter((item) => item.editorial_status !== "discarded"); if (!items.length) return;
    const now = Number($("#dubPreviewVideo").currentTime) || Number(state.selectedLine?.source_start_time) || 0;
    const next = direction < 0 ? [...items].reverse().find((item) => Number(item.source_start_time) < now - .04) || items[0]
      : items.find((item) => Number(item.source_start_time) > now + .04) || items[items.length - 1];
    setDubTimelineSelection([next.occurrence_id], next.occurrence_id); void selectLine(next.occurrence_id).then(() => seekDubTimeline(Number(next.source_start_time)));
  }

  async function discardDubTimelineSelection() {
    const items = selectedDubTimelineItems(), events = [...new Map(items.map((item) => [item.event?.event_id, item.event]).filter(([id]) => id)).values()];
    if (!events.length) { toast("Selecione um pino de diálogo para excluir ou descartar.", true); return; }
    try {
      const settings = await requestCapture("/api/settings"), permanent = Boolean(settings.review?.permanent_discard);
      const promptText = events.length > 1
        ? (permanent ? "Excluir definitivamente " : "Descartar ") + events.length + " diálogos selecionados?"
        : permanent ? "Excluir este diálogo definitivamente? Os dados de dublagem não compartilhados também serão apagados." : "Descartar este diálogo e preservá-lo para revisão?";
      if (settings.review?.confirm_dialogue_removal !== false && !confirm(promptText)) return;
      for (const event of events) {
        const route = permanent ? "/api/events/" + encodeURIComponent(event.event_id) : "/api/events/" + encodeURIComponent(event.event_id) + "/review/discard";
        await requestCapture(route, permanent ? { method: "DELETE" } : { method: "POST", body: "{}" });
      }
      state.timelineSelectedOccurrenceIds.clear(); await loadAll(true); await window.DubSyncCaptureRefreshTimeline?.();
      toast(permanent ? "Diálogo(s) excluído(s) definitivamente." : "Diálogo(s) descartado(s) e preservado(s) para revisão.");
    } catch (error) { toast(error.message, true); }
  }

  function handleDubTimelineShortcut(action, steps = {}) {
    const video = $("#dubPreviewVideo"), selected = state.timelineSelectedOccurrenceIds.size > 0;
    switch (action) {
      case "togglePlayback": if (video.paused) void video.play(); else video.pause(); return true;
      case "seekBackward": case "seekFineBackward": case "seekFastBackward":
        if (selected) void moveDubTimelineSelection(-dubTimelineMoveStep()); else seekDubTimeline((Number(video.currentTime) || 0) - (Number(steps[action === "seekFineBackward" ? "fine" : action === "seekFastBackward" ? "fast" : "main"]) || 1)); return true;
      case "seekForward": case "seekFineForward": case "seekFastForward":
        if (selected) void moveDubTimelineSelection(dubTimelineMoveStep()); else seekDubTimeline((Number(video.currentTime) || 0) + (Number(steps[action === "seekFineForward" ? "fine" : action === "seekFastForward" ? "fast" : "main"]) || 1)); return true;
      case "previousDialogue": navigateDubTimeline(-1); return true;
      case "nextDialogue": navigateDubTimeline(1); return true;
      case "zoomIn": changeDubTimelineZoom(.8); return true;
      case "zoomOut": changeDubTimelineZoom(1.25); return true;
      case "fitTimeline": fitDubTimeline(); return true;
      case "centerPlayhead": { const view = clampDubTimelineView((Number(video.currentTime) || 0) - dubTimelineRange.span / 2, dubTimelineRange.span, getDubTimelineBounds()); saveDubTimelineView(view); renderDubDialogueTimeline(); return true; }
      case "generateDubbingTake": void generate(); return true;
      case "manualDialogue": window.DubSyncCaptureOpenManualDialogue?.(state.selectedSessionId, Number(video.currentTime) || Number(state.selectedLine?.source_start_time) || 0); return true;
      case "removeDialogue": void discardDubTimelineSelection(); return true;
      case "openDubbing": return true;
      default: return false;
    }
  }

  function initializeDubTimelineInteractions() {
    const track = $("#dubDialogueTrack"); if (!track || track.dataset.controlsBound) return;
    track.dataset.controlsBound = "true";
    const drawBox = (left, width) => {
      let box = track.querySelector(":scope > .dub-timeline-selection-rect");
      if (!box) { box = make("div", "dub-timeline-selection-rect"); track.append(box); }
      box.style.left = `${left}px`; box.style.width = `${width}px`; return box;
    };
    track.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const pin = event.target.closest(".dub-dialogue-pin");
      if (pin) {
        event.preventDefault(); event.stopPropagation(); if (state.timelineSelectedOccurrenceIds.has(pin.dataset.occurrenceId) && !event.shiftKey && !event.ctrlKey && !event.metaKey) void selectLine(pin.dataset.occurrenceId); else selectDubTimelinePin(pin.dataset.occurrenceId, event);
        const items = selectedDubTimelineItems(); dubTimelinePinDrag = { items, startX: event.clientX, width: Math.max(1, track.getBoundingClientRect().width), span: dubTimelineRange.span, delta: 0, moved: false };
        dubTimelineIgnoreClick = true; track.setPointerCapture(event.pointerId); return;
      }
      const rect = track.getBoundingClientRect();
      dubTimelineMarquee = { pointerId: event.pointerId, startX: event.clientX, rect, moved: false, additive: event.ctrlKey || event.metaKey };
      track.classList.add("drawing-selection"); drawBox(event.clientX - rect.left, 0); track.setPointerCapture(event.pointerId);
    }, true);
    track.addEventListener("pointermove", (event) => {
      if (dubTimelinePinDrag) {
        const dx = event.clientX - dubTimelinePinDrag.startX; if (Math.abs(dx) > 2) dubTimelinePinDrag.moved = true;
        previewDubPinDrag(dubTimelinePinDrag, dx / Math.max(1, dubTimelinePinDrag.width) * dubTimelinePinDrag.span); event.preventDefault(); return;
      }
      if (!dubTimelineMarquee || dubTimelineMarquee.pointerId !== event.pointerId) return;
      const drag = dubTimelineMarquee, left = Math.min(drag.startX, event.clientX) - drag.rect.left, width = Math.abs(event.clientX - drag.startX);
      if (width > 4) drag.moved = true; drawBox(Math.max(0, left), Math.min(width, drag.rect.width)); event.preventDefault();
    }, true);
    track.addEventListener("pointerup", (event) => {
      if (dubTimelinePinDrag) {
        const drag = dubTimelinePinDrag; dubTimelinePinDrag = null; track.querySelector(":scope > .dub-timeline-selection-rect")?.remove(); track.classList.remove("drawing-selection");
        setTimeout(() => { dubTimelineIgnoreClick = false; }, 0);
        if (drag.moved) void moveDubTimelineSelection(drag.delta); else renderDubDialogueTimeline();
        event.preventDefault(); event.stopImmediatePropagation(); return;
      }
      if (!dubTimelineMarquee || dubTimelineMarquee.pointerId !== event.pointerId) return;
      const drag = dubTimelineMarquee; dubTimelineMarquee = null; track.querySelector(":scope > .dub-timeline-selection-rect")?.remove(); track.classList.remove("drawing-selection");
      if (drag.moved) {
        const left = Math.min(drag.startX, event.clientX), right = Math.max(drag.startX, event.clientX);
        const ids = [...track.querySelectorAll(".dub-dialogue-pin")].filter((pin) => { const rect = pin.getBoundingClientRect(), x = rect.left + rect.width / 2; return x >= left && x <= right; }).map((pin) => pin.dataset.occurrenceId);
        setDubTimelineSelection(drag.additive ? new Set([...state.timelineSelectedOccurrenceIds, ...ids]) : ids, ids.at(-1) || "");
        dubTimelineIgnoreClick = true; setTimeout(() => { dubTimelineIgnoreClick = false; }, 0); event.preventDefault(); event.stopImmediatePropagation();
      }
    }, true);
    track.addEventListener("pointercancel", () => { dubTimelineMarquee = null; dubTimelinePinDrag = null; track.querySelector(":scope > .dub-timeline-selection-rect")?.remove(); track.classList.remove("drawing-selection"); renderDubDialogueTimeline(); }, true);
    track.addEventListener("click", (event) => {
      if (dubTimelineIgnoreClick) { event.stopImmediatePropagation(); return; }
      const pin = event.target.closest(".dub-dialogue-pin");
      if (pin) { event.preventDefault(); event.stopImmediatePropagation(); selectDubTimelinePin(pin.dataset.occurrenceId, event); return; }
      const rect = track.getBoundingClientRect(), ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
      seekDubTimeline(dubTimelineRange.start + ratio * dubTimelineRange.span);
    }, true);
    track.addEventListener("wheel", (event) => {
      if (!event.ctrlKey) return; event.preventDefault();
      const rect = track.getBoundingClientRect(), ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
      changeDubTimelineZoom(event.deltaY < 0 ? .8 : 1.25, ratio);
    }, { passive: false });
    $("#dubTimelinePreviousPin").addEventListener("click", () => navigateDubTimeline(-1));
    $("#dubTimelineNextPin").addEventListener("click", () => navigateDubTimeline(1));
    $("#dubTimelineMoveLeft").addEventListener("click", () => void moveDubTimelineSelection(-dubTimelineMoveStep()));
    $("#dubTimelineMoveRight").addEventListener("click", () => void moveDubTimelineSelection(dubTimelineMoveStep()));
    $("#dubTimelineSelectAll").addEventListener("click", () => {
      const items = dubTimelineItems(), all = items.length > 0 && items.every((item) => state.timelineSelectedOccurrenceIds.has(item.occurrence_id));
      setDubTimelineSelection(all ? [] : items.map((item) => item.occurrence_id), all ? "" : items.at(-1)?.occurrence_id || "");
    });
    $("#dubTimelineZoomIn").addEventListener("click", () => changeDubTimelineZoom(.8));
    $("#dubTimelineZoomOut").addEventListener("click", () => changeDubTimelineZoom(1.25));
    $("#dubTimelineZoomFit").addEventListener("click", fitDubTimeline);
    $("#dubTimelineMoveStep").addEventListener("change", (event) => { if (!Number.isFinite(Number(event.target.value)) || Number(event.target.value) <= 0) event.target.value = "1"; });
  }

  async function renderTakes(takes) {
    const list = $("#dubTakeList"); list.replaceChildren();
    const ordered = [...takes].sort((a, b) => Number(b.version) - Number(a.version));
    const current = ordered.find((take) => take.status !== "superseded") || ordered[0];
    const historyCount = Math.max(0, ordered.length - (current ? 1 : 0));
    setText("#dubTakeCount", current ? `Áudio atual${historyCount ? ` · ${historyCount} versão(ões) anterior(es) guardada(s)` : ""}` : "Sem áudio");
    if (!takes.length) { list.append(make("div", "dub-empty-mini", "Nenhum take foi gerado para esta ocorrência.")); clearWaveform(); return; }
    if (!current) { list.append(make("div", "dub-empty-mini", "Nenhum áudio ativo para esta ocorrência.")); clearWaveform(); return; }
    for (const take of [current]) {
      const row = make("article", `dub-take-card${take.take_id === state.selectedTakeId ? " selected" : ""}`);
      row.dataset.takeId = take.take_id;
      const top = make("div", "dub-take-top");
      top.append(make("b", "", `TAKE ${String(take.version).padStart(2, "0")}`));
      top.append(make("span", `dub-status-pill ${take.status}`, labelStatus(take.status)));
      top.append(make("span", `dub-duration${deltaClass(take)}`, take.actual_duration_ms ? fmtDuration(take.actual_duration_ms) : "aguardando"));
      top.append(make("small", deltaClass(take), formatDelta(take)));
      row.append(top);
      if (take.error_message) row.append(make("small", "dub-take-error", take.error_message));
      if (!take.audio_master_path && ["queued", "generating", "publishing"].includes(take.status)) {
        row.append(make("small", "dub-empty-mini", "A prévia de áudio ficará disponível quando este take terminar de ser gerado."));
      }
      if (take.audio_master_path) {
        const audio = make("audio", "dub-take-audio"); audio.controls = true; audio.preload = "none"; audio.src = takeAudioUrl(take.take_id);
        audio.addEventListener("play", () => void selectTake(take, takes));
        audio.addEventListener("error", () => toast("Não foi possível carregar este take. Atualize a página e tente novamente.", true));
        row.append(audio);
      }
      const actions = make("div", "dub-take-actions");
      const listen = make("button", "button quiet", "▶ Ouvir take"); listen.type = "button"; listen.disabled = !take.audio_master_path;
      listen.addEventListener("click", async () => {
        try {
          await selectTake(take, takes);
          const preview = $("#dubPreviewAudio");
          preview.currentTime = 0;
          await preview.play();
        } catch (error) { toast(`Não foi possível reproduzir o take: ${error.message}`, true); }
      }); actions.append(listen);
      row.append(actions); list.append(row);
    }
    const chosen = current;
    if (chosen?.audio_master_path) await selectTake(chosen, takes, false);
    else clearWaveform();
  }

  function deltaClass(take) {
    const percent = Number(take.duration_delta_percent);
    if (!Number.isFinite(percent)) return "";
    return Math.abs(percent) <= 5 ? " delta-good" : Math.abs(percent) <= 15 ? " delta-warn" : " delta-bad";
  }
  function formatDelta(take) {
    if (take.duration_delta_percent == null) return "sem alvo";
    const value = Number(take.duration_delta_percent);
    return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
  }

  async function selectTake(take, allTakes, refreshRows = true) {
    state.selectedTakeId = take.take_id;
    const audio = $("#dubPreviewAudio");
    audio.pause(); audio.src = takeAudioUrl(take.take_id); audio.load();
    $("#dubPlaySynced").disabled = false; $("#dubAudioOnly").disabled = false;
    setText("#dubDurationDelta", `${take.actual_duration_ms ? fmtDuration(take.actual_duration_ms) : "—"} · ${formatDelta(take)}`);
    if (refreshRows) $("#dubTakeList").querySelectorAll(".dub-take-card").forEach((row) =>
      row.classList.toggle("selected", row.dataset.takeId === take.take_id));
    drawWaveform(audio.src);
  }

  function clearWaveform() {
    const audio = $("#dubPreviewAudio");
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    state.selectedTakeId = "";
    const canvas = $("#dubWaveformCanvas"), context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    $("#dubWaveformEmpty").classList.remove("hidden");
    $("#dubPlaySynced").disabled = true; $("#dubAudioOnly").disabled = true;
    setText("#dubDurationDelta", "Áudio indisponível");
  }

  async function drawWaveform(url) {
    const canvas = $("#dubWaveformCanvas"), context = canvas.getContext("2d");
    const revision = ++waveformRenderRevision;
    context.clearRect(0, 0, canvas.width, canvas.height);
    $("#dubWaveformEmpty").classList.remove("hidden");
    try {
      let waveform = waveformCache.get(url);
      if (waveform) {
        waveformCache.delete(url);
        waveformCache.set(url, waveform);
      } else {
        let pending = waveformPending.get(url);
        if (!pending) {
          pending = (async () => {
            const response = await fetch(url); if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const bytes = await response.arrayBuffer();
            const AudioCtx = window.AudioContext || window.webkitAudioContext; if (!AudioCtx) return null;
            const audioContext = new AudioCtx();
            try {
              const decoded = await audioContext.decodeAudioData(bytes.slice(0));
              const samples = decoded.getChannelData(0), bins = Math.max(512, Math.min(1600, canvas.width));
              const peaks = new Float32Array(bins), stride = Math.max(1, Math.ceil(samples.length / bins));
              for (let bin = 0; bin < bins; bin++) {
                const end = Math.min(samples.length, (bin + 1) * stride);
                for (let index = bin * stride; index < end; index++) peaks[bin] = Math.max(peaks[bin], Math.abs(samples[index]));
              }
              return { peaks, duration: decoded.duration };
            } finally { await audioContext.close().catch(() => {}); }
          })();
          waveformPending.set(url, pending);
        }
        try { waveform = await pending; }
        finally { if (waveformPending.get(url) === pending) waveformPending.delete(url); }
        if (!waveform) return;
        waveformCache.set(url, waveform);
        while (waveformCache.size > 48) waveformCache.delete(waveformCache.keys().next().value);
      }
      if (revision !== waveformRenderRevision || url !== $("#dubPreviewAudio").src) return;
      const { peaks, duration } = waveform;
      context.strokeStyle = "#548fff"; context.lineWidth = 1;
      context.beginPath();
      for (let x = 0; x < canvas.width; x++) {
        const peak = peaks[Math.min(peaks.length - 1, Math.floor(x * peaks.length / canvas.width))];
        const amplitude = Math.max(1, peak * canvas.height * 0.46), middle = canvas.height / 2;
        context.moveTo(x + .5, middle - amplitude); context.lineTo(x + .5, middle + amplitude);
      }
      context.stroke();
      $("#dubWaveformEmpty").classList.add("hidden");
      setText("#dubPreviewClock", `00:00 / ${fmtTime(duration)}`);
    } catch { /* The audio control remains available when waveform decoding is unavailable. */ }
  }

  function applyTakeReviewUpdate(takeId, updatedTake) {
    let previousStatus = "";
    const syncTake = (line) => {
      const take = line?.takes?.find((item) => item.take_id === takeId);
      if (take) {
        if (!previousStatus) previousStatus = take.status;
        Object.assign(take, updatedTake);
        updateLineRowStatus(line);
      }
    };
    state.lines.forEach(syncTake);
    state.playbackLines.forEach(syncTake);
    syncTake(state.selectedLine);
    const summary = state.overview?.summary;
    if (summary && previousStatus !== updatedTake.status) {
      if (previousStatus === "review") summary.review = Math.max(0, Number(summary.review || 0) - 1);
      if (updatedTake.status === "review") summary.review = Number(summary.review || 0) + 1;
      const generated = new Set(["generated", "review", "approved"]);
      if (generated.has(previousStatus) && !generated.has(updatedTake.status)) summary.generated = Math.max(0, Number(summary.generated || 0) - 1);
      if (!generated.has(previousStatus) && generated.has(updatedTake.status)) summary.generated = Number(summary.generated || 0) + 1;
      if (previousStatus === "approved") summary.approved = Math.max(0, Number(summary.approved || 0) - 1);
      if (updatedTake.status === "approved") summary.approved = Number(summary.approved || 0) + 1;
      if (previousStatus === "error") summary.errors = Math.max(0, Number(summary.errors || 0) - 1);
      if (updatedTake.status === "error") summary.errors = Number(summary.errors || 0) + 1;
      renderSummary(summary);
    }
    refreshLineReviewMetric();
    const takeCard = [...$("#dubTakeList").querySelectorAll(".dub-take-card")]
      .find((card) => card.dataset.takeId === takeId);
    if (takeCard && updatedTake.status) {
      const statusPill = takeCard.querySelector(".dub-status-pill");
      if (statusPill) {
        statusPill.className = `dub-status-pill ${updatedTake.status}`;
        statusPill.textContent = labelStatus(updatedTake.status);
      }
      const approveButton = takeCard.querySelector(".dub-take-actions .confirm");
      if (approveButton && updatedTake.status === "approved") {
        approveButton.textContent = "✓ Aprovado";
        approveButton.disabled = true;
        approveButton.title = "Este take já foi aprovado.";
      }
    }
  }

  async function reviewTake(takeId, action) {
    try {
      const updatedTake = await request(`/takes/${encodeURIComponent(takeId)}/review`, { method: "POST", body: JSON.stringify({ action, note: "" }) });
      applyTakeReviewUpdate(takeId, updatedTake);
      if (state.selectedLine?.occurrence_id) state.selectedOccurrenceIds.delete(state.selectedLine.occurrence_id);
      updateSelectionCount();
      const autoPublish = updatedTake.runtime_publish?.status === "queued"
        ? " A sincronização automática com o jogo foi enfileirada."
        : updatedTake.runtime_publish?.status === "not_ready"
          ? ` ${updatedTake.runtime_publish.message}` : "";
      toast((action === "approve" ? "Take aprovado para esta ocorrência." : "Take rejeitado; o histórico foi preservado.") + autoPublish);
      await refreshQueue(false);
      void refreshDubbingBlockPlan(true);
    } catch (error) { toast(error.message, true); }
  }

  async function approveSelectedTakes() {
    const visibleSelected = new Set(state.lines.filter((item) => state.selectedOccurrenceIds.has(item.occurrence_id))
      .map((item) => item.occurrence_id));
    if (!visibleSelected.size) { toast("Marque as falas que deseja aprovar.", true); return; }
    const button = $("#dubApproveSelected");
    button.disabled = true;
    try {
      const fresh = await request(`/lines?session_id=${encodeURIComponent(state.selectedSessionId)}&status=all`);
      const candidates = (fresh.items || []).filter((item) => visibleSelected.has(item.occurrence_id))
        .map((item) => ({ item, take: item.takes[0] }))
        .filter(({ take }) => take?.status === "review" && take.audio_master_path);
      if (!candidates.length) { toast("Nenhuma fala marcada tem um take recente aguardando revisão.", true); return; }
      if (!confirm(`Aprovar ${candidates.length} take(s) das falas marcadas? Confirme que ouviu cada áudio e que o texto, a voz e a qualidade estão corretos.`)) return;
      let approved = 0;
      let autoPublishQueued = false;
      let autoPublishMessage = "";
      const errors = [];
      for (const { item, take } of candidates) {
        try {
          const updatedTake = await request(`/takes/${encodeURIComponent(take.take_id)}/review`, {
            method: "POST", body: JSON.stringify({ action: "approve", note: "" }),
          });
          applyTakeReviewUpdate(take.take_id, updatedTake);
          autoPublishQueued ||= updatedTake.runtime_publish?.status === "queued";
          if (updatedTake.runtime_publish?.status === "not_ready") autoPublishMessage = updatedTake.runtime_publish.message || "";
          state.selectedOccurrenceIds.delete(item.occurrence_id);
          updateSelectionCount();
          approved++;
        } catch (error) { errors.push(`${item.dub_text || item.occurrence_id}: ${error.message}`); }
      }
      await refreshQueue(false);
      void refreshDubbingBlockPlan(true);
      toast(errors.length
        ? `${approved} take(s) aprovados; ${errors.length} falharam. Revise os restantes. ${errors[0]}`
        : `${approved} take(s) aprovados.${autoPublishQueued ? " Sincronização automática com o jogo enfileirada." : autoPublishMessage ? ` ${autoPublishMessage}` : ""}`, Boolean(errors.length));
    } catch (error) { toast(error.message, true); }
    finally { updateSelectionCount(); }
  }

  async function refreshProvider() {
    try {
      state.provider = await request("/provider");
      const provider = state.provider;
      const badge = $("#dubModelBadge");
      const online = provider.api_online && provider.model_loaded;
      badge.textContent = provider.model_loaded ? `● OmniVoice · ${provider.device || "local"}` : provider.api_online ? "◷ OmniVoice · carregando modelo" : provider.build_running ? "◷ OmniVoice · iniciando" : "○ OmniVoice · offline";
      badge.className = `dub-status-pill ${online ? "ready" : "waiting"}`;
      const button = $("#dubProviderButton");
      button.hidden = Boolean(provider.api_online || provider.build_running);
      button.disabled = !provider.docker_available;
      button.textContent = provider.docker_available ? "Iniciar OmniVoice" : "Docker indisponível";
      button.title = provider.message || provider.error || "Inicia o serviço local e prepara o modelo.";
      if (provider.model_loaded) setText("#dubQueueState", "Pronto para gerar");
      return provider;
    } catch (error) { setText("#dubModelBadge", error.message); return null; }
  }

  async function refreshQueue(reloadLines = true) {
    if (!state.selectedSessionId) return;
    try {
      const previousActive = Number(state.queue?.counts?.queued || 0) + Number(state.queue?.counts?.generating || 0);
      const queueParams = new URLSearchParams();
      if (state.selectionBatchId) queueParams.set("batch_id", state.selectionBatchId);
      else if (state.videoBatchId) queueParams.set("batch_id", state.videoBatchId);
      else queueParams.set("session_id", state.selectedSessionId);
      state.queue = await request(`/queue?${queueParams}`);
      const selectionProgressChanged = applySelectionBatchProgress(state.queue);
      renderQueue(state.queue);
      const currentActive = Number(state.queue?.counts?.queued || 0) + Number(state.queue?.counts?.generating || 0);
      if (reloadLines || selectionProgressChanged || (previousActive > 0 && currentActive === 0)) {
        await loadLines(true);
        await loadPlaybackLines();
        void refreshDubbingBlockPlan(true);
      }
    } catch (error) { console.warn("Fila de dublagem:", error); }
  }

  function renderQueue(queue) {
    const counts = queue.counts || {}, total = Object.values(counts).reduce((a, b) => a + Number(b || 0), 0);
    const batch = queue.batch;
    const progress = batch ? Math.min(100, Number(batch.percent) || 0) : 0;
    $("#dubBatchProgress").style.width = `${progress}%`;
    const publication = batch?.publication || {};
    setText("#dubQueuePercent", batch
      ? `${batch.finished} / ${batch.total} geradas · ${progress}% · no jogo ${publication.ready || 0} · sincronizando ${publication.pending || 0} · falhas ao enviar ${publication.errors || 0} · gerando ${batch.generating} · aguardando ${batch.queued} · erros de geração ${batch.errors} · canceladas ${batch.cancelled}`
      : total ? `Geração individual · ${counts.generating || 0} em geração · ${counts.queued || 0} aguardando · ${counts.error || 0} erros`
        : "Nenhum lote recente");
    setText("#dubBatchGenerated", batch ? batch.completed : Number($("#dubStatGenerated").textContent) || 0);
    setText("#dubBatchErrors", batch ? batch.errors : counts.error || 0);
    setText("#dubBatchQueued", batch ? batch.queued : counts.queued || 0);
    setText("#dubBatchReview", batch ? batch.generating : counts.generating || 0);
    const statePill = $("#dubQueueState");
    const publishing = Number(publication.pending || 0) > 0;
    const publicationErrors = Number(publication.errors || 0) > 0;
    statePill.textContent = queue.paused ? "Pausada" : counts.generating ? "Em geração" : counts.queued ? "Na fila" : publishing ? "Sincronizando jogo" : counts.error || publicationErrors ? "Requer atenção" : "Parada";
    statePill.className = `dub-status-pill ${queue.paused ? "waiting" : counts.generating ? "generating" : counts.error || publicationErrors ? "failed" : publishing ? "publishing" : counts.queued ? "queued" : "waiting"}`;
    $("#dubPauseQueue").textContent = queue.paused ? "▶ Retomar fila" : "Ⅱ Pausar fila";
    const list = $("#dubQueueList"); list.replaceChildren();
    const items = queue.items || [];
    if (!items.length) { list.append(make("small", "dub-empty-mini", "Fila sem jobs ativos ou com erro.")); return; }
    for (const job of items.slice(0, 5)) {
      const row = make("div", "dub-queue-item");
      row.append(make("span", `dub-queue-dot ${job.status}`, "●"));
      row.append(make("span", "", `${job.text.slice(0, 52)}${job.text.length > 52 ? "…" : ""}`));
      const itemStatus = job.status === "completed"
        ? ({ ready: "✓ Pronta no jogo", approved: "✓ Pronta no jogo", publishing: "◌ Sincronizando com o jogo", generated: "◌ Aguardando sincronização", publish_error: "⚠ Falha ao enviar ao jogo" }[job.take_status] || "✓ Geração concluída")
        : ({ queued: "◷ Aguardando", generating: "◌ Gerando", error: "⚠ Erro", cancelled: "○ Cancelado" }[job.status] || labelStatus(job.status));
      row.append(make("small", "", `${itemStatus}${job.status === "error" ? ` · ${job.attempt}/${job.max_attempts}` : ""}`));
      list.append(row);
    }
    $("#dubRetryErrors").disabled = !counts.error;
    $("#dubCancelQueue").disabled = !counts.queued;
  }

  function characterAvatar(character, className = "dub-character-avatar") {
    const avatar = make("span", className);
    avatar.setAttribute("aria-hidden", "true");
    const initial = (character.display_name || character.name || "?").trim().slice(0, 1).toLocaleUpperCase("pt-BR") || "?";
    if (/^#[0-9a-f]{6}$/i.test(String(character.color || ""))) {
      avatar.dataset.characterColor = character.color;
      avatar.style.setProperty("--character-color", character.color);
    }
    if (!character.photo_url) {
      avatar.textContent = initial;
      return avatar;
    }
    avatar.classList.add("has-photo");
    const image = document.createElement("img");
    image.alt = "";
    image.loading = "lazy";
    image.src = character.photo_url;
    image.addEventListener("error", () => { image.remove(); avatar.classList.remove("has-photo"); avatar.textContent = initial; }, { once: true });
    avatar.append(image);
    return avatar;
  }

  function renderCharacters() {
    const list = $("#dubCharacterList"), query = $("#dubCharacterSearch").value.trim().toLocaleLowerCase(); list.replaceChildren();
    const characters = state.characters.filter((character) => character.active && character.roi_id && (!query || `${character.display_name} ${character.name} ${(character.aliases_json || []).join(" ")}`.toLocaleLowerCase().includes(query)));
    if (!characters.length) list.append(make("div", "dub-empty-mini", query ? "Nenhum personagem encontrado." : "Nenhum personagem ativo cadastrado na ROI da Captura."));
    for (const character of characters) {
      const row = make("div", `dub-character-card${character.character_id === state.selectedCharacterId ? " selected" : ""}`);
      row.dataset.characterId = character.character_id;
      row.setAttribute("role", "option"); row.setAttribute("aria-selected", String(character.character_id === state.selectedCharacterId));
      const avatar = characterAvatar(character);
      const info = make("span", "dub-character-info"), characterName = make("b", "", character.display_name || character.name);
      if (/^#[0-9a-f]{6}$/i.test(String(character.color || ""))) characterName.style.color = character.color;
      info.append(characterName);
      info.append(make("small", "", `${character.voice_profiles.length} perfil(is) · ${character.name_verified ? "nome conferido" : "conferir nome"}`));
      const status = make("span", `dub-character-state ${character.voice_status}`, character.voice_status === "ready" ? "READY" : character.voice_status === "needs_review" ? "REVISAR" : "SEM VOZ");
      const main = make("button", "dub-character-main"); main.type = "button"; main.setAttribute("aria-label", `Selecionar ${character.display_name || character.name}`);
      main.append(avatar, info, status); main.addEventListener("click", () => { state.selectedCharacterId = character.character_id; renderCharacters(); renderCharacterDetail(character); });
      const assign = make("select", "dub-character-voice-select"); assign.setAttribute("aria-label", `Perfil vocal de ${character.display_name || character.name}`);
      fillCharacterVoiceProfileSelect(assign, character);
      assign.addEventListener("click", (event) => event.stopPropagation());
      assign.addEventListener("change", async (event) => {
        event.stopPropagation();
        assign.disabled = true;
        try {
          const result = await request(`/characters/${encodeURIComponent(character.character_id)}/voice-profile`, {
            method: "PUT", body: JSON.stringify({ voice_profile_id: assign.value || null }),
          });
          const reused = result.already_used_by || [];
          character.default_voice_profile_id = result.voice_profile_id || null;
          const assignedProfile = applyCharacterVoiceAssignment(character, result.voice_profile_id);
          state.selectedCharacterId = character.character_id;
          assign.disabled = !character.name_verified;
          refreshCharacterVoiceStatus(character);
          renderCharacterDetail(character);
          if (state.selectedLine?.character_id === character.character_id) {
            if (profileDraftDirty) renderProfileOptions(state.editingProfileId);
            else {
              renderProfileOptions(assignedProfile?.voice_profile_id || "");
              renderProfileEditor(assignedProfile, character);
            }
          }
          updateGenerateEnabled();
          toast(reused.length ? `Perfil associado. A mesma voz já está em uso por ${reused.join(", ")}.` : "Perfil vocal associado e salvo instantaneamente.");
        } catch (error) { toast(error.message, true); assign.disabled = false; }
      });
      const details = make("button", "dub-character-config", "⋯"); details.type = "button";
      details.title = `Ações para ${character.display_name || character.name}`;
      details.setAttribute("aria-label", details.title);
      details.addEventListener("click", (event) => { event.stopPropagation(); openCharacterActions(character); });
      row.append(main, assign, details);
      row.addEventListener("click", () => { state.selectedCharacterId = character.character_id; renderCharacters(); renderCharacterDetail(character); });
      list.append(row);
    }
    if (!selectedCharacter() && characters.length) { state.selectedCharacterId = characters[0].character_id; renderCharacterDetail(characters[0]); }
  }

  function renderCharacterDetail(character) {
    const box = $("#dubCharacterDetail"); box.replaceChildren();
    const header = make("div", "dub-character-detail-head");
    const identity = make("span", "dub-character-detail-identity"); identity.append(characterAvatar(character, "dub-character-avatar detail-avatar"));
    const characterName = make("b", "", character.display_name || character.name);
    if (/^#[0-9a-f]{6}$/i.test(String(character.color || ""))) characterName.style.color = character.color;
    identity.append(characterName); header.append(identity);
    const edit = make("button", "text-action", "Gerenciar na ROI"); edit.type = "button";
    edit.title = "Abrir o cadastro original deste personagem na tela Captura.";
    edit.addEventListener("click", () => { void openCharacterRoi(character); }); header.append(edit); box.append(header);
    box.append(make("small", "dub-roi-source-note", "Cadastro, nome, alias, cor e remoção são controlados na ROI da tela Captura."));
    if (!character.name_verified) box.append(make("p", "dub-verify-note", "Nome importado da Fase 1. Confira antes de associar uma voz."));
    const profiles = character.voice_profiles || [];
    if (character.sample_audio_available ?? character.sample_mp3_available) {
      const source = make("div", "dub-roi-reference-source");
      const label = make("span", "", `Áudio da ROI: ${roiAudioName(character)} · disponível automaticamente no modo Clonar voz.`);
      source.append(label); box.append(source);
    }
    for (const profile of profiles) {
      const row = make("div", "dub-profile-row");
      row.append(make("span", `dub-profile-indicator ${profile.ready ? "ready" : "failed"}`, profile.ready ? "●" : "○"));
      const detail = make("span", "dub-profile-row-info"); detail.append(make("b", "", profile.name));
      detail.append(make("small", "", `${profile.voice_mode === "clone" ? "clonagem" : "voz desenhada"} · ${profile.reference ? `${fmtDuration(profile.reference.duration_ms)} · ${labelStatus(profile.reference.quality_status)}` : "sem referência"}`));
      const editProfile = make("button", "button quiet", "Editar"); editProfile.type = "button"; editProfile.addEventListener("click", () => { if (!confirmDiscardProfileDraft()) return; renderProfileOptions(profile.voice_profile_id); renderProfileEditor(findProfile(profile.voice_profile_id), character); $("#dubProfileName").focus(); });
      row.append(detail, editProfile); box.append(row);
    }
    const add = make("button", "button quiet", "+ Novo perfil OmniVoice"); add.type = "button"; add.addEventListener("click", () => startNewVoiceProfile(character)); box.append(add);
  }

  function openCharacterActions(character) {
    if (!character) return;
    state.actionCharacterId = character.character_id;
    state.selectedCharacterId = character.character_id;
    $("#dubCharacterActionsTitle").textContent = character.display_name || character.name;
    $("#dubCharacterActionsContext").textContent = character.roi_id
      ? "Este card acompanha o cadastro ativo da ROI na tela Captura."
      : "O cadastro deste personagem não está mais ativo em uma ROI.";
    $("#dubCharacterConfirmIdentity").hidden = Boolean(character.name_verified);
    $("#dubCharacterConfirmIdentity").disabled = !character.active || !character.roi_id;
    $("#dubCharacterOpenRoi").disabled = !character.roi_id;
    $("#dubCharacterActionsDialog").showModal();
  }

  function actionCharacter() {
    return state.characters.find((item) => item.character_id === state.actionCharacterId) || null;
  }

  async function openCharacterRoi(character = selectedCharacter()) {
    if (!character?.roi_id) {
      document.querySelector('.nav-item[data-project-area="capture"]')?.click();
      toast("Abra a ROI da tela Captura para cadastrar ou remover personagens.");
      return false;
    }
    const openRoi = window.openDubbingCharacterRoi;
    if (typeof openRoi !== "function") {
      toast("Não foi possível abrir o cadastro da ROI nesta tela.", true);
      return false;
    }
    try {
      const opened = await openRoi(character.roi_id, character.legacy_character_profile_id);
      if (!opened) toast("Não encontrei a ROI responsável pelo cadastro. Abra o bloco correspondente na Captura e tente novamente.", true);
      return Boolean(opened);
    } catch (error) { toast(error.message, true); return false; }
  }

  async function confirmCharacterIdentity(character = actionCharacter()) {
    if (!character) return;
    try {
      const updated = await request(`/characters/${encodeURIComponent(character.character_id)}`, {
        method: "PUT",
        body: JSON.stringify({
          name: character.name, display_name: character.display_name || character.name,
          aliases: character.aliases_json || [], notes: character.notes || "",
          name_verified: true, active: true,
        }),
      });
      $("#dubCharacterActionsDialog").close();
      state.selectedCharacterId = character.character_id;
      storeCharacter(updated);
      toast("Identidade conferida. Agora você pode associar um perfil vocal.");
    } catch (error) { toast(error.message, true); }
  }

  function openCharacterVoiceProfile(character = actionCharacter()) {
    if (!character) return;
    if (!confirmDiscardProfileDraft()) return;
    $("#dubCharacterActionsDialog").close();
    if (!character.name_verified) {
      openCharacterActions(character);
      toast("Confirme a identidade deste personagem antes de abrir o perfil vocal.", true);
      return;
    }
    state.selectedCharacterId = character.character_id;
    renderCharacters();
    const profiles = character.voice_profiles || [];
    const profile = findProfile(character.default_voice_profile_id)
      || profiles.find((item) => item.active) || profiles[0];
    if (profile) {
      renderProfileOptions(profile.voice_profile_id);
      const owner = state.characters.find((item) => item.character_id === profile.character_id) || character;
      renderProfileEditor(findProfile(profile.voice_profile_id), owner);
      $("#dubProfileName").focus();
      toast(`Perfil vocal ${profile.name} aberto para edição.`);
      return;
    }
    startNewVoiceProfile(character);
  }

  function configureVoice(character) {
    if (!character) { toast("Selecione um personagem cadastrado na ROI.", true); return; }
    if (!character?.name_verified) { openCharacterActions(character); toast("Confira o personagem nas ações do card antes de associar uma voz.", true); return; }
    startNewVoiceProfile(character);
  }

  function startNewVoiceProfile(character = currentStudioCharacter()) {
    if (!character) { toast("Selecione um personagem antes de criar o perfil vocal.", true); return; }
    if (!character.name_verified) { openCharacterActions(character); toast("Confira o personagem nas ações do card antes de criar um perfil de voz.", true); return; }
    if (!confirmDiscardProfileDraft()) return;
    renderProfileOptions("");
    $("#dubVoiceSelect").value = "";
    state.profilePresetKey = "";
    renderProfileEditor(null, character);
    $("#dubProfileName").focus();
    toast(`Novo perfil local para ${character.display_name || character.name}.`);
  }

  function animateProfileSaved() {
    const button = $("#dubSaveProfile");
    button.classList.remove("saving", "saved");
    button.classList.add("saved");
    button.textContent = "✓ Perfil salvo";
    const status = $("#dubProfileSaveStatus");
    if (status) { status.textContent = "Perfil salvo."; status.className = "saved"; }
    setTimeout(() => { button.classList.remove("saved"); button.textContent = "Salvar perfil"; updateProfileSaveState(); }, 1900);
  }

  async function saveInlineVoiceProfile() {
    const button = $("#dubSaveProfile");
    const profileId = state.editingProfileId;
    const characterId = $("#dubVoiceCharacterId").value || currentStudioCharacter()?.character_id;
    const name = $("#dubProfileName").value.trim();
    if (!characterId || !name) { toast("Informe um personagem conferido e um nome para o perfil.", true); return; }
    const ownerCharacter = state.characters.find((item) => item.character_id === characterId);
    if (!ownerCharacter?.name_verified) { toast("Confira a identidade do personagem antes de salvar um perfil vocal.", true); return; }
    button.disabled = true; button.classList.add("saving"); button.textContent = "Salvando…";
    const body = {
      character_id: characterId, name, language: "pt-BR", voice_mode: $("#dubLocalProfileMode").value,
      default_speed: Number($("#dubSpeed").value || 1),
      voice_effect: $("#dubVoiceEffect").value,
      default_instruct: $("#dubLocalProfileMode").value === "design" ? voiceDesignInstruction()
        : findProfile(profileId)?.provider_voice_key === "dubsync:design-candidate" ? findProfile(profileId).default_instruct : "",
      default_direction: $("#dubInstruction").value.trim(),
      use_authorized: $("#dubProfileAuthorized").checked,
      preset_key: $("#dubLocalProfileMode").value === "design" ? state.profilePresetKey || null : null,
      reference_text: $("#dubProfileReferenceText").value.trim() || null,
      preview_text: $("#dubPreviewText").value,
      generation_settings: generationSettings(),
    };
    try {
      let saved = await request(`/voice-profiles${profileId ? `/${encodeURIComponent(profileId)}` : ""}`, {
        method: profileId ? "PUT" : "POST", body: JSON.stringify(body),
      });
      if (state.referenceRemoveRequested && saved.voice_profile_id) {
        saved = await request(`/voice-profiles/${encodeURIComponent(saved.voice_profile_id)}/reference`, { method: "DELETE" });
        state.referenceRemoveRequested = false;
      }
      const file = $("#dubReferenceFile").files[0];
      if (file && saved.voice_mode === "clone") {
        const params = new URLSearchParams({ filename: file.name, reference_text: $("#dubProfileReferenceText").value });
        const response = await request(`/voice-profiles/${encodeURIComponent(saved.voice_profile_id)}/references?${params}`, {
          method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" },
        });
        saved = response.profile;
      } else if (file) {
        $("#dubReferenceFile").value = "";
        if (state.referenceUploadUrl) URL.revokeObjectURL(state.referenceUploadUrl);
        state.referenceUploadUrl = "";
      }
      state.selectedCharacterId = characterId;
      storeVoiceProfile(saved, { refreshViews: false });
      if (!profileId && saved.voice_mode === "design") {
        state.candidateProfileId = saved.voice_profile_id;
        state.candidateLoadedProfileId = saved.voice_profile_id;
        state.candidateItems = [];
        state.designCandidateNodes.clear();
        $("#dubCandidateList").replaceChildren(); $("#dubCandidateHistory").classList.add("hidden");
      }
      refreshVoiceProfilePanel(saved.voice_profile_id, { select: true });
      const owner = state.characters.find((item) => item.character_id === characterId);
      animateProfileSaved();
      const roiSource = !file && saved.voice_mode === "clone" && saved.provider_voice_key !== "dubsync:design-candidate" && hasRoiAudio(owner);
      toast(file ? "Perfil salvo e áudio carregado. Revise a qualidade do arquivo para liberar a voz."
        : roiSource ? `Perfil salvo. ${roiAudioName(owner)} foi sincronizado automaticamente; confirme a autorização e revise a qualidade.`
          : "Perfil OmniVoice salvo localmente.");
    } catch (error) {
      toast(error.message, true);
      button.classList.remove("saving"); button.textContent = "Salvar perfil";
      const status = $("#dubProfileSaveStatus");
      if (status) { status.textContent = `Falha ao salvar: ${error.message}`; status.className = "error"; }
    } finally {
      if (!button.classList.contains("saved")) updateProfileSaveState();
    }
  }

  async function changeProfileActive() {
    const profile = findProfile(state.editingProfileId);
    if (!profile) return;
    const active = !profile.active;
    try {
      const updated = await request(`/voice-profiles/${encodeURIComponent(profile.voice_profile_id)}/active`, { method: "PUT", body: JSON.stringify({ active }) });
      storeVoiceProfile(updated, { refreshViews: false });
      refreshVoiceProfileStatus(profile.voice_profile_id, { updateModeControls: true, refreshCandidates: true });
      toast(active ? "Perfil ativado." : "Perfil inativado. Os takes anteriores foram preservados.");
    } catch (error) { toast(error.message, true); }
  }

  async function removeVoiceProfile() {
    const profile = findProfile(state.editingProfileId);
    if (profileDraftDirty && !confirmDiscardProfileDraft()) return;
    if (!profile || !confirm(`Remover o perfil “${profile.name}”? Perfis usados em takes serão inativados para preservar o histórico.`)) return;
    try {
      const result = await request(`/voice-profiles/${encodeURIComponent(profile.voice_profile_id)}`, { method: "DELETE" });
      removeVoiceProfileFromState(profile.voice_profile_id, result.deactivated);
      renderProfileOptions("");
      renderProfileEditor(null, currentStudioCharacter());
      updateGenerateEnabled();
      toast(result.deactivated ? "Perfil inativado; o histórico de takes foi preservado." : "Perfil removido.");
    } catch (error) { toast(error.message, true); }
  }

  async function removeVoiceReference() {
    const profile = findProfile(state.editingProfileId), file = $("#dubReferenceFile").files[0];
    if (file) {
      $("#dubReferenceFile").value = "";
      if (state.referenceUploadUrl) URL.revokeObjectURL(state.referenceUploadUrl);
      state.referenceUploadUrl = ""; $("#dubReferencePreview").removeAttribute("src"); $("#dubReferencePreview").classList.add("hidden");
      $("#dubRemoveReference").disabled = !profile?.reference;
      markProfileDraftDirty();
      if (!profile?.reference) {
        setVoiceMode("design");
        updateProfileModeControls();
      }
      toast("Arquivo escolhido removido do perfil.");
      const note = $(".dub-clone-copy small");
      if (note) note.textContent = profile?.reference ? `${fmtDuration(profile.reference.duration_ms)} · referência local salva` : "O áudio fica no DubSync local. Prefira uma fala limpa de 3 a 10 s.";
      renderVoiceReady(profile, currentStudioCharacter());
      return;
    }
    if (!profile?.reference) return;
    state.referenceRemoveRequested = true;
    state.profilePresetKey = "";
    setVoiceMode("design");
    updateProfileModeControls();
    markProfileDraftDirty();
    const status = $("#dubProfileReferenceStatus");
    if (status) status.replaceChildren(make("span", "", "O áudio de referência será removido ao salvar o perfil."));
    toast("Remoção preparada. Clique em Salvar perfil para aplicar.");
  }

  async function replaceRoiAudioReference(file) {
    if (!file) return;
    const characterId = $("#dubVoiceCharacterId").value || currentStudioCharacter()?.character_id || "";
    const character = state.characters.find((item) => item.character_id === characterId);
    if (!character?.active || !character.roi_id) { toast("Este personagem não está vinculado a uma ROI ativa.", true); return; }
    const roiCharacterId = character.legacy_character_profile_id;
    if (!roiCharacterId) { toast("Não foi possível localizar o cadastro deste personagem na ROI. Atualize os personagens e tente novamente.", true); return; }
    if (!/\.(mp3|wav)$/i.test(file.name)) { toast("Escolha um arquivo MP3 ou WAV.", true); return; }
    if (!file.size || file.size > 50 * 1024 * 1024) { toast("O áudio deve ter até 50 MiB e não pode estar vazio.", true); return; }
    const button = $("#dubReplaceRoiAudio"), profileId = state.editingProfileId;
    button.disabled = true; button.textContent = "Atualizando…";
    try {
      const audioType = file.name.toLowerCase().endsWith(".wav") ? "audio/wav" : "audio/mpeg";
      // Dubbing keeps a project-scoped character ID; the Capture endpoint
      // requires the linked ROI registry ID instead.
      const response = await fetch(`/api/roi-characters/${encodeURIComponent(roiCharacterId)}/audio-reference`, {
        method: "PUT",
        headers: { "Content-Type": audioType, "X-File-Name": encodeURIComponent(file.name) },
        body: file,
      });
      const text = await response.text();
      let result = {};
      try { result = text ? JSON.parse(text) : {}; } catch { result = { message: text }; }
      if (!response.ok) {
        const detail = result?.detail;
        throw new Error(typeof detail === "string" ? detail : detail?.message || result?.message || `HTTP ${response.status}`);
      }
      state.roiAudioRevision += 1;
      Object.assign(character, {
        sample_mp3_name: result.sample_mp3_name || file.name,
        sample_audio_name: result.sample_audio_name || result.sample_mp3_name || file.name,
        sample_mp3_bytes: result.sample_mp3_bytes || file.size,
        sample_audio_bytes: result.sample_audio_bytes || result.sample_mp3_bytes || file.size,
        sample_mp3_available: true, sample_audio_available: true,
      });
      refreshCharacterVoiceStatus(character);
      if (character.character_id === state.selectedCharacterId) renderCharacterDetail(character);
      const owner = character;
      const latest = findProfile(profileId);
      if (latest?.voice_mode === "clone" && latest.provider_voice_key !== "dubsync:design-candidate") {
        const synced = await syncRoiVoiceReference(latest, owner, { force: true });
        if (!synced) {
          refreshVoiceProfileStatus(profileId, { updateModeControls: true });
          return;
        }
        toast(`Áudio do ROI trocado por ${result.sample_mp3_name || file.name}. O perfil foi sincronizado; confirme a autorização antes de gerar takes.`);
      } else if (profileId) {
        refreshVoiceProfileStatus(profileId, { updateModeControls: true });
        toast(`Áudio do ROI trocado por ${result.sample_mp3_name || file.name}.`);
      } else {
        refreshCharacterVoiceStatus(character);
        if (character.character_id === state.selectedCharacterId) renderCharacterDetail(character);
        toast(`Áudio do ROI trocado por ${result.sample_mp3_name || file.name}.`);
      }
    } catch (error) {
      toast(`Não foi possível trocar o áudio do ROI: ${error.message}`, true);
    } finally {
      button.disabled = false;
      updateProfileModeControls();
    }
  }

  async function useRoiReference() {
    const profile = findProfile(state.editingProfileId);
    const character = state.characters.find((item) => item.character_id === profile?.character_id);
    if (!profile || !hasRoiAudio(character)) { toast("Este personagem não tem áudio de referência disponível na Captura.", true); return; }
    const button = $("#dubUseRoiReference");
    button.disabled = true; button.textContent = "Preparando áudio…";
    try {
      const synced = await request(`/voice-profiles/${encodeURIComponent(profile.voice_profile_id)}/sync-roi-audio`, { method: "POST" });
      storeVoiceProfile(synced, { refreshViews: false });
      refreshVoiceProfileStatus(profile.voice_profile_id, { updateModeControls: true });
      toast("Áudio da ROI selecionado. Confirme a autorização da voz para voltar a gerar takes.");
    } catch (error) { toast(error.message, true); }
    finally { button.textContent = "Usar este áudio como voz do perfil"; button.disabled = false; }
  }

  async function testVoiceProfile() {
    const profile = findProfile($("#dubVoiceSelect").value);
    const text = $("#dubPreviewText").value.trim();
    const designAudition = Boolean(profile?.active && profile.use_authorized && profile.voice_mode === "design"
      && profile.default_instruct === voiceDesignInstruction());
    const replaceablePreview = canReplaceProfilePreview(profile);
    if (!profile?.ready && !designAudition && !replaceablePreview) { toast("Autorize o perfil e salve os atributos atuais ou escolha uma voz oficial antes da prévia.", true); return; }
    if (!text) { toast("Digite um texto curto para a prévia.", true); return; }
    const button = $("#dubTestProfile"), audio = $("#dubProfilePreviewAudio");
    let previewGenerated = false;
    button.disabled = true; button.textContent = "Gerando prévia…";
    try {
      const response = await fetch(`/api/dubbing/voice-profiles/${encodeURIComponent(profile.voice_profile_id)}/preview`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          text, instruct: profile.voice_mode === "design" ? voiceDesignInstruction() : "",
          speed: Number($("#dubSpeed").value || 1),
          voice_effect: $("#dubVoiceEffect").value,
          generation_settings: generationSettings(),
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail?.message || data.detail || "Não foi possível gerar a prévia local.");
      }
      const blob = await response.blob();
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      if (state.profilePreviewUrl) URL.revokeObjectURL(state.profilePreviewUrl);
      state.profilePreviewUrl = URL.createObjectURL(blob);
      state.profilePreviewProfileId = profile.voice_profile_id;
      audio.src = state.profilePreviewUrl; audio.dataset.profileId = profile.voice_profile_id;
      audio.classList.remove("hidden");
      previewGenerated = true;
      await audio.play().catch(() => {});
      toast("Prévia gerada pelo OmniVoice local.");
    } catch (error) { toast(error.message, true); }
    finally {
      button.textContent = "▶ Testar voz";
      updateProfileModeControls();
      if (previewGenerated && $("#dubVoiceSelect").value === profile.voice_profile_id && canReplaceProfilePreview(profile)) button.disabled = false;
    }
  }

  function openVoiceDialog(character, profile = null) {
    $("#dubVoiceProfileId").value = profile?.voice_profile_id || "";
    $("#dubVoiceCharacterId").value = character.character_id;
    $("#dubVoiceDialogTitle").textContent = profile ? "Editar perfil vocal" : `Perfil vocal · ${character.display_name || character.name}`;
    $("#dubVoiceName").value = profile?.name || `${character.display_name || character.name} PT-BR 01`;
    $("#dubVoiceLanguage").value = profile?.language || "pt-BR";
    $("#dubVoiceMode").value = profile?.voice_mode || "clone";
    $("#dubVoiceDefaultInstruct").value = profile?.default_instruct || "";
    $("#dubVoiceDefaultSpeed").value = profile?.default_speed || 1;
    $("#dubVoiceAuthorized").checked = Boolean(profile?.use_authorized);
    $("#dubReferenceText").value = profile?.reference?.reference_text || "";
    $("#dubReferenceFile").value = "";
    $("#dubReferencePreview").classList.add("hidden");
    $("#dubVoiceReferenceFields").classList.toggle("hidden", $("#dubVoiceMode").value !== "clone");
    $("#dubVoiceInstructWrap").classList.toggle("hidden", $("#dubVoiceMode").value !== "design");
    if (state.selectedLine) {
      $("#dubExtractStart").value = Math.max(0, Number(state.selectedLine.source_start_time) - 1).toFixed(3);
      $("#dubExtractEnd").value = (Number(state.selectedLine.source_end_time) + 1).toFixed(3);
    }
    $("#dubVoiceDialog").showModal();
  }

  async function saveVoiceProfile() {
    const id = $("#dubVoiceProfileId").value, characterId = $("#dubVoiceCharacterId").value;
    const existingProfile = findProfile(id);
    const storedGenerationSettings = { ...(existingProfile?.generation_settings_json || {}) };
    delete storedGenerationSettings.voice_effect;
    const body = {
      character_id: characterId, name: $("#dubVoiceName").value.trim(), language: $("#dubVoiceLanguage").value,
      voice_mode: $("#dubVoiceMode").value, default_speed: Number($("#dubVoiceDefaultSpeed").value),
      default_instruct: $("#dubVoiceDefaultInstruct").value,
      default_direction: findProfile(id)?.default_direction || "",
      voice_effect: existingProfile?.voice_effect || "none",
      generation_settings: storedGenerationSettings,
      use_authorized: $("#dubVoiceAuthorized").checked,
    };
    try {
      let saved = await request(`/voice-profiles${id ? `/${encodeURIComponent(id)}` : ""}`, { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
      const file = $("#dubReferenceFile").files[0];
      if (file && saved.voice_mode === "clone") {
        const params = new URLSearchParams({ filename: file.name, reference_text: $("#dubReferenceText").value });
        const uploaded = await request(`/voice-profiles/${encodeURIComponent(saved.voice_profile_id)}/references?${params}`, { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } });
        saved = uploaded.profile;
      }
      $("#dubVoiceDialog").close();
      storeVoiceProfile(saved, { refreshViews: false });
      state.selectedCharacterId = characterId;
      refreshStoredVoiceProfileControls(state.characters.find((item) => item.character_id === characterId));
      renderProfileOptions($("#dubVoiceSelect").value || saved.voice_profile_id);
      if (state.editingProfileId === saved.voice_profile_id) refreshVoiceProfilePanel(saved.voice_profile_id);
      renderCharacterDetail(selectedCharacter());
      toast(file ? "Perfil salvo. Confira a qualidade da referência para habilitá-la." : "Perfil vocal salvo.");
    } catch (error) { toast(error.message, true); }
  }

  async function extractReference() {
    const profileId = $("#dubVoiceProfileId").value;
    const line = state.selectedLine;
    if (!profileId || !line) { toast("Salve o perfil vocal antes de extrair uma referência.", true); return; }
    const start = Number($("#dubExtractStart").value), end = Number($("#dubExtractEnd").value);
    try {
      const result = await request("/voice-references/extract", { method: "POST", body: JSON.stringify({ session_id: line.session_id, voice_profile_id: profileId, start_time: start, end_time: end, reference_text: $("#dubReferenceText").value }) });
      $("#dubVoiceDialog").close();
      storeVoiceProfile(result.profile, { refreshViews: false });
      const owner = state.characters.find((item) => item.character_id === result.profile.character_id);
      if (state.editingProfileId === profileId) {
        $("#dubProfileReferenceText").value = result.profile.reference?.reference_text || "";
        refreshVoiceProfileStatus(profileId, { updateModeControls: true });
      } else refreshVoiceProfileStatus(profileId);
      toast("Amostra extraída e normalizada. Revise sua qualidade no perfil.");
    } catch (error) { toast(error.message, true); }
  }

  async function saveStudioCharacterAssignment() {
    const item = state.selectedLine, select = $("#dubStudioCharacter");
    if (!item) return;
    const lineId = item.line_id, previousId = item.character_id || null, characterId = select.value || null;
    const character = currentStudioCharacter();
    applyStudioCharacterColor(character);
    if (profileDraftDirty) renderProfileOptions(state.editingProfileId);
    else {
      renderProfileOptions(character?.default_voice_profile_id || "");
      renderProfileEditor(findProfile($("#dubVoiceSelect").value), character);
    }
    updateGenerateEnabled();
    if (characterId === previousId) return;
    select.disabled = true;
    try {
      const saved = await scheduleLineAutosave(0);
      if (!saved) throw new Error("A alteração não foi salva. Tente novamente.");
      const filterCharacterId = $("#dubCharacterFilter").value;
      if (filterCharacterId && filterCharacterId !== (characterId || "")) {
        try { await loadLines(true); }
        catch (error) { renderLines(); toast(`Personagem salvo. Não foi possível atualizar a lista: ${error.message}`, true); return; }
      }
      toast("Personagem associado e salvo nesta fala.");
    } catch (error) {
      if (state.selectedLine?.line_id === lineId) {
        const previousCharacter = state.characters.find((entry) => entry.character_id === previousId);
        select.value = previousId || "";
        renderProfileOptions(state.selectedLine.voice_profile_id || "");
        renderProfileEditor(findProfile($("#dubVoiceSelect").value), previousCharacter);
        updateGenerateEnabled();
      }
      toast(error.message, true);
    } finally { select.disabled = false; }
  }

  async function saveLine() {
    clearTimeout(lineAutosaveTimer); lineAutosaveTimer = 0;
    const item = state.selectedLine;
    if (!item) { const saved = await lineAutosaveQueue; return saved !== false && !lineDirty; }
    if (!lineDirty) return await lineAutosaveQueue;
    const lineId = item.line_id, revision = lineEditRevision;
    const body = {
      dub_text: $("#dubText").value, character_id: $("#dubStudioCharacter").value || null,
      emotion: $("#dubEmotion").value, emotion_intensity: Number($("#dubIntensity").value),
    };
    lineDirty = false;
    setLineSaveStatus("Salvando…", "saving");
    const operation = lineAutosaveQueue.catch(() => false).then(async () => {
      const saved = await request(`/lines/${encodeURIComponent(lineId)}`, { method: "PUT", body: JSON.stringify(body) });
      if (state.selectedLine?.line_id === lineId) {
        Object.assign(state.selectedLine, saved);
        if (revision === lineEditRevision) setLineSaveStatus("Salvo automaticamente", "saved");
        updateGenerateEnabled();
      }
      updateSavedLineInViews(lineId, saved);
      return true;
    }).catch((error) => {
      if (state.selectedLine?.line_id === lineId && revision === lineEditRevision) {
        lineDirty = true;
        setLineSaveStatus("Falha ao salvar", "error", error.message);
      }
      toast(`Não foi possível salvar a fala: ${error.message}`, true);
      return false;
    });
    lineAutosaveQueue = operation;
    return operation;
  }

  async function generate() {
    const item = state.selectedLine;
    if (!item) { toast("Selecione uma ocorrência no roteiro.", true); return false; }
    if (!(await saveLine())) return false;
    const profileId = $("#dubVoiceSelect").value;
    if (!profileId) { toast("Selecione um perfil de voz pronto.", true); return false; }
    const oneOffSpeedText = $("#dubSingleTakeSpeed").value.trim();
    const oneOffSpeed = oneOffSpeedText === "" ? null : Number(oneOffSpeedText);
    if (oneOffSpeed !== null && (!Number.isFinite(oneOffSpeed) || oneOffSpeed < 0.5 || oneOffSpeed > 2)) {
      toast("A velocidade desta fala deve ficar entre 0,5× e 2×.", true); return false;
    }
    const takeGenerationSettings = generationSettings();
    if (oneOffSpeed !== null) takeGenerationSettings.duration = null;
    const button = $("#dubGenerateTake");
    button.disabled = true; button.innerHTML = '<span class="dub-inline-spinner" aria-hidden="true"></span> Gerando…';
    try {
      await request(`/lines/${encodeURIComponent(item.line_id)}/takes`, { method: "POST", body: JSON.stringify({
        occurrence_id: item.occurrence_id, voice_profile_id: profileId, instruct: $("#dubInstruction").value,
        speed: oneOffSpeed ?? Number($("#dubSpeed").value), manual_override: $("#dubManualOverride").checked,
        generation_settings: takeGenerationSettings,
      }) });
      toast("Take colocado na fila local de geração.");
      try {
        await refreshQueue(false);
        await loadLines(true);
        await loadPlaybackLines();
        void refreshDubbingBlockPlan(true);
      }
      catch (error) { toast(`Take enfileirado; não foi possível atualizar a lista: ${error.message}`, true); }
      return true;
    } catch (error) { toast(error.message, true); updateGenerateEnabled(); return false; }
  }

  async function startBatch(mode = "block", ignoreMissing = false) {
    if (!state.selectedSessionId) { toast("Não há sessão selecionada.", true); return; }
    if (!(await saveLine())) return;
    const pending = ignoreMissing ? state.pendingBatch : null;
    const scopeMode = pending?.mode || mode;
    const occurrenceIds = pending?.occurrence_ids || [...state.selectedOccurrenceIds];
    const characterId = pending?.character_id || currentStudioCharacter()?.character_id || "";
    let videoSessionIds = pending?.session_ids || [];
    if (scopeMode === "selected" && !occurrenceIds.length) { toast("Marque uma ou mais falas na tabela.", true); return; }
    if (scopeMode === "character" && !characterId) { toast("Selecione um personagem no Take Studio.", true); return; }
    if (scopeMode === "video_character" && !characterId) { toast("Selecione um personagem no Take Studio.", true); return; }
    try {
      if (scopeMode === "video_character" && !videoSessionIds.length) {
        const plan = await refreshDubbingBlockPlan(true);
        videoSessionIds = dubbingBlockChain(plan).map((item) => item.session_id);
        if (!videoSessionIds.length) throw new Error("Não foi possível localizar os blocos capturados deste vídeo.");
      }
      const result = await request("/batch", { method: "POST", body: JSON.stringify({
        session_id: state.selectedSessionId, ignore_missing: ignoreMissing, mode: scopeMode,
        occurrence_ids: scopeMode === "selected" ? occurrenceIds : [],
        character_id: scopeMode === "character" ? characterId : null,
        session_ids: scopeMode === "video_character" ? videoSessionIds : [],
      }) });
      if (result.requires_resolution) {
        state.pendingBatch = { mode: scopeMode, occurrence_ids: occurrenceIds, character_id: characterId, session_ids: videoSessionIds };
        renderMissingBatch(result.missing || []); $("#dubBatchDialog").showModal(); return;
      }
      state.pendingBatch = null;
      $("#dubBatchDialog").close();
      if (scopeMode === "selected") {
        state.selectionBatchId = result.batch_id || "";
        state.selectionBatchOccurrenceIds = new Set(result.occurrence_ids || []);
        state.selectionBatchStatuses.clear();
        renderLines();
      }
      if (scopeMode === "video_character") {
        state.videoBatchId = result.batch_id || "";
        state.videoBatchRootId = state.blockPlanRootId;
      }
      if (!result.eligible && result.already_done) toast(`As falas elegíveis deste personagem já têm takes nos blocos capturados (${result.already_done} concluídas).`);
      else if (!result.eligible) toast("Nenhuma fala elegível encontrada para este escopo.", true);
      else if (scopeMode === "video_character") toast(`${result.queued} fala(s) do vídeo adicionadas à fila. ${result.already_done || 0} já tinham take e ${result.already_active || 0} já estavam na fila.`);
      else toast(`${result.queued} fala(s) adicionada(s) à fila; ${result.missing.length} sem perfil foram ignoradas.`);
      await refreshQueue(false);
      await loadLines(true);
      await loadPlaybackLines();
      void refreshDubbingBlockPlan(true);
    } catch (error) { toast(error.message, true); }
  }

  function renderMissingBatch(items) {
    const list = $("#dubBatchMissingList"); list.replaceChildren();
    for (const item of items) {
      const row = make("div", "dub-missing-character"); row.append(make("b", "", `${item.name} · ${item.count} fala(s)`));
      row.append(make("small", "", item.reasons.join(" · ")));
      const character = state.characters.find((entry) => entry.character_id === item.character_id);
      const configure = make("button", "text-action", character ? "Revisar identidade / voz" : "Sem personagem vinculado"); configure.type = "button"; configure.disabled = !character;
      configure.addEventListener("click", () => { $("#dubBatchDialog").close(); configureVoice(character); }); row.append(configure); list.append(row);
    }
  }

  async function queueAction(action) {
    try {
      const queryParams = new URLSearchParams();
      if (state.videoBatchId) queryParams.set("batch_id", state.videoBatchId);
      else if (state.selectedSessionId) queryParams.set("session_id", state.selectedSessionId);
      const query = queryParams.size ? `?${queryParams}` : "";
      const result = await request(`/queue/${action}${query}`, { method: "POST" });
      if (action === "retry-errors") toast(`${result.retried} erro(s) recolocado(s) na fila.`);
      else if (action === "cancel-pending") toast(`${result.cancelled} job(s) pendente(s) cancelado(s).`);
      await refreshQueue(false);
    } catch (error) { toast(error.message, true); }
  }

  async function providerStart() {
    try { await request("/provider/start", { method: "POST" }); toast("Contêiner OmniVoice iniciado. A primeira preparação baixa o modelo."); await refreshProvider(); }
    catch (error) { toast(error.message, true); }
  }

  function openExport(format) {
    if (!state.selectedSessionId) return;
    const params = new URLSearchParams({ session_id: state.selectedSessionId, format });
    window.location.href = `/api/dubbing/export?${params}`;
  }

  function playbackLineAt(time, previousTime) {
    const active = state.playbackLines.filter((item) => {
      const start = Number(item.source_start_time), end = Number(item.source_end_time);
      return Number.isFinite(start) && Number.isFinite(end) && start <= time && time < end;
    }).sort((a, b) => Number(b.source_start_time) - Number(a.source_start_time));
    if (active.length) return active[0];
    if (Number.isFinite(previousTime) && time >= previousTime) {
      return state.playbackLines.filter((item) => {
        const start = Number(item.source_start_time);
        return Number.isFinite(start) && previousTime < start && start <= time;
      }).sort((a, b) => Number(b.source_start_time) - Number(a.source_start_time))[0] || null;
    }
    return null;
  }

  function waitForAudioMetadata(audio) {
    if (audio.readyState >= HTMLMediaElement.HAVE_METADATA) return Promise.resolve(true);
    return new Promise((resolve) => {
      let settled = false;
      const finish = (ready) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        audio.removeEventListener("loadedmetadata", onReady);
        audio.removeEventListener("error", onError);
        resolve(ready);
      };
      const onReady = () => finish(true);
      const onError = () => finish(false);
      const timer = window.setTimeout(() => finish(false), 5000);
      audio.addEventListener("loadedmetadata", onReady, { once: true });
      audio.addEventListener("error", onError, { once: true });
    });
  }

  async function playTimelineTake(item, token, restart = false) {
    const video = $("#dubPreviewVideo"), audio = $("#dubPreviewAudio");
    if (!item || token !== state.timelinePlaybackToken || video.paused || !$("#dubAutoFollowTimeline").checked) return;
    const takes = item.takes || [];
    const take = takes.find((entry) => entry.status !== "superseded" && entry.audio_master_path)
      || takes.find((entry) => entry.take_id === state.selectedTakeId && entry.audio_master_path);
    if (!take) {
      audio.pause();
      state.autoPlaybackTakeId = "";
      return;
    }
    if (state.selectedTakeId !== take.take_id) await selectTake(take, takes, false);
    if (token !== state.timelinePlaybackToken || video.paused || !$("#dubAutoFollowTimeline").checked) return;

    const sameTake = state.autoPlaybackTakeId === take.take_id;
    if (sameTake && !restart) {
      if (!audio.ended && audio.paused) {
        try { await audio.play(); }
        catch { warnAutoPlaybackBlocked(); }
      }
      return;
    }

    audio.pause();
    const ready = await waitForAudioMetadata(audio);
    if (!ready || token !== state.timelinePlaybackToken || video.paused || !$("#dubAutoFollowTimeline").checked) return;
    const currentTime = video.currentTime;
    const lineStart = Number(item.source_start_time) || 0;
    const lineEnd = Number(item.source_end_time);
    if (Number.isFinite(lineEnd) && currentTime >= lineEnd) return;
    const offset = Math.max(0, currentTime - lineStart);
    if (Number.isFinite(audio.duration) && offset >= audio.duration) return;
    try {
      audio.currentTime = offset;
      state.autoPlaybackTakeId = take.take_id;
      await audio.play();
    } catch { warnAutoPlaybackBlocked(); }
  }

  function warnAutoPlaybackBlocked() {
    if (state.autoPlaybackWarned) return;
    state.autoPlaybackWarned = true;
    toast("O navegador bloqueou a reprodução automática do take. Reproduza o vídeo após interagir com a página.", true);
  }

  async function handleTimelineAutoPlayback({ force = false } = {}) {
    const video = $("#dubPreviewVideo"), audio = $("#dubPreviewAudio");
    if (!$("#dubAutoFollowTimeline").checked || video.paused || video.seeking) return;
    const currentTime = video.currentTime;
    const line = playbackLineAt(currentTime, state.timelinePreviousTime);
    state.timelinePreviousTime = currentTime;
    if (!line) return;

    const occurrenceId = line.occurrence_id;
    if (state.autoPlaybackPendingId === occurrenceId) return;
    const needsSelection = force || state.autoPlaybackOccurrenceId !== occurrenceId
      || state.selectedLine?.occurrence_id !== occurrenceId;
    if (needsSelection) {
      const token = ++state.timelinePlaybackToken;
      state.autoPlaybackOccurrenceId = occurrenceId;
      state.autoPlaybackPendingId = occurrenceId;
      state.autoPlaybackTakeId = "";
      audio.pause();
      const selected = await selectLine(occurrenceId, true, token);
      if (token !== state.timelinePlaybackToken) return;
      if (!selected || state.selectedLine?.occurrence_id !== occurrenceId) {
        if (state.autoPlaybackPendingId === occurrenceId) state.autoPlaybackPendingId = "";
        return;
      }
      await playTimelineTake(state.selectedLine, token, true);
      if (token === state.timelinePlaybackToken && state.autoPlaybackPendingId === occurrenceId) state.autoPlaybackPendingId = "";
      return;
    }
    await playTimelineTake(state.selectedLine, state.timelinePlaybackToken);
  }

  function playSynced() {
    const video = $("#dubPreviewVideo"), audio = $("#dubPreviewAudio");
    const target = Number(video.dataset.targetTime) || 0;
    const start = () => {
      video.currentTime = target;
      if (!$("#dubAutoFollowTimeline").checked) { audio.currentTime = 0; void audio.play(); }
      void video.play();
    };
    if (video.readyState < 1) video.addEventListener("loadedmetadata", start, { once: true });
    else start();
  }

  function bind() {
    const roiAudioPlayer = $("#dubRoiAudioPlayer");
    roiAudioPlayer.addEventListener("error", () => {
      const audioKey = roiAudioPlayer.dataset.audioKey;
      if (!audioKey) return;
      roiAudioPlayer.dataset.loadError = audioKey;
      const note = $("#dubRoiAudioSourceNote");
      if (note) {
        note.textContent = "Não foi possível carregar o áudio. Confira se o arquivo ainda está anexado ao personagem na Captura.";
        note.classList.add("is-error");
      }
    });
    roiAudioPlayer.addEventListener("loadedmetadata", () => {
      if (!Number.isFinite(roiAudioPlayer.duration) || roiAudioPlayer.duration <= 0) return;
      roiAudioPlayer.dataset.loadError = "";
      $("#dubRoiAudioSourceNote")?.classList.remove("is-error");
    });
    const autoFollow = $("#dubAutoFollowTimeline");
    try { autoFollow.checked = localStorage.getItem("dubsync.dub.auto-follow-timeline.v1") !== "false"; }
    catch { autoFollow.checked = true; }
    autoFollow.addEventListener("change", () => {
      try { localStorage.setItem("dubsync.dub.auto-follow-timeline.v1", String(autoFollow.checked)); } catch { /* preference is optional */ }
      state.timelinePlaybackToken += 1;
      state.autoPlaybackPendingId = "";
      state.autoPlaybackOccurrenceId = "";
      state.autoPlaybackTakeId = "";
      state.timelinePreviousTime = $("#dubPreviewVideo").currentTime;
      if (autoFollow.checked) {
        state.autoPlaybackWarned = false;
        void handleTimelineAutoPlayback({ force: true });
      } else $("#dubPreviewAudio").pause();
    });
    $("#dubRefresh").addEventListener("click", () => void loadAll());
    $("#dubSessionFilter").addEventListener("change", async (event) => { state.selectedSessionId = event.target.value; updateDeleteBlockButton(); state.timelineSelectedOccurrenceIds.clear(); resetBatchSelection(); await loadAll(false); });
    $("#dubDeleteBlock").addEventListener("click", () => void deleteSelectedBlock());
    $("#dubCharacterFilter").addEventListener("change", () => void loadLines(false));
    $("#dubStatusFilter").addEventListener("change", () => void loadLines(false));
    let searchTimer = 0;
    $("#dubLineSearch").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => void loadLines(true), 180); });
    $("#dubCharacterSearch").addEventListener("input", renderCharacters);
    $("#dubClearFilters").addEventListener("click", () => { $("#dubCharacterFilter").value = ""; $("#dubStatusFilter").value = "not-discarded"; $("#dubLineSearch").value = ""; void loadLines(false); });
    $("#dubNewCharacter").addEventListener("click", () => void openCharacterRoi());
    $("#dubCharacterOpenRoi").addEventListener("click", async () => {
      const character = actionCharacter();
      $("#dubCharacterActionsDialog").close();
      await openCharacterRoi(character);
    });
    $("#dubCharacterConfirmIdentity").addEventListener("click", () => void confirmCharacterIdentity());
    $("#dubCharacterProfiles").addEventListener("click", () => openCharacterVoiceProfile());
    $("#dubConfigureVoice").addEventListener("click", () => startNewVoiceProfile());
    $("#dubNewVoice").addEventListener("click", () => startNewVoiceProfile());
    $("#dubEditVoice").addEventListener("click", () => $("#dubProfileName").focus());
    $("#dubToggleVoice").addEventListener("click", () => void changeProfileActive());
    $("#dubDeleteVoice").addEventListener("click", () => void removeVoiceProfile());
    $("#dubSaveProfile").addEventListener("click", () => void saveInlineVoiceProfile());
    $("#dubRemoveReference").addEventListener("click", () => void removeVoiceReference());
    $("#dubReplaceRoiAudio").addEventListener("click", () => $("#dubReplaceRoiAudioFile").click());
    $("#dubReplaceRoiAudioFile").addEventListener("change", (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (file) void replaceRoiAudioReference(file);
    });
    $("#dubUseRoiReference").addEventListener("click", () => void useRoiReference());
    $("#dubTestProfile").addEventListener("click", () => void testVoiceProfile());
    document.querySelectorAll("[data-dub-speed]").forEach((button) => button.addEventListener("click", () => {
      $("#dubSpeed").value = button.dataset.dubSpeed;
      $("#dubDuration").value = "";
      markProfileDraftDirty();
    }));
    $("#dubGenerateCandidates").addEventListener("click", () => void generateDesignCandidates());
    $("#dubDesignAddOption").addEventListener("click", () => {
      const select = addDesignPromptField();
      state.profilePresetKey = ""; updateProfileModeControls(); select.focus();
      markProfileDraftDirty();
    });
    $("#dubLocalProfileMode").addEventListener("change", updateProfileModeControls);
    document.querySelectorAll('input[name="dubVoiceSource"]').forEach((radio) => radio.addEventListener("change", () => {
      if (!radio.checked) return;
      if (radio.value === "clone") state.referenceRemoveRequested = false;
      clearDesignCandidates();
      setVoiceMode(radio.value);
      if (radio.value === "design") state.profilePresetKey = "";
      updateProfileModeControls();
    }));
    $("#dubProfileAuthorized").addEventListener("change", updateProfileModeControls);
    for (const selector of ["#dubDesignGender", "#dubDesignAge", "#dubDesignPitch", "#dubDesignStyle"]) {
      $(selector).addEventListener("change", () => {
        clearDesignCandidates();
        state.profilePresetKey = "";
        updateProfileModeControls();
      });
    }
    $("#dubClearVoiceModel").addEventListener("click", () => {
      state.profilePresetKey = "";
      applyVoiceDesignInstruction("");
      updateProfileModeControls();
      markProfileDraftDirty();
      toast("Modelo desmarcado. Os atributos podem ser escolhidos livremente.");
    });
    $("#dubBrowseModels").addEventListener("click", () => $("#dubVoiceCatalogDialog").showModal());
    document.querySelectorAll(".dub-select-model").forEach((button) => button.addEventListener("click", () => {
      const card = button.closest(".dub-voice-model-card"), name = card?.dataset.voiceModel || "";
      if (state.profilePresetKey === name) {
        state.profilePresetKey = "";
        applyVoiceDesignInstruction("");
        toast("Modelo desmarcado; atributos completos reativados.");
      } else {
        state.profilePresetKey = name;
        setVoiceMode("design");
        $("#dubProfileName").value = name;
        applyVoiceDesignInstruction(card?.dataset.instruct || "");
        if (card?.dataset.denoise) $("#dubDenoise").checked = card.dataset.denoise === "true";
        toast(`Atributos compatíveis aplicados para “${name}”. O áudio do site não foi baixado.`);
      }
      updateProfileModeControls();
      markProfileDraftDirty();
      $("#dubVoiceCatalogDialog").close();
      $("#dubProfileName").focus();
    }));
    $("#dubProfileName").addEventListener("input", () => {
      const owner = state.characters.find((item) => item.character_id === $("#dubVoiceCharacterId").value);
      updateProfileSaveState();
    });
    $("#dubReferenceFile").addEventListener("change", (event) => {
      const file = event.target.files[0], audio = $("#dubReferencePreview");
      if (state.referenceUploadUrl) URL.revokeObjectURL(state.referenceUploadUrl);
      state.referenceUploadUrl = file ? URL.createObjectURL(file) : ""; audio.src = state.referenceUploadUrl; audio.classList.toggle("hidden", !file);
      if (file) {
        state.referenceRemoveRequested = false;
        setVoiceMode("clone");
        state.profilePresetKey = "";
        updateProfileModeControls();
      }
      $("#dubRemoveReference").disabled = !file && !findProfile(state.editingProfileId)?.reference;
      const note = $(".dub-clone-copy small");
      if (note && file) note.textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB · será normalizado localmente`;
      if (file) $("#dubProfileReferenceStatus").textContent = "Arquivo de referência selecionado localmente; será anexado ao salvar o perfil.";
    });
    $("#dubSaveVoiceProfile").addEventListener("click", () => void saveVoiceProfile());
    $("#dubVoiceMode").addEventListener("change", () => { const clone = $("#dubVoiceMode").value === "clone"; $("#dubVoiceReferenceFields").classList.toggle("hidden", !clone); $("#dubVoiceInstructWrap").classList.toggle("hidden", clone); });
    $("#dubExtractReference").addEventListener("click", () => void extractReference());
    $("#dubStudioCharacter").addEventListener("change", () => { void saveStudioCharacterAssignment(); void refreshDubbingBlockPlan(true); });
    $("#dubVoiceSelect").addEventListener("change", (event) => {
      if (profileDraftDirty && event.target.value !== state.editingProfileId && !confirmDiscardProfileDraft()) {
        event.target.value = state.editingProfileId;
        return;
      }
      const character = currentStudioCharacter();
      const profile = findProfile($("#dubVoiceSelect").value);
      renderProfileEditor(profile, character);
      updateGenerateEnabled();
    });
    $("#dubStudioPanel").addEventListener("input", (event) => {
      if (!event.target.matches("input, select, textarea") || event.target.id === "dubVoiceSelect") return;
      if (event.target.closest(".dub-studio-voice, .dub-studio-direction")) markProfileDraftDirty();
    });
    $("#dubStudioPanel").addEventListener("change", (event) => {
      if (!event.target.matches("input, select, textarea") || event.target.id === "dubVoiceSelect") return;
      if (event.target.closest(".dub-studio-voice, .dub-studio-direction")) markProfileDraftDirty();
    });
    $("#dubManualOverride").addEventListener("change", updateGenerateEnabled);
    $("#dubText").addEventListener("input", () => { void scheduleLineAutosave(); });
    $("#dubText").addEventListener("change", () => { void scheduleLineAutosave(0); });
    $("#dubEmotion").addEventListener("change", () => { void scheduleLineAutosave(0); });
    $("#dubIntensity").addEventListener("input", (event) => {
      setText("#dubIntensityValue", `${Math.round(Number(event.target.value) * 100)}%`);
      void scheduleLineAutosave();
    });
    $("#dubIntensity").addEventListener("change", () => { void scheduleLineAutosave(0); });
    $("#dubGenerateTake").addEventListener("click", () => void generate());
    $("#dubSuggestTakeSpeed").addEventListener("click", suggestSingleTakeSpeed);
    $("#dubSingleTakeSpeed").addEventListener("input", () => {
      const value = $("#dubSingleTakeSpeed").value.trim();
      if (value) $("#dubSingleTakeSpeedHint").textContent = `Este take será gerado em ${Number(value).toFixed(2)}×. A velocidade do perfil não será alterada.`;
    });
    $("#dubPresetSelect").addEventListener("change", (event) => { const option = event.target.selectedOptions[0]; if (option?.value) $("#dubInstruction").value = option.dataset.instruct || ""; });
    $("#dubSavePreset").addEventListener("click", async () => {
      const name = prompt("Nome do preset de interpretação:"); if (!name) return;
      try {
        await request("/presets", { method: "POST", body: JSON.stringify({ name, emotion: $("#dubEmotion").value, intensity: Number($("#dubIntensity").value), instruct: $("#dubInstruction").value.trim() || "Interpretação natural e clara." }) });
        const result = await request("/presets");
        if (state.overview) state.overview.presets = result.items || [];
        renderPresets(result.items || []);
        toast("Preset salvo.");
      } catch (error) { toast(error.message, true); }
    });
    $("#dubSelectAll").addEventListener("change", (event) => {
      for (const item of state.lines) {
        if (event.target.checked) { state.selectedOccurrenceIds.add(item.occurrence_id); state.batchGeneratedOccurrenceIds.delete(item.occurrence_id); }
        else state.selectedOccurrenceIds.delete(item.occurrence_id);
      }
      renderLines();
    });
    $("#dubGenerateSelected").addEventListener("click", () => void startBatch("selected"));
    $("#dubGenerateCharacter").addEventListener("click", () => void startBatch("character"));
    $("#dubGenerateVideoCharacter").addEventListener("click", () => void startBatch("video_character"));
    $("#dubGenerateBlock").addEventListener("click", () => void startBatch("block"));
    $("#dubGeneratePending").addEventListener("click", () => void startBatch("pending"));
    $("#dubBatchIgnoreMissing").addEventListener("click", () => void startBatch(state.pendingBatch?.mode || "block", true));
    $("#dubPauseQueue").addEventListener("click", async () => { const action = state.queue?.paused ? "resume" : "pause"; try { await request(`/queue/${action}`, { method: "POST" }); await refreshQueue(false); } catch (error) { toast(error.message, true); } });
    $("#dubRetryErrors").addEventListener("click", () => void queueAction("retry-errors"));
    $("#dubCancelQueue").addEventListener("click", async () => { if (confirm(state.videoBatchId ? "Cancelar as falas ainda pendentes deste lote em todos os blocos? A fala que já começou continuará." : "Cancelar todos os jobs ainda pendentes desta sessão? A geração que já começou continuará.")) await queueAction("cancel-pending"); });
    $("#dubProviderButton").addEventListener("click", () => void providerStart());
    $("#dubExportJson").addEventListener("click", () => openExport("json"));
    $("#dubExportCsv").addEventListener("click", () => openExport("csv"));
    $("#dubPlaySynced").addEventListener("click", playSynced);
    $("#dubAudioOnly").addEventListener("click", () => { const audio = $("#dubPreviewAudio"); audio.currentTime = 0; void audio.play().catch((error) => toast(`Não foi possível reproduzir o take: ${error.message}`, true)); });
    $("#dubPreviewAudio").addEventListener("error", () => toast("Não foi possível carregar o áudio deste take. Atualize a página e tente novamente.", true));
    const previewVideo = $("#dubPreviewVideo");
    previewVideo.addEventListener("timeupdate", (event) => {
      const video = event.currentTarget;
      setText("#dubPreviewClock", `${fmtTime(video.currentTime)} / ${fmtTime(video.duration)}`);
      updateDubbingBlockCursor();
      updateDubTimelineCursor();
      void handleTimelineAutoPlayback();
    });
    previewVideo.addEventListener("play", () => {
      state.timelinePreviousTime = previewVideo.currentTime - 0.001;
      void handleTimelineAutoPlayback();
    });
    previewVideo.addEventListener("pause", () => {
      if (autoFollow.checked) $("#dubPreviewAudio").pause();
    });
    previewVideo.addEventListener("seeking", () => {
      state.timelinePlaybackToken += 1;
      state.autoPlaybackPendingId = "";
      state.autoPlaybackOccurrenceId = "";
      state.autoPlaybackTakeId = "";
      $("#dubPreviewAudio").pause();
    });
    previewVideo.addEventListener("seeked", () => {
      state.timelinePreviousTime = previewVideo.currentTime - 0.001;
      if (autoFollow.checked && !previewVideo.paused) void handleTimelineAutoPlayback({ force: true });
    });
    $("#dubPreviewAudio").addEventListener("ended", () => {
      if (!autoFollow.checked) previewVideo.pause();
    });
    document.querySelectorAll("[data-close-dub-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog")?.close()));
  }

  async function pollDubbing(generation = dubbingPollGeneration) {
    if (generation !== dubbingPollGeneration || $("#dubbingView").classList.contains("hidden")) return;
    const counts = state.queue?.counts || {};
    const queueActive = Number(counts.queued || 0) + Number(counts.generating || 0) > 0;
    const checks = [refreshProvider()];
    if (queueActive) checks.push(refreshQueue(false));
    await Promise.allSettled(checks);
    if (generation !== dubbingPollGeneration || $("#dubbingView").classList.contains("hidden")) return;
    const providerStarting = !state.provider?.api_online || state.provider?.build_running;
    state.poll = setTimeout(() => void pollDubbing(generation), queueActive || providerStarting ? 6000 : 30000);
  }

  function open(context = null) {
    // Refresh the canonical ROI roster whenever the user enters Dubbing.
    if (context?.sessionId) prepareNavigation(context);
    if (pendingSharedNavigation?.sessionId) state.selectedSessionId = pendingSharedNavigation.sessionId;
    void loadAll(state.loaded);
    clearTimeout(state.poll);
    const generation = ++dubbingPollGeneration;
    state.poll = setTimeout(() => void pollDubbing(generation), 6000);
  }

  bind();
  initializeDubbingRuntimeVolume();
  initializeLineSortControls();
  initializeLineColumnResizers();
  initializeDubTimelineInteractions();
  window.addEventListener("dubsync:character-colors-updated", (event) => {
    const byId = new Map((event.detail?.characters || []).map((item) => [String(item.character_id), item.color]));
    state.characters = state.characters.map((character) => ({
      ...character,
      color: byId.get(String(character.legacy_character_profile_id || "")) || character.color || "",
    }));
    if (state.loaded) {
      renderCharacters();
      const selected = state.characters.find((character) => character.character_id === state.selectedCharacterId);
      if (selected) renderCharacterDetail(selected);
      applyStudioCharacterColor(state.characters.find((character) => character.character_id === state.selectedLine?.character_id));
      renderLines();
    }
  });
  window.DubSyncDubbing = { open, prepareNavigation, getNavigationState, generate, handleTimelineShortcut: handleDubTimelineShortcut, refreshTimelineData: () => loadAll(true) };
})();
