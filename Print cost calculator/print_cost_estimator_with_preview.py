import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import PyPDF2
import math
import os
import platform
import subprocess
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4, A3
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from subprocess import call
import win32api
import win32print


class PDFSheetEstimatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Print Cost Estimator")

        self.pdf_data = []
        self.accepted_formats = set()  # Track accepted print formats

        # Cost settings (rest of your cost settings)
        self.cost_a4 = tk.DoubleVar(value=0.03)
        self.cost_a3 = tk.DoubleVar(value=0.05)
        self.ink_cost_a4 = tk.DoubleVar(value=0.01)
        self.ink_cost_a3 = tk.DoubleVar(value=0.015)
        self.photo_paper_surcharge = tk.DoubleVar(value=0.10)
        self.acetate_cost = tk.DoubleVar(value=0.15)

        self.printer_name = tk.StringVar()

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
        tk.Button(top, text="Print", command=self.start_printing).pack(side="left")

        tk.Label(top, text="Printer:").pack(side="left", padx=(20, 2))
        self.printer_combo = ttk.Combobox(top, textvariable=self.printer_name, width=40, state="readonly")
        self.printer_combo.pack(side="left")

        self.load_printers()
        

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
                    "file_name": file_name,
                    "pages": pages,
                    "qty_var": qty_var,
                    "type_var": type_var,
                    "binding_var": binding_var,
                    "front_cover_var": front_cover_var,
                    "back_cover_var": back_cover_var,
                    "acetate_var": acetate_var,
                    "paper_type_var": paper_type_var,
                    "accepted": False  # Add an 'accepted' flag
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

    def create_preview_pdf(self, items, print_format):
        """Creates a temporary PDF by merging documents of the same format for preview.
           It also handles cover removal.

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
        
    

    def print_pdf(self, pdf_path, printer_name=None):
        """Prints a PDF file using the default system printer."""
        try:
            system = platform.system()
            if system == "Windows":
                if printer_name:
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
                subprocess.call(["lp", pdf_path])
            elif system == "Linux":
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
        printer_name = "Brother MFC-L2710DW series" # Enter your default printer here
        for print_format, items in grouped_formats.items():
            if print_format in self.accepted_formats:
                # Batch print accepted formats
                for item in items:
                    self.print_pdf(item["file"], self.printer_name.get())
            else:
                # Create preview and ask for acceptance
                preview_path = self.create_preview_pdf(items, print_format)
                if preview_path:
                    # Open the PDF viewer for preview
                    self.open_pdf_viewer(preview_path)  # Call the new preview function

                    if messagebox.askyesno("Print Preview", f"Do you want to print this {print_format} format?\n"
                                                             f"Click 'Yes' to print and accept this format for batch printing.\n"
                                                             f"Click 'No' to cancel."):
                        # Print the preview and accept the format
                        self.print_pdf(preview_path, self.printer_name.get())
                        self.accepted_formats.add(print_format)
                        # Mark items as accepted
                        for item in items:
                            item["accepted"] = True
                    else:
                        messagebox.showinfo("Printing Cancelled", f"Printing cancelled for {print_format} format.")
                else:
                    messagebox.showerror("Error", f"Could not create preview for {print_format}.")


if __name__ == "__main__":
    root = tk.Tk()
    app = PDFSheetEstimatorApp(root)
    root.mainloop()
