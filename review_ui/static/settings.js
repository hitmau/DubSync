let settingsState = null;
let calibrationDraft = null;
let navBeforeSettings = null;

function settingsFormValue() {
  return {
    detection: {
      analysis_fps: Number($("#cfgAnalysisFps").value),
      periodic_ocr_seconds: Number($("#cfgPeriodicOcr").value),
      change_threshold: Number($("#cfgChangeThreshold").value),
      changed_pixel_fraction: Number($("#cfgChangedPixels").value) / 100,
      stable_observations: Number($("#cfgStable").value),
      absence_observations: Number($("#cfgAbsence").value),
    },
    preprocess: {
      mode: $("#cfgPreMode").value,
      contrast: Number($("#cfgContrast").value),
      brightness: Number($("#cfgBrightness").value),
      clahe: $("#cfgClahe").checked,
      threshold: $("#cfgThreshold").value,
      threshold_value: Number($("#cfgThresholdValue").value),
      morphology: Number($("#cfgMorphology").value),
      fallback_confidence: Number($("#cfgFallbackConfidence").value),
      smart_upscale: $("#cfgUpscale").checked,
      smart_sharpen: $("#cfgSharpen").checked,
    },
    ocr: {
      min_confidence: Number($("#cfgMinConfidence").value),
      detection_side_limit: Number($("#cfgDetectionSide").value),
      strip_trailing_hud_artifacts: $("#cfgStripHudSuffix").checked,
    },
    calibration: {
      model: $("#cfgOpenAiModel").value.trim(),
      duration_seconds: Number($("#cfgSampleDuration").value),
      sample_count: Number($("#cfgSampleCount").value),
    },
    review: {
      show_split_preview: $("#cfgSplitPreview").checked,
      permanent_discard: $("#cfgPermanentDiscard").checked,
      confirm_dialogue_removal: $("#cfgConfirmDialogueRemoval").checked,
    },
    dubbing: {
      base_url: $("#cfgDubbingBaseUrl").value,
      timeout_seconds: Number($("#cfgDubbingTimeout").value),
      auto_start_docker: $("#cfgDubbingAutoStart").checked,
    },
    appearance: {
      border_width: Number($("#cfgBorderWidth").value),
      border_color: $("#cfgBorderColor").value,
    },
  };
}

function applyAppearanceSettings(appearance = {}) {
  const width = Math.max(.5, Math.min(4, Number(appearance.border_width) || 1));
  const color = /^#[0-9a-f]{6}$/i.test(appearance.border_color || "") ? appearance.border_color : "#26364d";
  document.documentElement.style.setProperty("--ui-border-width", `${width}px`);
  document.documentElement.style.setProperty("--ui-border-color", color);
}

function fillSettingsForm(data) {
  const value = data || {};
  const detection = value.detection || {}, preprocess = value.preprocess || {};
  const ocr = value.ocr || {}, calibration = value.calibration || {}, review = value.review || {}, dubbing = value.dubbing || {};
  const appearance = value.appearance || {};
  $("#cfgAnalysisFps").value = detection.analysis_fps ?? 8;
  $("#cfgPeriodicOcr").value = detection.periodic_ocr_seconds ?? 1.25;
  $("#cfgChangeThreshold").value = detection.change_threshold ?? 2.4;
  $("#cfgChangedPixels").value = ((detection.changed_pixel_fraction ?? .006) * 100).toFixed(2);
  $("#cfgStable").value = detection.stable_observations ?? 3;
  $("#cfgAbsence").value = detection.absence_observations ?? 2;
  $("#cfgPreMode").value = preprocess.mode ?? "balanced";
  $("#cfgContrast").value = preprocess.contrast ?? 1.15;
  $("#cfgBrightness").value = preprocess.brightness ?? 0;
  $("#cfgClahe").checked = preprocess.clahe ?? true;
  $("#cfgThreshold").value = preprocess.threshold ?? "otsu";
  $("#cfgThresholdValue").value = preprocess.threshold_value ?? 180;
  $("#cfgMorphology").value = preprocess.morphology ?? 1;
  $("#cfgFallbackConfidence").value = preprocess.fallback_confidence ?? .78;
  $("#cfgUpscale").checked = preprocess.smart_upscale ?? true;
  $("#cfgSharpen").checked = preprocess.smart_sharpen ?? false;
  $("#cfgMinConfidence").value = ocr.min_confidence ?? .30;
  $("#cfgDetectionSide").value = ocr.detection_side_limit ?? 1280;
  $("#cfgStripHudSuffix").checked = ocr.strip_trailing_hud_artifacts ?? false;
  $("#cfgOpenAiModel").value = calibration.model ?? "gpt-5.4-mini";
  $("#cfgSampleDuration").value = calibration.duration_seconds ?? 10;
  $("#cfgSampleCount").value = calibration.sample_count ?? 6;
  $("#cfgSplitPreview").checked = review.show_split_preview ?? true;
  $("#cfgPermanentDiscard").checked = review.permanent_discard ?? false;
  $("#cfgConfirmDialogueRemoval").checked = review.confirm_dialogue_removal ?? true;
  window.setPermanentDiscardEnabled?.(review.permanent_discard ?? false, review.confirm_dialogue_removal ?? true);
  $("#cfgDubbingBaseUrl").value = dubbing.base_url ?? "http://127.0.0.1:8015";
  $("#cfgDubbingTimeout").value = dubbing.timeout_seconds ?? 600;
  $("#cfgDubbingAutoStart").checked = dubbing.auto_start_docker ?? false;
  $("#cfgBorderWidth").value = appearance.border_width ?? 1;
  $("#cfgBorderColor").value = appearance.border_color ?? "#26364d";
  applyAppearanceSettings(appearance);
  showSplitPreview = $("#cfgSplitPreview").checked;
  const openai = value.openai || {};
  $("#openaiKeyStatus").textContent = openai.configured
    ? `Chave disponível no arquivo local ${openai.env_file || ".env.local"}; valor oculto da interface.`
    : `Chave não encontrada. Defina OPENAI_API_KEY em ${openai.env_file || ".env.local"}.`;
  $("#openaiKeyStatus").classList.toggle("good", Boolean(openai.configured));
  $("#runCalibration").disabled = !openai.configured || !$("#calibrationConsent").checked;
}

async function openSettingsDialog() {
  const dialog = $("#settingsDialog");
  navBeforeSettings = document.querySelector(".nav-item.active");
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.hasAttribute("data-settings-open")));
  if (!dialog.open) dialog.showModal();
  $("#settingsStatus").textContent = "Carregando configurações locais…";
  try {
    settingsState = await api("/api/settings");
    fillSettingsForm(settingsState);
    renderShortcutSettings();
    $("#settingsStatus").textContent = "Configurações lidas do armazenamento local.";
  } catch (error) {
    $("#settingsStatus").textContent = `Não foi possível carregar as configurações: ${error.message}`;
    toast(`Não foi possível carregar as configurações: ${error.message}`, true);
  }
}

async function persistSettings() {
  const result = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ settings: settingsFormValue() }),
  });
  settingsState = result;
  fillSettingsForm(result);
  $("#settingsStatus").textContent = "Salvo localmente. A aparência dos cards foi aplicada em todas as telas.";
  return result;
}

function renderCalibrationResult(result) {
  const box = $("#calibrationResults");
  box.replaceChildren();
  const summary = document.createElement("div");
  const current = result.similarity?.current;
  const suggested = result.similarity?.suggested;
  summary.innerHTML = "";
  const headline = document.createElement("b");
  headline.textContent = `${result.sample_count} frames · ${result.duration_seconds}s · ${result.model}`;
  box.append(headline);
  if (current != null || suggested != null) {
    summary.textContent = `Concordância com a leitura visual da IA — atual: ${current ?? "—"}% · sugestão: ${suggested ?? "—"}%. É uma comparação de calibração, não uma medição de CER validada.`;
    box.append(summary);
  }
  if (result.suggested_region) {
    const roi = result.suggested_region;
    const region = document.createElement("div");
    region.textContent = `ROI sugerida: x ${Number(roi.x).toFixed(3)}, y ${Number(roi.y).toFixed(3)}, largura ${Number(roi.width).toFixed(3)}, altura ${Number(roi.height).toFixed(3)}.`;
    box.append(region);
  }
  if (result.notes) {
    const notes = document.createElement("p");
    notes.textContent = result.notes;
    box.append(notes);
  }
  const list = document.createElement("ul");
  for (const sample of result.samples || []) {
    const row = document.createElement("li");
    row.textContent = `${timecode(sample.source_time, true)} · ${sample.has_dialogue ? "diálogo provável" : "sem diálogo"}${sample.text ? ` · ${sample.text}` : ""}`;
    list.append(row);
  }
  box.append(list);
}

async function runOpenAiCalibration() {
  if (!$("#calibrationConsent").checked) return toast("Marque o consentimento para enviar as imagens desta amostra.", true);
  const sessionId = $("#sessionFilter").value || activeSessionId;
  const session = sessions.find((item) => item.session_id === sessionId) || currentSession;
  if (!session) return toast("Carregue uma sessão de vídeo antes da calibração.", true);
  const player = $("#videoPlayer");
  const length = Number($("#cfgSampleDuration").value) || 10;
  const rangeEnd = Number(session.source_start) + Number(session.requested_duration);
  const fromPlayer = !player.classList.contains("hidden") && Number.isFinite(player.currentTime)
    ? player.currentTime : Number(session.source_start);
  const startTime = Math.max(Number(session.source_start), Math.min(fromPlayer, rangeEnd - length));
  const button = $("#runCalibration"), resultBox = $("#calibrationResults");
  button.disabled = true;
  $("#applyCalibration").disabled = true;
  resultBox.textContent = "Salvando configurações e amostrando quadros visuais; a chamada à OpenAI pode levar alguns instantes…";
  try {
    await persistSettings();
    calibrationDraft = await api("/api/settings/calibrate", {
      method: "POST",
      body: JSON.stringify({
        session_id: session.session_id,
        consent_to_send_frames: true,
        start_time: startTime,
        duration_seconds: length,
        sample_count: Number($("#cfgSampleCount").value) || 6,
      }),
    });
    renderCalibrationResult(calibrationDraft);
    $("#applyCalibration").disabled = false;
    $("#settingsStatus").textContent = "Sugestão pronta; nada foi aplicado automaticamente.";
  } catch (error) {
    resultBox.textContent = `Falha na calibração: ${error.message}`;
  } finally {
    button.disabled = !settingsState?.openai?.configured || !$("#calibrationConsent").checked;
  }
}

async function applyCalibrationDraft() {
  if (!calibrationDraft) return;
  const sessionId = $("#sessionFilter").value || activeSessionId;
  const button = $("#applyCalibration");
  button.disabled = true;
  try {
    const result = await api("/api/settings/calibration/apply", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        settings: calibrationDraft.suggested_settings,
        region: calibrationDraft.suggested_region,
      }),
    });
    settingsState = result.settings;
    fillSettingsForm(settingsState);
    if (Array.isArray(result.regions) && result.regions.length) {
      regions = result.regions.map((region, index) => ({
        ...region,
        color: region.color || ["#e43c46", "#24a4ee", "#dd9d2e", "#b26be5"][index % 4],
        enabled: region.enabled !== false,
      }));
      updateRoiList();
      if (preview) drawPreview();
    }
    $("#settingsStatus").textContent = "Sugestão salva. A ROI e os parâmetros já aparecem nas configurações locais.";
    toast("Configuração de OCR e ROI atualizadas para a próxima análise.");
  } catch (error) {
    toast(`Não foi possível aplicar a sugestão: ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

async function refreshOpenAiStatus() {
  try {
    settingsState = await api("/api/settings");
    fillSettingsForm(settingsState);
  } catch (error) {
    $("#openaiKeyStatus").textContent = `Não foi possível ler a configuração local: ${error.message}`;
  }
}

async function updateMarkerButton() {
  const sessionId = $("#sessionFilter").value || activeSessionId;
  const player = $("#videoPlayer");
  $("#addAudioMarker").disabled = !sessionId || player.classList.contains("hidden") || !Number.isFinite(player.currentTime);
}

async function addAudioStartMarker() {
  const sessionId = $("#sessionFilter").value || activeSessionId;
  const player = $("#videoPlayer");
  if (!sessionId || player.classList.contains("hidden") || !Number.isFinite(player.currentTime)) {
    return toast("Abra uma sessão e posicione o player antes de criar o ponto.", true);
  }
  try {
    const result = await api(`/api/sessions/${sessionId}/markers`, {
      method: "POST",
      body: JSON.stringify({ source_time: Number(player.currentTime.toFixed(3)), kind: "audio_start", label: "Início de áudio (futuro)" }),
    });
    timelineMarkers = [...(timelineMarkers || []), result.marker];
    renderTimeline();
    toast(`Ponto salvo em ${timecode(result.marker.source_time, true)}; áudio não foi lido.`);
  } catch (error) {
    toast(`Não foi possível salvar o ponto: ${error.message}`, true);
  }
}

async function deleteAudioStartMarker(marker) {
  if (!confirm(`Excluir o ponto ${timecode(marker.source_time, true)}?`)) return;
  try {
    await api(`/api/markers/${marker.marker_id}`, { method: "DELETE" });
    timelineMarkers = timelineMarkers.filter((item) => item.marker_id !== marker.marker_id);
    renderTimeline();
  } catch (error) {
    toast(error.message, true);
  }
}

$("[data-settings-open]").addEventListener("click", openSettingsDialog);
$("#closeSettings").addEventListener("click", () => $("#settingsDialog").close());
$("#cancelSettings").addEventListener("click", () => $("#settingsDialog").close());
$("#settingsDialog").addEventListener("close", () => {
  $("[data-settings-open]").classList.remove("active");
  if (navBeforeSettings?.isConnected) navBeforeSettings.classList.add("active");
  navBeforeSettings = null;
  if (window.DUBSYNC_PROJECT_ID && window.DUBSYNC_SETTINGS_RETURN_URL) {
    history.replaceState(null, "", window.DUBSYNC_SETTINGS_RETURN_URL);
  }
});
$("#saveSettings").addEventListener("click", async () => {
  try {
    await persistSettings();
    toast("Configurações salvas. As opções de revisão já estão ativas.");
  } catch (error) {
    $("#settingsStatus").textContent = `Falha ao salvar: ${error.message}`;
  }
});
$("#testOpenAi").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "Testando…";
  try {
    await persistSettings();
    const result = await api("/api/settings/openai/test", { method: "POST", body: "{}" });
    $("#settingsStatus").textContent = result.selected_model_available
      ? `Conexão OpenAI válida · ${result.selected_model} disponível para esta chave.`
      : `Conexão válida, mas o modelo ${result.selected_model} não aparece nesta chave. Escolha um modelo disponível.`;
  } catch (error) {
    $("#settingsStatus").textContent = `Teste da API falhou: ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "Testar conexão";
  }
});
$("#runCalibration").addEventListener("click", runOpenAiCalibration);
$("#applyCalibration").addEventListener("click", applyCalibrationDraft);
$("#calibrationConsent").addEventListener("change", () => {
  $("#runCalibration").disabled = !settingsState?.openai?.configured || !$("#calibrationConsent").checked;
});
$("#addAudioMarker").addEventListener("click", addAudioStartMarker);
$("#sessionFilter").addEventListener("change", updateMarkerButton);
$("#videoPlayer").addEventListener("timeupdate", updateMarkerButton);
$("#videoPlayer").addEventListener("loadedmetadata", updateMarkerButton);
$("#videoPlayer").addEventListener("play", updateMarkerButton);
for (const tab of $$(".settings-tab")) {
  tab.addEventListener("click", () => {
    $$(".settings-tab").forEach((item) => item.classList.toggle("active", item === tab));
    $$(".settings-page").forEach((page) => page.classList.toggle("active", page.dataset.settingsPage === tab.dataset.settingsTab));
  });
}
$("#settingsDialog").addEventListener("click", (event) => {
  if (event.target === $("#settingsDialog")) $("#settingsDialog").close();
});
refreshOpenAiStatus();
updateMarkerButton();
