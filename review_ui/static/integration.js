(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { profile: null, plugins: [], mapping: [], offset: 0, total: 0, eligible: 0,
    approved: 0, mapped: 0, testRuns: [], loading: false };
  let toastTimer;

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}),
    } });
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = data?.detail;
      throw new Error(typeof detail === "string" ? detail : detail?.message || `Falha HTTP ${response.status}`);
    }
    return data;
  }

  function toast(message, error = false) {
    const box = $("#integrationToast");
    box.textContent = message;
    box.classList.toggle("error", error);
    box.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("visible"), 3800);
  }

  function setStatus(id, good, text) {
    const element = $(id);
    if (!element) return;
    element.classList.toggle("good", Boolean(good));
    element.classList.toggle("pending", !good);
    if (text !== undefined) element.textContent = text;
  }

  function setActivePluginSelect() {
    const select = $("#activePluginSelect");
    const selectedId = state.profile?.plugin?.plugin_record_id || "";
    const plugins = state.plugins.filter((plugin) => isPluginCompatible(plugin) || plugin.plugin_record_id === selectedId);
    select.innerHTML = `<option value="">Nenhum plugin vinculado</option>` + plugins.map((plugin) =>
      `<option value="${escapeHtml(plugin.plugin_record_id)}" ${plugin.plugin_record_id === selectedId ? "selected" : ""}>${escapeHtml(plugin.name)} · v${escapeHtml(plugin.version)}</option>`
    ).join("");
    select.disabled = state.plugins.length === 0;
  }

  function isPluginCompatible(plugin) {
    const project = window.DUBSYNC_PROJECT || {};
    const gameNames = [project.game_title, project.name, project.slug].filter(Boolean).map((value) => String(value).toLocaleLowerCase());
    const games = plugin.games_json || [];
    const gameMatch = !games.length || games.some((game) => {
      const key = String(game).toLocaleLowerCase();
      return key === "any" || gameNames.some((name) => name.includes(key) || key.includes(name));
    });
    const configuredEngine = String(state.profile?.engine || "");
    const engine = String(!configuredEngine || configuredEngine.toLocaleLowerCase() === "unknown"
      ? (/replaced/i.test(gameNames.join(" ")) ? "Unity IL2CPP" : "")
      : configuredEngine).toLocaleLowerCase();
    const engines = plugin.engines_json || [];
    const engineMatch = !engines.length || engines.some((item) => {
      const key = String(item).toLocaleLowerCase();
      return key === "any" || !engine || engine.includes(key) || key.includes(engine);
    });
    return gameMatch && engineMatch;
  }

  function fillProfile() {
    const form = $("#profileForm");
    const profile = state.profile;
    const project = window.DUBSYNC_PROJECT || {};
    form.elements.engine.value = profile?.engine || (/replaced/i.test(project.game_title || project.name || "") ? "Unity IL2CPP" : "unknown");
    form.elements.build_version.value = profile?.build?.version || project.game_version || "";
    form.elements.platform.value = profile?.build?.platform || project.platform || "";
    form.elements.language.value = profile?.language || project.dub_language || "pt-BR";
    form.elements.executable_path.value = profile?.executable_path || "";
    form.elements.install_root.value = profile?.install_root || "";
    form.elements.audio_format.value = profile?.audio_profile_json?.format || "ogg";
    form.elements.bitrate_kbps.value = profile?.audio_profile_json?.bitrate_kbps || 64;
    $("#heroBuildLabel").textContent = profile?.build?.version ? `Build ${profile.build.version}` : "Build não configurada";
    $("#summaryExecutable").textContent = profile?.executable_path || "Não configurado";
    $("#summaryEngine").textContent = profile?.engine || "—";
    $("#summaryPlugin").textContent = profile?.plugin?.name || "Nenhum";
    $("#publicationAdapter").textContent = profile?.plugin?.name || "Pendente";
    const audio = profile?.audio_profile_json || {};
    $("#compatAudio").textContent = `${String(audio.format || "ogg").toUpperCase()}${audio.format === "wav" ? "" : " Vorbis"} · mono · ${(audio.sample_rate || 24000) / 1000} kHz`;
    const configured = Boolean(profile?.executable_exists && profile?.game_build_id);
    setStatus("#statusGame", configured, configured ? "✓" : "·");
    $("#statusGameText").textContent = configured ? "Executável encontrado · build cadastrada" : "Caminho ou build pendentes";
    $("#summaryIntegration").textContent = configured ? "Perfil configurado" : "Configuração pendente";
    $("#heroStatusDot").classList.toggle("configured", configured);
    $("#runtimeHeaderState").textContent = profile?.runtime_ready ? "Runtime conectado" : "Runtime pendente";
    $("#runtimeHeaderState").previousElementSibling?.classList.toggle("green", Boolean(profile?.runtime_ready));
    $("#runtimeHeaderState").previousElementSibling?.classList.toggle("amber", !profile?.runtime_ready);
    $("#testPluginState").classList.toggle("good", Boolean(profile?.binding));
    $("#testPluginState").textContent = profile?.binding ? "✓" : "○";
    $("#testBridgeState").classList.remove("good");
    $("#testBridgeState").textContent = "○";
  }

  function renderCatalog() {
    const catalog = $("#pluginCatalog");
    const active = state.profile?.plugin?.plugin_record_id;
    const plugins = $("#showAllPlugins")?.checked ? state.plugins : state.plugins.filter((plugin) =>
      isPluginCompatible(plugin) || plugin.plugin_record_id === active);
    if (!plugins.length) {
      catalog.innerHTML = `<p class="empty-row">Nenhum plugin compatível. Marque “Mostrar todos” para inspecionar o catálogo completo.</p>`;
      return;
    }
    catalog.innerHTML = plugins.map((plugin) => {
      const status = plugin.status === "installed" ? "Instalado" : plugin.status === "adapter_pending" ? "Adaptador pendente" : plugin.status === "metadata_only" ? "Manifesto · não verificado" : "Planejado";
      return `<article class="plugin-card ${plugin.plugin_record_id === active ? "plugin-active" : ""}">
        <div class="plugin-emblem">✣</div><div class="plugin-card-copy"><div class="plugin-card-title"><h2>${escapeHtml(plugin.name)}</h2><span>v${escapeHtml(plugin.version)}</span></div>
        <p>${escapeHtml(plugin.description)}</p><div class="plugin-tags">${(plugin.engines_json || []).map((engine) => `<span>${escapeHtml(engine)}</span>`).join("")}${(plugin.capabilities_json || []).slice(0, 4).map((cap) => `<span>${escapeHtml(cap.replaceAll("_", " "))}</span>`).join("")}</div></div>
        <div class="plugin-card-action"><span class="plugin-state ${plugin.status === "installed" ? "installed" : "pending"}">${status}</span>
        <button class="button ${plugin.plugin_record_id === active ? "quiet" : "outline"}" type="button" data-bind-plugin="${escapeHtml(plugin.plugin_record_id)}" ${!state.profile ? "disabled title=\"Salve a configuração do jogo primeiro\"" : ""}>${plugin.plugin_record_id === active ? "Ativo neste jogo" : "Vincular ao jogo"}</button></div></article>`;
    }).join("");
  }

  function renderMapping() {
    const search = $("#mappingSearch").value.trim().toLocaleLowerCase();
    const approvedOnly = $("#onlyApproved").checked;
    const items = state.mapping.filter((item) => {
      if (approvedOnly && !item.approved_take_id) return false;
      return !search || [item.source_text, item.dub_text, item.runtime_trigger_id, item.speaker]
        .some((value) => String(value || "").toLocaleLowerCase().includes(search));
    });
    const body = $("#mappingBody");
    if (!items.length) {
      body.innerHTML = `<tr><td colspan="4" class="empty-row">Nenhuma fala corresponde ao filtro.</td></tr>`;
      return;
    }
    body.innerHTML = items.map((item) => `<tr data-occurrence="${escapeHtml(item.occurrence_id)}">
      <td><b>${escapeHtml(item.source_text || item.dub_text || "(fala sem texto)")}</b><small>${escapeHtml(item.speaker || "Personagem não vinculado")} · ${escapeHtml(item.occurrence_id)}</small></td>
      <td>${item.approved_take_id ? `<span class="take-good">✓ ${escapeHtml(item.approved_take_id)}</span>` : `<span class="take-missing">Sem take aprovado</span>`}</td>
      <td class="trigger-cell"><select class="trigger-type" aria-label="Tipo de trigger"><option value="articy_external_id" ${item.runtime_trigger_type === "articy_external_id" || !item.runtime_trigger_type ? "selected" : ""}>Articy ExternalId</option><option value="custom_event_id" ${item.runtime_trigger_type === "custom_event_id" ? "selected" : ""}>ID customizado</option></select><input class="trigger-id" value="${escapeHtml(item.runtime_trigger_id || "")}" maxlength="160" placeholder="Cole o ID observado"><small>${item.runtime_trigger_id ? "Gatilho salvo" : "Aguardando ID real do jogo"}</small></td>
      <td><button class="button quiet save-mapping" type="button">Salvar</button></td></tr>`).join("");
  }

  function updateCounts() {
    $("#mappingTotals").textContent = `${state.mapped}/${state.total} gatilhos · ${state.eligible} prontos`;
    $("#mappingPageInfo").textContent = `${state.mapping.length} de ${state.total} ocorrências`;
    $("#loadMoreMappings").hidden = state.mapping.length >= state.total;
    $("#summaryMapping").textContent = `${state.mapped} / ${state.total} gatilhos`;
    $("#summaryTakes").textContent = `${state.approved} aprovados`;
    $("#publicationEligible").textContent = String(state.eligible);
    $("#statusManifestText").textContent = state.eligible ? `${state.eligible} takes e gatilhos compatíveis` : "Nenhum take aprovado com gatilho real";
    setStatus("#statusManifest", state.eligible > 0, state.eligible ? "✓" : "·");
    $("#testPackageState").classList.toggle("good", Boolean(state.profile?.latest_package));
    $("#testPackageState").textContent = state.profile?.latest_package ? "✓" : "○";
  }

  async function loadMapping(reset = true) {
    if (state.loading) return;
    state.loading = true;
    if (reset) { state.offset = 0; state.mapping = []; }
    try {
      const page = await request(`/api/integration/mapping?limit=100&offset=${state.offset}`);
      state.total = page.total;
      state.mapped = page.mapped_count;
      state.approved = page.approved_count;
      state.eligible = page.eligible_count;
      state.mapping.push(...page.items);
      state.offset += page.items.length;
      updateCounts();
      renderMapping();
    } finally { state.loading = false; }
  }

  function renderTestRuns() {
    const list = $("#testRunsList");
    const latestPackage = state.profile?.latest_package;
    const launch = latestPackage?.status === "ready"
      ? `<button class="button outline" type="button" data-start-test="${escapeHtml(latestPackage.package_id)}">Abrir checklist deste pacote</button>`
      : "";
    if (!state.testRuns.length) {
      list.innerHTML = `${launch}<p class="empty-row">Nenhum teste registrado. O gate de 10 falas só passa com eventos reais e resultados corretos.</p>`;
      return;
    }
    list.innerHTML = `${launch}${state.testRuns.map((run) => {
      const gate = run.summary_json?.acceptance_gate;
      return `<article class="test-run"><span class="run-icon">▷</span><div><b>${escapeHtml(run.test_run_id)}</b><small>${new Date(run.started_at).toLocaleString()} · ${run.result_count}/${run.expected_count} registros</small>${gate ? `<small>Gate 10: ${gate.correct_starts}/10 corretas</small>` : ""}</div><span class="run-status ${run.status}">${escapeHtml(run.status)}</span></article>`;
    }).join("")}`;
  }

  async function reload() {
    try {
      const [profileData, pluginsData, mapData, testsData] = await Promise.all([
        request("/api/integration/profile"), request("/api/integration/plugins"),
        request("/api/integration/mapping?limit=100&offset=0"), request("/api/integration/test-runs"),
      ]);
      state.profile = profileData.profile;
      state.plugins = pluginsData.plugins || [];
      state.mapping = mapData.items || [];
      state.offset = state.mapping.length;
      state.total = mapData.total || 0;
      state.mapped = mapData.mapped_count || 0;
      state.approved = mapData.approved_count || 0;
      state.eligible = mapData.eligible_count || 0;
      state.testRuns = testsData.test_runs || [];
      setActivePluginSelect();
      fillProfile();
      renderCatalog();
      renderMapping();
      renderTestRuns();
      updateCounts();
      renderPackage();
    } catch (error) { toast(error.message, true); }
  }

  function renderPackage(result = null) {
    const latest = result || state.profile?.latest_package;
    const box = $("#packageResult");
    if (!latest) {
      box.hidden = true;
      $("#packageHistory").innerHTML = "";
      return;
    }
    box.hidden = false;
    const ready = latest.status === "ready" || !latest.status;
    const url = latest.download_url || `/api/integration/packages/${encodeURIComponent(latest.package_id)}/download`;
    box.innerHTML = `<div><b>Pacote ${escapeHtml(latest.version || latest.package_version || "")}</b><small>${escapeHtml(latest.entry_count ?? latest.mapped_count ?? 0)} falas · SHA-256 ${escapeHtml((latest.manifest_sha256 || "").slice(0, 16))}… ${ready ? "" : "· DESATUALIZADO"}</small></div>${ready ? `<a class="button outline" href="${escapeHtml(url)}" download>Baixar ZIP</a>` : ""}`;
    $("#packageHistory").innerHTML = `<span>Último pacote:</span> ${escapeHtml(latest.package_id)} · ${escapeHtml(latest.status || "gerado")}`;
  }

  function setSubnav(name) {
    const games = name === "games";
    $$(".switch-tab").forEach((tab) => {
      const active = tab.dataset.subnav === name;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    $("#gamesPanel").hidden = !games;
    $("#pluginsPanel").hidden = games;
    if (!games) renderCatalog();
  }

  function setGameTab(name) {
    $$(".game-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.gameTab === name));
    $$(".game-page").forEach((page) => {
      const active = page.dataset.gamePage === name;
      page.classList.toggle("active", active);
      page.hidden = !active;
    });
  }

  $$(".switch-tab").forEach((tab) => tab.addEventListener("click", () => setSubnav(tab.dataset.subnav)));
  $$(".game-tab").forEach((tab) => tab.addEventListener("click", () => setGameTab(tab.dataset.gameTab)));
  $$('[data-open-tab]').forEach((button) => button.addEventListener("click", () => setGameTab(button.dataset.openTab)));
  $("#managePluginsButton").addEventListener("click", () => setSubnav("plugins"));
  $("#backToGames").addEventListener("click", () => setSubnav("games"));
  $("#showAllPlugins").addEventListener("change", renderCatalog);
  $("#refreshWorkspace").addEventListener("click", reload);
  $("#gameSearch").addEventListener("input", (event) => {
    const query = event.target.value.trim().toLocaleLowerCase();
    $$(".game-entry").forEach((entry) => { entry.hidden = !entry.dataset.search.includes(query); });
  });
  $("#mappingSearch").addEventListener("input", renderMapping);
  $("#onlyApproved").addEventListener("change", renderMapping);
  $("#loadMoreMappings").addEventListener("click", () => loadMapping(false).catch((error) => toast(error.message, true)));

  $("#profileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = Object.fromEntries(form.entries());
    body.bitrate_kbps = Number(body.bitrate_kbps);
    const message = $("#profileSaveMessage");
    message.textContent = "Salvando...";
    try {
      const result = await request("/api/integration/profile", { method: "PUT", body: JSON.stringify(body) });
      state.profile = result.profile;
      fillProfile(); setActivePluginSelect(); renderCatalog(); renderPackage();
      message.textContent = "Configuração salva.";
      toast("Perfil do jogo atualizado.");
    } catch (error) { message.textContent = error.message; toast(error.message, true); }
  });

  $("#activePluginSelect").addEventListener("change", async (event) => {
    if (!event.target.value) return;
    if (!state.profile) { toast("Salve a configuração do jogo antes de vincular um plugin.", true); event.target.value = ""; return; }
    await bindPlugin(event.target.value);
  });

  async function bindPlugin(pluginRecordId) {
    try {
      const result = await request("/api/integration/bindings", { method: "POST", body: JSON.stringify({ plugin_record_id: pluginRecordId }) });
      state.profile = result.profile;
      setActivePluginSelect(); fillProfile(); renderCatalog();
      toast("Plugin vinculado ao perfil do jogo. O hook runtime ainda precisa ser validado.");
    } catch (error) { toast(error.message, true); setActivePluginSelect(); }
  }

  $("#pluginCatalog").addEventListener("click", (event) => {
    const button = event.target.closest("[data-bind-plugin]");
    if (button && !button.disabled) bindPlugin(button.dataset.bindPlugin);
  });

  $("#registerPluginButton").addEventListener("click", async () => {
    const message = $("#pluginRegisterMessage");
    try {
      const manifest = JSON.parse($("#pluginManifestInput").value);
      await request("/api/integration/plugins/register", { method: "POST", body: JSON.stringify({ manifest }) });
      message.textContent = "Manifesto registrado como não verificado.";
      toast("Metadados do plugin registrados; nenhum código foi instalado ou executado.");
      await reload();
    } catch (error) {
      message.textContent = error instanceof SyntaxError ? "JSON inválido." : error.message;
      toast(message.textContent, true);
    }
  });

  $("#mappingBody").addEventListener("click", async (event) => {
    const button = event.target.closest(".save-mapping");
    if (!button) return;
    const row = button.closest("tr[data-occurrence]");
    const triggerId = $(".trigger-id", row).value.trim();
    if (!triggerId) { toast("Informe o ID real observado no jogo.", true); return; }
    button.disabled = true;
    try {
      await request(`/api/integration/mapping/${encodeURIComponent(row.dataset.occurrence)}`, {
        method: "PUT", body: JSON.stringify({ runtime_trigger_type: $(".trigger-type", row).value, runtime_trigger_id: triggerId }),
      });
      toast("Gatilho salvo nesta ocorrência.");
      await loadMapping(true);
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });

  $("#buildPackageButton").addEventListener("click", async () => {
    const button = $("#buildPackageButton");
    const message = $("#packageMessage");
    button.disabled = true;
    message.textContent = "Preparando pacote...";
    try {
      const result = await request("/api/integration/packages", { method: "POST", body: JSON.stringify({ version: $("#packageVersion").value.trim() }) });
      renderPackage(result);
      message.textContent = "Pacote gerado.";
      toast("Pacote runtime criado. O adaptador do jogo continua pendente de prova real.");
      await reload();
    } catch (error) { message.textContent = error.message; toast(error.message, true); }
    finally { button.disabled = false; }
  });

  $("#testRunsList").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-start-test]");
    if (!button) return;
    try {
      const run = await request("/api/integration/test-runs", { method: "POST", body: JSON.stringify({ package_id: button.dataset.startTest }) });
      state.testRuns.unshift(run); renderTestRuns();
      toast(`Checklist aberto: ${run.expected_count} fala(s) no pacote. Isso ainda não registra reprodução real.`);
    } catch (error) { toast(error.message, true); }
  });

  reload();
})();
