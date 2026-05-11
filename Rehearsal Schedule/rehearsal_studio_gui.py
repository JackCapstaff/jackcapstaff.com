# rehearsal_studio_gui.py
# All-in-one GUI: Inputs (import or manual) -> Allocation -> Constraints (basic) -> Schedule (auto + editable) -> Export (PDF)
#
# Requires: pandas, numpy, tkinter (built-in), and your existing scripts present alongside this file:
#   1-Import-Time_per_work_per_rehearsal.py
#   2-Orchestration_organisation.py
#   3-Organised_rehearsal_with_time.py
#   4 - Final Compile and PDF.py

import os
import re
import json
import math
import subprocess
import importlib.util
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog


# =========================
# Paths / config
# =========================
SCRIPT1 = "1-Import-Time_per_work_per_rehearsal.py"
SCRIPT2 = "2-Orchestration_organisation.py"
SCRIPT3 = "3-Organised_rehearsal_with_time.py"
SCRIPT4 = "4 - Final Compile and PDF.py"

DEFAULT_G = 5  # minutes granularity

TIMED_XLSX_OUT = "timed_rehearsal.xlsx"   # for Script 4 compatibility
PROJECT_EXT = ".rehearsal_project.json"


# =========================
# Utilities
# =========================
def load_module_from_path(name: str, path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing required file: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def safe_int(x, default=0) -> int:
    try:
        if pd.isna(x):
            return default
        return int(float(x))
    except Exception:
        return default


def safe_float(x, default=0.0) -> float:
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def parse_truthy(x) -> bool:
    if pd.isna(x):
        return False
    s = str(x).strip().upper()
    return s in {"Y", "YES", "TRUE", "T", "1"}


def hhmm_from_minutes(m: int) -> str:
    m = int(m)
    h = (m // 60) % 24
    mm = m % 60
    return f"{h:02d}:{mm:02d}"


def minutes_from_timecell(val) -> Optional[int]:
    """Robust minutes since midnight for strings like 19:15 / 7:15 PM / 19.15 / 1915; numeric excel fractions; datetime/time."""
    if pd.isna(val):
        return None

    # python/pandas time-like
    if hasattr(val, "hour") and hasattr(val, "minute"):
        try:
            return int(val.hour) * 60 + int(val.minute)
        except Exception:
            pass

    s = str(val).strip()
    s = s.replace("：", ":")
    # "19 15" -> "19:15"
    if re.match(r"^\d{1,2}\s+\d{2}(\s*(AM|PM|am|pm))?$", s):
        s = s.replace(" ", ":")

    # "19.15" -> "19:15"
    if re.match(r"^\d{1,2}\.\d{2}(\s*(AM|PM|am|pm))?$", s):
        s = s.replace(".", ":")

    # HH:MM with optional AM/PM
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", s)
    if m:
        hh = int(m.group(1)) % 24
        mm = int(m.group(2))
        if re.search(r"pm$", s, flags=re.I) and hh < 12:
            hh += 12
        if re.search(r"am$", s, flags=re.I) and hh == 12:
            hh = 0
        return hh * 60 + mm

    # HHMM
    m = re.fullmatch(r"^(\d{3,4})$", s)
    if m:
        v = int(m.group(1))
        hh = v // 100
        mm = v % 100
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh * 60 + mm

    # numeric: excel fraction or raw minutes
    try:
        f = float(s)
        if 0 <= f < 1:
            return int(round(f * 24 * 60))
        if 1 <= f < 24 * 60 + 1:
            return int(round(f))
    except Exception:
        pass

    # pandas parse fallback
    t = pd.to_datetime(s, errors="coerce")
    if pd.notna(t):
        return int(t.hour) * 60 + int(t.minute)

    return None


def parse_break_minutes(val) -> int:
    if pd.isna(val):
        return 0
    # numeric minutes
    num = pd.to_numeric(val, errors="coerce")
    if pd.notna(num):
        return int(round(float(num)))
    # time-like cell
    t = pd.to_datetime(val, errors="coerce")
    if pd.notna(t):
        return int(t.hour) * 60 + int(t.minute)
    # "00:20"
    s = str(val).strip()
    if ":" in s:
        p = s.split(":")
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            return int(p[0]) * 60 + int(p[1])
    return 0

def choose_break_offset_favor_longer_first_half(durations: list[int]) -> int:
    """
    Choose a break offset (minutes) strictly at an internal boundary between items,
    minimising |left - right|. If tied, prefer the LATER boundary (slightly longer first half).
    """
    if not durations:
        return 0

    boundaries = [0]
    for m in durations:
        boundaries.append(boundaries[-1] + int(m))
    total = boundaries[-1]

    # If only one item, no internal boundary
    if len(boundaries) <= 2:
        return 0

    ideal = total / 2.0
    candidates = range(1, len(boundaries) - 1)  # internal only

    # primary: closest to ideal
    # secondary: prefer later (b >= ideal) when tied
    def key(i: int):
        b = boundaries[i]
        return (abs(2*b - total), 0 if b >= ideal else 1, -b)

    best_idx = min(candidates, key=key)
    return int(boundaries[best_idx])



# =========================
# Editable Treeview helper
# =========================
class EditableTree(ttk.Treeview):
    def __init__(self, master, columns, **kwargs):
        super().__init__(master, columns=columns, show="headings", **kwargs)
        self._entry = None
        self._edit_col = None
        self._edit_item = None
        self.bind("<Double-1>", self._begin_edit)

    def _begin_edit(self, event):
        region = self.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.identify_column(event.x)  # '#1', '#2', ...
        item = self.identify_row(event.y)
        if not item:
            return

        col_index = int(col.replace("#", "")) - 1
        x, y, w, h = self.bbox(item, col)

        value = self.item(item, "values")[col_index]

        # Create entry
        if self._entry is not None:
            self._entry.destroy()

        self._edit_col = col_index
        self._edit_item = item

        self._entry = ttk.Entry(self)
        self._entry.place(x=x, y=y, width=w, height=h)
        self._entry.insert(0, str(value))
        self._entry.focus_set()

        self._entry.bind("<Return>", self._save_edit)
        self._entry.bind("<Escape>", lambda _e: self._cancel_edit())
        self._entry.bind("<FocusOut>", self._save_edit)

    def _cancel_edit(self):
        if self._entry is not None:
            self._entry.destroy()
        self._entry = None
        self._edit_col = None
        self._edit_item = None

    def _save_edit(self, _event=None):
        if self._entry is None or self._edit_item is None:
            return
        new_val = self._entry.get()
        vals = list(self.item(self._edit_item, "values"))
        vals[self._edit_col] = new_val
        self.item(self._edit_item, values=vals)
        self._cancel_edit()


# =========================
# Core ordering logic (your “big forces first, decreasing, similarity second”)
# =========================
@dataclass
class Bundle:
    key: str
    items: pd.DataFrame  # rows for this bundle (movements)
    mins: int
    playerload: float
    sig: Dict[str, int]


def build_bundles_for_rehearsal(schedule_df: pd.DataFrame, sig_map: Dict[str, Dict[str, int]]) -> List[Bundle]:
    bundles: List[Bundle] = []
    for gk, grp in schedule_df.groupby("GroupKey", sort=False):
        grp2 = grp.copy()
        # MovementOrder sort inside the bundle (keep movements together)
        if "MovementOrder" in grp2.columns:
            grp2["MovementOrder"] = pd.to_numeric(grp2["MovementOrder"], errors="coerce")
            grp2 = grp2.sort_values(["MovementOrder", "Title"], na_position="last")
        mins = int(pd.to_numeric(grp2["Rehearsal Time (minutes)"], errors="coerce").fillna(0).sum())
        playerload = float(pd.to_numeric(grp2["PlayerLoad"], errors="coerce").fillna(0).max())
        # signature: use max over titles in bundle
        sig = {"Percs": 0, "PercProfile": 0, "Piano": 0, "Harp": 0, "Winds": 0, "Brass": 0, "Strings": 0}
        for t in grp2["Title"].astype(str).tolist():
            s = sig_map.get(t)
            if not s:
                continue
            for k in sig.keys():
                sig[k] = max(int(sig[k]), int(s.get(k, 0)))
        bundles.append(Bundle(key=str(gk), items=grp2, mins=mins, playerload=playerload, sig=sig))
    return bundles


def order_bundles_descending_load_with_similarity(
    bundles: List[Bundle],
    transition_cost_fn,
    increase_penalty_weight: float = 100.0
) -> List[Bundle]:
    """
    Primary objective: keep load decreasing (big forces early).
    Secondary: minimise transition costs between adjacent bundles.
    Tertiary: prefer higher load and longer mins.
    """
    if not bundles:
        return []

    remaining = bundles[:]

    # seed = highest load (then minutes)
    remaining.sort(key=lambda b: (b.playerload, b.mins), reverse=True)
    ordered = [remaining.pop(0)]

    while remaining:
        last = ordered[-1]
        last_load = last.playerload

        best_i = 0
        best_key = None

        for i, cand in enumerate(remaining):
            # penalise moving "up" in load (we want generally decreasing)
            inc = max(0.0, cand.playerload - last_load)
            inc_pen = inc * increase_penalty_weight

            tc = transition_cost_fn(last.sig, cand.sig)
            # tie-breakers: prefer higher load (still), then longer mins
            key = (inc_pen, tc, -cand.playerload, -cand.mins)

            if best_key is None or key < best_key:
                best_key = key
                best_i = i

        ordered.append(remaining.pop(best_i))

    return ordered


# =========================
# Main App
# =========================
class RehearsalStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Rehearsal Studio")
        self.geometry("1200x760")

        # Load your existing scripts as modules (we reuse their logic)
        self.mod1 = load_module_from_path("script1", SCRIPT1)
        self.mod2 = load_module_from_path("script2", SCRIPT2)
        self.mod3 = load_module_from_path("script3", SCRIPT3)

        # State
        self.G = DEFAULT_G
        self.works_df = self._default_works_df()
        self.rehearsals_df = self._default_rehearsals_df()
        self.allocation_df = pd.DataFrame()
        self.warnings: List[str] = []
        self.schedule_df = pd.DataFrame()
        self.timed_df = pd.DataFrame()

        # For schedule editing
        self.current_rehearsal: Optional[int] = None
        self._drag_item = None

        self._build_ui()
        self._refresh_inputs_tables()

    # -------------------------
    # Defaults
    # -------------------------
    def _default_works_df(self) -> pd.DataFrame:
        # Keep canonical columns so Script 2’s column resolver works
        cols = [
            "Title", "Duration", "Difficulty",
            "Flute", "Oboe", "Clarinet", "Bassoon",
            "Horn", "Trumpet", "Trombone", "Tuba",
            "Violin 1", "Violin 2", "Viola", "Cello", "Bass",
            "Percussion", "Timpani",
            "Piano", "Harp",
            "Soloist",
        ]
        return pd.DataFrame(columns=cols)

    def _default_rehearsals_df(self) -> pd.DataFrame:
        cols = ["Rehearsal", "Date", "Start Time", "End Time", "Break", "Percs", "Piano", "Harp", "Brass", "Soloist"]
        return pd.DataFrame(columns=cols)

    # -------------------------
    # UI
    # -------------------------
    def _build_ui(self):
        # Top toolbar
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="New Project", command=self.new_project).pack(side=tk.LEFT)
        ttk.Button(top, text="Open Project", command=self.open_project).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(top, text="Save Project", command=self.save_project).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Separator(top, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(top, text="Import User_Input_1.xlsx", command=self.import_user_input_xlsx).pack(side=tk.LEFT)
        ttk.Button(top, text="Export Inputs to Excel (optional)", command=self.export_inputs_xlsx).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Separator(top, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(top, text="Run Allocation", command=self.run_allocation).pack(side=tk.LEFT)
        ttk.Button(top, text="Generate Schedule", command=self.generate_schedule).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(top, text="Recompute Times", command=self.recompute_times).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Separator(top, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(top, text="Export timed_rehearsal.xlsx", command=self.export_timed_xlsx).pack(side=tk.LEFT)
        ttk.Button(top, text="Export PDF (run Script 4)", command=self.export_pdf_via_script4).pack(side=tk.LEFT, padx=(6, 0))

        # Notebook
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True)

        self.tab_inputs = ttk.Frame(self.nb, padding=10)
        self.tab_alloc = ttk.Frame(self.nb, padding=10)
        self.tab_constraints = ttk.Frame(self.nb, padding=10)
        self.tab_schedule = ttk.Frame(self.nb, padding=10)

        self.nb.add(self.tab_inputs, text="1) Inputs")
        self.nb.add(self.tab_alloc, text="2) Allocation / Totals")
        self.nb.add(self.tab_constraints, text="3) Requirements")
        self.nb.add(self.tab_schedule, text="4) Schedule (Auto + Edit)")

        self._build_inputs_tab()
        self._build_alloc_tab()
        self._build_constraints_tab()
        self._build_schedule_tab()

        # Status bar
        self.status = tk.StringVar(value="Ready.")
        bar = ttk.Label(self, textvariable=self.status, relief=tk.SUNKEN, anchor="w", padding=6)
        bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _build_inputs_tab(self):
        pan = ttk.PanedWindow(self.tab_inputs, orient=tk.HORIZONTAL)
        pan.pack(fill=tk.BOTH, expand=True)

        # Works
        lf = ttk.Labelframe(pan, text="Works (double-click to edit)", padding=8)
        pan.add(lf, weight=3)

        work_cols = list(self.works_df.columns)
        self.works_tree = EditableTree(lf, columns=work_cols, height=18)
        for c in work_cols:
            self.works_tree.heading(c, text=c)
            self.works_tree.column(c, width=110 if c != "Title" else 240)
        self.works_tree.pack(fill=tk.BOTH, expand=True)

        wbtn = ttk.Frame(lf)
        wbtn.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(wbtn, text="Add Work", command=self.add_work).pack(side=tk.LEFT)
        ttk.Button(wbtn, text="Remove Selected", command=self.remove_selected_work).pack(side=tk.LEFT, padx=(6, 0))

        # Rehearsals
        rf = ttk.Labelframe(pan, text="Rehearsals (double-click to edit)", padding=8)
        pan.add(rf, weight=2)

        reh_cols = list(self.rehearsals_df.columns)
        self.reh_tree = EditableTree(rf, columns=reh_cols, height=18)
        for c in reh_cols:
            self.reh_tree.heading(c, text=c)
            self.reh_tree.column(c, width=120 if c not in {"Date"} else 130)
        self.reh_tree.pack(fill=tk.BOTH, expand=True)

        rbtn = ttk.Frame(rf)
        rbtn.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(rbtn, text="Add Rehearsal", command=self.add_rehearsal).pack(side=tk.LEFT)
        ttk.Button(rbtn, text="Remove Selected", command=self.remove_selected_rehearsal).pack(side=tk.LEFT, padx=(6, 0))

    def _build_alloc_tab(self):
        top = ttk.Frame(self.tab_alloc)
        top.pack(fill=tk.BOTH, expand=True)

        ttk.Label(top, text="Allocation output (in-app).").pack(anchor="w")

        self.alloc_text = tk.Text(top, height=8)
        self.alloc_text.pack(fill=tk.X, pady=(6, 10))

        self.alloc_table_frame = ttk.Frame(top)
        self.alloc_table_frame.pack(fill=tk.BOTH, expand=True)

        self.alloc_tree = None

    def _build_constraints_tab(self):
        # MVP: constraints are just rehearsal availability flags (Percs/Piano/Harp/Brass/Soloist) already in Rehearsals table.
        # This tab is a place for future richer constraints.
        msg = (
            "MVP: Requirements are currently driven by the rehearsal availability flags in the Rehearsals table\n"
            "(Percs / Piano / Harp / Brass / Soloist).\n\n"
            "Next upgrade: add explicit constraints like:\n"
            "• Work X must be on rehearsal N\n"
            "• Soloist-only rehearsals\n"
            "• No percussion on date …\n"
        )
        ttk.Label(self.tab_constraints, text=msg, justify="left").pack(anchor="nw")

    def _build_schedule_tab(self):
        pan = ttk.PanedWindow(self.tab_schedule, orient=tk.HORIZONTAL)
        pan.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(pan)
        pan.add(left, weight=1)

        right = ttk.Frame(pan)
        pan.add(right, weight=3)

        ttk.Label(left, text="Rehearsals").pack(anchor="w")
        self.reh_list = tk.Listbox(left, height=25)
        self.reh_list.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.reh_list.bind("<<ListboxSelect>>", self.on_select_schedule_rehearsal)

        btns = ttk.Frame(left)
        btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text="Add Item", command=self.add_schedule_item).pack(fill=tk.X)
        ttk.Button(btns, text="Edit Minutes", command=self.edit_schedule_minutes).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="Remove Item", command=self.remove_schedule_item).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="Move to…", command=self.move_schedule_item).pack(fill=tk.X, pady=(6, 0))

        ttk.Label(right, text="Schedule items (drag to reorder within rehearsal)").pack(anchor="w")

        cols = ("Title", "Minutes", "GroupKey", "PlayerLoad")
        self.schedule_tree = ttk.Treeview(right, columns=cols, show="headings", height=22)
        for c in cols:
            self.schedule_tree.heading(c, text=c)
        self.schedule_tree.column("Title", width=520)
        self.schedule_tree.column("Minutes", width=80, anchor="center")
        self.schedule_tree.column("GroupKey", width=260)
        self.schedule_tree.column("PlayerLoad", width=100, anchor="center")
        self.schedule_tree.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        # Drag/drop reorder
        self.schedule_tree.bind("<ButtonPress-1>", self.on_drag_start)
        self.schedule_tree.bind("<B1-Motion>", self.on_drag_motion)
        self.schedule_tree.bind("<ButtonRelease-1>", self.on_drag_drop)

    # -------------------------
    # Project IO
    # -------------------------
    def new_project(self):
        if messagebox.askyesno("New project", "Discard current project state and start fresh?"):
            self.works_df = self._default_works_df()
            self.rehearsals_df = self._default_rehearsals_df()
            self.allocation_df = pd.DataFrame()
            self.schedule_df = pd.DataFrame()
            self.timed_df = pd.DataFrame()
            self.warnings = []
            self._refresh_inputs_tables()
            self._refresh_allocation_view()
            self._refresh_schedule_rehearsal_list()
            self.status.set("New project created.")

    def save_project(self):
        path = filedialog.asksaveasfilename(
            title="Save project",
            defaultextension=PROJECT_EXT,
            filetypes=[("Rehearsal Studio Project", f"*{PROJECT_EXT}")],
        )
        if not path:
            return
        self._sync_inputs_from_tables()

        payload = {
            "G": self.G,
            "works": self.works_df.to_dict(orient="records"),
            "rehearsals": self.rehearsals_df.to_dict(orient="records"),
            "allocation": self.allocation_df.to_dict(orient="records") if not self.allocation_df.empty else [],
            "schedule": self.schedule_df.to_dict(orient="records") if not self.schedule_df.empty else [],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self.status.set(f"Saved project: {os.path.basename(path)}")

    def open_project(self):
        path = filedialog.askopenfilename(
            title="Open project",
            filetypes=[("Rehearsal Studio Project", f"*{PROJECT_EXT}")],
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        self.G = int(payload.get("G", DEFAULT_G))
        self.works_df = pd.DataFrame(payload.get("works", [])).reindex(columns=self._default_works_df().columns, fill_value="")
        self.rehearsals_df = pd.DataFrame(payload.get("rehearsals", [])).reindex(columns=self._default_rehearsals_df().columns, fill_value="")
        self.allocation_df = pd.DataFrame(payload.get("allocation", []))
        self.schedule_df = pd.DataFrame(payload.get("schedule", []))

        self._refresh_inputs_tables()
        self._refresh_allocation_view()
        self._refresh_schedule_rehearsal_list()
        self.status.set(f"Opened project: {os.path.basename(path)}")

    # -------------------------
    # Import / export inputs
    # -------------------------
    def import_user_input_xlsx(self):
        path = filedialog.askopenfilename(
            title="Select User_Input_1.xlsx",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not path:
            return
        try:
            works = pd.read_excel(path, sheet_name="Works")
            rehe = pd.read_excel(path, sheet_name="Rehearsals")
        except Exception as e:
            messagebox.showerror("Import error", f"Failed to read Excel:\n{e}")
            return

        # Coerce into our canonical columns where possible
        w = self._default_works_df().copy()
        wcols = set(w.columns)
        for c in works.columns:
            if c in wcols:
                w[c] = works[c]
        # ensure required
        if "Title" not in w.columns or w["Title"].isna().all():
            if "Title" in works.columns:
                w["Title"] = works["Title"]
        w = w[w["Title"].notna()].copy()

        r = self._default_rehearsals_df().copy()
        rcols = set(r.columns)
        for c in rehe.columns:
            if c in rcols:
                r[c] = rehe[c]
        if "Rehearsal" in rehe.columns:
            r["Rehearsal"] = rehe["Rehearsal"]
        r = r[r["Rehearsal"].notna()].copy()

        self.works_df = w.reset_index(drop=True)
        self.rehearsals_df = r.reset_index(drop=True)

        self._refresh_inputs_tables()
        self.status.set(f"Imported inputs from: {os.path.basename(path)}")

    def export_inputs_xlsx(self):
        self._sync_inputs_from_tables()
        path = filedialog.asksaveasfilename(
            title="Export Inputs (optional)",
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not path:
            return
        try:
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                self.works_df.to_excel(writer, sheet_name="Works", index=False)
                self.rehearsals_df.to_excel(writer, sheet_name="Rehearsals", index=False)
        except Exception as e:
            messagebox.showerror("Export error", f"Failed writing Excel:\n{e}")
            return
        self.status.set(f"Exported inputs: {os.path.basename(path)}")

    # -------------------------
    # Inputs table sync
    # -------------------------
    def _refresh_inputs_tables(self):
        # Works
        self.works_tree.delete(*self.works_tree.get_children())
        for i, row in self.works_df.fillna("").iterrows():
            self.works_tree.insert("", tk.END, iid=f"w{i}", values=[row.get(c, "") for c in self.works_df.columns])

        # Rehearsals
        self.reh_tree.delete(*self.reh_tree.get_children())
        for i, row in self.rehearsals_df.fillna("").iterrows():
            self.reh_tree.insert("", tk.END, iid=f"r{i}", values=[row.get(c, "") for c in self.rehearsals_df.columns])

    def _sync_inputs_from_tables(self):
        # Works
        rows = []
        for iid in self.works_tree.get_children():
            vals = self.works_tree.item(iid, "values")
            rows.append(dict(zip(self.works_df.columns, vals)))
        self.works_df = pd.DataFrame(rows).reindex(columns=self._default_works_df().columns, fill_value="")

        # Rehearsals
        rows = []
        for iid in self.reh_tree.get_children():
            vals = self.reh_tree.item(iid, "values")
            rows.append(dict(zip(self.rehearsals_df.columns, vals)))
        self.rehearsals_df = pd.DataFrame(rows).reindex(columns=self._default_rehearsals_df().columns, fill_value="")

    def add_work(self):
        self.works_tree.insert("", tk.END, values=["New Work", 0, 1] + [""] * (len(self.works_df.columns) - 3))

    def remove_selected_work(self):
        sel = self.works_tree.selection()
        for iid in sel:
            self.works_tree.delete(iid)

    def add_rehearsal(self):
        # Suggest next rehearsal number
        existing = []
        for iid in self.reh_tree.get_children():
            vals = self.reh_tree.item(iid, "values")
            existing.append(safe_int(vals[0], 0))
        nxt = max(existing) + 1 if existing else 1
        self.reh_tree.insert("", tk.END, values=[nxt, "", "19:00", "21:30", 20, "Y", "Y", "Y", "Y", "N"])

    def remove_selected_rehearsal(self):
        sel = self.reh_tree.selection()
        for iid in sel:
            self.reh_tree.delete(iid)

    # -------------------------
    # Allocation
    # -------------------------
    def _prepare_works_for_allocator(self) -> pd.DataFrame:
        w = self.works_df.copy()
        # numeric coercions
        w["Title"] = w["Title"].astype(str).str.strip()
        w = w[w["Title"].str.len() > 0].copy()

        w["Duration"] = pd.to_numeric(w["Duration"], errors="coerce").fillna(0.0)
        w["Difficulty"] = pd.to_numeric(w["Difficulty"], errors="coerce").fillna(1.0)

        # Script 1 expects duration_norm / difficulty_norm; reuse its helper to build those if available
        try:
            w2 = self.mod1.normalise_works_columns(w)
        except Exception:
            w2 = w.copy()
            w2["duration_norm"] = w2["Duration"].astype(float)
            w2["difficulty_norm"] = w2["Difficulty"].astype(float).clip(lower=0.1)

        # Ensure orchestration columns exist (allocator checks these by name)
        for c in (
            self.mod1.WIND_COLS + self.mod1.BRASS_COLS + self.mod1.STRING_COLS +
            self.mod1.PERC_COLS + self.mod1.PIANO_COLS + self.mod1.HARP_COLS + self.mod1.SOLOIST_COLS
        ):
            if c not in w2.columns:
                w2[c] = 0
        return w2.reset_index(drop=True)

    def _prepare_rehearsals_for_allocator(self) -> pd.DataFrame:
        r = self.rehearsals_df.copy()
        r = r[r["Rehearsal"].astype(str).str.strip().ne("")].copy()
        r["Rehearsal"] = pd.to_numeric(r["Rehearsal"], errors="coerce").astype("Int64")
        r = r[r["Rehearsal"].notna()].copy()

        # Date
        r["Date"] = pd.to_datetime(r.get("Date"), errors="coerce").dt.date

        # times -> duration
        start_m = r.get("Start Time")
        end_m = r.get("End Time")

        start_min = start_m.apply(minutes_from_timecell) if start_m is not None else pd.Series([None] * len(r))
        end_min = end_m.apply(minutes_from_timecell) if end_m is not None else pd.Series([None] * len(r))

        start_min = start_min.fillna(19 * 60).astype(int)
        end_min = end_min.fillna(21 * 60 + 30).astype(int)

        gross = (end_min - start_min).astype(int)
        gross = gross.where(gross >= 0, gross + 24 * 60)

        br = r.get("Break").apply(parse_break_minutes) if "Break" in r.columns else pd.Series([0] * len(r))
        r["Break (minutes)"] = br.astype(int)

        r["Duration"] = (gross - r["Break (minutes)"]).clip(lower=0).astype(int)

        # Specialist flags
        for c in ["Percs", "Piano", "Harp", "Brass", "Soloist"]:
            if c not in r.columns:
                r[c] = False
            r[c] = r[c].apply(parse_truthy)

        # Start DateTime used later
        hh = (start_min // 60).astype(int).astype(str).str.zfill(2)
        mm = (start_min % 60).astype(int).astype(str).str.zfill(2)
        hhmm = hh + ":" + mm
        date_str = np.where(pd.notna(pd.Series(r["Date"])), pd.Series(r["Date"]).astype(str), "2000-01-01")
        r["Start DateTime"] = pd.to_datetime(date_str + " " + hhmm, errors="coerce").fillna(pd.to_datetime("2000-01-01 19:00"))

        return r.sort_values("Rehearsal").reset_index(drop=True)

    def run_allocation(self):
        self._sync_inputs_from_tables()

        try:
            works = self._prepare_works_for_allocator()
            rehe = self._prepare_rehearsals_for_allocator()
        except Exception as e:
            messagebox.showerror("Inputs error", str(e))
            return

        if works.empty or rehe.empty:
            messagebox.showerror("Missing data", "Please provide at least 1 work and 2 rehearsals.")
            return
        if rehe["Rehearsal"].nunique() < 2:
            messagebox.showerror("Missing rehearsals", "Need at least two rehearsals to allocate (first/last).")
            return

        G = self.G

        # snapped total capacity
        tokens_per = (rehe["Duration"].astype(float) // G).astype(int)
        snapped_caps = (tokens_per * G).astype(int)
        snapped_total = int(snapped_caps.sum())
        if snapped_total <= 0:
            messagebox.showerror("Capacity error", "Total rehearsal capacity is 0 minutes after snapping. Check times/breaks.")
            return

        try:
            req = self.mod1.compute_required_minutes(works, snapped_total, G)
            export_df, warnings = self.mod1.allocate_across_rehearsals(works, rehe, req, G)
        except Exception as e:
            messagebox.showerror("Allocation failed", f"{e}")
            return

        self.allocation_df = export_df.copy()
        self.warnings = list(warnings or [])
        self._refresh_allocation_view()

        self.status.set(f"Allocation complete: {len(self.allocation_df)} rows, {len(self.warnings)} warning(s).")
        self.nb.select(self.tab_alloc)

    def _refresh_allocation_view(self):
        self.alloc_text.delete("1.0", tk.END)
        if self.warnings:
            self.alloc_text.insert(tk.END, "Warnings:\n" + "\n".join(self.warnings[:200]))
            if len(self.warnings) > 200:
                self.alloc_text.insert(tk.END, f"\n… ({len(self.warnings)-200} more)")
        else:
            self.alloc_text.insert(tk.END, "No warnings.")

        # Allocation table
        for w in self.alloc_table_frame.winfo_children():
            w.destroy()

        if self.allocation_df.empty:
            ttk.Label(self.alloc_table_frame, text="(No allocation yet)").pack(anchor="w")
            return

        cols = list(self.allocation_df.columns)
        self.alloc_tree = ttk.Treeview(self.alloc_table_frame, columns=cols, show="headings", height=18)
        for c in cols:
            self.alloc_tree.heading(c, text=c)
            self.alloc_tree.column(c, width=120 if c != "Title" else 260)
        self.alloc_tree.pack(fill=tk.BOTH, expand=True)

        df_show = self.allocation_df.fillna("")
        # Limit for UI responsiveness
        for i, row in df_show.head(300).iterrows():
            self.alloc_tree.insert("", tk.END, values=[row.get(c, "") for c in cols])
        if len(df_show) > 300:
            ttk.Label(self.alloc_table_frame, text=f"(Showing first 300 of {len(df_show)} rows)").pack(anchor="w")

    # -------------------------
    # Schedule generation
    # -------------------------
    def _build_signature_map(self, works_df: pd.DataFrame) -> Dict[str, Dict[str, int]]:
        sig_map: Dict[str, Dict[str, int]] = {}
        for _, r in works_df.iterrows():
            title = str(r.get("Title", "")).strip()
            if not title:
                continue
            try:
                sig = self.mod3.signature_for_work(r)
            except Exception:
                sig = {"Percs": 0, "PercProfile": 0, "Piano": 0, "Harp": 0, "Winds": 0, "Brass": 0, "Strings": 0}
            sig_map[title] = {k: int(sig.get(k, 0)) for k in ["Percs","PercProfile","Piano","Harp","Winds","Brass","Strings"]}
        return sig_map

    def _parse_group_and_movement(self, title: str, group_hint: Optional[str] = None) -> Tuple[str, Optional[str], Optional[int]]:
        # reuse script 2’s parser if available
        try:
            return self.mod2.parse_group_and_movement(title, group_hint)
        except Exception:
            s = str(title).strip()
            group = group_hint.strip() if group_hint else s.split(":")[0].strip()
            return group or s, None, None

    def _estimate_playerload_map(self, works_df: pd.DataFrame) -> Dict[str, float]:
        # Use script 2’s player-load estimate (section-weighted)
        groups = self.mod2.gather_resolved_groups(works_df)
        out = {}
        works_indexed = works_df.set_index("Title", drop=False)
        for t, wr in works_indexed.groupby(level=0):
            row = wr.iloc[0]
            out[str(t)] = float(self.mod2.estimate_player_load(row, groups))
        return out

    def _group_hint_map(self, works_df: pd.DataFrame) -> Dict[str, Optional[str]]:
        # If the Works sheet has a group/parent column, use it as hint (script 2 aliases)
        hint = {}
        try:
            group_col = self.mod2._first_matching_col(works_df, self.mod2.GROUP_ALIASES)
        except Exception:
            group_col = None
        if group_col and group_col in works_df.columns:
            for _, r in works_df.iterrows():
                t = str(r.get("Title", "")).strip()
                if not t:
                    continue
                v = r.get(group_col)
                hint[t] = str(v).strip() if pd.notna(v) and str(v).strip() else None
        return hint

    def generate_schedule(self):
        if self.allocation_df.empty:
            messagebox.showerror("No allocation", "Run Allocation first.")
            return
        self._sync_inputs_from_tables()

        works = self._prepare_works_for_allocator()
        rehe = self._prepare_rehearsals_for_allocator()

        # Build per-rehearsal rows from allocation matrix (ignore summary rows)
        alloc = self.allocation_df.copy()
        alloc = alloc[~alloc["Title"].astype(str).str.startswith("[Summary]")].copy()

        # Identify rehearsal columns
        reh_cols = [c for c in alloc.columns if str(c).strip().startswith("Rehearsal ")]
        col_to_num = {}
        for c in reh_cols:
            try:
                col_to_num[c] = int(str(c).split()[-1])
            except Exception:
                pass
        if not col_to_num:
            messagebox.showerror("Allocation format", "No 'Rehearsal N' columns found in allocation output.")
            return

        playerload_map = self._estimate_playerload_map(works)
        group_hint_map = self._group_hint_map(works)
        sig_map = self._build_signature_map(works)

        rows = []
        for _, r in alloc.iterrows():
            title = str(r.get("Title", "")).strip()
            if not title:
                continue

            for col, rnum in col_to_num.items():
                mins = pd.to_numeric(r.get(col, 0), errors="coerce")
                if pd.isna(mins) or mins <= 0:
                    continue

                group_hint = group_hint_map.get(title)
                group_title, mov_label, mov_ord = self._parse_group_and_movement(title, group_hint)

                rows.append({
                    "Rehearsal": int(rnum),
                    "Title": title,
                    "Rehearsal Time (minutes)": int(round(float(mins))),
                    # IMPORTANT: use Script 3’s expected names
                    "PlayerLoad": float(playerload_map.get(title, 0.0)),
                    "GroupKey": str(group_title),
                    "MovementOrder": mov_ord if mov_ord is not None else np.nan,
                })

        if not rows:
            messagebox.showerror("Schedule", "No scheduled minutes found to build a schedule.")
            return

        sched = pd.DataFrame(rows)
        sched["Rehearsal"] = pd.to_numeric(sched["Rehearsal"], errors="coerce").astype("Int64")
        sched["MovementOrder"] = pd.to_numeric(sched["MovementOrder"], errors="coerce")

        # Order each rehearsal with your desired heuristic
        ordered_rows = []
        for rnum, df_r in sched.groupby("Rehearsal", sort=True):
            bundles = build_bundles_for_rehearsal(df_r, sig_map)
            ordered_bundles = order_bundles_descending_load_with_similarity(
                bundles,
                transition_cost_fn=self.mod3.transition_cost,
                increase_penalty_weight=100.0
            )
            for b in ordered_bundles:
                ordered_rows.append(b.items)

        sched2 = pd.concat(ordered_rows, ignore_index=True)
        self.schedule_df = sched2.copy()

        # Build timed schedule immediately
        self.timed_df = self._compute_timed_df(self.schedule_df, rehe)

        self._refresh_schedule_rehearsal_list()
        self.status.set("Schedule generated (auto-ordered). You can now edit it.")
        self.nb.select(self.tab_schedule)

    # -------------------------
    # Schedule tab views + editing
    # -------------------------
    def _refresh_schedule_rehearsal_list(self):
        self.reh_list.delete(0, tk.END)
        if self.schedule_df.empty:
            return
        rehs = sorted([int(x) for x in self.schedule_df["Rehearsal"].dropna().unique()])
        for r in rehs:
            self.reh_list.insert(tk.END, str(r))
        if rehs:
            self.reh_list.selection_set(0)
            self.current_rehearsal = rehs[0]
            self._load_schedule_tree_for_rehearsal(rehs[0])

    def on_select_schedule_rehearsal(self, _evt=None):
        sel = self.reh_list.curselection()
        if not sel:
            return
        r = int(self.reh_list.get(sel[0]))
        self.current_rehearsal = r
        self._load_schedule_tree_for_rehearsal(r)

    def _load_schedule_tree_for_rehearsal(self, r: int):
        self.schedule_tree.delete(*self.schedule_tree.get_children())
        block = self.schedule_df[self.schedule_df["Rehearsal"] == r].reset_index(drop=True)
        for i, row in block.iterrows():
            self.schedule_tree.insert(
                "", tk.END, iid=str(i),
                values=(
                    row["Title"],
                    int(row["Rehearsal Time (minutes)"]),
                    row.get("GroupKey", ""),
                    f"{float(row.get('PlayerLoad', 0.0)):.1f}",
                )
            )

    def _write_schedule_tree_back(self):
        """Write current rehearsal tree order back into schedule_df."""
        if self.current_rehearsal is None or self.schedule_df.empty:
            return
        r = self.current_rehearsal
        block = self.schedule_df[self.schedule_df["Rehearsal"] == r].copy()

        new_rows = []
        for iid in self.schedule_tree.get_children(""):
            title, mins, gk, pl = self.schedule_tree.item(iid, "values")
            mins_i = safe_int(mins, 0)
            pl_f = safe_float(pl, 0.0)

            # try to grab the first matching row for this title+groupkey
            hit = block[(block["Title"].astype(str) == str(title)) & (block["GroupKey"].astype(str) == str(gk))].head(1)
            if hit.empty:
                new_rows.append({
                    "Rehearsal": r,
                    "Title": str(title),
                    "Rehearsal Time (minutes)": mins_i,
                    "PlayerLoad": pl_f,
                    "GroupKey": str(gk) if str(gk).strip() else str(title),
                    "MovementOrder": np.nan,
                })
            else:
                row = hit.iloc[0].to_dict()
                row["Rehearsal Time (minutes)"] = mins_i
                row["PlayerLoad"] = pl_f
                row["GroupKey"] = str(gk) if str(gk).strip() else row.get("GroupKey", str(title))
                new_rows.append(row)
                block = block.drop(hit.index)

        new_block = pd.DataFrame(new_rows)
        others = self.schedule_df[self.schedule_df["Rehearsal"] != r].copy()
        self.schedule_df = pd.concat([others, new_block], ignore_index=True)

    # Drag/drop
    def on_drag_start(self, event):
        iid = self.schedule_tree.identify_row(event.y)
        self._drag_item = iid

    def on_drag_motion(self, event):
        if not self._drag_item:
            return
        target = self.schedule_tree.identify_row(event.y)
        if target and target != self._drag_item:
            self.schedule_tree.move(self._drag_item, "", self.schedule_tree.index(target))

    def on_drag_drop(self, _event):
        if self._drag_item:
            self._write_schedule_tree_back()
        self._drag_item = None

    def _selected_schedule_item(self) -> Optional[str]:
        sel = self.schedule_tree.selection()
        return sel[0] if sel else None

    def add_schedule_item(self):
        if self.current_rehearsal is None:
            return
        title = simpledialog.askstring("Add item", "Title:")
        if not title:
            return
        mins = simpledialog.askinteger("Add item", "Minutes:", minvalue=1, maxvalue=600)
        if not mins:
            return
        gk = simpledialog.askstring("GroupKey", "GroupKey (leave blank = same as Title):")
        gk = gk.strip() if gk else title.strip()

        # crude playerload (unknown)
        self.schedule_tree.insert("", tk.END, values=(title.strip(), int(mins), gk, "0.0"))
        self._write_schedule_tree_back()
        self.recompute_times()

    def edit_schedule_minutes(self):
        iid = self._selected_schedule_item()
        if not iid:
            return
        title, mins, gk, pl = self.schedule_tree.item(iid, "values")
        new_mins = simpledialog.askinteger("Edit minutes", f"Minutes for:\n{title}", initialvalue=safe_int(mins, 0), minvalue=1, maxvalue=600)
        if not new_mins:
            return
        self.schedule_tree.item(iid, values=(title, int(new_mins), gk, pl))
        self._write_schedule_tree_back()
        self.recompute_times()

    def remove_schedule_item(self):
        iid = self._selected_schedule_item()
        if not iid:
            return
        self.schedule_tree.delete(iid)
        self._write_schedule_tree_back()
        self.recompute_times()

    def move_schedule_item(self):
        if self.current_rehearsal is None:
            return
        iid = self._selected_schedule_item()
        if not iid:
            return

        title, mins, gk, pl = self.schedule_tree.item(iid, "values")
        all_rehs = sorted([int(x) for x in self.schedule_df["Rehearsal"].dropna().unique()]) if not self.schedule_df.empty else []
        if not all_rehs:
            return

        target = simpledialog.askinteger("Move item", f"Move to rehearsal:\nAvailable: {all_rehs}", minvalue=min(all_rehs), maxvalue=max(all_rehs))
        if not target:
            return

        # remove from current rehearsal tree
        self.schedule_tree.delete(iid)
        self._write_schedule_tree_back()

        # append to schedule_df for target
        new_row = pd.DataFrame([{
            "Rehearsal": int(target),
            "Title": str(title),
            "Rehearsal Time (minutes)": safe_int(mins, 0),
            "PlayerLoad": safe_float(pl, 0.0),
            "GroupKey": str(gk),
            "MovementOrder": np.nan,
        }])
        self.schedule_df = pd.concat([self.schedule_df, new_row], ignore_index=True)

        self._refresh_schedule_rehearsal_list()
        self.recompute_times()
        self.status.set(f"Moved '{title}' -> rehearsal {target}")

    # -------------------------
    # Timing
    # -------------------------
    def _compute_timed_df(self, schedule_df: pd.DataFrame, rehearsals_prepared: pd.DataFrame) -> pd.DataFrame:
        """
        Produce a timed schedule compatible with Script 4.
        Inserts a single Break row at an internal boundary closest to halfway,
        favouring a slightly longer first half when an equal split isn't possible.
        """
        if schedule_df.empty:
            return pd.DataFrame()

        rehe = rehearsals_prepared.set_index("Rehearsal", drop=False)
        out_rows = []

        for rnum, df_r in schedule_df.groupby("Rehearsal", sort=True):
            rnum_i = int(rnum)
            if rnum_i not in rehe.index:
                continue

            rrow = rehe.loc[rnum_i]
            date = rrow.get("Date")
            start_dt = pd.to_datetime(rrow.get("Start DateTime"), errors="coerce")
            if pd.isna(start_dt):
                start_dt = pd.to_datetime("2000-01-01 19:00")

            break_mins = safe_int(rrow.get("Break (minutes)", 0), 0)

            # durations in minutes for each scheduled item (NO break row here)
            durs = [safe_int(x, 0) for x in df_r["Rehearsal Time (minutes)"].tolist()]
            durs = [d for d in durs if d > 0]

            # break offset is MINUTES from rehearsal start (must be at internal boundary)
            break_offset = 0
            if break_mins > 0 and len(durs) >= 2:
                break_offset = choose_break_offset_favor_longer_first_half(durs)

            # start offsets for each item (minutes before it starts)
            cum_before = [0]
            for m in durs[:-1]:
                cum_before.append(cum_before[-1] + m)

            elapsed = 0
            item_idx = 0

            # Iterate through original rows to keep titles aligned
            for _, item in df_r.iterrows():
                mins = safe_int(item["Rehearsal Time (minutes)"], 0)
                if mins <= 0:
                    continue

                # Insert break exactly when we hit the boundary offset (internal only)
                if break_mins > 0 and break_offset > 0 and elapsed == break_offset:
                    br_start = start_dt + pd.Timedelta(minutes=elapsed)
                    br_end = br_start + pd.Timedelta(minutes=break_mins)
                    out_rows.append({
                        "Rehearsal": rnum_i,
                        "Date": date,
                        "Title": "Break",
                        "Time in Rehearsal": br_start.strftime("%H:%M"),
                        "Break Start (HH:MM)": br_start.strftime("%H:%M"),
                        "Break End (HH:MM)": br_end.strftime("%H:%M"),
                    })
                    elapsed += break_mins  # shift everything after the break

                # Normal item row
                it_start = start_dt + pd.Timedelta(minutes=elapsed)
                out_rows.append({
                    "Rehearsal": rnum_i,
                    "Date": date,
                    "Title": str(item["Title"]),
                    "Time in Rehearsal": it_start.strftime("%H:%M"),
                    "Break Start (HH:MM)": "",
                    "Break End (HH:MM)": "",
                })

                elapsed += mins
                item_idx += 1

            # If break_offset somehow equals the total (shouldn’t happen with internal-only),
            # we intentionally do NOT put a break at the end.

        out = pd.DataFrame(out_rows)
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out["Rehearsal"] = pd.to_numeric(out["Rehearsal"], errors="coerce").astype("Int64")
        return out


    def recompute_times(self):
        if self.schedule_df.empty:
            return
        self._write_schedule_tree_back()
        rehe = self._prepare_rehearsals_for_allocator()
        self.timed_df = self._compute_timed_df(self.schedule_df, rehe)
        self.status.set("Times recomputed from current schedule edits.")

    # -------------------------
    # Export
    # -------------------------
    def export_timed_xlsx(self):
        if self.timed_df.empty:
            messagebox.showerror("Nothing to export", "No timed schedule yet. Generate Schedule first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export timed schedule Excel",
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not path:
            return
        try:
            self.timed_df.to_excel(path, index=False)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        self.status.set(f"Exported: {os.path.basename(path)}")

    def export_pdf_via_script4(self):
        if self.timed_df.empty:
            messagebox.showerror("No schedule", "Generate Schedule first.")
            return
        # Write the expected file for Script 4
        try:
            self.timed_df.to_excel(TIMED_XLSX_OUT, index=False)
        except Exception as e:
            messagebox.showerror("Write failed", f"Couldn't write {TIMED_XLSX_OUT}:\n{e}")
            return

        if not os.path.exists(SCRIPT4):
            messagebox.showerror("Missing Script 4", f"Can't find {SCRIPT4} in this folder.")
            return

        try:
            subprocess.check_call(["python", SCRIPT4])
        except subprocess.CalledProcessError as e:
            messagebox.showerror("PDF export failed", str(e))
            return

        self.status.set("PDF generated via Script 4 (DCO_Rehearsal_Schedule.pdf).")


if __name__ == "__main__":
    app = RehearsalStudio()
    app.mainloop()
