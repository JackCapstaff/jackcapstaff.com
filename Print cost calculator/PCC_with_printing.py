import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import PyPDF2
import math
import os

class PDFSheetEstimatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Print Cost Estimator")

        self.pdf_data = []

        # Cost settings
        self.cost_a4 = tk.DoubleVar(value=0.03)
        self.cost_a3 = tk.DoubleVar(value=0.05)
        self.ink_cost_a4 = tk.DoubleVar(value=0.01)
        self.ink_cost_a3 = tk.DoubleVar(value=0.015)
        self.photo_paper_surcharge = tk.DoubleVar(value=0.10)
        self.acetate_cost = tk.DoubleVar(value=0.15)

        self.binding_costs = {
            "None": tk.DoubleVar(value=0.00),
            "Staple": tk.DoubleVar(value=0.10),
            "Plastic Comb": tk.DoubleVar(value=0.40),
            "Wire Comb": tk.DoubleVar(value=0.60)
        }

        self.bw_cover_costs = {
            "Card 300gsm": tk.DoubleVar(value=0.10),
            "Card 450gsm": tk.DoubleVar(value=0.15),
            "Card 600gsm": tk.DoubleVar(value=0.20)
        }
        self.colour_cover_costs = {
            "Card 300gsm": tk.DoubleVar(value=0.20),
            "Card 450gsm": tk.DoubleVar(value=0.30),
            "Card 600gsm": tk.DoubleVar(value=0.40)
        }

        self.build_gui()

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

        canvas_frame = tk.Frame(self.main_tab)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.result_label = tk.Label(self.main_tab, text="", font=("Arial", 10), justify="left")
        self.result_label.pack(pady=10)

    def build_settings_tab(self):
        row = 0
        tk.Label(self.settings_tab, text="Ink cost per side (A4):").grid(row=row, column=0, sticky="e")
        tk.Entry(self.settings_tab, textvariable=self.ink_cost_a4, width=6).grid(row=row, column=1)

        row += 1
        tk.Label(self.settings_tab, text="Ink cost per side (A3):").grid(row=row, column=0, sticky="e")
        tk.Entry(self.settings_tab, textvariable=self.ink_cost_a3, width=6).grid(row=row, column=1)

        row += 1
        tk.Label(self.settings_tab, text="Cost per A4 sheet:").grid(row=row, column=0, sticky="e")
        tk.Entry(self.settings_tab, textvariable=self.cost_a4, width=6).grid(row=row, column=1)

        row += 1
        tk.Label(self.settings_tab, text="Cost per A3 sheet:").grid(row=row, column=0, sticky="e")
        tk.Entry(self.settings_tab, textvariable=self.cost_a3, width=6).grid(row=row, column=1)

        row += 1
        tk.Label(self.settings_tab, text="Photo paper surcharge:").grid(row=row, column=0, sticky="e")
        tk.Entry(self.settings_tab, textvariable=self.photo_paper_surcharge, width=6).grid(row=row, column=1)

        row += 2
        tk.Label(self.settings_tab, text="Binding Costs:").grid(row=row, column=0, columnspan=2, pady=(10, 0))
        for k, var in self.binding_costs.items():
            row += 1
            tk.Label(self.settings_tab, text=f"{k}:").grid(row=row, column=0, sticky="e")
            tk.Entry(self.settings_tab, textvariable=var, width=6).grid(row=row, column=1)

        row += 2
        tk.Label(self.settings_tab, text="Card Cover Costs - B/W:").grid(row=row, column=0, columnspan=2, pady=(10, 0))
        for k, var in self.bw_cover_costs.items():
            row += 1
            tk.Label(self.settings_tab, text=f"{k}:").grid(row=row, column=0, sticky="e")
            tk.Entry(self.settings_tab, textvariable=var, width=6).grid(row=row, column=1)

        row += 2
        tk.Label(self.settings_tab, text="Card Cover Costs - Colour:").grid(row=row, column=0, columnspan=2, pady=(10, 0))
        for k, var in self.colour_cover_costs.items():
            row += 1
            tk.Label(self.settings_tab, text=f"{k}:").grid(row=row, column=0, sticky="e")
            tk.Entry(self.settings_tab, textvariable=var, width=6).grid(row=row, column=1)

        row += 2
        tk.Label(self.settings_tab, text="Acetate Cost per sheet:").grid(row=row, column=0, sticky="e")
        tk.Entry(self.settings_tab, textvariable=self.acetate_cost, width=6).grid(row=row, column=1)

    def import_pdfs(self):
        file_paths = filedialog.askopenfilenames(filetypes=[("PDF files", "*.pdf")])
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.pdf_data.clear()

        headers = [
            "File", "Pages", "Qty", "Print Type", "Binding", "Front Cover", "Back Cover",
            "Acetate", "Paper Type"
        ]
        for col, h in enumerate(headers):
            tk.Label(self.scrollable_frame, text=h, font=("Arial", 9, "bold")).grid(row=0, column=col, sticky="w", padx=2)

        for idx, file in enumerate(file_paths):
            try:
                with open(file, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    pages = len(reader.pages)

                file_name = os.path.basename(file)

                # Variables
                qty_var = tk.StringVar(value="1")
                type_var = tk.StringVar(value="A3 Booklet")
                binding_var = tk.StringVar(value="None")
                front_cover_var = tk.StringVar(value="None")
                back_cover_var = tk.StringVar(value="None")
                acetate_var = tk.StringVar(value="None")
                paper_type_var = tk.StringVar(value="Standard")

                row = idx + 1
                tk.Label(self.scrollable_frame, text=file_name, width=25, anchor="w").grid(row=row, column=0, sticky="w")
                tk.Label(self.scrollable_frame, text=str(pages)).grid(row=row, column=1)
                tk.Entry(self.scrollable_frame, textvariable=qty_var, width=4).grid(row=row, column=2)

                type_menu = ttk.Combobox(self.scrollable_frame, textvariable=type_var, width=14, state="readonly",
                                         values=["A3 Single-sided", "A3 Double-sided", "A3 Booklet",
                                                 "A4 Single-sided", "A4 Double-sided", "A4 Booklet"])
                type_menu.grid(row=row, column=3)

                binding_menu = ttk.Combobox(self.scrollable_frame, textvariable=binding_var, width=12, state="readonly",
                                            values=list(self.binding_costs.keys()))
                binding_menu.grid(row=row, column=4)

                cover_options = ["None"] + \
                    [f"{k} (B/W)" for k in self.bw_cover_costs.keys()] + \
                    [f"{k} (Colour)" for k in self.colour_cover_costs.keys()]
                ttk.Combobox(self.scrollable_frame, textvariable=front_cover_var, width=18, state="readonly",
                             values=cover_options).grid(row=row, column=5)
                ttk.Combobox(self.scrollable_frame, textvariable=back_cover_var, width=18, state="readonly",
                             values=cover_options).grid(row=row, column=6)

                acetate_menu = ttk.Combobox(self.scrollable_frame, textvariable=acetate_var, width=10, state="readonly",
                                            values=["None", "Front", "Back", "Both"])
                acetate_menu.grid(row=row, column=7)

                paper_menu = ttk.Combobox(self.scrollable_frame, textvariable=paper_type_var, width=10, state="readonly",
                                          values=["Standard", "Photo"])
                paper_menu.grid(row=row, column=8)

                self.pdf_data.append({
                    "file": file,
                    "pages": pages,
                    "qty_var": qty_var,
                    "type_var": type_var,
                    "binding_var": binding_var,
                    "front_cover_var": front_cover_var,
                    "back_cover_var": back_cover_var,
                    "acetate_var": acetate_var,
                    "paper_type_var": paper_type_var
                })

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load {file}:\n{str(e)}")

        self.recalculate_total()

    def recalculate_total(self):
        total_pages = 0
        total_sheets = 0
        total_cost = 0

        for item in self.pdf_data:
            qty = int(item["qty_var"].get())
            pages = item["pages"] * qty
            pages_per_sheet = self.get_pages_per_sheet(item["type_var"].get())
            sheets = math.ceil(pages / pages_per_sheet)

            # Costs
            base_cost = self.cost_a3.get() if "A3" in item["type_var"].get() else self.cost_a4.get()
            ink_cost = pages * (self.ink_cost_a3.get() if "A3" in item["type_var"].get() else self.ink_cost_a4.get())
            if item["paper_type_var"].get() == "Photo":
                base_cost += self.photo_paper_surcharge.get()
            sheet_cost = sheets * base_cost

            # Covers
            def cover_price(cstr):
                if cstr == "None": return 0.0
                base, type_ = cstr.split(" (")
                type_ = type_.strip(")")
                return (self.bw_cover_costs if type_ == "B/W" else self.colour_cover_costs)[base].get()

            front_cover_cost = cover_price(item["front_cover_var"].get())
            back_cover_cost = cover_price(item["back_cover_var"].get())

            # Binding
            binding_cost = self.binding_costs[item["binding_var"].get()].get()

            # Acetate
            acetate_map = {"None": 0, "Front": 1, "Back": 1, "Both": 2}
            acetate_count = acetate_map[item["acetate_var"].get()]
            acetate_total = acetate_count * self.acetate_cost.get()

            doc_cost = sheet_cost + ink_cost + front_cover_cost + back_cover_cost + binding_cost + acetate_total

            total_cost += doc_cost
            total_pages += pages
            total_sheets += sheets

        self.result_label.config(
            text=f"Total Pages: {total_pages}\nTotal Sheets: {total_sheets}\nEstimated Cost: £{total_cost:.2f}"
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


if __name__ == "__main__":
    root = tk.Tk()
    app = PDFSheetEstimatorApp(root)
    root.mainloop()
