from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid


TRANSPARENT = "#010203"
BLUE = "#42a5ff"
CYAN = "#67d9ee"
RED = "#ff6470"


def _request(api_base: str, project_id: str, path: str, method: str = "GET", body=None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_base.rstrip("/") + path, data=payload, method=method,
        headers={"Content-Type": "application/json", "X-DubSync-Project": project_id},
    )
    with urllib.request.urlopen(request, timeout=2.5) as response:
        return json.loads(response.read().decode("utf-8"))


class RuntimeOverlay:
    def __init__(self, tk, ttk, messagebox, api_base: str, project_id: str, session_id: str):
        self.tk, self.ttk, self.messagebox = tk, ttk, messagebox
        self.api_base, self.project_id, self.session_id = api_base, project_id, session_id
        self.root = tk.Tk()
        self.root.title("DubSync · ROIs do jogo")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(background=TRANSPARENT)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError as exc:
            raise RuntimeError("O Windows não disponibilizou transparência por cor-chave para o overlay.") from exc
        self.canvas = tk.Canvas(self.root, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.regions: list[dict] = []
        self.window: dict = {}
        self.editing = False
        self.add_mode: str | None = None
        self.delete_mode = False
        self.drag: dict | None = None
        self.selected_roi = 0
        self.boxes: list[dict] = []
        self.seen_events: set[str] = set()
        self.popup_open = False
        self.orientation = "vertical"
        self._toolbar_offset = (12, 12)
        self.status_var = tk.StringVar(value="Captura Real iniciada")
        self._last_error = ""
        self._build_toolbar(ttk)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.root.protocol("WM_DELETE_WINDOW", self._close_requested)
        self.root.bind("<Escape>", lambda _event: self._toggle_edit(False))
        self._set_click_through(True)
        self._poll()

    def _build_toolbar(self, ttk):
        self.toolbar = self.tk.Toplevel(self.root)
        self.toolbar.title("DubSync · Captura Real")
        self.toolbar.overrideredirect(True)
        self.toolbar.attributes("-topmost", True)
        self.toolbar.configure(bg="#111c2c")
        self.tool_frame = self.tk.Frame(self.toolbar, bg="#111c2c", padx=6, pady=6)
        self.tool_frame.pack(fill="both", expand=True)
        self.status_label = self.tk.Label(self.tool_frame, textvariable=self.status_var,
                                          bg="#111c2c", fg="#dbe7f6", font=("Segoe UI", 9),
                                          wraplength=150, justify="left")
        self.status_label.pack(fill="x", pady=(0, 5))
        self.buttons = []
        self._button(ttk, "Ajustar ROIs", lambda: self._toggle_edit(not self.editing))
        self._button(ttk, "+ Diálogo", lambda: self._begin_add("dialogue"))
        self._button(ttk, "+ Personagem", lambda: self._begin_add("speaker"))
        self._button(ttk, "Remover ROI", self._begin_delete)
        self._button(ttk, "Barra ↔", self._toggle_orientation)
        self.pause_button = self._button(ttk, "Ⅱ Pausar", self._toggle_pause)
        self._button(ttk, "■ Encerrar", self._stop)
        drag_area = self.tk.Label(self.tool_frame, text="Arraste para mover", bg="#17263a",
                                  fg="#8fa3bd", font=("Segoe UI", 8), cursor="fleur")
        drag_area.pack(fill="x", pady=(5, 0))
        drag_area.bind("<ButtonPress-1>", self._toolbar_drag_start)
        drag_area.bind("<B1-Motion>", self._toolbar_drag_move)
        self.toolbar.update_idletasks()
        self._layout_toolbar()

    def _button(self, ttk, label: str, command):
        button = ttk.Button(self.tool_frame, text=label, command=command)
        button.pack(fill="x", pady=2)
        self.buttons.append(button)
        return button

    def _layout_toolbar(self):
        left, top = self.window.get("left", 20), self.window.get("top", 20)
        self.toolbar.geometry(f"+{int(left + self._toolbar_offset[0])}+{int(top + self._toolbar_offset[1])}")
        if self.orientation == "vertical":
            self.tool_frame.pack_configure(fill="both", expand=True)
        else:
            self.tool_frame.pack_configure(fill="x")
            for button in self.buttons:
                button.pack_configure(side="left", fill="x", expand=True, padx=2, pady=2)
            self.status_label.pack_configure(side="left", fill="x", padx=(0, 5), pady=0)
        self.toolbar.lift()

    def _toggle_orientation(self):
        self.orientation = "horizontal" if self.orientation == "vertical" else "vertical"
        for child in self.tool_frame.winfo_children():
            child.pack_forget()
        self.status_label.pack_forget()
        for button in self.buttons:
            button.pack_forget()
        drag = next((child for child in self.tool_frame.winfo_children()
                     if isinstance(child, self.tk.Label) and child is not self.status_label), None)
        if self.orientation == "vertical":
            self.status_label.pack(fill="x", pady=(0, 5))
            for button in self.buttons:
                button.pack(fill="x", pady=2)
            if drag:
                drag.pack(fill="x", pady=(5, 0))
        else:
            self.status_label.pack(side="left", fill="x", padx=(0, 5))
            for button in self.buttons:
                button.pack(side="left", fill="x", expand=True, padx=2, pady=2)
            if drag:
                drag.pack(side="left", fill="x", padx=3)
        self.toolbar.update_idletasks()
        self._layout_toolbar()

    def _set_click_through(self, enabled: bool):
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            user32 = ctypes.windll.user32
            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
            exstyle = get_long(ctypes.c_void_p(hwnd), -20)
            exstyle |= 0x00080000  # WS_EX_LAYERED
            if enabled:
                exstyle |= 0x00000020  # WS_EX_TRANSPARENT
            else:
                exstyle &= ~0x00000020
            set_long(ctypes.c_void_p(hwnd), -20, exstyle)
            user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(-1),
                                0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except (AttributeError, OSError, self.tk.TclError):
            pass

    def _toggle_edit(self, value: bool):
        self.editing = bool(value)
        self.add_mode = None
        self.delete_mode = False
        self._set_click_through(not self.editing)
        self.status_var.set("Ajuste ativo · arraste área ou cantos" if self.editing else "Captura Real · jogo livre")
        self._draw()

    def _begin_add(self, kind: str):
        self._toggle_edit(True)
        if kind == "speaker" and not self.regions:
            self.status_var.set("Crie primeiro uma área de diálogo.")
            return
        self.add_mode = kind
        self.status_var.set("Arraste sobre o jogo para marcar " + ("o diálogo" if kind == "dialogue" else "o nome"))

    def _begin_delete(self):
        self._toggle_edit(True)
        self.delete_mode = True
        self.status_var.set("Clique na ROI que deseja remover. A última ROI é protegida.")

    def _toggle_pause(self):
        state = _request(self.api_base, self.project_id,
                         f"/api/runtime-capture/{self.session_id}/status")
        path = "resume" if state.get("state") == "paused" else "pause"
        result = _request(self.api_base, self.project_id,
                          f"/api/runtime-capture/{self.session_id}/{path}", "POST", {})
        paused = result.get("state") == "paused"
        self.pause_button.configure(text="▶ Continuar" if paused else "Ⅱ Pausar")
        self.status_var.set("Captura pausada" if paused else "Capturando frames do jogo")

    def _stop(self):
        if not self.messagebox.askyesno("Encerrar Captura Real", "Salvar os diálogos atuais e encerrar esta captura?"):
            return
        try:
            _request(self.api_base, self.project_id,
                     f"/api/runtime-capture/{self.session_id}/stop", "POST", {})
            self.status_var.set("Salvando o diálogo atual…")
            self.root.after(500, self._wait_until_stopped)
        except Exception as exc:
            self.status_var.set(f"Não foi possível encerrar: {exc}")

    def _wait_until_stopped(self):
        try:
            state = _request(self.api_base, self.project_id,
                             f"/api/runtime-capture/{self.session_id}/status")
            if state.get("state") in {"complete", "failed", "stopped"}:
                self.root.destroy()
                return
        except Exception:
            pass
        self.root.after(500, self._wait_until_stopped)

    def _toolbar_drag_start(self, event):
        self._toolbar_origin = (event.x_root - self.toolbar.winfo_x(), event.y_root - self.toolbar.winfo_y())

    def _toolbar_drag_move(self, event):
        offset = getattr(self, "_toolbar_origin", (0, 0))
        left, top = event.x_root - offset[0], event.y_root - offset[1]
        self._toolbar_offset = (left - int(self.window.get("left", 0)),
                                top - int(self.window.get("top", 0)))
        self.toolbar.geometry(f"+{left}+{top}")

    def _find_box(self, x: int, y: int) -> dict | None:
        for box in reversed(self.boxes):
            x0, y0, x1, y1 = box["rect"]
            if x0 - 8 <= x <= x1 + 8 and y0 - 8 <= y <= y1 + 8:
                return box
        return None

    def _press(self, event):
        if not self.editing:
            return
        if self.add_mode:
            self.drag = {"kind": "create", "box_type": self.add_mode,
                         "start": (event.x, event.y), "current": (event.x, event.y)}
            return
        box = self._find_box(event.x, event.y)
        if not box:
            return
        self.selected_roi = int(box["index"])
        if self.delete_mode:
            if box["box_type"] == "speaker":
                region = self.regions[box["index"]]
                region["speaker_enabled"] = False
                region["speaker_region"] = None
                self.delete_mode = False
                self._save_regions()
            elif len(self.regions) > 1:
                self.regions.pop(box["index"])
                self.delete_mode = False
                self._save_regions()
            else:
                self.status_var.set("Mantenha ao menos uma ROI de diálogo.")
            return
        x0, y0, x1, y1 = box["rect"]
        if abs(event.x - x0) <= 13 and abs(event.y - y0) <= 13:
            action = "move"
        elif abs(event.x - x1) <= 13 and abs(event.y - y1) <= 13:
            action = "resize"
        else:
            action = "move"
        self.drag = {"kind": action, "box_type": box["box_type"], "index": box["index"],
                     "start": (event.x, event.y), "origin": self._box_normalized(box),
                     "current": (event.x, event.y)}

    def _move(self, event):
        if self.drag:
            self.drag["current"] = (event.x, event.y)
            self._draw()

    def _release(self, event):
        if not self.drag:
            return
        drag = self.drag
        self.drag = None
        end = (event.x, event.y)
        if drag["kind"] == "create":
            x0, y0 = drag["start"]
            left, top, right, bottom = min(x0, end[0]), min(y0, end[1]), max(x0, end[0]), max(y0, end[1])
            if right - left < 18 or bottom - top < 14:
                self.status_var.set("A área ficou muito pequena; arraste uma caixa maior.")
                self._draw()
                return
            rect = self._normalize_rect((left, top, right, bottom))
            if drag["box_type"] == "dialogue":
                used_names = {str(item.get("name", "")).casefold() for item in self.regions}
                index = 1
                while f"ROI {index}".casefold() in used_names:
                    index += 1
                self.regions.append({"roi_id": str(uuid.uuid4()), "name": f"ROI {index}",
                                     **rect, "enabled": True, "lifetime": {"enabled": False, "windows": []},
                                     "motion": {"enabled": False, "keyframes": []},
                                     "speaker_enabled": False, "speaker_region": None,
                                     "character_detection": {"enabled": True, "prefix_before_colon": True,
                                                              "color_rules": []}})
                self.selected_roi = len(self.regions) - 1
            else:
                region = self.regions[self.selected_roi]
                region["speaker_enabled"] = True
                region["speaker_region"] = rect
            self.add_mode = None
            self._save_regions()
            return
        dx, dy = end[0] - drag["start"][0], end[1] - drag["start"][1]
        box = drag["origin"].copy()
        if drag["kind"] == "move":
            box["x"] += dx / max(1, int(self.window.get("width", 1)))
            box["y"] += dy / max(1, int(self.window.get("height", 1)))
        else:
            box["width"] += dx / max(1, int(self.window.get("width", 1)))
            box["height"] += dy / max(1, int(self.window.get("height", 1)))
        box = self._clamp_box(box)
        region = self.regions[drag["index"]]
        if drag["box_type"] == "dialogue":
            old = {key: float(region.get(key, 0)) for key in ("x", "y", "width", "height")}
            region.update(box)
            if region.get("speaker_enabled") and region.get("speaker_region"):
                spec = region["speaker_region"]
                spec["x"] += box["x"] - old["x"]
                spec["y"] += box["y"] - old["y"]
        else:
            region["speaker_enabled"] = True
            region["speaker_region"] = box
        self._save_regions()

    def _box_normalized(self, box: dict) -> dict:
        region = self.regions[box["index"]]
        if box["box_type"] == "speaker":
            return dict(region["speaker_region"])
        return {key: float(region[key]) for key in ("x", "y", "width", "height")}

    @staticmethod
    def _clamp_box(box: dict) -> dict:
        width = min(1.0, max(0.01, float(box["width"])))
        height = min(1.0, max(0.01, float(box["height"])))
        x = min(max(0.0, float(box["x"])), 1 - width)
        y = min(max(0.0, float(box["y"])), 1 - height)
        return {"x": x, "y": y, "width": width, "height": height}

    def _normalize_rect(self, rect):
        width, height = max(1, int(self.window.get("width", 1))), max(1, int(self.window.get("height", 1)))
        left, top, right, bottom = rect
        return self._clamp_box({"x": left / width, "y": top / height,
                                "width": (right - left) / width, "height": (bottom - top) / height})

    def _save_regions(self):
        try:
            value = _request(self.api_base, self.project_id,
                             f"/api/runtime-capture/{self.session_id}/regions", "PUT",
                             {"regions": self.regions})
            self.regions = value.get("regions") or self.regions
            self.status_var.set("ROI atualizada · captura continua")
        except Exception as exc:
            self.status_var.set(f"Falha ao salvar ROI: {str(exc)[:80]}")
        self._draw()

    def _draw(self):
        if not self.root.winfo_exists():
            return
        self.canvas.delete("all")
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        self.boxes = []
        for index, region in enumerate(self.regions):
            x0 = int(float(region.get("x", 0)) * width)
            y0 = int(float(region.get("y", 0)) * height)
            x1 = int((float(region.get("x", 0)) + float(region.get("width", 0))) * width)
            y1 = int((float(region.get("y", 0)) + float(region.get("height", 0))) * height)
            color = ["#ff5663", "#43a8ff", "#ffc857", "#bd86ff"][index % 4]
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=3)
            self.canvas.create_text(x0 + 5, max(10, y0 - 12), anchor="w",
                                    text=region.get("name", f"ROI {index + 1}"), fill=color,
                                    font=("Segoe UI", 10, "bold"))
            self.canvas.create_rectangle(x0, y0, x0 + 12, y0 + 12, fill=color, outline="white")
            self.canvas.create_rectangle(x1 - 12, y1 - 12, x1, y1, fill=color, outline="white")
            self.boxes.append({"index": index, "box_type": "dialogue", "rect": (x0, y0, x1, y1)})
            speaker = region.get("speaker_region")
            if region.get("speaker_enabled") and speaker:
                sx0, sy0 = int(float(speaker["x"]) * width), int(float(speaker["y"]) * height)
                sx1 = int((float(speaker["x"]) + float(speaker["width"])) * width)
                sy1 = int((float(speaker["y"]) + float(speaker["height"])) * height)
                self.canvas.create_rectangle(sx0, sy0, sx1, sy1, outline=CYAN, width=2, dash=(5, 3))
                self.canvas.create_text(sx0 + 5, max(10, sy0 - 11), anchor="w",
                                        text=f"Personagem · {region.get('name', '')}", fill=CYAN,
                                        font=("Segoe UI", 9, "bold"))
                self.canvas.create_rectangle(sx0, sy0, sx0 + 11, sy0 + 11, fill=CYAN, outline="white")
                self.canvas.create_rectangle(sx1 - 11, sy1 - 11, sx1, sy1, fill=CYAN, outline="white")
                self.boxes.append({"index": index, "box_type": "speaker", "rect": (sx0, sy0, sx1, sy1)})
        if self.drag:
            x0, y0 = self.drag["start"]
            x1, y1 = self.drag["current"]
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=BLUE, width=2, dash=(5, 3))

    def _show_popup(self, event: dict):
        self.popup_open = True
        popup = self.tk.Toplevel(self.root)
        popup.title("Conferir fala · DubSync")
        popup.attributes("-topmost", True)
        popup.configure(bg="#111c2c")
        popup.geometry(f"390x178+{int(self.window.get('left', 80) + self.window.get('width', 800) - 420)}+{int(self.window.get('top', 60) + 60)}")
        self.tk.Label(popup, text=f"Fala detectada · {float(event.get('start_time') or 0):.2f}s",
                      bg="#111c2c", fg="#8dbcf5", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(9, 2))
        if event.get("character"):
            self.tk.Label(popup, text=f"Personagem: {event['character']}", bg="#111c2c", fg="#b9c8db",
                          font=("Segoe UI", 9)).pack(anchor="w", padx=10)
        text = self.tk.Text(popup, height=3, wrap="word", bg="#0d1726", fg="#e7edf6",
                            insertbackground="white", relief="solid", bd=1)
        text.pack(fill="both", expand=True, padx=10, pady=5)
        text.insert("1.0", event.get("processed_text") or event.get("original_text") or "")
        actions = self.tk.Frame(popup, bg="#111c2c")
        actions.pack(fill="x", padx=10, pady=(0, 8))

        def finish(action: str):
            corrected = text.get("1.0", "end-1c").strip()
            try:
                if action == "accept":
                    _request(self.api_base, self.project_id,
                             f"/api/events/{event['event_id']}/review/accept", "POST", {})
                elif corrected:
                    _request(self.api_base, self.project_id,
                             f"/api/events/{event['event_id']}/review/correct", "POST",
                             {"corrected_text": corrected})
                popup.destroy()
            except Exception as exc:
                self.status_var.set(f"Falha ao salvar revisão: {str(exc)[:80]}")
            self.popup_open = False

        self.ttk.Button(actions, text="Confirmar", command=lambda: finish("accept")).pack(side="right", padx=(4, 0))
        self.ttk.Button(actions, text="Corrigir texto", command=lambda: finish("correct")).pack(side="right")
        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(), setattr(self, "popup_open", False)))

    def _poll(self):
        try:
            state = _request(self.api_base, self.project_id,
                             f"/api/runtime-capture/{self.session_id}/status")
            self.window = state.get("window") or self.window
            self.regions = state.get("regions") or self.regions
            progress = state.get("progress") or {}
            is_paused = state.get("state") == "paused"
            self.pause_button.configure(text="▶ Continuar" if is_paused else "Ⅱ Pausar")
            if not self.editing:
                self.status_var.set("Pausada" if is_paused else
                                    f"Capturando · {int(progress.get('frames_seen') or 0)} frames · {float(progress.get('ocr_calls') or 0):.0f} OCR")
            if self.window:
                self.root.geometry(f"{int(self.window['width'])}x{int(self.window['height'])}+{int(self.window['left'])}+{int(self.window['top'])}")
                self.toolbar.geometry(f"+{int(self.window['left']) + self._toolbar_offset[0]}+{int(self.window['top']) + self._toolbar_offset[1]}")
                self._draw()
            self._last_error = ""
            if state.get("state") in {"complete", "failed", "stopped"}:
                self.status_var.set("Captura encerrada · eventos salvos")
                self.root.after(1600, self.root.destroy)
                return
            pending = _request(self.api_base, self.project_id,
                               f"/api/runtime-capture/{self.session_id}/pending").get("events", [])
            if not self.popup_open:
                candidate = next((item for item in reversed(pending)
                                  if item["event_id"] not in self.seen_events), None)
                if candidate:
                    self.seen_events.add(candidate["event_id"])
                    self._show_popup(candidate)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            message = f"DubSync indisponível · {str(exc)[:90]}"
            if message != self._last_error:
                self.status_var.set(message)
                self._last_error = message
        self.root.after(450, self._poll)

    def _close_requested(self):
        if self.messagebox.askyesno("Encerrar Captura Real", "Fechar o overlay também encerra e salva a captura?"):
            self._stop()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--api-base", required=True)
    args = parser.parse_args()
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
        _request(args.api_base, args.project_id,
                 f"/api/runtime-capture/{args.session_id}/status")
        RuntimeOverlay(tk, ttk, messagebox, args.api_base, args.project_id,
                       args.session_id).run()
    except Exception as exc:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("DubSync · Captura Real", str(exc))
            root.destroy()
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
