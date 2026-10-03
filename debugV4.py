import os
import json
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from tkinter.scrolledtext import ScrolledText

REFRESH_INTERVAL = 1000
MAX_LINES = 1000
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "log_viewer_config.json")
DEFAULT_PROFILE = "Default"
SEARCH_FILTER_SAVE_DELAY = 800  # ms debounce for search/filter typing

BG = "#F0F4F8"
SURFACE = "#FFFFFF"
HEADER = "#1E3A5F"
ACCENT = "#2563EB"
ACCENT_H = "#1D4ED8"
ACCENT_LIGHT = "#EFF6FF"
BORDER = "#CBD5E1"
TEXT = "#1E293B"
MUTED = "#64748B"
GREEN = "#059669"
RED = "#DC2626"
YELLOW = "#D97706"
BLUE = "#2563EB"

# Legacy aliases kept so the rest of the file (written against a dark theme)
# maps cleanly onto the new light LogCleaner-style palette.
BG2 = SURFACE
BG3 = "#F8FAFC"
FG = TEXT
FONT = "Segoe UI"


def _empty_profile():
    return {"geometry": "1400x800", "sash_positions": [], "entries": []}


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"current_profile": DEFAULT_PROFILE, "profiles": {DEFAULT_PROFILE: _empty_profile()}}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"current_profile": DEFAULT_PROFILE, "profiles": {DEFAULT_PROFILE: _empty_profile()}}

    # Migrate old flat formats (list of entries, or dict without "profiles")
    if isinstance(data, list):
        profile = _empty_profile()
        profile["entries"] = data
        return {"current_profile": DEFAULT_PROFILE, "profiles": {DEFAULT_PROFILE: profile}}

    if "profiles" not in data:
        profile = _empty_profile()
        profile["geometry"] = data.get("geometry", profile["geometry"])
        profile["sash_positions"] = data.get("sash_positions", [])
        profile["entries"] = data.get("entries", [])
        return {"current_profile": DEFAULT_PROFILE, "profiles": {DEFAULT_PROFILE: profile}}

    if not data.get("profiles"):
        data["profiles"] = {DEFAULT_PROFILE: _empty_profile()}
    if data.get("current_profile") not in data["profiles"]:
        data["current_profile"] = next(iter(data["profiles"]))

    return data


def save_config(data):
    # Never let a save failure block the caller (e.g. window close) -
    # a bad CWD or locked file used to leave the app un-closeable.
    try:
        tmp_path = CONFIG_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, CONFIG_FILE)
    except OSError as e:
        print(f"[WARN] Konnte Konfiguration nicht speichern: {e}")


class LogView:
    def __init__(self, parent, base_path, name, filter_key="", search_term="", paused=False, on_change=None):
        self.base_path = base_path
        self.name = name
        self.latest_file = None
        self.file_position = 0
        self.lines_buffer = []
        self.paused = paused
        self.search_term = search_term
        self.filter_key = filter_key
        self.on_change = on_change
        self._save_after_id = None

        self.frame = tk.Frame(
            parent, bg=SURFACE,
            highlightbackground=BORDER, highlightthickness=1, bd=0,
        )

        # Header
        header = tk.Frame(self.frame, bg=BG2, pady=4)
        header.pack(fill="x", padx=4)

        tk.Label(
            header, text=name, bg=BG2, fg=FG,
            font=("Segoe UI", 10, "bold")
        ).pack(side="left", padx=(4, 8))

        self.status_label = tk.Label(
            header, text="● RUNNING", bg=BG2, fg=GREEN,
            font=("Segoe UI", 9)
        )
        self.status_label.pack(side="left")

        # Right-side controls (packed right-to-left)
        btn_style = dict(
            bg=ACCENT, fg="white", activebackground=ACCENT_H,
            activeforeground="white", relief="flat", bd=0,
            font=("Segoe UI", 9), padx=8, pady=3, cursor="hand2"
        )

        tk.Button(
            header, text="Remove", command=self.remove_self,
            bg=SURFACE, fg=RED, activebackground="#FEF2F2", activeforeground=RED,
            relief="flat", bd=0, font=("Segoe UI", 9), padx=8, pady=3, cursor="hand2"
        ).pack(side="right", padx=(2, 4))

        self.pause_btn = tk.Button(
            header, text="⏸ Pause", command=self.toggle_pause, **btn_style
        )
        self.pause_btn.pack(side="right", padx=2)

        # Search
        tk.Label(header, text="Search", bg=BG2, fg=MUTED, font=("Segoe UI", 9)).pack(side="right", padx=(8, 2))
        self.search_entry = tk.Entry(
            header, width=14,
            bg=BG3, fg=FG, insertbackground=FG,
            relief="flat", bd=1, font=("Segoe UI", 9),
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT
        )
        self.search_entry.pack(side="right")
        if search_term:
            self.search_entry.insert(0, search_term)

        # Key filter
        tk.Label(header, text="Key", bg=BG2, fg=MUTED, font=("Segoe UI", 9)).pack(side="right", padx=(8, 2))
        self.filter_entry = tk.Entry(
            header, width=14,
            bg=BG3, fg=FG, insertbackground=FG,
            relief="flat", bd=1, font=("Segoe UI", 9),
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT
        )
        self.filter_entry.pack(side="right")
        if filter_key:
            self.filter_entry.insert(0, filter_key)

        # Log text area
        self.text = ScrolledText(
            self.frame,
            bg=BG3, fg=FG,
            insertbackground=FG,
            selectbackground=ACCENT_LIGHT,
            selectforeground=FG,
            font=("Consolas", 9),
            relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER,
            padx=6, pady=4,
        )
        self.text.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self.text.tag_config("ERROR",   foreground=RED)
        self.text.tag_config("WARNING", foreground=YELLOW)
        self.text.tag_config("INFO",    foreground=BLUE)
        self.text.tag_config("SEARCH",  background="#FDE68A")

        self.search_entry.bind("<KeyRelease>", self.update_search)
        self.filter_entry.bind("<KeyRelease>", self.update_filter)

        if paused:
            self.update_status()

        # No auto-pause on scroll/click — only the button pauses
        self.update_loop()

    def remove_self(self):
        self.frame.destroy()
        if self.on_change:
            self.on_change(removed=self)

    def toggle_pause(self):
        self.paused = not self.paused
        self.update_status()
        self._schedule_save()

    def update_status(self):
        if self.paused:
            self.status_label.config(text="● PAUSED", fg=RED)
            self.pause_btn.config(text="▶ Resume")
        else:
            self.status_label.config(text="● RUNNING", fg=GREEN)
            self.pause_btn.config(text="⏸ Pause")

    def update_search(self, event=None):
        self.search_term = self.search_entry.get()
        self.apply_search()
        self._schedule_save()

    def update_filter(self, event=None):
        self.filter_key = self.filter_entry.get()
        self.refresh_display()
        self._schedule_save()

    def _schedule_save(self):
        if not self.on_change:
            return
        if self._save_after_id is not None:
            try:
                self.frame.after_cancel(self._save_after_id)
            except Exception:
                pass
        self._save_after_id = self.frame.after(SEARCH_FILTER_SAVE_DELAY, lambda: self.on_change())

    def get_latest_log(self):
        if not os.path.exists(self.base_path):
            return None
        subdirs = [
            os.path.join(self.base_path, d)
            for d in os.listdir(self.base_path)
            if os.path.isdir(os.path.join(self.base_path, d))
        ]
        if not subdirs:
            return None
        latest_dir = max(subdirs, key=os.path.getmtime)
        log_file = os.path.join(latest_dir, "script.log")
        return log_file if os.path.exists(log_file) else None

    def tail(self):
        log_file = self.get_latest_log()
        if not log_file:
            return

        if log_file != self.latest_file:
            self.latest_file = log_file
            self.file_position = 0
            self.lines_buffer.clear()

        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(self.file_position)
            new_lines = f.readlines()
            self.file_position = f.tell()

        self.lines_buffer.extend(new_lines)
        self.lines_buffer = self.lines_buffer[-MAX_LINES:]
        self.refresh_display()

    def refresh_display(self):
        self.text.delete(1.0, tk.END)

        lines = self.lines_buffer
        if self.filter_key:
            lines = [l for l in lines if self.filter_key.lower() in l.lower()]

        for line in reversed(lines):
            tag = None
            if "ERROR" in line:
                tag = "ERROR"
            elif "WARNING" in line:
                tag = "WARNING"
            elif "INFO" in line:
                tag = "INFO"
            self.text.insert(tk.END, line, tag)

        self.apply_search()

    def apply_search(self):
        self.text.tag_remove("SEARCH", "1.0", tk.END)
        if not self.search_term:
            return
        start = "1.0"
        while True:
            pos = self.text.search(self.search_term, start, stopindex=tk.END, nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(self.search_term)}c"
            self.text.tag_add("SEARCH", pos, end)
            start = end

    def update_loop(self):
        if not self.paused:
            self.tail()
        self.frame.after(REFRESH_INTERVAL, self.update_loop)


# ── Main window ─────────────────────────────────────────────────────────────

cfg = load_config()
current_profile = cfg["current_profile"]

root = tk.Tk()
root.title("Sky Log Debug Console")
root.configure(bg=BG)

style = ttk.Style()
style.theme_use("clam")
style.configure(".", background=BG, foreground=FG)
style.configure("TPanedwindow", background=BG)
style.configure("Sash", sashthickness=5, sashpad=2, background=BORDER)
style.configure(
    "TCombobox",
    fieldbackground=SURFACE, background=SURFACE, foreground=TEXT,
    arrowcolor=TEXT, bordercolor=BORDER,
)

# Header bar (matches LogCleaner's navy title strip)
header_bar = tk.Frame(root, bg=HEADER, height=52)
header_bar.pack(fill="x", side="top")
header_bar.pack_propagate(False)

tk.Label(
    header_bar, text="Sky Log Debug Console", bg=HEADER, fg="white",
    font=(FONT, 15, "bold")
).pack(side="left", padx=20)

# Toolbar (card-style row below the header)
top = tk.Frame(root, bg=SURFACE, pady=8, highlightbackground=BORDER, highlightthickness=1)
top.pack(fill="x")

tk.Button(
    top, text="＋ Add Log View", command=lambda: add_view(),
    bg=ACCENT, fg="white", activebackground=ACCENT_H, activeforeground="white",
    relief="flat", bd=0, font=(FONT, 9, "bold"), padx=12, pady=6, cursor="hand2"
).pack(side="left", padx=(12, 6))

tk.Frame(top, bg=BORDER, width=1).pack(side="left", fill="y", padx=8, pady=2)

tk.Label(top, text="Profil", bg=SURFACE, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(0, 6))

profile_var = tk.StringVar(value=current_profile)
profile_combo = ttk.Combobox(
    top, textvariable=profile_var, state="readonly", width=18,
    values=list(cfg["profiles"].keys())
)
profile_combo.pack(side="left", padx=(0, 8))

small_btn_style = dict(
    bg=SURFACE, fg=ACCENT, activebackground=ACCENT_LIGHT, activeforeground=ACCENT,
    relief="solid", bd=1, font=(FONT, 9), padx=10, pady=5, cursor="hand2",
)

tk.Button(top, text="Neu", command=lambda: new_profile(), **small_btn_style).pack(side="left", padx=3)
tk.Button(top, text="Umbenennen", command=lambda: rename_profile(), **small_btn_style).pack(side="left", padx=3)
tk.Button(
    top, text="Löschen", command=lambda: delete_profile(),
    **{**small_btn_style, "fg": RED, "activebackground": "#FEF2F2", "activeforeground": RED}
).pack(side="left", padx=3)

paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
paned.pack(fill="both", expand=True, padx=0, pady=0)

views = []
_profile_save_after_id = None


def get_profile_data(name):
    return cfg["profiles"].setdefault(name, _empty_profile())


def gather_current_state():
    entries = []
    for v in views:
        entries.append({
            "path": v.base_path,
            "name": v.name,
            "filter": v.filter_key,
            "search": v.search_term,
            "paused": v.paused,
        })
    sashes = []
    for i in range(len(views) - 1):
        try:
            sashes.append(paned.sashpos(i))
        except Exception:
            pass
    return {
        "geometry": root.geometry(),
        "sash_positions": sashes,
        "entries": entries,
    }


def save_current_profile():
    cfg["profiles"][current_profile] = gather_current_state()
    cfg["current_profile"] = current_profile
    save_config(cfg)


def on_view_change(removed=None):
    if removed is not None and removed in views:
        views.remove(removed)
    save_current_profile()


def add_view(path=None, name=None, filter_key="", search_term="", paused=False, save=True):
    if path is None:
        path = filedialog.askdirectory()
        if not path:
            return
    if name is None:
        name = simpledialog.askstring("Name", "Name für diesen Log:")
        if not name:
            return

    view = LogView(
        paned, path, name,
        filter_key=filter_key, search_term=search_term, paused=paused,
        on_change=on_view_change,
    )
    paned.add(view.frame, weight=1)
    views.append(view)

    if save:
        save_current_profile()


def clear_views():
    for v in list(views):
        v.on_change = None  # avoid redundant saves while tearing down
        v.frame.destroy()
    views.clear()


def load_profile_into_ui(name):
    global current_profile
    current_profile = name
    profile_data = get_profile_data(name)

    clear_views()
    root.geometry(profile_data.get("geometry", "1400x800"))

    for entry in profile_data.get("entries", []):
        if os.path.exists(entry["path"]):
            add_view(
                entry["path"], entry["name"],
                filter_key=entry.get("filter", ""),
                search_term=entry.get("search", ""),
                paused=entry.get("paused", False),
                save=False,
            )

    def restore_sashes():
        for i, pos in enumerate(profile_data.get("sash_positions", [])):
            try:
                paned.sashpos(i, pos)
            except Exception:
                pass

    root.after(150, restore_sashes)


def switch_profile(event=None):
    global current_profile
    new_name = profile_var.get()
    if new_name == current_profile:
        return
    save_current_profile()
    load_profile_into_ui(new_name)
    profile_combo["values"] = list(cfg["profiles"].keys())
    cfg["current_profile"] = current_profile
    save_config(cfg)


profile_combo.bind("<<ComboboxSelected>>", switch_profile)


def new_profile():
    name = simpledialog.askstring("Neues Profil", "Name des neuen Profils:")
    if not name or name in cfg["profiles"]:
        if name in cfg["profiles"]:
            messagebox.showerror("Fehler", "Ein Profil mit diesem Namen existiert bereits.")
        return

    save_current_profile()
    cfg["profiles"][name] = _empty_profile()
    profile_var.set(name)
    load_profile_into_ui(name)
    profile_combo["values"] = list(cfg["profiles"].keys())
    cfg["current_profile"] = current_profile
    save_config(cfg)


def rename_profile():
    global current_profile
    old_name = current_profile
    new_name = simpledialog.askstring("Profil umbenennen", "Neuer Name:", initialvalue=old_name)
    if not new_name or new_name == old_name:
        return
    if new_name in cfg["profiles"]:
        messagebox.showerror("Fehler", "Ein Profil mit diesem Namen existiert bereits.")
        return

    cfg["profiles"][new_name] = cfg["profiles"].pop(old_name)
    current_profile = new_name
    profile_var.set(new_name)
    profile_combo["values"] = list(cfg["profiles"].keys())
    save_current_profile()


def delete_profile():
    global current_profile
    if len(cfg["profiles"]) <= 1:
        messagebox.showerror("Fehler", "Das letzte Profil kann nicht gelöscht werden.")
        return
    if not messagebox.askyesno("Profil löschen", f"Profil '{current_profile}' wirklich löschen?"):
        return

    del cfg["profiles"][current_profile]
    remaining = list(cfg["profiles"].keys())
    next_profile = remaining[0]
    profile_var.set(next_profile)
    profile_combo["values"] = remaining
    load_profile_into_ui(next_profile)
    cfg["current_profile"] = current_profile
    save_config(cfg)


def on_close():
    try:
        save_current_profile()
    finally:
        root.destroy()


root.protocol("WM_DELETE_WINDOW", on_close)

load_profile_into_ui(current_profile)

root.mainloop()
