/* Configuração visual de cada ROI; os valores são persistidos no config_json da próxima sessão. */
(() => {
  const el = (id) => document.getElementById(id);
  const dialog = el("roiSettingsDialog");
  let index = -1;
  let profilesAvailable = false;
  let profilesLoading = false;
  const percent = (value) => (Number(value) * 100).toFixed(1);
  const number = (value) => Number(value);
  const validColor = (value) => /^#[0-9a-f]{6}$/i.test(String(value || ""));
  const colorPalette = ["#E8596E", "#F08C46", "#E8B93B", "#78C85A", "#27B985", "#29B9AE", "#32A9D4", "#4B8EE8", "#706BE8", "#9B68E8", "#C65BCB", "#E05BA4", "#FF725E", "#D3A12C", "#62BD80", "#55AFD0", "#788CE8", "#AC78D2", "#DE7692", "#99B440", "#26A5A1", "#E5842B", "#5875BD", "#BB5F67"];
  const nextColor = (used) => {
    const available = colorPalette.filter((color) => !used.has(color.toUpperCase()));
    if (available.length) return available[Math.floor(Math.random() * available.length)];
    let color;
    do { color = `#${Math.floor(Math.random() * 0x1000000).toString(16).padStart(6, "0").toUpperCase()}`; }
    while (used.has(color));
    return color;
  };
  const option = (value, label) => `<option value="${value}">${label}</option>`;
  function renderCharacters(items = []) {
    const list = el("roiCharacters");
    list.querySelectorAll(".roi-character-avatar img").forEach((image) => { if (image._objectUrl) URL.revokeObjectURL(image._objectUrl); });
    list.replaceChildren();
    const usedColors = new Set(items.filter((item) => validColor(item.color)).map((item) => item.color.toUpperCase()));
    for (const item of items) {
      const autoColor = item.autoColor ?? !validColor(item.color);
      if (!validColor(item.color)) { item.color = nextColor(usedColors); usedColors.add(item.color.toUpperCase()); }
      const card = document.createElement("div"); card.className = "roi-character"; card.dataset.characterId = item.character_id || crypto.randomUUID();
      card._autoColor = autoColor;
      const top = document.createElement("div"); top.className = "roi-character-top";
      const photoButton = document.createElement("button"); photoButton.type = "button"; photoButton.className = "roi-character-avatar";
      photoButton.title = "Anexar foto para " + (item.alias || item.name || "este personagem"); photoButton.setAttribute("aria-label", photoButton.title);
      const photo = document.createElement("img"); photo.alt = ""; photo.hidden = true;
      const photoFallback = document.createElement("span"); photoFallback.className = "roi-character-avatar-fallback";
      const setFallback = (value) => { photoFallback.textContent = value.trim().slice(0, 1).toLocaleUpperCase("pt-BR") || "＋"; };
      setFallback(item.alias || item.name || "");
      if (item.photo_url) { photo.src = item.photo_url; photo.hidden = false; photoFallback.hidden = true; }
      photo.addEventListener("error", () => { photo.hidden = true; photoFallback.hidden = false; });
      photoButton.append(photo, photoFallback);
      photoButton.style.setProperty("--character-color", item.color);
      const name = document.createElement("input"); name.className = "roi-character-name"; name.value = item.name || ""; name.placeholder = "Nome identificado pelo app"; name.maxLength = 120; name.setAttribute("aria-label", "Nome identificado pelo app");
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "roi-remove-row"; remove.textContent = "×"; remove.title = "Remover personagem desta ROI"; remove.addEventListener("click", () => card.remove());
      const aliasField = document.createElement("label"); aliasField.className = "roi-character-alias-field";
      const aliasCaption = document.createElement("span"); aliasCaption.textContent = "Alias interno (opcional)";
      const alias = document.createElement("input"); alias.className = "roi-character-alias"; alias.value = item.alias || ""; alias.placeholder = "Nome usado no DubSync"; alias.maxLength = 120; alias.setAttribute("aria-label", "Alias interno usado em todo o DubSync");
      const aliasHelp = document.createElement("small"); aliasHelp.textContent = "Substitui o nome identificado nas telas e nos vínculos internos.";
      aliasField.append(aliasCaption, alias, aliasHelp);
      const colorField = document.createElement("label"); colorField.className = "roi-character-color-field";
      const colorCaption = document.createElement("span"); colorCaption.textContent = "Cor do personagem";
      const characterColor = document.createElement("input"); characterColor.type = "color"; characterColor.className = "roi-character-color-input";
      characterColor.value = item.color; characterColor.setAttribute("aria-label", `Cor usada para ${item.alias || item.name || "este personagem"} em todo o DubSync`);
      const applyCharacterColor = () => { card.style.setProperty("--character-color", characterColor.value); photoButton.style.setProperty("--character-color", characterColor.value); };
      characterColor.addEventListener("input", () => { card._autoColor = false; applyCharacterColor(); }); colorField.append(colorCaption, characterColor);
      const updateAvatarName = () => { const shownName = alias.value.trim() || name.value.trim(); photoButton.title = "Anexar foto para " + (shownName || "este personagem"); photoButton.setAttribute("aria-label", photoButton.title); if (!card._photoFile) setFallback(shownName); };
      name.addEventListener("input", updateAvatarName);
      alias.addEventListener("input", updateAvatarName);
      top.append(photoButton, name, remove);
      const notes = document.createElement("textarea"); notes.className = "roi-character-notes"; notes.value = item.notes || ""; notes.placeholder = "Observações sobre o personagem ou a voz futura"; notes.maxLength = 2000; notes.setAttribute("aria-label", "Observações do personagem");
      const attachment = document.createElement("div"); attachment.className = "roi-character-attachment";
      const photoFile = document.createElement("input"); photoFile.className = "roi-photo-file"; photoFile.type = "file"; photoFile.accept = ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"; photoFile.setAttribute("aria-label", "Anexar foto do personagem");
      const attachPhoto = document.createElement("button"); attachPhoto.type = "button"; attachPhoto.className = "button quiet roi-attach-photo"; attachPhoto.textContent = "📷 Anexar foto"; attachPhoto.title = "Escolha uma foto JPG, PNG ou WebP para este personagem."; attachPhoto.addEventListener("click", () => photoFile.click());
      photoButton.addEventListener("click", () => photoFile.click());
      const photoStatus = document.createElement("span"); photoStatus.className = "roi-character-file-status roi-photo-status";
      photoStatus.textContent = item.photo_name || "Foto opcional";
      if (item.photoFile) { const transfer = new DataTransfer(); transfer.items.add(item.photoFile); photoFile.files = transfer.files; photoStatus.textContent = "Pronta: " + item.photoFile.name; photo._objectUrl = URL.createObjectURL(item.photoFile); photo.src = photo._objectUrl; photo.hidden = false; photoFallback.hidden = true; }
      photoFile.addEventListener("change", () => { const selected = photoFile.files && photoFile.files[0]; if (!selected) return; card._photoFile = selected; photoStatus.textContent = "Pronta: " + selected.name; if (photo._objectUrl) URL.revokeObjectURL(photo._objectUrl); photo._objectUrl = URL.createObjectURL(selected); photo.src = photo._objectUrl; photo.hidden = false; photoFallback.hidden = true; });
      const file = document.createElement("input"); file.className = "roi-character-file"; file.type = "file"; file.accept = ".mp3,.wav,audio/mpeg,audio/wav,audio/x-wav"; file.setAttribute("aria-label", "Anexar referência de voz em MP3 ou WAV");
      const attach = document.createElement("button"); attach.type = "button"; attach.className = "button quiet roi-attach-mp3"; attach.textContent = "📎 Anexar áudio"; attach.title = "Anexe uma referência de voz em MP3 ou WAV. O perfil da tela Dublagem detectará este arquivo automaticamente para clonagem."; attach.addEventListener("click", () => file.click());
      const status = document.createElement("span"); status.className = "roi-character-file-status";
      status.textContent = item.sample_mp3_name ? `${item.sample_mp3_name} · ${formatBytes(item.sample_mp3_bytes)}` : "MP3 ou WAV opcional — você pode anexar depois";
      if (item.sample_mp4_name) status.title = `MP4 já anexado anteriormente: ${item.sample_mp4_name} · ${formatBytes(item.sample_mp4_bytes)}. Ele foi mantido.`;
      if (item.file) { const transfer = new DataTransfer(); transfer.items.add(item.file); file.files = transfer.files; status.textContent = `Pronto para anexar: ${item.file.name}`; }
      file.addEventListener("change", () => { if (file.files?.[0]) status.textContent = `Pronto para anexar: ${file.files[0].name}`; });
      card._fileInput = file; card._photoInput = photoFile; card._photoFile = item.photoFile || null; card._profile = item;
      attachment.append(attachPhoto, photoFile, photoStatus, attach, file, status); card.append(top, aliasField, colorField, notes, attachment); applyCharacterColor(); list.append(card);
      card.dataset.characterId = item.character_id || card.dataset.characterId;
    }
  }
  async function loadCharacters(region) {
    if (!region.roi_id) region.roi_id = crypto.randomUUID();
    el("roiCharacters").textContent = "Carregando personagens…";
    profilesAvailable = false;
    profilesLoading = true;
    const status = el("roiCharacterLoadStatus");
    status.textContent = "";
    status.classList.add("hidden");
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      let result;
      try {
        await api(`/api/roi-characters/${encodeURIComponent(region.roi_id)}/sync-from-events`, {
          method: "POST", body: "{}", signal: controller.signal,
        });
        result = await api(`/api/roi-characters?roi_id=${encodeURIComponent(region.roi_id)}`, { signal: controller.signal });
      } finally { clearTimeout(timeout); }
      profilesAvailable = true;
      region.characters = result.characters || [];
      renderCharacters(region.characters);
    } catch (error) {
      profilesAvailable = false;
      renderCharacters([]);
      status.textContent = `Lista de personagens indisponível (${error.name === "AbortError" ? "tempo esgotado" : error.message}). As configurações da ROI ainda podem ser aplicadas.`;
      status.classList.remove("hidden");
    } finally { profilesLoading = false; }
  }
  function collectCharacters() {
    const cards = [...el("roiCharacters").querySelectorAll(".roi-character")];
    const characters = cards.map((card) => ({
      character_id: card.dataset.characterId || crypto.randomUUID(),
      name: card.querySelector(".roi-character-name").value.trim(),
      alias: card.querySelector(".roi-character-alias").value.trim(),
      color: card._autoColor ? "" : card.querySelector(".roi-character-color-input").value,
      autoColor: card._autoColor,
      notes: card.querySelector(".roi-character-notes").value.trim(),
      file: card._fileInput?.files?.[0] || null,
      photoFile: card._photoInput?.files?.[0] || card._photoFile || null,
    }));
    if (characters.some((item) => !item.name)) throw new Error("Informe o nome de cada personagem cadastrado.");
    const names = characters.map((item) => (item.alias || item.name).normalize("NFKC").toLocaleLowerCase("pt-BR")
      .replace(/[^\p{L}\p{N}_\s]/gu, " ").trim().replace(/\s+/g, " "));
    if (new Set(names).size !== names.length) throw new Error("Os aliases internos dos personagens nesta ROI devem ser únicos.");
    return characters;
  }
  async function persistCharacters(region) {
    const characters = collectCharacters();
    if (profilesLoading && characters.length) throw new Error("Aguarde o carregamento dos personagens antes de salvar novos cadastros.");
    if (characters.some((item) => item.file && !/\.(mp3|wav)$/i.test(item.file.name))) throw new Error("A referência de voz precisa estar no formato MP3 ou WAV.");
    if (characters.some((item) => item.photoFile && !/\.(jpe?g|png|webp)$/i.test(item.photoFile.name))) throw new Error("A foto precisa estar no formato JPG, PNG ou WebP.");
    // If the profile endpoint was unavailable, an empty form must not archive
    // profiles that could not be loaded. Other ROI settings remain applicable.
    if (!profilesAvailable && characters.length === 0) return;
    const saved = await api(`/api/roi-characters/${encodeURIComponent(region.roi_id)}`, {
      method: "PUT", body: JSON.stringify({ characters: characters.map(({ character_id, name, alias, color, notes }) => ({ character_id, name, alias, color, notes })) }),
    });
    for (const character of characters) {
      if (character.photoFile) {
        const response = await fetch("/api/roi-characters/" + encodeURIComponent(character.character_id) + "/photo", {
          method: "PUT", headers: { "Content-Type": character.photoFile.type || "application/octet-stream", "X-File-Name": encodeURIComponent(character.photoFile.name) }, body: character.photoFile,
        });
        if (!response.ok) { let data = {}; try { data = await response.json(); } catch {} throw new Error(data.detail || "Falha ao anexar a foto " + character.photoFile.name + "."); }
        const uploaded = await response.json();
        const profile = (saved.characters || []).find((item) => item.character_id === uploaded.character_id);
        if (profile) { profile.photo_name = uploaded.photo_name; profile.photo_bytes = uploaded.photo_bytes; profile.photo_url = uploaded.photo_url; }
      }
      if (!character.file) continue;
      const audioType = character.file.name.toLowerCase().endsWith(".wav") ? "audio/wav" : "audio/mpeg";
      const response = await fetch(`/api/roi-characters/${encodeURIComponent(character.character_id)}/audio-reference`, {
        method: "PUT", headers: { "Content-Type": audioType, "X-File-Name": encodeURIComponent(character.file.name) }, body: character.file,
      });
      if (!response.ok) { let data = {}; try { data = await response.json(); } catch {} throw new Error(data.detail || `Falha ao anexar ${character.file.name}.`); }
      const uploaded = await response.json();
      const profile = (saved.characters || []).find((item) => item.character_id === uploaded.character_id);
      if (profile) { profile.sample_mp3_name = uploaded.sample_mp3_name; profile.sample_mp3_bytes = uploaded.sample_mp3_bytes; }
    }
    region.characters = saved.characters || [];
    for (const card of el("roiCharacters").querySelectorAll(".roi-character")) {
      const profile = region.characters.find((item) => item.character_id === card.dataset.characterId);
      if (profile) {
        const color = card.querySelector(".roi-character-color-input");
        color.value = profile.color; card._autoColor = false;
        card.style.setProperty("--character-color", profile.color);
        card.querySelector(".roi-character-avatar").style.setProperty("--character-color", profile.color);
      }
    }
    await window.refreshRoiCharacterColors?.(true);
    if (typeof characterOptionsRegionId !== "undefined" && characterOptionsRegionId === region.roi_id) {
      characterOptionsData = region.characters;
      characterOptionsLoadedAt = Date.now();
      updateReviewCharacterPhoto();
    } else if (typeof characterOptionsLoadedAt !== "undefined") characterOptionsLoadedAt = 0;
  }
  function row(kind, value = {}) {
    const tr = document.createElement("tr");
    tr.innerHTML = kind === "lifetime"
      ? `<td><select class="roi-mode">${option("absolute", "Vídeo completo")}${option("relative", "Relativo ao trecho")}</select></td><td><input class="roi-start" type="number" min="0" step=".001" value="${Number(value.start ?? 0)}" aria-label="Início da janela em segundos"></td><td><input class="roi-end" type="number" min="0" step=".001" value="${Number(value.end ?? 60)}" aria-label="Fim da janela em segundos"></td><td><button type="button" class="roi-remove-row" title="Excluir esta janela de tempo" aria-label="Excluir janela">×</button></td>`
      : `<td><select class="roi-mode">${option("absolute", "Vídeo completo")}${option("relative", "Relativo ao trecho")}</select></td><td><input class="roi-time" type="number" min="0" step=".001" value="${Number(value.time ?? 0)}" aria-label="Tempo desta posição em segundos"></td>${["x", "y", "width", "height"].map((key) => `<td><input class="roi-${key}" type="number" min="0" max="100" step=".1" value="${percent(value[key] ?? regions[index]?.[key] ?? 0)}" aria-label="${key} em porcentagem"></td>`).join("")}<td><button type="button" class="roi-remove-row" title="Excluir esta posição" aria-label="Excluir posição">×</button></td>`;
    tr.querySelector(".roi-mode").value = value.mode || "relative";
    tr.querySelector(".roi-remove-row").addEventListener("click", () => tr.remove());
    el(kind === "lifetime" ? "roiLifetimeRows" : "roiMotionRows").append(tr);
  }
  function tab(name) {
    dialog.querySelectorAll(".roi-dialog-tab").forEach((button) => button.classList.toggle("active", button.dataset.roiTab === name));
    dialog.querySelectorAll(".roi-page").forEach((page) => page.classList.toggle("active", page.dataset.roiPage === name));
  }
  window.openRoiSettings = async (regionIndex) => {
    index = regionIndex;
    const region = regions[index];
    if (!region) return;
    el("roiDialogTitle").textContent = `Configurar ${region.name}`;
    el("roiSettingsError").textContent = "";
    el("roiLifetimeEnabled").checked = !!region.lifetime?.enabled;
    el("roiMotionEnabled").checked = !!region.motion?.enabled;
    el("roiSpeakerEnabled").checked = !!region.speaker_enabled;
    el("roiLifetimeRows").replaceChildren();
    el("roiMotionRows").replaceChildren();
    (region.lifetime?.windows || []).forEach((item) => row("lifetime", item));
    (region.motion?.keyframes || []).forEach((item) => row("motion", item));
    if (!el("roiMotionRows").children.length) row("motion", { mode: "relative", time: 0, ...region });
    const speaker = region.speaker_region || { x: region.x, y: Math.max(0, region.y - .09), width: Math.min(region.width, .28), height: .07 };
    for (const [key, id] of [["x", "roiSpeakerX"], ["y", "roiSpeakerY"], ["width", "roiSpeakerW"], ["height", "roiSpeakerH"]]) el(id).value = percent(speaker[key]);
    renderCharacters([]);
    tab("lifetime");
    dialog.showModal();
    await loadCharacters(region);
  };
  window.openRoiCharacterSettings = async (roiId, characterId) => {
    const regionIndex = regions.findIndex((region) => String(region.roi_id) === String(roiId));
    if (regionIndex < 0) return false;
    await window.openRoiSettings(regionIndex);
    dialog.querySelector('.roi-dialog-tab[data-roi-tab="speaker"]')?.click();
    const card = [...el("roiCharacters").querySelectorAll(".roi-character")]
      .find((item) => String(item.dataset.characterId) === String(characterId));
    if (!card) return false;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("roi-character-targeted");
    window.setTimeout(() => card.classList.remove("roi-character-targeted"), 1800);
    return true;
  };
  function checkedTime(value, label) {
    const result = number(value);
    if (!Number.isFinite(result) || result < 0) throw new Error(`${label}: use segundos iguais ou maiores que zero.`);
    return result;
  }
  function checkedBox(values, label) {
    const result = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, number(value) / 100]));
    const { x, y, width, height } = result;
    if (![x, y, width, height].every(Number.isFinite) || Math.min(x, y) < 0 || Math.min(width, height) <= 0 || x + width > 1.00001 || y + height > 1.00001) throw new Error(`${label}: posição e tamanho devem caber no frame.`);
    return result;
  }
  function collect() {
    const windows = [...el("roiLifetimeRows").children].map((tr) => {
      const start = checkedTime(tr.querySelector(".roi-start").value, "Início da janela");
      const end = checkedTime(tr.querySelector(".roi-end").value, "Fim da janela");
      if (end <= start) throw new Error("O fim da janela deve ser posterior ao início.");
      return { mode: tr.querySelector(".roi-mode").value, start, end };
    });
    const keyframes = [...el("roiMotionRows").children].map((tr) => ({
      mode: tr.querySelector(".roi-mode").value,
      time: checkedTime(tr.querySelector(".roi-time").value, "Tempo da posição"),
      ...checkedBox(Object.fromEntries(["x", "y", "width", "height"].map((key) => [key, tr.querySelector(`.roi-${key}`).value])), "Posição da ROI"),
    }));
    const speaker_region = checkedBox({ x: el("roiSpeakerX").value, y: el("roiSpeakerY").value,
      width: el("roiSpeakerW").value, height: el("roiSpeakerH").value }, "Área do personagem");
    if (el("roiLifetimeEnabled").checked && !windows.length) throw new Error("Adicione pelo menos uma janela de tempo.");
    if (el("roiMotionEnabled").checked && !keyframes.length) throw new Error("Adicione pelo menos uma posição da ROI.");
    return { lifetime: { enabled: el("roiLifetimeEnabled").checked, windows },
      motion: { enabled: el("roiMotionEnabled").checked, keyframes },
      speaker_enabled: el("roiSpeakerEnabled").checked, speaker_region };
  }
  function close() { dialog.close(); }
  dialog.querySelectorAll(".roi-dialog-tab").forEach((button) => button.addEventListener("click", () => tab(button.dataset.roiTab)));
  el("roiAddWindow").addEventListener("click", () => row("lifetime", { mode: "relative", start: 0, end: 60 }));
  el("roiAddPoint").addEventListener("click", () => row("motion", { mode: "relative", time: 0, ...regions[index] }));
  el("roiAddCharacter").addEventListener("click", () => renderCharacters([...el("roiCharacters").querySelectorAll(".roi-character")].map((card) => ({
    ...card._profile, character_id: card.dataset.characterId, name: card.querySelector(".roi-character-name").value,
    alias: card.querySelector(".roi-character-alias").value,
    color: card.querySelector(".roi-character-color-input").value,
    autoColor: card._autoColor,
    notes: card.querySelector(".roi-character-notes").value,
    file: card._fileInput?.files?.[0] || null,
    photoFile: card._photoInput?.files?.[0] || card._photoFile || null,
  })).concat({ character_id: crypto.randomUUID(), name: "", notes: "", autoColor: true })));
  el("roiDialogClose").addEventListener("click", close);
  el("roiDialogCancel").addEventListener("click", close);
  el("roiDialogSave").addEventListener("click", async () => {
    try {
      const region = regions[index];
      Object.assign(region, collect());
      await persistCharacters(region);
      if (typeof eventFilterOptionsLoadedAt !== "undefined") eventFilterOptionsLoadedAt = 0;
      if (typeof refreshEvents === "function") await refreshEvents(true);
      updateRoiList(); drawPreview(); close();
      toast("ROI salva. Os aliases foram aplicados aos diálogos vinculados e às próximas análises.");
    } catch (error) { el("roiSettingsError").textContent = error.message; }
  });
  el("roiMarkSpeaker").addEventListener("click", async () => {
    try {
      const region = regions[index];
      if (!preview) throw new Error("Abra a pré-visualização para marcar a área do personagem.");
      const speaker_region = checkedBox({ x: el("roiSpeakerX").value, y: el("roiSpeakerY").value,
        width: el("roiSpeakerW").value, height: el("roiSpeakerH").value }, "Área do personagem");
      region.speaker_region = speaker_region;
      region.speaker_enabled = el("roiSpeakerEnabled").checked;
      window.speakerRoiIndex = index;
      previewMode = "speaker-roi";
      dialog.close();
      drawPreview();
      toast("Arraste sobre o nome do personagem na prévia.");
    } catch (error) { el("roiSettingsError").textContent = error.message; }
  });
  window.roiSpeakerSelectionComplete = (regionIndex, selected) => {
    if (regionIndex !== index || !regions[index]) return;
    const region = regions[index], speaker = region.speaker_region || {};
    for (const [key, id] of [["x", "roiSpeakerX"], ["y", "roiSpeakerY"], ["width", "roiSpeakerW"], ["height", "roiSpeakerH"]]) {
      if (Number.isFinite(Number(speaker[key]))) el(id).value = percent(speaker[key]);
    }
    el("roiSpeakerEnabled").checked = !!region.speaker_enabled;
    tab("speaker");
    if (!dialog.open) dialog.showModal();
    el("roiSettingsError").textContent = selected ? "Área do nome marcada. Clique em Aplicar à próxima análise para guardar." : "Seleção cancelada ou muito pequena. Tente arrastar uma área maior.";
  };
})();
