import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

import pandas as pd

SCHEDULE_FILE = "Rehearsal_schedule.xlsx"     # output of script 2, input to script 3
TIMED_FILE    = "timed_rehearsal.xlsx"        # output of script 3, input to script 4

SCRIPT_3 = "3-Organised_rehearsal_with_time.py"
SCRIPT_4 = "4 - Final Compile and PDF.py"


REQUIRED_COLS = ["Rehearsal", "Title", "Rehearsal Time (minutes)"]
OPTIONAL_COLS = ["PlayerLoad", "GroupKey", "MovementOrder"]  # script 3 can fall back if missing


class ScheduleEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Rehearsal Schedule Editor")
        self.geometry("1050x650")

        self.df = pd.DataFrame()
        self.current_rehearsal = None

        self._drag_item = None

        self._build_ui()

    def _build_ui(self):
        # Top toolbar
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(top, text="Open Rehearsal_schedule.xlsx", command=self.open_schedule).pack(side=tk.LEFT)
        ttk.Button(top, text="Save", command=self.save_schedule).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(top, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(top, text="Run Script 3 (timed excel)", command=self.run_script_3).pack(side=tk.LEFT)
        ttk.Button(top, text="Run Script 4 (PDF)", command=self.run_script_4).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(top, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(top, text="Add Item", command=self.add_item).pack(side=tk.LEFT)
        ttk.Button(top, text="Edit Minutes", command=self.edit_minutes).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(top, text="Remove Item", command=self.remove_item).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(top, text="Move to…", command=self.move_item).pack(side=tk.LEFT, padx=(8, 0))

        # Main split
        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        # Left: rehearsal list
        left = ttk.Frame(main, padding=8)
        main.add(left, weight=1)

        ttk.Label(left, text="Rehearsals").pack(anchor="w")
        self.reh_list = tk.Listbox(left, height=25)
        self.reh_list.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.reh_list.bind("<<ListboxSelect>>", self.on_select_rehearsal)

        # Right: items table
        right = ttk.Frame(main, padding=8)
        main.add(right, weight=4)

        ttk.Label(right, text="Items (drag to reorder within this rehearsal)").pack(anchor="w")

        cols = ("Title", "Minutes")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=22)
        self.tree.heading("Title", text="Title")
        self.tree.heading("Minutes", text="Minutes")
        self.tree.column("Title", width=700)
        self.tree.column("Minutes", width=120, anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        # Drag & drop reorder (within rehearsal)
        self.tree.bind("<ButtonPress-1>", self.on_drag_start)
        self.tree.bind("<B1-Motion>", self.on_drag_motion)
        self.tree.bind("<ButtonRelease-1>", self.on_drag_drop)

        # Status bar
        self.status = tk.StringVar(value="Open a schedule to begin.")
        bar = ttk.Label(self, textvariable=self.status, relief=tk.SUNKEN, anchor="w", padding=6)
        bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ------------------------
    # File IO
    # ------------------------
    def open_schedule(self):
        path = filedialog.askopenfilename(
            title="Select Rehearsal_schedule.xlsx",
            filetypes=[("Excel files", "*.xlsx")],
        )
        if not path:
            return

        try:
            df = pd.read_excel(path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed reading Excel:\n{e}")
            return

        # Basic validation + normalize
        missing = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing:
            messagebox.showerror(
                "Missing columns",
                f"Your schedule is missing required columns:\n{missing}\n\n"
                f"Expected at least: {REQUIRED_COLS}"
            )
            return

        df = df.copy()
        df["Rehearsal"] = pd.to_numeric(df["Rehearsal"], errors="coerce").astype("Int64")
        df["Title"] = df["Title"].astype(str)
        df["Rehearsal Time (minutes)"] = pd.to_numeric(df["Rehearsal Time (minutes)"], errors="coerce").fillna(0).astype(int)

        for c in OPTIONAL_COLS:
            if c not in df.columns:
                df[c] = None

        self.df = df
        self._refresh_rehearsal_list()
        self.status.set(f"Loaded: {os.path.basename(path)}  ({len(self.df)} rows)")

    def save_schedule(self):
        if self.df.empty:
            messagebox.showinfo("Nothing to save", "No schedule loaded.")
            return

        # Save in same “canonical” format script 3 expects
        out = self.df[REQUIRED_COLS + OPTIONAL_COLS].copy()

        try:
            out.to_excel(SCHEDULE_FILE, index=False)
        except Exception as e:
            messagebox.showerror("Error", f"Failed writing {SCHEDULE_FILE}:\n{e}")
            return

        self.status.set(f"Saved → {SCHEDULE_FILE}")

    # ------------------------
    # Rehearsal navigation
    # ------------------------
    def _refresh_rehearsal_list(self):
        self.reh_list.delete(0, tk.END)
        rehs = sorted([int(x) for x in self.df["Rehearsal"].dropna().unique()])
        for r in rehs:
            self.reh_list.insert(tk.END, str(r))

        if rehs:
            self.reh_list.selection_set(0)
            self.current_rehearsal = rehs[0]
            self._load_tree_for_rehearsal(rehs[0])

    def on_select_rehearsal(self, _evt=None):
        sel = self.reh_list.curselection()
        if not sel:
            return
        r = int(self.reh_list.get(sel[0]))
        self.current_rehearsal = r
        self._load_tree_for_rehearsal(r)

    def _load_tree_for_rehearsal(self, r: int):
        self.tree.delete(*self.tree.get_children())
        block = self.df[self.df["Rehearsal"] == r].reset_index(drop=True)

        # Keep existing order by row appearance (important!)
        for i, row in block.iterrows():
            self.tree.insert("", tk.END, iid=str(i), values=(row["Title"], row["Rehearsal Time (minutes)"]))

        self.status.set(f"Viewing rehearsal {r} — {len(block)} items")

    def _write_tree_back(self):
        """Write the TreeView order back into self.df for the current rehearsal."""
        if self.current_rehearsal is None:
            return

        r = self.current_rehearsal
        block = self.df[self.df["Rehearsal"] == r].copy()

        # Build reordered block from tree rows
        new_rows = []
        for item_id in self.tree.get_children(""):
            title, mins = self.tree.item(item_id, "values")
            # take first matching row from block (keeps optional cols)
            hit = block[block["Title"] == title].head(1)
            if hit.empty:
                # fallback: create minimal row
                new_rows.append({
                    "Rehearsal": r, "Title": title,
                    "Rehearsal Time (minutes)": int(mins),
                    "PlayerLoad": None, "GroupKey": title, "MovementOrder": None
                })
            else:
                row = hit.iloc[0].to_dict()
                row["Rehearsal Time (minutes)"] = int(mins)
                new_rows.append(row)
                block = block.drop(hit.index)

        new_block = pd.DataFrame(new_rows)

        # Replace rows for this rehearsal in the master df (preserve other rehearsals)
        others = self.df[self.df["Rehearsal"] != r].copy()
        self.df = pd.concat([others, new_block], ignore_index=True)

    # ------------------------
    # Drag/drop reorder
    # ------------------------
    def on_drag_start(self, event):
        iid = self.tree.identify_row(event.y)
        self._drag_item = iid

    def on_drag_motion(self, event):
        if not self._drag_item:
            return
        target = self.tree.identify_row(event.y)
        if target and target != self._drag_item:
            # Move dragged item just above target
            self.tree.move(self._drag_item, "", self.tree.index(target))

    def on_drag_drop(self, _event):
        if self._drag_item:
            self._write_tree_back()
        self._drag_item = None

    # ------------------------
    # Editing actions
    # ------------------------
    def _get_selected_tree_item(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def add_item(self):
        if self.current_rehearsal is None:
            return
        title = simpledialog.askstring("Add item", "Title:")
        if not title:
            return
        mins = simpledialog.askinteger("Add item", "Minutes:", minvalue=1, maxvalue=600)
        if not mins:
            return

        self.tree.insert("", tk.END, values=(title, int(mins)))
        self._write_tree_back()

    def edit_minutes(self):
        item = self._get_selected_tree_item()
        if not item:
            messagebox.showinfo("Select an item", "Select an item first.")
            return
        title, mins = self.tree.item(item, "values")
        new_mins = simpledialog.askinteger("Edit minutes", f"Minutes for:\n{title}", initialvalue=int(mins), minvalue=1, maxvalue=600)
        if not new_mins:
            return
        self.tree.item(item, values=(title, int(new_mins)))
        self._write_tree_back()

    def remove_item(self):
        item = self._get_selected_tree_item()
        if not item:
            return
        self.tree.delete(item)
        self._write_tree_back()

    def move_item(self):
        if self.current_rehearsal is None:
            return
        item = self._get_selected_tree_item()
        if not item:
            return

        title, mins = self.tree.item(item, "values")
        all_rehs = sorted([int(x) for x in self.df["Rehearsal"].dropna().unique()])

        target = simpledialog.askinteger(
            "Move item",
            f"Move '{title}' to rehearsal number:\nAvailable: {all_rehs}",
            minvalue=min(all_rehs) if all_rehs else 1,
            maxvalue=max(all_rehs) if all_rehs else 999
        )
        if not target:
            return

        # Remove from current tree
        self.tree.delete(item)

        # Append to target rehearsal in df
        row = {
            "Rehearsal": int(target),
            "Title": str(title),
            "Rehearsal Time (minutes)": int(mins),
            "PlayerLoad": None,
            "GroupKey": str(title),
            "MovementOrder": None
        }
        self.df = pd.concat([self.df, pd.DataFrame([row])], ignore_index=True)

        # Write current rehearsal order back and refresh lists
        self._write_tree_back()
        self._refresh_rehearsal_list()
        self.status.set(f"Moved '{title}' → rehearsal {target}")

    # ------------------------
    # Run your pipeline steps
    # ------------------------
    def run_script_3(self):
        self.save_schedule()
        if not os.path.exists(SCRIPT_3):
            messagebox.showerror("Missing file", f"Can't find {SCRIPT_3} in this folder.")
            return
        try:
            subprocess.check_call(["python", SCRIPT_3])
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Script 3 failed", str(e))
            return
        self.status.set(f"Ran script 3 → {TIMED_FILE}")

    def run_script_4(self):
        if not os.path.exists(SCRIPT_4):
            messagebox.showerror("Missing file", f"Can't find {SCRIPT_4} in this folder.")
            return
        if not os.path.exists(TIMED_FILE):
            messagebox.showerror("Missing timed file", f"Can't find {TIMED_FILE}. Run Script 3 first.")
            return
        try:
            subprocess.check_call(["python", SCRIPT_4])
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Script 4 failed", str(e))
            return
        self.status.set("Ran script 4 → PDF generated.")


if __name__ == "__main__":
    app = ScheduleEditor()
    app.mainloop()
