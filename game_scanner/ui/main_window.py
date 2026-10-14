from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from game_scanner import __version__
from game_scanner.core.models import DiagnosticReport, Evidence, LayerFinding
from game_scanner.core.scanner import run_scan
from game_scanner.reports.exporter import export_json
from game_scanner.core.inventory import resolve_target
from game_scanner.runtime.observer import RuntimeObserver


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DATA = PROJECT_ROOT / ".data" / "game_scanner"
REPORTS_DIR = APP_DATA / "reports"
HISTORY_FILE = APP_DATA / "history.json"

BG = "#061321"
PANEL = "#0b1b2d"
PANEL_ALT = "#0e2238"
EDGE = "#173657"
TEXT = "#edf4ff"
MUTED = "#9bb4d5"
BLUE = "#18a8ff"
CYAN = "#34c9ff"
GREEN = "#19d69a"
YELLOW = "#ffc84a"
PURPLE = "#a66bff"
RED = "#ff6e83"

LAYER_META = [
    ("engine", "Engine", "♧"),
    ("build_runtime", "Build / Runtime", "⬡"),
    ("dialogue", "Diálogo / Narrativa", "▤"),
    ("localization", "Legendas / Localização", "▣"),
    ("audio", "Áudio", "◖"),
    ("runtime_events", "Eventos / Runtime", "⚙"),
    ("modding", "Modding / Injeção", "✣"),
    ("visual_fallback", "Fallback Visual", "⑂"),
]


def _report_from_dict(data: dict) -> DiagnosticReport:
    game = data.get("game", {})
    findings = []
    for raw in data.get("diagnostics", {}).values():
        findings.append(LayerFinding(
            layer=raw.get("layer", "unknown"), title=raw.get("title", "Diagnóstico"),
            status=raw.get("status", "not_confirmed"), confidence=raw.get("confidence", "unknown"),
            confidence_percent=raw.get("confidence_percent"), summary=raw.get("summary", ""),
            evidence=[Evidence(**item) for item in raw.get("evidence", [])],
            details=list(raw.get("details", [])),
            runtime_validation_required=bool(raw.get("runtime_validation_required", False)),
        ))
    return DiagnosticReport(
        game_name=game.get("name", "Jogo"), root_path=game.get("root_path", ""),
        executable_path=game.get("executable_path"),
        executable_file_version=game.get("executable_file_version"),
        executable_product_version=game.get("executable_product_version"),
        scanned_at=data.get("scanned_at", ""), duration_seconds=data.get("duration_seconds"),
        report_id=data.get("report_id", ""), scanner_version=data.get("scanner_version", __version__),
        schema_version=data.get("schema_version", 1), findings=findings,
        recommendation=data.get("recommendation", {}), warnings=list(data.get("warnings", [])),
        inventory_summary=data.get("inventory_summary", {}),
    )


class GameScannerWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DubSync Game Scanner — Diagnóstico técnico para integração")
        self.geometry("1580x900")
        self.minsize(1120, 700)
        self.configure(bg=BG)
        self._setup_theme()
        self.report: DiagnosticReport | None = None
        self.history: list[dict] = []
        self._history_mtime: int | None = None
        self.runtime_observer: RuntimeObserver | None = None
        self.events: queue.Queue = queue.Queue()
        self.path_var = tk.StringVar()
        self.game_var = tk.StringVar(value="Novo diagnóstico")
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Leitura passiva · nenhum arquivo do jogo será alterado")
        self.progress_var = tk.DoubleVar(value=0)
        self._build_layout()
        self._load_history()
        self.after(120, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close_window)

    def _setup_theme(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 17, "bold"))
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 12, "bold"))
        style.configure("TButton", background=PANEL_ALT, foreground=TEXT, bordercolor=EDGE,
                        padding=(12, 8), font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", "#143454"), ("disabled", PANEL)],
                  foreground=[("disabled", "#6d83a0")])
        style.configure("Accent.TButton", background="#075ce5", foreground="white",
                        bordercolor=BLUE, padding=(14, 9), font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#0a78ff"), ("disabled", "#203a58")])
        style.configure("TEntry", fieldbackground="#081a2d", foreground=TEXT,
                        insertcolor=TEXT, bordercolor=EDGE, padding=8)
        style.configure("TCombobox", fieldbackground="#081a2d", foreground=TEXT,
                        background=PANEL_ALT, arrowcolor=CYAN, bordercolor=EDGE, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "#081a2d")],
                  foreground=[("readonly", TEXT)])
        style.configure("Horizontal.TProgressbar", troughcolor="#06101d", background=CYAN,
                        bordercolor=EDGE, lightcolor=CYAN, darkcolor=BLUE)
        style.configure("Treeview", background="#071522", fieldbackground="#071522",
                        foreground=TEXT, rowheight=30, bordercolor=EDGE, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=PANEL_ALT, foreground=MUTED,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#134d78")])

    def _card(self, parent, padding=12, bg=PANEL):
        frame = tk.Frame(parent, bg=bg, highlightbackground=EDGE, highlightthickness=1,
                         bd=0, padx=padding, pady=padding)
        return frame

    def _build_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_toolbar()
        self._build_body()

    def _build_toolbar(self):
        bar = tk.Frame(self, bg="#07111f", height=68, highlightbackground=EDGE, highlightthickness=1)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        brand = tk.Frame(bar, bg="#07111f")
        brand.grid(row=0, column=0, sticky="w", padx=(18, 14), pady=9)
        tk.Label(brand, text="〰", fg=CYAN, bg="#07111f", font=("Segoe UI Symbol", 30, "bold")).grid(row=0, column=0, rowspan=2, padx=(0, 10))
        tk.Label(brand, text="DubSync Game Scanner", fg=TEXT, bg="#07111f",
                 font=("Segoe UI", 15, "bold")).grid(row=0, column=1, sticky="sw")
        tk.Label(brand, text="Diagnóstico técnico para integração", fg=MUTED, bg="#07111f",
                 font=("Segoe UI", 9)).grid(row=1, column=1, sticky="nw")

        controls = tk.Frame(bar, bg="#07111f")
        controls.grid(row=0, column=1, sticky="ew", padx=8, pady=12)
        controls.grid_columnconfigure(1, weight=1)
        ttk.Label(controls, text="Jogo alvo:").grid(row=0, column=0, padx=(0, 8))
        self.game_combo = ttk.Combobox(controls, textvariable=self.game_var, state="readonly", width=20,
                                       values=["Novo diagnóstico"])
        self.game_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.game_combo.bind("<<ComboboxSelected>>", self._select_history_game)
        ttk.Button(controls, text="▱  Selecionar pasta", command=self._browse_folder).grid(row=0, column=2, padx=4)
        ttk.Button(controls, text="▣  Selecionar executável", command=self._browse_executable).grid(row=0, column=3, padx=4)
        self.scan_button = ttk.Button(controls, text="▶  Iniciar diagnóstico", style="Accent.TButton",
                                      command=self._start_scan)
        self.scan_button.grid(row=0, column=4, padx=4)
        self.export_button = ttk.Button(controls, text="⇧  Exportar JSON", command=self._export_json,
                                        state="disabled")
        self.export_button.grid(row=0, column=5, padx=4)
        passive = tk.Frame(bar, bg="#092326", highlightbackground="#10624f", highlightthickness=1, padx=12, pady=6)
        passive.grid(row=0, column=2, sticky="e", padx=15)
        self.observation_mode_label = tk.Label(passive, text="●  Leitura passiva", bg="#092326", fg=GREEN,
                                               font=("Segoe UI", 9, "bold"))
        self.observation_mode_label.pack(anchor="w")
        tk.Label(passive, text="Sem alterações nos arquivos do jogo", bg="#092326", fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")

        path_bar = tk.Frame(self, bg="#09192a", height=36)
        path_bar.grid(row=1, column=0, sticky="ew")
        path_bar.grid_columnconfigure(1, weight=1)
        tk.Label(path_bar, text="Pasta ou executável selecionado", bg="#09192a", fg=MUTED,
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, padx=(18, 8), pady=5)
        self.path_entry = ttk.Entry(path_bar, textvariable=self.path_var)
        self.path_entry.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=4)
        self.observe_button = ttk.Button(path_bar, text="▶  Abrir e observar jogo",
                                         command=self._toggle_observation)
        self.observe_button.grid(row=0, column=2, padx=(0, 6), pady=4)
        self.capture_button = ttk.Button(path_bar, text="▣  Registrar fala",
                                         command=self._capture_observation, state="disabled")
        self.capture_button.grid(row=0, column=3, padx=(0, 14), pady=4)
        self.runtime_label = tk.Label(path_bar, text="Runtime: aguardando observação real.",
                                      bg="#09192a", fg=MUTED, anchor="w", justify="left",
                                      font=("Segoe UI", 9))
        self.runtime_label.grid(row=1, column=0, columnspan=4, sticky="ew", padx=18, pady=(0, 5))

    def _toggle_observation(self):
        observer = self.runtime_observer
        if observer is not None:
            observer.stop()
            self.observe_button.configure(state="disabled")
            self.runtime_label.configure(text="Encerrando observação e salvando o relatório…", fg=MUTED)
            return
        target = self.path_var.get().strip()
        if not target:
            messagebox.showinfo("Game Scanner", "Selecione o executável do jogo primeiro.")
            return
        try:
            _, executable = resolve_target(target)
            if executable is None:
                raise ValueError("Não encontrei um executável único nesta pasta. Use Selecionar executável.")
            observer = RuntimeObserver(executable, APP_DATA / "runtime",
                                       self.report.report_id if self.report else None)
        except (OSError, ValueError) as error:
            messagebox.showerror("Observação indisponível", str(error))
            return
        self.runtime_observer = observer
        self.observe_button.configure(text="■  Parar observação")
        self.capture_button.configure(state="disabled")
        self.runtime_label.configure(text="Procurando o processo do jogo…", fg=CYAN)
        threading.Thread(target=observer.run,
                         args=(lambda kind, payload: self.events.put((f"runtime_{kind}", payload)),),
                         name="DubSync Game Observer", daemon=True).start()

    def _capture_observation(self):
        if self.runtime_observer is not None:
            self.runtime_observer.request_capture()
            self.runtime_label.configure(text="Registro em 3 segundos; volte ao jogo e deixe a fala visível.", fg=CYAN)

    def _close_window(self):
        if self.runtime_observer is not None:
            self.runtime_observer.stop()
            self.runtime_label.configure(text="Finalizando o relatório de observação…", fg=MUTED)
            self.after(150, self._close_window)
            return
        self.destroy()

    def _build_body(self):
        body = tk.Frame(self, bg=BG)
        body.grid(row=2, column=0, sticky="nsew", padx=12, pady=(10, 8))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, minsize=240, weight=0)
        body.grid_columnconfigure(1, minsize=500, weight=1)
        body.grid_columnconfigure(2, minsize=300, weight=0)
        self._build_sidebar(body)
        self._build_center(body)
        self._build_right(body)

    def _build_sidebar(self, body):
        sidebar = self._card(body, 12)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        sidebar.grid_rowconfigure(3, weight=1)
        tk.Label(sidebar, text="◷  Jogos / Scans recentes", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 12))
        search = ttk.Entry(sidebar, textvariable=self.search_var)
        search.insert(0, "")
        search.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        search.bind("<KeyRelease>", lambda _event: self._render_history())
        self.history_frame = tk.Frame(sidebar, bg=PANEL)
        self.history_frame.grid(row=2, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(2, weight=1)
        ttk.Button(sidebar, text="＋  Novo diagnóstico", command=self._new_scan).grid(row=4, column=0, sticky="ew", pady=(12, 0))

    def _build_center(self, body):
        center = tk.Frame(body, bg=BG)
        center.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(3, weight=1)

        self.hero = self._card(center, 14, bg="#0a1d31")
        self.hero.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        self.hero.grid_columnconfigure(1, weight=1)
        tk.Label(self.hero, text="▰", fg=BLUE, bg="#0a1d31", font=("Segoe UI Symbol", 42, "bold")).grid(row=0, column=0, rowspan=2, padx=(2, 18))
        self.hero_name = tk.Label(self.hero, text="Selecione uma pasta ou executável do jogo", fg=TEXT,
                                  bg="#0a1d31", font=("Segoe UI", 20, "bold"), anchor="w")
        self.hero_name.grid(row=0, column=1, sticky="w")
        self.hero_path = tk.Label(self.hero, text="O scanner fará somente leitura dos arquivos selecionados.",
                                  fg=MUTED, bg="#0a1d31", font=("Segoe UI", 10), anchor="w")
        self.hero_path.grid(row=1, column=1, sticky="w", pady=(3, 0))
        self.hero_meta = tk.Label(self.hero, text="Engine: —     Build / Runtime: —     Scan: —",
                                  fg="#bfd3ed", bg="#0a1d31", font=("Segoe UI", 9),
                                  justify="right", wraplength=280)
        self.hero_meta.grid(row=0, column=2, rowspan=2, padx=(18, 6), sticky="e")

        summary = self._card(center, 13)
        summary.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        summary.grid_columnconfigure(0, weight=1)
        tk.Label(summary, text="▤  Resumo do diagnóstico", fg=TEXT, bg=PANEL,
                 font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        self.summary_count = tk.Label(summary, text="Aguardando diagnóstico", fg=CYAN, bg=PANEL,
                                      font=("Segoe UI", 10, "bold"))
        self.summary_count.grid(row=0, column=1, sticky="e")
        self.progressbar = ttk.Progressbar(summary, variable=self.progress_var, maximum=10,
                                            mode="determinate", length=320)
        self.progressbar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        self.status_label = tk.Label(summary, textvariable=self.status_var, fg=MUTED, bg=PANEL,
                                     font=("Segoe UI", 9), anchor="w")
        self.status_label.grid(row=2, column=0, columnspan=2, sticky="ew")

        layer_header = tk.Frame(center, bg=BG)
        layer_header.grid(row=2, column=0, sticky="ew", pady=(1, 6))
        tk.Label(layer_header, text="▱  Camadas de diagnóstico", fg=TEXT, bg=BG,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Label(layer_header, text="Análise estática · 8 aspectos", fg=MUTED, bg=BG,
                 font=("Segoe UI", 9)).pack(side="left", padx=12)
        self.layers_frame = tk.Frame(center, bg=BG)
        self.layers_frame.grid(row=3, column=0, sticky="nsew")
        self.layers_frame.grid_columnconfigure(0, weight=1, uniform="layers")
        self.layers_frame.grid_columnconfigure(1, weight=1, uniform="layers")
        for i, (key, title, icon) in enumerate(LAYER_META):
            card = self._layer_card(self.layers_frame, i + 1, key, title, icon)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0 if i % 2 == 0 else 5, 5 if i % 2 == 0 else 0),
                      pady=4)
            self.layers_frame.grid_rowconfigure(i // 2, weight=1, uniform="layerrows")

    def _layer_card(self, parent, number, key, title, icon):
        card = tk.Frame(parent, bg=PANEL, highlightbackground=EDGE, highlightthickness=1,
                        padx=10, pady=8, cursor="hand2")
        card.grid_columnconfigure(1, weight=1)
        tk.Label(card, text=f"{number}", bg="#0c3760", fg=CYAN, font=("Segoe UI", 10, "bold"),
                 width=3, height=1).grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 9))
        tk.Label(card, text=icon, bg=PANEL, fg=CYAN, font=("Segoe UI Symbol", 19, "bold")).grid(row=0, column=1, sticky="w")
        title_label = tk.Label(card, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold"), anchor="w")
        title_label.grid(row=0, column=2, sticky="w")
        state = tk.Label(card, text="Aguardando", bg="#16283b", fg=MUTED,
                         font=("Segoe UI", 8, "bold"), padx=7, pady=3)
        state.grid(row=0, column=3, sticky="e", padx=(4, 0))
        summary = tk.Label(card, text="A camada será analisada após iniciar o diagnóstico.",
                           bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=340)
        summary.grid(row=1, column=2, columnspan=2, sticky="ew", pady=(5, 0))
        percent = tk.Label(card, text="—", bg=PANEL, fg=CYAN, font=("Segoe UI", 9, "bold"))
        percent.grid(row=0, column=4, rowspan=2, sticky="e", padx=(8, 2))
        card._scanner_labels = (state, summary, percent)
        card.bind("<Button-1>", lambda _event, layer=key: self._show_layer_details(layer))
        for child in card.winfo_children():
            child.bind("<Button-1>", lambda _event, layer=key: self._show_layer_details(layer))
        return card

    def _build_right(self, body):
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=2, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        evidence_card = self._card(right, 10)
        evidence_card.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        evidence_card.grid_columnconfigure(0, weight=1)
        evidence_card.grid_rowconfigure(1, weight=1)
        tk.Label(evidence_card, text="▤  Evidências", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.evidence_tree = ttk.Treeview(evidence_card, columns=("kind", "path"), show="headings", height=9)
        self.evidence_tree.heading("kind", text="Tipo")
        self.evidence_tree.heading("path", text="Arquivo / evidência")
        self.evidence_tree.column("kind", width=90, stretch=False)
        self.evidence_tree.column("path", width=240, stretch=True)
        self.evidence_tree.grid(row=1, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(evidence_card, orient="vertical", command=self.evidence_tree.yview)
        tree_scroll.grid(row=1, column=1, sticky="ns")
        self.evidence_tree.configure(yscrollcommand=tree_scroll.set)
        self.evidence_tree.bind("<Double-1>", self._show_selected_evidence)

        confidence = self._card(right, 10)
        confidence.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        tk.Label(confidence, text="▥  Confiança", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.confidence_label = tk.Label(confidence, text="Alta 0   ·   Média 0   ·   Baixa 0   ·   Não confirmada 8",
                                         bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=300, justify="left")
        self.confidence_label.pack(anchor="w", pady=(8, 0))

        recommendation = self._card(right, 11)
        recommendation.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        tk.Label(recommendation, text="💡  Recomendação de integração", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.recommendation_title = tk.Label(recommendation, text="Aguardando evidências", bg=PANEL,
                                             fg=CYAN, font=("Segoe UI", 10, "bold"), wraplength=300, justify="left")
        self.recommendation_title.pack(anchor="w", pady=(8, 3))
        self.recommendation_text = tk.Label(recommendation, text="A recomendação será baseada somente nos sinais encontrados.",
                                            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=300, justify="left")
        self.recommendation_text.pack(anchor="w")

        next_step = self._card(right, 10, bg="#151332")
        next_step.grid(row=3, column=0, sticky="ew")
        tk.Label(next_step, text="⚑  Próximo passo", bg="#151332", fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.next_step_label = tk.Label(next_step, text="Selecione o jogo e execute as oito camadas.",
                                        bg="#151332", fg="#cbb9ff", font=("Segoe UI", 9), wraplength=300,
                                        justify="left")
        self.next_step_label.pack(anchor="w", pady=(7, 0))

    def _browse_folder(self):
        chosen = filedialog.askdirectory(title="Selecione a pasta de instalação do jogo")
        if chosen:
            self.path_var.set(chosen)
            self.game_var.set("Novo diagnóstico")

    def _browse_executable(self):
        chosen = filedialog.askopenfilename(title="Selecione o executável do jogo",
                                            filetypes=[("Executáveis", "*.exe"), ("Todos os arquivos", "*.*")])
        if chosen:
            self.path_var.set(chosen)
            self.game_var.set("Novo diagnóstico")

    def _new_scan(self):
        self.report = None
        self.game_var.set("Novo diagnóstico")
        self.path_var.set("")
        self.hero_name.configure(text="Selecione uma pasta ou executável do jogo")
        self.hero_path.configure(text="O scanner fará somente leitura dos arquivos selecionados.")
        self.hero_meta.configure(text="Engine: —     Build / Runtime: —     Scan: —")
        self.summary_count.configure(text="Aguardando diagnóstico")
        self.status_var.set("Leitura passiva · nenhum arquivo do jogo será alterado")
        self.progress_var.set(0)
        self.export_button.configure(state="disabled")
        self._render_report(None)

    def _start_scan(self):
        if self.report is not None or not self.path_var.get().strip():
            if not self.path_var.get().strip():
                messagebox.showinfo("Game Scanner", "Selecione a pasta do jogo ou seu executável primeiro.")
                return
        self.scan_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.progress_var.set(0)
        self.summary_count.configure(text="Diagnóstico em andamento")
        self.status_var.set("Abrindo arquivos em modo somente leitura…")
        target = self.path_var.get().strip()

        def worker():
            try:
                report = run_scan(target, progress=lambda value, message: self.events.put(("progress", value, message)))
                self.events.put(("complete", report))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, name="DubSync Game Scanner", daemon=True).start()

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self.progress_var.set(max(0, min(10, float(event[1]))))
                    self.status_var.set(event[2])
                elif event[0] == "complete":
                    self._scan_complete(event[1])
                elif event[0] == "error":
                    self.scan_button.configure(state="normal")
                    self.summary_count.configure(text="Diagnóstico interrompido")
                    self.status_var.set(event[1])
                    messagebox.showerror("Não foi possível diagnosticar", event[1])
                elif event[0] == "runtime_status":
                    self.runtime_label.configure(text=str(event[1]), fg=CYAN)
                elif event[0] == "runtime_ready":
                    info = event[1]
                    mode = "conectado ao jogo aberto" if info["mode"] == "attached" else "jogo iniciado"
                    self.runtime_label.configure(text=f"Runtime: {mode} · PID {info['pid']} · OCR visual a cada 5 s com a janela em primeiro plano.", fg=GREEN)
                    self.capture_button.configure(state="normal")
                    self.observation_mode_label.configure(text="●  Observação ativa", fg=YELLOW)
                elif event[0] == "runtime_window":
                    window = event[1]
                    if not window.get("foreground"):
                        self.runtime_label.configure(text="Runtime: jogo detectado; traga a janela para frente para registrar texto visual.", fg=YELLOW)
                elif event[0] == "runtime_observation":
                    item = event[1]
                    text = item.get("ocr_text_candidate") or "frame salvo sem texto reconhecido"
                    self.runtime_label.configure(text=f"OCR candidato: {text[:125]}", fg=CYAN)
                    self.evidence_tree.insert("", "end", values=("OCR visual", text[:90]),
                                              tags=("runtime", item.get("frame_path", "")))
                elif event[0] == "runtime_error":
                    self.runtime_label.configure(text=f"Observação interrompida: {event[1]}", fg=RED)
                elif event[0] == "runtime_end":
                    self.runtime_observer = None
                    self.observe_button.configure(text="▶  Abrir e observar jogo", state="normal")
                    self.capture_button.configure(state="disabled")
                    self.observation_mode_label.configure(text="●  Leitura passiva", fg=GREEN)
                    self.runtime_label.configure(text=f"Relatório runtime salvo em {event[1]}", fg=GREEN)
        except queue.Empty:
            pass
        self._refresh_history_if_changed()
        self.after(120, self._poll_events)

    def _scan_complete(self, report: DiagnosticReport):
        self.report = report
        self.scan_button.configure(state="normal")
        self.export_button.configure(state="normal")
        self.progress_var.set(10)
        self.path_var.set(report.executable_path or report.root_path)
        self.hero_name.configure(text=report.game_name.upper())
        self.hero_path.configure(text=report.executable_path or report.root_path)
        engine = next((item.summary for item in report.findings if item.layer == "engine"), "—")
        runtime = next((item.summary for item in report.findings if item.layer == "build_runtime"), "—")
        file_version = report.executable_file_version or "não encontrada"
        product_version = report.executable_product_version or "não encontrado"
        self.hero_meta.configure(text=(f"Engine: {engine}\nBuild / Runtime: {runtime}\n"
                                       f"FileVersion: {file_version}\nProductVersion: {product_version}"))
        self.summary_count.configure(text=f"8/8 camadas · {report.duration_seconds or 0:.2f}s")
        self.status_var.set("Varredura passiva concluída · revisão estática, sem confirmação de runtime")
        self._render_report(report)
        self._save_history(report)

    def _render_report(self, report: DiagnosticReport | None):
        findings = {item.layer: item for item in report.findings} if report else {}
        for card in self.layers_frame.winfo_children():
            key = LAYER_META[int(card.grid_info()["row"]) * 2 + int(card.grid_info()["column"])][0]
            finding = findings.get(key)
            state, summary, percent = card._scanner_labels
            if not finding:
                state.configure(text="Aguardando", fg=MUTED, bg="#16283b")
                summary.configure(text="A camada será analisada após iniciar o diagnóstico.")
                percent.configure(text="—")
                continue
            color = {"detected": GREEN, "probable": BLUE, "not_confirmed": YELLOW,
                     "not_found": MUTED, "not_applicable": MUTED}.get(finding.status, MUTED)
            label = {"detected": "Detectado", "probable": "Provável", "not_confirmed": "A validar",
                     "not_found": "Não encontrado", "not_applicable": "N/A"}.get(finding.status, finding.status)
            state.configure(text=label, fg=color, bg="#11283b")
            summary.configure(text=finding.summary)
            percent.configure(text=f"{finding.confidence_percent}%" if finding.confidence_percent is not None else "—")
        for item in self.evidence_tree.get_children():
            self.evidence_tree.delete(item)
        if report:
            evidence_rows = []
            for finding in report.findings:
                for evidence in finding.evidence:
                    evidence_rows.append((finding.title, evidence))
            for layer_title, evidence in evidence_rows[:120]:
                description = evidence.path if evidence.path else evidence.description
                iid = self.evidence_tree.insert("", "end", values=(evidence.kind, description),
                                                tags=(layer_title, evidence.description))
                self.evidence_tree.item(iid, tags=(layer_title, evidence.description))
            counters = {key: 0 for key in ("high", "medium", "low", "unknown")}
            for finding in report.findings:
                counters[finding.confidence] = counters.get(finding.confidence, 0) + 1
            self.confidence_label.configure(text=(f"Alta {counters['high']}   ·   Média {counters['medium']}\n"
                                                  f"Baixa {counters['low']}   ·   Não determinada {counters['unknown']}"))
            recommendation = report.recommendation
            self.recommendation_title.configure(text=recommendation.get("title", "Sem recomendação"))
            plugin_candidates = recommendation.get("registered_plugin_candidates", [])
            extra = []
            if plugin_candidates:
                plugin = plugin_candidates[0]
                extra.append(f"Referência do projeto: callbacks observados anteriormente = "
                             f"{'sim' if plugin.get('runtime_callback_observed') else 'não'}; "
                             f"hook confirmado = {'sim' if plugin.get('runtime_hook_confirmed') else 'não'}.")
            self.recommendation_text.configure(text="\n".join(recommendation.get("reasons", []) + extra))
            next_text = ("Valide em runtime qualquer ID, callback ou adapter sugerido. "
                         "O scan estático não confirma o funcionamento em jogo.")
            if plugin_candidates and not plugin_candidates[0].get("runtime_hook_confirmed"):
                next_text = ("Compare a build selecionada com o candidato existente e mantenha a validação do hook "
                             "como pendente até o runtime confirmar o ciclo da fala.")
            self.next_step_label.configure(text=next_text)
        else:
            self.confidence_label.configure(text="Alta 0   ·   Média 0   ·   Baixa 0   ·   Não determinada 8")
            self.recommendation_title.configure(text="Aguardando evidências")
            self.recommendation_text.configure(text="A recomendação será baseada somente nos sinais encontrados.")
            self.next_step_label.configure(text="Selecione o jogo e execute as oito camadas.")

    def _show_layer_details(self, layer_key: str):
        if not self.report:
            return
        finding = next((item for item in self.report.findings if item.layer == layer_key), None)
        if not finding:
            return
        win = tk.Toplevel(self)
        win.title(f"{finding.title} — evidências")
        win.geometry("760x560")
        win.configure(bg=BG)
        win.transient(self)
        body = self._card(win, 18)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(body, text=finding.title, bg=PANEL, fg=TEXT, font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(body, text=f"Estado: {finding.status}  ·  Confiança: {finding.confidence}"
                 + (f" ({finding.confidence_percent}%)" if finding.confidence_percent is not None else ""),
                 bg=PANEL, fg=CYAN, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(5, 12))
        tk.Label(body, text=finding.summary, bg=PANEL, fg=TEXT, font=("Segoe UI", 11),
                 wraplength=700, justify="left").pack(anchor="w", pady=(0, 12))
        text = tk.Text(body, bg="#071522", fg="#c9dcf3", insertbackground=TEXT,
                       font=("Consolas", 10), wrap="word", relief="flat", padx=10, pady=10)
        text.pack(fill="both", expand=True)
        for evidence in finding.evidence:
            text.insert("end", f"EVIDÊNCIA · {evidence.kind}\n  {evidence.path}\n  {evidence.description}\n")
            if evidence.matches:
                text.insert("end", f"  Assinaturas: {', '.join(evidence.matches)}\n")
            text.insert("end", "\n")
        if not finding.evidence:
            text.insert("end", "Nenhuma evidência estática encontrada nesta camada.\n\n")
        for detail in finding.details:
            text.insert("end", f"• {detail}\n")
        if finding.runtime_validation_required:
            text.insert("end", "\nVALIDAÇÃO PENDENTE · resultado estático não confirma o comportamento em runtime.\n")
        text.configure(state="disabled")

    def _show_selected_evidence(self, _event=None):
        selection = self.evidence_tree.selection()
        if not selection:
            return
        tags = self.evidence_tree.item(selection[0], "tags")
        if tags and tags[0] == "runtime" and len(tags) > 1:
            frame = Path(tags[1])
            if frame.is_file():
                os.startfile(frame)
            return
        if tags and self.report:
            finding = next((item for item in self.report.findings if item.title == tags[0]), None)
            if finding:
                self._show_layer_details(finding.layer)

    def _export_json(self):
        if not self.report:
            return
        suggested = f"{self.report.game_name.replace(' ', '_')}_{self.report.report_id}.json"
        destination = filedialog.asksaveasfilename(title="Exportar diagnóstico estruturado",
                                                   defaultextension=".json", initialfile=suggested,
                                                   filetypes=[("JSON", "*.json")])
        if not destination:
            return
        try:
            path = export_json(self.report, destination)
        except OSError as exc:
            messagebox.showerror("Falha ao exportar", str(exc))
            return
        self.status_var.set(f"Relatório JSON exportado para {path}")

    def _save_history(self, report: DiagnosticReport):
        try:
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            saved_path = export_json(report, REPORTS_DIR / f"{report.report_id}.json")
            root_key = os.path.normcase(os.path.abspath(report.root_path))
            self.history = [item for item in self.history
                            if os.path.normcase(os.path.abspath(item.get("root_path", ""))) != root_key]
            self.history.insert(0, {"report_id": report.report_id, "game_name": report.game_name,
                                    "root_path": report.root_path, "report_path": str(saved_path),
                                    "scanned_at": report.scanned_at})
            self.history = self.history[:30]
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")
            self._render_history()
        except OSError as exc:
            self.status_var.set(f"Scan concluído; histórico local não salvo: {exc}")

    def _load_history(self):
        try:
            saved = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.is_file() else []
            records = saved if isinstance(saved, list) else []
            seen_roots = set()
            self.history = []
            for item in records:
                if not isinstance(item, dict):
                    continue
                root_key = os.path.normcase(os.path.abspath(item.get("root_path", "")))
                if root_key in seen_roots:
                    continue
                seen_roots.add(root_key)
                self.history.append(item)
            self._history_mtime = HISTORY_FILE.stat().st_mtime_ns if HISTORY_FILE.is_file() else None
        except (OSError, ValueError, TypeError):
            self.history = []
            self._history_mtime = None
        self._render_history()

    def _refresh_history_if_changed(self):
        try:
            current_mtime = HISTORY_FILE.stat().st_mtime_ns if HISTORY_FILE.is_file() else None
        except OSError:
            return
        if current_mtime == self._history_mtime:
            return
        self._load_history()
        if self.report is None and self.history:
            self._open_history(self.history[0])

    def _render_history(self):
        if not hasattr(self, "history_frame"):
            return
        for child in self.history_frame.winfo_children():
            child.destroy()
        query = self.search_var.get().strip().casefold()
        visible = [item for item in self.history if query in item.get("game_name", "").casefold()
                   or query in item.get("root_path", "").casefold()]
        self.game_combo.configure(values=["Novo diagnóstico"] + [item.get("game_name", "Jogo") for item in self.history])
        if not visible:
            tk.Label(self.history_frame, text="Nenhum scan recente.", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9), wraplength=210, justify="left").pack(anchor="w", pady=8)
        for record in visible[:20]:
            stamp = record.get("scanned_at", "")[:16].replace("T", " ")
            button = tk.Button(self.history_frame, text=f"◈  {record.get('game_name', 'Jogo')}\n     {stamp}\n     {record.get('root_path', '')}",
                               command=lambda item=record: self._open_history(item), bg=PANEL_ALT, fg=TEXT,
                               activebackground="#143454", activeforeground=TEXT, relief="flat", bd=0,
                               anchor="w", justify="left", padx=9, pady=9, wraplength=220,
                               font=("Segoe UI", 9, "bold"), highlightbackground=EDGE, highlightthickness=1)
            button.pack(fill="x", pady=4)

    def _open_history(self, record):
        try:
            data = json.loads(Path(record["report_path"]).read_text(encoding="utf-8"))
            self.report = _report_from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            messagebox.showerror("Scan indisponível", f"Não foi possível abrir o relatório salvo.\n{exc}")
            return
        self.path_var.set(self.report.executable_path or self.report.root_path)
        self.game_var.set(self.report.game_name)
        self.hero_name.configure(text=self.report.game_name.upper())
        self.hero_path.configure(text=self.report.executable_path or self.report.root_path)
        engine = next((item.summary for item in self.report.findings if item.layer == "engine"), "—")
        runtime = next((item.summary for item in self.report.findings if item.layer == "build_runtime"), "—")
        file_version = self.report.executable_file_version or "não encontrada"
        product_version = self.report.executable_product_version or "não encontrado"
        self.hero_meta.configure(text=(f"Engine: {engine}\nBuild / Runtime: {runtime}\n"
                                       f"FileVersion: {file_version}\nProductVersion: {product_version}"))
        self.summary_count.configure(text=f"8/8 camadas · {self.report.duration_seconds or 0:.2f}s")
        self.progress_var.set(10)
        self.status_var.set("Relatório local carregado · análise estática")
        self.export_button.configure(state="normal")
        self._render_report(self.report)

    def _select_history_game(self, _event=None):
        selected = self.game_var.get()
        if selected == "Novo diagnóstico":
            self._new_scan()
            return
        record = next((item for item in self.history if item.get("game_name") == selected), None)
        if record:
            self._open_history(record)


def main():
    app = GameScannerWindow()
    app.mainloop()
