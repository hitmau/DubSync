const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const DEFAULT_REGIONS = [{ roi_id: "dubsync-roi-main", x: .08, y: .63, width: .84, height: .32, name: "ROI 1 (Diálogo principal)", color: "#e43c46", enabled: true, visible: true }];
const ROI_VISIBILITY_STORAGE_KEY = "dubsync.roiVisibility.v1";
let mode = "youtube", activeSessionId = "", selectedDesktopRect = null, currentSession = null, preparePending = false, sessionHydrated = false;
let regions = structuredClone(DEFAULT_REGIONS), preview = null, previewKind = "", previewMode = "capture", drag = null;
let persistentFilePauseAvailable = false;
let sessions = [], events = [], selectedEventId = "", selectedThumb = "crop", pollTimer = 0, livePreviewTimer = 0, sessionWatchTimer = 0, toastTimer = 0, pausePending = false;
let overviewSessions = [], overviewSessionId = "", overviewVideoDuration = 0;
let coverageUrl = "", coverageLoadedAt = 0, coverageCompleteSession = "", fileOrganizationForSession = "";
let layoutDrag = null, timelineEdit = null, timelineNavigatorDrag = null, ignoreTimelineClick = false;
let timelinePinSelectedIds = new Set(), timelinePinSelectionAnchor = "", timelineMarqueeDrag = null, timelinePinDrag = null;
let characterOptionsRegionId = "", characterOptionsData = [], characterOptionsLoadedAt = 0;
let characterOptionsLoadPromise = null, characterOptionsRequestId = 0, characterOptionsActiveIndex = -1;
let visibleCharacterOptions = [];
let roiCharacterColorById = new Map(), roiCharacterColorByRoiName = new Map(), roiCharacterColorByName = new Map();
let roiCharacterColorsLoadedAt = 0, roiCharacterColorsLoadPromise = null;
let timelineRange = {start:0,span:60,end:60};
const TIMELINE_VIEW_STORAGE_KEY="dubsync.timelineViews.v1";
const SHORTCUTS_STORAGE_KEY="dubsync.shortcuts.v1";
const SHORTCUT_STEPS_STORAGE_KEY="dubsync.shortcutSteps.v1";
const BLOCK_AUTOMATION_STORAGE_KEY="dubsync.blockAutomation.v1";
const AUTO_NEXT_PARENT_STORAGE_KEY="dubsync.autoNextParent.v1";
let blockAutomationMode=localStorage.getItem(BLOCK_AUTOMATION_STORAGE_KEY)==="automatic"?"automatic":"manual";
let autoNextStartedForSession=localStorage.getItem(AUTO_NEXT_PARENT_STORAGE_KEY)||"";
let timelineViews=readLocalObject(TIMELINE_VIEW_STORAGE_KEY);
let recordingShortcutAction="";
const SHORTCUT_ACTIONS=[
  ["togglePlayback","Reproduzir / pausar","Alterna o player local; referência After Effects: Espaço.",{code:"Space"}],
  ["seekBackward","Voltar (passo principal)","Volta o número de segundos configurado em Passo principal.",{code:"ArrowLeft"}],
  ["seekForward","Avançar (passo principal)","Avança o número de segundos configurado em Passo principal.",{code:"ArrowRight"}],
  ["seekFineBackward","Voltar (passo fino)","Volta o número de segundos configurado em Passo fino.",{code:"ArrowLeft",alt:true}],
  ["seekFineForward","Avançar (passo fino)","Avança o número de segundos configurado em Passo fino.",{code:"ArrowRight",alt:true}],
  ["seekFastBackward","Voltar (passo rápido)","Volta o número de segundos configurado em Passo rápido.",{code:"ArrowLeft",shift:true}],
  ["seekFastForward","Avançar (passo rápido)","Avança o número de segundos configurado em Passo rápido.",{code:"ArrowRight",shift:true}],
  ["previousDialogue","Diálogo anterior","Seleciona o ponto de diálogo anterior; adaptação de navegação temporal J/K do After Effects.",{code:"KeyJ"}],
  ["nextDialogue","Próximo diálogo","Seleciona o próximo ponto de diálogo; adaptação de navegação temporal J/K do After Effects.",{code:"KeyK"}],
  ["manualDialogue","Adicionar diálogo manual","Abre o formulário para criar uma fala no tempo atual do player.",{code:"KeyN"}],
  ["removeDialogue","Excluir diálogo selecionado","Executa a mesma ação do botão Excluir/Descartar, incluindo confirmação e exclusão definitiva configurada.",{code:"Delete"}],
  ["zoomIn","Aumentar zoom da timeline","Aproxima a escala temporal; referência After Effects: +/=. ",{code:"Equal"}],
  ["zoomOut","Diminuir zoom da timeline","Afasta a escala temporal; referência After Effects: −.",{code:"Minus"}],
  ["fitTimeline","Ajustar timeline ao trecho","Mostra novamente toda a duração; referência After Effects: Shift+;.",{code:"Semicolon",shift:true}],
  ["centerPlayhead","Centralizar no tempo atual","Reposiciona a vista ampliada ao redor do playhead; referência After Effects: D.",{code:"KeyD"}],
  ["openDubbing","Abrir Dublagem","Mostra o Take Studio sem perder o estado da captura.",{code:"KeyD",alt:true,shift:true}],
  ["generateDubbingTake","Gerar take selecionado","Gera a ocorrência aberta no Take Studio.",{code:"Enter",ctrl:true}],
];
let shortcutBindings=readShortcutBindings();
let shortcutSteps=readShortcutSteps();
let timelineMarkers = [];
let lastEventRefreshAt = 0;
let observedActivitySession = "", observedActivitySignature = "";
let observedActivityHistory = [];
let lastTimelinePlaybackTime = null;
let timelineVideoFrameId = 0, timelineAnimationFrameId = 0;
let followDialogue = localStorage.getItem("dubsync.followDialogue") === "true";
let showSplitPreview = true, permanentDiscardEnabled = false;
function setPermanentDiscardEnabled(value){permanentDiscardEnabled=Boolean(value);const button=$("#discardButton");if(button){button.textContent=permanentDiscardEnabled?"Excluir definitivamente":"▤ Descartar";button.title=permanentDiscardEnabled?"Excluir este diálogo permanentemente; será solicitada confirmação.":"Descartar preserva o diálogo para revisão posterior.";}}
window.setPermanentDiscardEnabled=setPermanentDiscardEnabled;void api("/api/settings").then((value)=>setPermanentDiscardEnabled(value.review?.permanent_discard)).catch(()=>{});
let fineSweepRun = null, fineSweepSessionId = "", fineSweepPollTimer = 0;
const layoutStorageKey = "dubsync.layout.v1";
const EVENT_TABLE_LAYOUT_KEY = "dubsync.eventTableLayout.v1";
const EVENT_TABLE_COLUMNS = ["select", "number", "occurrence", "start", "end", "duration", "text", "character", "emotion", "confidence", "status"];
const EVENT_TABLE_DEFAULT_WIDTHS = {select:38, number:48, occurrence:108, start:88, end:88, duration:64, text:260, character:135, emotion:100, confidence:64, status:150};
const EVENT_TABLE_MIN_WIDTHS = {select:32, number:38, occurrence:72, start:70, end:70, duration:52, text:120, character:76, emotion:65, confidence:50, status:92};
let eventColumnOrder = [...EVENT_TABLE_COLUMNS];
let eventColumnWidths = {...EVENT_TABLE_DEFAULT_WIDTHS};
let eventColumnDragKey = "";

function readLocalObject(key){try{const value=JSON.parse(localStorage.getItem(key)||"{}");return value&&typeof value==="object"&&!Array.isArray(value)?value:{};}catch{return{};}}
function readShortcutBindings(){const saved=readLocalObject(SHORTCUTS_STORAGE_KEY),defaults=Object.fromEntries(SHORTCUT_ACTIONS.map(([id,,,binding])=>[id,binding]));return Object.fromEntries(Object.entries(defaults).map(([id,value])=>[id,saved[id]&&typeof saved[id].code==="string"?saved[id]:value]));}
function readShortcutSteps(){const saved=readLocalObject(SHORTCUT_STEPS_STORAGE_KEY);return{main:Number(saved.main)||1,fine:Number(saved.fine)||.1,fast:Number(saved.fast)||5};}
function persistEventTableLayout(){try{localStorage.setItem(EVENT_TABLE_LAYOUT_KEY,JSON.stringify({order:eventColumnOrder,widths:eventColumnWidths}));}catch{/* the table remains usable without saved preferences */}}
function applyEventTableLayout(){
  const table=$("#eventTable"),scroll=table?.closest(".event-table-scroll");if(!table||!scroll)return;
  const columns=table.querySelector("colgroup"),header=table.tHead?.rows[0],body=table.tBodies[0];if(!columns||!header||!body)return;
  const colMap=new Map([...columns.querySelectorAll("col[data-column]")].map((node)=>[node.dataset.column,node]));
  const headerMap=new Map([...header.querySelectorAll("th[data-column]")].map((node)=>[node.dataset.column,node]));
  for(const key of eventColumnOrder){const col=colMap.get(key),cell=headerMap.get(key);if(col){col.style.width=`${eventColumnWidths[key]}px`;columns.append(col);}if(cell)header.append(cell);}
  for(const row of body.rows){if(row.classList.contains("table-empty"))continue;const cellMap=new Map([...row.querySelectorAll("td[data-column]")].map((node)=>[node.dataset.column,node]));for(const key of eventColumnOrder){const cell=cellMap.get(key);if(cell)row.append(cell);}}
  const totalWidth=eventColumnOrder.reduce((total,key)=>total+(eventColumnWidths[key]||EVENT_TABLE_DEFAULT_WIDTHS[key]),0);
  table.style.width=`${Math.max(totalWidth,scroll.clientWidth)}px`;
}
function initializeEventTableColumns(){
  const table=$("#eventTable");if(!table)return;
  const saved=readLocalObject(EVENT_TABLE_LAYOUT_KEY),savedOrder=Array.isArray(saved.order)?saved.order:[];
  if(savedOrder.length===EVENT_TABLE_COLUMNS.length&&new Set(savedOrder).size===EVENT_TABLE_COLUMNS.length&&savedOrder.every((key)=>EVENT_TABLE_COLUMNS.includes(key)))eventColumnOrder=[...savedOrder];
  if(saved.widths&&typeof saved.widths==="object")for(const key of EVENT_TABLE_COLUMNS){const value=Number(saved.widths[key]);if(Number.isFinite(value))eventColumnWidths[key]=Math.max(EVENT_TABLE_MIN_WIDTHS[key],Math.min(900,value));}
  for(const header of table.querySelectorAll("th[data-column]")){
    const key=header.dataset.column;header.draggable=true;header.title="Arraste para reordenar esta coluna. Arraste sua borda direita para ajustar a largura.";
    header.addEventListener("dragstart",(event)=>{if(event.target.closest(".event-column-resizer")){event.preventDefault();return;}eventColumnDragKey=key;header.classList.add("event-column-dragging");event.dataTransfer.effectAllowed="move";event.dataTransfer.setData("text/plain",key);});
    header.addEventListener("dragend",()=>{eventColumnDragKey="";table.querySelectorAll(".event-column-dragging,.event-column-drop-target").forEach((node)=>node.classList.remove("event-column-dragging","event-column-drop-target"));});
    header.addEventListener("dragover",(event)=>{if(!eventColumnDragKey||eventColumnDragKey===key)return;event.preventDefault();event.dataTransfer.dropEffect="move";table.querySelectorAll(".event-column-drop-target").forEach((node)=>node.classList.remove("event-column-drop-target"));header.classList.add("event-column-drop-target");});
    header.addEventListener("drop",(event)=>{event.preventDefault();const source=event.dataTransfer.getData("text/plain")||eventColumnDragKey;if(!source||source===key)return;const from=eventColumnOrder.indexOf(source),target=eventColumnOrder.indexOf(key);if(from<0||target<0)return;const rect=header.getBoundingClientRect(),insertAfter=event.clientX>rect.left+rect.width/2;let destination=target+(insertAfter?1:0);if(from<destination)destination--;eventColumnOrder.splice(from,1);eventColumnOrder.splice(Math.max(0,Math.min(eventColumnOrder.length,destination)),0,source);applyEventTableLayout();persistEventTableLayout();});
    const resizer=document.createElement("span");resizer.className="event-column-resizer";resizer.draggable=false;resizer.setAttribute("role","separator");resizer.setAttribute("aria-orientation","vertical");resizer.setAttribute("aria-label",`Ajustar largura da coluna ${header.textContent.trim()||"seleção"}`);resizer.title="Arraste para ajustar a largura da coluna";
    let pointerStart=0,widthStart=0;
    resizer.addEventListener("pointerdown",(event)=>{if(event.button!==0)return;event.preventDefault();event.stopPropagation();pointerStart=event.clientX;widthStart=eventColumnWidths[key];document.body.classList.add("resizing-event-column");resizer.setPointerCapture?.(event.pointerId);});
    resizer.addEventListener("pointermove",(event)=>{if(!resizer.hasPointerCapture?.(event.pointerId))return;const direction=getComputedStyle(table).direction==="rtl"?-1:1;eventColumnWidths[key]=Math.max(EVENT_TABLE_MIN_WIDTHS[key],Math.min(900,widthStart+(event.clientX-pointerStart)*direction));applyEventTableLayout();});
    const finishResize=(event)=>{if(!resizer.hasPointerCapture?.(event.pointerId))return;resizer.releasePointerCapture?.(event.pointerId);document.body.classList.remove("resizing-event-column");persistEventTableLayout();};
    resizer.addEventListener("pointerup",finishResize);resizer.addEventListener("pointercancel",finishResize);header.append(resizer);
  }
  applyEventTableLayout();
  if(typeof ResizeObserver!=="undefined"){const observer=new ResizeObserver(()=>applyEventTableLayout());observer.observe(table.closest(".event-table-scroll"));}
  window.addEventListener("resize",applyEventTableLayout,{passive:true});
}

function toast(message, bad = false) { const item = $("#toast"); item.textContent = message; item.style.background = bad ? "#f1abb0" : "#c3f2da"; item.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => item.classList.remove("show"), 3000); }
function formatBytes(value){const bytes=Number(value)||0;if(bytes<1024)return `${bytes} B`;const units=["KiB","MiB","GiB","TiB"];let amount=bytes/1024,index=0;while(amount>=1024&&index<units.length-1){amount/=1024;index++;}return `${amount.toFixed(amount>=100?0:amount>=10?1:2)} ${units[index]}`;}
function formatEta(value){const seconds=Math.max(0,Math.round(Number(value)||0));if(!seconds)return "calculando tempo";const h=Math.floor(seconds/3600),m=Math.floor((seconds%3600)/60),s=seconds%60;return h?`${h}h ${String(m).padStart(2,"0")}m`:m?`${m}m ${String(s).padStart(2,"0")}s`:`${s}s`;}
async function loadCoverage(url,force=false){
  const value=(url||"").trim(),panel=$("#coveragePanel"),list=$("#coverageList");
  if(!value){panel.classList.add("hidden");coverageUrl="";return;}
  if(!force&&value===coverageUrl&&Date.now()-coverageLoadedAt<30000)return;
  try{
    const result=await api(`/api/youtube/coverage?url=${encodeURIComponent(value)}`);coverageUrl=value;coverageLoadedAt=Date.now();list.replaceChildren();
    const grouped=new Map();
    for(const interval of result.intervals||[]){const session=sessions.find((item)=>item.session_id===interval.session_id),language=session?.language||"?",key=`${Number(interval.start_time).toFixed(3)}|${Number(interval.end_time).toFixed(3)}|${language}`;const current=grouped.get(key);if(!current)grouped.set(key,{...interval,language,runs:1});else{current.runs++;if(String(interval.completed_at||"")>String(current.completed_at||""))Object.assign(current,interval,{language,runs:current.runs});}}
    if(!grouped.size){const empty=document.createElement("div");empty.className="coverage-empty";empty.textContent="Nenhum intervalo analisado ainda.";list.append(empty);}
    for(const interval of [...grouped.values()].sort((a,b)=>a.start_time-b.start_time||a.end_time-b.end_time||a.language.localeCompare(b.language))){
      const button=document.createElement("button");button.type="button";button.className="coverage-item";
      const label=document.createElement("span"),count=document.createElement("span");label.textContent=`✓ ${timecode(interval.start_time)}–${timecode(interval.end_time)} · ${interval.language.toUpperCase()}`;count.textContent=`${interval.events} eventos${interval.runs>1?` · ${interval.runs} execuções`:""} · abrir`;
      button.append(label,count);button.addEventListener("click",async()=>{const session=sessions.find((item)=>item.session_id===interval.session_id);if(session){await attachActiveSession(session);toast("Intervalo concluído aberto para revisão.");}});list.append(button);
    }
    panel.classList.remove("hidden");
  }catch(error){console.warn("Não foi possível consultar intervalos concluídos:",error);}
}
function splitSelectors(handle){return handle.dataset.tracks.split("|");}
function splitVars(handle){return handle.dataset.vars.split("|");}
function splitSizes(handle){const axis=handle.dataset.axis;return splitSelectors(handle).map((selector)=>{const rect=$(selector).getBoundingClientRect();return axis==="x"?rect.width:rect.height;});}
function writeSplitSizes(handle,sizes){const container=$(handle.dataset.container);splitVars(handle).forEach((name,index)=>container.style.setProperty(name,`${Math.max(1,sizes[index])}fr`));}
function saveSplitSizes(handle,sizes=splitSizes(handle)){try{const saved=JSON.parse(localStorage.getItem(layoutStorageKey)||"{}");saved[handle.dataset.tracks]=sizes;localStorage.setItem(layoutStorageKey,JSON.stringify(saved));}catch{/* layout persistence is optional */}}
function applySplitDelta(handle,delta){
  const selectors=splitSelectors(handle),beforeIndex=selectors.indexOf(handle.dataset.before),afterIndex=selectors.indexOf(handle.dataset.after);
  if(beforeIndex<0||afterIndex<0)return null;
  const sizes=splitSizes(handle),pairTotal=sizes[beforeIndex]+sizes[afterIndex];
  const minBefore=Number(handle.dataset.minBefore)||1,minAfter=Number(handle.dataset.minAfter)||1;
  sizes[beforeIndex]=Math.max(minBefore,Math.min(pairTotal-minAfter,sizes[beforeIndex]+delta));
  sizes[afterIndex]=pairTotal-sizes[beforeIndex];writeSplitSizes(handle,sizes);return sizes;
}
function layoutSplitters(){
  const splitters=$$(".grid-splitter");
  for(const handle of splitters){
    const before=$(handle.dataset.before),after=$(handle.dataset.after),overlay=$("#appGrid");
    if(!overlay||!before||!after)continue;
    const parent=overlay.getBoundingClientRect(),a=before.getBoundingClientRect(),b=after.getBoundingClientRect();
    if(handle.dataset.axis==="x"){
      const top=Math.max(0,Math.min(a.top,b.top)-parent.top),bottom=Math.min(parent.height,Math.max(a.bottom,b.bottom)-parent.top);
      handle.style.left=`${(a.right+b.left)/2-parent.left}px`;handle.style.top=`${top}px`;handle.style.height=`${Math.max(0,bottom-top)}px`;
    }else{
      const left=Math.max(0,Math.min(a.left,b.left)-parent.left),right=Math.min(parent.width,Math.max(a.right,b.right)-parent.left);
      handle.style.top=`${(a.bottom+b.top)/2-parent.top}px`;handle.style.left=`${left}px`;handle.style.width=`${Math.max(0,right-left)}px`;
    }
  }
}
function initializeSplitters(){
  try{
    const saved=JSON.parse(localStorage.getItem(layoutStorageKey)||"{}");
    for(const handle of $$(".grid-splitter")){
      const sizes=saved[handle.dataset.tracks],vars=splitVars(handle);
      if(Array.isArray(sizes)&&sizes.length===vars.length){const container=$(handle.dataset.container);vars.forEach((name,index)=>{const size=Number(sizes[index]);if(Number.isFinite(size)&&size>0&&size<10000)container.style.setProperty(name,`${size}fr`);});}
    }
  }catch{/* ignore stale or unavailable browser storage */}
  for(const handle of $$(".grid-splitter")){
    handle.addEventListener("pointerdown",(event)=>{
      if(event.button!==0)return;event.preventDefault();
      layoutDrag={handle,start:handle.dataset.axis==="x"?event.clientX:event.clientY,sizes:splitSizes(handle),pointerId:event.pointerId};
      handle.classList.add("dragging");document.body.classList.add("layout-resizing",handle.dataset.axis==="x"?"resize-x":"resize-y");handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener("pointermove",(event)=>{
      if(!layoutDrag||layoutDrag.handle!==handle)return;
      const point=handle.dataset.axis==="x"?event.clientX:event.clientY;
      const sizes=applySplitDelta(handle,point-layoutDrag.start);
      if(sizes){layoutDrag.start=point;layoutDrag.sizes=sizes;requestAnimationFrame(layoutSplitters);}
    });
    const finish=()=>{if(!layoutDrag||layoutDrag.handle!==handle)return;saveSplitSizes(handle,layoutDrag.sizes);layoutDrag=null;handle.classList.remove("dragging");document.body.classList.remove("layout-resizing","resize-x","resize-y");requestAnimationFrame(layoutSplitters);};
    handle.addEventListener("pointerup",finish);handle.addEventListener("pointercancel",finish);
    handle.addEventListener("keydown",(event)=>{
      const positive=handle.dataset.axis==="x"?["ArrowRight"]:["ArrowDown"],negative=handle.dataset.axis==="x"?["ArrowLeft"]:["ArrowUp"];
      if(!positive.includes(event.key)&&!negative.includes(event.key))return;
      event.preventDefault();const sizes=applySplitDelta(handle,positive.includes(event.key)?12:-12);if(sizes)saveSplitSizes(handle,sizes);requestAnimationFrame(layoutSplitters);
    });
  }
  requestAnimationFrame(layoutSplitters);window.addEventListener("resize",()=>requestAnimationFrame(layoutSplitters));
}
async function api(url, options = {}) { const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options }); const text = await response.text(); let value = {}; try { value = text ? JSON.parse(text) : {}; } catch { value = { detail: text }; } if (!response.ok) throw new Error(value.detail || `HTTP ${response.status}`); return value; }
function durationValue(value) { const text = String(value ?? "").trim(); if (text.includes(":")) { const parts = text.split(":").map(Number); if (parts.some((part) => !Number.isFinite(part) || part < 0) || parts.length > 3) throw new Error("Use duração em segundos ou hh:mm:ss."); return parts.reduce((total, part) => total * 60 + part, 0); } const number = Number(text); if (!Number.isFinite(number) || number < 0) throw new Error("Informe um tempo válido."); return number; }
function timecode(seconds, millis = false) { if (seconds == null || !Number.isFinite(Number(seconds))) return "—"; const n = Math.max(0, Number(seconds)); const h = Math.floor(n / 3600), m = Math.floor(n % 3600 / 60), s = n % 60; return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${millis ? s.toFixed(3).padStart(6,"0") : String(Math.floor(s)).padStart(2,"0")}`; }
function getInitialRange() { const start=durationValue($("#sourceStart").value),end=durationValue($("#sourceDuration").value);if(end<=start)throw new Error("O fim do primeiro bloco precisa ser posterior ao início.");return{start,end,duration:end-start}; }
function updateInitialRangeSummary() { const node=$("#initialRangeSummary");if(!node)return;try{const range=getInitialRange();node.classList.remove("error-text");node.textContent=`Primeiro bloco: ${timecode(range.start)} → ${timecode(range.end)} · ${timecode(range.duration)} de análise.`;}catch(error){node.classList.add("error-text");node.textContent=error.message;} }
function sessionBlockIndex(session) { const config=session?.config_json||{};return Math.max(1,Number(config.block_index||(config.previous_session_id?2:1))); }
function updateInitialRangeUi(session=currentSession) { const continuation=Boolean(session)&&sessionBlockIndex(session)>1,locked=Boolean(session)&&activeState(session.state);$("#initialRangeConfig")?.classList.toggle("hidden",continuation);$("#continuationRangeSummary")?.classList.toggle("hidden",!continuation);$("#sourceStart").disabled=locked;$("#sourceDuration").disabled=locked;if(!continuation){updateInitialRangeSummary();return;}const config=session.config_json||{},start=Number(session.source_start??config.start??0),duration=Number(config.duration??session.requested_duration??0),end=start+duration;$("#continuationRangeSummary").textContent=`Bloco ${sessionBlockIndex(session)}: ${timecode(start)} → ${timecode(end)} · ${timecode(duration)}. O próximo começará em ${timecode(end)}.`; }
function activeState(state=currentSession?.state){return ["queued","downloading","preparing","running","processing","pausing","paused","stopping"].includes(state);}
function sessionStateLabel(session){if(session?.state==="preparing"&&session.ready_for_calibration)return"Pronto para calibrar e iniciar";return({queued:"Na fila",downloading:"Baixando vídeo",preparing:"Preparando vídeo",running:"Analisando vídeo",processing:"Analisando vídeo",pausing:"Salvando pausa",paused:"Pausada",stopping:"Encerrando",complete:"Concluída",failed:"Falhou",stopped:"Interrompida"})[session?.state]||"Pronto";}
function updatePlaybackButton(){const button=$("#playButton"),state=currentSession?.state,config=currentSession?.config_json||{},progress=currentSession?.progress||{};button.classList.toggle("busy",["queued","downloading","preparing","running","processing","pausing","stopping"].includes(state));button.classList.toggle("analysis-active",persistentFilePauseAvailable&&currentSession?.source_type==="file"&&state==="running"&&progress.stage==="analysis");$("#pauseButton").classList.toggle("hidden",persistentFilePauseAvailable&&currentSession?.source_type==="file");if(!sessionHydrated||preparePending){button.disabled=true;return;}if(state==="downloading"&&currentSession?.progress?.download_status==="finished"){button.disabled=true;button.textContent="Validando vídeo…";return;}if(config.mode==="youtube_download"&&["failed","stopped"].includes(state)&&!config.analysis_started){button.disabled=false;button.textContent="↻ Retomar download";return;}if(state==="preparing"&&currentSession?.ready_for_calibration&&config.downloaded_path&&!config.analysis_started){button.disabled=false;button.textContent="▶ Iniciar primeiro bloco";return;}if(persistentFilePauseAvailable&&currentSession?.source_type==="file"&&state==="paused"){button.disabled=false;button.textContent=`▶ Continuar análise do bloco ${sessionBlockIndex(currentSession)}`;return;}if(state==="complete"&&sessionBlockIndex(currentSession)>1){button.disabled=true;button.textContent=`✓ Bloco ${sessionBlockIndex(currentSession)} concluído`;return;}if(currentSession?.source_type==="file"&&["failed","stopped"].includes(state)){button.disabled=false;button.textContent=`↻ Retomar bloco ${sessionBlockIndex(currentSession)}`;return;}if(!currentSession||!activeState(state)){button.disabled=false;button.textContent=mode==="file"?"▶ Iniciar primeiro bloco":"⬇ Baixar vídeo";return;}if(state==="paused"){button.disabled=false;button.textContent="▶ Retomar análise";return;}if(persistentFilePauseAvailable&&currentSession?.source_type==="file"&&state==="running"){button.disabled=false;button.textContent="◌ Analisando frames / Pausar";return;}if(state==="pausing"){button.disabled=true;button.textContent="◌ Salvando pausa…";return;}button.disabled=true;if(state==="downloading")button.textContent="⬇ Baixando vídeo…";else if(state==="preparing")button.textContent="◌ Preparando vídeo…";else if(state==="running"&&(!progress.stage||progress.stage==="indexing_timestamps"||Number(progress.frames_seen||0)===0))button.textContent="◌ Indexando timestamps…";else button.textContent="◌ Analisando frames…";}
function setMode(next,allowPreparedYoutube=false) { const preparedDownload=currentSession?.source_type==="youtube"&&currentSession?.config_json?.mode==="youtube_download"&&Boolean(currentSession?.config_json?.downloaded_path);if(activeState()&&currentSession&&next!==currentSession.source_type&&!(allowPreparedYoutube&&preparedDownload&&next==="file")){toast("Encerre a sessão ativa antes de mudar a fonte.",true);next=currentSession.source_type==="youtube"?"youtube":"file";}mode=next;$$('.source-tab').forEach((item)=>item.classList.toggle('active',item.dataset.mode===next));$('#urlField').classList.toggle('hidden',next!=='youtube');$('#fileField').classList.toggle('hidden',next!=='file');$('.segment-config').classList.toggle('hidden',next!=='youtube');$('.next-block-config').classList.toggle('hidden',next==='youtube');$('#selectCaptureButton').classList.toggle('hidden',next==='youtube');$('#transportCaption').textContent=next==='youtube'?'Download e análise local':'Vídeo local · reprodução e análise';$('#pauseButton').classList.remove('hidden');$('#segmentDuration').disabled=next!=='youtube';updatePlaybackButton();updateNextBlockControl();updateInitialRangeUi(currentSession);renderVideoOverview(); }
function parseRegions() { return regions.filter((region) => region.enabled).map(({ roi_id,x,y,width,height,name,lifetime,motion,speaker_enabled,speaker_region }) => ({ roi_id:roi_id||crypto.randomUUID(),x,y,width,height,name,lifetime:lifetime||{enabled:false,windows:[]},motion:motion||{enabled:false,keyframes:[]},speaker_enabled:!!speaker_enabled,speaker_region:speaker_region||null })); }
function readRoiVisibility() { try { const value=JSON.parse(localStorage.getItem(ROI_VISIBILITY_STORAGE_KEY)||"{}"); return value&&typeof value==="object"&&!Array.isArray(value)?value:{}; } catch { return {}; } }
function saveRoiVisibility(region,visible) { region.visible=visible; if(!region.roi_id)return; const saved=readRoiVisibility(); saved[region.roi_id]=visible; try { localStorage.setItem(ROI_VISIBILITY_STORAGE_KEY,JSON.stringify(saved)); } catch {} }
function updateRoiList() { const list = $("#roiList"); list.replaceChildren(); regions.forEach((region,index) => { const row=document.createElement("div"); row.className="roi-row"; const checkbox=document.createElement("input"); checkbox.type="checkbox"; checkbox.checked=region.enabled;checkbox.title="Ativar ou desativar a busca de texto nesta região."; checkbox.addEventListener("change",()=>{region.enabled=checkbox.checked;drawPreview();}); const name=document.createElement("span"); name.textContent=region.name; name.title=`x ${region.x.toFixed(3)}, y ${region.y.toFixed(3)}, ${region.width.toFixed(3)} × ${region.height.toFixed(3)}`; const visibility=document.createElement("button");visibility.type="button";visibility.className=`roi-visibility-button${region.visible===false?" is-hidden":""}`;visibility.innerHTML=region.visible===false?'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8"/><path d="M9.9 5.2A10.8 10.8 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.1 3.8M6.2 6.2C3.5 8.1 2 12 2 12s3.6 7 10 7a10.8 10.8 0 0 0 3-.4"/></svg>':'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';visibility.title=region.visible===false?`Exibir ${region.name} na prévia. A busca OCR continua independente.`:`Ocultar ${region.name} na prévia sem desativar a busca OCR.`;visibility.setAttribute("aria-label",visibility.title);visibility.setAttribute("aria-pressed",String(region.visible!==false));visibility.addEventListener("click",()=>{saveRoiVisibility(region,region.visible===false);updateRoiList();drawPreview();}); const color=document.createElement("i"); color.className="roi-color"; color.style.background=region.color; const settings=document.createElement("button");settings.type="button";settings.className="roi-settings-button";settings.textContent="⚙";settings.title=`Configurar tempo de vida, personagem e movimento da ${region.name}.`;settings.setAttribute("aria-label",`Configurar ${region.name}`);settings.addEventListener("click",()=>window.openRoiSettings?.(index)); const remove=document.createElement("button"); remove.type="button"; remove.textContent="×"; remove.title=`Remover ${region.name} da próxima análise.`; remove.addEventListener("click",()=>{regions.splice(index,1);updateRoiList();drawPreview();}); row.append(checkbox,name,visibility,color,settings,remove); list.append(row); }); }
function canvasPoint(event) { const canvas=$("#previewCanvas"), rect=canvas.getBoundingClientRect(); return { x:Math.max(0,Math.min(canvas.width,(event.clientX-rect.left)*canvas.width/rect.width)), y:Math.max(0,Math.min(canvas.height,(event.clientY-rect.top)*canvas.height/rect.height)) }; }
function roiRect(region) { const time=Number($("#videoPlayer").currentTime)||Number(currentSession?.source_start)||0,sourceStart=Number(currentSession?.source_start??durationValue($("#sourceStart").value||0));let box=region;const points=(region.motion?.enabled?(region.motion.keyframes||[]):[]).map((item)=>({...item,at:Number(item.time)+(item.mode==="relative"?sourceStart:0)})).sort((a,b)=>a.at-b.at);if(points.length){box=points[0];for(let i=1;i<points.length;i++){if(time>=points[i].at){box=points[i];continue;}const prev=points[i-1],next=points[i],f=Math.max(0,Math.min(1,(time-prev.at)/Math.max(.001,next.at-prev.at)));box=Object.fromEntries(["x","y","width","height"].map((key)=>[key,Number(prev[key])+(Number(next[key])-Number(prev[key]))*f]));break;}}return { left:box.x*$("#previewCanvas").width, top:box.y*$("#previewCanvas").height, width:box.width*$("#previewCanvas").width, height:box.height*$("#previewCanvas").height }; }
function roiActiveAt(region,time){if(!region.lifetime?.enabled)return true;const base=Number(currentSession?.source_start??durationValue($("#sourceStart").value||0));return(region.lifetime.windows||[]).some((window)=>{const offset=window.mode==="relative"?base:0;return time>=Number(window.start)+offset&&time<Number(window.end)+offset;});}
function drawPreview(dragRect = null) { const canvas=$("#previewCanvas"); if (!preview) return; let sx=0,sy=0,sw=preview.width,sh=preview.height; if ((previewKind==="desktop"||previewKind==="youtube")&&selectedDesktopRect) { sx=selectedDesktopRect.left; sy=selectedDesktopRect.top; sw=selectedDesktopRect.width; sh=selectedDesktopRect.height; }
  canvas.width=Math.max(1,Math.round(sw)); canvas.height=Math.max(1,Math.round(sh)); const ctx=canvas.getContext("2d"); ctx.clearRect(0,0,canvas.width,canvas.height); const useVideo=!$("#videoPlayer").classList.contains("hidden")&&Boolean(currentSession?.config_json?.downloaded_path||currentSession?.config_json?.path);if(!useVideo)ctx.drawImage(preview.image,sx,sy,sw,sh,0,0,canvas.width,canvas.height);canvas.classList.toggle("interactive",["roi","new-roi","capture","speaker-roi"].includes(previewMode));
  if ((previewKind==="desktop"||previewKind==="youtube")&&selectedDesktopRect&&previewMode==="capture") { ctx.strokeStyle="#2585ff";ctx.lineWidth=Math.max(2,canvas.width/500);ctx.strokeRect(1,1,canvas.width-2,canvas.height-2); }
  if(!["desktop","youtube"].includes(previewKind)||selectedDesktopRect)regions.forEach((region,index)=>{ if(region.visible===false)return; const r=roiRect(region),time=Number($("#videoPlayer").currentTime)||Number(currentSession?.source_start)||0,active=roiActiveAt(region,time),opacity=(region.enabled?1:.2)*(active?1:.38);ctx.globalAlpha=opacity;ctx.fillStyle=`${region.color}20`;ctx.strokeStyle=region.color;ctx.lineWidth=Math.max(2,canvas.width/650);ctx.fillRect(r.left,r.top,r.width,r.height);ctx.strokeRect(r.left,r.top,r.width,r.height);ctx.fillStyle=region.color;ctx.font=`bold ${Math.max(11,canvas.width/95)}px Segoe UI`;ctx.fillText(region.name+(region.enabled?(active?"":" · fora do tempo ativo"):" · busca desativada"),r.left+5,Math.max(14,r.top-4));ctx.fillRect(r.left+r.width-9,r.top+r.height-9,9,9);if(region.speaker_enabled&&region.speaker_region){const box=region.speaker_region,dx=r.left-region.x*canvas.width,dy=r.top-region.y*canvas.height;ctx.setLineDash([7,5]);ctx.strokeStyle="#69d2ef";ctx.strokeRect(box.x*canvas.width+dx,box.y*canvas.height+dy,box.width*canvas.width,box.height*canvas.height);ctx.setLineDash([]);ctx.fillStyle="#69d2ef";ctx.fillText("Personagem",box.x*canvas.width+dx+3,Math.max(13,box.y*canvas.height+dy-3));}ctx.globalAlpha=1; });
  if(dragRect){ctx.fillStyle=previewMode==="capture"?"#2585ff22":"#29a7ff22";ctx.strokeStyle=previewMode==="capture"?"#2585ff":"#29a7ff";ctx.lineWidth=2;ctx.fillRect(dragRect.left,dragRect.top,dragRect.width,dragRect.height);ctx.strokeRect(dragRect.left,dragRect.top,dragRect.width,dragRect.height);}
  $("#previewEmpty").classList.add("hidden");$("#resolutionBadge").textContent=`${Math.round(canvas.width)} × ${Math.round(canvas.height)}`;
}
function drawImage(info,kind){const image=new Image();image.onload=()=>{preview={...info,image,width:info.width||image.naturalWidth,height:info.height||image.naturalHeight};previewKind=kind;if(kind==="desktop"){selectedDesktopRect=null;previewMode="capture";}else if(kind==="file"||kind==="youtube"){selectedDesktopRect={left:0,top:0,width:preview.width,height:preview.height};previewMode="roi";}drawPreview();renderVideoOverview();$("#selectCaptureButton").classList.toggle("active",previewMode==="capture");$("#addRoiButton").classList.toggle("active",previewMode==="roi");if(kind==="desktop")$("#captureStatusBadge").textContent="SELECIONE O VÍDEO";if(kind==="youtube")$("#captureStatusBadge").textContent="VÍDEO LOCAL · EDITE AS ROIs";if(kind==="live")$("#captureStatusBadge").textContent="ANÁLISE VISUAL · PLAYER DISPONÍVEL";};image.src=info.image;}
function drawVideoPreview(){
  const player=$("#videoPlayer"),config=currentSession?.config_json||{};
  if(!player.videoWidth||!player.videoHeight||!(config.downloaded_path||config.path))return false;
  preview={image:player,width:player.videoWidth,height:player.videoHeight,video_duration:Number(config.video_duration)||Number(player.duration)||0};
  previewKind=currentSession?.source_type==="youtube"?"youtube":"file";
  previewMode="roi";
  if(previewKind==="file")selectedDesktopRect={left:0,top:0,width:preview.width,height:preview.height};
  drawPreview();renderVideoOverview();return true;
}
function videoSourceBounds(){const inputRange=(()=>{try{return getInitialRange();}catch{return{start:durationValue($("#sourceStart").value||0),duration:900};}})(),start=Number(currentSession?.source_start??currentSession?.config_json?.start??inputRange.start),requested=Number(currentSession?.config_json?.duration??currentSession?.requested_duration??inputRange.duration),videoDuration=Number.isFinite($("#videoPlayer").duration)?$("#videoPlayer").duration:Infinity,end=requested>0?Math.min(start+requested,videoDuration):videoDuration;return{start,end:Math.max(start,end),duration:Math.max(0,Math.min(requested||videoDuration-start,videoDuration-start))};}
function timelineIdentity(){const selected=events.find((event)=>event.event_id===selectedEventId);return $("#sessionFilter").value||selected?.session_id||activeSessionId;}
function getTimelineBounds(sessionId=timelineIdentity()){const session=sessions.find((item)=>item.session_id===sessionId),config=session?.config_json||{},inputRange=(()=>{try{return getInitialRange();}catch{return{start:durationValue($("#sourceStart").value||0),duration:900};}})(),start=Number(session?.source_start??config.start??inputRange.start),duration=Number(config.duration||session?.requested_duration||inputRange.duration);return{sessionId,start,end:start+Math.max(1,duration),span:Math.max(1,duration)};}
function saveTimelineView(sessionId,start,span){if(!sessionId)return;timelineViews[sessionId]={start,span};try{localStorage.setItem(TIMELINE_VIEW_STORAGE_KEY,JSON.stringify(timelineViews));}catch{/* local view persistence is optional */}}
function clampTimelineView(start,span,bounds){const visibleSpan=Math.max(.25,Math.min(bounds.span,span)),visibleStart=Math.max(bounds.start,Math.min(bounds.end-visibleSpan,start));return{start:visibleStart,span:visibleSpan,end:visibleStart+visibleSpan};}
function zoomTimeline(factor,anchorRatio=.5){const bounds=getTimelineBounds();if(!bounds.sessionId||bounds.span<=0)return;const ratio=Math.max(0,Math.min(1,anchorRatio)),nextSpan=Math.max(.25,Math.min(bounds.span,timelineRange.span*factor));if(Math.abs(nextSpan-timelineRange.span)<.001)return;const anchor=timelineRange.start+timelineRange.span*ratio,next=clampTimelineView(anchor-nextSpan*ratio,nextSpan,bounds);saveTimelineView(bounds.sessionId,next.start,next.span);renderTimeline();}
function fitTimeline(){const bounds=getTimelineBounds();if(!bounds.sessionId)return;delete timelineViews[bounds.sessionId];try{localStorage.setItem(TIMELINE_VIEW_STORAGE_KEY,JSON.stringify(timelineViews));}catch{/* local view persistence is optional */}renderTimeline();}
function centerTimelineOnPlayhead(){const bounds=getTimelineBounds();if(!bounds.sessionId)return;const next=clampTimelineView(videoClock()-timelineRange.span/2,timelineRange.span,bounds);saveTimelineView(bounds.sessionId,next.start,next.span);renderTimeline();}
function panTimelineTo(sourceTime,ratio=.2){const bounds=getTimelineBounds();if(!bounds.sessionId)return;const next=clampTimelineView(Number(sourceTime)-timelineRange.span*ratio,timelineRange.span,bounds);saveTimelineView(bounds.sessionId,next.start,next.span);renderTimeline();}
function captureTimelinePinItems(){const sessionId=timelineIdentity();return events.filter((item)=>item.session_id===sessionId).slice().sort((a,b)=>Number(a.start_time)-Number(b.start_time));}
function captureTimelineMoveStep(){const value=Number($("#timelineMoveStep")?.value);return Number.isFinite(value)&&value>0?Math.min(3600,value):1;}
function applyCaptureTimelinePinSelection(){
  const track=$("#timelineTrack");if(!track)return;
  const visible=[...track.querySelectorAll(".timeline-dialogue-point")];
  for(const point of visible){const selected=timelinePinSelectedIds.has(point.dataset.eventId);point.classList.toggle("multi-selected",selected);point.setAttribute("aria-pressed",String(selected));}
  const selected=selectedCaptureTimelineItems();
  for(const button of ["#timelineMovePinLeft","#timelineMovePinRight"]){const node=$(button);if(node)node.disabled=!selected.length;}
  const selectAll=$("#timelineSelectAllPins"),all=captureTimelinePinItems();
  if(selectAll){selectAll.classList.toggle("active",all.length>0&&all.every((item)=>timelinePinSelectedIds.has(item.event_id)));selectAll.title=all.length>0&&all.every((item)=>timelinePinSelectedIds.has(item.event_id))?"Limpar seleção de pinos":"Selecionar todos os pinos de diálogo da sessão";}
}
function selectedCaptureTimelineItems(){return captureTimelinePinItems().filter((item)=>timelinePinSelectedIds.has(item.event_id));}
function setCaptureTimelinePinSelection(ids,anchor=""){
  timelinePinSelectedIds=new Set(ids);timelinePinSelectionAnchor=anchor||[...timelinePinSelectedIds].at(-1)||"";
  applyCaptureTimelinePinSelection();
}
function selectCaptureTimelinePin(eventId,modifiers={}){
  const items=captureTimelinePinItems(),index=items.findIndex((item)=>item.event_id===eventId);if(index<0)return;
  let ids=new Set(timelinePinSelectedIds);
  if(modifiers.shift&&timelinePinSelectionAnchor){const anchor=items.findIndex((item)=>item.event_id===timelinePinSelectionAnchor);if(anchor>=0){ids=new Set(items.slice(Math.min(anchor,index),Math.max(anchor,index)+1).map((item)=>item.event_id));}else ids=new Set([eventId]);}
  else if(modifiers.ctrl||modifiers.meta){if(ids.has(eventId))ids.delete(eventId);else ids.add(eventId);}
  else ids=new Set([eventId]);
  if(!ids.size)ids.add(eventId);
  setCaptureTimelinePinSelection(ids,eventId);selectEvent(eventId,false,true,true);
}
function captureTimelinePinSvg(){return '<svg viewBox="0 0 16 30" aria-hidden="true"><rect class="pin-head" x="4" y="1" width="8" height="7" rx="1"/><path class="pin-stem" d="M8 8v19"/></svg>';}
function captureTimelineSelectionRect(track,left,width){let box=track.querySelector(":scope > .timeline-selection-rect");if(!box){box=document.createElement("div");box.className="timeline-selection-rect";track.append(box);}box.style.left=`${left}px`;box.style.width=`${width}px`;return box;}
function previewCapturePinDrag(drag,delta){
  const bounds=getTimelineBounds(drag.sessionId),minStart=Math.min(...drag.items.map((item)=>Number(item.start_time))),maxEnd=Math.max(...drag.items.map((item)=>Number(item.end_time??item.start_time)));
  const shift=Math.max(bounds.start-minStart,Math.min(bounds.end-maxEnd,delta));drag.delta=shift;
  for(const item of drag.items){const start=Number(item.start_time)+shift,end=Number(item.end_time??item.start_time)+shift,left=(start-timelineRange.start)/timelineRange.span*100,right=(end-timelineRange.start)/timelineRange.span*100;const point=$(`.timeline-dialogue-point[data-event-id="${CSS.escape(item.event_id)}"]`),block=$(`.timeline-event[data-event-id="${CSS.escape(item.event_id)}"]`);if(point)point.style.left=`${left}%`;if(block){block.style.left=`${Math.max(0,left)}%`;if(item.end_time!=null)block.style.width=`${Math.max(.8,Math.min(100,right)-Math.max(0,left))}%`;}}
}
async function moveSelectedCapturePins(delta){
  const items=selectedCaptureTimelineItems();if(!items.length){toast("Selecione um pino de diálogo para mover.",true);return;}
  const bounds=getTimelineBounds(items[0].session_id),minStart=Math.min(...items.map((item)=>Number(item.start_time))),maxEnd=Math.max(...items.map((item)=>Number(item.end_time??item.start_time))),shift=Math.max(bounds.start-minStart,Math.min(bounds.end-maxEnd,Number(delta)||0));if(Math.abs(shift)<.0005)return;
  const keep=new Set(items.map((item)=>item.event_id)),position=captureTimelinePosition();
  try{for(const item of items){const body={start_time:Number((Number(item.start_time)+shift).toFixed(3))};if(item.end_time!=null)body.end_time=Number((Number(item.end_time)+shift).toFixed(3));await api(`/api/events/${encodeURIComponent(item.event_id)}/review/update`,{method:"POST",body:JSON.stringify(body)});}timelinePinSelectedIds=keep;await refreshEvents(true,true,position);timelinePinSelectedIds=keep;applyCaptureTimelinePinSelection();toast(`${items.length} pino(s) movido(s) ${shift<0?"para a esquerda":"para a direita"}.`);}catch(error){timelinePinSelectedIds=keep;await refreshEvents(true,true,position);timelinePinSelectedIds=keep;applyCaptureTimelinePinSelection();toast(error.message,true);}
}
function initializeCaptureTimelinePins(){
  const track=$("#timelineTrack");if(!track||track.dataset.pinControlsBound)return;track.dataset.pinControlsBound="true";
  track.addEventListener("pointerdown",(event)=>{
    if(event.button!==0)return;const pin=event.target.closest(".timeline-dialogue-point");
    if(pin){event.preventDefault();event.stopPropagation();if(timelinePinSelectedIds.has(pin.dataset.eventId)&&!event.shiftKey&&!event.ctrlKey&&!event.metaKey)selectEvent(pin.dataset.eventId,false,true,true);else selectCaptureTimelinePin(pin.dataset.eventId,event);const selected=selectedCaptureTimelineItems();timelinePinDrag={sessionId:selected[0]?.session_id,items:selected,startX:event.clientX,width:Math.max(1,track.getBoundingClientRect().width),span:timelineRange.span,delta:0,moved:false};ignoreTimelineClick=true;track.setPointerCapture(event.pointerId);return;}
    const eventBlock=event.target.closest(".timeline-event");if(event.target.closest(".timeline-audio-marker,.timeline-missing-point")||(eventBlock&&!event.shiftKey))return;
    const rect=track.getBoundingClientRect();timelineMarqueeDrag={pointerId:event.pointerId,startX:event.clientX,startY:event.clientY,rect,moved:false,additive:event.ctrlKey||event.metaKey};track.classList.add("drawing-selection");captureTimelineSelectionRect(track,event.clientX-rect.left,0);track.setPointerCapture(event.pointerId);if(eventBlock){event.preventDefault();event.stopImmediatePropagation();}
  },true);
  track.addEventListener("pointermove",(event)=>{
    if(timelinePinDrag){const dx=event.clientX-timelinePinDrag.startX;if(Math.abs(dx)>2)timelinePinDrag.moved=true;previewCapturePinDrag(timelinePinDrag,dx/Math.max(1,timelinePinDrag.width)*timelinePinDrag.span);event.preventDefault();return;}
    if(!timelineMarqueeDrag||timelineMarqueeDrag.pointerId!==event.pointerId)return;const drag=timelineMarqueeDrag,left=Math.min(drag.startX,event.clientX)-drag.rect.left,width=Math.abs(event.clientX-drag.startX);if(width>4)drag.moved=true;const box=captureTimelineSelectionRect(track,Math.max(0,left),Math.min(width,drag.rect.width));box.style.top="0";box.style.bottom="0";event.preventDefault();
  },true);
  track.addEventListener("pointerup",(event)=>{
    if(timelinePinDrag){const drag=timelinePinDrag;timelinePinDrag=null;track.querySelector(":scope > .timeline-selection-rect")?.remove();track.classList.remove("drawing-selection");setTimeout(()=>ignoreTimelineClick=false,0);if(drag.moved)void moveSelectedCapturePins(drag.delta);else applyCaptureTimelinePinSelection();event.preventDefault();event.stopImmediatePropagation();return;}
    if(!timelineMarqueeDrag||timelineMarqueeDrag.pointerId!==event.pointerId)return;const drag=timelineMarqueeDrag;timelineMarqueeDrag=null;track.querySelector(":scope > .timeline-selection-rect")?.remove();track.classList.remove("drawing-selection");if(drag.moved){const left=Math.min(drag.startX,event.clientX),right=Math.max(drag.startX,event.clientX),ids=[...track.querySelectorAll(".timeline-dialogue-point")].filter((pin)=>{const rect=pin.getBoundingClientRect(),x=rect.left+rect.width/2;return x>=left&&x<=right;}).map((pin)=>pin.dataset.eventId);setCaptureTimelinePinSelection(drag.additive?new Set([...timelinePinSelectedIds,...ids]):ids,ids.at(-1)||"");ignoreTimelineClick=true;setTimeout(()=>ignoreTimelineClick=false,0);event.preventDefault();event.stopImmediatePropagation();}
  },true);
  track.addEventListener("pointercancel",()=>{timelinePinDrag=null;timelineMarqueeDrag=null;track.querySelector(":scope > .timeline-selection-rect")?.remove();track.classList.remove("drawing-selection");renderTimeline();},true);
  track.addEventListener("click",(event)=>{if(ignoreTimelineClick){event.stopImmediatePropagation();return;}const pin=event.target.closest(".timeline-dialogue-point"),block=event.target.closest(".timeline-event");if(pin||block){const id=(pin||block).dataset.eventId;if(id){event.preventDefault();event.stopImmediatePropagation();selectCaptureTimelinePin(id,event);} }},true);
}
function renderTimelineNavigator(bounds,view){
  const track=$("#timelineNavigatorTrack"),thumb=$("#timelineNavigatorWindow");if(!track||!thumb)return;
  const width=track.getBoundingClientRect().width,maxStart=Math.max(0,bounds.span-view.span),travel=width>0?Math.max(0,width-Math.min(width,Math.max(24,width*view.span/bounds.span))):0;
  const thumbWidth=width>0?Math.min(width,Math.max(24,width*view.span/bounds.span)):0,ratio=maxStart>.001?(view.start-bounds.start)/maxStart:0;
  thumb.style.width=`${thumbWidth}px`;thumb.style.left=`${Math.max(0,Math.min(1,ratio))*travel}px`;thumb.disabled=!bounds.sessionId||maxStart<=.001||width<24;
  thumb.setAttribute("aria-valuenow",String(Math.round(Math.max(0,Math.min(1,ratio))*1000)));
  thumb.setAttribute("aria-valuetext",`${timecode(view.start)}–${timecode(view.end)} de ${timecode(bounds.start)}–${timecode(bounds.end)}`);
  $("#timelineNavigatorStart").textContent=timecode(bounds.start);$("#timelineNavigatorEnd").textContent=timecode(bounds.end);
  track.title=thumb.disabled?`Bloco completo · ${timecode(bounds.start)}–${timecode(bounds.end)}`:`Janela visível ${timecode(view.start)}–${timecode(view.end)} · arraste ou clique para navegar pelo bloco completo`;
}
function setTimelineNavigatorStart(start,bounds=getTimelineBounds(),span=timelineRange.span){if(!bounds.sessionId)return;const next=clampTimelineView(start,span,bounds);saveTimelineView(bounds.sessionId,next.start,next.span);renderTimeline();}
function initializeTimelineNavigator(){
  initializeEventTableColumns();
  const track=$("#timelineNavigatorTrack"),thumb=$("#timelineNavigatorWindow");if(!track||!thumb)return;
  track.addEventListener("click",(event)=>{
    if(event.target.closest("#timelineNavigatorWindow"))return;
    const bounds=getTimelineBounds();if(!bounds.sessionId||timelineRange.span>=bounds.span)return;
    const rect=track.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-rect.left)/Math.max(1,rect.width)));
    setTimelineNavigatorStart(bounds.start+ratio*bounds.span-timelineRange.span/2,bounds);
  });
  thumb.addEventListener("pointerdown",(event)=>{
    if(event.button!==0||thumb.disabled)return;const bounds=getTimelineBounds(),rect=track.getBoundingClientRect(),thumbWidth=thumb.getBoundingClientRect().width,maxPixels=Math.max(1,rect.width-thumbWidth);
    event.preventDefault();timelineNavigatorDrag={pointerId:event.pointerId,startX:event.clientX,start:timelineRange.start,span:timelineRange.span,bounds,maxPixels,maxShift:Math.max(0,bounds.span-timelineRange.span)};thumb.setPointerCapture(event.pointerId);
  });
  thumb.addEventListener("pointermove",(event)=>{
    if(!timelineNavigatorDrag||timelineNavigatorDrag.pointerId!==event.pointerId)return;const drag=timelineNavigatorDrag,delta=(event.clientX-drag.startX)/drag.maxPixels*drag.maxShift;
    setTimelineNavigatorStart(drag.start+delta,drag.bounds,drag.span);
  });
  const finish=(event)=>{if(timelineNavigatorDrag?.pointerId===event.pointerId)timelineNavigatorDrag=null;};thumb.addEventListener("pointerup",finish);thumb.addEventListener("pointercancel",finish);
  thumb.addEventListener("keydown",(event)=>{
    if(!["ArrowLeft","ArrowRight","PageUp","PageDown","Home","End"].includes(event.key))return;const bounds=getTimelineBounds();if(!bounds.sessionId||timelineRange.span>=bounds.span)return;
    const step=timelineRange.span*(event.shiftKey ? .5 : .1);let start=timelineRange.start;
    if(event.key==="ArrowLeft")start-=step;else if(event.key==="ArrowRight")start+=step;else if(event.key==="PageUp")start-=timelineRange.span*.8;else if(event.key==="PageDown")start+=timelineRange.span*.8;else if(event.key==="Home")start=bounds.start;else start=bounds.end-timelineRange.span;
    event.preventDefault();setTimelineNavigatorStart(start,bounds);
  });
  if("ResizeObserver" in window){const observer=new ResizeObserver(()=>{const bounds=getTimelineBounds(),saved=timelineViews[bounds.sessionId]||{start:bounds.start,span:bounds.span};renderTimelineNavigator(bounds,clampTimelineView(Number(saved.start)||bounds.start,Number(saved.span)||bounds.span,bounds));});observer.observe(track);}
}
function updateTimelinePlayback(sourceTime){
  const cursor=$("#timelineCursor");if(!cursor)return;
  const position=Math.max(0,Math.min(100,(sourceTime-timelineRange.start)/Math.max(.001,timelineRange.span)*100));
  cursor.style.left=`${position}%`;
  if(sourceTime<timelineRange.start||sourceTime>timelineRange.end)panTimelineTo(sourceTime,.2);
  const previousTime=lastTimelinePlaybackTime;lastTimelinePlaybackTime=sourceTime;
  if(!followDialogue)return;
  const candidates=events.filter((item)=>item.session_id===activeSessionId&&item.review_status!=="discarded"&&item.review_status!=="merged");
  const player=$("#videoPlayer"),isPlaying=!player.paused&&!player.seeking;
  let event=null;
  if(isPlaying&&previousTime!=null&&sourceTime!==previousTime){
    const low=Math.min(previousTime,sourceTime),high=Math.max(previousTime,sourceTime);
    event=candidates.filter((item)=>Number(item.start_time)>low&&Number(item.start_time)<=high).sort((a,b)=>Math.abs(sourceTime-Number(a.start_time))-Math.abs(sourceTime-Number(b.start_time)))[0]||null;
  }
  if(!event){
    const pointTolerance=Math.max(.08,Math.min(.18,6*timelineRange.span/Math.max(1,$("#timelineTrack").getBoundingClientRect().width)));
    event=candidates.filter((item)=>Math.abs(sourceTime-Number(item.start_time))<=pointTolerance).sort((a,b)=>Math.abs(sourceTime-Number(a.start_time))-Math.abs(sourceTime-Number(b.start_time)))[0]||null;
  }
  if(event&&event.event_id!==selectedEventId)selectEvent(event.event_id,false);
}
function startTimelinePlaybackTracking(){
  const player=$("#videoPlayer");if(player.paused)return;
  if(typeof player.requestVideoFrameCallback==="function"){
    if(timelineVideoFrameId)return;
    const update=()=>{timelineVideoFrameId=0;if(player.paused)return;updateTimelinePlayback(player.currentTime);timelineVideoFrameId=player.requestVideoFrameCallback(update);};
    timelineVideoFrameId=player.requestVideoFrameCallback(update);return;
  }
  if(timelineAnimationFrameId)return;
  const update=()=>{timelineAnimationFrameId=0;if(player.paused)return;updateTimelinePlayback(player.currentTime);timelineAnimationFrameId=requestAnimationFrame(update);};
  timelineAnimationFrameId=requestAnimationFrame(update);
}
function stopTimelinePlaybackTracking(){
  const player=$("#videoPlayer");
  if(timelineVideoFrameId&&typeof player.cancelVideoFrameCallback==="function")player.cancelVideoFrameCallback(timelineVideoFrameId);
  if(timelineAnimationFrameId)cancelAnimationFrame(timelineAnimationFrameId);
  timelineVideoFrameId=0;timelineAnimationFrameId=0;
}
function updatePreviewToggle(){const player=$("#videoPlayer"),button=$("#previewToggle");if(!player||!button)return;const paused=player.paused;button.textContent=paused?"▶":"Ⅱ";button.setAttribute("aria-label",paused?"Reproduzir vídeo":"Pausar vídeo");button.title=paused?"Reproduzir vídeo":"Pausar vídeo";button.disabled=player.classList.contains("hidden");}
function togglePreviewPlayback(){const player=$("#videoPlayer"),bounds=videoSourceBounds();if(player.classList.contains("hidden"))return;if(player.paused){if(player.currentTime>=bounds.end-.03||player.currentTime<bounds.start)player.currentTime=bounds.start;player.play().catch(()=>toast("Não foi possível reproduzir este vídeo no Chromium.",true));}else player.pause();}
function updatePlayerDisplay(){const player=$("#videoPlayer"),slider=$("#seekSlider");updatePreviewToggle();if(!player||!Number.isFinite(player.duration))return;const bounds=videoSourceBounds();if(player.currentTime<bounds.start-.05){player.currentTime=bounds.start;return;}if(player.currentTime>=bounds.end-.03){if(!player.paused)player.pause();if(Math.abs(player.currentTime-bounds.end)>.001)player.currentTime=bounds.end;}slider.disabled=false;slider.min=String(bounds.start);slider.max=String(bounds.end);slider.value=String(Math.max(bounds.start,Math.min(bounds.end,player.currentTime||bounds.start)));$("#previewTime").textContent=timecode(player.currentTime||bounds.start)+" / "+timecode(bounds.end);updateTimelinePlayback(player.currentTime||bounds.start);updateVideoOverviewCursor();if(preview&&regions.some((item)=>item.motion?.enabled||item.lifetime?.enabled))drawPreview();}function loadSessionPlayer(session){const config=session?.config_json||{},path=config.downloaded_path||config.path;if(!session||!path){const player=$("#videoPlayer");player.pause();player.removeAttribute("src");player.load();player.classList.add("hidden");$("#seekSlider").disabled=true;updatePreviewToggle();return;}const player=$("#videoPlayer"),key=`${session.session_id}|${path}`;player.classList.remove("hidden");player.muted=true;player.dataset.pendingStart=String(Number(session.source_start??config.start??0));if(player.dataset.key!==key){player.dataset.key=key;delete player.dataset.seekInitialized;player.src=`/api/sessions/${encodeURIComponent(session.session_id)}/video?project_id=${encodeURIComponent(window.DUBSYNC_PROJECT_ID||"")}`;player.load();}if(player.readyState>=1){const wanted=Number(player.dataset.pendingStart)||0;if(Math.abs(player.currentTime-wanted)>.5&&player.dataset.seekInitialized!==key){player.currentTime=Math.min(wanted,Math.max(0,player.duration||wanted));player.dataset.seekInitialized=key;}updatePlayerDisplay();}}
async function showSourceTimeInPlayer(sourceTime){
  const player=$("#videoPlayer");if(player.classList.contains("hidden"))return false;
  if(player.readyState<1||!Number.isFinite(player.duration)){
    const loaded=await new Promise((resolve)=>{let timer;const finish=(ready)=>{clearTimeout(timer);player.removeEventListener("loadedmetadata",onReady);player.removeEventListener("error",onError);resolve(ready);},onReady=()=>finish(true),onError=()=>finish(false);player.addEventListener("loadedmetadata",onReady,{once:true});player.addEventListener("error",onError,{once:true});timer=setTimeout(()=>finish(false),12000);});
    if(!loaded)return false;
  }
  const target=Math.max(0,Math.min(player.duration,Number(sourceTime)||0));player.pause();
  if(Math.abs(player.currentTime-target)>.025){
    const seeked=new Promise((resolve)=>{let timer;const finish=()=>{clearTimeout(timer);player.removeEventListener("seeked",finish);resolve();};player.addEventListener("seeked",finish,{once:true});timer=setTimeout(finish,2500);});
    player.currentTime=target;await seeked;
  }
  lastTimelinePlaybackTime=target;updatePlayerDisplay();
  if(preview)drawPreview();else drawVideoPreview();
  return Math.abs(player.currentTime-target)<.25;
}
function showDownloadedOrigin(session){
  const config=session?.config_json||{},origin=config.source_origin||{},videoPath=config.downloaded_path||config.path||"",normalizedPath=videoPath.replace(/\\/g,"/");
  const libraryVideo=normalizedPath.toLowerCase().includes("/videos/")&&/\.(webm|mp4|mkv|mov)$/i.test(normalizedPath);
  const downloaded=Boolean(config.downloaded_path||libraryVideo),isYoutube=downloaded&&(origin.provider==="youtube"||session.source_type==="youtube"||libraryVideo);
  $("#fileOrigin").classList.toggle("hidden",!isYoutube);
  if(!downloaded)return;
  $("#filePath").value=videoPath;$("#filePath").title=videoPath;
  if(isYoutube){
    const fileName=videoPath.split(/[\\/]/).pop()||"vídeo",stem=fileName.replace(/\.[^.]+$/,""),idMatch=/\[([A-Za-z0-9_-]{11})\]$/.exec(stem);
    const inferredTitle=stem.replace(/\s*\[[A-Za-z0-9_-]{11}\]$/,"")||stem;
    const title=config.video_title||origin.title||inferredTitle,resolution=config.video_width&&config.video_height?config.video_width+" × "+config.video_height:"vídeo local",size=formatBytes(config.video_size_bytes||0),videoId=config.video_id||origin.video_id||idMatch?.[1]||"—";
    $("#fileOriginDetails").textContent=!config.downloaded_path&&libraryVideo?title+" · baixado do YouTube · pasta videos/ · ID "+videoId:title+" · "+resolution+" · "+size+" · ID "+videoId;
  }
}
async function activateDownloadedFile(session){if(!session?.config_json?.downloaded_path)return;showDownloadedOrigin(session);if(fileOrganizationForSession!==session.session_id){fileOrganizationForSession=session.session_id;try{const location=await api(`/api/sessions/${session.session_id}/organize-video`,{method:"POST",body:"{}"});session.config_json.downloaded_path=location.path;session.config_json.video_library_dir=location.directory;if(currentSession?.session_id===session.session_id){currentSession.config_json.downloaded_path=location.path;currentSession.config_json.video_library_dir=location.directory;}$("#filePath").value=location.path;$("#filePath").title=location.path;}catch(error){fileOrganizationForSession="";toast(`Vídeo baixado, mas não foi possível organizá-lo na pasta do projeto: ${error.message}`,true);}}
  if(mode!=="file")setMode("file",true);showDownloadedOrigin(session);loadSessionPlayer(session);}
$("#videoPlayer").addEventListener("loadedmetadata",()=>{const player=$("#videoPlayer"),key=player.dataset.key;if(player.dataset.seekInitialized!==key){const start=videoSourceBounds().start;player.currentTime=Math.min(start,player.duration||start);player.dataset.seekInitialized=key;}updatePlayerDisplay();if(preview)drawPreview();else drawVideoPreview();});$("#videoPlayer").addEventListener("timeupdate",updatePlayerDisplay);$("#videoPlayer").addEventListener("play",()=>{updatePreviewToggle();startTimelinePlaybackTracking();});$("#videoPlayer").addEventListener("pause",()=>{updatePreviewToggle();stopTimelinePlaybackTracking();});$("#videoPlayer").addEventListener("seeked",updatePlayerDisplay);$("#videoPlayer").addEventListener("ended",()=>{stopTimelinePlaybackTracking();updatePlayerDisplay();});
$("#videoPlayer").addEventListener("loadedmetadata",updateNextBlockControl);
function rectFrom(a,b){return{left:Math.min(a.x,b.x),top:Math.min(a.y,b.y),width:Math.abs(a.x-b.x),height:Math.abs(a.y-b.y)};}
function regionAt(point){for(let i=regions.length-1;i>=0;i--){if(regions[i].visible===false)continue;const r=roiRect(regions[i]);if(point.x>=r.left&&point.x<=r.left+r.width&&point.y>=r.top&&point.y<=r.top+r.height)return{i,r};}return null;}
function commitRegionRect(index,rect){const canvas=$("#previewCanvas");const x=Math.max(0,rect.left),y=Math.max(0,rect.top),right=Math.min(canvas.width,rect.left+rect.width),bottom=Math.min(canvas.height,rect.top+rect.height);if(right-x<8||bottom-y<8)return;if(index==null){const n=regions.length+1;regions.push({roi_id:crypto.randomUUID(),x:x/canvas.width,y:y/canvas.height,width:(right-x)/canvas.width,height:(bottom-y)/canvas.height,name:`ROI ${n}`,color:["#24a4ee","#dd9d2e","#b26be5","#43c4a5"][regions.length%4],enabled:true,visible:true});}else{regions[index].x=x/canvas.width;regions[index].y=y/canvas.height;regions[index].width=(right-x)/canvas.width;regions[index].height=(bottom-y)/canvas.height;}updateRoiList();drawPreview();}
$("#previewCanvas").addEventListener("pointerdown",(event)=>{if(!preview)return;const point=canvasPoint(event);$("#previewCanvas").setPointerCapture(event.pointerId);if(previewMode==="speaker-roi"){drag={mode:"speaker",start:point,index:window.speakerRoiIndex};return;}if(previewMode==="capture"&&["desktop","youtube"].includes(previewKind)&&!selectedDesktopRect){drag={mode:"capture",start:point};return;}if(previewMode==="new-roi"){drag={mode:"new",start:point,index:null};return;}const hit=regionAt(point);if(previewMode==="roi"){if(hit){const grip=point.x>hit.r.left+hit.r.width-13&&point.y>hit.r.top+hit.r.height-13;drag={mode:grip?"resize":"move",start:point,index:hit.i,rect:{...hit.r}};}else{drag={mode:"new",start:point,index:null};}return;}drag=null;});
$("#previewCanvas").addEventListener("pointermove",(event)=>{if(!drag)return;const point=canvasPoint(event);if(["capture","new","speaker"].includes(drag.mode)){drawPreview(rectFrom(drag.start,point));return;}let rect={...drag.rect};if(drag.mode==="move"){rect.left+=point.x-drag.start.x;rect.top+=point.y-drag.start.y;}else{rect.width=Math.max(8,rect.width+point.x-drag.start.x);rect.height=Math.max(8,rect.height+point.y-drag.start.y);}drawPreview();const ctx=$("#previewCanvas").getContext("2d");ctx.fillStyle=`${regions[drag.index].color}20`;ctx.strokeStyle=regions[drag.index].color;ctx.lineWidth=2;ctx.fillRect(rect.left,rect.top,rect.width,rect.height);ctx.strokeRect(rect.left,rect.top,rect.width,rect.height);drag.current=rect;});
$("#previewCanvas").addEventListener("pointerup",(event)=>{
  if(!drag)return;
  const point=canvasPoint(event);
  if(drag.mode==="capture"){
    const rect=rectFrom(drag.start,point);
    if(rect.width<96||rect.height<96){drag=null;drawPreview();return toast("Selecione uma área de vídeo maior que 96 × 96 px.",true);}
    selectedDesktopRect={left:Math.round(rect.left),top:Math.round(rect.top),width:Math.round(rect.width),height:Math.round(rect.height)};
    previewMode="roi";$("#captureStatusBadge").textContent="ÁREA SELECIONADA · EDITE AS ROIs";$("#selectCaptureButton").classList.remove("active");$("#addRoiButton").classList.add("active");drawPreview();
  }else if(drag.mode==="speaker"){
    const rect=rectFrom(drag.start,point),canvas=$("#previewCanvas"),speakerIndex=drag.index;
    const selected=rect.width>=8&&rect.height>=8&&Boolean(regions[speakerIndex]);
    if(selected){
      regions[speakerIndex].speaker_region={x:rect.left/canvas.width,y:rect.top/canvas.height,width:rect.width/canvas.width,height:rect.height/canvas.height};
      regions[speakerIndex].speaker_enabled=true;updateRoiList();toast("Área do nome do personagem marcada.");
    }
    previewMode="roi";drawPreview();window.roiSpeakerSelectionComplete?.(speakerIndex,selected);
  }else if(drag.mode==="new"){commitRegionRect(null,rectFrom(drag.start,point));}
  else if(drag.current){commitRegionRect(drag.index,drag.current);}
  drag=null;
});
window.addEventListener("keydown",(event)=>{
  if(event.key!=="Escape"||previewMode!=="speaker-roi")return;
  const speakerIndex=window.speakerRoiIndex;previewMode="roi";drawPreview();window.roiSpeakerSelectionComplete?.(speakerIndex,false);
});

async function prepareYoutube(){
  if(preparePending)return;
  preparePending=true;updatePlaybackButton();
  try{
    clearInterval(pollTimer);clearInterval(livePreviewTimer);
    selectedDesktopRect=null;preview=null;previewKind="";
    $("#previewEmpty").classList.remove("hidden");
    const {start,end,duration}=getInitialRange();
    const url=$("#youtubeUrl").value.trim();
    const result=await api("/api/youtube/prepare",{method:"POST",body:JSON.stringify({
      url,start,duration,language:$("#language").value,segment_duration_seconds:Number($("#segmentDuration").value)||60
    })});
    activeSessionId=result.session_id;currentSession=null;
    await refreshSessions();
    $("#captureStatusBadge").textContent=result.reused?"INTERVALO JÁ CONCLUÍDO":"BAIXANDO VÍDEO";
    if(result.reused)toast("Este intervalo já foi analisado; reabri a sessão existente.");
    else if(result.resumed)toast("Retomando o download parcial preservado.");
    else toast(`Baixando o vídeo para o primeiro bloco: ${timecode(start)} → ${timecode(end)} (${timecode(duration)}).`);
    await loadCoverage(url,true);
    await refreshSession();
    pollTimer=setInterval(refreshSession,1500);
  }catch(error){
    if(error.message.includes("Encerre a sessão ativa antes de iniciar outra sessão.")){
      preparePending=false;
      await syncActiveSession();
      if(activeState()){toast("Sessão ativa conectada. Continue pela prévia.");return;}
    }
    toast(error.message,true);
  }
  finally{preparePending=false;updatePlaybackButton();}
}
async function beginYoutube(){
  if(preparePending||!preview)return toast("Aguarde o download e ajuste as ROIs sobre a prévia local.",true);
  preparePending=true;updatePlaybackButton();
  try{
    const {start,end}=getInitialRange();
    await api("/api/youtube/"+activeSessionId+"/start",{method:"POST",body:JSON.stringify({regions:parseRegions()})});
    $("#captureStatusBadge").textContent="ANÁLISE DO VÍDEO LOCAL";
    $("#previewOverlayState").classList.remove("hidden");
    toast(`Primeiro bloco iniciado: ${timecode(start)} → ${timecode(end)}.`);
    await refreshSession();await refreshSessions();
  }catch(error){toast(error.message,true);}
  finally{preparePending=false;updatePlaybackButton();}
}
function clearSessionPlayerForFilePreview(){const player=$("#videoPlayer");player.pause();player.removeAttribute("src");player.load();delete player.dataset.key;delete player.dataset.pendingStart;delete player.dataset.seekInitialized;player.classList.add("hidden");$("#seekSlider").disabled=true;updatePreviewToggle();}
async function previewFile(){try{const path=$("#filePath").value.trim();if(!path)throw new Error("Informe o caminho do arquivo.");const query=new URLSearchParams({path,start:String(durationValue($("#sourceStart").value))}),image=await api(`/api/file-preview?${query}`),sessionPath=currentSession?.config_json?.downloaded_path||currentSession?.config_json?.path;if(!sessionPath||sessionPath.toLowerCase()!==path.toLowerCase())clearSessionPlayerForFilePreview();drawImage(image,"file");}catch(error){toast(error.message,true);}}
async function chooseVideoFile(){if(activeState()){toast("Encerre a sessão ativa antes de trocar o arquivo de vídeo.",true);return;}const button=$("#openVideoFile");button.disabled=true;try{const result=await api("/api/video-library/select-file",{method:"POST",body:"{}"});if(result.cancelled)return;document.querySelector('.source-tab[data-mode="file"]').click();$("#filePath").value=result.path;$("#filePath").title=result.path;showDownloadedOrigin(null);await previewFile();toast(`Vídeo selecionado: ${result.path.split("\\").pop()}`);}catch(error){toast(error.message,true);}finally{button.disabled=false;}}
async function beginFile(){
  if(preparePending)return;
  preparePending=true;updatePlaybackButton();
  try{
    const {start,end,duration}=getInitialRange();
    const path=$("#filePath").value.trim(),config=currentSession?.config_json||{};
    const savedPath=String(config.downloaded_path||config.path||currentSession?.source_uri||"").trim();
    const samePath=path&&savedPath&&path.toLocaleLowerCase("pt-BR")===savedPath.toLocaleLowerCase("pt-BR");
    const sameRange=currentSession&&Math.abs(start-Number(currentSession.source_start))<.001&&
      Math.abs(duration-Number(currentSession.requested_duration))<.001;
    const canResume=currentSession?.source_type==="file"&&["stopped","failed"].includes(currentSession.state)&&samePath&&sameRange;
    const result=canResume
      ?await api(`/api/file/${encodeURIComponent(currentSession.session_id)}/resume`,{method:"POST",body:"{}"})
      :await api("/api/file/start",{method:"POST",body:JSON.stringify({
        path,start,duration,language:$("#language").value,regions:parseRegions()
      })});
    activeSessionId=result.session_id;currentSession=null;await refreshSessions();await refreshSession();
    $("#sourceDuration").value=timecode(start+result.duration);updateInitialRangeUi(currentSession);pollTimer=setInterval(refreshSession,1400);
    toast(result.resumed?`Retomando o bloco ${result.block_index} desde ${timecode(start)}. Nenhum diálogo salvo será perdido.`:result.duration<duration?`Primeiro bloco ajustado ao fim do vídeo: ${timecode(start)} → ${timecode(start+result.duration)}. Eventos coincidentes serão substituídos.`:`Primeiro bloco iniciado: ${timecode(start)} → ${timecode(end)}. Eventos coincidentes serão substituídos.`);
  }catch(error){toast(error.message,true);}
  finally{preparePending=false;updatePlaybackButton();}
}
function updateNextBlockControl(){
  const button=$("#nextSegmentButton"),field=$("#nextBlockMinutes");if(!button||!field)return;
  const config=currentSession?.config_json||{},path=config.downloaded_path||config.path;
  const total=Number(config.video_duration||$("#videoPlayer").duration),start=Number(currentSession?.source_start||0),duration=Number(config.duration||currentSession?.requested_duration||0),remaining=total-(start+duration);
  const ready=currentSession?.state==="complete"&&Boolean(path)&&Number.isFinite(remaining)&&remaining>.001;
  const desired=Math.max(1,Number(field.value)||20)*60,actual=ready?Math.min(desired,remaining):desired;
  button.disabled=!ready||preparePending;button.textContent=ready?`Próximo bloco · ${timecode(actual)} ▶`:currentSession?.state==="complete"&&path?"Vídeo finalizado":"Próximo bloco ▶";
  button.title=ready?`Começa em ${timecode(start+duration)} e analisa ${timecode(actual)}. Se restar menos tempo no vídeo, usa somente o restante.`:"Disponível quando a análise do arquivo atual terminar e ainda houver vídeo.";
}
function overviewIndex(session){const config=session?.config_json||{};return Math.max(1,Number(config.block_index||(config.previous_session_id?2:1)));}
function overviewChain(){
  const byId=new Map();for(const item of [...overviewSessions,...sessions])byId.set(item.session_id,item);if(currentSession)byId.set(currentSession.session_id,currentSession);
  const all=[...byId.values()];let root=currentSession?byId.get(currentSession.session_id):null;
  if(!root)root=all.filter((item)=>!(item.config_json||{}).previous_session_id).sort((a,b)=>String(b.created_at||"").localeCompare(String(a.created_at||"")))[0]||null;
  if(!root)return[];const ancestors=[],seen=new Set();let node=root;
  while(node&&!seen.has(node.session_id)){ancestors.push(node);seen.add(node.session_id);const previousId=node.config_json?.previous_session_id;node=previousId?byId.get(previousId):null;}
  const chain=ancestors.reverse();node=chain.at(-1);
  while(node&&chain.length<5000){const children=all.filter((item)=>item.config_json?.previous_session_id===node.session_id).sort((a,b)=>String(b.created_at||"").localeCompare(String(a.created_at||"")));const child=children[0];if(!child||seen.has(child.session_id))break;chain.push(child);seen.add(child.session_id);node=child;}
  return chain;
}
async function loadVideoOverviewPlan(session=currentSession,force=false){
  const id=session?.session_id;if(!id){overviewSessionId="";overviewSessions=[];overviewVideoDuration=0;renderVideoOverview();return false;}
  if(!force&&overviewSessionId===id)return true;const cachedSessions=overviewSessions;overviewSessionId=id;overviewSessions=[];overviewVideoDuration=Number(session.config_json?.video_duration)||0;renderVideoOverview();
  try{const result=await api(`/api/sessions/${encodeURIComponent(id)}/video-plan`);if(overviewSessionId!==id)return false;overviewSessions=result.sessions||[];overviewVideoDuration=Number(result.video_duration)||overviewVideoDuration;}
  catch(error){if(overviewSessionId===id){overviewSessions=cachedSessions.length?cachedSessions:[session];console.warn("Não foi possível carregar o plano completo do vídeo:",error);renderVideoOverview();}return false;}
  renderVideoOverview();return true;
}
function updateVideoOverviewCursor(totalOverride){
  const cursor=$("#overviewCursor"),player=$("#videoPlayer");if(!cursor)return;const total=Number(totalOverride)||Number(currentSession?.config_json?.video_duration)||overviewVideoDuration||Number(preview?.video_duration)||Number(player.duration)||0;
  if(!(total>0)){cursor.hidden=true;return;}cursor.hidden=false;const sourceTime=!player.classList.contains("hidden")&&Number.isFinite(player.currentTime)?player.currentTime:Number(currentSession?.progress?.current_source_time??currentSession?.current_source_time??currentSession?.source_start??0);cursor.style.left=`${Math.max(0,Math.min(100,sourceTime/total*100))}%`;
}
async function openVideoOverviewBlock(sessionId,sourceTime){
  if(!sessionId){seekVideoTo(sourceTime);return;}if(activeState()&&sessionId!==activeSessionId)return toast("Aguarde o bloco atual terminar antes de abrir outro.",true);
  try{let session=sessions.find((item)=>item.session_id===sessionId)||overviewSessions.find((item)=>item.session_id===sessionId);if(!session||!session.config_json?.path&&!session.config_json?.downloaded_path){session=await api(`/api/sessions/${encodeURIComponent(sessionId)}`);}if(sessionId!==activeSessionId)await attachActiveSession(session);await showSourceTimeInPlayer(sourceTime);}
  catch(error){toast(error.message,true);}
}
function updatePreviousBlockButton(){
  const button=$("#previousBlockButton");if(!button)return;const previousId=currentSession?.config_json?.previous_session_id,index=overviewIndex(currentSession);button.disabled=!previousId||activeState(currentSession?.state)||preparePending;
  const previous=overviewSessions.find((item)=>item.session_id===previousId),previousStart=Number(previous?.source_start??0),previousEnd=previousStart+Number(previous?.config_json?.duration??previous?.requested_duration??0);button.title=previousId?`Abrir o bloco ${Math.max(1,index-1)}${previous?` · ${timecode(previousStart)}–${timecode(previousEnd)}`:""}.`:"Este é o primeiro bloco do vídeo.";
}
async function goToPreviousBlock(){
  if(!currentSession?.config_json?.previous_session_id||activeState(currentSession.state)||preparePending)return;const previousId=currentSession.config_json.previous_session_id;
  try{const previous=await api(`/api/sessions/${encodeURIComponent(previousId)}`);await attachActiveSession(previous);await showSourceTimeInPlayer(Number(previous.source_start)||0);toast(`Bloco ${overviewIndex(previous)} aberto em ${timecode(previous.source_start)}.`);}
  catch(error){toast(`Não foi possível abrir o bloco anterior: ${error.message}`,true);}
}
function renderVideoOverview(){
  const blocksNode=$("#overviewBlocks"),ruler=$("#overviewRuler"),track=$("#overviewTrack");if(!blocksNode||!ruler||!track)return;
  const chain=overviewChain(),player=$("#videoPlayer"),inputRange=(()=>{try{return getInitialRange();}catch{return{start:0,end:0,duration:0};}})();
  const total=Number(currentSession?.config_json?.video_duration)||Number(overviewVideoDuration)||Number(chain.map((item)=>item.config_json?.video_duration).find((value)=>Number(value)>0))||Number(preview?.video_duration)||Number(player.duration)||0;
  const root=chain[0],rootStart=Number(root?.source_start??inputRange.start)||0,rootDuration=Number(root?.config_json?.duration??root?.requested_duration??inputRange.duration)||0;
  const step=Number([...chain].reverse().map((item)=>item.config_json?.block_duration_requested).find((value)=>Number(value)>0))||Math.max(60,Number($("#nextBlockMinutes")?.value||20)*60);
  const plan=[];let lastEnd=rootStart;
  for(const session of chain){const config=session.config_json||{},start=Number(session.source_start??config.start??0),duration=Number(config.duration??session.requested_duration??0),end=Math.min(total||Infinity,start+duration);if(duration>0&&end>start){plan.push({index:overviewIndex(session),start,end,session});lastEnd=Math.max(lastEnd,end);}}
  if(!plan.length&&rootDuration>0){const end=Math.min(total||Infinity,rootStart+rootDuration);if(end>rootStart){plan.push({index:1,start:rootStart,end,session:null});lastEnd=end;}}
  if(total>0&&plan.length){let cursor=lastEnd,index=Math.max(...plan.map((item)=>item.index))+1;for(let count=0;cursor<total-.01&&count<5000;count++,index++){const end=Math.min(total,cursor+step);plan.push({index,start:cursor,end,session:null});cursor=end;}}
  const actualTotal=total||Math.max(inputRange.end,plan.at(-1)?.end||0);ruler.replaceChildren();blocksNode.replaceChildren();
  for(let i=0;i<=4;i++){const tick=document.createElement("span");tick.textContent=timecode(actualTotal*i/4);ruler.append(tick);}
  let complete=0,activeFraction=0;
  for(const item of plan){const session=item.session,block=document.createElement("button"),fill=document.createElement("span"),state=session?.state||"pending";let fraction=state==="complete"?1:0;
    if(state!=="complete"&&activeState(state)){const progress=session.progress||{},now=Number(progress.current_source_time??session.current_source_time??item.start);fraction=Math.max(0,Math.min(1,(now-item.start)/Math.max(.001,item.end-item.start)));activeFraction+=fraction;}
    if(state==="complete")complete++;
    block.type="button";block.className=`overview-block ${state==="complete"?"complete":activeState(state)?"active":["failed","stopped"].includes(state)?"failed":"pending"}`;block.style.left=`${Math.max(0,item.start/actualTotal*100)}%`;block.style.width=`${Math.max(.12,(item.end-item.start)/actualTotal*100)}%`;fill.className="overview-block-fill";fill.style.width=`${fraction*100}%`;block.append(fill);
    const label=state==="complete"?"Concluído":activeState(state)?(state==="paused"?"Pausado":"Em processamento"):["failed","stopped"].includes(state)?"Interrompido":"Aguardando";block.title=`Bloco ${item.index} · ${timecode(item.start)}–${timecode(item.end)} · ${label}${activeState(state)?` · ${Math.round(fraction*100)}%`:""}`;block.setAttribute("aria-label",block.title);block.disabled=!session; if(session)block.addEventListener("click",()=>openVideoOverviewBlock(session.session_id,item.start));blocksNode.append(block);
  }
  const denominator=plan.length,percent=denominator?Math.min(100,(complete+activeFraction)/denominator*100):0;
  $("#overviewProgressLabel").textContent=denominator?`${percent.toFixed(1)}% · ${complete}/${denominator} blocos concluídos`:"—";
  const current=chain.find((item)=>item.session_id===currentSession?.session_id)||currentSession;
  if(current){const index=overviewIndex(current),item=plan.find((part)=>part.session?.session_id===current.session_id),state=current.state,progress=current.progress||{},fraction=state==="complete"?1:item?Math.max(0,Math.min(1,(Number(progress.current_source_time??current.current_source_time??item.start)-item.start)/Math.max(.001,item.end-item.start))):0;$("#overviewCurrentLabel").textContent=`Bloco ${index} · ${timecode(current.source_start)}–${timecode(Number(current.source_start)+Number(current.config_json?.duration??current.requested_duration??0))}${state==="complete"?" · concluído":activeState(state)?` · ${Math.round(fraction*100)}%`:state==="failed"?" · falhou":""}`;}
  else if(plan.length)$("#overviewCurrentLabel").textContent=`Planejamento · ${timecode(rootStart)}–${timecode(plan[0].end)} · ${denominator} blocos no vídeo`;
  else $("#overviewCurrentLabel").textContent="Abra a prévia para detectar a duração completa do vídeo";
  updatePreviousBlockButton();updateVideoOverviewCursor(actualTotal);
}
async function beginNextBlock({automatic=false}={}){
  if(preparePending||!currentSession?.session_id)return false;
  const minutes=Number($("#nextBlockMinutes").value);if(!Number.isFinite(minutes)||minutes<1||minutes>240){toast("Informe a duração do próximo bloco entre 1 e 240 minutos.",true);return false;}
  const previousSessionId=currentSession.session_id;
  let transitioned=false;
  preparePending=true;updateNextBlockControl();
  try{
    // Recheck persisted children before creating a new block, so a refresh or a
    // repeated click cannot create the same continuation twice.
    const planUpdated=await loadVideoOverviewPlan(currentSession,true);
    const existing=overviewSessions.filter((item)=>item.config_json?.previous_session_id===previousSessionId).sort((a,b)=>String(b.created_at||"").localeCompare(String(a.created_at||"")))[0];
    if(existing){const next=await api(`/api/sessions/${encodeURIComponent(existing.session_id)}`);await attachActiveSession(next);await showSourceTimeInPlayer(Number(next.source_start)||0);toast(`Bloco ${overviewIndex(next)} reaberto.`);transitioned=true;return true;}
    if(!planUpdated)throw new Error("Não foi possível verificar se esse próximo bloco já existe. Verifique a conexão e tente novamente.");
    const result=await api("/api/file/next-block",{method:"POST",body:JSON.stringify({previous_session_id:previousSessionId,duration_seconds:minutes*60})});
    clearInterval(pollTimer);activeSessionId=result.session_id;currentSession=null;$("#sourceStart").value=timecode(result.start);$("#sourceDuration").value=timecode(result.start+result.duration);
    updateInitialRangeUi({source_start:result.start,config_json:{block_index:result.block_index||2,duration:result.duration,previous_session_id:previousSessionId}});
    await refreshSessions();await refreshSession();
    const playerMoved=await showSourceTimeInPlayer(result.start);
    await refreshEvents(true);pollTimer=setInterval(refreshSession,1400);
    const message=result.duration<minutes*60?`Próximo trecho limitado aos ${timecode(result.duration)} restantes no vídeo.`:`Próximo bloco iniciado em ${timecode(result.start)}.`;
    toast(playerMoved?`${message} Player posicionado em ${timecode(result.start)}.`:`${message} O player ainda está carregando o trecho.`);
    transitioned=true;return true;
  }catch(error){if(automatic&&autoNextStartedForSession===previousSessionId){autoNextStartedForSession="";localStorage.removeItem(AUTO_NEXT_PARENT_STORAGE_KEY);}toast(automatic?`O avanço automático falhou: ${error.message}`:error.message,true);return false;}
  finally{preparePending=false;updatePlaybackButton();updateNextBlockControl();if(transitioned)queueMicrotask(()=>void maybeAutoStartNextBlock());}
}
async function maybeAutoStartNextBlock(){
  const session=currentSession,config=session?.config_json||{};
  if(blockAutomationMode!=="automatic"||preparePending||session?.state!=="complete"||!session.session_id)return;
  const videoPath=config.downloaded_path||config.path;if(!videoPath||autoNextStartedForSession===session.session_id)return;
  const total=Number(config.video_duration||$("#videoPlayer").duration),start=Number(session.source_start||0),duration=Number(config.duration??session.requested_duration??0);
  if(!Number.isFinite(total)||total<=0||total-(start+duration)<=.001)return;
  autoNextStartedForSession=session.session_id;localStorage.setItem(AUTO_NEXT_PARENT_STORAGE_KEY,session.session_id);
  await beginNextBlock({automatic:true});
}
async function refreshDesktop(){try{drawImage(await api("/api/desktop-preview"),"desktop");}catch(error){toast(error.message,true);}}
function startLivePreview(){clearInterval(livePreviewTimer);livePreviewTimer=setInterval(async()=>{if(!activeSessionId)return;try{const value=await api(`/api/sessions/${activeSessionId}/preview`);if(value.image)drawImage(value,"live");}catch{/* capture may not have emitted its first frame yet */}},900);}
async function sessionAction(action){if(!activeSessionId)return;if(action==="pause")pausePending=true;if(action==="resume")pausePending=false;try{const result=await api(`/api/youtube/${activeSessionId}/${action}`,{method:"POST"});toast(action==="pause"?"Análise pausada.":action==="resume"&&result.resumed?"Retomando o download parcial preservado.":action==="resume"?"Análise retomada.":"A sessão será encerrada.");if(action==="resume"&&result.resumed)pollTimer=setInterval(refreshSession,1500);await refreshSession();}catch(error){toast(error.message,true);}}
async function pauseFileAnalysis(){if(!activeSessionId)return;try{await api(`/api/file/${activeSessionId}/pause`,{method:"POST"});toast("Pausa solicitada. Aguarde o estado Pausada antes de desligar o PC.");await refreshSession();}catch(error){toast(error.message,true);}}
function openFileResumeDialog(){const session=currentSession;if(!session||session.source_type!=="file"||session.state!=="paused")return;const at=Number(session.config_json?.resume_source_time??session.source_start??0);$("#fileResumeTitle").textContent=`Continuar análise do bloco ${sessionBlockIndex(session)}?`;$("#fileResumeInfo").textContent=`Ponto salvo: ${timecode(at,true)}. Os diálogos dos outros blocos não serão alterados.`;$("#fileResumeDialog").showModal();}
async function chooseFileResume(restart){const dialog=$("#fileResumeDialog"),id=activeSessionId;if(!id||currentSession?.state!=="paused")return;const buttons=[$("#fileResumeContinue"),$("#fileResumeRestart")];buttons.forEach((button)=>button.disabled=true);try{const result=await api(`/api/file/${encodeURIComponent(id)}/${restart?"restart":"resume"}`,{method:"POST"});dialog.close();toast(restart?`Bloco reiniciado; ${result.removed_events??0} diálogo(s) deste bloco removido(s).`:`Análise retomada em ${timecode(Number(result.resume_source_time),true)}.`);await refreshSession();await refreshEvents(true);}catch(error){toast(error.message,true);}finally{buttons.forEach((button)=>button.disabled=false);}}
function updatePipeline(session){
  const state=session?.state||"ready",config=session?.config_json||{},progress=session?.progress||session?.stats||{};
  const stage=progress.stage||(state==="downloading"?"downloading":state==="preparing"?"preparing":state==="running"||state==="processing"?(Number(progress.frames_seen||0)===0?"indexing_timestamps":"analysis"):state);
  const panel=$("#processing"),steps=$$("#pipelineSteps [data-step]");panel.dataset.state=state;panel.dataset.stage=stage;
  let active=-1,done=-1;
  if(state==="downloading"){active=0;}
  else if(state==="preparing"){done=config.downloaded_path?0:-1;active=config.downloaded_path?1:0;}
  else if(state==="running"||state==="processing"){
    done=0;
    if(stage==="loading_ocr"||stage==="indexing_timestamps")active=1;
    else if(stage==="opening_decoder"){done=1;active=2;}
    else if(stage==="analysis"){if(Number(progress.ocr_calls||0)>0){done=2;active=3;}else{done=1;active=2;}}
    else if(stage==="finalizing"){done=3;active=4;}
    else active=1;
  }
  else if(state==="paused"){done=1;}
  else if(state==="complete"){done=5;}
  else if(state==="failed"){active=stage==="analysis"?3:config.downloaded_path||config.path?1:0;}
  const activeLabels={downloading:"Baixando vídeo",preparing:"Preparando",loading_ocr:"Carregando OCR",indexing_timestamps:"Indexando timestamps",opening_decoder:"Abrindo decodificador",analysis:"Executando",finalizing:"Gerando eventos",paused:"Pausado"};
  steps.forEach((el,i)=>{el.classList.remove("done","active","error");const small=el.querySelector("small"),icon=el.querySelector("i");if(state==="failed"&&i===Math.max(0,active)){el.classList.add("error");icon.textContent="!";small.textContent="Erro";}else if(i<=done){el.classList.add("done");icon.textContent="✓";small.textContent="Concluído";}else if(i===active){el.classList.add("active");icon.textContent="◌";small.textContent=activeLabels[stage]||"Executando";}else{icon.textContent="◌";small.textContent=state==="complete"?"Concluído":"Aguardando";}});
  const start=Number(session?.source_start??config.start??0),duration=Number(config.duration??session?.requested_duration??0),time=Number(progress.current_source_time??session?.current_source_time??start);
  const percent=duration>0?Math.max(0,Math.min(99,(time-start)/duration*100)):0;
  const width=state==="complete"?100:state==="downloading"&&Number.isFinite(Number(progress.percent))?Math.min(58,Math.max(4,Number(progress.percent)*.58)):state==="downloading"?14:state==="preparing"?58:state==="running"||state==="processing"?Math.max(1,percent):state==="paused"?Math.max(1,percent):0;
  $("#progressBar").style.width=width+"%";$("#nextSegmentButton").disabled=true;
}
function updateDownloadStatus(session){
  const node=$("#downloadStatus"),config=session?.config_json||{},progress=session?.progress||{},state=session?.state||"ready";
  node.dataset.state=state;
  if(!session){node.textContent="Aguardando inicio do download.";return;}
  if(session.source_type!=="youtube"){
    const normalizedPath=(config.path||"").replace(/\\/g,"/"),fromYoutube=config.source_origin?.provider==="youtube"||Boolean(config.downloaded_path)||normalizedPath.toLowerCase().includes("/videos/");
    node.textContent=fromYoutube?"Vídeo baixado do YouTube · análise visual local, sem áudio.":"Arquivo local selecionado; download do YouTube não se aplica.";
    return;
  }
  const downloaded=Number(progress.downloaded_bytes)||0,total=Number(progress.total_bytes)||0,pct=Number(progress.percent);
  const amount=total?`${formatBytes(downloaded)} / ${formatBytes(total)}`:formatBytes(downloaded);
  if(state==="downloading"){
    if(progress.download_status==="finished"){node.textContent=`Download concluido (${amount}) - validando video e preparando previa...`;return;}
    const speed=Number(progress.speed_bytes_per_second),speedText=speed>0?` \u00b7 ${formatBytes(speed)}/s`:"";
    const eta=progress.eta_seconds!=null?` \u00b7 restante ${formatEta(progress.eta_seconds)}`:"";
    node.textContent=`Download ${Number.isFinite(pct)?`${pct.toFixed(1)}% \u00b7 `:""}${amount}${speedText}${eta} \u00b7 somente video`;
    return;
  }
  if(["failed","stopped"].includes(state)&&config.mode==="youtube_download"&&!config.analysis_started){
    node.textContent=`Download interrompido${downloaded?` em ${amount}`:""}; arquivo parcial preservado. Clique em Retomar download.`;
    return;
  }
  if(config.downloaded_path){
    const size=formatBytes(config.video_size_bytes||downloaded);
    const title=config.video_title||config.video_id||"vídeo",resolution=config.video_width&&config.video_height?`${config.video_width} × ${config.video_height}`:"vídeo local";
    node.textContent=state==="complete"?`Análise concluída · ${timecode(session.source_start)}–${timecode(session.source_start+Number(config.duration||session.requested_duration||0))} · ${size} sem áudio.`:state==="preparing"?`Download concluído · ${title} · ${resolution} · ${size} · pronto para calibrar a ROI.`:`Vídeo baixado${config.reused_source_from_session?" e reutilizado":""} · ${size} sem áudio; pronto para analisar.`;
    return;
  }
  node.textContent=session.detail||"Aguardando inicio do download.";
}
function updateAnalysisActivity(session){
  const progress=session?.progress||session?.stats||{},state=session?.state||"idle";
  let stage=progress.stage||((state==="running"||state==="processing")?(Number(progress.frames_seen||0)===0?"indexing_timestamps":"analysis"):state);
  const labels={idle:"Aguardando",queued:"Na fila",downloading:"Baixando vídeo",preparing:"Preparando vídeo",loading_ocr:"Carregando OCR",indexing_timestamps:"Indexando timestamps",opening_decoder:"Abrindo decodificador",analysis:"Analisando frames",finalizing:"Finalizando eventos",complete:"Concluída",pausing:"Salvando pausa",paused:"Pausada",failed:"Falha",stopped:"Interrompida",stopping:"Encerrando"};
  const stageNode=$("#activityStage"),indexing=stage==="indexing_timestamps",read=Number(progress.frames_seen??session?.frames_captured??0),total=Number(progress.frames_total||0),indexed=Number(progress.frames_indexed||0),indexEstimate=Number(progress.estimated_index_frames||0),sampled=Number(progress.frames_analyzed||0),calls=Number(progress.ocr_calls||0),failures=Number(progress.ocr_failures||0),eventsCreated=Number(progress.events_created||0),duplicates=Number(progress.events_deduplicated||0);
  stageNode.textContent=labels[stage]||sessionStateLabel(session)||"Aguardando";stageNode.dataset.stage=stage;$("#analysisActivity").dataset.stage=stage;
  const frame=progress.current_frame_id;$("#activityFrame").textContent=frame!=null&&frame!==""&&Number.isFinite(Number(frame))?`#${Number(frame)+1}`:indexing&&indexed?`#${indexed.toLocaleString("pt-BR")} (índice)`:"—";
  $("#activityFramesReadLabel").textContent=indexing?"Indexados":"Lidos";
  $("#activityFramesRead").textContent=(indexing?indexed:read).toLocaleString("pt-BR");
  $("#activityFramesTotal").textContent=(indexing?indexEstimate:total)?`${indexing?"~":""}${(indexing?indexEstimate:total).toLocaleString("pt-BR")}`:"—";
  $("#activityFramesAnalyzed").textContent=sampled.toLocaleString("pt-BR");$("#activityOcrCalls").textContent=calls.toLocaleString("pt-BR");$("#activityOcrFailures").textContent=failures.toLocaleString("pt-BR");$("#activityEvents").textContent=eventsCreated.toLocaleString("pt-BR");$("#activityDuplicates").textContent=duplicates.toLocaleString("pt-BR");
  let percent=null,summary=progress.activity_message||"";
  if(indexing){percent=Number(progress.index_progress_percent);summary=indexed?`Indexando timestamps: ${indexed.toLocaleString("pt-BR")} / ${indexEstimate?`~${indexEstimate.toLocaleString("pt-BR")}`:"estimativa indisponível"}${Number.isFinite(percent)?` (${percent.toFixed(1)}%)`:""}.`:summary||"Iniciando ffprobe para indexar os timestamps dos frames.";}
  else if(stage==="analysis"){percent=total?Math.min(100,read/total*100):null;summary=`${read.toLocaleString("pt-BR")}${total?` / ${total.toLocaleString("pt-BR")}`:""} frames lidos · ${sampled.toLocaleString("pt-BR")} amostrados · ${calls.toLocaleString("pt-BR")} consultas OCR${Number.isFinite(Number(progress.current_source_time))?` · fonte ${timecode(progress.current_source_time,true)}`:""}.`;}
  else if(stage==="complete"){percent=100;summary=`Concluída · ${eventsCreated.toLocaleString("pt-BR")} diálogos encontrados · ${sampled.toLocaleString("pt-BR")} frames amostrados.`;}
  else if(stage==="opening_decoder")percent=100;
  else if(stage==="finalizing")percent=100;
  if(!Number.isFinite(percent))percent=null;$("#activityMeterFill").style.width=percent==null?"0%":`${Math.max(0,Math.min(100,percent))}%`;
  $("#activityProgressText").textContent=summary||"Aguardando atualização do processamento.";
  const lastText=String(progress.last_ocr_text||"").trim(),lastRegion=progress.current_region||"ROI",lastTime=Number(progress.last_ocr_source_time),confidence=Number(progress.last_ocr_confidence);
  $("#activityLastOcr").textContent=lastText?`Último OCR · ${lastText} · ${lastRegion}${Number.isFinite(lastTime)?` · ${timecode(lastTime,true)}`:""}${Number.isFinite(confidence)?` · ${Math.round(confidence*100)}%`:""}`:indexing?"OCR ainda não começou; o DubSync está indexando os timestamps do vídeo.":"Último OCR: aguardando uma consulta com texto.";
  const history=$("#activityHistory");let entries=Array.isArray(progress.recent_activity)?progress.recent_activity.slice(-5).reverse():[];
  if(!entries.length&&session){
    const sessionKey=String(session.session_id||"active-session"),signature=[stage,read,indexed,sampled,calls,failures,eventsCreated,duplicates,Math.round(Number(progress.current_source_time||0)*10)].join(":");
    if(sessionKey!==observedActivitySession){observedActivitySession=sessionKey;observedActivitySignature="";observedActivityHistory=[];}
    if(signature!==observedActivitySignature){
      observedActivitySignature=signature;
      const time=Number(progress.current_source_time),message=progress.activity_message||(indexing?`Indexação ativa · ${indexed.toLocaleString("pt-BR")} quadros consultados${indexEstimate?` de ~${indexEstimate.toLocaleString("pt-BR")}`:""}.`:stage==="analysis"?`${progress.current_frame_id!=null?`Frame ${Number(progress.current_frame_id)+1} · `:""}${read.toLocaleString("pt-BR")} lidos · ${sampled.toLocaleString("pt-BR")} amostrados · ${calls.toLocaleString("pt-BR")} OCR · ${eventsCreated.toLocaleString("pt-BR")} diálogos${Number.isFinite(time)?` · fonte ${timecode(time,true)}`:""}.`:labels[stage]||"Sessão aguardando atividade.");
      observedActivityHistory.unshift({at:new Date().toISOString(),message});observedActivityHistory=observedActivityHistory.slice(0,10);
    }
    entries=observedActivityHistory.slice(0,5);
  }
  history.replaceChildren();
  if(!entries.length){const item=document.createElement("li");item.textContent=progress.activity_message||(indexing?"Indexando timestamps antes da leitura do primeiro frame.":session?.detail||"A atividade recente aparecerá aqui.");history.append(item);return;}
  for(const entry of entries){const item=document.createElement("li"),time=entry.at?new Date(entry.at).toLocaleTimeString("pt-BR",{hour:"2-digit",minute:"2-digit",second:"2-digit"}):"";item.textContent=`${time?time+" · ":""}${entry.message||"Atividade do processamento"}`;history.append(item);}
}
async function refreshSession(){
  if(!activeSessionId)return;
  try{
    const previousState=currentSession?.state;
    const session=await api("/api/sessions/"+activeSessionId);
    currentSession=session;updateInitialRangeUi(session);updatePreviousBlockButton();void loadVideoOverviewPlan(session);renderVideoOverview();
    const state=session.state,config=session.config_json||{},progress=session.progress||session.stats||{},label=sessionStateLabel(session);
    $("#sessionState").textContent=label;$("#sessionState").className="state-dot "+state;$("#statusStateText").textContent=label;$("#fineSweepStartButton").classList.toggle("hidden",state!=="complete");
    const indexing=state==="running"&&Number(progress.frames_seen||0)===0;
    $("#sessionDetail").textContent=progress.activity_message|| (indexing?"Indexando timestamps do trecho para localizar cada quadro.":state==="preparing"&&session.ready_for_calibration?"Download concluído. Ajuste a ROI sobre o vídeo e clique em Iniciar análise; ainda não há eventos desta faixa.":session.detail||"");
    $("#sessionError").textContent=session.error||"";
    const duration=Number(config.duration??session.requested_duration??0),offset=Number(session.source_start??config.start??0),end=offset+duration;
    const reportedTime=Number(progress.current_source_time??session.current_source_time??offset),current=Math.max(offset,Math.min(end,Number.isFinite(reportedTime)?reportedTime:offset));
    $("#statusSourceTime").textContent=timecode(current)+" / "+timecode(end);
    $("#statusSegment").textContent=config.block_index?`Bloco ${config.block_index}`:config.downloaded_path?"Vídeo local":session.current_segment?String(session.current_segment):"—";
    $("#framesCaptured").textContent=Number(progress.frames_seen??session.frames_captured??0).toLocaleString("pt-BR");
    $("#framesAnalyzed").textContent=Number(progress.frames_analyzed??0).toLocaleString("pt-BR");
    $("#effectiveFps").textContent=(session.effective_fps||progress.effective_fps)?Number(session.effective_fps||progress.effective_fps).toFixed(1):"—";
    $("#timingQuality").textContent=session.timing_quality?session.timing_quality+(session.timing_error_ms?" (±"+Math.round(session.timing_error_ms)+" ms)":""):"—";
    $("#currentFile").textContent=config.downloaded_path?(session.current_file||config.video_title||"vídeo baixado"):(session.current_file||"—");
    const downloadPct=session.progress?.percent;
    $("#segmentDurationLabel").textContent=config.downloaded_path?(config.video_width+" × "+config.video_height+" · sem áudio"):state==="downloading"&&session.progress?.download_status==="finished"?"Validando vídeo…":state==="downloading"&&Number.isFinite(downloadPct)?"Baixando "+downloadPct+"%":"Melhor qualidade";
    updateDownloadStatus(session);
    const live=["downloading","running","processing","paused"].includes(state),youtube=session.source_type==="youtube";
    $("#pauseButton").disabled=!(youtube&&state==="processing")||state==="paused";
    $("#stopButton").disabled=!(youtube&&["downloading","preparing","processing","paused"].includes(state));
    $("#previewToggle").disabled=$("#videoPlayer").classList.contains("hidden");updatePreviewToggle();updatePlaybackButton();
    $("#captureStatusBadge").textContent=state==="downloading"?"BAIXANDO VÍDEO":(state==="processing"||state==="running")?"ANÁLISE DO ARQUIVO LOCAL":state==="pausing"?"SALVANDO PAUSA":state==="paused"?"ANÁLISE PAUSADA":state==="preparing"&&config.downloaded_path?"VÍDEO LOCAL · PRONTO PARA CALIBRAR":state.toUpperCase();
    $("#previewOverlayState").classList.toggle("hidden",!live);updatePipeline(session);updateAnalysisActivity(session);updateNextBlockControl();if(["running","processing"].includes(state)&&Date.now()-lastEventRefreshAt>1800){lastEventRefreshAt=Date.now();void refreshEvents(true);}if(state==="complete"&&previousState!=="complete"){void refreshEvents(true);showBlockCompleteChoice(session);}if(state==="complete")void maybeAutoStartNextBlock();
    const coverageSource=youtube?(config.url||session.source_uri):"";
    if(coverageSource){const force=state==="complete"&&coverageCompleteSession!==session.session_id;if(force)coverageCompleteSession=session.session_id;void loadCoverage(coverageSource,force);}else $("#coveragePanel").classList.add("hidden");
    if(config.downloaded_path){await activateDownloadedFile(session);loadSessionPlayer(session);}else if(config.path){loadSessionPlayer(session);if(mode==="file")$("#filePath").value=config.path;}
    if(!preview&&(config.downloaded_path||config.path)&&!["downloading","queued"].includes(state))drawVideoPreview();
    if(["complete","failed","stopped"].includes(state)){
      clearInterval(pollTimer);clearInterval(livePreviewTimer);pausePending=false;
      if(state==="complete"&&previousState!=="complete")$("#videoPlayer").pause();
      $("#previewOverlayState").classList.add("hidden");
    }
  }catch(error){$("#sessionError").textContent=error.message;}
}
function assetUrl(session,path){return path?`/assets/${encodeURIComponent(session)}/${path.split("/").map(encodeURIComponent).join("/")}?project_id=${encodeURIComponent(window.DUBSYNC_PROJECT_ID||"")}`:"";}
function statusText(event){return event.review_status==="accepted"?"✓ Concluído na Captura":event.review_status==="corrected"?"✎ Corrigido · pronto para revisar":event.review_status==="discarded"?"○ Descartado":event.confidence<.68?"◷ Baixa confiança · revisar":"○ Pendente";}
function fineSweepEventIds(){return new Set((fineSweepRun?.findings_json||[]).map((item)=>item.event_id).filter(Boolean));}
function renderFineSweepStatus(){
  const panel=$("#fineSweepStatus"),run=fineSweepRun;panel.classList.toggle("hidden",!run);
  $("#fineSweepStartButton").disabled=run?.state==="running";
  if(!run)return;
  const findings=run.findings_json||[];$("#fineSweepHeadline").textContent=run.state==="running"?"◉ Pente fino em andamento":run.state==="complete"?`✓ Pente fino concluído · ${findings.length} indício(s)`:"Pente fino interrompido";
  $("#fineSweepProgress").textContent=run.state==="running"?`${Number(run.progress_percent||0).toFixed(1)}% · ${Number(run.checked_frames||0).toLocaleString("pt-BR")} frames · ${Number(run.ocr_calls||0).toLocaleString("pt-BR")} leituras OCR · ${timecode(run.current_source_time)}`:run.error||"Compare os pontos amarelos; nenhuma correção foi alterada.";
  const list=$("#fineSweepFindings");list.replaceChildren();for(const item of findings.slice(0,60)){const button=document.createElement("button");button.type="button";button.textContent=`${timecode(item.source_time)} · ${item.kind==="missing_dialogue"?"Possível diálogo ausente":`Evento · ${item.reason}`} · ${item.observed_text||""}`;button.title=item.reason||"Indício do pente fino";button.addEventListener("click",()=>{if(item.event_id)selectEvent(item.event_id,false);seekVideoTo(item.source_time);});list.append(button);}
}
async function refreshFineSweep(sessionId=activeSessionId){if(!sessionId)return;try{const result=await api(`/api/sessions/${encodeURIComponent(sessionId)}/fine-sweep`);if(sessionId!==activeSessionId)return;const prior=fineSweepRun?.state;fineSweepSessionId=sessionId;fineSweepRun=result.run;renderFineSweepStatus();renderTimeline();renderRows();if(fineSweepRun?.state==="running"&&!fineSweepPollTimer)fineSweepPollTimer=setInterval(()=>refreshFineSweep(sessionId),2000);if(fineSweepRun?.state!=="running"&&fineSweepPollTimer){clearInterval(fineSweepPollTimer);fineSweepPollTimer=0;}if(prior==="running"&&fineSweepRun?.state==="complete"){toast(`Pente fino concluído: ${(fineSweepRun.findings_json||[]).length} indício(s) em amarelo.`);if($("#statusFilter").value==="fine-sweep")void refreshEvents(true);}}catch(error){console.warn("Pente fino indisponível:",error);}}
async function startFineSweep(){const session=currentSession;if(!session||session.state!=="complete")return toast("Conclua este bloco antes do pente fino.",true);try{const result=await api(`/api/sessions/${encodeURIComponent(session.session_id)}/fine-sweep`,{method:"POST",body:"{}"});if($("#blockCompleteDialog").open)$("#blockCompleteDialog").close();fineSweepRun=result.run;fineSweepSessionId=session.session_id;renderFineSweepStatus();toast("Pente fino iniciado. O progresso aparecerá em Segmento e Processamento.");if(!fineSweepPollTimer)fineSweepPollTimer=setInterval(()=>refreshFineSweep(session.session_id),2000);}catch(error){toast(error.message,true);}}
function showBlockCompleteChoice(session){if(blockAutomationMode==="automatic"){toast(`Bloco ${overviewIndex(session)} concluído: 100%. ${$("#nextSegmentButton").disabled?"Fim do vídeo.":"Modo automático seguirá para o próximo."}`);return;}const key=`dubsync.blockCompleteNotice.${session.session_id}`;if(localStorage.getItem(key))return;localStorage.setItem(key,"1");const start=Number(session.source_start),end=start+Number(session.config_json?.duration??session.requested_duration);$("#blockCompleteTitle").textContent=`Bloco ${overviewIndex(session)} concluído: 100%`;$("#blockCompleteRange").textContent=`${timecode(start)} → ${timecode(end)}. Os eventos estão salvos e prontos para revisão.`;$("#blockCompleteNext").disabled=$("#nextSegmentButton").disabled;$("#blockCompleteNext").title=$("#nextSegmentButton").disabled?"Este bloco termina no fim do vídeo.":"Abre a faixa seguinte deste vídeo.";if(!$("#blockCompleteDialog").open)$("#blockCompleteDialog").showModal();}
function selectEvent(id,seek=true,preserveTimelineNodes=false,preserveTimelineView=false){closeEventCharacterOptions();selectedEventId=id;const event=events.find((item)=>item.event_id===id);if(!event)return;if(!preserveTimelineView&&event.session_id===timelineIdentity()&&(event.start_time<timelineRange.start||event.start_time>timelineRange.end))panTimelineTo(event.start_time,.2);renderRows();if(preserveTimelineNodes){$$(".timeline-event,.timeline-dialogue-point").forEach((node)=>node.classList.toggle("selected",node.dataset.eventId===id));}else renderTimeline();renderDetails(event);const manualEvent=["manual","duplicate"].includes(event.segment_id);$("#reviewOriginalLabel").textContent=event.segment_id==="duplicate"?"Texto duplicado (sem novo OCR)":manualEvent?"Texto manual (sem OCR)":"Texto Original (OCR)";$("#reviewConfidenceCaption").textContent=manualEvent?"Origem":"Confiança OCR";$("#reviewConfidence").textContent=manualEvent?"Texto copiado/manual":`${Math.round((event.confidence||0)*100)}%`;$("#reviewOriginal").value=event.original_text||"";$("#reviewCorrected").value=event.corrected_text??event.processed_text??event.original_text??"";$("#reviewCharacter").value=event.character||"";updateReviewCharacterPhoto();void loadEventCharacterOptions();$("#reviewEmotion").value=event.emotion||"";$("#reviewStart").value=Number(event.start_time).toFixed(3);$("#reviewEnd").value=event.end_time==null?"":Number(event.end_time).toFixed(3);$("#reviewConfidence").textContent=manualEvent?"Texto copiado/manual":`${Math.round((event.confidence||0)*100)}%`;$("#reviewSelectionHint").textContent=`Evento #${event.sequence} · ${timecode(event.start_time)} — ${timecode(event.end_time)}`;["#acceptButton","#correctButton","#adjustButton","#mergePrevious","#mergeNext","#splitButton","#discardButton","#duplicateDialogue","#retryDialogueOcr"].forEach((selector)=>$(selector).disabled=false);const eventIndex=events.findIndex((row)=>row.event_id===id);$("#mergePrevious").disabled=eventIndex<=0||events[eventIndex-1]?.session_id!==event.session_id;$("#mergeNext").disabled=eventIndex>=events.length-1||events[eventIndex+1]?.session_id!==event.session_id;$("#splitButton").disabled=event.end_time==null||event.end_time<=event.start_time;if(seek&&event.session_id===activeSessionId)seekVideoTo(event.start_time);}
function renderRows(){
  const body=$("#eventRows"),flagged=fineSweepEventIds(),auditActive=fineSweepRun?.state==="complete";
  $("#eventList").classList.toggle("audit-active",auditActive);body.replaceChildren();
  if(!events.length){const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=EVENT_TABLE_COLUMNS.length;cell.className="table-empty";cell.textContent="Nenhum diálogo encontrado com estes filtros.";row.append(cell);body.append(row);updateMergeToolbar();return;}
  for(const event of events){
    const row=document.createElement("tr"),divergent=flagged.has(event.event_id)&&event.session_id===fineSweepSessionId;
    row.className=`event-row${event.event_id===selectedEventId?" selected":""}${divergent?" fine-sweep-divergence":""}`;row.dataset.id=event.event_id;row.addEventListener("click",()=>{setCaptureTimelinePinSelection([event.event_id],event.event_id);selectEvent(event.event_id);});
    const select=document.createElement("td"),check=document.createElement("input");select.dataset.column="select";check.type="checkbox";check.className="merge-check";check.dataset.id=event.event_id;check.addEventListener("click",(e)=>e.stopPropagation());check.addEventListener("change",updateMergeToolbar);select.append(check);
    const number=document.createElement("td"),occurrence=document.createElement("td"),start=document.createElement("td"),end=document.createElement("td"),duration=document.createElement("td"),text=document.createElement("td"),character=document.createElement("td"),emotion=document.createElement("td"),confidence=document.createElement("td"),status=document.createElement("td");
    number.dataset.column="number";number.textContent=String(event.sequence).padStart(2,"0");
    occurrence.dataset.column="occurrence";const occurrenceIds=[...new Set((Array.isArray(event.occurrence_ids)?event.occurrence_ids:[]).map((id)=>String(id||"").trim()).filter(Boolean))];
    if(occurrenceIds.length){const displayId=(id)=>id.length>10?id.slice(-8):id;occurrence.textContent=displayId(occurrenceIds[0])+(occurrenceIds.length>1?` +${occurrenceIds.length-1}`:"");occurrence.title=occurrenceIds.join("\n");occurrence.className="event-occurrence-id";}
    else{occurrence.textContent="—";occurrence.title=`A ocorrência #${event.sequence} ainda não está vinculada à tela de Dublagem.`;}
    start.dataset.column="start";end.dataset.column="end";duration.dataset.column="duration";text.dataset.column="text";character.dataset.column="character";emotion.dataset.column="emotion";confidence.dataset.column="confidence";status.dataset.column="status";
    start.textContent=timecode(event.start_time,true);end.textContent=timecode(event.end_time,true);duration.textContent=event.end_time==null?"—":`${(event.end_time-event.start_time).toFixed(2)}s`;
    text.textContent=event.corrected_text||event.processed_text||event.original_text;text.title=event.original_text;
    const speakerColor=characterColorForEvent(event),speakerName=event.character||"—";character.title=event.character||"Sem personagem";
    if(speakerColor){row.dataset.characterColor=speakerColor;character.style.color=speakerColor;const dot=document.createElement("i");dot.className="event-character-color-dot";dot.style.backgroundColor=speakerColor;dot.setAttribute("aria-hidden","true");character.append(dot,document.createTextNode(speakerName));}
    else character.textContent=speakerName;
    emotion.textContent=event.emotion||"—";emotion.title=event.emotion||"Sem emoção";
    confidence.textContent=event.segment_id==="manual"?"Manual":(event.confidence||0).toFixed(2);
    const label=document.createElement("span");label.className=`status-label ${divergent?"fine-sweep-divergence":event.review_status==="pending"&&event.confidence<.68?"low-confidence":event.review_status}`;label.textContent=divergent?`${statusText(event)} · Divergência`:statusText(event);status.append(label);
    const cells={select,number,occurrence,start,end,duration,text,character,emotion,confidence,status};for(const key of eventColumnOrder)row.append(cells[key]);body.append(row);
  }
  applyEventTableLayout();
  updateMergeToolbar();
}
function seekVideoTo(sourceTime){const player=$("#videoPlayer");if(player.classList.contains("hidden")||!Number.isFinite(player.duration))return;const bounds=videoSourceBounds();player.currentTime=Math.max(bounds.start,Math.min(bounds.end,Number(sourceTime)||bounds.start));lastTimelinePlaybackTime=player.currentTime;updatePlayerDisplay();}
function normalizeCharacterColorName(value){return String(value||"").normalize("NFKC").toLocaleLowerCase("pt-BR").replace(/[^\p{L}\p{N}_\s]/gu," ").trim().replace(/\s+/g," ");}
function characterColorForEvent(item){
  if(!item)return "";
  const byId=roiCharacterColorById.get(String(item.character_profile_id||""));if(byId)return byId;
  const name=normalizeCharacterColorName(item.character||"");if(!name)return "";
  const roi=String(item.region_id||item.roi_id||"");
  return (roi&&roiCharacterColorByRoiName.get(`${roi}|${name}`))||roiCharacterColorByName.get(name)||"";
}
async function refreshRoiCharacterColors(force=false){
  if(!window.DUBSYNC_PROJECT_ID)return [];
  if(!force&&Date.now()-roiCharacterColorsLoadedAt<30000)return [...roiCharacterColorById.entries()].map(([character_id,color])=>({character_id,color}));
  if(roiCharacterColorsLoadPromise)return roiCharacterColorsLoadPromise;
  const request=(async()=>{
    try{
      const result=await api("/api/roi-characters/colors"),items=result.characters||[];
      const byId=new Map(),byRoiName=new Map(),byName=new Map();
      for(const item of items){
        const color=/^#[0-9a-f]{6}$/i.test(String(item.color||""))?item.color.toUpperCase():"";
        if(!color)continue;
        byId.set(String(item.character_id),color);
        const names=[item.alias,item.name].map(normalizeCharacterColorName).filter(Boolean);
        for(const name of new Set(names)){
          if(item.roi_id)byRoiName.set(`${item.roi_id}|${name}`,color);
          if(!byName.has(name))byName.set(name,color);else if(byName.get(name)!==color)byName.set(name,"");
        }
      }
      roiCharacterColorById=byId;roiCharacterColorByRoiName=byRoiName;roiCharacterColorByName=byName;roiCharacterColorsLoadedAt=Date.now();
      window.dispatchEvent(new CustomEvent("dubsync:character-colors-updated",{detail:{characters:items}}));
      if(events.length){renderRows();renderTimeline();}
      return items;
    }catch(error){console.warn("Não foi possível carregar as cores dos personagens:",error);return [];}
    finally{roiCharacterColorsLoadPromise=null;}
  })();
  roiCharacterColorsLoadPromise=request;return request;
}
window.refreshRoiCharacterColors=refreshRoiCharacterColors;
function renderTimeline(){
  const track=$("#timelineTrack"),ticks=$("#timelineTicks");track.querySelectorAll(".timeline-event,.timeline-dialogue-point,.timeline-audio-marker,.timeline-missing-point").forEach((node)=>node.remove());ticks.replaceChildren();
  const selected=events.find((event)=>event.event_id===selectedEventId),sessionId=$("#sessionFilter").value||selected?.session_id||activeSessionId,session=sessions.find((item)=>item.session_id===sessionId),config=session?.config_json||{};
  const auditActive=fineSweepSessionId===sessionId&&fineSweepRun?.state==="complete";$(".timeline-panel").classList.toggle("audit-active",auditActive);$("#fineSweepLegend").classList.toggle("hidden",!auditActive);
  const bounds=getTimelineBounds(sessionId),saved=timelineViews[sessionId]||{start:bounds.start,span:bounds.span},view=clampTimelineView(Number(saved.start)||bounds.start,Number(saved.span)||bounds.span,bounds);
  const start=view.start,end=view.end,span=view.span;timelineRange={start,span,end};
  renderTimelineNavigator(bounds,view);
  $("#timelineZoomIn").disabled=span<=.251;$("#timelineZoomOut").disabled=span>=bounds.span-.001;$("#timelineZoomFit").disabled=span>=bounds.span-.001;
  for(let i=0;i<6;i++){const mark=document.createElement("span");mark.textContent=timecode(start+span*i/5);ticks.append(mark);}
  const flagged=fineSweepSessionId===sessionId?fineSweepEventIds():new Set();
  for(const item of events){if((item.end_time??item.start_time)<start||item.start_time>end)continue;const block=document.createElement("button");block.type="button";block.className=`timeline-event ${item.confidence<.68&&item.review_status==="pending"?"low-confidence":item.review_status}${flagged.has(item.event_id)?" fine-sweep-divergence":""}${item.event_id===selectedEventId?" selected":""}`;const left=Math.max(0,(item.start_time-start)/span*100),right=Math.min(100,Math.max(left+.5,((item.end_time??item.start_time+.1)-start)/span*100));block.style.left=`${left}%`;block.style.width=`${Math.max(.8,right-left)}%`;block.title=`#${item.sequence} · ${item.corrected_text||item.processed_text||item.original_text} · ${timecode(item.start_time)}–${timecode(item.end_time)}\nArraste as bordas para ajustar os tempos.`;
    if(item.end_time!=null){const leftHandle=document.createElement("i"),rightHandle=document.createElement("i");leftHandle.className="timeline-handle start";rightHandle.className="timeline-handle end";block.append(leftHandle,rightHandle);}
    block.addEventListener("pointerdown",(event)=>{if(event.button!==0||item.end_time==null)return;const rect=block.getBoundingClientRect(),edge=event.target.closest(".timeline-handle")?.classList.contains("start")?"start":event.target.closest(".timeline-handle")?.classList.contains("end")?"end":event.clientX-rect.left<7?"start":rect.right-event.clientX<7?"end":"move";timelineEdit={event:item,node:block,edge,startX:event.clientX,originalStart:Number(item.start_time),originalEnd:Number(item.end_time),nextStart:Number(item.start_time),nextEnd:Number(item.end_time),width:track.getBoundingClientRect().width,moved:false};block.setPointerCapture(event.pointerId);});
    block.addEventListener("pointermove",(event)=>{if(!timelineEdit||timelineEdit.node!==block)return;const delta=(event.clientX-timelineEdit.startX)/Math.max(1,timelineEdit.width)*timelineRange.span;if(Math.abs(delta)<.025)return;timelineEdit.moved=true;const min=timelineRange.start,max=timelineRange.end,duration=timelineEdit.originalEnd-timelineEdit.originalStart;if(timelineEdit.edge==="start")timelineEdit.nextStart=Math.max(min,Math.min(timelineEdit.originalEnd-.05,timelineEdit.originalStart+delta));else if(timelineEdit.edge==="end")timelineEdit.nextEnd=Math.min(max,Math.max(timelineEdit.originalStart+.05,timelineEdit.originalEnd+delta));else{const shift=Math.max(min-timelineEdit.originalStart,Math.min(max-timelineEdit.originalEnd,delta));timelineEdit.nextStart=timelineEdit.originalStart+shift;timelineEdit.nextEnd=timelineEdit.originalEnd+shift;}const l=(timelineEdit.nextStart-timelineRange.start)/timelineRange.span*100,r=(timelineEdit.nextEnd-timelineRange.start)/timelineRange.span*100;block.style.left=`${Math.max(0,l)}%`;block.style.width=`${Math.max(.8,Math.min(100,r)-Math.max(0,l))}%`;});
    block.addEventListener("pointerup",async(event)=>{if(!timelineEdit||timelineEdit.node!==block)return;const edit=timelineEdit;timelineEdit=null;if(!edit.moved)return;event.preventDefault();event.stopPropagation();ignoreTimelineClick=true;setTimeout(()=>ignoreTimelineClick=false,0);try{await api(`/api/events/${item.event_id}/review/update`,{method:"POST",body:JSON.stringify({start_time:Number(edit.nextStart.toFixed(3)),end_time:Number(edit.nextEnd.toFixed(3))})});item.start_time=edit.nextStart;item.end_time=edit.nextEnd;selectedEventId=item.event_id;await refreshEvents(true);toast("Tempos ajustados na timeline.");}catch(error){toast(error.message,true);renderTimeline();}});
    block.dataset.eventId=item.event_id;block.addEventListener("pointercancel",()=>{timelineEdit=null;renderTimeline();});block.addEventListener("click",(event)=>{event.stopPropagation();if(ignoreTimelineClick)return;selectEvent(item.event_id,false,true);});block.addEventListener("dblclick",(event)=>{event.stopPropagation();seekVideoTo(item.start_time);});track.append(block);const point=document.createElement("button");point.type="button";point.className=`timeline-dialogue-point ${item.confidence<.68&&item.review_status==="pending"?"low-confidence":item.review_status}${flagged.has(item.event_id)?" fine-sweep-divergence":""}${item.event_id===selectedEventId?" selected":""}`;point.dataset.eventId=item.event_id;point.innerHTML=captureTimelinePinSvg();point.style.left=`${left}%`;const speakerColor=characterColorForEvent(item);if(speakerColor){point.dataset.characterColor=speakerColor;point.style.setProperty("--character-color",speakerColor);}const dialogueText=(item.corrected_text||item.processed_text||item.original_text||"Texto não reconhecido").trim();point.title=`Diálogo #${item.sequence} · ${timecode(item.start_time,true)}\n${dialogueText}\nClique para selecionar; duplo clique para ir ao início.`;point.dataset.tooltip=point.title;block.dataset.tooltip=`Diálogo #${item.sequence} · ${timecode(item.start_time,true)}–${timecode(item.end_time,true)}\n${dialogueText}\nClique para selecionar; duplo clique para ir ao início.`;point.setAttribute("aria-label",`Diálogo #${item.sequence}: ${dialogueText}, em ${timecode(item.start_time)}`);point.addEventListener("click",(event)=>{event.stopPropagation();selectEvent(item.event_id,false,true);});point.addEventListener("dblclick",(event)=>{event.stopPropagation();seekVideoTo(item.start_time);});track.append(point);}
  if(fineSweepSessionId===sessionId)for(const finding of fineSweepRun?.findings_json||[]){if(finding.kind!=="missing_dialogue"||finding.source_time<start||finding.source_time>end)continue;const marker=document.createElement("button");marker.type="button";marker.className="timeline-missing-point";marker.style.left=`${(finding.source_time-start)/span*100}%`;marker.textContent="!";marker.title=`Possível diálogo ausente · ${timecode(finding.source_time,true)}\n${finding.observed_text}`;marker.addEventListener("click",(event)=>{event.stopPropagation();seekVideoTo(finding.source_time);toast("Possível diálogo ausente. Use + Diálogo para cadastrá-lo após conferir o vídeo.");});track.append(marker);}
  for(const marker of timelineMarkers){if(marker.source_time<start||marker.source_time>end)continue;const pin=document.createElement("button");pin.type="button";pin.className="timeline-audio-marker";pin.style.left=`${(marker.source_time-start)/span*100}%`;pin.title=`${marker.label} · ${timecode(marker.source_time,true)} · clique para buscar; duplo clique para excluir`;pin.setAttribute("aria-label",`${marker.label} em ${timecode(marker.source_time,true)}`);pin.addEventListener("click",(event)=>{event.stopPropagation();if(marker.session_id===activeSessionId)seekVideoTo(marker.source_time);else{const session=sessions.find((item)=>item.session_id===marker.session_id);if(session)attachActiveSession(session).then(()=>seekVideoTo(marker.source_time));}});pin.addEventListener("dblclick",(event)=>{event.stopPropagation();deleteAudioStartMarker(marker);});track.append(pin);}
  const cursor=$("#timelineCursor"),time=Number.isFinite($("#videoPlayer").currentTime)?$("#videoPlayer").currentTime:selected?.start_time??bounds.start;cursor.style.left=`${Math.max(0,Math.min(100,(time-start)/span*100))}%`;applyCaptureTimelinePinSelection();
}
function shortcutLabel(binding){const parts=[];if(binding.ctrl)parts.push("Ctrl");if(binding.alt)parts.push("Alt");if(binding.shift)parts.push("Shift");if(binding.meta)parts.push("Win");const names={Space:"Espaço",ArrowLeft:"←",ArrowRight:"→",ArrowUp:"↑",ArrowDown:"↓",Equal:"=",Minus:"−",BracketLeft:"[",BracketRight:"]"};const key=names[binding.code]||(binding.code.startsWith("Key")?binding.code.slice(3):binding.code.startsWith("Digit")?binding.code.slice(5):binding.code);return [...parts,key].join("+");}
function sameShortcut(a,b){return a&&b&&a.code===b.code&&Boolean(a.ctrl)===Boolean(b.ctrl)&&Boolean(a.alt)===Boolean(b.alt)&&Boolean(a.shift)===Boolean(b.shift)&&Boolean(a.meta)===Boolean(b.meta);}
function saveShortcutPreferences(){try{localStorage.setItem(SHORTCUTS_STORAGE_KEY,JSON.stringify(shortcutBindings));localStorage.setItem(SHORTCUT_STEPS_STORAGE_KEY,JSON.stringify(shortcutSteps));}catch{toast("Não foi possível salvar os atalhos neste navegador.",true);}}
function renderShortcutSettings(){const list=$("#shortcutList");if(!list)return;list.replaceChildren();$("#shortcutStepMain").value=shortcutSteps.main;$("#shortcutStepFine").value=shortcutSteps.fine;$("#shortcutStepFast").value=shortcutSteps.fast;for(const [id,label,description] of SHORTCUT_ACTIONS){const row=document.createElement("div");row.className="shortcut-row";const copy=document.createElement("div");copy.className="shortcut-copy";const name=document.createElement("b"),hint=document.createElement("small");name.textContent=label;hint.textContent=description;copy.append(name,hint);const button=document.createElement("button");button.type="button";button.className="shortcut-record button quiet";button.textContent=recordingShortcutAction===id?"Pressione uma combinação…":shortcutLabel(shortcutBindings[id]);button.title=`Clique e pressione a nova combinação para ${label.toLowerCase()}.`;button.setAttribute("aria-label",`${label}: ${shortcutLabel(shortcutBindings[id])}. Clique para alterar.`);button.addEventListener("click",()=>{recordingShortcutAction=id;renderShortcutSettings();$("#shortcutList").querySelector(`[data-action="${id}"]`)?.focus();});button.dataset.action=id;row.append(copy,button);list.append(row);}}
function setShortcutStep(name,value){const number=Number(value),limits={main:[.01,3600],fine:[.01,60],fast:[.1,3600]},[min,max]=limits[name];if(!Number.isFinite(number))return;shortcutSteps[name]=Math.max(min,Math.min(max,number));saveShortcutPreferences();}
function recordShortcut(event){if(!recordingShortcutAction)return false;if(event.code==="Escape"){event.preventDefault();recordingShortcutAction="";renderShortcutSettings();return true;}if(["ControlLeft","ControlRight","AltLeft","AltRight","ShiftLeft","ShiftRight","MetaLeft","MetaRight"].includes(event.code))return true;const binding={code:event.code,ctrl:event.ctrlKey,alt:event.altKey,shift:event.shiftKey,meta:event.metaKey};const conflict=Object.entries(shortcutBindings).find(([id,value])=>id!==recordingShortcutAction&&sameShortcut(value,binding));event.preventDefault();if(conflict){const label=SHORTCUT_ACTIONS.find(([id])=>id===conflict[0])?.[1]||"outro comando";recordingShortcutAction="";renderShortcutSettings();toast(`Essa combinação já está atribuída a “${label}”.`,true);return true;}shortcutBindings[recordingShortcutAction]=binding;recordingShortcutAction="";saveShortcutPreferences();renderShortcutSettings();toast("Atalho salvo neste navegador.");return true;}
function editableKeyboardTarget(target){if(!(target instanceof Element))return false;const field=target.closest("input,textarea,select,[contenteditable='true'],[role='textbox']");if(!field)return false;if(field instanceof HTMLInputElement&&["checkbox","radio","button","submit","reset"].includes(field.type))return false;return true;}
function videoClock(){const player=$("#videoPlayer");return !player.classList.contains("hidden")&&Number.isFinite(player.currentTime)?player.currentTime:Number(currentSession?.source_start)||0;}
function moveBySeconds(seconds){const player=$("#videoPlayer");if(player.classList.contains("hidden"))return;seekVideoTo(videoClock()+seconds);}
function navigateDialogue(direction){const list=captureTimelinePinItems().filter((item)=>item.review_status!=="discarded");if(!list.length)return;const now=videoClock();const next=direction<0?[...list].reverse().find((item)=>item.start_time<now-.04)||list[0]:list.find((item)=>item.start_time>now+.04)||list[list.length-1];setCaptureTimelinePinSelection([next.event_id],next.event_id);selectEvent(next.event_id);}
function openManualEventDialog(sessionId=activeSessionId,atTime=videoClock()){const session=sessions.find((item)=>item.session_id===sessionId)||(currentSession?.session_id===sessionId?currentSession:null);if(!session)return toast("Abra uma sessão para adicionar um diálogo.",true);const low=Number(session.source_start??session.config_json?.start)||0,high=low+Math.max(.1,Number(session.requested_duration??session.config_json?.duration)||0),start=Math.max(low,Math.min(high-.01,Number(atTime)||low));$("#manualEventText").value="";$("#manualEventStart").value=start.toFixed(3);$("#manualEventEnd").value=Math.min(high,start+2).toFixed(3);$("#manualEventStart").min=String(low);$("#manualEventStart").max=String(high-.001);$("#manualEventEnd").min=String(low+.001);$("#manualEventEnd").max=String(high);$("#manualEventCharacter").value="";$("#manualEventDialog").dataset.sessionId=session.session_id;$("#manualEventDialog").showModal();$("#manualEventText").focus();}
window.DubSyncCaptureOpenManualDialogue=openManualEventDialog;
async function saveManualDialogue(){const dialog=$("#manualEventDialog"),text=$("#manualEventText").value.trim(),start=Number($("#manualEventStart").value),end=Number($("#manualEventEnd").value);if(!text)return toast("Digite o texto do diálogo.",true);if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)return toast("O fim precisa ser posterior ao início.",true);const button=$("#saveManualEvent");button.disabled=true;try{const created=await api("/api/events/manual",{method:"POST",body:JSON.stringify({session_id:dialog.dataset.sessionId,text,start_time:start,end_time:end,character:$("#manualEventCharacter").value.trim()||null})});dialog.close();await refreshEvents(true);selectedEventId=created.event_id;selectEvent(created.event_id,false);if(document.body.classList.contains("dubbing-active"))void window.DubSyncDubbing?.refreshTimelineData?.();toast("Diálogo manual criado na timeline.");}catch(error){toast(error.message,true);}finally{button.disabled=false;}}
async function duplicateSelectedDialogue(){
  const event=events.find((item)=>item.event_id===selectedEventId);if(!event)return toast("Selecione um diálogo para duplicar.",true);
  if(event.session_id!==activeSessionId)return toast("Abra o bloco do diálogo selecionado antes de duplicar.",true);
  const atTime=videoClock(),button=$("#duplicateDialogue");button.disabled=true;button.textContent="Duplicando…";
  try{const copy=await api(`/api/events/${encodeURIComponent(event.event_id)}/duplicate`,{method:"POST",body:JSON.stringify({at_time:atTime})});await refreshEvents(true);selectEvent(copy.event_id,false);toast(`Diálogo duplicado no playhead (${timecode(copy.start_time,true)}). Revise o texto e o tempo.`);}
  catch(error){toast(error.message,true);}finally{button.textContent="⧉ Duplicar";button.disabled=false;}
}
async function retrySelectedDialogueOcr(){
  const event=events.find((item)=>item.event_id===selectedEventId);if(!event)return toast("Selecione um diálogo para refazer o OCR.",true);
  const cursor=videoClock(),atTime=cursor>=event.start_time&&cursor<=(event.end_time??event.start_time)?cursor:event.start_time+((event.end_time??event.start_time)-event.start_time)/2;
  const button=$("#retryDialogueOcr");button.disabled=true;button.textContent="Lendo…";
  try{const result=await api(`/api/events/${encodeURIComponent(event.event_id)}/retry-ocr`,{method:"POST",body:JSON.stringify({at_time:atTime})});await refreshEvents(true);if(!result.processed_text){toast("O OCR não encontrou texto neste frame. A leitura vazia ficou no histórico; tente outro ponto dentro do diálogo.",true);return;}const dialog=$("#ocrRetryDialog");dialog.dataset.eventId=event.event_id;$("#ocrRetryPrevious").value=event.corrected_text??event.processed_text??event.original_text??"";$("#ocrRetryCandidate").value=result.processed_text;$("#ocrRetryInfo").textContent=`Frame ${timecode(result.source_time,true)} · confiança ${Math.round(result.confidence*100)}%. Compare antes de aplicar.`;dialog.showModal();}
  catch(error){toast(error.message,true);}finally{button.textContent="◉ Refazer OCR";button.disabled=false;}
}
async function applyOcrRetry(){const dialog=$("#ocrRetryDialog"),id=dialog.dataset.eventId,text=$("#ocrRetryCandidate").value.trim();if(!id||!text)return;const button=$("#ocrRetryApply");button.disabled=true;try{await api(`/api/events/${encodeURIComponent(id)}/review/correct`,{method:"POST",body:JSON.stringify({corrected_text:text})});dialog.close();await refreshEvents(true);selectEvent(id,false);toast("Nova leitura aplicada como correção; OCR original preservado.");}catch(error){toast(error.message,true);}finally{button.disabled=false;}}
function captureTimelinePosition(){return{sessionId:timelineIdentity(),start:timelineRange.start,span:timelineRange.span,playhead:videoClock()};}
function restoreTimelinePosition(position){if(!position?.sessionId||!Number.isFinite(position.start)||!Number.isFinite(position.span))return;saveTimelineView(position.sessionId,position.start,position.span);}
async function focusAfterRemoval(removedIds,preferredSessionId="",timelinePosition=captureTimelinePosition()){
  await refreshEvents(true,true,timelinePosition);
  const candidates=events.filter((item)=>!removedIds.includes(item.event_id)&&item.review_status!=="discarded"&&item.review_status!=="merged"&&(!preferredSessionId||item.session_id===preferredSessionId));
  const next=candidates[0]||events.find((item)=>!removedIds.includes(item.event_id)&&item.review_status!=="discarded");
  selectedEventId=next?.event_id||"";
  restoreTimelinePosition(timelinePosition);
  if(next)selectEvent(next.event_id,false,false,true);else{clearSelected();renderRows();renderTimeline();}
}
async function bulkRemoveSelected(){
  const ids=$$(".merge-check:checked").map((item)=>item.dataset.id);if(!ids.length)return;
  const timelinePosition=captureTimelinePosition();
  try{const result=await api("/api/events/bulk-discard",{method:"POST",body:JSON.stringify({event_ids:ids})});await focusAfterRemoval(ids,"",timelinePosition);toast(`${result.removed||ids.length} diálogo(s) removido(s) da revisão e preservado(s) em Descartados.`);}catch(error){toast(error.message,true);}
}
async function bulkAcceptSelected(){
  const ids=$$(".merge-check:checked").map((item)=>item.dataset.id);if(!ids.length)return;
  try{const result=await api("/api/events/bulk-accept",{method:"POST",body:JSON.stringify({event_ids:ids})});await refreshEvents(true,true);toast(`${result.confirmed||ids.length} diálogo(s) confirmado(s).`);}catch(error){toast(error.message,true);}
}
function handleGlobalShortcut(event){
  if(recordShortcut(event))return;
  // Keep editable fields and controls with their own keyboard behavior intact, but
  // don't tie application shortcuts to the currently focused panel or button.
  if(event.repeat||event.defaultPrevented||editableKeyboardTarget(event.target))return;
  const entry=SHORTCUT_ACTIONS.find(([id])=>sameShortcut(shortcutBindings[id],{code:event.code,ctrl:event.ctrlKey,alt:event.altKey,shift:event.shiftKey,meta:event.metaKey}));
  if(!entry)return;
  event.preventDefault();
  const [id]=entry;
  if(document.body.classList.contains("dubbing-active")){window.DubSyncDubbing?.handleTimelineShortcut(id,{main:shortcutSteps.main,fine:shortcutSteps.fine,fast:shortcutSteps.fast});return;}
  switch(id){case"togglePlayback":togglePreviewPlayback();break;case"seekBackward":if(timelinePinSelectedIds.size)void moveSelectedCapturePins(-captureTimelineMoveStep());else moveBySeconds(-shortcutSteps.main);break;case"seekForward":if(timelinePinSelectedIds.size)void moveSelectedCapturePins(captureTimelineMoveStep());else moveBySeconds(shortcutSteps.main);break;case"seekFineBackward":if(timelinePinSelectedIds.size)void moveSelectedCapturePins(-captureTimelineMoveStep());else moveBySeconds(-shortcutSteps.fine);break;case"seekFineForward":if(timelinePinSelectedIds.size)void moveSelectedCapturePins(captureTimelineMoveStep());else moveBySeconds(shortcutSteps.fine);break;case"seekFastBackward":if(timelinePinSelectedIds.size)void moveSelectedCapturePins(-captureTimelineMoveStep());else moveBySeconds(-shortcutSteps.fast);break;case"seekFastForward":if(timelinePinSelectedIds.size)void moveSelectedCapturePins(captureTimelineMoveStep());else moveBySeconds(shortcutSteps.fast);break;case"previousDialogue":navigateDialogue(-1);break;case"nextDialogue":navigateDialogue(1);break;case"manualDialogue":openManualEventDialog();break;case"removeDialogue":void discardSelectedEvent();break;case"zoomIn":zoomTimeline(.8);break;case"zoomOut":zoomTimeline(1.25);break;case"fitTimeline":fitTimeline();break;case"centerPlayhead":centerTimelineOnPlayhead();break;case"openDubbing":window.DubSyncDubbing?.open();break;case"generateDubbingTake":void window.DubSyncDubbing?.generate();break;}
}
function renderDetails(event){
  $("#selectedEventLabel").textContent=`#${event.sequence} · ${timecode(event.start_time)}`;
  $("#detailOriginalLabel").textContent=event.segment_id==="manual"?"Texto manual (sem OCR)":"OCR original";
  $("#detailOriginal").value=event.original_text||"";
  $("#detailCorrected").value=event.corrected_text??event.processed_text??event.original_text??"";
  $("#detailConfidence").textContent=event.segment_id==="manual"?"Anotação manual":`${Math.round((event.confidence||0)*100)}%`;
  $("#detailConfidenceLabel").textContent=event.segment_id==="manual"?"Origem":"Confiança OCR";
  $("#detailNormalized").textContent=event.normalized_text||"—";
  $("#detailId").textContent=event.event_id;
  $("#detailTimes").textContent=`${timecode(event.start_time,true)} → ${timecode(event.end_time,true)}`;
  $("#detailOrigin").textContent=`${event.segment_id||"segmento local"} · frame ${event.frame_id}`;
  $("#detailTiming").textContent=`${event.timing_quality||"?"} ±${Math.round(event.timing_error_ms||0)} ms`;
  $("#detailHash").textContent=event.text_phash||"—";
  $("#detailReviewState").textContent=statusText(event);
  const observations=Array.isArray(event.ocr_observations_json)?event.ocr_observations_json:[];
  const latestRetry=[...observations].reverse().find((item)=>item.source==="manual_retry");
  $("#detailLatestRaw").value=latestRetry?.text||"";
  $("#detailLatestProcessed").value=latestRetry?.processed_text||"";
  $("#detailLatestOcrInfo").textContent=latestRetry?`${Math.round((latestRetry.confidence||0)*100)}% · ${timecode(latestRetry.source_time,true)} · frame ${latestRetry.frame_id??"—"}`:"—";
  const names={pre_1000:"−1000 ms",pre_500:"−500 ms",pre_250:"−250 ms",start:"Início",best:"Estável",end:"Fim",post_250:"+250 ms",ocr_retry_crop:"Recorte da nova leitura",ocr_retry_frame:"Frame da nova leitura"};
  const retryAssets=Array.isArray(latestRetry?.image_assets)?latestRetry.image_assets.filter((asset)=>asset?.path):[];
  const assets=Array.isArray(event.assets_json)?event.assets_json:[];
  const options=[...retryAssets.map((asset)=>({kind:asset.kind,path:asset.path,label:names[asset.kind]||"Nova imagem OCR"})),...(event.crop_path?[{kind:"crop",path:event.crop_path,label:"Crop (legenda)"}]:[]),...assets.map((asset)=>({kind:asset.kind,path:asset.path,label:event.segment_id==="manual"&&asset.kind==="start"?"Quadro manual (original)":names[asset.kind]||asset.kind}))];
  selectedThumb=options[0]?.kind||"crop";
  const hero=$("#detailHero"),heroOption=options[0];
  hero.hidden=!heroOption;
  $("#heroWrap").classList.toggle("empty",!heroOption);
  if(heroOption){hero.src=assetUrl(event.session_id,heroOption.path);$("#heroCaption").textContent=heroOption.label;}
  else{hero.removeAttribute("src");$("#heroCaption").textContent="Sem imagem disponível para este evento";}
  const thumbs=$("#detailThumbs");thumbs.replaceChildren();
  for(const option of options){
    const button=document.createElement("button");button.className=`detail-thumb${option.kind===selectedThumb?" active":""}`;button.type="button";
    const img=document.createElement("img");img.src=assetUrl(event.session_id,option.path);img.alt=option.label;
    const caption=document.createElement("span");caption.textContent=option.label;button.append(img,caption);
    button.addEventListener("click",()=>{selectedThumb=option.kind;hero.src=assetUrl(event.session_id,option.path);hero.hidden=false;$("#heroWrap").classList.remove("empty");$("#heroCaption").textContent=option.label;$$('.detail-thumb').forEach((node)=>node.classList.toggle("active",node===button));});
    thumbs.append(button);
  }
}function renderDialogueSummary(summary){
  const total=Math.max(0,Number(summary.total)||0),green=Math.max(0,Number(summary.green)||0),pending=Math.max(0,Number(summary.pending)||0),low=Math.max(0,Number(summary.low_confidence)||0),discarded=Math.max(0,Number(summary.discarded)||0),active=green+pending+low,greenPercent=active?green/active*100:0;
  $("#dialogueGreenPercent").textContent=Math.round(greenPercent)+"%";
  $("#dialogueGreenRatio").textContent=green+" / "+active;
  $("#dialogueGreenProgress").style.width=greenPercent+"%";
  $("#dialogueGreenProgress").parentElement.setAttribute("aria-valuenow",String(Math.round(greenPercent)));
  const states=[["green",green],["pending",pending],["low",low],["discarded",discarded]];
  for(const [name,count] of states){const share=total?count/total*100:0;$("#dialogueStack"+({green:"Green",pending:"Pending",low:"Low",discarded:"Discarded"})[name]).style.width=share+"%";$("#dialogue"+({green:"Green",pending:"Pending",low:"Low",discarded:"Discarded"})[name]+"Breakdown").textContent=count+" · "+Math.round(share)+"%";}
  $("#dialogueStatusStacked").setAttribute("aria-label",total?("Total "+total+": "+green+" confirmados ou corrigidos, "+pending+" pendentes, "+low+" com baixa confiança e "+discarded+" descartados."):"Nenhum diálogo neste escopo.");
  $("#dialogueTotalLabel").textContent=total?"Total: "+total+" · "+active+" ativos para revisão · "+discarded+" descartados.":"Nenhum diálogo encontrado neste escopo.";
  $("#dialogueStatusScope").textContent=$("#sessionFilter").value?"Sessão selecionada · independente dos filtros da lista":"Todas as sessões · independente dos filtros da lista";
}
async function refreshDialogueSummary(){
  const sessionId=$("#sessionFilter").value,query=sessionId?"?session_id="+encodeURIComponent(sessionId):"";
  try{renderDialogueSummary(await api("/api/events/summary"+query));}
  catch(error){console.warn("Não foi possível atualizar o resumo dos diálogos.",error);$("#dialogueTotalLabel").textContent="Resumo dos diálogos indisponível.";}
}let eventFilterOptionsSession="",eventFilterOptionsLoadedAt=0;
async function refreshEventFilterOptions(force=false){
  const sessionId=$("#sessionFilter").value;
  if(!force&&eventFilterOptionsSession===sessionId&&Date.now()-eventFilterOptionsLoadedAt<8000)return;
  try{
    const params=new URLSearchParams();if(sessionId)params.set("session_id",sessionId);
    const result=await api(`/api/events/filter-options?${params}`);
    for(const [selector,items,noneLabel,allLabel,hasNone] of [
      ["#characterFilter",result.characters||[],"Sem personagem","Todos os personagens",result.has_unassigned_character],
      ["#emotionFilter",result.emotions||[],"Sem emoção","Todas as emoções",result.has_unassigned_emotion],
    ]){
      const select=$(selector),previous=select.value;select.replaceChildren(new Option(allLabel,""));
      if(hasNone)select.add(new Option(noneLabel,"__none__"));
      for(const item of items)select.add(new Option(item,item));
      select.value=[...select.options].some((option)=>option.value===previous)?previous:"";
    }
    eventFilterOptionsSession=sessionId;eventFilterOptionsLoadedAt=Date.now();
  }catch(error){console.warn("Não foi possível carregar os filtros de personagens e emoções.",error);}
}
async function refreshEvents(preservePlayback=false,preserveTimeline=false,timelinePosition=null){
  const viewToPreserve=preserveTimeline?(timelinePosition||captureTimelinePosition()):null;
  try{
    void refreshDialogueSummary();await refreshEventFilterOptions();await refreshRoiCharacterColors();
    const query=new URLSearchParams(),filter=$("#statusFilter").value;
    if($("#sessionFilter").value)query.set("session_id",$("#sessionFilter").value);
    if(filter&&filter!=="fine-sweep")query.set("status",filter);
    const character=$("#characterFilter").value,emotion=$("#emotionFilter").value;
    if(character)query.set("character",character);if(emotion)query.set("emotion",emotion);
    const min=Number($("#minConfidence").value||0);if(min>0)query.set("min_confidence",String(min));
    const result=await api(`/api/events?${query}`);events=filter==="fine-sweep"?result.events.filter((item)=>fineSweepEventIds().has(item.event_id)):result.events;
    const markerSessionId=viewToPreserve?.sessionId||$("#sessionFilter").value||events[0]?.session_id||activeSessionId;timelineMarkers=markerSessionId?(await api(`/api/sessions/${markerSessionId}/markers`)).markers:[];
    if(!events.some((item)=>item.event_id===selectedEventId)){const sameSession=viewToPreserve?.sessionId?events.find((item)=>item.session_id===viewToPreserve.sessionId):null;selectedEventId=sameSession?.event_id||events[0]?.event_id||"";}
    if(viewToPreserve)restoreTimelinePosition(viewToPreserve);
    renderRows();renderTimeline();if(selectedEventId)selectEvent(selectedEventId,!preservePlayback,false,Boolean(viewToPreserve));else clearSelected();updateMergeToolbar();lastEventRefreshAt=Date.now();
  }catch(error){toast(error.message,true);}
}
window.DubSyncCaptureRefreshTimeline=()=>refreshEvents(true,true);
function clearSelected(){$("#reviewOriginalLabel").textContent="Texto Original (OCR)";$("#reviewConfidenceCaption").textContent="Confiança OCR";$("#detailOriginalLabel").textContent="OCR original";$("#detailConfidenceLabel").textContent="Confiança OCR";$("#detailLatestRaw").value="";$("#detailLatestProcessed").value="";$("#detailLatestOcrInfo").textContent="—";for(const selector of ["#reviewOriginal","#reviewCorrected"]){$(selector).value="";}$("#reviewSelectionHint").textContent="Selecione um evento na tabela ou timeline";$("#reviewConfidence").textContent="—";["#acceptButton","#correctButton","#adjustButton","#mergePrevious","#mergeNext","#splitButton","#discardButton","#duplicateDialogue","#retryDialogueOcr"].forEach((selector)=>$(selector).disabled=true);$("#selectedEventLabel").textContent="Nenhum evento";const hero=$("#detailHero");hero.removeAttribute("src");hero.hidden=true;$("#heroWrap").classList.add("empty");$("#heroCaption").textContent="Selecione um evento";$("#detailThumbs").replaceChildren();$("#reviewCharacter").value="";updateReviewCharacterPhoto();}
async function refreshSessions(){try{const data=await api("/api/sessions"),select=$("#sessionFilter"),previous=select.value;sessions=data.sessions;select.replaceChildren(new Option("Todas as sessões",""));for(const session of sessions){const sourceLabel=({youtube:"YouTube",file:"Arquivo",game:"Jogo"})[session.source_type]||session.source_type;select.add(new Option(`${sourceLabel} · ${session.session_id.slice(0,8)} · ${sessionStateLabel(session)}`,session.session_id));}if(activeSessionId&&[...select.options].some((item)=>item.value===activeSessionId))select.value=activeSessionId;else if([...select.options].some((item)=>item.value===previous))select.value=previous;renderVideoOverview();updatePreviousBlockButton();}catch(error){console.warn(error);}}
function updateMergeToolbar(){const all=$$(".merge-check"),checked=$$(".merge-check:checked"),selectAll=$("#selectAllEvents");$("#mergeToolbar").classList.toggle("hidden",checked.length===0);$("#mergeCount").textContent=`${checked.length} selecionado(s)`;$("#bulkAcceptButton").disabled=checked.length===0;$("#mergeButton").disabled=checked.length<2||checked.length>12;$("#mergeButton").title=checked.length>12?"A união suporta até 12 eventos por vez.":"Unir os eventos selecionados.";$("#bulkRemoveButton").disabled=checked.length===0;if(selectAll){selectAll.disabled=all.length===0;selectAll.checked=all.length>0&&checked.length===all.length;selectAll.indeterminate=checked.length>0&&checked.length<all.length;}}
async function mergeIds(ids){if(ids.length<2)return;try{await api("/api/events/merge",{method:"POST",body:JSON.stringify({event_ids:ids})});toast("Eventos unidos. Os registros anteriores foram preservados.");await refreshEvents();}catch(error){toast(error.message,true);}}
async function mergeAdjacent(direction){const index=events.findIndex((event)=>event.event_id===selectedEventId),other=events[index+direction];if(!other)return;await mergeIds(direction<0?[other.event_id,selectedEventId]:[selectedEventId,other.event_id]);}
async function discardSelectedEvent(){
  const event=events.find((item)=>item.event_id===selectedEventId);if(!event)return;
  const timelinePosition=captureTimelinePosition();
  try{
    const settings=await api("/api/settings"),permanent=Boolean(settings.review?.permanent_discard);
    if(permanent){
      const answer=confirm("Excluir este diálogo definitivamente? O registro, o histórico de revisão e os dados de dublagem que não forem compartilhados serão apagados. Esta ação não pode ser desfeita.");
      if(!answer)return;
      const result=await api(`/api/events/${encodeURIComponent(event.event_id)}`,{method:"DELETE"});
      toast(result.restored_merged_sources?`Diálogo excluído; ${result.restored_merged_sources} parte(s) da união voltaram à lista.`:"Diálogo excluído definitivamente.");
    }else{
      await api(`/api/events/${encodeURIComponent(event.event_id)}/review/discard`,{method:"POST",body:"{}"});toast("Evento descartado e preservado para revisão.");
    }
    await focusAfterRemoval([event.event_id],event.session_id,timelinePosition);
  }catch(error){toast(error.message,true);}
}

async function saveReview(action){
  const event=events.find((item)=>item.event_id===selectedEventId);if(!event)return;
  const corrected=$("#reviewCorrected").value.trim();
  const changes={corrected_text:corrected,start_time:Number($("#reviewStart").value),end_time:$("#reviewEnd").value===""?null:Number($("#reviewEnd").value),character:$("#reviewCharacter").value,emotion:$("#reviewEmotion").value};
  if(changes.end_time!=null&&changes.end_time<changes.start_time)return toast("O fim precisa ser posterior ao início.",true);
  const route=action==="correct"?"correct":action==="accept"?"accept":"update";
  try{
    await api(`/api/events/${event.event_id}/review/${route}`,{method:"POST",body:JSON.stringify(changes)});
    characterOptionsLoadedAt=0;eventFilterOptionsLoadedAt=0;
    toast(action==="correct"?"Texto corrigido e salvo.":action==="accept"?"Evento confirmado e campos salvos.":"Revisão salva.");
    await refreshEvents(true,true);
  }catch(error){toast(error.message,true);}
}
function openReplaceDialog(){
  const dialog=$("#replaceDialog");
  renderReplaceRules([{"find_text":"","replace_text":""}]);
  $("#replaceRulesStatus").textContent="Carregando regras deste projeto…";
  $("#saveReplaceRules").disabled=true;
  dialog.showModal();
  void api("/api/project/text-replacements").then((result)=>{
    if(!dialog.open)return;
    const rules=Array.isArray(result.rules)?result.rules:[];
    renderReplaceRules(rules.length?rules:[{"find_text":"","replace_text":""}]);
    $("#replaceRulesStatus").textContent=rules.length?`${rules.length} regra(s) carregada(s).`:"Nenhuma regra salva. Adicione a primeira se quiser corrigir o OCR automaticamente.";
  }).catch((error)=>{$("#replaceRulesStatus").textContent=error.message;toast(error.message,true);})
    .finally(()=>{if(dialog.open)$("#saveReplaceRules").disabled=false;});
}
function renderReplaceRules(rules){
  const list=$("#replaceRulesList");list.replaceChildren();
  for(const rule of rules){
    const row=document.createElement("div"),findLabel=document.createElement("label"),replaceLabel=document.createElement("label"),findInput=document.createElement("input"),replaceInput=document.createElement("input"),remove=document.createElement("button");
    row.className="replace-rule-row";
    findLabel.className="field";findLabel.textContent="Localizar";
    findInput.type="text";findInput.maxLength=4000;findInput.autocomplete="off";findInput.placeholder="Ex.: R.E.A.C.H";findInput.value=rule.find_text||"";findInput.dataset.replaceField="find";findLabel.append(findInput);
    replaceLabel.className="field";replaceLabel.textContent="Substituir por";
    replaceInput.type="text";replaceInput.maxLength=4000;replaceInput.autocomplete="off";replaceInput.placeholder="Ex.: Reach";replaceInput.value=rule.replace_text||"";replaceInput.dataset.replaceField="with";replaceLabel.append(replaceInput);
    remove.type="button";remove.className="button replace-rule-remove";remove.textContent="🗑";remove.title="Remover regra";remove.setAttribute("aria-label","Remover regra");remove.addEventListener("click",()=>{row.remove();updateReplaceRuleCount();});
    row.append(findLabel,replaceLabel,remove);list.append(row);
  }
  updateReplaceRuleCount();
}
function updateReplaceRuleCount(){
  const count=$("#replaceRulesList").children.length;$("#replaceRuleCount").textContent=`${count} / 100 regras`;
  $("#addReplaceRule").disabled=count>=100;
}
function addReplaceRule(){
  const rules=$$("#replaceRulesList .replace-rule-row").map((row)=>({
    find_text:row.querySelector('[data-replace-field="find"]').value,
    replace_text:row.querySelector('[data-replace-field="with"]').value,
  }));
  if(rules.length>=100)return;
  rules.push({find_text:"",replace_text:""});renderReplaceRules(rules);
  $("#replaceRulesList .replace-rule-row:last-child [data-replace-field=\"find\"]").focus();
}
async function saveReplaceRules(){
  const rows=$$("#replaceRulesList .replace-rule-row");
  const rules=rows.map((row)=>({find_text:row.querySelector('[data-replace-field="find"]').value.trim(),replace_text:row.querySelector('[data-replace-field="with"]').value.trim()})).filter((rule)=>rule.find_text||rule.replace_text);
  const seen=new Set();
  for(let index=0;index<rules.length;index++){
    const rule=rules[index];
    if(!rule.find_text){$("#replaceRulesStatus").textContent=`Preencha o texto a localizar na regra ${index+1}.`;return;}
    const key=rule.find_text.normalize("NFKC").toLocaleLowerCase("pt-BR");
    if(seen.has(key)){$("#replaceRulesStatus").textContent=`A regra ${index+1} repete um texto de busca já cadastrado.`;return;}
    seen.add(key);
  }
  const button=$("#saveReplaceRules");button.disabled=true;$("#cancelReplace").disabled=true;$("#replaceRulesStatus").textContent="Salvando regras…";
  try{
    const result=await api("/api/project/text-replacements",{method:"PUT",body:JSON.stringify({rules})});
    $("#replaceDialog").close();toast(`${result.rules.length} regra(s) de OCR salva(s) para este projeto.`);
  }catch(error){$("#replaceRulesStatus").textContent=error.message;toast(error.message,true);}
  finally{button.disabled=false;$("#cancelReplace").disabled=false;}
}
async function openSplit(){
  const event=events.find((item)=>item.event_id===selectedEventId);if(!event)return toast("Selecione o diálogo que deseja dividir.",true);
  if(event.end_time==null||!Number.isFinite(Number(event.end_time)))return toast("Este diálogo não tem um tempo final válido para dividir.",true);
  const start=Number(event.start_time),end=Number(event.end_time),minimum=start+.001,maximum=end-.001;
  if(maximum<=minimum)return toast("O diálogo é curto demais para dividir em dois trechos.",true);
  const midpoint=(start+end)/2,cursor=videoClock(),cursorInside=cursor>start&&cursor<end;
  const suggested=cursorInside?Math.max(minimum,Math.min(maximum,cursor)):midpoint,dialog=$("#splitDialog");
  $("#splitTimeInput").min=String(minimum);$("#splitTimeInput").max=String(maximum);$("#splitTimeInput").value=suggested.toFixed(3);
  dialog._splitPreviewController?.abort();dialog.classList.toggle("no-preview",!showSplitPreview);
  dialog.dataset.event=event.event_id;dialog.dataset.time="";dialog.dataset.previewRequested="";dialog.dataset.previewRequestId="0";$("#splitPreviewImage").removeAttribute("src");$("#splitPreviewImage").hidden=true;$("#splitPreviewPlaceholder").hidden=false;$("#splitPreviewPlaceholder").classList.toggle("loading",showSplitPreview);$("#splitPlaceholderText").textContent=showSplitPreview?"Buscando o frame no ponto escolhido…":"Prévia desativada nas configurações.";$("#confirmSplit").disabled=showSplitPreview;updateSplitTimestampHint(suggested);
  $("#splitReason").textContent=cursorInside
    ?`O cursor está dentro deste diálogo. O corte foi posicionado em ${timecode(suggested,true)}.`
    :`O cursor está fora deste diálogo. A divisão será no ponto médio, em ${timecode(midpoint,true)}.`;
  $("#splitTimeLabel").textContent=showSplitPreview?"Aguarde enquanto localizo o frame correspondente.":"Confirme para dividir no tempo escolhido; os frames serão preservados.";
  if(!dialog.open)dialog.showModal();
  if(showSplitPreview)void previewSplit();
}
function updateSplitTimestampHint(value=Number($("#splitTimeInput").value)){$("#splitTimestampHint").textContent=Number.isFinite(value)&&value>=0?`${timecode(value,true)} na linha do tempo do vídeo`:"Informe um tempo válido em segundos.";}
async function previewSplit(){
  const dialog=$("#splitDialog"),eventId=dialog.dataset.event,point=Number($("#splitTimeInput").value),button=$("#previewSplit"),confirmButton=$("#confirmSplit");if(dialog.classList.contains("no-preview")||!eventId||!Number.isFinite(point))return;
  const event=events.find((item)=>item.event_id===eventId);if(!event||point<=Number(event.start_time)||point>=Number(event.end_time)){toast("Escolha um ponto estritamente entre o início e o fim do diálogo.",true);return;}
  dialog._splitPreviewController?.abort();const controller=new AbortController(),requestId=String((Number(dialog.dataset.previewRequestId)||0)+1);dialog._splitPreviewController=controller;dialog.dataset.previewRequestId=requestId;button.disabled=true;confirmButton.disabled=true;dialog.dataset.time="";dialog.dataset.previewRequested="";$("#splitPreviewImage").hidden=true;$("#splitPreviewPlaceholder").hidden=false;$("#splitPreviewPlaceholder").classList.add("loading");$("#splitPlaceholderText").textContent="Buscando o frame no ponto escolhido…";$("#splitTimeLabel").textContent="Carregando a imagem da prévia…";const timeoutId=setTimeout(()=>controller.abort(),15000);
  try{
    const sample=await api(`/api/events/${encodeURIComponent(eventId)}/split-preview?split_time=${encodeURIComponent(point)}`,{signal:controller.signal});
    if(dialog.dataset.previewRequestId!==requestId||!dialog.open||Number($("#splitTimeInput").value)!==point)return;
    const image=$("#splitPreviewImage"),loaded=await new Promise((resolve)=>{image.onload=()=>resolve(true);image.onerror=()=>resolve(false);image.src=sample.image;if(image.complete&&image.naturalWidth>0)resolve(true);});
    if(dialog.dataset.previewRequestId!==requestId||!dialog.open||Number($("#splitTimeInput").value)!==point)return;
    if(!loaded)throw new Error("O frame foi encontrado, mas a imagem da prévia não pôde ser exibida.");
    const resolvedTime=Number(sample.source_time);image.hidden=false;$("#splitPreviewPlaceholder").hidden=true;$("#splitPreviewPlaceholder").classList.remove("loading");
    $("#splitTimeInput").value=resolvedTime.toFixed(3);updateSplitTimestampHint(resolvedTime);
    $("#splitTimeLabel").textContent=`Frame mais próximo: ${timecode(resolvedTime,true)} · ${sample.timing_quality} · erro ±${Math.round(sample.timing_error_ms||0)} ms`;
    dialog.dataset.time=String(resolvedTime);dialog.dataset.previewRequested=String(Number($("#splitTimeInput").value));confirmButton.disabled=false;
  }
  catch(error){if(dialog.dataset.previewRequestId===requestId){const message=controller.signal.aborted?"A busca do frame passou de 15 segundos e foi cancelada.":error.message;$("#splitPreviewPlaceholder").hidden=false;$("#splitPreviewPlaceholder").classList.remove("loading");$("#splitPlaceholderText").textContent="A prévia não ficou disponível. Você ainda pode confirmar o tempo selecionado ou tentar de novo.";$("#splitTimeLabel").textContent=`Prévia indisponível: ${message}`;confirmButton.disabled=false;toast(message,true);}}
  finally{clearTimeout(timeoutId);if(dialog.dataset.previewRequestId===requestId){dialog._splitPreviewController=null;button.disabled=false;}}
}
function characterOptionsOpen(){return $("#characterOptions")?.matches(":popover-open")||false;}
function roiCharacterLabel(profile){return profile?.alias?.trim()||profile?.name?.trim()||"";}
function findEventCharacterProfile(name){const normalized=(name||"").trim().toLocaleLowerCase("pt-BR");return characterOptionsData.find((person)=>[person.alias,person.name].some((value)=>value?.trim().toLocaleLowerCase("pt-BR")===normalized));}
function createCharacterAvatar(profile,name,className){
  const avatar=document.createElement("span");avatar.className=className;
  avatar.setAttribute("aria-hidden","true");
  if(/^#[0-9a-f]{6}$/i.test(String(profile?.color||""))){avatar.dataset.characterColor=profile.color;avatar.style.setProperty("--character-color",profile.color);}
  const image=document.createElement("img");image.alt="";image.hidden=true;
  const fallback=document.createElement("span");fallback.textContent=(name||"?").trim().slice(0,1).toLocaleUpperCase("pt-BR")||"?";
  if(profile?.photo_url){image.src=profile.photo_url;image.hidden=false;fallback.hidden=true;}
  image.addEventListener("error",()=>{image.hidden=true;fallback.hidden=false;});
  avatar.append(image,fallback);return avatar;
}
function updateReviewCharacterPhoto(){
  const input=$("#reviewCharacter"),container=$("#reviewCharacterCombobox");if(!input||!container)return;
  let avatar=$("#reviewCharacterPhoto");
  if(!avatar){avatar=document.createElement("span");avatar.id="reviewCharacterPhoto";avatar.className="character-current-avatar";avatar.setAttribute("aria-hidden","true");container.insertBefore(avatar,input);}
  const name=input.value.trim(),profile=findEventCharacterProfile(name);
  const image=document.createElement("img");image.alt="";image.hidden=true;
  const fallback=document.createElement("span");fallback.textContent=(roiCharacterLabel(profile)||name||"?").trim().slice(0,1).toLocaleUpperCase("pt-BR")||"?";
  if(profile?.photo_url){image.src=profile.photo_url;image.hidden=false;fallback.hidden=true;}
  image.addEventListener("error",()=>{image.hidden=true;fallback.hidden=false;});
  avatar.replaceChildren(image,fallback);
  if(/^#[0-9a-f]{6}$/i.test(String(profile?.color||""))){avatar.dataset.characterColor=profile.color;avatar.style.setProperty("--character-color",profile.color);}
  input.style.color=/^#[0-9a-f]{6}$/i.test(String(profile?.color||""))?profile.color:"";
  avatar.title=profile?.photo_name?profile.photo_name:(roiCharacterLabel(profile)||"Foto do personagem");
}
function closeEventCharacterOptions(){
  const input=$("#reviewCharacter"),toggle=$("#reviewCharacterToggle"),menu=$("#characterOptions");
  if(menu?.matches(":popover-open"))menu.hidePopover();
  input?.setAttribute("aria-expanded","false");toggle?.setAttribute("aria-expanded","false");
  input?.removeAttribute("aria-activedescendant");characterOptionsActiveIndex=-1;
}
async function loadEventCharacterOptions({force=false}={}){
  const event=events.find((item)=>item.event_id===selectedEventId),regionId=event?.region_id||"";
  if(!regionId){characterOptionsRegionId="";characterOptionsData=[];characterOptionsLoadedAt=Date.now();updateReviewCharacterPhoto();return characterOptionsData;}
  if(!force&&characterOptionsRegionId===regionId&&Date.now()-characterOptionsLoadedAt<15000)return characterOptionsData;
  if(characterOptionsLoadPromise?.regionId===regionId)return characterOptionsLoadPromise.promise;
  const requestId=++characterOptionsRequestId;
  const promise=(async()=>{
    try{
      try{await api(`/api/roi-characters/${encodeURIComponent(regionId)}/sync-from-events`,{method:"POST",body:"{}"});}
      catch(syncError){console.warn("Não foi possível sincronizar nomes detectados; carregando os personagens já cadastrados.",syncError);}
      const result=await api(`/api/roi-characters?roi_id=${encodeURIComponent(regionId)}`);
      const selected=events.find((item)=>item.event_id===selectedEventId);
      if(requestId!==characterOptionsRequestId||selected?.region_id!==regionId)return [];
      characterOptionsRegionId=regionId;characterOptionsData=(result.characters||[]).filter((person)=>person.name?.trim());characterOptionsLoadedAt=Date.now();updateReviewCharacterPhoto();
      return characterOptionsData;
    }catch(error){
      if(requestId===characterOptionsRequestId){characterOptionsRegionId=regionId;characterOptionsData=[];characterOptionsLoadedAt=0;updateReviewCharacterPhoto();}
      console.warn("Não foi possível carregar os nomes cadastrados nesta ROI:",error);return [];
    }
    finally{if(characterOptionsLoadPromise?.requestId===requestId)characterOptionsLoadPromise=null;}
  })();
  characterOptionsLoadPromise={regionId,requestId,promise};
  return promise;
}
function renderEventCharacterOptions(filter=false){
  const input=$("#reviewCharacter"),menu=$("#characterOptions"),query=filter?input.value.trim().toLocaleLowerCase("pt-BR"):"";
  const names=["Desconhecido",...characterOptionsData.map(roiCharacterLabel).filter(Boolean)];
  const unique=[...new Map(names.map((name)=>[name.toLocaleLowerCase("pt-BR"),name])).values()];
  visibleCharacterOptions=query?unique.filter((name)=>name.toLocaleLowerCase("pt-BR").includes(query)
    ||findEventCharacterProfile(name)?.name?.trim().toLocaleLowerCase("pt-BR").includes(query)):unique;
  menu.replaceChildren();characterOptionsActiveIndex=-1;input.removeAttribute("aria-activedescendant");
  if(!visibleCharacterOptions.length){
    const empty=document.createElement("div");empty.className="character-option-empty";empty.setAttribute("role","status");
    empty.textContent=query?`Nenhum personagem encontrado. “${input.value.trim()}” será salvo como novo nome ao salvar a revisão.`:"Nenhum personagem cadastrado nesta ROI. Digite um nome e salve a revisão para cadastrá-lo.";
    menu.append(empty);return;
  }
  const selected=input.value.trim().toLocaleLowerCase("pt-BR");
  visibleCharacterOptions.forEach((name,index)=>{
    const option=document.createElement("button");option.type="button";option.className="character-option";option.id=`character-option-${index}`;
    const normalized=name.toLocaleLowerCase("pt-BR"),profile=findEventCharacterProfile(name);
    option.append(createCharacterAvatar(profile,name,"character-option-avatar"));
    const label=document.createElement("span");label.className="character-option-name";label.textContent=name;if(/^#[0-9a-f]{6}$/i.test(String(profile?.color||"")))label.style.color=profile.color;option.append(label);
    option.setAttribute("role","option");option.setAttribute("aria-selected",String(normalized===selected));option.dataset.value=name;option.title=name;
    option.addEventListener("pointerdown",(event)=>event.preventDefault());option.addEventListener("click",()=>chooseEventCharacter(name));menu.append(option);
    if(name.toLocaleLowerCase("pt-BR")===selected)characterOptionsActiveIndex=index;
  });
  if(characterOptionsActiveIndex>=0)input.setAttribute("aria-activedescendant",`character-option-${characterOptionsActiveIndex}`);
}
function positionEventCharacterOptions(){
  const input=$("#reviewCharacter"),menu=$("#characterOptions"),rect=input.getBoundingClientRect(),width=Math.min(Math.max(rect.width,220),window.innerWidth-16);
  const below=window.innerHeight-rect.bottom-8,above=rect.top-8,placeBelow=below>=Math.min(180,above)||below>=above;
  const maxHeight=Math.max(90,Math.min(300,placeBelow?below:above));
  menu.style.width=`${width}px`;menu.style.maxHeight=`${maxHeight}px`;menu.dataset.placement=placeBelow?"below":"above";
  menu.style.left=`${Math.max(8,Math.min(rect.left,window.innerWidth-width-8))}px`;
  if(placeBelow){menu.style.top=`${rect.bottom+4}px`;menu.style.bottom="auto";}
  else{menu.style.top="auto";menu.style.bottom=`${window.innerHeight-rect.top+4}px`;}
}
function openEventCharacterOptions(filter=false){
  const input=$("#reviewCharacter"),toggle=$("#reviewCharacterToggle"),menu=$("#characterOptions");
  renderEventCharacterOptions(filter);if(!characterOptionsOpen())menu.showPopover();positionEventCharacterOptions();
  input.setAttribute("aria-expanded","true");toggle.setAttribute("aria-expanded","true");
}
function chooseEventCharacter(name){const input=$("#reviewCharacter");input.value=name;input.dispatchEvent(new Event("change",{bubbles:true}));updateReviewCharacterPhoto();closeEventCharacterOptions();input.focus();}
async function showEventCharacterOptions(filter=false){
  const toggle=$("#reviewCharacterToggle"),selectedId=selectedEventId;toggle.setAttribute("aria-busy","true");
  await loadEventCharacterOptions();
  toggle.removeAttribute("aria-busy");
  if(selectedId!==selectedEventId)return;
  openEventCharacterOptions(filter);$("#reviewCharacter").focus();
}
function moveEventCharacterOption(direction){
  const options=$$("#characterOptions .character-option");if(!options.length)return;
  characterOptionsActiveIndex=characterOptionsActiveIndex<0?(direction>0?0:options.length-1):(characterOptionsActiveIndex+direction+options.length)%options.length;
  options.forEach((option,index)=>option.dataset.active=String(index===characterOptionsActiveIndex));
  const active=options[characterOptionsActiveIndex];$("#reviewCharacter").setAttribute("aria-activedescendant",active.id);active.scrollIntoView({block:"nearest"});
}
$("#reviewCharacter").addEventListener("focus",()=>{void loadEventCharacterOptions();});
$("#reviewCharacter").addEventListener("input",()=>{updateReviewCharacterPhoto();if(characterOptionsOpen())renderEventCharacterOptions(true);else void showEventCharacterOptions(true);});
$("#reviewCharacter").addEventListener("keydown",(event)=>{
  if(event.key==="ArrowDown"||event.key==="ArrowUp"){
    event.preventDefault();if(!characterOptionsOpen())void showEventCharacterOptions(false);else moveEventCharacterOption(event.key==="ArrowDown"?1:-1);return;
  }
  if(event.key==="Enter"&&characterOptionsOpen()){
    event.preventDefault();if(characterOptionsActiveIndex>=0&&visibleCharacterOptions[characterOptionsActiveIndex])chooseEventCharacter(visibleCharacterOptions[characterOptionsActiveIndex]);else closeEventCharacterOptions();return;
  }
  if(event.key==="Escape"||event.key==="Tab")closeEventCharacterOptions();
});
$("#reviewCharacterToggle").addEventListener("pointerdown",(event)=>event.preventDefault());
$("#reviewCharacterToggle").addEventListener("click",()=>{if(characterOptionsOpen())closeEventCharacterOptions();else void showEventCharacterOptions(false);});
document.addEventListener("pointerdown",(event)=>{if(!event.target.closest("#reviewCharacterCombobox")&&!event.target.closest("#characterOptions"))closeEventCharacterOptions();});
window.addEventListener("resize",()=>{if(characterOptionsOpen())positionEventCharacterOptions();});
window.addEventListener("scroll",(event)=>{if(event.target===document||event.target===document.documentElement||event.target===document.body)closeEventCharacterOptions();else if(!event.target.closest?.("#characterOptions")&&characterOptionsOpen())positionEventCharacterOptions();},true);
async function confirmSplit(){const dialog=$("#splitDialog"),event=events.find((item)=>item.event_id===dialog.dataset.event),inputTime=Number($("#splitTimeInput").value),previewTime=Number(dialog.dataset.previewRequested)===inputTime?Number(dialog.dataset.time):inputTime;if(!event||!Number.isFinite(previewTime)||previewTime<=Number(event.start_time)||previewTime>=Number(event.end_time))return toast("O ponto de divisão deve ficar dentro do diálogo selecionado.",true);const button=$("#confirmSplit");button.disabled=true;button.textContent="Dividindo…";try{await api(`/api/events/${encodeURIComponent(dialog.dataset.event)}/split`,{method:"POST",body:JSON.stringify({split_time:previewTime})});dialog.close();toast(`Diálogo dividido em ${timecode(previewTime,true)}; imagens e OCR originais preservados.`);await refreshEvents();}catch(error){toast(error.message,true);}finally{button.disabled=false;button.textContent="Confirmar divisão";}}
async function health(){try{const result=await api("/api/health");persistentFilePauseAvailable=result.persistent_file_pause===true;updatePlaybackButton();const ready=Boolean(result.ffmpeg&&result.ffprobe&&result.node&&result.downloader);$("#health").textContent=ready?"yt-dlp · Node · FFmpeg · visual":!result.downloader?"yt-dlp ausente":!result.node?"Node.js ausente":"FFmpeg/ffprobe ausente";$("#health").classList.toggle("good",ready);}catch(error){$("#health").textContent=error.message;}}

$("#blockAutomationMode").value=blockAutomationMode;$("#blockAutomationMode").addEventListener("change",(event)=>{blockAutomationMode=event.target.value==="automatic"?"automatic":"manual";localStorage.setItem(BLOCK_AUTOMATION_STORAGE_KEY,blockAutomationMode);if(blockAutomationMode==="automatic")void maybeAutoStartNextBlock();});$("#nextBlockMinutes").addEventListener("input",()=>{updateNextBlockControl();renderVideoOverview();});$("#sourceStart").addEventListener("input",()=>{updateInitialRangeSummary();renderVideoOverview();});$("#sourceDuration").addEventListener("input",()=>{updateInitialRangeSummary();renderVideoOverview();});
$("#followDialogue").setAttribute("aria-pressed",String(followDialogue));$("#followDialogue").classList.toggle("active",followDialogue);$("#followDialogue").addEventListener("click",()=>{followDialogue=!followDialogue;localStorage.setItem("dubsync.followDialogue",String(followDialogue));$("#followDialogue").setAttribute("aria-pressed",String(followDialogue));$("#followDialogue").classList.toggle("active",followDialogue);$("#followDialogue").title=followDialogue?"Seleção automática ativa: seleciona quando o cursor cruza o ponto circular de início do diálogo. Clique para desativar.":"Ativar seleção automática ao cruzar o ponto circular de início do diálogo na timeline.";if(followDialogue)updateTimelinePlayback($("#videoPlayer").currentTime);});
$$(".source-tab[data-mode]").forEach((tab)=>tab.addEventListener("click",()=>setMode(tab.dataset.mode)));
$("#selectCaptureButton").addEventListener("click",()=>{if(!preview||!["desktop","youtube"].includes(previewKind))return toast("Clique em Play para abrir a prévia do vídeo.",true);if(selectedDesktopRect){selectedDesktopRect=null;previewMode="capture";$("#captureStatusBadge").textContent="SELECIONE O RECORTE";drawPreview();}$("#selectCaptureButton").classList.add("active");$("#addRoiButton").classList.remove("active");});
$("#addRoiButton").addEventListener("click",()=>{if(!preview)return toast("Prepare uma prévia primeiro.",true);if(["desktop","youtube"].includes(previewKind)&&!selectedDesktopRect)return toast("Selecione primeiro a área do vídeo.",true);previewMode=previewMode==="new-roi"?"roi":"new-roi";$("#selectCaptureButton").classList.remove("active");$("#addRoiButton").classList.toggle("active",previewMode==="new-roi");$("#addRoiButton").textContent=previewMode==="new-roi"?"✓ Concluir novas ROIs":"⌖ Adicionar região de diálogo (ROI)";toast(previewMode==="new-roi"?"Arraste para desenhar uma nova ROI.":"Arraste uma ROI para mover; use o canto para redimensionar.");});
$("#playButton").addEventListener("click",()=>{if(!sessionHydrated||preparePending)return;if(persistentFilePauseAvailable&&currentSession?.source_type==="file"&&currentSession.state==="paused")return openFileResumeDialog();if(persistentFilePauseAvailable&&currentSession?.source_type==="file"&&currentSession.state==="running")return pauseFileAnalysis();if(currentSession?.state==="paused")return sessionAction("resume");if(currentSession?.state==="preparing"&&currentSession.config_json?.downloaded_path){if(!currentSession.ready_for_calibration)return toast("Aguardando o vídeo terminar de preparar.");if(currentSession.config_json?.analysis_started)return toast("A análise deste vídeo já foi iniciada.");if(!preview)return toast("Aguarde a prévia local e ajuste as ROIs.",true);return beginYoutube();}if(mode==="file"){if(activeState())return toast("Encerre ou aguarde a sessão atual antes de analisar outro arquivo.",true);return beginFile();}if(activeState())return toast("Aguarde ou encerre a sessão atual antes de iniciar outro download.",true);if(activeSessionId){activeSessionId="";currentSession=null;selectedDesktopRect=null;preview=null;previewKind="";$("#previewEmpty").classList.remove("hidden");clearInterval(pollTimer);clearInterval(livePreviewTimer);}return prepareYoutube();});$("#playButton").addEventListener("click",(event)=>{const config=currentSession?.config_json||{};if(currentSession?.source_type==="youtube"&&["failed","stopped"].includes(currentSession.state)&&config.mode==="youtube_download"&&!config.analysis_started){event.preventDefault();event.stopImmediatePropagation();sessionAction("resume");}},true);$("#fileResumeCancel").addEventListener("click",()=>$("#fileResumeDialog").close());$("#fileResumeContinue").addEventListener("click",()=>chooseFileResume(false));$("#fileResumeRestart").addEventListener("click",()=>chooseFileResume(true));$("#pauseButton").addEventListener("click",()=>sessionAction("pause"));$("#stopButton").addEventListener("click",()=>sessionAction("stop"));$("#previewToggle").addEventListener("click",togglePreviewPlayback);$("#seekSlider").addEventListener("input",()=>seekVideoTo(Number($("#seekSlider").value)));$("#nextSegmentButton").addEventListener("click",beginNextBlock);$("#previousBlockButton").addEventListener("click",goToPreviousBlock);
$("#sessionFilter").addEventListener("change",()=>{timelinePinSelectedIds.clear();refreshEvents();});$("#timelineTrack").addEventListener("click",(event)=>{if(ignoreTimelineClick||event.target.closest(".timeline-event,.timeline-dialogue-point,.timeline-audio-marker,.timeline-missing-point"))return;const rect=event.currentTarget.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width));seekVideoTo(timelineRange.start+ratio*timelineRange.span);});initializeCaptureTimelinePins();$("#timelinePreviousPin").addEventListener("click",()=>navigateDialogue(-1));$("#timelineNextPin").addEventListener("click",()=>navigateDialogue(1));$("#timelineMovePinLeft").addEventListener("click",()=>void moveSelectedCapturePins(-captureTimelineMoveStep()));$("#timelineMovePinRight").addEventListener("click",()=>void moveSelectedCapturePins(captureTimelineMoveStep()));$("#timelineSelectAllPins").addEventListener("click",()=>{const items=captureTimelinePinItems(),all=items.length>0&&items.every((item)=>timelinePinSelectedIds.has(item.event_id));setCaptureTimelinePinSelection(all?[]:items.map((item)=>item.event_id),all?"":items.at(-1)?.event_id||"");});$("#timelineMoveStep").addEventListener("change",(event)=>{if(!Number.isFinite(Number(event.target.value))||Number(event.target.value)<=0)event.target.value="1";});$("#openVideoFile").addEventListener("click",chooseVideoFile);$("#statusFilter").addEventListener("change",refreshEvents);$("#characterFilter").addEventListener("change",refreshEvents);$("#emotionFilter").addEventListener("change",refreshEvents);$("#minConfidence").addEventListener("change",refreshEvents);$("#selectAllEvents").addEventListener("change",(event)=>{$$(".merge-check").forEach((item)=>{item.checked=event.currentTarget.checked;});updateMergeToolbar();});$("#bulkAcceptButton").addEventListener("click",bulkAcceptSelected);$("#mergeButton").addEventListener("click",()=>mergeIds($$(".merge-check:checked").map((item)=>item.dataset.id)));$("#bulkRemoveButton").addEventListener("click",bulkRemoveSelected);$("#acceptButton").addEventListener("click",()=>saveReview("accept"));$("#discardButton").addEventListener("click",discardSelectedEvent);$("#correctButton").addEventListener("click",()=>saveReview("correct"));$("#adjustButton").addEventListener("click",()=>saveReview("update"));$("#mergePrevious").addEventListener("click",()=>mergeAdjacent(-1));$("#mergeNext").addEventListener("click",()=>mergeAdjacent(1));$("#splitButton").addEventListener("click",openSplit);$("#replaceButton").addEventListener("click",openReplaceDialog);$("#previewSplit").addEventListener("click",previewSplit);$("#splitTimeInput").addEventListener("input",()=>{const dialog=$("#splitDialog"),value=Number($("#splitTimeInput").value);if(Number(dialog.dataset.previewRequested)!==value){dialog.dataset.time="";$("#splitPreviewImage").removeAttribute("src");$("#splitTimeLabel").textContent="Ponto alterado. Pré-visualize o frame antes de confirmar.";if(Number.isFinite(value))$("#splitReason").textContent=`Ponto de divisão manual: ${timecode(value,true)}.`;}});$("#cancelSplit").addEventListener("click",()=>$("#splitDialog").close());$("#cancelReplace").addEventListener("click",()=>$("#replaceDialog").close());$("#addReplaceRule").addEventListener("click",addReplaceRule);$("#saveReplaceRules").addEventListener("click",saveReplaceRules);$("#confirmSplit").addEventListener("click",confirmSplit);
$("#splitTimeInput").addEventListener("input",()=>{const dialog=$("#splitDialog"),value=Number($("#splitTimeInput").value),event=events.find((item)=>item.event_id===dialog.dataset.event),valid=Boolean(event&&Number.isFinite(value)&&value>Number(event.start_time)&&value<Number(event.end_time)),withoutPreview=dialog.classList.contains("no-preview");if(Number(dialog.dataset.previewRequested)===value)return;dialog._splitPreviewController?.abort();dialog.dataset.previewRequestId=String((Number(dialog.dataset.previewRequestId)||0)+1);dialog.dataset.time="";$("#splitPreviewImage").hidden=true;$("#splitPreviewImage").removeAttribute("src");$("#splitPreviewPlaceholder").hidden=false;$("#splitPreviewPlaceholder").classList.remove("loading");$("#splitPlaceholderText").textContent="Ponto ajustado. Pré-visualize o novo frame antes de confirmar.";$("#previewSplit").disabled=false;$("#confirmSplit").disabled=!valid;$("#splitTimeLabel").textContent=withoutPreview?"Prévia desativada. Confirme para dividir no tempo escolhido.":"A prévia precisa ser atualizada para este ponto.";updateSplitTimestampHint(value);});
$("#settingsDialog").addEventListener("close",()=>{recordingShortcutAction="";});
document.addEventListener("keydown",handleGlobalShortcut);
$("#timelineScroll").addEventListener("wheel",(event)=>{if(!event.ctrlKey)return;event.preventDefault();const rect=$("#timelineTrack").getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-rect.left)/Math.max(1,rect.width)));zoomTimeline(event.deltaY<0?.8:1.25,ratio);},{passive:false});
$("#timelineZoomIn").addEventListener("click",()=>zoomTimeline(.8));$("#timelineZoomOut").addEventListener("click",()=>zoomTimeline(1.25));$("#timelineZoomFit").addEventListener("click",fitTimeline);
$("#addManualDialogue").addEventListener("click",openManualEventDialog);$("#cancelManualEvent").addEventListener("click",()=>$("#manualEventDialog").close());$("#saveManualEvent").addEventListener("click",saveManualDialogue);
$("#duplicateDialogue").addEventListener("click",duplicateSelectedDialogue);$("#retryDialogueOcr").addEventListener("click",retrySelectedDialogueOcr);$("#ocrRetryKeep").addEventListener("click",()=>$("#ocrRetryDialog").close());$("#ocrRetryApply").addEventListener("click",applyOcrRetry);
$("#blockCompleteNext").addEventListener("click",()=>{$("#blockCompleteDialog").close();beginNextBlock();});$("#blockCompleteSweep").addEventListener("click",startFineSweep);$("#blockCompleteLater").addEventListener("click",()=>$("#blockCompleteDialog").close());
$("#fineSweepStartButton").addEventListener("click",startFineSweep);
[["main","#shortcutStepMain"],["fine","#shortcutStepFine"],["fast","#shortcutStepFast"]].forEach(([name,selector])=>{$(selector).addEventListener("input",(event)=>setShortcutStep(name,event.target.value));$(selector).addEventListener("change",()=>renderShortcutSettings());});
renderShortcutSettings();updateInitialRangeUi(null);renderVideoOverview();updateAnalysisActivity(null);
$$('.detail-tab').forEach((tab)=>tab.addEventListener("click",()=>{$$('.detail-tab').forEach((item)=>item.classList.toggle("active",item===tab));$$('.detail-content').forEach((item)=>item.classList.toggle("active",item.id===`detail${tab.dataset.detailTab[0].toUpperCase()}${tab.dataset.detailTab.slice(1)}`));}));$$(".nav-item").forEach((tab)=>tab.addEventListener("click",()=>{if(window.DUBSYNC_PROJECT_ID&&tab.dataset.projectArea){if(tab.dataset.projectArea==="settings")window.DUBSYNC_SETTINGS_RETURN_URL=location.pathname.endsWith("/settings")?`/projects/${encodeURIComponent(window.DUBSYNC_PROJECT_ID)}/capture`:location.pathname;history.replaceState(null,"",`/projects/${encodeURIComponent(window.DUBSYNC_PROJECT_ID)}/${tab.dataset.projectArea}`);}if(tab.hasAttribute("data-settings-open"))return;$$(".nav-item").forEach((item)=>item.classList.toggle("active",item===tab));const inDubbing=tab.dataset.view==="dubbing";$("#appGrid").classList.toggle("hidden",inDubbing);$("#dubbingView").classList.toggle("hidden",!inDubbing);document.body.classList.toggle("dubbing-active",inDubbing);if(inDubbing){window.DubSyncDubbing?.open();return;}const target=$("#"+tab.dataset.anchor);if(target)target.scrollIntoView({behavior:"smooth",block:"nearest"});}));$("#segmentDuration").addEventListener("change",()=>$("#segmentDurationLabel").textContent=`${Number($("#segmentDuration").value)||60} segundos`);$("#youtubeUrl").addEventListener("change",()=>loadCoverage($("#youtubeUrl").value,true));$("#fitPreview").addEventListener("click",()=>drawPreview());
function restoreSessionSettings(session){
  const config=session.config_json||{},next=config.downloaded_path||session.source_type==="file"?"file":"youtube",start=Number(config.start??session.source_start??0),duration=Number(config.duration??session.requested_duration??900);
  setMode(next,Boolean(config.downloaded_path));$("#youtubeUrl").value=config.url||session.source_uri||"";$("#filePath").value=config.downloaded_path||config.path||"";
  $("#sourceStart").value=timecode(start);$("#sourceDuration").value=timecode(start+duration);$("#language").value=session.language||"en";
  $("#segmentDuration").value=String(config.segment_duration_seconds||60);$("#nextBlockMinutes").value=String(Math.max(1,Number(config.block_duration_requested||1200)/60));
  showDownloadedOrigin(session);const savedVisibility=readRoiVisibility();regions=(config.regions?.length?config.regions:DEFAULT_REGIONS).map((region,index)=>{const roiId=region.roi_id||crypto.randomUUID();return{...region,roi_id:roiId,color:region.color||["#e43c46","#24a4ee","#dd9d2e","#b26be5"][index%4],enabled:region.enabled!==false,visible:Object.prototype.hasOwnProperty.call(savedVisibility,roiId)?savedVisibility[roiId]!==false:region.visible!==false};});
  updateRoiList();updateNextBlockControl();updateInitialRangeUi(session);
}
async function attachActiveSession(session,notify=false){
  activeSessionId=session.session_id;currentSession=session;selectedDesktopRect=null;preview=null;previewKind="";
  if(fineSweepSessionId!==session.session_id){fineSweepRun=null;fineSweepSessionId="";renderFineSweepStatus();clearInterval(fineSweepPollTimer);fineSweepPollTimer=0;}
  clearInterval(pollTimer);clearInterval(livePreviewTimer);
  $("#previewEmpty").classList.remove("hidden");$("#sessionFilter").value=session.session_id;
  restoreSessionSettings(session);await refreshSession();
  pollTimer=setInterval(refreshSession,1400);
  if(session.source_type==="youtube"&&session.config_json?.downloaded_path)await refreshSession();
  await refreshEvents();await refreshFineSweep(session.session_id);
  if(notify)toast("Sessão ativa conectada de outra aba.");
}
window.openDubbingCharacterRoi = async (roiId, characterId) => {
  const captureTab=document.querySelector('.nav-item[data-project-area="capture"]');
  if(!captureTab)return false;
  if(document.body.classList.contains("dubbing-active"))captureTab.click();
  const openCurrentRoi=async()=>Boolean(await window.openRoiCharacterSettings?.(roiId,characterId));
  if(regions.some((region)=>String(region.roi_id)===String(roiId)))return openCurrentRoi();
  await refreshSessions();
  const owner=sessions.find((session)=>(session.config_json?.regions||[]).some((region)=>String(region.roi_id)===String(roiId)));
  if(!owner)return false;
  if(owner.session_id!==activeSessionId)await attachActiveSession(owner);
  return openCurrentRoi();
};
async function syncActiveSession(){
  if(!sessionHydrated||preparePending)return;
  try{
    await refreshSessions();
    const session=sessions.find((item)=>activeState(item.state));
    if(!session||(activeState()&&session.session_id===activeSessionId))return;
    if(activeState())return;
    await attachActiveSession(session,true);
  }catch(error){console.warn("Não foi possível sincronizar as sessões abertas:",error);}
}
async function restoreActiveSession(){
  await refreshSessions();
  const active=sessions.find((item)=>activeState(item.state));
  if(active)await attachActiveSession(active);
  else if(sessions.length){
    const latest=sessions[0];activeSessionId=latest.session_id;currentSession=latest;
    $("#sessionFilter").value=latest.session_id;restoreSessionSettings(latest);await refreshSession();
  }
  sessionHydrated=true;updatePlaybackButton();await refreshEvents();
  if(!sessionWatchTimer)sessionWatchTimer=setInterval(syncActiveSession,1100);
}
initializeTimelineNavigator();initializeSplitters();updateRoiList();const captureLanguage=String(window.DUBSYNC_PROJECT?.capture_language||window.DUBSYNC_PROJECT?.source_language||"").split("-")[0].toLowerCase();if(captureLanguage&&[...$("#language").options].some(option=>option.value===captureLanguage))$("#language").value=captureLanguage;try{const source=JSON.parse(localStorage.getItem(`dubsync.projectSource.${window.DUBSYNC_PROJECT_ID}`)||"null");if(source?.mode){document.querySelector(`.source-tab[data-mode="${source.mode}"]`)?.click();if(source.mode==="youtube"){$("#youtubeUrl").value=source.value||"";if(source.value)loadCoverage(source.value,true);}if(source.mode==="file")$("#filePath").value=source.value||"";}}catch{}health();api("/api/settings").then((value)=>{showSplitPreview=value.review?.show_split_preview!==false;}).catch((error)=>console.warn("Não foi possível ler a preferência de prévia:",error));restoreActiveSession().catch((error)=>{sessionHydrated=true;updatePlaybackButton();toast("Não foi possível restaurar a sessão: "+error.message,true);});updatePipeline(null);if(window.DUBSYNC_INITIAL_AREA&&window.DUBSYNC_INITIAL_AREA!=="capture")window.addEventListener("load",()=>document.querySelector(`.nav-item[data-project-area="${window.DUBSYNC_INITIAL_AREA}"]`)?.click(),{once:true});
