#!/usr/bin/env python3
"""
PDF Tranche Splitter (GUI) — Drag & Drop Edition
- Open password-protected PDFs
- Show all pages as thumbnails in a scrollable grid
- Click/Shift/Ctrl selection
- Drag & drop thumbnails to reorder the visual page order
- Create named "tranches" (groups) from the current *visual* selection
- Export tranches honoring the current visual order

Install:
    pip install pymupdf pypdf pillow

Run:
    python pdf_tranche_splitter_dragdrop.py
"""

import io
import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox, ttk
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Optional
import pytesseract
import unicodedata


try:
    import fitz  # PyMuPDF
except ImportError:
    messagebox.showerror("Missing dependency", "PyMuPDF (fitz) is required. Install with:\n\npip install pymupdf")
    raise

try:
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:
        from PyPDF2 import PdfReader, PdfWriter
except ImportError:
    messagebox.showerror("Missing dependency", "pypdf / PyPDF2 is required. Install with:\n\npip install pypdf\n# or\npip install PyPDF2")
    raise

from PIL import Image, ImageTk



@dataclass
class Tranche:
    name: str
    # Store ORIGINAL page numbers (1-based) in the order they were selected
    # This stays correct even if we later remove pages from the grid.
    orig_pages: List[int] = field(default_factory=list)


class PDFTrancheSplitterDnD(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Tranche Splitter — Drag & Drop")
        self.geometry("1200x800")
        self.minsize(900, 600)

        # Document state
        self.pdf_path: str = ""
        self.doc: Optional[fitz.Document] = None
        self.page_count: int = 0

        # Visual state
        self.visual_page_order: List[int] = []  # maps visual index -> original page number (1-based)
        self.thumbnails: List[Optional[ImageTk.PhotoImage]] = []  # cache per original page
        self.thumb_frames: List[tk.Frame] = []  # frame widgets aligned to visual positions
        self.last_layout_cols: int = 0

        # Selection state (visual positions, 1-based)
        self.selected_positions: Set[int] = set()
        self.last_clicked_position: Optional[int] = None

        # Tranches
        self.tranches: List[Tranche] = []

        # Drag state
        self.drag_src_pos: Optional[int] = None
        self.drag_hover_pos: Optional[int] = None

        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        # Toolbar
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=6)

        ttk.Button(bar, text="Open PDF…", command=self.open_pdf).pack(side=tk.LEFT)
        self.btn_new_tranche = ttk.Button(bar, text="➕ New Tranche from Selection", command=self.create_tranche_from_selection, state=tk.DISABLED)
        self.btn_new_tranche.pack(side=tk.LEFT, padx=6)
        self.btn_clear_sel = ttk.Button(bar, text="Clear Selection", command=self.clear_selection, state=tk.DISABLED)
        self.btn_clear_sel.pack(side=tk.LEFT)
        self.btn_select_all = ttk.Button(bar, text="Select All", command=self.select_all, state=tk.DISABLED)
        self.btn_select_all.pack(side=tk.LEFT, padx=6)
        self.btn_export = ttk.Button(bar, text="💾 Export Tranches…", command=self.export_tranches, state=tk.DISABLED)
        self.btn_export.pack(side=tk.RIGHT)

        # Panes
        panes = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0,6))

        # Left: grid in scrollable canvas
        left = ttk.Frame(panes)
        panes.add(left, weight=3)
        self.canvas = tk.Canvas(left, highlightthickness=0)
        self.scroll_y = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.canvas.yview)
        self.scroll_x = ttk.Scrollbar(left, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.scroll_y.set, xscrollcommand=self.scroll_x.set)

        self.grid_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0,0), window=self.grid_frame, anchor="nw")

        self.grid_frame.bind("<Configure>", self._on_grid_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        # Right: tranche manager
        right = ttk.Frame(panes)
        panes.add(right, weight=1)

        ttk.Label(right, text="Tranches (follow grid order)", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", padx=6, pady=(6, 0))

        

        self.tranche_tree = ttk.Treeview(right, columns=("name", "pages"), show="headings", selectmode="browse")
        self.tranche_tree.heading("name", text="Name")
        self.tranche_tree.heading("pages", text="Original Pages")
        self.tranche_tree.column("name", width=160, stretch=True)
        self.tranche_tree.column("pages", width=180, stretch=True)
        self.tranche_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        act = ttk.Frame(right)
        act.pack(fill=tk.X, padx=6, pady=(0,6))
        self.btn_rename = ttk.Button(act, text="Rename", command=self.rename_tranche, state=tk.DISABLED)
        self.btn_remove = ttk.Button(act, text="Remove", command=self.remove_tranche, state=tk.DISABLED)
        self.btn_rename.pack(side=tk.LEFT)
        self.btn_remove.pack(side=tk.LEFT, padx=6)

        self.tranche_tree.bind("<<TreeviewSelect>>", lambda e: self._update_tranche_buttons())

        # Status
        self.status = tk.StringVar(value="Open a PDF to begin.")
        ttk.Label(self, textvariable=self.status, anchor="w", relief=tk.SUNKEN).pack(fill=tk.X, side=tk.BOTTOM)

    # -------------- PDF & Thumbs --------------
    def open_pdf(self):
        path = filedialog.askopenfilename(title="Select PDF", filetypes=[("PDF files", "*.pdf")])
        if not path:
            return

        # Reset
        self._reset_state()

        # Open with PyMuPDF (render + decrypt)
        try:
            doc = fitz.open(path)
            if doc.needs_pass:
                while True:
                    pwd = simpledialog.askstring("Password Required", "Enter PDF password:", show="*")
                    if pwd is None:
                        doc.close()
                        return
                    if doc.authenticate(pwd):
                        break
                    messagebox.showerror("Incorrect Password", "That password didn't work. Try again.")
            self.doc = doc
        except Exception as e:
            messagebox.showerror("Failed to open PDF", f"{e}")
            return

        self.pdf_path = path
        self.page_count = len(self.doc)
        self.visual_page_order = list(range(1, self.page_count + 1))  # initial 1..N (original order)
        self.thumbnails = [None] * self.page_count
        self.status.set(f"Loaded {os.path.basename(path)} — {self.page_count} pages.")
        self.btn_export.configure(state=tk.NORMAL)
        self.btn_clear_sel.configure(state=tk.NORMAL)
        self.btn_select_all.configure(state=tk.NORMAL)

        self._render_grid()

    def _reset_state(self):
        self.pdf_path = ""
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        self.page_count = 0
        self.visual_page_order.clear()
        self.thumbnails.clear()
        for fr in self.thumb_frames:
            fr.destroy()
        self.thumb_frames.clear()
        self.selected_positions.clear()
        self.last_clicked_position = None
        self.tranches.clear()
        for iid in self.tranche_tree.get_children():
            self.tranche_tree.delete(iid)
        self.btn_new_tranche.configure(state=tk.DISABLED)
        self.btn_export.configure(state=tk.DISABLED)
        self.btn_clear_sel.configure(state=tk.DISABLED)
        self.btn_select_all.configure(state=tk.DISABLED)
        self.status.set("Open a PDF to begin.")

    def _render_grid(self):
        # clear existing frames
        for fr in self.thumb_frames:
            fr.destroy()
        self.thumb_frames.clear()
        self.grid_frame.update_idletasks()

        available_width = max(self.canvas.winfo_width() - 20, 800)
        thumb_w, thumb_h = 150, 200
        num_cols = max(3, min(8, (available_width // (thumb_w + 16))))
        self.last_layout_cols = num_cols

        # Build frames by current visual order
        for visual_pos, orig_page in enumerate(self.visual_page_order, start=1):
            # Render thumbnail cache for this original page if needed
            img = self.thumbnails[orig_page - 1]
            if img is None:
                try:
                    page = self.doc[orig_page - 1]
                    pix = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2))
                    img_data = Image.open(io.BytesIO(pix.tobytes("png")))
                    img_data.thumbnail((thumb_w, thumb_h), Image.LANCZOS)
                    img = ImageTk.PhotoImage(img_data)
                    self.thumbnails[orig_page - 1] = img
                except Exception:
                    img = None

            # Container frame (for border highlighting)
            frame = tk.Frame(self.grid_frame, bd=2, relief="flat", bg="#dddddd")
            r = (visual_pos - 1) // num_cols
            c = (visual_pos - 1) % num_cols
            frame.grid(row=r, column=c, padx=6, pady=6, sticky="n")

            # Header
            ttk.Label(frame, text=f"Pos {visual_pos}  (Pg {orig_page})").pack(fill=tk.X)

            # Thumbnail
            if img is not None:
                lbl = tk.Label(frame, image=img, bg="#ffffff")
                lbl.image = img
            else:
                lbl = tk.Label(frame, text=f"[Pg {orig_page}]", width=18, height=12, bg="#ffffff", anchor="center", justify="center")

            lbl.pack()

            # Bind selection
            for ev in ("<Button-1>", "<Control-Button-1>", "<Shift-Button-1>"):
                lbl.bind(ev, lambda e, pos=visual_pos: self.on_thumb_click(e, pos))

            # Bind drag start/move/end on frame to get broader hit area
            frame.bind("<ButtonPress-1>", lambda e, pos=visual_pos: self.on_drag_start(e, pos))
            frame.bind("<B1-Motion>", self.on_drag_motion)
            frame.bind("<ButtonRelease-1>", self.on_drag_release)

            self.thumb_frames.append(frame)

        self.btn_new_tranche.configure(state=(tk.NORMAL if self.visual_page_order else tk.DISABLED))


        self._refresh_selection_ui()
        self._update_tranche_list()  # keep tranche labels in sync (visual positions)

    # -------- Selection --------
    def on_thumb_click(self, event, pos: int):
        shift = (event.state & 0x0001) != 0
        ctrl = (event.state & 0x0004) != 0 or (event.state & 0x20000) != 0

        if shift and self.last_clicked_position is not None:
            a, b = sorted([self.last_clicked_position, pos])
            if not ctrl:
                self.selected_positions.clear()
            self.selected_positions.update(range(a, b+1))
        else:
            if ctrl:
                if pos in self.selected_positions:
                    self.selected_positions.remove(pos)
                else:
                    self.selected_positions.add(pos)
            else:
                # toggle single
                if self.selected_positions == {pos}:
                    self.selected_positions.clear()
                else:
                    self.selected_positions = {pos}

        self.last_clicked_position = pos
        self._refresh_selection_ui()

    def clear_selection(self):
        self.selected_positions.clear()
        self._refresh_selection_ui()

    def select_all(self):
        if not self.page_count:
            return
        self.selected_positions = set(range(1, self.page_count + 1))
        self._refresh_selection_ui()

    def _refresh_selection_ui(self):
        for i, frame in enumerate(self.thumb_frames, start=1):
            if i in self.selected_positions:
                frame.configure(bd=3, relief="solid", bg="#81c784")  # green highlight
            else:
                frame.configure(bd=2, relief="flat", bg="#dddddd")

        # Drag hover highlight
        if self.drag_hover_pos is not None and 1 <= self.drag_hover_pos <= len(self.thumb_frames):
            self.thumb_frames[self.drag_hover_pos-1].configure(bd=3, relief="solid", bg="#64b5f6")  # blue hover

        sel_count = len(self.selected_positions)
        self.status.set(f"{sel_count} selected. Drag thumbnails to reorder. Shift-click selects range.")
        self.btn_new_tranche.configure(state=(tk.NORMAL if sel_count > 0 else tk.DISABLED))

    # -------- Tranches --------
    def create_tranche_from_selection(self):
        if not self.selected_positions:
            messagebox.showinfo("No pages selected", "Select one or more positions in the grid first.")
            return

        vis_sorted = sorted(self.selected_positions)
        selected_orig_pages = [self.visual_page_order[pos - 1] for pos in vis_sorted]

        # Instrument-aware hint (text first, then OCR across first two pages)
        hint = self._guess_part_name_for_pages(selected_orig_pages)

        if hint:
            # ensure uniqueness
            existing = {t.name for t in self.tranches}
            candidate, n = hint, 2
            while candidate in existing:
                candidate = f"{hint} {n}"
                n += 1
            initial = candidate
        else:
            initial = self._suggest_tranche_name()  # numeric fallback

        name = simpledialog.askstring("New Tranche", "Enter a name:", initialvalue=initial)
        if not name:
            return

        self.tranches.append(Tranche(name=name.strip(), orig_pages=selected_orig_pages))

        for pos in sorted(self.selected_positions, reverse=True):
            del self.visual_page_order[pos - 1]

        self.selected_positions.clear()
        self.last_clicked_position = None
        self._update_tranche_list()
        self._render_grid()

    # --- Instrument detection (EN/IT/FR/DE) ---
    def _instrument_lexicon(self):
        # canonical -> list of STRICT "head" regexes (word tokens only)
        return {
            "FULL SCORE": [
                r"\bFULL\s+SCORE\b",
                r"\bCONDUCTOR(?:'S)?\s+SCORE\b",
                r"\bPARTITION\s+COMPL[ÈE]TE\b",   # FR: Partition complète
                r"\bPARTITURA\s+COMPLETA\b",      # IT/ES: Partitura completa
                r"\bPARTITUR\b",                  # DE: Partitur (commonly the full score)
            ],
            "SCORE": [
                r"\bSCORE\b",
                # Optional: add FR/DE bare nouns if you want generic “score” to match
                # r"\bPARTITION\b", r"\bPARTITURA\b"
            ],
            "Piccolo":        [r"\bpiccolo\b", r"\bottavino\b"],
            "Flute":          [r"\bflute\b", r"\bflûte\b", r"\bflauto\b", r"\bquerfl[oö]te\b", r"\balto\s+flute\b", r"\bbass\s+flute\b"],
            "Oboe":           [r"\boboe\b", r"\bhautbois\b"],
            "English Horn":   [r"\benglish\s*horn\b", r"\bcor\s+anglais\b", r"\bcorno\s+inglese\b", r"\benglischhorn\b"],
            "Clarinet":       [r"\bclarinet\b", r"\bclarinette\b", r"\bclarinetto\b", r"\bklarinette\b"],
            "Bass Clarinet":  [r"\bbass\s+clarinet\b", r"\bclarinette\s+basse\b", r"\bclarinetto\s+basso\b", r"\bbass?klarinette\b"],
            "Bassoon":        [r"\bbassoon\b", r"\bbasson\b", r"\bfagott[io]?\b"],  # fagotto/fagotti/Fagott
            "Contrabassoon":  [r"\bcontra?bassoon\b", r"\bcontre?basson\b", r"\bcontrafagotto\b", r"\bkontrafagott\b"],
            "Saxophone":      [r"\b(sax(?:ophone|ophon|ofono))\b(?:\s*(soprano|alto|tenor|baritone))?"],  # requires 'sax…' head
            "Horn":           [r"\bhorns?\b", r"\bcorni\b", r"\bcorno\b", r"\bh[oö]rner\b", r"\bhorn\b"],
            "Trumpet":        [r"\btrumpet\b", r"\btrompette\b", r"\btromba\b", r"\btrombe\b", r"\btrompete\b"],
            "Cornet":         [r"\bcornet\b"],
            "Flugelhorn":     [r"\bfl[uü]gelhorn\b", r"\bflicorno\b"],
            "Trombone":       [r"\btrombone\b", r"\bposaune\b", r"\btenor\s+trombone\b", r"\bbass\s+trombone\b"],
            "Euphonium":      [r"\beuphonium\b"],  # keep 'baritone'—still an instrument; safer now via title filter
            "Tuba":           [r"\btuba\b"],
            "Timpani":        [r"\btimpani\b", r"\btimpano\b", r"\btimbales\b", r"\bpauken\b"],
            "Percussion":     [r"\bpercussion\b", r"\bschlagzeug\b", r"\bbatteria\b", r"\bmallets\b"],
            "Harp":           [r"\bharp\b", r"\bharfe\b", r"\barpa\b"],
            "Piano":          [r"\bpiano\b", r"\bklavier\b"],
            "Violin":         [r"\bviolin\b", r"\bviolon\b", r"\bviolino\b", r"\bgeige\b", r"\bvioline\b"],
            "Viola":          [r"\bviola\b", r"\bbratsche\b"],  # removed plain 'alto'
            "Cello":          [r"\bcello\b", r"\bvioloncello\b", r"\bvioloncelle\b"],
            "Double Bass":    [r"\b(double|contra)\s*bass\b", r"\bcontrabbasso\b", r"\bcontrebasse\b", r"\bkontrabass\b", r"bbass\b(?!oon)"],
            # --- Brass band extensions ---
            "Soprano Cornet": [
                r"\bsop(?:r(?:ano)?)?\s*cornet\b", r"\beb\s*cornet\b", r"\be\s*flat\s*cornet\b", r"\be\s*b\s*cornet\b"
            ],
            "Solo Cornet":     [r"\bprincipal\s+cornet\b", r"\bsolo\s+cornet\b"],
            "Repiano Cornet":  [r"\brep(?:iano)?\s+cornet\b", r"\brep\.\s*cornet\b"],
            # generic section cornet remains; numbers (1/2/3/4) are extracted by _extract_part_number_and_key
            "Cornet":          [r"\bcornet\b"],

            "Flugelhorn":      [r"\bfl[uü]gelhorn\b", r"\bflugel\b", r"\bflicorno\b", r"\bflgh?\.?\b"],

            # Distinguish brass-band Eb (Tenor) horns from orchestral Horn in F
            "Tenor Horn": [
                r"\btenor\s+horn\b", r"\beb\s*horn\b", r"\be\s*flat\s*horn\b",
                r"\bsolo\s+horn\b", r"\b1(?:st)?\s+horn\b", r"\b2(?:nd)?\s+horn\b"
            ],

            "Baritone":        [r"\bbaritone\b", r"\bbar\.\b", r"\bbaritono\b"],
            "Euphonium":       [r"\beuphonium\b", r"\beuph\.?\b"],

            " Bass Trombone":  [r"\bbass\s+trombone\b", r"\btrombone\s+bass\b"],
            # keep generic Trombone as well (already present)

            # Brass-band tubas are “Basses”
            "E♭ Bass":         [r"\beb\s*bass(?:es)?\b", r"\beeb\s*bass\b", r"\be\s*flat\s*bass\b", r"\beb\s*tuba\b", r"\beeb\s*tuba\b", r"\bbass\s*\(\s*eb\s*\)\b"],
            "B♭ Bass":         [r"\bbb\s*bass(?:es)?\b", r"\bbbb\s*bass\b", r"\bb\s*flat\s*bass\b", r"\bbb\s*tuba\b", r"\bbbb\s*tuba\b", r"\bbass\s*\(\s*bb\s*\)\b"],

            "Drum Kit":        [r"\bdrum\s*kit\b", r"\bdrum\s*set\b"],

        }

    def _compile_instrument_patterns(self):
        if not hasattr(self, "_instrument_patterns"):
            pats = []
            for canon, variants in self._instrument_lexicon().items():
                for v in variants:
                    pats.append((canon, re.compile(v, re.IGNORECASE)))
            self._instrument_patterns = pats
        return self._instrument_patterns
    
    # --- put these near your imports ---
    

    # Map weird OCR/encoding variants to ASCII
    def _norm_text(self, s: str) -> str:
        s = s.upper()
        # normalize unicode (turns 'B♭' into 'B♭' with consistent form)
        s = unicodedata.normalize("NFKD", s)
        # common flat/accidental & OCR artifacts -> ASCII
        repl = {
            "♭": "B",           # B♭ -> BB after next rule
            "B¯": "BB",         # OCR artifact seen in some PDFs
            "B ̄": "BB",         # B + combining overbar
            "B B": "BB",        # split by spacing
            "B‐": "BB", "B-": "BB", "B–": "BB", "B —": "BB",
        }
        for k,v in repl.items():
            s = s.replace(k, v)
        # collapse multiple spaces/punct
        s = re.sub(r"[^\w\s]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    # Canonical name -> list of regex patterns to match
    INSTRUMENT_PATTERNS = [
        ("FULL SCORE", [r"\bFULL\s+SCORE\b", r"\bCONDUCTOR(?:'S)?\s+SCORE\b", r"\bPARTITION\s+COMPL[ÈE]TE\b", r"\bPARTITURA\s+COMPLETA\b", r"\bPARTITUR\b", r"\bPARTITURA\b", r"\bSCORE\b"]),
        ("SCORE", [r"\bSCORE\b"]),

        # woodwind
        ("FLUTE 1",   [r"\bFLUTE\s*1\b", r"\bFL 1\b"]),
        ("FLUTE 2",   [r"\bFLUTE\s*2\b", r"\bFL 2\b"]),
        ("FLUTE",     [r"\bFLUTE\b"]),
        ("PICCOLO",   [r"\bPICCOLO\b", r"\bPICC\b"]),
        ("OBOE",      [r"\bOBOE\b", r"\bHAUTBOIS\b", r"\bOBOE?\b"]),
        ("CLARINET IN BB 1", [r"\bBB\s*CLARINET\s*1\b", r"\bCLARINET\s*IN\s*BB\s*1\b"]),
        ("CLARINET IN BB 2", [r"\bBB\s*CLARINET\s*2\b", r"\bCLARINET\s*IN\s*BB\s*2\b"]),
        ("CLARINET IN BB",   [r"\bBB\s*CLARINET\b", r"\bCLARINET\s*IN\s*BB\b",
                            r"\bCLARINETTE\s*EN\s*SI\s*B\b", r"\bKLARINETTE\s*IN\s*B\b"]),
        ("BASS CLARINET",    [r"\bBB\s*BASS\s*CLARINET\b", r"\bBASS\s*CLARINET\b",
                            r"\bCLARINET(TE)?\s*BASSE\b", r"\bBASSKLARINETTE\b"]),
        ("BASSOON",   [r"\bBASSOON\b", r"\bFAGOTT(I)?\b", r"\bFAGOTTO\b"]),
        # brass
        ("TRUMPET IN BB 1", [r"\bBB\s*TRUMPET\s*1\b", r"\bTRUMPET\s*IN\s*BB\s*1\b"]),
        ("TRUMPET IN BB 2", [r"\bBB\s*TRUMPET\s*2\b", r"\bTRUMPET\s*IN\s*BB\s*2\b"]),
        ("TRUMPET IN BB 3", [r"\bBB\s*TRUMPET\s*3\b", r"\bTRUMPET\s*IN\s*BB\s*3\b"]),
        ("HORN IN F 1",     [r"\bF\s*HORN\s*1\b", r"\bHORN\s*IN\s*F\s*1\b"]),
        ("HORN IN F 2",     [r"\bF\s*HORN\s*2\b", r"\bHORN\s*IN\s*F\s*2\b"]),
        ("HORN IN F 3",     [r"\bF\s*HORN\s*3\b", r"\bHORN\s*IN\s*F\s*3\b"]),
        ("HORN IN F 4",     [r"\bF\s*HORN\s*4\b", r"\bHORN\s*IN\s*F\s*4\b"]),
        ("TROMBONE 1",      [r"\bTROMBONE\s*1\b", r"\bPOSAUNE\s*1\b"]),
        ("TROMBONE 2",      [r"\bTROMBONE\s*2\b", r"\bPOSAUNE\s*2\b"]),
        ("TROMBONE 3",      [r"\bTROMBONE\s*3\b", r"\bPOSAUNE\s*3\b"]),
        ("TUBA",            [r"\bTUBA\b"]),
        # strings & rhythm
        ("BASS",            [r"\bBASS\b(?!OON)", r"\bCONTRABASS(O)?\b", r"\bDOUBLE\s+BASS\b", r"\bCB\b"]),
        # --- Brass band specifics (place before generic "BASS") ---
        ("SOPRANO CORNET", [r"\bSOP(?:R(?:ANO)?)?\s*CORNET\b", r"\bEB\s*CORNET\b", r"\bE\s*FLAT\s*CORNET\b", r"\bE\s*B\s*CORNET\b"]),
        ("SOLO CORNET",    [r"\bPRINCIPAL\s+CORNET\b", r"\bSOLO\s+CORNET\b"]),
        ("REPIANO CORNET", [r"\bREPIANO\s+CORNET\b", r"\bREP\.\s*CORNET\b"]),
        ("CORNET 1",       [r"\bCORNET\s*1\b", r"\b1(?:ST)?\s+CORNET\b"]),
        ("CORNET 2",       [r"\bCORNET\s*2\b", r"\b2(?:ND)?\s+CORNET\b"]),
        ("CORNET 3",       [r"\bCORNET\s*3\b", r"\b3(?:RD)?\s+CORNET\b"]),
        ("CORNET 4",       [r"\bCORNET\s*4\b", r"\b4(?:TH)?\s+CORNET\b"]),
        ("FLUGEL HORN",    [r"\bFLUGEL(HORN)?\b", r"\bFLGH?\.?\b"]),
        ("TENOR HORN 1",   [r"\bTENOR\s+HORN\s*1\b", r"\bEB\s*HORN\s*1\b", r"\b1(?:ST)?\s+HORN\b"]),
        ("TENOR HORN 2",   [r"\bTENOR\s+HORN\s*2\b", r"\bEB\s*HORN\s*2\b", r"\b2(?:ND)?\s+HORN\b"]),
        ("TENOR HORN",     [r"\bTENOR\s+HORN\b", r"\bEB\s*HORN\b", r"\bE\s*FLAT\s*HORN\b"]),
        ("BARITONE 1",     [r"\bBARITONE\s*1\b", r"\b1(?:ST)?\s+BARITONE\b"]),
        ("BARITONE 2",     [r"\bBARITONE\s*2\b", r"\b2(?:ND)?\s+BARITONE\b"]),
        ("BARITONE",       [r"\bBARITONE\b", r"\bBAR\.\b"]),
        ("BASS TROMBONE",  [r"\bBASS\s+TROMBONE\b"]),
        # keep your existing TROMBONE 1/2/3 entries
        ("E B BASS 1",     [r"\bEB\s*BASS\s*1\b", r"\bE\s*FLAT\s*BASS\s*1\b", r"\bEEB\s*BASS\s*1\b", r"\bBASS\s*\(\s*EB\s*\)\s*1\b"]),
        ("E B BASS 2",     [r"\bEB\s*BASS\s*2\b", r"\bE\s*FLAT\s*BASS\s*2\b", r"\bEEB\s*BASS\s*2\b", r"\bBASS\s*\(\s*EB\s*\)\s*2\b"]),
        ("E B BASS",       [r"\bEB\s*BASS(?:ES)?\b", r"\bE\s*FLAT\s*BASS(?:ES)?\b", r"\bEEB\s*BASS\b", r"\bEB\s*TUBA\b", r"\bEEB\s*TUBA\b"]),
        ("B B BASS 1",     [r"\bBB\s*BASS\s*1\b", r"\bB\s*FLAT\s*BASS\s*1\b", r"\bBBB\s*BASS\s*1\b", r"\bBASS\s*\(\s*BB\s*\)\s*1\b"]),
        ("B B BASS 2",     [r"\bBB\s*BASS\s*2\b", r"\bB\s*FLAT\s*BASS\s*2\b", r"\bBBB\s*BASS\s*2\b", r"\bBASS\s*\(\s*BB\s*\)\s*2\b"]),
        ("B B BASS",       [r"\bBB\s*BASS(?:ES)?\b", r"\bB\s*FLAT\s*BASS(?:ES)?\b", r"\bBBB\s*BASS\b", r"\bBB\s*TUBA\b", r"\bBBB\s*TUBA\b"]),
        ("DRUM KIT",       [r"\bDRUM\s*KIT\b", r"\bDRUM\s*SET\b"]),

    ]

    def find_instrument_label_for_page(self, doc: fitz.Document, page_index: int, ocr_text: str | None = None) -> Optional[str]:
        """Try three zones (top-left, top-center, bottom band). Use PDF text first; fall back to OCR string if provided."""
        page = doc[page_index]
        w, h = page.rect.width, page.rect.height

        # Define zones as rectangles (x0, y0, x1, y1)
        zones = [
            fitz.Rect(0.00*w, 0.00*h, 0.35*w, 0.20*h),  # top-left
            fitz.Rect(0.30*w, 0.00*h, 0.70*w, 0.22*h),  # top-center-ish
            fitz.Rect(0.00*w, 0.78*h, 1.00*w, 1.00*h),  # bottom band (where "BASS" sits in your PDF)
        ]

        # Pull text per zone from PDF extraction
        pdf_zone_texts = []
        for r in zones:
            try:
                pdf_zone_texts.append(page.get_text("text", clip=r) or "")
            except Exception:
                pdf_zone_texts.append("")

        # Build candidate text corpus
        raw = "\n".join(pdf_zone_texts)
        if not raw and ocr_text:
            raw = ocr_text  # OCR fallback (already full-page)

        norm = self._norm_text(raw)

        # Try each instrument pattern in order
        for canon, pats in self.INSTRUMENT_PATTERNS:
            for pat in pats:
                if re.search(pat, norm, re.IGNORECASE):
                    return canon

        # If still nothing, try a last-chance “header line” heuristic
        header_guess = next((ln for ln in norm.splitlines() if 2 <= len(ln.split()) <= 5 and ln.isupper()), None)
        return header_guess



    def _roman_to_int(self, s: str) -> Optional[int]:
        s = s.upper().strip()
        if not re.fullmatch(r"[IVX]+", s): 
            return None
        vals = dict(I=1, V=5, X=10)
        total = 0
        prev = 0
        for ch in reversed(s):
            v = vals[ch]
            total = total - v if v < prev else total + v
            prev = v
        return total
    
    def _looks_like_title(self, text: str) -> bool:
        t = text.lower()
        # Common title-ish words; add more as you encounter them
        title_words = [
            "symphony", "symphonie", "sinfonia",
            "concerto", "concert", "konzert",
            "overture", "ouverture",
            "suite", "rhapsody", "rhapsodie",
            "movement", "mov.", "satz",
            "no.", "nr.", "opus", "op.", "bv", "k.", "kv", "bwv", "hob.", "d.",
            "act", "scene", "scena",
            "allegro", "andante", "adagio", "presto", "largo", "moderato"
        ]
        # If it’s mostly uppercase and fairly long, that’s also a hint of a big title line
        upper_ratio = sum(c.isupper() for c in text if c.isalpha()) / max(1, sum(c.isalpha() for c in text))
        if upper_ratio > 0.85 and len(text) >= 10:
            return True
        return any(w in t for w in title_words)

    def _band_for_part_headers(self, page: fitz.Page):
        h = page.rect.height
        # Ignore top 8% (titles), look in 8%–40%
        return (h * 0.08, h * 0.40)


    def _extract_part_number_and_key(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Pull things like 'I', 'II', '1', '2', '1st', and keys/transpositions like 'in Bb', 'in A', 'in Es (E♭)'.
        Returns (part_no, transposition) as display strings.
        """
        part_no = None
        # e.g., "I", "II", "III"
        m = re.search(r"\b([IVX]{1,3})\b", text, re.IGNORECASE)
        if m:
            num = self._roman_to_int(m.group(1))
            if num:
                part_no = str(num)
        # e.g., "1", "2", "1st", "2nd"
        if not part_no:
            m = re.search(r"\b([1-6])(?:st|nd|rd|th)?\b", text, re.IGNORECASE)
            if m:
                part_no = m.group(1)

        # transpositions: "in Bb/A/F/E♭/Es/B/H" (+ German)
        transp = None
        # Handle flats/sharps text and symbols
        key_map = {
            "bb": "B♭", "b♭": "B♭", "b": "B♭",  # de "B" is B♭ in German usage
            "eb": "E♭", "e♭": "E♭", "es": "E♭",
            "ab": "A♭", "a♭": "A♭", 
            "db": "D♭", "d♭": "D♭",
            "gb": "G♭", "g♭": "G♭",
            "f": "F", "a": "A", "c": "C", "d": "D", "e": "E", "g": "G", "h": "B",  # German H=B natural
            "b nat": "B", "b♮": "B",
        }
        m = re.search(r"\bin\s*([A-GH](?:[b#]|♭|♯)?|es|bb|eb|ab|db|gb|b(?:\s*nat)?|h)\b", text, re.IGNORECASE)
        if m:
            raw = m.group(1).lower().replace("♯", "#").replace("♭", "b").strip()
            transp = key_map.get(raw, raw.upper())
            # Normalize "Bb" -> "B♭", "#"/"b" ascii to glyphs
            transp = transp.replace("b", "♭").replace("#", "♯") if len(transp) <= 2 else transp

        return part_no, transp

    def _instrument_from_line(self, line: str) -> Optional[str]:
        patterns = self._compile_instrument_patterns()

        # 1) Try whitelist first (allows 'Full Score'/'Score' even if title-like)
        hits = [canon for canon, rx in patterns if rx.search(line)]
        if not hits:
            # 2) Only if nothing matched, apply the title guard
            if self._looks_like_title(line):
                return None
            else:
                return None  # nothing to do

        # Prefer most specific (longest) canonical name
        hits.sort(key=len, reverse=True)
        instrument = hits[0]

        # For Score, we don't add 'in X' or part numbers
        if instrument in {"Full Score", "Score"}:
            return instrument

        # Existing transposition/part number enrichment for instruments
        part_no, transp = self._extract_part_number_and_key(line)
        bits = [instrument]
        add_transp_for = {"Piccolo","Flute","Oboe","English Horn","Clarinet","Bass Clarinet",
                        "Bassoon","Contrabassoon","Saxophone","Horn","Trumpet","Cornet",
                        "Flugelhorn","Trombone","Euphonium"}
        if transp and instrument in add_transp_for:
            bits.append(f"in {transp}")
        if part_no:
            bits.append(part_no)
        return " ".join(bits)






    def _normalize_part_name(self, s: str) -> Optional[str]:
        if not s:
            return None
        # Clean common artifacts, trim whitespace/double spaces
        s = re.sub(r"[\r\n]+", " ", s)
        s = re.sub(r"\s{2,}", " ", s).strip(" -–—_.,")
        # Reject overly short or silly results
        if len(s) < 2:
            return None
        # Common header trash to ignore (tweak as you like)
        blacklist = {"score", "part", "page", "orchestral", "orchestra"}
        if s.lower() in blacklist:
            return None
        return s
    
    def _format_part_name(self, s: str) -> str:
        """Normalize instrument names to a single style:
        - Title case instruments
        - 'in' in lowercase
        - 'Bb'/'BB' -> 'B♭', 'Eb'/'EB' -> 'E♭', etc.
        - Map BASS (footer label) to 'Bass' (consistent with strings)
        """
        if not s:
            return s

        # Basic cleanup
        s = re.sub(r"\s+", " ", s.strip())

        # If it came in UPPERCASE (from pattern hits), drop to title case
        # but keep roman numerals as digits if they’re already digits
        # We’ll fix 'in' to lowercase later.
        t = s.title()

        # Make sure 'in' stays lowercase when used as a preposition
        t = re.sub(r"\bIn\b", "in", t)

        # Normalize common transposition spellings to glyphs
        # Do this *after* title-casing.
        def _flat_glyph(m):
            letter = m.group(1).upper()
            return f"{letter}♭"

        t = re.sub(r"\b([A-G])\s*[bB]\b", _flat_glyph, t)   # A b / Ab / A B -> A♭
        t = re.sub(r"\b([A-G])b\b", _flat_glyph, t)         # Ab (already)
        t = re.sub(r"\b([A-G])\s*#\b", lambda m: f"{m.group(1).upper()}♯", t)  # A # -> A♯

        # Special cases coming from your codenames
        t = re.sub(r"\bBb\b", "B♭", t)
        t = re.sub(r"\bEb\b", "E♭", t)
        t = re.sub(r"\bAb\b", "A♭", t)
        t = re.sub(r"\bDb\b", "D♭", t)
        t = re.sub(r"\bGb\b", "G♭", t)

        # Map footer 'BASS' to 'Bass' (string part), not 'Double Bass'
        # (Your whitelist “Double Bass” still formats as “Double Bass”)
        if t.strip().upper() == "BASS":
            t = "Bass"

        # If someone shouted 'CLARINET IN BB 1', ensure 'B♭'
        t = re.sub(r"\bin\s+Bb\b", "in B♭", t)

        return t


    def _extract_part_name_from_pdf_text(self, page: fitz.Page) -> Optional[str]:
        try:
            info = page.get_text("dict")
        except Exception:
            return None

        height = page.rect.height
        top_cut = height * 0.25

        candidates = []  # (score, display_name)
        for block in info.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                # y of first span, avg font size, joined text
                y0 = spans[0].get("bbox", [0, 0, 0, 0])[1]
                if y0 > top_cut:
                    continue
                avg_size = sum(s.get("size", 0) for s in spans) / max(1, len(spans))
                raw = " ".join(s.get("text", "") for s in spans)
                text = self._normalize_part_name(raw)
                if not text:
                    continue
                inst = self._instrument_from_line(text)
                if inst:
                    # Score: prefer bigger font and longer line slightly
                    score = avg_size * 3 + len(text) * 0.1
                    candidates.append((score, inst))

        if not candidates:
            return None
        candidates.sort(reverse=True, key=lambda x: x[0])
        return candidates[0][1]

    def _extract_part_name_via_ocr(self, page: fitz.Page) -> Optional[str]:
        try:
            rect = page.rect
            clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.25)
            scale = 220 / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img, lang="eng")
            if not text:
                return None
            lines = [self._normalize_part_name(l) for l in text.splitlines()]
            lines = [l for l in lines if l]
            cands = []
            for l in lines:
                inst = self._instrument_from_line(l)
                if inst:
                    score = sum(ch.isalpha() for ch in l) + len(l) * 0.05
                    cands.append((score, inst))
            if not cands:
                return None
            cands.sort(reverse=True, key=lambda x: x[0])
            return cands[0][1]
        except Exception:
            return None



    

    def _guess_part_name_for_pages(self, orig_pages: List[int]) -> Optional[str]:
        if not self.doc or not orig_pages:
            return None
        to_try = orig_pages[:2]  # sometimes pg 2 has the part header

        for p in to_try:
            page = self.doc[p - 1]
            name = self._extract_part_name_from_pdf_text(page)
            if name:
                return name

        for p in to_try:
            page = self.doc[p - 1]
            name = self._extract_part_name_via_ocr(page)
            if name:
                return name
        return None



    def _suggest_tranche_name(self) -> str:
        # numeric fallback
        existing = {t.name for t in self.tranches}
        def next_number():
            n = 1
            while str(n) in existing:
                n += 1
            return str(n)

        # try to detect instrument from the first selected page
        inst = None
        if self.doc and self.selected_positions:
            pos = min(self.selected_positions)
            orig_page_1based = self.visual_page_order[pos - 1]
            # NOTE: use the instance method version from the earlier fix
            inst_raw = self._find_instrument_label_for_page(orig_page_1based - 1)
            if inst_raw:
                inst = self._format_part_name(inst_raw)

        if not inst:
            return next_number()

        # ensure uniqueness: "Clarinet in B♭ 1", "Clarinet in B♭ 1 (2)", etc.
        candidate = inst
        k = 2
        while candidate in existing:
            candidate = f"{inst} ({k})"
            k += 1
        return candidate
    

    def _find_instrument_label_for_page(self, page_index: int, ocr_text: Optional[str] = None) -> Optional[str]:
        page = self.doc[page_index]
        w, h = page.rect.width, page.rect.height
        zones = [
            fitz.Rect(0.00*w, 0.00*h, 0.35*w, 0.20*h),
            fitz.Rect(0.30*w, 0.00*h, 0.70*w, 0.22*h),
            fitz.Rect(0.00*w, 0.78*h, 1.00*w, 1.00*h),
        ]
        pdf_zone_texts = []
        for r in zones:
            try:
                pdf_zone_texts.append(page.get_text("text", clip=r) or "")
            except Exception:
                pdf_zone_texts.append("")
        raw = "\n".join(pdf_zone_texts) or (ocr_text or "")
        norm = self._norm_text(raw)
        for canon, rx in self._compile_instrument_patterns():  # reuse compiled whitelist
            if rx.search(norm):
                return canon.upper()  # zone scan expects uppercase canonical
        return None





    def rename_tranche(self):
        sel = self.tranche_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        tr = self.tranches[idx]
        new = simpledialog.askstring("Rename Tranche", "New name:", initialvalue=tr.name)
        if new:
            tr.name = new.strip()
            self._update_tranche_list()

    def remove_tranche(self):
        sel = self.tranche_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        tr = self.tranches[idx]
        if messagebox.askyesno("Remove Tranche", f"Remove '{tr.name}'?"):
            del self.tranches[idx]
            self._update_tranche_list()

    def _update_tranche_buttons(self):
        has = bool(self.tranche_tree.selection())
        self.btn_rename.configure(state=(tk.NORMAL if has else tk.DISABLED))
        self.btn_remove.configure(state=(tk.NORMAL if has else tk.DISABLED))

    def _update_tranche_list(self):
        for iid in self.tranche_tree.get_children():
            self.tranche_tree.delete(iid)
        for i, t in enumerate(self.tranches):
            pages_str = self._positions_to_compact_ranges(t.orig_pages)
            self.tranche_tree.insert("", "end", iid=str(i), values=(t.name, pages_str))
        self._update_tranche_buttons()


    def _positions_to_compact_ranges(self, positions: List[int]) -> str:
        if not positions:
            return ""
        positions = sorted(positions)
        ranges: List[Tuple[int,int]] = []
        s = positions[0]
        prev = positions[0]
        for p in positions[1:]:
            if p == prev + 1:
                prev = p
            else:
                ranges.append((s, prev))
                s = prev = p
        ranges.append((s, prev))
        parts = [f"{a}" if a == b else f"{a}-{b}" for a,b in ranges]
        return ", ".join(parts)

    # -------- Drag & Drop --------
    def on_drag_start(self, event, pos: int):
        # Begin a drag of the single item under pointer (even if multiple selected)
        self.drag_src_pos = pos
        self.drag_hover_pos = pos
        self._refresh_selection_ui()

    def on_drag_motion(self, event):
        if self.drag_src_pos is None:
            return
        # Find widget under cursor and map to a visual position
        w = event.widget.winfo_containing(event.x_root, event.y_root)
        pos = self._frame_to_position(w)
        if pos != self.drag_hover_pos:
            self.drag_hover_pos = pos
            self._refresh_selection_ui()

    def on_drag_release(self, event):
        if self.drag_src_pos is None:
            return
        w = event.widget.winfo_containing(event.x_root, event.y_root)
        target = self._frame_to_position(w)

        src = self.drag_src_pos
        self.drag_src_pos = None
        self.drag_hover_pos = None

        if not target or target == src:
            self._refresh_selection_ui()
            return

        # Swap visual order entries
        i, j = src - 1, target - 1
        self.visual_page_order[i], self.visual_page_order[j] = self.visual_page_order[j], self.visual_page_order[i]

        # Update selection set to reflect swapped positions
        sel = set()
        for p in self.selected_positions: 
            if p == src:
                sel.add(target)
            elif p == target:
                sel.add(src)
            else:
                sel.add(p)
        self.selected_positions = sel

        # After reordering, re-render grid (simplest/robust)
        self._render_grid()

    def _frame_to_position(self, widget) -> Optional[int]:
        """Given any widget, climb up to a thumbnail frame and return its visual position (1-based)."""
        if not widget:
            return None
        # climb parents until found in self.thumb_frames
        cur = widget
        seen = set()
        while cur and cur not in seen:
            seen.add(cur)
            if cur in self.thumb_frames:
                # index in thumb_frames is visual_pos-1
                return self.thumb_frames.index(cur) + 1
            cur = cur.master
        return None

    # -------- Export --------
    def export_tranches(self):
        if not self.tranches:
            messagebox.showinfo("Nothing to export", "Create at least one tranche first.")
            return

        out_dir = filedialog.askdirectory(title="Choose output folder")
        if not out_dir:
            return

        try:
            reader = PdfReader(self.pdf_path)
        except Exception as e:
            messagebox.showerror("Export error", f"Failed to open PDF for export:\n{e}")
            return

        # Decrypt if needed (unchanged) ...
        if getattr(reader, "is_encrypted", False):
            try:
                ok = reader.decrypt("")
            except TypeError:
                ok = True
            if not ok:
                while True:
                    pwd = simpledialog.askstring("Password Required", "Enter PDF password for export:", show="*")
                    if pwd is None:
                        return
                    try:
                        ok = reader.decrypt(pwd)
                    except TypeError:
                        ok = True
                    if ok:
                        break
                    messagebox.showerror("Incorrect Password", "That password didn't work. Try again.")

        errors = []
        base = os.path.splitext(os.path.basename(self.pdf_path))[0]

        for tr in self.tranches:
            try:
                writer = PdfWriter()
                for orig_page in tr.orig_pages:
                    if 1 <= orig_page <= len(reader.pages):
                        writer.add_page(reader.pages[orig_page - 1])

                safe = self._sanitize_filename(tr.name) or "tranche"
                out_path = os.path.join(out_dir, f"{base}__{safe}.pdf")
                counter = 1
                final = out_path
                while os.path.exists(final):
                    final = os.path.join(out_dir, f"{base}__{safe}({counter}).pdf")
                    counter += 1
                with open(final, "wb") as f:
                    writer.write(f)
            except Exception as e:
                errors.append(f"{tr.name}: {e}")

        if errors:
            messagebox.showwarning("Export finished with errors", "Some tranches could not be exported:\n\n" + "\n".join(errors))
        else:
            messagebox.showinfo("Export complete", f"Exported {len(self.tranches)} tranche(s) to:\n{out_dir}")


    def _sanitize_filename(self, name: str) -> str:
        name = name.strip()
        name = re.sub(r'[\\/*?:"<>|]+', "_", name)
        name = re.sub(r"\s+", " ", name).strip()
        return name[:100]

    # -------- Layout callbacks --------
    def _on_grid_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

# ---------- main ----------
def main():
    app = PDFTrancheSplitterDnD()
    app.mainloop()

if __name__ == "__main__":
    main()
