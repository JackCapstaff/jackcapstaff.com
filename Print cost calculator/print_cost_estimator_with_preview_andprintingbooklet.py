
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
import PyPDF2
import math
import os
import platform
import subprocess
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4, A3, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from subprocess import call
import win32api
import win32print
import json
import re


class PDFSheetEstimatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Print Cost Estimator")

        self.pdf_data = []
        self.accepted_formats = set()  # Track accepted print formats

        self.automation_file = "automation_defaults.json"
        self.automation_rules = self.load_automation_defaults()


        # Cost settings (rest of your cost settings)
        self.cost_a4 = tk.DoubleVar(value=0.05)     # base sheet A4
        self.cost_a3 = tk.DoubleVar(value=0.12)     # base sheet A3
        self.ink_cost_a4 = tk.DoubleVar(value=0.03) # consumables A4
        self.ink_cost_a3 = tk.DoubleVar(value=0.035) # consumables A3
        self.photo_paper_surcharge = tk.DoubleVar(value=0.15)
        self.acetate_cost = tk.DoubleVar(value=0.60)  # slightly higher

        # --- Labour & margin ---
        self.labour_per_job = tk.DoubleVar(value=0.25)     # applied per copy
        self.markup_multiplier = tk.DoubleVar(value=1.25)  # 30% margin 

        # Extra labour by binding type (per copy). Tune these to taste.
        self.binding_labour = {
            "None": tk.DoubleVar(value=0.00),
            "Staple": tk.DoubleVar(value=0.30),
            "Plastic Comb": tk.DoubleVar(value=1.00),
            "Wire Comb": tk.DoubleVar(value=2.50),
        }

        # Paper grades: per-sheet surcharge by size
        self.paper_grade_surcharge = {
            "80gsm":   {"A4": tk.DoubleVar(value=0.00), "A3": tk.DoubleVar(value=0.00)},
            "100gsm":  {"A4": tk.DoubleVar(value=0.01), "A3": tk.DoubleVar(value=0.02)},
            "110gsm":  {"A4": tk.DoubleVar(value=0.02), "A3": tk.DoubleVar(value=0.04)},
            "120gsm":  {"A4": tk.DoubleVar(value=0.03), "A3": tk.DoubleVar(value=0.06)},
        }
        self.default_paper_grade = tk.StringVar(value=next(iter(self.paper_grade_surcharge.keys())))




        self.printer_name = tk.StringVar()

        self.binding_costs = {
            "None": tk.DoubleVar(value=0.00),
            "Staple": tk.DoubleVar(value=0.10),
            "Plastic Comb": tk.DoubleVar(value=0.40),
            "Wire Comb": tk.DoubleVar(value=0.60)
        }

        self.bw_cover_costs = {
            "Card 300gsm": tk.DoubleVar(value=1.10),
            "Card 450gsm": tk.DoubleVar(value=1.15),
            "Card 600gsm": tk.DoubleVar(value=1.20)
        }
        self.colour_cover_costs = {
            "Card 300gsm": tk.DoubleVar(value=1.20),
            "Card 450gsm": tk.DoubleVar(value=1.30),
            "Card 600gsm": tk.DoubleVar(value=1.40)
        }

        self.presets_file = "print_presets.json"
        self.default_styles = self.load_presets()

        self.keyword_variants = {
            "Piccolo": [r"piccolo", r"ottavino"],
            "Flute": [r"flute", r"flûte", r"flauto", r"querfl[oö]te", r"alto\s+flute", r"bass\s+flute"],
            "Oboe": [r"oboe", r"hautbois"],
            "English Horn": [r"english\s*horn", r"cor\s+anglais", r"corno\s+inglese", r"englischhorn"],
            "Clarinet": [r"clarinet", r"clarinette", r"clarinetto", r"klarinette"],
            "Bass Clarinet": [r"bass\s+clarinet", r"clarinette\s+basse", r"clarinetto\s+basso", r"bass?klarinette"],
            "Bassoon": [r"bassoon", r"basson", r"fagott[io]?"],
            "Contrabassoon": [r"contra?bassoon", r"contre?basson", r"contrafagotto", r"kontrafagott"],
            "Saxophone": [r"sax(?:ophone|ophon|ofono)\b(?:\s*(alto|tenor|baritone|soprano))?"],
            "Horn": [r"horns?", r"cor(?!(?:\s+anglais))\b", r"corni", r"corno\b", r"h[oö]rner"],
            "Trumpet": [r"trumpet", r"trompette", r"tromba", r"trombe", r"trompete"],
            "Cornet": [r"cornet"],
            "Flugelhorn": [r"fl[uü]gelhorn", r"flicorno"],
            "Trombone": [r"trombone", r"posaune", r"tenor\s+trombone", r"bass\s+trombone"],
            "Tuba": [r"tuba"],
            "Timpani": [r"timpani", r"timpano", r"timbales", r"pauken"],
            "Percussion": [r"percussion", r"schlagzeug", r"batteria"],
            "Harp": [r"harp", r"harfe", r"arpa"],
            "Piano": [r"piano", r"klavier"],
            "Violin": [r"violin", r"violon\b", r"violino", r"geige", r"violine"],
            "Viola": [r"viola", r"alto\b", r"bratsche"],
            "Cello": [r"cello", r"violoncello", r"violoncelle"],
            "Double Bass": [r"(double|contra)\s*bass", r"contrabbasso", r"contrebasse", r"kontrabass"],
        }

        # Lowercased lookup for convenience
        self._variants_lc = {k.lower(): v for k, v in self.keyword_variants.items()}

        # Build regex matchers for the current automation rules
        self._rule_patterns = {}
        self.build_keyword_matchers()

        self.build_gui()

    def ask_default_paper_grade(self):
        """Modal dropdown dialog to choose a default paper grade for this import."""
        grades = list(self.paper_grade_surcharge.keys())
        initial = self.default_paper_grade.get() if self.default_paper_grade.get() in grades else grades[0]

        top = tk.Toplevel(self.root)
        top.title("Default Paper Grade")
        top.transient(self.root)
        top.grab_set()  # modal

        # Center on parent
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() // 2) - 140
        y = self.root.winfo_rooty() + (self.root.winfo_height() // 2) - 60
        top.geometry(f"+{x}+{y}")

        tk.Label(top, text="Choose default paper grade for this import:").pack(padx=12, pady=(12,4), anchor="w")

        choice = tk.StringVar(value=initial)
        cb = ttk.Combobox(top, textvariable=choice, values=grades, state="readonly", width=18)
        cb.pack(padx=12, pady=(0,12), anchor="w")
        cb.focus_set()

        btns = tk.Frame(top)
        btns.pack(padx=12, pady=(0,12), anchor="e")

        selected = {"value": initial}  # closure container

        def _ok():
            selected["value"] = choice.get() if choice.get() in grades else initial
            top.destroy()

        def _cancel():
            # keep previous default if user cancels
            selected["value"] = self.default_paper_grade.get() if self.default_paper_grade.get() in grades else initial
            top.destroy()

        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="right", padx=(0,6))
        ttk.Button(btns, text="OK", command=_ok).pack(side="right")

        top.bind("<Return>", lambda _e: _ok())
        top.bind("<Escape>", lambda _e: _cancel())

        top.wait_window()  # block until closed
        self.default_paper_grade.set(selected["value"])
        return selected["value"]

   

    def build_gui(self):
        self.tabs = ttk.Notebook(self.root)
        self.main_tab = ttk.Frame(self.tabs)
        self.settings_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.main_tab, text="Estimator")
        self.tabs.add(self.settings_tab, text="Settings")
        self.tabs.pack(expand=1, fill="both")

        self.build_main_tab()
        self.build_settings_tab()

    def build_main_tab(self):
        top = tk.Frame(self.main_tab)
        top.pack(fill="x", pady=5)
        tk.Button(top, text="Import PDFs", command=self.import_pdfs).pack(side="left", padx=5)
        tk.Button(top, text="Recalculate Total", command=self.recalculate_total).pack(side="left")
        tk.Button(top, text="Print", command=self.start_printing).pack(side="left")

        tk.Label(top, text="Printer:").pack(side="left", padx=(20, 2))
        self.printer_combo = ttk.Combobox(top, textvariable=self.printer_name, width=40, state="readonly")
        self.printer_combo.pack(side="left")

        self.load_printers()
        
        tk.Button(top, text="Export CSV",  command=self.export_breakdown_csv).pack(side="left", padx=(10,0))
        tk.Button(top, text="Export Quote", command=self.export_quote_pdf).pack(side="left", padx=5)


        canvas_frame = tk.Frame(self.main_tab)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame)
        vscroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        hscroll = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)  # NEW

        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")

        # wire up both scrollbars
        self.canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)  # NEW

        # pack: canvas expands, vscroll on the right, hscroll along the bottom
        self.canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")
        hscroll.pack(side="bottom", fill="x")  # NEW

        self.result_label = tk.Label(self.main_tab, text="", font=("Arial", 10), justify="left")
        self.result_label.pack(pady=10)

    def load_automation_defaults(self):
        """Load filename automation rules from JSON."""
        if os.path.exists(self.automation_file):
            try:
                with open(self.automation_file, "r") as f:
                    return json.load(f)
            except Exception:
                messagebox.showwarning("Automation Defaults", "Failed to load automation defaults file.")
        # Defaults
        return {
            "score": {"preset": "A3 Score", "qty": 1},
            "violin": {"preset": "A3 Booklet Part", "qty": 10},
            "viola": {"preset": "A3 Booklet Part", "qty": 8},
            "cello": {"preset": "A3 Booklet Part", "qty": 6},
            "bass": {"preset": "A3 Booklet Part", "qty": 6},
            "clarinet": {"preset": "A3 Booklet Part", "qty": 1},
            "trumpet": {"preset": "A3 Booklet Part", "qty": 1},
            "flute": {"preset": "A3 Booklet Part", "qty": 1},
        }

    def save_automation_defaults(self):
        """Save automation rules to JSON file."""
        try:
            with open(self.automation_file, "w") as f:
                json.dump(self.automation_rules, f, indent=4)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save automation defaults:\n{e}")



    def load_presets(self):
        """Load presets from JSON file or create defaults."""
        if os.path.exists(self.presets_file):
            try:
                with open(self.presets_file, "r") as f:
                    return json.load(f)
            except Exception:
                messagebox.showwarning("Presets", "Failed to load presets file, using defaults.")
        # Default presets
        return {
            "A3 Booklet Part": {
                "type_var": "A3 Booklet",
                "binding_var": "Staple",
                "front_cover_var": "None",
                "back_cover_var": "None",
                "acetate_var": "None",
                "paper_type_var": "120gsm"
            },
            "A3 Score": {
                "type_var": "A3 Double-sided",
                "binding_var": "Wire Comb",
                "front_cover_var": "Card 450gsm (Colour)",
                "back_cover_var": "Card 450gsm (Colour)",
                "acetate_var": "None",
                "paper_type_var": "120gsm"
            }
        }

    def save_presets(self):
        """Save current presets to file."""
        try:
            with open(self.presets_file, "w") as f:
                json.dump(self.default_styles, f, indent=4)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save presets:\n{e}")

    def apply_preset(self, preset_name, type_var, binding_var, front_cover_var, back_cover_var, acetate_var, paper_type_var):
        """Apply preset values to given variables."""
        preset = self.default_styles.get(preset_name)
        if not preset:
            return
        type_var.set(preset["type_var"])
        binding_var.set(preset["binding_var"])
        front_cover_var.set(preset["front_cover_var"])
        back_cover_var.set(preset["back_cover_var"])
        acetate_var.set(preset["acetate_var"])
        paper_type_var.set(preset["paper_type_var"])

    def add_or_edit_preset(self, name=None):
        """Create or edit a preset."""
        if not name:
            name = simpledialog.askstring("New Preset", "Enter preset name:")
        if not name:
            return
        name = name.strip()

        # Ask user for settings (basic dialog)
        top = tk.Toplevel(self.root)
        top.title(f"Edit Preset: {name}")
        entries = {}

        fields = ["type_var", "binding_var", "front_cover_var", "back_cover_var", "acetate_var", "paper_type_var"]
        current = self.default_styles.get(name, {})

        tk.Label(top, text="Define default values:").pack(pady=5)
        tk.Label(top, text="Define default values:").pack(pady=5)
        for field in fields:
            frame = ttk.Frame(top)
            frame.pack(fill="x", padx=10, pady=2)
            ttk.Label(frame, text=field.replace("_var", "").replace("_", " ").title(), width=18).pack(side="left")

            var = tk.StringVar(value=current.get(field, ""))

            if field == "type_var":
                values = ["A3 Single-sided", "A3 Double-sided", "A3 Booklet",
                        "A4 Single-sided", "A4 Double-sided", "A4 Booklet"]
            elif field == "binding_var":
                values = list(self.binding_costs.keys())
            elif field in ["front_cover_var", "back_cover_var"]:
                values = ["None"] + \
                        [f"{k} (B/W)" for k in self.bw_cover_costs.keys()] + \
                        [f"{k} (Colour)" for k in self.colour_cover_costs.keys()]
            elif field == "acetate_var":
                values = ["None", "Front", "Back", "Both"]
            elif field == "paper_type_var":
                values = ["120gsm", "Photo"]
            else:
                values = []

            ttk.Combobox(frame, textvariable=var, width=40, state="readonly", values=values).pack(side="left")
            entries[field] = var

            entries[field] = var

        def save_and_close():
            self.default_styles[name] = {k: v.get() for k, v in entries.items()}
            self.save_presets()
            self.refresh_preset_list()
            top.destroy()
            messagebox.showinfo("Saved", f"Preset '{name}' saved successfully.")

        ttk.Button(top, text="Save", command=save_and_close).pack(pady=10)

    def delete_selected_preset(self):
        selection = self.preset_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Select a preset to delete.")
            return
        name = self.preset_listbox.get(selection[0])
        self.delete_preset(name)



    def load_printers(self):
        import win32print
        printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        if printers:
            self.printer_combo["values"] = printers
            self.printer_combo.current(0)  # default to the first one
            self.printer_name.set(printers[0])
        else:
            messagebox.showerror("No Printers", "No printers found on this system.")

    def build_settings_tab(self):
        # === Scrollable canvas ===
        outer = ttk.Frame(self.settings_tab)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)

        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_configure)

        # mousewheel (Windows)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # === Two columns inside 'inner' ===
        left  = ttk.Frame(inner)
        right = ttk.Frame(inner)
        left.grid(row=0, column=0, sticky="nw", padx=10, pady=10)
        right.grid(row=0, column=1, sticky="ne", padx=10, pady=10)

        # ===== LEFT COLUMN: Costs & Labour =====
        row = 0
        tk.Label(left, text="Ink cost per side (A4):").grid(row=row, column=0, sticky="e")
        tk.Entry(left, textvariable=self.ink_cost_a4, width=8).grid(row=row, column=1, sticky="w")

        row += 1
        tk.Label(left, text="Ink cost per side (A3):").grid(row=row, column=0, sticky="e")
        tk.Entry(left, textvariable=self.ink_cost_a3, width=8).grid(row=row, column=1, sticky="w")

        row += 1
        tk.Label(left, text="Cost per A4 sheet:").grid(row=row, column=0, sticky="e")
        tk.Entry(left, textvariable=self.cost_a4, width=8).grid(row=row, column=1, sticky="w")

        row += 1
        tk.Label(left, text="Cost per A3 sheet:").grid(row=row, column=0, sticky="e")
        tk.Entry(left, textvariable=self.cost_a3, width=8).grid(row=row, column=1, sticky="w")

        row += 2
        tk.Label(left, text="Paper Grades (surcharge per sheet)", font=("Arial", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")

        for grade, sizes in self.paper_grade_surcharge.items():
            row += 1
            frm = ttk.Frame(left); frm.grid(row=row, column=0, columnspan=2, sticky="w")
            ttk.Label(frm, text=f"{grade}: ").pack(side="left")
            ttk.Label(frm, text="A4").pack(side="left", padx=(4,0))
            tk.Entry(frm, textvariable=sizes["A4"], width=6).pack(side="left")
            ttk.Label(frm, text="A3").pack(side="left", padx=(8,0))
            tk.Entry(frm, textvariable=sizes["A3"], width=6).pack(side="left")


        row += 1
        tk.Label(left, text="Photo paper surcharge:").grid(row=row, column=0, sticky="e")
        tk.Entry(left, textvariable=self.photo_paper_surcharge, width=8).grid(row=row, column=1, sticky="w")

        row += 2
        tk.Label(left, text="Binding Costs:", font=("Arial", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        for k, var in self.binding_costs.items():
            row += 1
            tk.Label(left, text=f"{k}:").grid(row=row, column=0, sticky="e")
            tk.Entry(left, textvariable=var, width=8).grid(row=row, column=1, sticky="w")

        row += 2
        tk.Label(left, text="Card Cover Costs - B/W:", font=("Arial", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        for k, var in self.bw_cover_costs.items():
            row += 1
            tk.Label(left, text=f"{k}:").grid(row=row, column=0, sticky="e")
            tk.Entry(left, textvariable=var, width=8).grid(row=row, column=1, sticky="w")

        row += 2
        tk.Label(left, text="Card Cover Costs - Colour:", font=("Arial", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        for k, var in self.colour_cover_costs.items():
            row += 1
            tk.Label(left, text=f"{k}:").grid(row=row, column=0, sticky="e")
            tk.Entry(left, textvariable=var, width=8).grid(row=row, column=1, sticky="w")

        row += 2
        tk.Label(left, text="Acetate Cost per sheet:").grid(row=row, column=0, sticky="e")
        tk.Entry(left, textvariable=self.acetate_cost, width=8).grid(row=row, column=1, sticky="w")

        # Labour & margin
        row += 2
        ttk.Separator(left, orient="horizontal").grid(row=row, columnspan=2, sticky="ew", pady=8)
        row += 1
        tk.Label(left, text="Labour & Margin:", font=("Arial", 10, "bold")).grid(row=row, column=0, sticky="w")

        row += 1
        tk.Label(left, text="Labour per job (per copy):").grid(row=row, column=0, sticky="e")
        tk.Entry(left, textvariable=self.labour_per_job, width=8).grid(row=row, column=1, sticky="w")

        row += 1
        tk.Label(left, text="Markup multiplier:").grid(row=row, column=0, sticky="e")
        tk.Entry(left, textvariable=self.markup_multiplier, width=8).grid(row=row, column=1, sticky="w")

        row += 2
        tk.Label(left, text="Binding Labour (per copy):").grid(row=row, column=0, columnspan=2, sticky="w")
        for name, var in self.binding_labour.items():
            row += 1
            tk.Label(left, text=f"{name}:").grid(row=row, column=0, sticky="e")
            tk.Entry(left, textvariable=var, width=8).grid(row=row, column=1, sticky="w")

        # ===== RIGHT COLUMN: Presets & Automation =====
        rrow = 0
        tk.Label(right, text="Manage Presets:", font=("Arial", 10, "bold")).grid(row=rrow, column=0, columnspan=2, sticky="w", pady=(0,4))

        rrow += 1
        self.preset_listbox = tk.Listbox(right, height=8, width=35)
        self.preset_listbox.grid(row=rrow, column=0, columnspan=2, sticky="ew")
        self.preset_listbox.bind("<Double-1>", lambda _e: self.edit_selected_preset())

        self.refresh_preset_list()

        rrow += 1
        ttk.Button(right, text="New Preset", command=lambda: self.add_or_edit_preset()).grid(row=rrow, column=0, sticky="ew", padx=2, pady=2)
        ttk.Button(right, text="Edit Selected", command=self.edit_selected_preset).grid(row=rrow, column=1, sticky="ew", padx=2, pady=2)

        rrow += 1
        ttk.Button(right, text="Delete Selected", command=self.delete_selected_preset).grid(row=rrow, column=0, columnspan=2, sticky="ew", padx=2, pady=2)

        rrow += 2
        ttk.Separator(right, orient="horizontal").grid(row=rrow, columnspan=2, sticky="ew", pady=8)

        rrow += 1
        tk.Label(right, text="Filename Automation Rules:", font=("Arial", 10, "bold")).grid(row=rrow, column=0, columnspan=2, sticky="w", pady=(0,4))

        rrow += 1
        self.auto_tree = ttk.Treeview(right, columns=("keyword", "preset", "qty"), show="headings", height=8)
        self.auto_tree.heading("keyword", text="Keyword")
        self.auto_tree.heading("preset", text="Preset")
        self.auto_tree.heading("qty", text="Qty")
        self.auto_tree.column("keyword", width=140)
        self.auto_tree.column("preset", width=160)
        self.auto_tree.column("qty", width=60)
        self.auto_tree.grid(row=rrow, column=0, columnspan=2, sticky="ew", padx=2)
        self.refresh_automation_rules()

        # Double-click to edit an automation rule (optional if you already added this)
        self.auto_tree.bind("<Double-1>", self.on_rule_double_click)

        rrow += 1
        ttk.Button(right, text="Add Rule", command=self.add_automation_rule).grid(row=rrow, column=0, sticky="ew", padx=2, pady=2)
        ttk.Button(right, text="Delete Selected", command=self.delete_automation_rule).grid(row=rrow, column=1, sticky="ew", padx=2, pady=2)



        


    def refresh_automation_rules(self):
        self.auto_tree.delete(*self.auto_tree.get_children())
        for kw, data in self.automation_rules.items():
            self.auto_tree.insert("", "end", values=(kw, data["preset"], data["qty"]))

    def add_automation_rule(self):
        top = tk.Toplevel(self.root)
        top.title("Add Automation Rule")

        tk.Label(top, text="Keyword (case-insensitive):").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        kw_var = tk.StringVar()
        tk.Entry(top, textvariable=kw_var, width=25).grid(row=0, column=1, padx=5, pady=5)

        tk.Label(top, text="Preset:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        preset_var = tk.StringVar(value="A3 Booklet Part")
        ttk.Combobox(top, textvariable=preset_var, values=list(self.default_styles.keys()), width=25, state="readonly").grid(row=1, column=1, padx=5, pady=5)

        tk.Label(top, text="Qty:").grid(row=2, column=0, padx=5, pady=5, sticky="e")
        qty_var = tk.StringVar(value="1")
        tk.Entry(top, textvariable=qty_var, width=8).grid(row=2, column=1, padx=5, pady=5, sticky="w")

        def save_rule():
            kw = kw_var.get().strip().lower()
            if not kw:
                messagebox.showerror("Error", "Keyword cannot be empty.")
                return
            self.automation_rules[kw] = {"preset": preset_var.get(), "qty": int(qty_var.get())}
            self.save_automation_defaults()
            if hasattr(self, "build_keyword_matchers"):
                 self.build_keyword_matchers()
            self.refresh_automation_rules()
            top.destroy()

        ttk.Button(top, text="Save", command=save_rule).grid(row=3, column=0, columnspan=2, pady=10)

    def on_rule_double_click(self, event):
        """Handle double-click on a row in the automation rules Treeview."""
        item_id = self.auto_tree.identify_row(event.y)
        if item_id:
            self.edit_automation_rule(item_id)

    def edit_automation_rule(self, item_id=None):
        """Open a dialog to edit an existing automation rule (keyword, preset, qty)."""
        if item_id is None:
            sel = self.auto_tree.selection()
            if not sel:
                return
            item_id = sel[0]

        old_kw, old_preset, old_qty = self.auto_tree.item(item_id, "values")

        top = tk.Toplevel(self.root)
        top.title(f"Edit Rule: {old_kw}")
        top.transient(self.root)
        top.grab_set()

        tk.Label(top, text="Keyword (case-insensitive):").grid(row=0, column=0, padx=6, pady=6, sticky="e")
        kw_var = tk.StringVar(value=old_kw)
        tk.Entry(top, textvariable=kw_var, width=25).grid(row=0, column=1, padx=6, pady=6)

        tk.Label(top, text="Preset:").grid(row=1, column=0, padx=6, pady=6, sticky="e")
        preset_var = tk.StringVar(value=old_preset)
        ttk.Combobox(top, textvariable=preset_var, values=list(self.default_styles.keys()),
                    state="readonly", width=25).grid(row=1, column=1, padx=6, pady=6)

        tk.Label(top, text="Qty:").grid(row=2, column=0, padx=6, pady=6, sticky="e")
        qty_var = tk.StringVar(value=str(old_qty))
        tk.Entry(top, textvariable=qty_var, width=10).grid(row=2, column=1, padx=6, pady=6, sticky="w")

        def save():
            new_kw = kw_var.get().strip().lower()
            if not new_kw:
                messagebox.showerror("Error", "Keyword cannot be empty.")
                return
            try:
                q = int(qty_var.get())
            except ValueError:
                messagebox.showerror("Error", "Qty must be a whole number.")
                return

            # If keyword changed, remove old key
            if new_kw != old_kw:
                self.automation_rules.pop(old_kw, None)

            self.automation_rules[new_kw] = {"preset": preset_var.get(), "qty": q}
            self.save_automation_defaults()
            # Rebuild the compiled regex matchers if you added the variants feature
            if hasattr(self, "build_keyword_matchers"):
                self.build_keyword_matchers()

            # Refresh table
            self.refresh_automation_rules()
            top.destroy()

        ttk.Button(top, text="Save", command=save).grid(row=3, column=0, columnspan=2, pady=10)



    def build_keyword_matchers(self):
        """Compile regex for each user rule keyword, using variants if available."""
        self._rule_patterns = {}
        for kw in self.automation_rules.keys():
            lk = kw.lower().strip()
            if lk in self._variants_lc:
                # Union of variant patterns
                union = "|".join(self._variants_lc[lk])
                regex = re.compile(rf"(?:{union})", re.IGNORECASE)
            else:
                # Safe literal search for the keyword
                regex = re.compile(re.escape(kw), re.IGNORECASE)
            self._rule_patterns[kw] = regex


    def delete_automation_rule(self):
        sel = self.auto_tree.selection()
        if not sel:
            return
        vals = self.auto_tree.item(sel[0])["values"]
        kw = vals[0]
        if messagebox.askyesno("Delete Rule", f"Delete rule for '{kw}'?"):
            del self.automation_rules[kw]
            self.save_automation_defaults()
            if hasattr(self, "build_keyword_matchers"):
                self.build_keyword_matchers()
            self.refresh_automation_rules()


    def refresh_preset_list(self):
        self.preset_listbox.delete(0, tk.END)
        for name in sorted(self.default_styles.keys()):
            self.preset_listbox.insert(tk.END, name)

    def edit_selected_preset(self):
        selection = self.preset_listbox.curselection()
        if selection:
            name = self.preset_listbox.get(selection[0])
            self.add_or_edit_preset(name)

    def refresh_automation_list(self):
        self.refresh_automation_rules()
        

    def add_automation_rule(self):
        top = tk.Toplevel(self.root)
        top.title("Add Automation Rule")

        tk.Label(top, text="Keyword:").grid(row=0, column=0, padx=5, pady=5)
        tk.Label(top, text="Preset:").grid(row=1, column=0, padx=5, pady=5)
        tk.Label(top, text="Qty:").grid(row=2, column=0, padx=5, pady=5)

        kw_var = tk.StringVar()
        preset_var = tk.StringVar(value="A3 Booklet Part")
        qty_var = tk.StringVar(value="1")

        ttk.Entry(top, textvariable=kw_var, width=20).grid(row=0, column=1)
        ttk.Combobox(top, textvariable=preset_var, width=20, state="readonly", values=list(self.default_styles.keys())).grid(row=1, column=1)
        ttk.Entry(top, textvariable=qty_var, width=8).grid(row=2, column=1)

        def save_rule():
            kw = kw_var.get().strip().lower()
            if not kw:
                messagebox.showerror("Error", "Keyword cannot be empty.")
                return
            self.automation_rules[kw] = {"preset": preset_var.get(), "qty": int(qty_var.get() or 1)}
            self.save_automation_defaults()
            if hasattr(self, "build_keyword_matchers"):
                 self.build_keyword_matchers()
            self.refresh_automation_list()
            top.destroy()

        ttk.Button(top, text="Save", command=save_rule).grid(row=3, column=0, columnspan=2, pady=10)
        self.build_keyword_matchers()

    def delete_automation_rule(self):
        selected = self.auto_tree.selection()
        if not selected:
            messagebox.showinfo("No Selection", "Select a rule to delete.")
            return
        vals = self.auto_tree.item(selected[0], "values")
        kw = vals[0]
        if messagebox.askyesno("Delete Rule", f"Delete automation rule for '{kw}'?"):
            del self.automation_rules[kw]
            self.save_automation_defaults()
            if hasattr(self, "build_keyword_matchers"):
                self.build_keyword_matchers()
            self.refresh_automation_list()
            self.build_keyword_matchers()


    def import_pdfs(self):
        if getattr(self, "importing", False):
            return
        self.importing = True

        paths = filedialog.askopenfilenames(filetypes=[("PDF files", "*.pdf")])
        if not paths:
            self.importing = False
            return
        
        # Ask once per import batch
        chosen_default_grade = self.ask_default_paper_grade()


        # Clear table
        for w in self.scrollable_frame.winfo_children():
            w.destroy()
        self.pdf_data.clear()

        # Headers
        headers = [
            ("File", 60), ("Pages", 6), ("Qty", 4), ("Line £", 10), ("Preset", 20),
            ("Print Type", 16), ("Binding", 12), ("Front Cover", 20),
            ("Back Cover", 20), ("Acetate", 10), ("Grade", 10), ("Paper Type", 10),
        ]
        for c, (title, _w) in enumerate(headers):
            tk.Label(self.scrollable_frame, text=title, font=("Arial", 9, "bold")).grid(
                row=0, column=c, sticky="w", padx=4, pady=2
            )

        # Static value lists
        type_values = [
            "A3 Single-sided", "A3 Double-sided", "A3 Booklet",
            "A4 Single-sided", "A4 Double-sided", "A4 Booklet",
        ]
        grade_values = list(self.paper_grade_surcharge.keys())

        binding_values = list(self.binding_costs.keys())
        cover_values = (
            ["None"]
            + [f"{k} (B/W)" for k in self.bw_cover_costs.keys()]
            + [f"{k} (Colour)" for k in self.colour_cover_costs.keys()]
        )
        acetate_values = ["None", "Front", "Back", "Both"]
        paper_values = ["Standard", "Photo"]
        preset_values = ["None"] + list(self.default_styles.keys())

        import re, math, os, PyPDF2

        # Helper: build a row-specific recompute (avoids late binding)
        def make_recompute(pcount, qty_var, type_var, binding_var,
                   front_cover_var, back_cover_var, acetate_var,
                   paper_type_var, paper_grade_var,  
                   binding_menu, line_cost_var):
            def recompute():
                # qty
                try:
                    qty_int = int(qty_var.get() or 0)
                except ValueError:
                    qty_int = 0

                tstr = type_var.get()
                is_a3 = ("A3" in tstr)  
                pps = self.get_pages_per_sheet(tstr)
                sheets_per_copy = math.ceil(pcount / pps) if pps else 0

                
                


                # auto-binding for A3 Booklet
                if ("A3" in tstr) and ("Booklet" in tstr):
                    if sheets_per_copy <= 1 and binding_var.get() == "Staple":
                        binding_var.set("None"); binding_menu.set("None")
                    elif sheets_per_copy > 1 and binding_var.get() == "None":
                        binding_var.set("Staple"); binding_menu.set("Staple")

                base_cost_per_sheet = self.cost_a3.get() if "A3" in tstr else self.cost_a4.get()
                ink_cost_per_page   = self.ink_cost_a3.get() if "A3" in tstr else self.ink_cost_a4.get()
                if paper_type_var.get() == "Photo":
                    base_cost_per_sheet += self.photo_paper_surcharge.get()

                total_pages  = pcount * qty_int
                total_sheets = sheets_per_copy * qty_int   # ← was ceil(total_pages / pps)
                sheet_cost   = total_sheets * base_cost_per_sheet
                ink_cost     = total_pages * ink_cost_per_page

                def _cover_price(cstr: str) -> float:
                    if cstr == "None":
                        return 0.0
                    try:
                        base, type_ = cstr.split(" (")
                        type_ = type_.strip(")")
                        return (self.bw_cover_costs if type_ == "B/W" else self.colour_cover_costs)[base].get()
                    except Exception:
                        return 0.0

                fc_cost_per_copy = _cover_price(front_cover_var.get())
                bc_cost_per_copy = _cover_price(back_cover_var.get())
                fc_cost_total = fc_cost_per_copy * qty_int
                bc_cost_total = bc_cost_per_copy * qty_int

                # Binding material + labour, with same rule for A3 booklet + staple
                binding_name = binding_var.get()
                charge_binding = not (("Booklet" in tstr) and ("A3" in tstr) and (binding_name == "Staple") and (sheets_per_copy <= 1))

                binding_mat_per_copy = (self.binding_costs.get(binding_name, tk.DoubleVar(value=0.0)).get()
                                        if charge_binding else 0.0)
                binding_lab_per_copy = (self.binding_labour.get(binding_name, tk.DoubleVar(value=0.0)).get()
                                        if charge_binding else 0.0)
                binding_mat_total = binding_mat_per_copy * qty_int
                binding_lab_total = binding_lab_per_copy * qty_int

                # Acetate
                acetate_map = {"None": 0, "Front": 1, "Back": 1, "Both": 2}
                acetate_total = acetate_map.get(acetate_var.get(), 0) * self.acetate_cost.get() * qty_int

                # Labour per job (per copy)
                labour_job_total = self.labour_per_job.get() * qty_int

                # Apply margin multiplier
                pre_margin = (sheet_cost + ink_cost + fc_cost_total + bc_cost_total +
                            binding_mat_total + binding_lab_total + acetate_total +
                            labour_job_total)
                line_total = pre_margin * self.markup_multiplier.get()

                line_cost_var.set(f"£{line_total:.2f}")
                self.recalculate_total()
            return recompute



        # Helper: preset change handler (row-specific)
        def make_preset_change(preset_var, type_var, binding_var, front_cover_var, back_cover_var,
                            acetate_var, paper_type_var, type_menu, binding_menu,
                            front_cover_menu, back_cover_menu, acetate_menu, paper_menu, recompute):
            def on_preset_change(_e=None):
                name = preset_var.get()
                if name in self.default_styles:
                    self.apply_preset(name, type_var, binding_var, front_cover_var, back_cover_var, acetate_var, paper_type_var)
                    type_menu.set(type_var.get())
                    binding_menu.set(binding_var.get())
                    front_cover_menu.set(front_cover_var.get())
                    back_cover_menu.set(back_cover_var.get())
                    acetate_menu.set(acetate_var.get())
                    paper_menu.set(paper_type_var.get())
                recompute()
            return on_preset_change

        variants = getattr(self, "keyword_variants", None)

        


        for idx, path in enumerate(paths):
            # Pages
            try:
                with open(path, "rb") as f:
                    pages = len(PyPDF2.PdfReader(f).pages)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to read {os.path.basename(path)}:\n{e}")
                continue

            fname   = os.path.basename(path)
            fname_l = fname.lower()

            # Pick automation: regex variants first, else literal keywords
            chosen_preset = "A3 Booklet Part"
            qty_default   = 1
            matched_key   = None

            if variants:
                for canon, patterns in variants.items():
                    for pat in patterns:
                        try:
                            if re.search(pat, fname, flags=re.IGNORECASE):
                                matched_key = canon.lower()
                                break
                        except re.error:
                            pass
                    if matched_key:
                        break

            if matched_key and matched_key in self.automation_rules:
                rule = self.automation_rules[matched_key]
                chosen_preset = rule.get("preset", chosen_preset)
                qty_default   = int(rule.get("qty", qty_default))
            else:
                for kw, rule in self.automation_rules.items():
                    if kw in fname_l:
                        chosen_preset = rule.get("preset", chosen_preset)
                        qty_default   = int(rule.get("qty", qty_default))
                        break

            if chosen_preset not in self.default_styles:
                chosen_preset = "A3 Booklet Part" if "A3 Booklet Part" in self.default_styles else "None"

            # Pull preset values (fallbacks)
            if chosen_preset in self.default_styles:
                p = self.default_styles[chosen_preset]
                type_val        = p.get("type_var", "A3 Booklet")
                binding_val     = p.get("binding_var", "Staple")
                front_cover_val = p.get("front_cover_var", "None")
                back_cover_val  = p.get("back_cover_var", "None")
                acetate_val     = p.get("acetate_var", "None")
                paper_type_val  = p.get("paper_type_var", "120gsm")
            else:
                type_val, binding_val = "A3 Booklet", "Staple"
                front_cover_val = back_cover_val = "None"
                acetate_val, paper_type_val = "None", "Standard"

            # Per-row vars
            qty_var         = tk.StringVar(self.root, value=str(qty_default))
            line_cost_var   = tk.StringVar(self.root, value="£0.00")
            preset_var      = tk.StringVar(self.root, value=chosen_preset)
            type_var        = tk.StringVar(self.root, value=type_val)
            binding_var     = tk.StringVar(self.root, value=binding_val)
            front_cover_var = tk.StringVar(self.root, value=front_cover_val)
            back_cover_var  = tk.StringVar(self.root, value=back_cover_val)
            acetate_var     = tk.StringVar(self.root, value=acetate_val)
            paper_type_var  = tk.StringVar(self.root, value=paper_type_val)
            grade_values = list(self.paper_grade_surcharge.keys())              
            paper_grade_var  = tk.StringVar(self.root, value=chosen_default_grade)  # use the chosen default



            # Row UI
            r = idx + 1
            tk.Label(self.scrollable_frame, text=fname, anchor="w", width=60).grid(row=r, column=0, sticky="w")
            tk.Label(self.scrollable_frame, text=str(pages)).grid(row=r, column=1, sticky="w")
            tk.Entry(self.scrollable_frame, textvariable=qty_var, width=4).grid(row=r, column=2, sticky="w")
            tk.Label(self.scrollable_frame, textvariable=line_cost_var, width=10, anchor="w").grid(row=r, column=3, sticky="w")

            preset_menu = ttk.Combobox(self.scrollable_frame, textvariable=preset_var, values=preset_values,
                                    state="readonly", width=20)
            preset_menu.grid(row=r, column=4, sticky="w")
            # ensure initial value paints on all themes
            try:
                preset_menu.current(preset_values.index(preset_var.get()))
            except ValueError:
                preset_menu.current(0)

            type_menu = ttk.Combobox(self.scrollable_frame, textvariable=type_var, values=type_values,
                                    state="readonly", width=16)
            type_menu.grid(row=r, column=5, sticky="w")
            try: type_menu.current(type_values.index(type_var.get()))
            except ValueError: type_menu.current(0)

            binding_menu = ttk.Combobox(self.scrollable_frame, textvariable=binding_var, values=binding_values,
                                        state="readonly", width=12)
            binding_menu.grid(row=r, column=6, sticky="w")
            try: binding_menu.current(binding_values.index(binding_var.get()))
            except ValueError: binding_menu.current(0)

            front_cover_menu = ttk.Combobox(self.scrollable_frame, textvariable=front_cover_var, values=cover_values,
                                            state="readonly", width=20)
            front_cover_menu.grid(row=r, column=7, sticky="w")
            try: front_cover_menu.current(cover_values.index(front_cover_var.get()))
            except ValueError: front_cover_menu.current(0)

            back_cover_menu = ttk.Combobox(self.scrollable_frame, textvariable=back_cover_var, values=cover_values,
                                        state="readonly", width=20)
            back_cover_menu.grid(row=r, column=8, sticky="w")
            try: back_cover_menu.current(cover_values.index(back_cover_var.get()))
            except ValueError: back_cover_menu.current(0)

            acetate_menu = ttk.Combobox(self.scrollable_frame, textvariable=acetate_var, values=acetate_values,
                                        state="readonly", width=10)
            acetate_menu.grid(row=r, column=9, sticky="w")
            try: acetate_menu.current(acetate_values.index(acetate_var.get()))
            except ValueError: acetate_menu.current(0)

            grade_menu = ttk.Combobox(self.scrollable_frame, textvariable=paper_grade_var,
                                    values=grade_values, state="readonly", width=10)
            grade_menu.grid(row=r, column=10, sticky="w")
            try:
                grade_menu.current(grade_values.index(paper_grade_var.get()))
            except ValueError:
                grade_menu.current(0)


            paper_menu = ttk.Combobox(self.scrollable_frame, textvariable=paper_type_var, values=paper_values,
                                    state="readonly", width=10)
            paper_menu.grid(row=r, column=11, sticky="w")


            # Row-specific recompute + preset handler (no shared closures)
            recompute = make_recompute(
                pages, qty_var, type_var, binding_var,
                front_cover_var, back_cover_var, acetate_var, paper_type_var,
                paper_grade_var,                       # <-- add
                binding_menu, line_cost_var
            )
            on_preset_change = make_preset_change(
                preset_var, type_var, binding_var, front_cover_var, back_cover_var,
                acetate_var, paper_type_var, type_menu, binding_menu,
                front_cover_menu, back_cover_menu, acetate_menu, paper_menu, recompute
            )

            # Trace & events (row-bound callables)
            qty_var.trace_add("write", lambda *_: recompute())
            type_var.trace_add("write", lambda *_: recompute())
            binding_var.trace_add("write", lambda *_: recompute())
            front_cover_var.trace_add("write", lambda *_: recompute())
            back_cover_var.trace_add("write", lambda *_: recompute())
            acetate_var.trace_add("write", lambda *_: recompute())
            paper_type_var.trace_add("write", lambda *_: recompute())
            preset_var.trace_add("write", lambda *_: on_preset_change())

            preset_menu.bind("<<ComboboxSelected>>", on_preset_change)
            type_menu.bind("<<ComboboxSelected>>", lambda _e: recompute())
            binding_menu.bind("<<ComboboxSelected>>", lambda _e: recompute())
            front_cover_menu.bind("<<ComboboxSelected>>", lambda _e: recompute())
            back_cover_menu.bind("<<ComboboxSelected>>", lambda _e: recompute())
            acetate_menu.bind("<<ComboboxSelected>>", lambda _e: recompute())
            paper_menu.bind("<<ComboboxSelected>>", lambda _e: recompute())
            paper_grade_var.trace_add("write", lambda *_: recompute())
            grade_menu.bind("<<ComboboxSelected>>", lambda _e: recompute())


            # Initial compute for this row (ensures Line £ shows immediately)
            recompute()

            self.pdf_data.append({
                "file": path,
                "file_name": fname,
                "pages": pages,
                "qty_var": qty_var,
                "type_var": type_var,
                "binding_var": binding_var,
                "front_cover_var": front_cover_var,
                "back_cover_var": back_cover_var,
                "acetate_var": acetate_var,
                "paper_type_var": paper_type_var,
                "preset_var": preset_var, 
                "paper_grade_var": paper_grade_var,
                "line_cost_var": line_cost_var,  
                "accepted": False,
            })

        self.recalculate_total()
        self.importing = False

    def _get_var_value(self, *names, default=0.0):
        """Return the first existing variable's value (supports tk.DoubleVar or raw float)."""
        for name in names:
            v = getattr(self, name, None)
            if v is None:
                continue
            # Tk variable
            try:
                import tkinter as tk
                if isinstance(v, (tk.DoubleVar, tk.StringVar, tk.IntVar)):
                    try:
                        return float(v.get())
                    except Exception:
                        pass
            except Exception:
                pass
            # Raw numeric
            if isinstance(v, (int, float)):
                return float(v)
        return float(default)

    def _get_pricing_knobs(self):
        """
        Read margin & labour in a tolerant way:
        - Accept multiple possible attribute names
        - If margin > 1, treat as percent (e.g., 30 -> 0.30)
        """
        margin = self._get_var_value("margin_pct", "margin_percent", "markup_pct", "markup", default=0.0)
        if margin > 1.0:
            margin = margin / 100.0

        base_labour = self._get_var_value(
            "labour_base", "labour_base_job", "labour_per_job", "labour_job_base", default=0.0
        )
        per_staple = self._get_var_value(
            "labour_per_staple", "labour_staple", "labour_staple_job", default=0.0
        )
        per_wire = self._get_var_value(
            "labour_per_wire", "labour_wire", "labour_wire_job", default=0.0
        )
        return margin, base_labour, per_staple, per_wire


    def _make_quote_ref(self):
        # QUO-YYYYMMDD-HHMMSS (you can change format if you like)
        import datetime, random
        ts = datetime.datetime.now().strftime("%Y%m%d")
        # Add a short random suffix to avoid duplicates if multiple quotes per second
        suf = random.randint(100, 999)
        return f"QUO-{ts}-{suf}"

    



    def export_quote_pdf(self):
        """Quotation PDF: compact columns, sub-details, rounded summary box, org-aware reference & filename."""
        from tkinter import filedialog, messagebox, simpledialog
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepInFrame
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        import datetime, os, re

        if not self.pdf_data:
            messagebox.showinfo("Export Quote", "No items to export.")
            return

        # ---- Who's this for? (optional but used in ref/filename) ----
        org = simpledialog.askstring("Organisation", "Customer / Organisation (optional):")
        org_code = ""
        if org:
            org_code = re.sub(r"[^A-Z0-9]+", "", org.upper())[:12]

        # ---- Identity ----
        your_name = "Jack Capstaff"
        your_email = "jack@jackcapstaff.com"
        your_phone = "07805165842"
        your_address_lines = ["Hood Hill Lodge", "Wentworth", "S62 7UB"]
        bank_name, bank_acct, bank_sort = "Natwest", "48589381", "54-41-24"

        # ---- Reference / date / suggested filename ----
        base_ref = self._make_quote_ref()  # QUO-YYYYMMDD-###
        quote_ref = f"{org_code}-{base_ref}" if org_code else base_ref
        today = datetime.date.today()
        today_str = today.strftime("%d %b %Y")
        suggested = f"{quote_ref} - printing quote - {today_str}.pdf"

        # ---- Styles ----
        styles = getSampleStyleSheet()
        base  = ParagraphStyle("Base", parent=styles["BodyText"], fontSize=9.5, leading=12)
        small = ParagraphStyle("Small", parent=base, fontSize=8.0, leading=10)
        bold  = ParagraphStyle("Bold", parent=base, fontName="Helvetica-Bold")
        key   = ParagraphStyle("Key", parent=base, fontSize=11, leading=14)

        # ---- Save-as dialog (with suggested filename) ----
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=suggested,
            filetypes=[("PDF", "*.pdf")],
            title="Save Quote PDF"
        )
        if not path:
            return

        # ---- Doc ----
        doc = SimpleDocTemplate(
            path, pagesize=A4,
            topMargin=22*mm, bottomMargin=18*mm, leftMargin=14*mm, rightMargin=14*mm
        )
        frame_w = doc.width

        # ---- Header left: identity + payment details (separate lines) ----
        left_lines = [
            [Paragraph(your_name, bold)],
            [Paragraph("<br/>".join(your_address_lines), base)],
            [Paragraph(your_email, base)],
            [Paragraph(your_phone, base)],
            [Spacer(1, 4)],
            [Paragraph("<b>Payable to:</b>", base)],
            [Paragraph(bank_name, base)],
            [Paragraph(f"Account: {bank_acct}", base)],
            [Paragraph(f"Sort: {bank_sort}", base)],
            [Paragraph(f"Please include the reference <b>{quote_ref}</b> in your bank transfer.", base)],
        ]

        right_w = min(frame_w * 0.35, 180)
        gap_w   = 8
        left_w  = frame_w - right_w - gap_w

        left_tbl = Table(left_lines, colWidths=[left_w])
        left_tbl.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),0),
            ("RIGHTPADDING",(0,0),(-1,-1),4),
            ("TOPPADDING",(0,0),(-1,-1),0),
            ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ]))

        # ---- Header right: rounded box with ref/date ----
        right_content = [
            Paragraph("Quotation", ParagraphStyle("qt", parent=bold, fontSize=13)),
            Spacer(1, 2),
            Paragraph(f"Ref: {quote_ref}", base),
            Spacer(1, 2),
            Paragraph(f"Date: {today_str}", base),
        ]
        # Wrap inner content so RoundedBox can compute its own height
        kb = KeepInFrame(right_w - 12, 9999, right_content, mode="shrink")
        right_box = RoundedBox([kb], max_width=right_w, radius=6, padding=6)

        header = Table([[left_tbl, "", right_box]], colWidths=[left_w, gap_w, right_w])
        header.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),0),
            ("RIGHTPADDING",(0,0),(-1,-1),0),
            ("TOPPADDING",(0,0),(-1,-1),0),
            ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ]))

        # ---- Items table (compact columns with sub-details) ----
        rows = [["Description", "Qty", "Preset", "Unit £", "Line £"]]
        grand = 0.0

        for item in self.pdf_data:
            b = self._line_breakdown(item)
            if "line_cost_var" in item:
                item["line_cost_var"].set(f"£{b['line_total']:.2f}")

            covers_bits = []
            if b["front_cover"] != "None": covers_bits.append(f"Front: {b['front_cover']}")
            if b["back_cover"]  != "None": covers_bits.append(f"Back: {b['back_cover']}")
            covers_txt = ", ".join(covers_bits) if covers_bits else "None"

            desc = Paragraph(
                f"<b>{b['file']}</b>"
                f"<br/><font size=8>Binding: {b['binding']} | Covers: {covers_txt} | Paper: {b['paper_grade']} / {b['paper_type']}</font>",
                base
            )


            rows.append([
                desc,
                str(b["qty"]),
                b["preset"],          # “Preset” (e.g., A3 Booklet / A3 Double-sided / A3 Score)
                f"£{b['unit_price']:.2f}",
                f"£{b['line_total']:.2f}",
            ])
            grand += b["line_total"]

        col_widths = [
            frame_w * 0.64, frame_w * 0.06, frame_w * 0.14, frame_w * 0.08, frame_w * 0.08
        ]

        items = Table(rows, repeatRows=1, colWidths=col_widths, hAlign="LEFT", splitByRow=1)
        items.setStyle(TableStyle([
            ("FONT",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,0),9.5),
            ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),
            ("GRID",(0,0),(-1,-1),0.25,colors.grey),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("ALIGN",(1,1),(1,-1),"RIGHT"),
            ("ALIGN",(-2,1),(-1,-1),"RIGHT"),
            ("LEFTPADDING",(0,0),(-1,-1),4),
            ("RIGHTPADDING",(0,0),(-1,-1),4),
            ("TOPPADDING",(0,0),(-1,-1),3),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ]))

        markup = self.markup_multiplier.get()
        margin_pct_display = int(round((markup - 1.0) * 100))

        totals = [
            Spacer(1, 8),
            Paragraph(f"<b>Total: £{grand:.2f}</b>", key),
            
        ]

        story = [header, Spacer(1, 10), items] + totals

        # ---- Footer with page number ----
        def _footer(c, d):
            c.setFont("Helvetica", 8.5)
            c.drawRightString(A4[0] - d.rightMargin, 12*mm, f"Page {c.getPageNumber()}")
            c.setLineWidth(0.4)
            c.line(d.leftMargin, 15*mm, A4[0]-d.rightMargin, 15*mm)

        try:
            doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
            messagebox.showinfo("Export Quote", f"Saved: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Export Quote", f"Failed to build PDF:\n{e}")




    def export_breakdown_csv(self):
        """Export a CSV with what to print + full materials & labour breakdown."""
        import csv
        from tkinter import filedialog, messagebox

        if not self.pdf_data:
            messagebox.showinfo("Export CSV", "No items to export.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            title="Save Breakdown CSV"
        )
        if not path:
            return

        rows = []
        totals = {
            "sheet_cost": 0.0, "ink_cost": 0.0, "front_cover_cost": 0.0, "back_cover_cost": 0.0,
            "binding_cost": 0.0, "acetate_cost": 0.0, "labour": 0.0, "margin_amt": 0.0,
            "materials_subtotal": 0.0, "grand_total": 0.0
        }

        for item in self.pdf_data:
            b = self._line_breakdown(item)
            rows.append(b)
            totals["sheet_cost"]       += b["sheet_cost"]
            totals["ink_cost"]         += b["ink_cost"]
            totals["front_cover_cost"] += b["front_cover_cost"]
            totals["back_cover_cost"]  += b["back_cover_cost"]
            totals["binding_cost"]     += b["binding_cost"]
            totals["acetate_cost"]     += b["acetate_cost"]
            totals["labour"]           += b["labour"]
            totals["materials_subtotal"] += b["materials_subtotal"]
            totals["margin_amt"]       += b["margin_amt"]
            totals["grand_total"]      += b["line_total"]

        fieldnames = [
            "file","print_type","pages_per_copy","qty","sheets_per_copy",
            "total_pages","total_sheets","binding","front_cover","back_cover","acetate","paper_type",
            "sheet_cost","ink_cost","front_cover_cost","back_cover_cost","binding_cost","acetate_cost",
            "labour","materials_subtotal","margin_amt","unit_price","line_total"
        ]

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                for r in rows:
                    w.writerow(r)
                # blank line + totals
                w.writerow({})
                w.writerow({"file": "TOTALS",
                            "sheet_cost": round(totals["sheet_cost"],2),
                            "ink_cost": round(totals["ink_cost"],2),
                            "front_cover_cost": round(totals["front_cover_cost"],2),
                            "back_cover_cost": round(totals["back_cover_cost"],2),
                            "binding_cost": round(totals["binding_cost"],2),
                            "acetate_cost": round(totals["acetate_cost"],2),
                            "labour": round(totals["labour"],2),
                            "materials_subtotal": round(totals["materials_subtotal"],2),
                            "margin_amt": round(totals["margin_amt"],2),
                            "line_total": round(totals["grand_total"],2)})
            messagebox.showinfo("Export CSV", f"Saved: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Export CSV", f"Failed to save CSV:\n{e}")



    def _line_breakdown(self, item):
        import math

        qty = int(item["qty_var"].get() or 0)
        pages_per_copy = item["pages"]
        tstr = item["type_var"].get()
        is_a3 = ("A3" in tstr)   
        preset_name = item.get("preset_var").get() if item.get("preset_var") else tstr

        # pages/sheets
        pps = self.get_pages_per_sheet(tstr) or 1
        sheets_per_copy = math.ceil(pages_per_copy / pps)
        total_pages  = pages_per_copy * qty
        total_sheets = sheets_per_copy * qty   # ← was ceil(total_pages / pps)

        # binding (mirror auto-switch logic used in UI)
        binding = item["binding_var"].get()
        if ("A3" in tstr) and ("Booklet" in tstr):
            if sheets_per_copy <= 1 and binding == "Staple":
                binding = "None"
            elif sheets_per_copy > 1 and binding == "None":
                binding = "Staple"

        # base & ink
        bis_a3 = ("A3" in tstr)
        base_cost_per_sheet = self.cost_a3.get() if is_a3 else self.cost_a4.get()



        tstr = item["type_var"].get()
        is_a3 = ("A3" in tstr)
        grade = item.get("paper_grade_var").get() if item.get("paper_grade_var") else list(self.paper_grade_surcharge.keys())[0]

        base_cost_per_sheet = self.cost_a3.get() if is_a3 else self.cost_a4.get()

        # grade surcharge
        grade_map = self.paper_grade_surcharge.get(grade, {})
        base_cost_per_sheet += grade_map["A3"].get() if is_a3 else grade_map["A4"].get()

        # photo surcharge
        if item["paper_type_var"].get() == "Photo":
            base_cost_per_sheet += self.photo_paper_surcharge.get()



        sheet_cost = total_sheets * base_cost_per_sheet
        ink_cost = total_pages * (self.ink_cost_a3.get() if is_a3 else self.ink_cost_a4.get())

        # covers
        def cover_price(cstr: str) -> float:
            if cstr == "None":
                return 0.0
            try:
                base, type_ = cstr.split(" (")
                type_ = type_.strip(")")
                return (self.bw_cover_costs if type_ == "B/W" else self.colour_cover_costs)[base].get()
            except Exception:
                return 0.0

        fc_cost_per_copy = cover_price(item["front_cover_var"].get())
        bc_cost_per_copy = cover_price(item["back_cover_var"].get())
        front_cover_cost_total = fc_cost_per_copy * qty
        back_cover_cost_total  = bc_cost_per_copy * qty

        # binding costs (materials + labour per copy), with same “charge only if >1 sheet” rule for A3 booklet + staple
        charge_binding = True
        if ("Booklet" in tstr) and ("A3" in tstr) and (binding == "Staple"):
            if sheets_per_copy <= 1:
                charge_binding = False

        binding_material_per_copy = (self.binding_costs.get(binding, tk.DoubleVar(value=0.0)).get()
                                    if charge_binding else 0.0)
        binding_labour_per_copy   = (self.binding_labour.get(binding, tk.DoubleVar(value=0.0)).get()
                                    if charge_binding else 0.0)

        binding_material_total = binding_material_per_copy * qty
        binding_labour_total   = binding_labour_per_copy   * qty

        # acetate
        acetate_map = {"None": 0, "Front": 1, "Back": 1, "Both": 2}
        acetate_count_per_copy = acetate_map.get(item["acetate_var"].get(), 0)
        acetate_total = acetate_count_per_copy * self.acetate_cost.get() * qty

        # labour per job (per copy)
        labour_job_total = self.labour_per_job.get() * qty

        # materials subtotal (no labour)
        materials_subtotal = (sheet_cost + ink_cost +
                            front_cover_cost_total + back_cover_cost_total +
                            binding_material_total + acetate_total)

        # total labour
        labour_total = binding_labour_total + labour_job_total

        # margin & totals via markup_multiplier (e.g. 1.30)
        pre_margin = materials_subtotal + labour_total
        markup = self.markup_multiplier.get()
        margin_amt = pre_margin * (markup - 1.0)
        line_total = pre_margin * markup
        unit_price = (line_total / qty) if qty else 0.0

        return {
            "file": item["file_name"],
            "print_type": tstr,
            "preset": preset_name,
            "pages_per_copy": pages_per_copy,
            "qty": qty,
            "sheets_per_copy": sheets_per_copy,
            "total_pages": total_pages,
            "total_sheets": total_sheets,
            "binding": binding,
            "front_cover": item["front_cover_var"].get(),
            "back_cover": item["back_cover_var"].get(),
            "acetate": item["acetate_var"].get(),
            "paper_type": item["paper_type_var"].get(),
            "paper_grade": grade,
            "sheet_cost": round(sheet_cost, 2),
            "ink_cost": round(ink_cost, 2),
            "front_cover_cost": round(front_cover_cost_total, 2),
            "back_cover_cost": round(back_cover_cost_total, 2),
            "binding_cost": round(binding_material_total, 2),
            "acetate_cost": round(acetate_total, 2),

            # labour now includes binding labour + labour_per_job
            "labour": round(labour_total, 2),

            "materials_subtotal": round(materials_subtotal, 2),
            "margin_amt": round(margin_amt, 2),

            "unit_price": round(unit_price, 2),
            "line_total": round(line_total, 2),
        }



    def enforce_binding_rule_for_row(self, pages_per_copy, type_str, qty_var, binding_var, binding_menu):
        """
        Auto-switch binding for A3 Booklet:
        - If sheets_per_copy <= 1 and binding is Staple -> set to None
        - If sheets_per_copy > 1 and binding is None    -> set to Staple
        Does not override other binding choices.
        """
        try:
            qty = int(qty_var.get() or 0)
        except ValueError:
            qty = 0

        # pages_per_copy is the file's page count; we only need it to know sheets_per_copy
        pages_per_sheet = self.get_pages_per_sheet(type_str)
        sheets_per_copy = math.ceil(pages_per_copy / pages_per_sheet) if pages_per_sheet else 0

        if ("A3" in type_str) and ("Booklet" in type_str):
            if sheets_per_copy <= 1 and binding_var.get() == "Staple":
                binding_var.set("None")
                binding_menu.set("None")
            elif sheets_per_copy > 1 and binding_var.get() == "None":
                binding_var.set("Staple")
                binding_menu.set("Staple")



    def recalculate_total(self):
        total_pages = 0
        total_sheets = 0
        total_cost = 0.0

        for item in self.pdf_data:
            try:
                qty = int(item["qty_var"].get() or 0)
            except ValueError:
                qty = 0
            if qty <= 0:
                if "line_cost_var" in item:
                    item["line_cost_var"].set("£0.00")
                continue

            b = self._line_breakdown(item)

            # If your row dicts don't store line_cost_var, you can skip this safely.
            if "line_cost_var" in item:
                item["line_cost_var"].set(f"£{b['line_total']:.2f}")

            total_cost   += b["line_total"]
            total_pages  += b["total_pages"]
            total_sheets += b["total_sheets"]

        self.result_label.config(
            text=f"Total Pages: {total_pages}\n"
                f"Total Sheets: {total_sheets}\n"
                f"Estimated Charge: £{total_cost:.2f}"
        )



    def get_pages_per_sheet(self, print_type):
        return {
            "A3 Single-sided": 1,
            "A3 Double-sided": 2,
            "A3 Booklet": 4,
            "A4 Single-sided": 1,
            "A4 Double-sided": 2,
            "A4 Booklet": 4
        }.get(print_type, 4)

    def group_by_format(self):
        """Groups the imported PDFs by their specified print format."""
        grouped_formats = {}
        for item in self.pdf_data:
            print_format = item["type_var"].get()
            if print_format not in grouped_formats:
                grouped_formats[print_format] = []
            grouped_formats[print_format].append(item)
        return grouped_formats

    def remove_covers(self, pdf_path, front_cover, back_cover):
        """Removes specified front and back covers from a PDF.

        Args:
            pdf_path (str): Path to the PDF file.
            front_cover (str): Front cover type specified by the user.
            back_cover (str): Back cover type specified by the user.

        Returns:
            list: A list of extracted cover page PDF readers,
                  and a PDF reader for the document with covers removed.
                  Returns ([], original_pdf) if no covers are to be removed,
                  or if an error occurred.
        """
        try:
            with open(pdf_path, "rb") as file:
                pdf_reader = PyPDF2.PdfReader(file)
                num_pages = len(pdf_reader.pages)

                extracted_covers = []
                pdf_writer = PyPDF2.PdfWriter()

                # Determine if front and/or back covers should be removed
                remove_front = front_cover != "None"
                remove_back = back_cover != "None"

                # Handle cover extraction and removal
                start_page = 1 if remove_front else 0
                end_page = num_pages - 1 if remove_back else num_pages

                if remove_front:
                    extracted_covers.append(pdf_reader.pages[0])
                if remove_back:
                    extracted_covers.append(pdf_reader.pages[-1])

                for page_num in range(start_page, end_page):
                    page = pdf_reader.pages[page_num]
                    pdf_writer.add_page(page)

                # Create a new PDF reader from the modified PDF writer
                modified_pdf = io.BytesIO()
                pdf_writer.write(modified_pdf)
                modified_pdf.seek(0)  # Go back to the beginning of the stream
                modified_pdf_reader = PyPDF2.PdfReader(modified_pdf)

                return extracted_covers, modified_pdf_reader

        except Exception as e:
            messagebox.showerror("Error", f"Failed to remove covers from {pdf_path}:\n{str(e)}")
            return [], pdf_reader  # Return original PDF if error

    def create_booklet_pdf(self, input_path, paper_size="A4"):
        """
        Rearranges PDF pages for booklet printing.

        Args:
            input_path (str): Path to the input PDF file.
            paper_size (str):  "A4" or "A3" (determines output page size).

        Returns:
            str: Path to the new PDF file, or None on error.
        """
        try:
            with open(input_path, "rb") as infile:
                reader = PyPDF2.PdfReader(infile)
                num_pages = len(reader.pages)

                # Determine page dimensions for the specified paper size
                if paper_size == "A4":
                    page_width, page_height = A4  # A4 dimensions (portrait)
                elif paper_size == "A3":
                    page_width, page_height = A3  # A3 dimensions (portrait)
                else:
                    messagebox.showerror("Error", f"Unsupported paper size: {paper_size}")
                    return None

                # Create a new PDF with a landscape orientation
                new_pdf = io.BytesIO()
                c = canvas.Canvas(new_pdf, pagesize=landscape((page_width, page_height))) # Landscape required

                # Calculate the number of "signatures" (groups of 4 pages)
                num_signatures = (num_pages + 3) // 4

                for i in range(num_signatures):
                    # Calculate page numbers for each signature
                    page1 = i * 2 + 1  # Right page
                    page2 = num_pages - i * 2 # Left page

                    # Calculate page numbers for the back of the signature
                    page3 = num_pages - (i * 2 + 1) # Right page (back)
                    page4 = i * 2 # Left page (back)

                    # Function to add a page to the canvas
                    def add_page(page_num, x_offset):
                        if page_num <= num_pages and page_num > 0:
                            page = reader.pages[page_num - 1]
                            x1, y1, x2, y2 = page.mediabox
                            page_width = x2 - x1
                            page_height = y2 - y1
                            # Scale factor to fit the page on half of the output page
                            scale = min((page_width/2) / page_width, (page_height/2) / page_height)

                            # Save the canvas state
                            c.saveState()

                            # Translate and scale the canvas
                            c.translate(x_offset, 0)
                            c.scale(scale, scale)

                            # Draw the page content
                            c.doFormXobj(page)

                            # Restore the canvas state
                            c.restoreState()

                    # Add pages to the canvas
                    add_page(page1, page_width)
                    add_page(page2, 0)

                    # Start a new page for the back of the signature
                    c.showPage()

                    add_page(page3, page_width)
                    add_page(page4, 0)

                    # Start a new page for the next signature
                    c.showPage()

                # Save the PDF to a file
                c.save()
                new_pdf.seek(0)

                output_path = f"temp_booklet_{os.path.basename(input_path)}"
                with open(output_path, "wb") as outfile:
                    outfile.write(new_pdf.read())
                return output_path

        except Exception as e:
            messagebox.showerror("Error", f"Failed to create booklet PDF: {e}")
            return None

    def create_preview_pdf(self, items, print_format):
        """Creates a temporary PDF by merging documents of the same format for preview.
           It also handles cover removal and booklet conversion.

        Args:
            items (list): List of PDF data dictionaries for the specified format.
            print_format (str): The print format of the PDFs.

        Returns:
            str: The path to the temporary preview PDF, or None on error.
        """
        try:
            output_path = f"temp_preview_{print_format.replace(' ', '_')}.pdf"
            pdf_writer = PyPDF2.PdfWriter()

            for item in items:
                # Remove covers
                extracted_covers, pdf_reader = self.remove_covers(
                    item["file"], item["front_cover_var"].get(), item["back_cover_var"].get()
                )

                # Convert to booklet if needed
                if "Booklet" in print_format:
                    paper_size = "A3" if "A3" in print_format else "A4"
                    booklet_path = self.create_booklet_pdf(item["file"], paper_size)
                    if not booklet_path:
                        messagebox.showerror("Error", f"Failed to create booklet for {item['file']}")
                        continue  # Skip to the next item
                    try:
                        with open(booklet_path, "rb") as booklet_file:
                            booklet_reader = PyPDF2.PdfReader(booklet_file)
                            for page in booklet_reader.pages:
                                pdf_writer.add_page(page)
                    except Exception as e:
                        messagebox.showerror("Error", f"Error reading temporary booklet file: {e}")

                else:
                    # Add pages from the modified PDF to the output
                    for page in pdf_reader.pages:
                        pdf_writer.add_page(page)

            # Write the merged PDF to the output file
            with open(output_path, "wb") as output_pdf:
                pdf_writer.write(output_pdf)

            return output_path
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create preview PDF for {print_format}:\n{str(e)}")
            return None

    def set_printer_options(self, printer_name, paper_size="A4", duplex=False, booklet=False):
        """Sets printer options using win32print."""
        try:
            hPrinter = win32print.OpenPrinter(printer_name)
            defaults = win32print.GetPrinter(hPrinter, 2)
            printer_info = defaults["pDevMode"]

            # Set paper size
            if paper_size == "A4":
                printer_info.PaperSize = win32con.DMPAPER_A4
            elif paper_size == "A3":
                printer_info.PaperSize = win32con.DMPAPER_A3
            elif paper_size == "Letter":
                printer_info.PaperSize = win32con.DMPAPER_LETTER

            # Set duplex printing
            if duplex:
                printer_info.Duplex = win32con.DMDUP_HORIZONTAL
            else:
                printer_info.Duplex = win32con.DMDUP_SIMPLEX

            # Set booklet printing (conditionally, if supported)
            if booklet:
                try:
                    # Check if PrintSchemaNamespace attribute exists
                    if hasattr(printer_info, 'PrintSchemaNamespace'):
                        printer_info.PrintSchemaNamespace = "http://schemas.microsoft.com/windows/2010/01/printing/PrintSchemaFramework"
                        printer_info.DocumentParameters = "<psf:ParameterBlock xmlns:psf=\"http://schemas.microsoft.com/windows/2010/01/printing/PrintSchemaFramework\"><psf:Parameter name=\"BookletPrinting\" psf:datatype=\"xsd:boolean\">True</psf:Parameter></psf:ParameterBlock>"
                    else:
                        messagebox.showinfo("Warning", "Booklet printing may not be supported by this printer driver.")
                except Exception as e:
                    messagebox.showerror("Booklet Setup Error", f"Error setting booklet options: {e}")

            else:
                # Reset booklet settings if not needed (and if attribute exists)
                if hasattr(printer_info, 'PrintSchemaNamespace'):
                    printer_info.PrintSchemaNamespace = None
                    printer_info.DocumentParameters = None

            win32print.SetPrinter(hPrinter, 2, defaults, 0)
            win32print.ClosePrinter(hPrinter)
            return True

        except Exception as e:
            messagebox.showerror("Printer Settings Error", f"Failed to set printer options: {e}")
            return False


    def print_pdf(self, pdf_path, printer_name=None, paper_size="A4", duplex=False, booklet=False):
        """Prints a PDF file with specified printer settings."""
        try:
            system = platform.system()
            if system == "Windows":
                if printer_name:
                    # Apply printer settings
                    if not self.set_printer_options(printer_name, paper_size, duplex, booklet):
                        return  # Stop if settings failed to apply

                    import win32print
                    try:
                        current_printer = win32print.GetDefaultPrinter()
                        win32print.SetDefaultPrinter(printer_name)
                        win32api.ShellExecute(0, "print", pdf_path, None, ".", 0)
                        win32print.SetDefaultPrinter(current_printer)  # Restore
                    except Exception as e:
                        messagebox.showerror("Print Error", f"Failed to print to {printer_name}:\n{e}")
                else:
                    win32api.ShellExecute(0, "print", pdf_path, None, ".", 0)
            elif system == "Darwin":
                # Implement printing with options for macOS
                messagebox.showinfo("Info", "Printing with options not implemented for macOS.")
                subprocess.call(["lp", pdf_path])
            elif system == "Linux":
                # Implement printing with options for Linux
                messagebox.showinfo("Info", "Printing with options not implemented for Linux.")
                subprocess.call(["lp", pdf_path])
            else:
                messagebox.showerror("Error", "Unsupported OS for printing.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to print {pdf_path}:\n{str(e)}")

    def open_pdf_viewer(self, pdf_path):
        """Opens the PDF in the default PDF viewer."""
        try:
            if platform.system() == "Windows":
                os.startfile(pdf_path)
            elif platform.system() == "Darwin":  # macOS
                subprocess.call(["open", pdf_path])
            elif platform.system() == "Linux":
                subprocess.call(["xdg-open", pdf_path])  # Use xdg-open for Linux
            else:
                messagebox.showerror("Error", "Unsupported operating system for preview.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open PDF viewer: {e}")

    def start_printing(self):
        """Initiates the printing process, grouping documents by format and handling preview/acceptance."""
        grouped_formats = self.group_by_format()
        printer_name = self.printer_name.get()

        for print_format, items in grouped_formats.items():
            # Determine print settings based on format
            paper_size = "A3" if "A3" in print_format else "A4"
            duplex = "Double-sided" in print_format or "Booklet" in print_format  #Enforce duplex if using booklet
            booklet = "Booklet" in print_format

            if print_format in self.accepted_formats:
                # Batch print accepted formats
                for item in items:

                    #Convert each item to a booklet if specified:
                    if booklet:
                        paper_size = "A3" if "A3" in print_format else "A4"
                        booklet_path = self.create_booklet_pdf(item["file"], paper_size)
                        if booklet_path:
                            self.print_pdf(
                                booklet_path,
                                printer_name,
                                paper_size,
                                duplex,
                                booklet
                                )
                            continue #Skip printing the original item file
                        else:
                            messagebox.showerror("Error", f"Failed to create booklet for {item['file']}")
                            continue #Skip printing the original item file
                    else:
                        self.print_pdf(
                            item["file"],
                            printer_name,
                            paper_size,
                            duplex,
                            booklet
                        )


            else:
                # Create preview and ask for acceptance
                preview_path = self.create_preview_pdf(items, print_format)
                if preview_path:
                    # Open the PDF viewer for preview
                    self.open_pdf_viewer(preview_path)  # Call the new preview function

                    if messagebox.askyesno(
                        "Print Preview",
                        f"Do you want to print this {print_format} format?\n"
                        f"Click 'Yes' to print and accept this format for batch printing.\n"
                        f"Click 'No' to cancel."
                    ):
                        # Print the preview and accept the format
                        self.print_pdf(
                            preview_path,
                            printer_name,
                            paper_size,
                            duplex,
                            booklet
                        )
                        self.accepted_formats.add(print_format)
                        # Mark items as accepted
                        for item in items:
                            item["accepted"] = True
                    else:
                        messagebox.showinfo(
                            "Printing Cancelled",
                            f"Printing cancelled for {print_format} format."
                        )
                else:
                    messagebox.showerror(
                        "Error",
                        f"Could not create preview for {print_format}."
                    )

# ---- Helper Flowable: rounded rectangle container with padding ----
from reportlab.platypus import Flowable, KeepInFrame
from reportlab.lib.units import mm

class RoundedBox(Flowable):
    def __init__(self, inner_flowables, max_width, radius=6, stroke=1, padding=6):
        super().__init__()
        self.inner = inner_flowables if isinstance(inner_flowables, (list, tuple)) else [inner_flowables]
        self.max_width = max_width
        self.radius = radius
        self.stroke = stroke
        self.padding = padding
        self._wrapped = None
        self.width = max_width
        self.height = 0

    def wrap(self, availWidth, availHeight):
        w = min(self.max_width, availWidth)
        kif = KeepInFrame(w - 2*self.padding, availHeight, self.inner, mode="shrink")
        iw, ih = kif.wrapOn(self.canv, w - 2*self.padding, availHeight)
        self._wrapped = kif
        self.width = w
        self.height = ih + 2*self.padding
        return (self.width, self.height)

    def draw(self):
        c = self.canv
        c.roundRect(0, 0, self.width, self.height, self.radius, stroke=self.stroke, fill=0)
        if self._wrapped is None:
            kif = KeepInFrame(self.width - 2*self.padding, self.height - 2*self.padding, self.inner, mode="shrink")
        else:
            kif = self._wrapped
        kif.drawOn(c, self.padding, self.padding)                    

import win32con
if __name__ == "__main__":
    root = tk.Tk()
    app = PDFSheetEstimatorApp(root)
    root.mainloop()
