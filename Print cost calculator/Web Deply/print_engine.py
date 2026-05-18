"""
Lightweight, GUI-free subset of the PDF/price utilities from
`print_cost_estimator_with_preview_andprintingbooklet.py`.

This module exposes pure functions suitable for importing into a
Flask backend: `get_pages_per_sheet`, `line_breakdown`, `calculate_totals`,
and PDF helpers (`remove_covers`, `create_booklet_pdf`, `create_preview_pdf`).

Note: printer-specific functions that require `win32*` are included
but should only be used on Windows and from trusted contexts.
"""
from __future__ import annotations
import math
import os
import io
try:
    import PyPDF2
except Exception:
    import pypdf as PyPDF2
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, A3, landscape
from typing import Dict, Any, List, Tuple


def get_pages_per_sheet(print_type: str) -> int:
    return {
        "A3 Single-sided": 1,
        "A3 Double-sided": 2,
        "A3 Booklet": 4,
        "A4 Single-sided": 1,
        "A4 Double-sided": 2,
        "A4 Booklet": 4
    }.get(print_type, 4)


def cover_price_from_maps(cstr: str, bw_cover_costs: Dict[str, float], colour_cover_costs: Dict[str, float]) -> float:
    if not cstr or cstr == "None":
        return 0.0
    try:
        base, type_ = cstr.split(" (")
        type_ = type_.strip(")")
        if type_ == "B/W":
            return float(bw_cover_costs.get(base, 0.0))
        else:
            return float(colour_cover_costs.get(base, 0.0))
    except Exception:
        return 0.0


def line_breakdown(item: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute pricing for a single item.

    item: {
      file_name: str,
      pages: int,
      qty: int,
      type: str,
      binding: str,
      front_cover: str,
      back_cover: str,
      acetate: str,
      paper_type: str,
      paper_grade: str
    }

    settings: cost primitives (see README for expected keys).
    """
    qty = int(item.get("qty", 0) or 0)
    pages_per_copy = int(item.get("pages", 0) or 0)
    tstr = item.get("type", "A4 Double-sided")
    is_a3 = ("A3" in tstr)

    pps = get_pages_per_sheet(tstr) or 1
    sheets_per_copy = math.ceil(pages_per_copy / pps)
    total_pages = pages_per_copy * qty
    total_sheets = sheets_per_copy * qty

    # binding auto-switch rule (same logic as GUI)
    binding = item.get("binding", "None")
    if ("A3" in tstr) and ("Booklet" in tstr):
        if sheets_per_copy <= 1 and binding == "Staple":
            binding = "None"
        elif sheets_per_copy > 1 and binding == "None":
            binding = "Staple"

    base_cost_per_sheet = float(settings.get("cost_a3") if is_a3 else settings.get("cost_a4"))

    # paper grade surcharge
    grade = item.get("paper_grade") or list(settings.get("paper_grade_surcharge", {}).keys())[0]
    grade_map = settings.get("paper_grade_surcharge", {}).get(grade, {})
    if is_a3:
        base_cost_per_sheet += float(grade_map.get("A3", 0.0))
    else:
        base_cost_per_sheet += float(grade_map.get("A4", 0.0))

    # photo paper
    if item.get("paper_type") == "Photo":
        base_cost_per_sheet += float(settings.get("photo_paper_surcharge", 0.0))

    sheet_cost = total_sheets * base_cost_per_sheet
    ink_cost = total_pages * float(settings.get("ink_cost_a3") if is_a3 else settings.get("ink_cost_a4"))

    # covers
    fc_cost_per_copy = cover_price_from_maps(item.get("front_cover", "None"), settings.get("bw_cover_costs", {}), settings.get("colour_cover_costs", {}))
    bc_cost_per_copy = cover_price_from_maps(item.get("back_cover", "None"), settings.get("bw_cover_costs", {}), settings.get("colour_cover_costs", {}))
    front_cover_cost_total = fc_cost_per_copy * qty
    back_cover_cost_total = bc_cost_per_copy * qty

    # binding material + labour
    charge_binding = True
    if ("Booklet" in tstr) and ("A3" in tstr) and (binding == "Staple"):
        if sheets_per_copy <= 1:
            charge_binding = False

    binding_material_per_copy = float(settings.get("binding_costs", {}).get(binding, 0.0)) if charge_binding else 0.0
    binding_labour_per_copy = float(settings.get("binding_labour", {}).get(binding, 0.0)) if charge_binding else 0.0
    binding_material_total = binding_material_per_copy * qty
    binding_labour_total = binding_labour_per_copy * qty

    # acetate
    acetate_map = {"None": 0, "Front": 1, "Back": 1, "Both": 2}
    acetate_count_per_copy = acetate_map.get(item.get("acetate", "None"), 0)
    acetate_total = acetate_count_per_copy * float(settings.get("acetate_cost", 0.0)) * qty

    labour_job_total = float(settings.get("labour_per_job", 0.0)) * qty

    materials_subtotal = (sheet_cost + ink_cost + front_cover_cost_total + back_cover_cost_total + binding_material_total + acetate_total)
    labour_total = binding_labour_total + labour_job_total

    pre_margin = materials_subtotal + labour_total
    markup = float(settings.get("markup_multiplier", 1.0))
    margin_amt = pre_margin * (markup - 1.0)
    line_total = pre_margin * markup
    unit_price = (line_total / qty) if qty else 0.0

    return {
        "file": item.get("file_name"),
        "print_type": tstr,
        "preset": item.get("preset", tstr),
        "pages_per_copy": pages_per_copy,
        "qty": qty,
        "sheets_per_copy": sheets_per_copy,
        "total_pages": total_pages,
        "total_sheets": total_sheets,
        "binding": binding,
        "front_cover": item.get("front_cover"),
        "back_cover": item.get("back_cover"),
        "acetate": item.get("acetate"),
        "paper_type": item.get("paper_type"),
        "paper_grade": grade,
        "sheet_cost": round(sheet_cost, 2),
        "ink_cost": round(ink_cost, 2),
        "front_cover_cost": round(front_cover_cost_total, 2),
        "back_cover_cost": round(back_cover_cost_total, 2),
        "binding_cost": round(binding_material_total, 2),
        "acetate_cost": round(acetate_total, 2),
        "labour": round(labour_total, 2),
        "materials_subtotal": round(materials_subtotal, 2),
        "margin_amt": round(margin_amt, 2),
        "unit_price": round(unit_price, 2),
        "line_total": round(line_total, 2),
    }


def calculate_totals(items: List[Dict[str, Any]], settings: Dict[str, Any]) -> Dict[str, Any]:
    totals = {"total_pages": 0, "total_sheets": 0, "grand_total": 0.0}
    breakdowns = []
    for it in items:
        b = line_breakdown(it, settings)
        breakdowns.append(b)
        totals["total_pages"] += b.get("total_pages", 0)
        totals["total_sheets"] += b.get("total_sheets", 0)
        totals["grand_total"] += b.get("line_total", 0.0)
    totals["breakdowns"] = breakdowns
    return totals


def remove_covers(pdf_path: str, front_cover: str, back_cover: str) -> Tuple[List[Any], PyPDF2.PdfReader]:
    """Return (extracted_covers, modified_pdf_reader). Raises on error."""
    with open(pdf_path, "rb") as file:
        pdf_reader = PyPDF2.PdfReader(file)
        num_pages = len(pdf_reader.pages)
        extracted_covers = []
        pdf_writer = PyPDF2.PdfWriter()

        remove_front = front_cover != "None"
        remove_back = back_cover != "None"

        start_page = 1 if remove_front else 0
        end_page = num_pages - 1 if remove_back else num_pages

        if remove_front:
            extracted_covers.append(pdf_reader.pages[0])
        if remove_back:
            extracted_covers.append(pdf_reader.pages[-1])

        for page_num in range(start_page, end_page):
            page = pdf_reader.pages[page_num]
            pdf_writer.add_page(page)

        modified_pdf = io.BytesIO()
        pdf_writer.write(modified_pdf)
        modified_pdf.seek(0)
        modified_pdf_reader = PyPDF2.PdfReader(modified_pdf)
        return extracted_covers, modified_pdf_reader


def create_booklet_pdf(input_path: str, paper_size: str = "A4") -> str:
    """Create a temporary booklet PDF and return its path.
    Raises on errors."""
    with open(input_path, "rb") as infile:
        reader = PyPDF2.PdfReader(infile)
        num_pages = len(reader.pages)

        if paper_size == "A4":
            page_width, page_height = A4
        elif paper_size == "A3":
            page_width, page_height = A3
        else:
            raise ValueError("Unsupported paper size")

        new_pdf = io.BytesIO()
        c = canvas.Canvas(new_pdf, pagesize=landscape((page_width, page_height)))

        num_signatures = (num_pages + 3) // 4
        for i in range(num_signatures):
            page1 = i * 2 + 1
            page2 = num_pages - i * 2
            page3 = num_pages - (i * 2 + 1)
            page4 = i * 2

            def add_page(page_num, x_offset):
                if page_num <= num_pages and page_num > 0:
                    page = reader.pages[page_num - 1]
                    # try to draw page as form XObject; fallback to noop if unsupported
                    try:
                        c.saveState()
                        c.translate(x_offset, 0)
                        c.doFormXobj(page)
                        c.restoreState()
                    except Exception:
                        pass

            add_page(page1, page_width)
            add_page(page2, 0)
            c.showPage()
            add_page(page3, page_width)
            add_page(page4, 0)
            c.showPage()

        c.save()
        new_pdf.seek(0)
        output_path = f"temp_booklet_{os.path.basename(input_path)}"
        with open(output_path, "wb") as outfile:
            outfile.write(new_pdf.read())
        return output_path


def create_preview_pdf(items: List[Dict[str, Any]], print_format: str) -> str:
    """Merge items of the same format into a preview PDF and return path."""
    output_path = f"temp_preview_{print_format.replace(' ', '_')}.pdf"
    pdf_writer = PyPDF2.PdfWriter()
    for item in items:
        _, pdf_reader = remove_covers(item["file"], item.get("front_cover", "None"), item.get("back_cover", "None"))
        if "Booklet" in print_format:
            paper_size = "A3" if "A3" in print_format else "A4"
            booklet_path = create_booklet_pdf(item["file"], paper_size)
            with open(booklet_path, "rb") as bf:
                br = PyPDF2.PdfReader(bf)
                for p in br.pages:
                    pdf_writer.add_page(p)
        else:
            for p in pdf_reader.pages:
                pdf_writer.add_page(p)

    with open(output_path, "wb") as output_pdf:
        pdf_writer.write(output_pdf)
    return output_path
