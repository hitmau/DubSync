/* Encaixe local de painéis: a posição e as preferências de exibição nunca alteram as sessões. */
(() => {
  const storageKey = "dubsync.docks.v1";
  const defaults = {
    left: [["sourcePanel", 2], ["roiPanel", .75], ["sessionPanel", 1], ["dialogueStatusPanel", .72]],
    center: [["videoOverview", .22], ["previewPanel", 1.35], ["timelinePanel", .52], ["eventList", .88]],
    right: [["processing", .66], ["eventDetailPanel", 1]],
    bottom: [["review", 1]],
    float: [],
  };
  const zoneElements = {
    left: document.querySelector(".left-column"),
    center: document.querySelector(".center-column"),
    right: document.querySelector(".right-column"),
    bottom: document.querySelector("#dockBottom"),
    float: document.querySelector("#dockFloatLayer"),
  };
  document.querySelector(".preview-panel").id = "previewPanel";
  document.querySelector(".timeline-panel").id = "timelinePanel";
  document.querySelector(".event-detail-panel").id = "eventDetailPanel";
  const ids = Object.values(defaults).flat().map(([id]) => id);
  const panels = new Map(ids.map((id) => [id, document.getElementById(id)]));
  const names = new Map(ids.map((id) => [id, panels.get(id).querySelector("h2")?.firstChild?.textContent?.trim() || id]));
  let draggingId = null;
  let highlightedGroup = null;
  let resizeState = null;
  let floatMove = null;
  let floatFront = 30;

  function safeRead() {
    try {
      const state = JSON.parse(localStorage.getItem(storageKey) || "null");
      if (!state || typeof state !== "object" || !state.zones) return null;
      const found = [];
      for (const zone of Object.keys(zoneElements)) {
        const entries = zone === "float" && state.zones[zone] == null ? [] : state.zones[zone];
        if (!Array.isArray(entries)) return null;
        for (const group of entries) {
          if (!Array.isArray(group.panels) || !group.panels.length) return null;
          found.push(...group.panels);
        }
      }
      const hasOnlyKnownPanels = new Set(found).size === found.length && found.every((id) => panels.has(id));
      if (hasOnlyKnownPanels && found.length < ids.length) {
        for (const missingId of ids.filter((id) => !found.includes(id))) {
          const home = Object.entries(defaults).find(([, entries]) => entries.some(([id]) => id === missingId));
          if (!home) return null;
          const [zoneName, orderedDefaults] = home;
          const groups = state.zones[zoneName];
          const defaultIndex = orderedDefaults.findIndex(([id]) => id === missingId);
          const nextId = orderedDefaults.slice(defaultIndex + 1).map(([id]) => id).find((id) => found.includes(id));
          const previousId = orderedDefaults.slice(0, defaultIndex).reverse().map(([id]) => id).find((id) => found.includes(id));
          const nextGroup = nextId && groups.findIndex((group) => group.panels.includes(nextId));
          const previousGroup = previousId && groups.findIndex((group) => group.panels.includes(previousId));
          const insertionIndex = nextGroup >= 0 ? nextGroup : previousGroup >= 0 ? previousGroup + 1 : groups.length;
          const [, weight] = orderedDefaults[defaultIndex];
          groups.splice(insertionIndex, 0, { panels: [missingId], active: missingId, weight });
          state.homes = { ...(state.homes || {}), [missingId]: zoneName };
          found.push(missingId);
        }
        try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* private browsing */ }
      }
      if (found.length !== ids.length || new Set(found).size !== ids.length || found.some((id) => !panels.has(id))) return null;
      if (state.collapsed && (!Array.isArray(state.collapsed) || state.collapsed.some((id) => !panels.has(id)))) return null;
      return state;
    } catch { return null; }
  }

  function save() {
    const zones = {};
    for (const [name, zone] of Object.entries(zoneElements)) {
      zones[name] = [...zone.querySelectorAll(":scope > .dock-group")].map((group) => {
        const result = {
          panels: [...group.querySelectorAll(":scope > .panel")].map((panel) => panel.id),
          active: group.dataset.active,
          weight: Number(group.style.getPropertyValue("--dock-weight")) || 1,
        };
        if (name === "float") result.geometry = {
          x: parseFloat(group.style.left) || 0, y: parseFloat(group.style.top) || 0,
          width: parseFloat(group.style.width) || 500, height: parseFloat(group.style.height) || 350,
        };
        return result;
      });
    }
    const collapsed = ids.filter((id) => panels.get(id).classList.contains("dock-collapsed"));
    const homes = Object.fromEntries(ids.map((id) => [id, panels.get(id).dataset.dockHome || "center"]));
    try { localStorage.setItem(storageKey, JSON.stringify({ zones, collapsed, homes })); } catch { /* private browsing */ }
  }

  function panelHeader(panel) {
    return panel.querySelector(":scope > .panel-title, :scope > .timeline-heading, :scope > .table-heading, :scope > .review-dock-title");
  }

  function clearDropHint() {
    if (highlightedGroup) highlightedGroup.classList.remove("dock-target-before", "dock-target-after", "dock-target-tab");
    highlightedGroup = null;
    Object.values(zoneElements).forEach((zone) => zone.classList.remove("dock-over"));
  }

  function afterLayout() {
    const grid = document.getElementById("appGrid");
    const bottomGroups = groupChildren(zoneElements.bottom);
    grid.classList.toggle("no-bottom", bottomGroups.length === 0);
    grid.classList.toggle("bottom-compact", bottomGroups.length > 0 && bottomGroups.every((group) => group.classList.contains("dock-compact")));
    for (const [id, panel] of panels) {
      const button = panel.querySelector(":scope > * .dock-float-toggle");
      if (!button) continue;
      const floating = panel.parentElement?.parentElement === zoneElements.float;
      button.textContent = floating ? "▣" : "▢";
      button.title = floating ? `Encaixar ${names.get(id)} novamente` : `Destacar ${names.get(id)} como quadro flutuante`;
      button.setAttribute("aria-label", button.title);
    }
    requestAnimationFrame(() => {
      if (typeof layoutSplitters === "function") layoutSplitters();
      if (typeof drawPreview === "function") drawPreview();
      if (typeof renderTimeline === "function") renderTimeline();
    });
  }

  function showPanel(group, id, persist = true) {
    const all = [...group.querySelectorAll(":scope > .panel")];
    if (!all.some((panel) => panel.id === id)) id = all[0]?.id;
    if (!id) return;
    group.dataset.active = id;
    for (const panel of all) panel.classList.toggle("dock-active", panel.id === id);
    group.classList.toggle("dock-compact", panels.get(id).classList.contains("dock-collapsed"));
    const tabs = group.querySelector(":scope > .dock-tabs");
    tabs.replaceChildren();
    if (all.length > 1) {
      for (const panel of all) {
        const tab = document.createElement("button");
        tab.type = "button";
        tab.className = `dock-tab${panel.id === id ? " active" : ""}`;
        tab.textContent = names.get(panel.id);
        tab.title = `Exibir ${names.get(panel.id)}; arraste para mover o painel`;
        tab.dataset.panelId = panel.id;
        tab.draggable = true;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(panel.id === id));
        tab.addEventListener("click", () => { showPanel(group, panel.id); rebuildDividers(group.parentElement); });
        tab.addEventListener("dragstart", startDrag);
        tabs.append(tab);
      }
    }
    if (persist) { save(); afterLayout(); }
  }

  function makeGroup(panelIds, active, weight = 1) {
    const group = document.createElement("div");
    group.className = "dock-group";
    group.style.setProperty("--dock-weight", String(Math.max(.2, Math.min(10000, Number(weight) || 1))));
    const tabs = document.createElement("div");
    tabs.className = "dock-tabs";
    tabs.setAttribute("role", "tablist");
    group.append(tabs);
    for (const id of panelIds) group.append(panels.get(id));
    showPanel(group, active || panelIds[0], false);
    return group;
  }

  function groupChildren(zone) { return [...zone.children].filter((child) => child.classList.contains("dock-group")); }
  function groupSize(group, horizontal) { const rect = group.getBoundingClientRect(); return horizontal ? rect.width : rect.height; }

  function positionFloat(group, geometry = {}) {
    const grid = document.getElementById("appGrid");
    const maxWidth = Math.max(280, grid.clientWidth - 12), maxHeight = Math.max(160, grid.clientHeight - 12);
    const width = Math.min(maxWidth, Math.max(280, Number(geometry.width) || 500));
    const height = Math.min(maxHeight, Math.max(160, Number(geometry.height) || 350));
    group.style.width = `${width}px`;
    group.style.height = `${height}px`;
    group.style.left = `${Math.max(0, Math.min(grid.clientWidth - width, Number(geometry.x) || 0))}px`;
    group.style.top = `${Math.max(0, Math.min(grid.clientHeight - (group.classList.contains("dock-compact") ? 32 : height), Number(geometry.y) || 0))}px`;
    group.style.zIndex = String(++floatFront);
  }

  function syncFloatingGroup(group) {
    const floating = group.parentElement === zoneElements.float;
    group.classList.toggle("dock-floating", floating);
    for (const panel of group.querySelectorAll(":scope > .panel")) panelHeader(panel).draggable = !floating;
    if (floating && !group.querySelector(":scope > .float-resize")) {
      const grip = document.createElement("button");
      grip.type = "button";
      grip.className = "float-resize";
      grip.title = "Redimensionar quadro flutuante";
      grip.setAttribute("aria-label", "Redimensionar quadro flutuante");
      group.append(grip);
    }
  }

  function floatPointerDown(event) {
    if (event.button !== 0) return;
    const group = event.target.closest(".dock-floating");
    if (!group || group.parentElement !== zoneElements.float) return;
    group.style.zIndex = String(++floatFront);
    const grip = event.target.closest(".float-resize");
    const header = event.target.closest(".panel-title,.timeline-heading,.table-heading,.review-dock-title");
    if (!grip && (!header || event.target.closest("button,input,select,textarea"))) return;
    event.preventDefault();
    const handle = grip || header;
    handle.setPointerCapture(event.pointerId);
    floatMove = {
      handle, group, resizing: Boolean(grip), x: event.clientX, y: event.clientY,
      left: parseFloat(group.style.left) || 0, top: parseFloat(group.style.top) || 0,
      width: parseFloat(group.style.width) || group.offsetWidth,
      height: parseFloat(group.style.height) || group.offsetHeight,
    };
    document.body.classList.add("dock-dragging");
  }

  function floatPointerMove(event) {
    if (!floatMove) return;
    const state = floatMove;
    const grid = document.getElementById("appGrid");
    const dx = event.clientX - state.x, dy = event.clientY - state.y;
    if (state.resizing) {
      state.group.style.width = `${Math.max(280, Math.min(grid.clientWidth - state.left, state.width + dx))}px`;
      state.group.style.height = `${Math.max(160, Math.min(grid.clientHeight - state.top, state.height + dy))}px`;
    } else {
      const height = state.group.classList.contains("dock-compact") ? state.group.getBoundingClientRect().height : state.height;
      state.group.style.left = `${Math.max(0, Math.min(grid.clientWidth - state.width, state.left + dx))}px`;
      state.group.style.top = `${Math.max(0, Math.min(grid.clientHeight - height, state.top + dy))}px`;
    }
    if (typeof drawPreview === "function") requestAnimationFrame(drawPreview);
  }

  function floatPointerUp(event) {
    if (!floatMove || (event.target !== floatMove.handle && !floatMove.handle.contains(event.target))) return;
    floatMove = null;
    document.body.classList.remove("dock-dragging");
    save(); afterLayout();
  }

  function endResize(divider) {
    if (!resizeState || resizeState.divider !== divider) return;
    const { zone, groups, horizontal } = resizeState;
    for (const group of groups) {
      if (!group.classList.contains("dock-compact")) {
        const size = groupSize(group, horizontal);
        group.style.flex = "";
        group.style.setProperty("--dock-weight", String(Math.max(1, size)));
      }
    }
    divider.classList.remove("dragging");
    document.body.classList.remove("layout-resizing", "resize-x", "resize-y");
    resizeState = null;
    save(); afterLayout();
  }

  function makeDivider(zone, before, after) {
    const horizontal = zone === zoneElements.bottom;
    const divider = document.createElement("button");
    divider.type = "button";
    divider.className = `dock-divider ${horizontal ? "horizontal" : "vertical"}`;
    divider.title = "Arraste para ajustar o tamanho dos quadros";
    divider.setAttribute("aria-label", `Redimensionar ${names.get(before.dataset.active)} e ${names.get(after.dataset.active)}`);
    divider.innerHTML = `<span aria-hidden="true">${horizontal ? "↔" : "↕"}</span>`;
    divider.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const groups = groupChildren(zone).filter((group) => !group.classList.contains("dock-compact"));
      const sizes = new Map(groups.map((group) => [group, groupSize(group, horizontal)]));
      const a = sizes.get(before), b = sizes.get(after);
      if (!a || !b) return;
      event.preventDefault();
      for (const group of groups) group.style.flex = `0 0 ${sizes.get(group)}px`;
      resizeState = { divider, zone, groups, horizontal, before, after, start: horizontal ? event.clientX : event.clientY, a, b };
      divider.classList.add("dragging");
      document.body.classList.add("layout-resizing", horizontal ? "resize-x" : "resize-y");
      divider.setPointerCapture(event.pointerId);
    });
    divider.addEventListener("pointermove", (event) => {
      if (!resizeState || resizeState.divider !== divider) return;
      const state = resizeState;
      const delta = (state.horizontal ? event.clientX : event.clientY) - state.start;
      const min = state.horizontal ? 160 : 75;
      const next = Math.max(min, Math.min(state.a + state.b - min, state.a + delta));
      state.before.style.flex = `0 0 ${next}px`;
      state.after.style.flex = `0 0 ${state.a + state.b - next}px`;
      if (typeof layoutSplitters === "function") requestAnimationFrame(layoutSplitters);
    });
    divider.addEventListener("pointerup", () => endResize(divider));
    divider.addEventListener("pointercancel", () => endResize(divider));
    return divider;
  }

  function rebuildDividers(zone) {
    zone.querySelectorAll(":scope > .dock-divider").forEach((divider) => divider.remove());
    const groups = groupChildren(zone);
    for (const group of groups) syncFloatingGroup(group);
    if (zone !== zoneElements.float) {
      for (let i = 1; i < groups.length; i++) {
        if (!groups[i - 1].classList.contains("dock-compact") && !groups[i].classList.contains("dock-compact")) {
          groups[i].before(makeDivider(zone, groups[i - 1], groups[i]));
        }
      }
    }
    afterLayout();
  }

  function startDrag(event) {
    if (event.target.closest("button:not(.dock-tab),input,select,textarea")) { event.preventDefault(); return; }
    const id = event.currentTarget.dataset.panelId;
    if (!panels.has(id)) return;
    draggingId = id;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", id);
    document.body.classList.add("dock-dragging");
  }

  function placement(event, zone, group) {
    if (!group) return "append";
    if (event.target.closest(".dock-tabs")) return "tab";
    const rect = group.getBoundingClientRect();
    const ratio = zone === zoneElements.bottom ? (event.clientX - rect.left) / rect.width : (event.clientY - rect.top) / rect.height;
    return ratio < .23 ? "before" : ratio > .77 ? "after" : "tab";
  }

  function movePanel(id, zone, target, place, geometry = null) {
    const panel = panels.get(id);
    const sourceGroup = panel.parentElement;
    if (target === sourceGroup && (place === "tab" || sourceGroup.querySelectorAll(":scope > .panel").length === 1)) {
      showPanel(sourceGroup, id); return;
    }
    const sourceZone = sourceGroup.parentElement;
    const sourceName = Object.entries(zoneElements).find(([, element]) => element === sourceZone)?.[0];
    const destinationName = Object.entries(zoneElements).find(([, element]) => element === zone)?.[0];
    const sourceRect = panel.getBoundingClientRect();
    const gridRect = document.getElementById("appGrid").getBoundingClientRect();
    if (zone === zoneElements.float && !geometry) geometry = {
      x: sourceRect.left - gridRect.left + 24, y: sourceRect.top - gridRect.top + 18,
      width: Math.min(920, Math.max(id === "review" ? 760 : 350, sourceRect.width)),
      height: Math.min(700, Math.max(id === "review" ? 190 : 260, sourceRect.height)),
    };
    if (sourceName && sourceName !== "float" && destinationName === "float") panel.dataset.dockHome = sourceName;
    else if (destinationName && destinationName !== "float") panel.dataset.dockHome = destinationName;
    const player = id === "previewPanel" ? document.getElementById("videoPlayer") : null;
    const playback = player ? { time: player.currentTime, playing: !player.paused } : null;
    panel.remove();
    const remaining = [...sourceGroup.querySelectorAll(":scope > .panel")];
    if (remaining.length) showPanel(sourceGroup, sourceGroup.dataset.active === id ? remaining[0].id : sourceGroup.dataset.active, false);
    else sourceGroup.remove();
    if (place === "tab" && target?.isConnected) {
      target.append(panel);
      showPanel(target, id, false);
    } else {
      const group = makeGroup([id], id, target?.style.getPropertyValue("--dock-weight") || 1);
      if (target?.isConnected && place === "before") target.before(group);
      else if (target?.isConnected && place === "after") target.after(group);
      else zone.append(group);
      if (zone === zoneElements.float) positionFloat(group, geometry);
    }
    rebuildDividers(sourceZone);
    if (zone !== sourceZone) rebuildDividers(zone);
    save();
    if (playback) requestAnimationFrame(() => {
      if (Number.isFinite(playback.time) && Math.abs(player.currentTime - playback.time) > .1) player.currentTime = playback.time;
      if (playback.playing && player.paused) player.play().catch(() => {});
    });
  }

  function handleDragOver(event) {
    if (!draggingId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearDropHint();
    const zone = event.currentTarget;
    zone.classList.add("dock-over");
    const group = event.target.closest(".dock-group");
    if (group && group.parentElement === zone) {
      highlightedGroup = group;
      group.classList.add(`dock-target-${placement(event, zone, group)}`);
    }
  }

  function handleDrop(event) {
    if (!draggingId) return;
    event.preventDefault();
    const zone = event.currentTarget;
    const group = event.target.closest(".dock-group");
    const target = group?.parentElement === zone ? group : null;
    const place = placement(event, zone, target);
    const id = draggingId;
    clearDropHint();
    movePanel(id, zone, target, place);
    draggingId = null;
    document.body.classList.remove("dock-dragging");
  }

  function toggleFloat(id) {
    const panel = panels.get(id);
    const sourceZone = panel.parentElement.parentElement;
    if (sourceZone === zoneElements.float) {
      const home = zoneElements[panel.dataset.dockHome] || zoneElements.center;
      movePanel(id, home, null, "append");
    } else {
      movePanel(id, zoneElements.float, null, "append");
    }
  }

  function initialize() {
    const grid = document.getElementById("appGrid");
    const restored = safeRead();
    for (const [name, zone] of Object.entries(zoneElements)) {
      zone.classList.add("dock-zone");
      zone.replaceChildren();
      zone.addEventListener("dragover", handleDragOver);
      zone.addEventListener("drop", handleDrop);
      const groups = restored ? (restored.zones[name] || []) : defaults[name].map(([id, weight]) => ({ panels: [id], active: id, weight }));
      for (const group of groups) {
        const node = makeGroup(group.panels, group.active, group.weight);
        zone.append(node);
        if (name === "float") positionFloat(node, group.geometry);
      }
    }
    for (const id of ids) {
      const panel = panels.get(id);
      const defaultHome = Object.entries(defaults).find(([, groups]) => groups.some(([panelId]) => panelId === id))?.[0] || "center";
      panel.dataset.dockHome = zoneElements[restored?.homes?.[id]] && restored.homes[id] !== "float" ? restored.homes[id] : defaultHome;
      const header = panelHeader(panel);
      const actions = document.createElement("div");
      actions.className = "dock-header-actions";
      for (const child of [...header.children]) {
        if (child.tagName !== "H2" && !(header.classList.contains("review-dock-title") && child === header.firstElementChild)) actions.append(child);
      }
      header.append(actions);
      const floatButton = document.createElement("button");
      floatButton.type = "button";
      floatButton.className = "dock-float-toggle";
      floatButton.addEventListener("click", (event) => { event.stopPropagation(); toggleFloat(id); });
      actions.append(floatButton);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "dock-collapse";
      button.setAttribute("aria-label", `Recolher ${names.get(id)}`);
      button.setAttribute("aria-expanded", "true");
      button.title = `Recolher ${names.get(id)}`;
      button.textContent = "−";
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const collapsed = panel.classList.toggle("dock-collapsed");
        button.textContent = collapsed ? "+" : "−";
        button.setAttribute("aria-label", `${collapsed ? "Expandir" : "Recolher"} ${names.get(id)}`);
        button.setAttribute("aria-expanded", String(!collapsed));
        button.title = button.getAttribute("aria-label");
        const group = panel.parentElement;
        showPanel(group, id, false);
        rebuildDividers(group.parentElement);
        save();
      });
      actions.append(button);
      header.draggable = true;
      header.dataset.panelId = id;
      header.title = `Arraste para mover ${names.get(id)}. Solte na borda para separar ou no centro para criar abas.`;
      header.addEventListener("dragstart", startDrag);
      header.addEventListener("dragend", () => { draggingId = null; clearDropHint(); document.body.classList.remove("dock-dragging"); });
    }
    for (const id of restored?.collapsed || []) {
      const panel = panels.get(id);
      panel.classList.add("dock-collapsed");
      const button = panel.querySelector(":scope > * .dock-collapse");
      button.textContent = "+";
      button.setAttribute("aria-label", `Expandir ${names.get(id)}`);
      button.setAttribute("aria-expanded", "false");
      button.title = button.getAttribute("aria-label");
    }
    grid.classList.add("dock-enabled");
    zoneElements.float.addEventListener("pointerdown", floatPointerDown);
    zoneElements.float.addEventListener("pointermove", floatPointerMove);
    zoneElements.float.addEventListener("pointerup", floatPointerUp);
    zoneElements.float.addEventListener("pointercancel", floatPointerUp);
    for (const zone of Object.values(zoneElements)) {
      for (const group of groupChildren(zone)) showPanel(group, group.dataset.active, false);
      rebuildDividers(zone);
    }
    document.querySelectorAll(".nav-item[data-anchor]").forEach((button) => button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.anchor);
      const panel = target?.classList.contains("panel") ? target : target?.querySelector(".panel");
      if (panel?.parentElement?.classList.contains("dock-group")) showPanel(panel.parentElement, panel.id);
    }, true));
    afterLayout();
    save();
    window.DubSyncLayoutPresets?.register({
      scope: "capture",
      label: "Captura",
      storageKey,
      extraKeys: ["dubsync.layout.v1", "dubsync.eventTableLayout.v1"],
      defaultKeys: [storageKey, "dubsync.layout.v1", "dubsync.eventTableLayout.v1"],
    });
  }

  initialize();
})();
