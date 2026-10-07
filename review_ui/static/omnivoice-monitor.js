(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const dialog = $("#omnivoiceMonitorDialog");
  const headerButton = $("#omnivoiceHeaderButton");
  if (!dialog || !headerButton) return;

  let providerState = null;
  let runtime = null;
  let dubbingQueue = null;
  let busy = false;
  let detailTimer = 0;

  async function getJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function text(selector, value) {
    const node = $(selector);
    if (node) node.textContent = value ?? "—";
  }

  function bytes(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount) || amount < 0) return "—";
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let size = amount, index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index++; }
    return `${size.toFixed(index >= 2 ? 2 : 0)} ${units[index]}`;
  }

  function duration(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(value / 3600), minutes = Math.floor((value % 3600) / 60);
    return hours ? `${hours} h ${minutes} min` : minutes ? `${minutes} min ${value % 60} s` : `${value} s`;
  }

  function renderHeader() {
    const provider = providerState || {};
    const badge = headerButton;
    const loaded = Boolean(provider.api_online && provider.model_loaded);
    const loading = Boolean(provider.build_running || (provider.api_online && !provider.model_loaded && !provider.error));
    const queueCounts = dubbingQueue?.counts || {};
    const cpu = runtime?.current;
    const cpuCoresUsed = Number(cpu?.cpu_cores_used);
    const cpuActive = Number.isFinite(cpuCoresUsed) ? cpuCoresUsed >= 0.08 : Number(cpu?.cpu_percent) >= 2;
    const processing = loaded && Boolean(Number(queueCounts.generating) || Number(runtime?.jobs?.generating) || cpuActive);
    const failed = Boolean(provider.error || provider.state === "error");
    badge.className = `omnivoice-header-badge ${failed ? "failed" : processing ? "processing" : loaded ? "ready" : loading ? "waiting" : "offline"}`;
    text("#omnivoiceHeaderLabel", failed ? "OmniVoice · erro" : processing ? "OmniVoice · processando" : loaded ? "OmniVoice · online" : loading ? "OmniVoice · iniciando" : "OmniVoice · offline");
    text("#omnivoiceHeaderCpu", cpu ? `CPU ${Number(cpu.cpu_percent).toFixed(0)}%` : "CPU —");
    badge.title = cpu
      ? `OmniVoice ${failed ? "com erro" : processing ? "processando" : loaded ? "online" : "indisponível"} · CPU ${Number(cpu.cpu_percent).toFixed(1)}% da capacidade do contêiner · clique para detalhes`
      : "Ver uso de CPU, RAM, contêiner e processos OmniVoice";
    badge.setAttribute("aria-label", badge.title);
  }

  async function refreshProject() {
    try {
      const overview = await getJson("/api/dubbing/overview");
      const project = overview.project || {};
      text("#projectHeaderName", project.name || project.game_title || "Projeto");
      text("#projectHeaderLanguage", String(project.dub_language || "pt-BR").toUpperCase());
    } catch { /* Keep the project name already rendered in the header. */ }
  }

  function drawChart(selector, samples, key, color, label) {
    const canvas = $(selector);
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 10) return;
    const scale = Math.max(1, window.devicePixelRatio || 1);
    const width = Math.round(rect.width), height = 142;
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const context = canvas.getContext("2d");
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, width, height);
    const left = 35, right = 8, top = 8, bottom = 20;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    context.font = '8px "Segoe UI", Arial, sans-serif';
    context.textAlign = "right";
    for (let tick = 0; tick <= 100; tick += 25) {
      const y = top + plotHeight * (1 - tick / 100);
      context.strokeStyle = "#263950";
      context.beginPath(); context.moveTo(left, y); context.lineTo(width - right, y); context.stroke();
      context.fillStyle = "#7f91a8";
      context.fillText(`${tick}${label}`, left - 5, y + 3);
    }
    context.textAlign = "left";
    context.fillStyle = "#73859b";
    context.fillText("mais antigo", left, height - 4);
    context.textAlign = "right";
    context.fillText("agora", width - right, height - 4);
    const valid = samples.filter((sample) => Number.isFinite(Number(sample[key])));
    if (!valid.length) {
      context.textAlign = "center";
      context.fillStyle = "#71839a";
      context.fillText("Aguardando amostras…", left + plotWidth / 2, top + plotHeight / 2);
      return;
    }
    context.strokeStyle = color;
    context.lineWidth = 2;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.beginPath();
    valid.forEach((sample, index) => {
      const x = left + (valid.length < 2 ? 0 : plotWidth * index / (valid.length - 1));
      const value = Math.max(0, Math.min(100, Number(sample[key])));
      const y = top + plotHeight * (1 - value / 100);
      if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
    });
    context.stroke();
    const lastValue = Math.max(0, Math.min(100, Number(valid.at(-1)[key])));
    context.fillStyle = color;
    context.beginPath();
    context.arc(left + (valid.length < 2 ? 0 : plotWidth), top + plotHeight * (1 - lastValue / 100), 3, 0, Math.PI * 2);
    context.fill();
  }

  function renderProcesses(items) {
    const body = $("#omnivoiceProcessRows");
    const rows = Array.isArray(items) ? items : [];
    body.replaceChildren();
    if (!rows.length) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 6;
      cell.textContent = providerState?.api_online ? "Nenhum processo listado pelo provider." : "O contêiner está offline.";
      row.append(cell); body.append(row); return;
    }
    for (const process of rows) {
      const row = document.createElement("tr");
      const values = [process.pid, process.command || process.name, process.status,
        `${Number(process.cpu_percent || 0).toFixed(1)}%`, bytes(process.rss_bytes), process.threads ?? "—"];
      for (const value of values) {
        const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell);
      }
      row.title = process.command || process.name || "";
      body.append(row);
    }
  }

  function renderDetails() {
    const provider = providerState || {};
    const current = runtime?.current;
    const history = runtime?.history || [];
    const loaded = Boolean(provider.api_online && provider.model_loaded);
    const loading = Boolean(provider.build_running || (provider.api_online && !provider.model_loaded && !provider.error));
    const stateNode = $("#omnivoiceMonitorState");
    stateNode.className = `omnivoice-runtime-state ${loaded ? "" : loading ? "waiting" : "offline"}`;
    stateNode.textContent = loaded ? "Online · saudável" : loading ? "Iniciando / carregando" : provider.error ? "Erro" : "Offline";

    text("#omnivoiceCpuValue", current ? `${Number(current.cpu_percent).toFixed(1)}%` : "—");
    text("#omnivoiceCpuDetail", current ? `${Number(current.cpu_cores_used).toFixed(2)} de ${current.cpu_available_cores} núcleos · medição do contêiner` : "% da capacidade total");
    text("#omnivoiceMemoryValue", current ? `${bytes(current.memory_used_bytes)} / ${bytes(current.memory_limit_bytes)}` : "—");
    text("#omnivoiceMemoryDetail", current ? `${Number(current.memory_percent).toFixed(1)}% do limite · RAM do contêiner` : "uso atual / limite");
    text("#omnivoiceCoreValue", current ? `${Number(current.cpu_cores_used).toFixed(2)} / ${current.cpu_available_cores}` : "—");
    text("#omnivoiceModelValue", provider.model_loaded ? "Carregado" : provider.api_online ? "Carregando" : "—");
    text("#omnivoiceModelDetail", `OmniVoice ${provider.provider_version || "—"} · dispositivo ${provider.device || "—"}`);
    text("#omnivoiceContainerName", current ? `OmniVoice · ${current.container_id}` : provider.api_online ? "OmniVoice" : "—");
    text("#omnivoiceContainerHealth", loaded ? "running · healthy" : loading ? "running · starting" : provider.api_online ? "running · unhealthy" : "indisponível");
    text("#omnivoiceUptime", current ? duration(current.uptime_seconds) : "—");
    text("#omnivoiceRevision", provider.model_revision || "—");
    text("#omnivoiceProcessCount", current ? `${current.process_count} processo(s)` : "");
    text("#omnivoiceMemoryChartLabel", current ? `RAM · ${bytes(current.memory_used_bytes)} / ${bytes(current.memory_limit_bytes)}` : "uso do limite do contêiner");
    renderProcesses(current?.processes);
    drawChart("#omnivoiceCpuChart", history, "cpu_percent", "#58a6ff", "%");
    drawChart("#omnivoiceMemoryChart", history, "memory_percent", "#bd8bff", "%");
    const message = current
      ? "CPU e memória são coletadas a cada 2 s dentro do contêiner; o gráfico mantém até 4 minutos de histórico local."
      : provider.message || provider.error || "Aguardando resposta do provider local.";
    text("#omnivoiceMonitorMessage", message);
  }

  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      providerState = await getJson("/api/dubbing/provider");
      dubbingQueue = await getJson("/api/dubbing/queue").catch(() => null);
      runtime = null;
      if (providerState.api_online && providerState.base_url) {
        const base = providerState.base_url.replace(/\/$/, "");
        try { runtime = await getJson(`${base}/v1/monitoring`); }
        catch (error) { providerState.metrics_error = error.message; }
      }
      renderHeader();
      if (dialog.open) renderDetails();
    } catch (error) {
      providerState = { api_online: false, state: "offline", message: `Não foi possível consultar o serviço: ${error.message}` };
      runtime = null;
      renderHeader();
      if (dialog.open) renderDetails();
    } finally { busy = false; }
  }

  headerButton.addEventListener("click", () => {
    if (!dialog.open) dialog.showModal();
    void refresh();
    clearInterval(detailTimer);
    detailTimer = setInterval(() => { if (dialog.open) void refresh(); }, 2000);
  });
  $("#omnivoiceMonitorClose").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => { clearInterval(detailTimer); detailTimer = 0; });

  void refreshProject();
  void refresh();
  setInterval(() => { if (!dialog.open) void refresh(); }, 2000);
  window.addEventListener("resize", () => { if (dialog.open) renderDetails(); });
})();
