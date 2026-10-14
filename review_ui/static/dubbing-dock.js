/* Dock flexível da tela de Dublagem: árvore de divisões, abas e painéis flutuantes. */
(() => {
  const grid = document.getElementById("dubGrid");
  if (!grid) return;

  const storageKey = "dubsync.dub.docks.v4";
  const legacyStorageKeys = ["dubsync.dub.docks.v3", "dubsync.dub.docks.v2", "dubsync.dub.docks.v1"];
  const panelIds = [
    "dubVideoOverview", "dubCharactersPanel", "dubLinesPanel", "dubTimelinePanel",
    "dubSyncPanel", "dubStudioPanel", "dubProductionPanel",
  ];
  const panels = new Map(panelIds.map((id) => [id, document.getElementById(id)]));
  const names = new Map(panelIds.map((id) => {
    const panel = panels.get(id);
    return [id, panel?.querySelector("h2")?.textContent.trim() || id];
  }));
  const floatLayer = document.getElementById("dubDockFloatLayer");
  const legacyZones = ["dubDockLeft", "dubDockCenter", "dubDockTimeline", "dubDockExtra", "dubDockRight"]
    .map((id) => document.getElementById(id)).filter(Boolean);
  const legacyDividers = ["dubDockLeftDivider", "dubDockCenterDivider", "dubDockRightDivider", "dubDockRowDivider"]
    .map((id) => document.getElementById(id)).filter(Boolean);
  const root = document.createElement("div");
  root.id = "dubDockRoot";
  root.className = "dub-dock-root";
  root.setAttribute("aria-label", "Área de painéis encaixáveis da Dublagem");
  grid.prepend(root);

  let layoutTree = null;
  let draggingId = null;
  let resizeState = null;
  let floatMove = null;
  let floatFront = 30;

  function isLocked(id) { return panels.get(id)?.classList.contains("dock-locked") || false; }

  function updateLockControl(panel) {
    if (!panel) return;
    const locked = isLocked(panel.id);
    const button = panel.querySelector(":scope > * .dock-lock-toggle");
    if (button) {
      const label = locked ? "Permitir mover " + names.get(panel.id) : "Impedir mover " + names.get(panel.id);
      button.innerHTML = locked
        ? '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>'
        : '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
      button.title = label;
      button.setAttribute("aria-label", label);
      button.setAttribute("aria-pressed", String(locked));
    }
    const header = panelHeader(panel);
    if (header) header.draggable = !locked && !panel.closest(".dock-floating");
    document.querySelectorAll('.dock-tab[data-panel-id="' + panel.id + '"]').forEach((tab) => { tab.draggable = !locked; });
  }

  function setPanelLocked(panel, locked) {
    if (!panel) return;
    panel.classList.toggle("dock-locked", locked);
    updateLockControl(panel);
    save();
    afterLayout();
  }

  function makeGroupNode(ids, active) {
    return { type: "group", panelIds: [...ids], active: active || ids[0], element: null };
  }

  function makeSplit(axis, first, second, ratio) {
    return { type: "split", axis, ratio: Math.max(.05, Math.min(.95, Number(ratio) || .5)), children: [first, second] };
  }

  function defaultLayout() {
    const overview = makeGroupNode(["dubVideoOverview"]);
    const characters = makeGroupNode(["dubCharactersPanel"]);
    const lines = makeGroupNode(["dubLinesPanel"]);
    const studio = makeGroupNode(["dubStudioPanel"]);
    const production = makeGroupNode(["dubProductionPanel"]);
    const videoTimeline = makeGroupNode(["dubTimelinePanel"]);
    const syncedPreview = makeGroupNode(["dubSyncPanel"]);
    const rightStack = makeSplit("y", studio, production, .56);
    const middle = makeSplit("x", lines, rightStack, .70);
    const top = makeSplit("x", characters, middle, .21);
    const bottom = makeSplit("x", videoTimeline, syncedPreview, .62);
    const workspace = makeSplit("y", top, bottom, .64);
    return makeSplit("y", overview, workspace, .16);
  }

  function collectPanelIds(node, result = []) {
    if (!node) return result;
    if (node.type === "group") result.push(...node.panelIds);
    else node.children.forEach((child) => collectPanelIds(child, result));
    return result;
  }

  function firstGroup(node) {
    if (!node) return null;
    if (node.type === "group") return node;
    return firstGroup(node.children[0]) || firstGroup(node.children[1]);
  }

  function firstPanelId(node) {
    return firstGroup(node)?.panelIds[0] || null;
  }

  function containsGroup(node, group) {
    if (!node) return false;
    if (node.type === "group") return node.element === group;
    return node.children.some((child) => containsGroup(child, group));
  }

  function findGroup(node, group) {
    if (!node) return null;
    if (node.type === "group") return node.element === group ? node : null;
    return findGroup(node.children[0], group) || findGroup(node.children[1], group);
  }

  function findGroupForPanel(node, panelId) {
    if (!node) return null;
    if (node.type === "group") return node.panelIds.includes(panelId) ? node : null;
    return findGroupForPanel(node.children[0], panelId) || findGroupForPanel(node.children[1], panelId);
  }

  function replaceGroup(node, group, replacement) {
    if (!node) return null;
    if (node.type === "group") return node.element === group ? replacement : node;
    const first = replaceGroup(node.children[0], group, replacement);
    const second = replaceGroup(node.children[1], group, replacement);
    if (first === node.children[0] && second === node.children[1]) return node;
    node.children = [first, second];
    if (!first) return second;
    if (!second) return first;
    return node;
  }

  function removeGroup(node, group) {
    if (!node) return null;
    if (node.type === "group") return node.element === group ? null : node;
    const first = removeGroup(node.children[0], group);
    const second = removeGroup(node.children[1], group);
    if (!first) return second;
    if (!second) return first;
    node.children = [first, second];
    return node;
  }

  function removeGroupNode(node, targetNode) {
    if (!node) return null;
    if (node === targetNode) return null;
    if (node.type === "group") return node;
    const first = removeGroupNode(node.children[0], targetNode);
    const second = removeGroupNode(node.children[1], targetNode);
    if (!first) return second;
    if (!second) return first;
    node.children = [first, second];
    return node;
  }

  function groupPanelElements(group) {
    return [...group.children].filter((child) => child.classList.contains("panel"));
  }

  function setPanelCollapsed(panel, collapsed) {
    if (!panel) return;
    panel.classList.toggle("dock-collapsed", collapsed);
    const button = panel.querySelector(":scope > * .dock-collapse");
    if (!button) return;
    button.textContent = collapsed ? "+" : "−";
    button.setAttribute("aria-label", (collapsed ? "Expandir " : "Recolher ") + names.get(panel.id));
    button.setAttribute("aria-expanded", String(!collapsed));
    button.title = button.getAttribute("aria-label");
  }

  function panelHeader(panel) {
    return panel.querySelector(":scope > .panel-title, :scope > .dub-panel-heading");
  }

  function showPanel(group, id, persist) {
    const all = groupPanelElements(group);
    if (!all.some((panel) => panel.id === id)) id = all[0]?.id;
    if (!id) return;
    const expanded = all.filter((panel) => !panel.classList.contains("dock-collapsed"));
    if (panels.get(id)?.classList.contains("dock-collapsed") && expanded.length) id = expanded[0].id;
    const allCollapsed = all.length > 0 && expanded.length === 0;
    group.dataset.active = id;
    group.classList.toggle("dock-compact", allCollapsed);
    group.classList.toggle("dock-all-collapsed", allCollapsed);
    for (const panel of all) panel.classList.toggle("dock-active", panel.id === id);
    const tabs = group.querySelector(":scope > .dock-tabs");
    tabs.replaceChildren();
    if (all.length > 1) {
      for (const panel of all) {
        const tab = document.createElement("button");
        tab.type = "button";
        tab.className = "dock-tab" + (panel.id === id ? " active" : "");
        tab.textContent = names.get(panel.id);
        tab.title = "Exibir " + names.get(panel.id) + "; arraste para mover o painel";
        const isCollapsed = panel.classList.contains("dock-collapsed");
        tab.classList.toggle("dock-tab-minimized", isCollapsed);
        if (isCollapsed) tab.title = "Restaurar " + names.get(panel.id);
        tab.dataset.panelId = panel.id;
        tab.draggable = !isLocked(panel.id);
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(panel.id === id));
        tab.addEventListener("click", () => {
          if (panel.classList.contains("dock-collapsed")) setPanelCollapsed(panel, false);
          showPanel(group, panel.id, true);
        });
        tab.addEventListener("dragstart", startDrag);
        tabs.append(tab);
      }
    }
    if (persist) {
      save();
      afterLayout();
    }
  }

  function makeGroupElement(node) {
    const group = document.createElement("div");
    group.className = "dock-group";
    const tabs = document.createElement("div");
    tabs.className = "dock-tabs";
    tabs.setAttribute("role", "tablist");
    group.append(tabs);
    node.panelIds.forEach((id) => group.append(panels.get(id)));
    node.element = group;
    showPanel(group, node.active, false);
    return group;
  }

  function setSplitWeights(splitElement, ratio) {
    const cells = splitElement.querySelectorAll(":scope > .dock-cell");
    if (cells.length !== 2) return;
    cells[0].style.flex = String(ratio) + " 1 0px";
    cells[1].style.flex = String(1 - ratio) + " 1 0px";
    splitElement.dataset.ratio = String(ratio);
  }

  function collapsedFootprint(node, axis) {
    if (!node) return null;
    if (node.type === "group") {
      const fullyCollapsed = node.panelIds.length > 0 && node.panelIds.every((id) =>
        panels.get(id)?.classList.contains("dock-collapsed"));
      if (!fullyCollapsed) return null;
      return axis === "x" ? 34 : 32;
    }
    const first = collapsedFootprint(node.children[0], axis);
    const second = collapsedFootprint(node.children[1], axis);
    if (first == null || second == null) return null;
    return node.axis === axis ? first + 8 + second : Math.max(first, second);
  }

  function applySplitWeights(splitElement, node) {
    const cells = splitElement.querySelectorAll(":scope > .dock-cell");
    if (cells.length !== 2) return;
    const firstSize = collapsedFootprint(node.children[0], node.axis);
    const secondSize = collapsedFootprint(node.children[1], node.axis);
    if (firstSize == null && secondSize == null) {
      setSplitWeights(splitElement, node.ratio);
      return;
    }
    cells[0].style.flex = firstSize == null ? "1 1 0px" : "0 0 " + firstSize + "px";
    cells[1].style.flex = secondSize == null ? "1 1 0px" : "0 0 " + secondSize + "px";
    splitElement.dataset.ratio = String(node.ratio);
  }

  function makeDivider(node, splitElement) {
    const divider = document.createElement("button");
    divider.type = "button";
    divider.className = "dock-divider " + (node.axis === "x" ? "horizontal" : "vertical");
    divider.setAttribute("role", "separator");
    divider.setAttribute("aria-orientation", node.axis === "x" ? "vertical" : "horizontal");
    divider.setAttribute("aria-valuemin", "10");
    divider.setAttribute("aria-valuemax", "90");
    divider.setAttribute("aria-valuenow", String(Math.round(node.ratio * 100)));
    divider.title = "Arraste para ajustar os dois painéis";
    divider.setAttribute("aria-label", "Redimensionar os painéis vizinhos");
    divider.innerHTML = "<span aria-hidden=\"true\">" + (node.axis === "x" ? "↔" : "↕") + "</span>";

    function resizeBy(delta) {
      const rect = splitElement.getBoundingClientRect();
      const span = Math.max(1, (node.axis === "x" ? rect.width : rect.height) - 8);
      node.ratio = Math.max(.1, Math.min(.9, node.ratio + delta / span));
      applySplitWeights(splitElement, node);
      divider.setAttribute("aria-valuenow", String(Math.round(node.ratio * 100)));
    }
    divider.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      resizeState = { node, splitElement, divider, start: node.axis === "x" ? event.clientX : event.clientY, ratio: node.ratio };
      divider.classList.add("dragging");
      document.body.classList.add("layout-resizing", node.axis === "x" ? "resize-x" : "resize-y");
      divider.setPointerCapture(event.pointerId);
    });
    divider.addEventListener("pointermove", (event) => {
      if (!resizeState || resizeState.divider !== divider) return;
      const rect = splitElement.getBoundingClientRect();
      const length = Math.max(1, (node.axis === "x" ? rect.width : rect.height) - 8);
      const delta = (node.axis === "x" ? event.clientX : event.clientY) - resizeState.start;
      node.ratio = Math.max(.1, Math.min(.9, resizeState.ratio + delta / length));
      applySplitWeights(splitElement, node);
      divider.setAttribute("aria-valuenow", String(Math.round(node.ratio * 100)));
    });
    const finish = () => {
      if (!resizeState || resizeState.divider !== divider) return;
      resizeState = null;
      divider.classList.remove("dragging");
      document.body.classList.remove("layout-resizing", "resize-x", "resize-y");
      save();
      afterLayout();
    };
    divider.addEventListener("pointerup", finish);
    divider.addEventListener("pointercancel", finish);
    divider.addEventListener("keydown", (event) => {
      const relevant = node.axis === "x" ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
      if (!relevant.includes(event.key)) return;
      event.preventDefault();
      const positive = node.axis === "x" ? event.key === "ArrowRight" : event.key === "ArrowDown";
      resizeBy((positive ? 1 : -1) * (event.shiftKey ? 36 : 16));
      save();
    });
    return divider;
  }

  function buildNode(node, collapseAxis = "y") {
    if (node.type === "group") {
      const visibleIds = node.panelIds.filter((id) => panels.has(id));
      if (!visibleIds.length) return null;
      if (!node.element) {
        node.element = document.createElement("div");
        node.element.className = "dock-group";
      }
      const current = groupPanelElements(node.element).map((panel) => panel.id);
      if (current.join("|") !== visibleIds.join("|")) {
        node.element.replaceChildren();
        const tabs = document.createElement("div");
        tabs.className = "dock-tabs";
        tabs.setAttribute("role", "tablist");
        node.element.append(tabs);
        visibleIds.forEach((id) => node.element.append(panels.get(id)));
      }
      const active = visibleIds.includes(node.element.dataset.active) ? node.element.dataset.active
        : visibleIds.includes(node.active) ? node.active : visibleIds[0];
      showPanel(node.element, active, false);
      for (const id of visibleIds) panels.get(id)?.classList.toggle("dock-collapse-vertical", collapseAxis === "x");
      return node.element;
    }
    const firstNode = buildNode(node.children[0], node.axis);
    const secondNode = buildNode(node.children[1], node.axis);
    if (!firstNode) return secondNode;
    if (!secondNode) return firstNode;
    const split = document.createElement("div");
    split.className = "dock-split dock-split-" + node.axis;
    split.dataset.axis = node.axis;
    const first = document.createElement("div");
    first.className = "dock-cell";
    const second = document.createElement("div");
    second.className = "dock-cell";
    first.append(firstNode);
    second.append(secondNode);
    const divider = makeDivider(node, split);
    const hasCollapsedSide = collapsedFootprint(node.children[0], node.axis) != null ||
      collapsedFootprint(node.children[1], node.axis) != null;
    divider.disabled = hasCollapsedSide;
    if (hasCollapsedSide) divider.title = "Restaure o card minimizado antes de redimensionar esta divisão";
    split.append(first, divider, second);
    applySplitWeights(split, node);
    return split;
  }

  function renderLayout() {
    root.replaceChildren();
    const surface = document.createElement("div");
    surface.className = "dub-dock-surface";
    const visibleLayout = layoutTree ? buildNode(layoutTree) : null;
    if (visibleLayout) surface.append(visibleLayout);
    root.append(surface);

    root.classList.toggle("dock-root-empty", !visibleLayout);
    afterLayout();
  }

  function serializeNode(node) {
    if (!node) return null;
    if (node.type === "group") {
      return { type: "group", panels: node.panelIds, active: node.element?.dataset.active || node.active };
    }
    return { type: "split", axis: node.axis, ratio: node.ratio, children: node.children.map(serializeNode) };
  }

  function save() {
    const state = {
      version: 4,
      tree: serializeNode(layoutTree),
      collapsed: panelIds.filter((id) => panels.get(id)?.classList.contains("dock-collapsed")),
      locked: panelIds.filter((id) => panels.get(id)?.classList.contains("dock-locked")),
      floats: [...floatLayer.querySelectorAll(":scope > .dock-group")].map((group) => ({
        panels: groupPanelElements(group).map((panel) => panel.id),
        active: group.dataset.active,
        geometry: {
          x: parseFloat(group.style.left) || 0,
          y: parseFloat(group.style.top) || 0,
          width: parseFloat(group.style.width) || 500,
          height: parseFloat(group.style.height) || 350,
        },
      })),
      homes: Object.fromEntries(panelIds.map((id) => [id, {
        target: panels.get(id)?.dataset.dockHomeTarget || "",
        place: panels.get(id)?.dataset.dockHomePlace || "tab",
      }])),
    };
    try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* armazenamento local indisponível */ }
  }

  function afterLayout() {
    for (const id of panelIds) {
      const panel = panels.get(id);
      const button = panel?.querySelector(":scope > * .dock-float-toggle");
      if (!button) continue;
      const floating = Boolean(panel.closest(".dock-floating"));
      const locked = isLocked(id);
      button.textContent = floating ? "▣" : "▢";
      button.disabled = locked;
      button.title = locked ? "Desbloqueie " + names.get(id) + " para mover ou destacar" : floating ? "Encaixar " + names.get(id) + " novamente" : "Destacar " + names.get(id) + " como quadro flutuante";
      button.setAttribute("aria-label", button.title);
    }
    window.dispatchEvent(new Event("resize"));
  }

  function parseTree(data, depth) {
    if (!data || depth > 12) return null;
    if (data.type === "group" && Array.isArray(data.panels)) {
      const valid = data.panels.filter((id) => panels.has(id));
      return valid.length ? makeGroupNode(valid, panels.has(data.active) && valid.includes(data.active) ? data.active : valid[0]) : null;
    }
    if (data.type !== "split" || !["x", "y"].includes(data.axis) || !Array.isArray(data.children) || data.children.length !== 2) return null;
    const first = parseTree(data.children[0], depth + 1);
    const second = parseTree(data.children[1], depth + 1);
    if (!first) return second;
    if (!second) return first;
    return makeSplit(data.axis, first, second, data.ratio);
  }

  function joinGroups(groups, axis) {
    const usable = (groups || []).map((group) => ({
      node: makeGroupNode((group.panels || []).filter((id) => panels.has(id)), group.active),
      weight: Math.max(.1, Number(group.weight) || 1),
    })).filter((item) => item.node.panelIds.length);
    if (!usable.length) return null;
    let result = usable[0].node;
    let total = usable[0].weight;
    for (let index = 1; index < usable.length; index++) {
      const next = usable[index];
      result = makeSplit(axis, result, next.node, total / (total + next.weight));
      total += next.weight;
    }
    return result;
  }

  function migrateOldZones(saved) {
    const old = saved?.zones;
    if (!old || typeof old !== "object") return null;
    const columns = saved.columnWeights || {};
    const column = (name, fallback) => Math.max(.1, Number(columns[name]) || fallback);
    const top = [
      { node: joinGroups(old.left, "y"), weight: column("left", .19) },
      { node: joinGroups(old.center, "y"), weight: column("center", .40) },
      { node: joinGroups(old.extra, "y"), weight: column("extra", .16) },
      { node: joinGroups(old.right, "y"), weight: column("right", .25) },
    ].filter((item) => item.node);
    const bottom = joinGroups(old.timeline, "x");
    let upper = null, upperWeight = 0;
    for (const item of top) {
      if (!upper) { upper = item.node; upperWeight = item.weight; }
      else {
        upper = makeSplit("x", upper, item.node, upperWeight / (upperWeight + item.weight));
        upperWeight += item.weight;
      }
    }
    let tree = upper && bottom
      ? makeSplit("y", upper, bottom, Number(saved.rowWeights?.top) || .52)
      : upper || bottom;
    const floats = (old.float || []).map((group) => ({
      panels: (group.panels || []).filter((id) => panels.has(id)),
      active: group.active,
      geometry: group.geometry || {},
    })).filter((group) => group.panels.length);
    return { tree, floats, collapsed: saved.collapsed || [] };
  }

  function loadState() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) ||
        legacyStorageKeys.map((key) => localStorage.getItem(key)).find(Boolean) || "null");
      if (!saved) return null;
      if (Number(saved.version) < 4) {
        let hasNamedLayout = false;
        try { hasNamedLayout = Boolean(localStorage.getItem("dubsync.layout-presets-selected.v1:dubbing")); }
        catch { /* layout padrão continua disponível sem armazenamento local */ }
        if (!hasNamedLayout) return null;
      }
      const migrated = saved.tree ? null : migrateOldZones(saved);
      const parsed = saved.tree ? parseTree(saved.tree, 0) : migrated?.tree;
      const oldFloats = saved.tree ? (saved.floats || []) : (migrated?.floats || []);
      const floats = oldFloats.map((group) => ({
        ...group, panels: (group.panels || []).filter((id) => panels.has(id)),
      })).filter((group) => group.panels.length);
      const flat = collectPanelIds(parsed);
      for (const group of floats) flat.push(...group.panels);
      if (new Set(flat).size !== flat.length || flat.some((id) => !panels.has(id))) return null;
      const missing = panelIds.filter((id) => !flat.includes(id));
      let tree = parsed;
      if (missing.includes("dubVideoOverview")) {
        const overview = makeGroupNode(["dubVideoOverview"]);
        tree = tree ? makeSplit("y", overview, tree, .19) : overview;
      }
      const otherMissing = missing.filter((id) => id !== "dubVideoOverview");
      if (otherMissing.length) {
        const missingNode = joinGroups(otherMissing.map((id) => ({ panels: [id], active: id, weight: 1 })), "x");
        tree = tree ? makeSplit("x", tree, missingNode, .8) : missingNode;
      }
      return { tree, floats, collapsed: (saved.collapsed || []).filter((id) => panels.has(id)), locked: (saved.locked || []).filter((id) => panels.has(id)), homes: saved.homes || {} };
    } catch { return null; }
  }

  function groupChildren(layer) {
    return [...layer.children].filter((child) => child.classList.contains("dock-group"));
  }

  function positionFloat(group, geometry) {
    const maxWidth = Math.max(280, grid.clientWidth - 12);
    const maxHeight = Math.max(160, grid.clientHeight - 12);
    const width = Math.min(maxWidth, Math.max(280, Number(geometry.width) || 500));
    const height = Math.min(maxHeight, Math.max(160, Number(geometry.height) || 350));
    group.style.width = width + "px";
    group.style.height = height + "px";
    group.style.left = Math.max(0, Math.min(grid.clientWidth - width, Number(geometry.x) || 0)) + "px";
    group.style.top = Math.max(0, Math.min(grid.clientHeight - height, Number(geometry.y) || 0)) + "px";
    group.style.zIndex = String(++floatFront);
  }

  function syncFloating(group) {
    const floating = group.parentElement === floatLayer;
    group.classList.toggle("dock-floating", floating);
    const activePanel = panels.get(group.dataset.active);
    group.classList.toggle("dock-compact", Boolean(activePanel?.classList.contains("dock-collapsed")));
    for (const panel of groupPanelElements(group)) {
      if (floating) panel.classList.remove("dock-collapse-vertical");
      updateLockControl(panel);
    }
    if (floating && !group.querySelector(":scope > .float-resize")) {
      const grip = document.createElement("button");
      grip.type = "button";
      grip.className = "float-resize";
      grip.title = "Redimensionar quadro flutuante";
      grip.setAttribute("aria-label", grip.title);
      group.append(grip);
    }
  }

  function startFloatMove(event) {
    if (event.button !== 0) return;
    const group = event.target.closest(".dock-floating");
    if (!group || group.parentElement !== floatLayer) return;
    if (!event.target.closest(".float-resize") && groupPanelElements(group).some((panel) => isLocked(panel.id))) return;
    group.style.zIndex = String(++floatFront);
    const grip = event.target.closest(".float-resize");
    const header = event.target.closest(".panel-title,.dub-panel-heading");
    if (!grip && (!header || event.target.closest("button,input,select,textarea"))) return;
    event.preventDefault();
    const handle = grip || header;
    handle.setPointerCapture(event.pointerId);
    floatMove = {
      handle, group, resizing: Boolean(grip), x: event.clientX, y: event.clientY,
      left: parseFloat(group.style.left) || 0, top: parseFloat(group.style.top) || 0,
      width: parseFloat(group.style.width) || group.offsetWidth, height: parseFloat(group.style.height) || group.offsetHeight,
    };
    document.body.classList.add("dock-dragging");
  }

  function moveFloat(event) {
    if (!floatMove) return;
    const state = floatMove;
    const dx = event.clientX - state.x, dy = event.clientY - state.y;
    if (state.resizing) {
      state.group.style.width = Math.max(280, Math.min(grid.clientWidth - state.left, state.width + dx)) + "px";
      state.group.style.height = Math.max(160, Math.min(grid.clientHeight - state.top, state.height + dy)) + "px";
    } else {
      state.group.style.left = Math.max(0, Math.min(grid.clientWidth - state.width, state.left + dx)) + "px";
      state.group.style.top = Math.max(0, Math.min(grid.clientHeight - state.height, state.top + dy)) + "px";
    }
  }

  function stopFloatMove(event) {
    if (!floatMove || (event.target !== floatMove.handle && !floatMove.handle.contains(event.target))) return;
    floatMove = null;
    document.body.classList.remove("dock-dragging");
    save();
  }

  function findNeighbor(node, group) {
    if (!node || node.type === "group") return null;
    if (containsGroup(node.children[0], group)) {
      const sibling = firstGroup(node.children[1]);
      if (sibling) return { id: sibling.panelIds[0], place: node.axis === "x" ? "left" : "top" };
      return findNeighbor(node.children[0], group);
    }
    if (containsGroup(node.children[1], group)) {
      const sibling = firstGroup(node.children[0]);
      if (sibling) return { id: sibling.panelIds[0], place: node.axis === "x" ? "right" : "bottom" };
      return findNeighbor(node.children[1], group);
    }
    return null;
  }

  function rememberFloatHome(panel, group) {
    const sourceNode = findGroup(layoutTree, group) || findGroupForPanel(layoutTree, panel.id);
    const remaining = sourceNode?.panelIds.filter((id) => id !== panel.id) ||
      groupPanelElements(group).filter((item) => item !== panel).map((item) => item.id);
    if (remaining.length) {
      panel.dataset.dockHomeTarget = remaining[0];
      panel.dataset.dockHomePlace = "tab";
      return;
    }
    const neighbor = findNeighbor(layoutTree, sourceNode?.element || group);
    if (neighbor?.id) {
      panel.dataset.dockHomeTarget = neighbor.id;
      panel.dataset.dockHomePlace = neighbor.place;
      return;
    }
    const fallback = firstGroup(layoutTree);
    panel.dataset.dockHomeTarget = fallback?.panelIds.find((id) => id !== panel.id) || "";
    panel.dataset.dockHomePlace = panel.dataset.dockHomeTarget ? "tab" : "right";
  }

  function detachFromDockedGroup(panel, group) {
    const sourceNode = findGroup(layoutTree, group) || findGroupForPanel(layoutTree, panel.id);
    if (!sourceNode) { panel.remove(); group.remove(); return; }
    const wasRenderedGroup = sourceNode.element === group;
    sourceNode.panelIds = sourceNode.panelIds.filter((id) => id !== panel.id);
    if (sourceNode.active === panel.id) sourceNode.active = sourceNode.panelIds[0] || null;
    panel.remove();
    if (wasRenderedGroup) {
      const remaining = groupPanelElements(group);
      if (remaining.length) {
        const active = remaining.some((item) => item.id === sourceNode.active) ? sourceNode.active : remaining[0].id;
        showPanel(group, active, false);
      } else group.remove();
    } else {
      if (!groupPanelElements(group).length) group.remove();
    }
    if (!sourceNode.panelIds.length) layoutTree = removeGroupNode(layoutTree, sourceNode);
  }

  function detachFromFloat(panel, group) {
    panel.remove();
    const remaining = groupPanelElements(group);
    if (remaining.length) showPanel(group, group.dataset.active === panel.id ? remaining[0].id : group.dataset.active, false);
    else group.remove();
  }

  function newPanelGroup(panelId) {
    const node = makeGroupNode([panelId], panelId);
    node.element = makeGroupElement(node);
    return node;
  }

  function splitLeaf(targetGroup, newNode, place) {
    const targetNode = findGroup(layoutTree, targetGroup);
    if (!targetNode) {
      if (!layoutTree) { layoutTree = newNode; return; }
      const axis = place === "top" || place === "bottom" ? "y" : "x";
      const newFirst = place === "left" || place === "top";
      layoutTree = newFirst ? makeSplit(axis, newNode, layoutTree, .35) : makeSplit(axis, layoutTree, newNode, .65);
      return;
    }
    const axis = place === "left" || place === "right" ? "x" : "y";
    const newFirst = place === "left" || place === "top";
    const replacement = newFirst
      ? makeSplit(axis, newNode, targetNode, .5)
      : makeSplit(axis, targetNode, newNode, .5);
    layoutTree = replaceGroup(layoutTree, targetGroup, replacement);
  }

  function dockPanel(panelId, targetGroup, place) {
    const panel = panels.get(panelId);
    if (!panel || isLocked(panelId)) return;
    if (place === "tab" && targetGroup && groupPanelElements(targetGroup).some((item) => isLocked(item.id))) return;
    const sourceGroup = panel.parentElement?.closest(".dock-group");
    if (!sourceGroup) return;
    if (sourceGroup === targetGroup && (place === "tab" || groupPanelElements(sourceGroup).length === 1)) {
      showPanel(sourceGroup, panelId, true);
      return;
    }
    const sourceFloating = sourceGroup.parentElement === floatLayer;
    if (sourceFloating) detachFromFloat(panel, sourceGroup);
    else detachFromDockedGroup(panel, sourceGroup);

    if (place === "tab" && targetGroup && targetGroup.isConnected && targetGroup !== sourceGroup) {
      const targetNode = findGroup(layoutTree, targetGroup);
      if (targetNode) {
        targetGroup.append(panel);
        targetNode.panelIds.push(panelId);
        targetNode.active = panelId;
        showPanel(targetGroup, panelId, false);
      } else if (targetGroup.parentElement === floatLayer) {
        targetGroup.append(panel);
        showPanel(targetGroup, panelId, false);
        syncFloating(targetGroup);
      } else {
        const leaf = newPanelGroup(panelId);
        splitLeaf(targetGroup, leaf, "right");
      }
    } else if (place === "float") {
      floatPanel(panelId);
      return;
    } else {
      const leaf = newPanelGroup(panelId);
      splitLeaf(targetGroup, leaf, place || "right");
    }
    renderLayout();
    save();
  }

  function floatPanel(panelId, geometry) {
    const panel = panels.get(panelId);
    if (!panel || isLocked(panelId)) return;
    const sourceGroup = panel.parentElement?.closest(".dock-group");
    if (!sourceGroup || sourceGroup.parentElement === floatLayer) return;
    const panelRect = panel.getBoundingClientRect();
    const gridRect = grid.getBoundingClientRect();
    const startGeometry = geometry || {
      x: Math.max(0, panelRect.left - gridRect.left + 24),
      y: Math.max(0, panelRect.top - gridRect.top + 18),
      width: Math.min(920, Math.max(350, panelRect.width)),
      height: Math.min(700, Math.max(260, panelRect.height)),
    };
    rememberFloatHome(panel, sourceGroup);
    detachFromDockedGroup(panel, sourceGroup);
    const node = newPanelGroup(panelId);
    const group = node.element;
    floatLayer.append(group);
    showPanel(group, panelId, false);
    positionFloat(group, startGeometry);
    syncFloating(group);
    renderLayout();
    save();
  }

  function toggleFloat(panelId) {
    const panel = panels.get(panelId);
    const group = panel?.parentElement?.closest(".dock-group");
    if (!panel || !group) return;
    if (group.parentElement !== floatLayer) { floatPanel(panelId); return; }
    const targetPanel = panels.get(panel.dataset.dockHomeTarget);
    const targetGroup = targetPanel?.parentElement?.closest(".dock-group");
    dockPanel(panelId, targetGroup || firstGroup(layoutTree)?.element || null, panel.dataset.dockHomePlace || "tab");
  }

  function startDrag(event) {
    if (event.target.closest("button:not(.dock-tab),input,select,textarea")) {
      event.preventDefault();
      return;
    }
    const id = event.currentTarget.dataset.panelId;
    if (!panels.has(id) || isLocked(id)) { event.preventDefault(); return; }
    draggingId = id;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", id);
    document.body.classList.add("dock-dragging");
  }

  function placement(event, group) {
    if (event.target.closest(".dock-tabs")) return "tab";
    const rect = group.getBoundingClientRect();
    const x = rect.width ? (event.clientX - rect.left) / rect.width : .5;
    const y = rect.height ? (event.clientY - rect.top) / rect.height : .5;
    const edges = [
      { place: "left", distance: x },
      { place: "right", distance: 1 - x },
      { place: "top", distance: y },
      { place: "bottom", distance: 1 - y },
    ].sort((a, b) => a.distance - b.distance);
    return edges[0].distance < .28 ? edges[0].place : "tab";
  }

  function clearDropHint() {
    root.querySelectorAll(".dock-target-left,.dock-target-right,.dock-target-top,.dock-target-bottom,.dock-target-tab")
      .forEach((group) => group.classList.remove("dock-target-left", "dock-target-right", "dock-target-top", "dock-target-bottom", "dock-target-tab"));
    floatLayer.querySelectorAll(".dock-target-left,.dock-target-right,.dock-target-top,.dock-target-bottom,.dock-target-tab")
      .forEach((group) => group.classList.remove("dock-target-left", "dock-target-right", "dock-target-top", "dock-target-bottom", "dock-target-tab"));
  }

  function handleDragOver(event) {
    if (!draggingId) return;
    const group = event.target.closest(".dock-group");
    if (!group) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearDropHint();
    group.classList.add("dock-target-" + placement(event, group));
  }

  function handleDrop(event) {
    if (!draggingId) return;
    const group = event.target.closest(".dock-group");
    if (!group) return;
    event.preventDefault();
    const place = placement(event, group);
    const id = draggingId;
    clearDropHint();
    const draggedPanel = panels.get(id);
    const sourceGroup = draggedPanel?.parentElement?.closest(".dock-group");
    if (!(sourceGroup === group && draggedPanel?.classList.contains("dock-collapsed"))) {
      if (draggedPanel?.classList.contains("dock-collapsed")) setPanelCollapsed(draggedPanel, false);
    }
    dockPanel(id, group, place);
    draggingId = null;
    document.body.classList.remove("dock-dragging");
  }

  function clearDrag() {
    draggingId = null;
    clearDropHint();
    document.body.classList.remove("dock-dragging");
  }

  function setupPanelControls() {
    for (const id of panelIds) {
      const panel = panels.get(id);
      const header = panelHeader(panel);
      if (!panel || !header) continue;
      const title = [...header.children].find((child) => child.querySelector("h2")) || header.firstElementChild;
      const actions = document.createElement("div");
      actions.className = "dock-header-actions";
      for (const child of [...header.children]) if (child !== title) actions.append(child);
      header.append(actions);

      const floatButton = document.createElement("button");
      floatButton.type = "button";
      floatButton.className = "dock-float-toggle";
      floatButton.addEventListener("click", (event) => { event.stopPropagation(); toggleFloat(id); });
      actions.append(floatButton);

      const lockButton = document.createElement("button");
      lockButton.type = "button";
      lockButton.className = "dock-lock-toggle";
      lockButton.addEventListener("click", (event) => {
        event.stopPropagation();
        setPanelLocked(panel, !isLocked(id));
      });
      actions.append(lockButton);

      const collapse = document.createElement("button");
      collapse.type = "button";
      collapse.className = "dock-collapse";
      collapse.setAttribute("aria-label", "Recolher " + names.get(id));
      collapse.setAttribute("aria-expanded", "true");
      collapse.title = collapse.getAttribute("aria-label");
      collapse.textContent = "−";
      collapse.addEventListener("click", (event) => {
        event.stopPropagation();
        const collapsed = !panel.classList.contains("dock-collapsed");
        setPanelCollapsed(panel, collapsed);
        const group = panel.parentElement?.closest(".dock-group");
        if (group) showPanel(group, id, false);
        renderLayout();
        save();
      });
      actions.append(collapse);

      header.draggable = !isLocked(id);
      header.dataset.panelId = id;
      header.title = "Arraste o cabeçalho até uma borda para encaixar acima, abaixo, à esquerda ou à direita; no centro, crie uma aba. Os demais cards se ajustam junto.";
      header.addEventListener("dragstart", startDrag);
      header.addEventListener("dragend", clearDrag);
    }
  }

  function initialize() {
    const restored = loadState();
    for (const zone of legacyZones) zone.remove();
    for (const divider of legacyDividers) divider.remove();
    grid.classList.add("dock-enabled");
    setupPanelControls();
    for (const id of panelIds) {
      const home = restored?.homes?.[id];
      if (home && panels.has(home.target)) panels.get(id).dataset.dockHomeTarget = home.target;
      if (home?.place) panels.get(id).dataset.dockHomePlace = home.place;
    }
    for (const id of restored?.collapsed || []) {
      const panel = panels.get(id);
      setPanelCollapsed(panel, true);
    }
    for (const id of restored?.locked || []) panels.get(id).classList.add("dock-locked");
    layoutTree = restored?.tree || defaultLayout();
    for (const saved of restored?.floats || []) {
      const ids = saved.panels.filter((id) => panels.has(id));
      if (!ids.length) continue;
      const group = makeGroupElement(makeGroupNode(ids, saved.active));
      floatLayer.append(group);
      showPanel(group, saved.active, false);
      positionFloat(group, saved.geometry || {});
      syncFloating(group);
    }
    renderLayout();
    for (const panel of panels.values()) updateLockControl(panel);
    root.addEventListener("dragover", handleDragOver);
    root.addEventListener("drop", handleDrop);
    floatLayer.addEventListener("dragover", handleDragOver);
    floatLayer.addEventListener("drop", handleDrop);
    floatLayer.addEventListener("pointerdown", startFloatMove);
    floatLayer.addEventListener("pointermove", moveFloat);
    floatLayer.addEventListener("pointerup", stopFloatMove);
    floatLayer.addEventListener("pointercancel", stopFloatMove);
    document.addEventListener("dragend", clearDrag);
    window.addEventListener("resize", () => {
      for (const group of groupChildren(floatLayer)) {
        positionFloat(group, {
          x: parseFloat(group.style.left), y: parseFloat(group.style.top),
          width: parseFloat(group.style.width), height: parseFloat(group.style.height),
        });
      }
    });
    save();
    window.DubSyncLayoutPresets?.register({
      scope: "dubbing",
      label: "Dublagem",
      storageKey,
      extraKeys: ["dubsync.dub.lineColumns.v1"],
      legacyKeys: [...legacyStorageKeys, "dubsync.dub.lineColumns.v1"],
      defaultKeys: [storageKey, ...legacyStorageKeys, "dubsync.dub.lineColumns.v1"],
    });
  }

  initialize();
})();
