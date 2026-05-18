import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
from PIL import Image, ImageTk
import fitz  # PyMuPDF
from PyPDF2 import PdfReader, PdfWriter
import io

class PDFSplitterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Splitter Tool")
        self.doc = None
        self.page_images = []
        self.pdf_path = ""
        self.setup_ui()

    def setup_ui(self):
        self.canvas = tk.Canvas(self.root, width=600, height=800)
        self.canvas.pack()

        btn_frame = tk.Frame(self.root)
        btn_frame.pack()

        tk.Button(btn_frame, text="Open PDF", command=self.open_pdf).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="Add Range", command=self.add_range).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="Export Ranges", command=self.export_ranges).pack(side=tk.LEFT)

        self.range_listbox = tk.Listbox(self.root)
        self.range_listbox.pack(fill=tk.X)

        self.page_slider = tk.Scale(self.root, from_=0, to=0, orient=tk.HORIZONTAL, command=self.show_page)
        self.page_slider.pack(fill=tk.X)

    def open_pdf(self):
        self.pdf_path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if not self.pdf_path:
            return

        password = simpledialog.askstring("Password", "Enter PDF password (if any):", show="*")

        try:
            self.doc = fitz.open(self.pdf_path)
            if self.doc.needs_pass:
                if not self.doc.authenticate(password or ""):
                    messagebox.showerror("Error", "Incorrect password.")
                    return
            self.page_slider.config(to=len(self.doc) - 1)
            self.show_page(0)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open PDF: {e}")

    def show_page(self, page_num):
        try:
            page = self.doc[int(page_num)]
            pix = page.get_pixmap(matrix=fitz.Matrix(0.6, 0.6))
            img_data = Image.open(io.BytesIO(pix.tobytes("png")))
            img = ImageTk.PhotoImage(img_data)
            self.canvas.image = img
            self.canvas.create_image(0, 0, anchor=tk.NW, image=img)
        except Exception as e:
            print(f"Error showing page: {e}")

    def add_range(self):
        start = simpledialog.askinteger("Range Start", "Start page (1-based index):")
        end = simpledialog.askinteger("Range End", "End page (1-based index):")
        if start and end and 1 <= start <= end <= len(self.doc):
            self.range_listbox.insert(tk.END, f"{start}-{end}")
        else:
            messagebox.showerror("Invalid Range", "Please enter a valid page range.")

    def export_ranges(self):
        output_dir = filedialog.askdirectory(title="Select Output Directory")
        if not output_dir:
            return

        reader = PdfReader(self.pdf_path)
        for idx, item in enumerate(self.range_listbox.get(0, tk.END), start=1):
            start, end = map(int, item.split("-"))
            writer = PdfWriter()
            for page_num in range(start - 1, end):
                writer.add_page(reader.pages[page_num])

            output_path = f"{output_dir}/split_part_{idx}_{start}-{end}.pdf"
            with open(output_path, "wb") as f_out:
                writer.write(f_out)

        messagebox.showinfo("Success", "PDF chunks exported successfully!")

if __name__ == "__main__":
    root = tk.Tk()
    app = PDFSplitterApp(root)
    root.mainloop()
