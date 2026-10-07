/* Gerenciador compartilhado de layouts nomeados para qualquer tela encaixável.
   Novas telas registram um escopo, chave de estado e chaves legadas/padrão. */
(() => {
  const adapters = new Map();
  const menu = document.getElementById("layoutMenu");
  const toggle = document.getElementById("layoutMenuToggle");
  const popover = document.getElementById("layoutMenuPopover");
  const list = document.getElementById("layoutPresetList");
  const empty = document.getElementById("layoutPresetEmpty");
  const dialog = document.getElementById("layoutNameDialog");
  const form = document.getElementById("layoutNameForm");
  const nameInput = document.getElementById("layoutNameInput");
  let nameDialogMode = "create";

  const clone = (value) => value == null ? null : JSON.parse(JSON.stringify(value));
  const encodedScope = (scope) => encodeURIComponent(String(scope || "default").trim().toLowerCase());
  const presetsKey = (scope) => `dubsync.layout-presets.v1:${encodedScope(scope)}`;
  const selectedKey = (scope) => `dubsync.layout-presets-selected.v1:${encodedScope(scope)}`;

  function activeScope() {
    if (window.DUBSYNC_LAYOUT_SCOPE) return String(window.DUBSYNC_LAYOUT_SCOPE);
    const visible = [...document.querySelectorAll("[data-layout-scope]")].find((element) =>
      !element.classList.contains("hidden") && getComputedStyle(element).display !== "none");
    if (visible?.dataset.layoutScope) return visible.dataset.layoutScope;
    return location.pathname.split("/").filter(Boolean).pop() || "default";
  }

  function getAdapter(scope = activeScope()) {
    return adapters.get(String(scope)) || null;
  }

  function readPresets(scope) {
    try {
      const value = JSON.parse(localStorage.getItem(presetsKey(scope)) || "[]");
      return Array.isArray(value) ? value.filter((entry) => entry && typeof entry.id === "string" && typeof entry.name === "string" && entry.state) : [];
    } catch { return []; }
  }

  function writePresets(scope, presets) {
    try { localStorage.setItem(presetsKey(scope), JSON.stringify(presets)); return true; }
    catch { announce("Não foi possível salvar layouts neste navegador.", true); return false; }
  }

  function selectedId(scope) {
    try { return localStorage.getItem(selectedKey(scope)) || ""; } catch { return ""; }
  }

  function setSelected(scope, id) {
    try { if (id) localStorage.setItem(selectedKey(scope), id); else localStorage.removeItem(selectedKey(scope)); }
    catch { /* armazenamento local indisponível */ }
  }

  function announce(message, error = false) {
    const toast = document.getElementById("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.toggle("error", error);
    toast.classList.add("show");
    clearTimeout(announce.timer);
    announce.timer = setTimeout(() => toast.classList.remove("show", "error"), 2300);
  }

  function icon(markup) {
    const wrapper = document.createElement("span");
    wrapper.className = "layout-preset-check";
    wrapper.innerHTML = markup;
    return wrapper;
  }

  function render() {
    if (!popover) return;
    const scope = activeScope();
    const adapter = getAdapter(scope);
    const presets = readPresets(scope);
    const activeId = selectedId(scope);
    document.getElementById("layoutMenuTitle").textContent = "Layouts salvos";
    document.getElementById("layoutMenuScopeLabel").textContent = adapter?.label || scope;
    list.replaceChildren();
    for (const preset of presets) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "layout-preset-option" + (preset.id === activeId ? " selected" : "");
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(preset.id === activeId));
      row.append(icon(preset.id === activeId
        ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>'
        : '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="3"/></svg>'));
      const text = document.createElement("span");
      text.className = "layout-preset-name";
      text.textContent = preset.name;
      row.append(text);
      const timestamp = document.createElement("small");
      timestamp.textContent = preset.updatedAt ? new Date(preset.updatedAt).toLocaleDateString("pt-BR") : "";
      row.append(timestamp);
      row.addEventListener("click", () => { setSelected(scope, preset.id); render(); });
      row.addEventListener("dblclick", () => applyPreset(preset.id));
      list.append(row);
    }
    empty.hidden = presets.length > 0;
    const hasSelection = presets.some((preset) => preset.id === activeId);
    for (const id of ["layoutApply", "layoutUpdate", "layoutRename", "layoutDelete"]) {
      document.getElementById(id).disabled = !adapter || !hasSelection;
    }
    document.getElementById("layoutSaveAs").disabled = !adapter;
    document.getElementById("layoutRestoreDefault").disabled = !adapter;
  }

  function openMenu() {
    render();
    popover.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => document.getElementById("layoutSaveAs").focus());
  }

  function closeMenu() {
    popover.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  function currentState(adapter) {
    try {
      const state = JSON.parse(localStorage.getItem(adapter.storageKey) || "null");
      if (!state || typeof state !== "object") return null;
      const extras = Object.fromEntries(adapter.extraKeys.map((key) => {
        try { return [key, JSON.parse(localStorage.getItem(key) || "null")]; }
        catch { return [key, null]; }
      }));
      return { snapshotVersion: 1, primary: state, extras };
    } catch { return null; }
  }

  function applyPreset(id) {
    const scope = activeScope();
    const adapter = getAdapter(scope);
    const preset = readPresets(scope).find((entry) => entry.id === id);
    if (!adapter || !preset) return;
    try {
      const snapshot = preset.state;
      const isSnapshot = snapshot?.snapshotVersion === 1 && snapshot.primary && typeof snapshot.primary === "object";
      localStorage.setItem(adapter.storageKey, JSON.stringify(clone(isSnapshot ? snapshot.primary : snapshot)));
      if (isSnapshot) {
        for (const key of adapter.extraKeys) {
          if (!Object.prototype.hasOwnProperty.call(snapshot.extras || {}, key)) continue;
          const value = snapshot.extras[key];
          if (value == null) localStorage.removeItem(key);
          else localStorage.setItem(key, JSON.stringify(value));
        }
      }
      for (const key of adapter.legacyKeys) localStorage.removeItem(key);
      setSelected(scope, id);
      closeMenu();
      announce(`Layout “${preset.name}” aplicado.`);
      setTimeout(() => location.reload(), 180);
    } catch { announce("Não foi possível aplicar este layout.", true); }
  }

  function openNameDialog(mode) {
    const scope = activeScope();
    const preset = readPresets(scope).find((entry) => entry.id === selectedId(scope));
    nameDialogMode = mode;
    document.getElementById("layoutNameTitle").textContent = mode === "rename" ? "Renomear layout" : "Salvar layout como novo";
    document.getElementById("layoutNameDescription").textContent = mode === "rename"
      ? "Escolha um nome claro para encontrar este layout depois."
      : `Salve a organização atual dos cards de ${getAdapter(scope)?.label || "esta tela"}.`;
    document.getElementById("layoutNameConfirm").lastChild.textContent = mode === "rename" ? "Renomear" : "Salvar";
    nameInput.value = mode === "rename" ? (preset?.name || "") : "";
    dialog.showModal();
    requestAnimationFrame(() => { nameInput.focus(); nameInput.select(); });
  }

  function saveAsNew(name) {
    const scope = activeScope();
    const adapter = getAdapter(scope);
    const state = adapter && currentState(adapter);
    if (!state) { announce("O layout atual ainda não está disponível para salvar.", true); return; }
    const presets = readPresets(scope);
    if (presets.some((entry) => entry.name.localeCompare(name, "pt-BR", { sensitivity: "base" }) === 0)) {
      announce("Já existe um layout com esse nome nesta tela.", true);
      nameInput.focus();
      return;
    }
    const id = globalThis.crypto?.randomUUID?.() || `layout-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    presets.push({ id, name, state: clone(state), updatedAt: new Date().toISOString() });
    if (writePresets(scope, presets)) {
      setSelected(scope, id);
      closeNameDialog();
      render();
      announce(`Layout “${name}” salvo.`);
    }
  }

  function closeNameDialog() {
    if (dialog.open) dialog.close();
  }

  if (menu && toggle && popover) {
    toggle.addEventListener("click", () => popover.hidden ? openMenu() : closeMenu());
    document.getElementById("layoutMenuClose").addEventListener("click", closeMenu);
    document.getElementById("layoutSaveAs").addEventListener("click", () => openNameDialog("create"));
    document.getElementById("layoutApply").addEventListener("click", () => applyPreset(selectedId(activeScope())));
    document.getElementById("layoutUpdate").addEventListener("click", () => {
      const scope = activeScope();
      const adapter = getAdapter(scope);
      const state = adapter && currentState(adapter);
      const presets = readPresets(scope);
      const preset = presets.find((entry) => entry.id === selectedId(scope));
      if (!preset || !state) return;
      preset.state = clone(state);
      preset.updatedAt = new Date().toISOString();
      if (writePresets(scope, presets)) { render(); announce(`Layout “${preset.name}” atualizado.`); }
    });
    document.getElementById("layoutRename").addEventListener("click", () => openNameDialog("rename"));
    document.getElementById("layoutDelete").addEventListener("click", () => {
      const scope = activeScope();
      const preset = readPresets(scope).find((entry) => entry.id === selectedId(scope));
      if (!preset || !window.confirm(`Excluir o layout “${preset.name}” de ${getAdapter(scope)?.label || "esta tela"}?`)) return;
      const next = readPresets(scope).filter((entry) => entry.id !== preset.id);
      if (writePresets(scope, next)) { setSelected(scope, ""); render(); announce(`Layout “${preset.name}” excluído.`); }
    });
    document.getElementById("layoutRestoreDefault").addEventListener("click", () => {
      const scope = activeScope();
      const adapter = getAdapter(scope);
      if (!adapter || !window.confirm(`Restaurar o layout padrão de ${adapter.label || "esta tela"}? O layout personalizado atual será substituído.`)) return;
      try {
        for (const key of adapter.defaultKeys) localStorage.removeItem(key);
        setSelected(scope, "");
        closeMenu();
        announce(`Layout padrão de ${adapter.label || "esta tela"} restaurado.`);
        setTimeout(() => location.reload(), 180);
      } catch { announce("Não foi possível restaurar o layout padrão.", true); }
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const name = nameInput.value.trim();
      if (!name) { nameInput.focus(); return; }
      if (nameDialogMode === "create") { saveAsNew(name); return; }
      const scope = activeScope();
      const presets = readPresets(scope);
      const preset = presets.find((entry) => entry.id === selectedId(scope));
      if (!preset) return closeNameDialog();
      if (presets.some((entry) => entry.id !== preset.id && entry.name.localeCompare(name, "pt-BR", { sensitivity: "base" }) === 0)) {
        announce("Já existe um layout com esse nome nesta tela.", true);
        nameInput.focus();
        return;
      }
      preset.name = name;
      preset.updatedAt = new Date().toISOString();
      if (writePresets(scope, presets)) { closeNameDialog(); render(); announce("Nome do layout atualizado."); }
    });
    document.getElementById("layoutNameCancel").addEventListener("click", closeNameDialog);
    document.addEventListener("pointerdown", (event) => { if (!menu.contains(event.target)) closeMenu(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); });
  }

  window.DubSyncLayoutPresets = {
    register(adapter) {
      if (!adapter?.scope || !adapter.storageKey) return;
      adapters.set(String(adapter.scope), {
        ...adapter,
        legacyKeys: Array.isArray(adapter.legacyKeys) ? adapter.legacyKeys : [],
        extraKeys: Array.isArray(adapter.extraKeys) ? adapter.extraKeys : [],
        defaultKeys: Array.isArray(adapter.defaultKeys) ? adapter.defaultKeys : [adapter.storageKey],
      });
      render();
    },
    storageKey(scope, version = 1) { return `dubsync.docks.${encodedScope(scope)}.v${Math.max(1, Number(version) || 1)}`; },
    setActiveScope(scope) { window.DUBSYNC_LAYOUT_SCOPE = String(scope || ""); render(); },
    refresh: render,
  };
})();
