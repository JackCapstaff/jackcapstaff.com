import os
from datetime import datetime
import pandas as pd

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
)

# =========================
# Config
# =========================
INPUT_XLSX = "timed_rehearsal.xlsx"   # <- reads your Script 3 output directly
OUTPUT_PDF = "DCO_Rehearsal_Schedule.pdf"
LOGO_FILE  = "JC_logo.png"                     # e.g. "Jc_logo.png" (set to None to hide)

TITLE      = "Derby Concert Orchestra"
SUBTITLE   = "Autumn Season Rehearsal Schedule"
CONDUCTOR  = "Jack Capstaff"

PAGE_SIZE  = A5
MARGINS    = dict(left=18*mm, right=18*mm, top=18*mm, bottom=16*mm)

# Column widths: left time column ~26mm, right column fills
TIME_COL_W = 26*mm

# =========================
# Styles
# =========================
styles = getSampleStyleSheet()

hdr_style = ParagraphStyle("RehHeader",
    parent=styles["Heading3"], fontName="Helvetica-Bold",
    fontSize=12, leading=14, spaceAfter=4, alignment=0
)
time_style = ParagraphStyle("Time",
    parent=styles["Normal"], fontName="Helvetica",
    fontSize=10, leading=12
)
work_style = ParagraphStyle("Work",
    parent=styles["Normal"], fontName="Helvetica",
    fontSize=10, leading=12
)
work_bold = ParagraphStyle("WorkBold",
    parent=work_style, fontName="Helvetica-Bold"
)
break_style = ParagraphStyle("Break",
    parent=styles["Normal"], fontName="Helvetica-Bold",
    fontSize=10, leading=12, alignment=1  # centered
)

title_style = ParagraphStyle("DocTitle",
    parent=styles["Title"], fontName="Helvetica-Bold",
    fontSize=18, leading=22, spaceAfter=6, alignment=1
)
subtitle_style = ParagraphStyle("DocSubtitle",
    parent=styles["Normal"], fontName="Helvetica",
    fontSize=12, leading=16, spaceAfter=14, alignment=1
)
conductor_style = ParagraphStyle("Conductor",
    parent=styles["Normal"], fontName="Helvetica",
    fontSize=10, leading=14, spaceAfter=12, alignment=1
)
footer_style = ParagraphStyle("Footer",
    parent=styles["Normal"], fontName="Helvetica",
    fontSize=8, leading=10, alignment=1, textColor=colors.gray
)

# =========================
# Footer callback
# =========================
def footer(canvas, doc):
    canvas.saveState()
    w, h = PAGE_SIZE
    y = 10*mm

    # optional logo (left)
    if LOGO_FILE and os.path.exists(LOGO_FILE):
        try:
            canvas.drawImage(LOGO_FILE, x=doc.leftMargin, y=y-1*mm, width=25*mm,
                             height=10*mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    # centered contact/footer
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.gray)
    line1 = "Jack Capstaff | Conductor | Composer"
    line2 = "M 07805 165 842 | E jack@jackcapstaff.com | W www.jackcapstaff.com"
    canvas.drawCentredString(w/2, y+3*mm, line1)
    canvas.drawCentredString(w/2, y, line2)

    canvas.restoreState()

# =========================
# Helpers
# =========================
def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1:"st",2:"nd",3:"rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

def format_pretty_date(d) -> str:
    if pd.isna(d):
        return ""
    if not isinstance(d, (pd.Timestamp, datetime)):
        d = pd.to_datetime(d, errors="coerce")
    if pd.isna(d):
        return ""
    return f"{d.strftime('%A')} {ordinal(d.day)} {d.strftime('%B')}"

def to_hhmm(x) -> str:
    if pd.isna(x): return ""
    s = str(x)
    # parse via pandas if possible
    t = pd.to_datetime(s, errors="coerce")
    if pd.notna(t): return t.strftime("%H:%M")
    if ":" in s:
        parts = s.split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return s

def read_schedule(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)

    # column resolver (case/space-insensitive)
    cmap = {c.lower().strip(): c for c in df.columns}
    def pick(*names):
        for n in names:
            k = n.lower().strip()
            if k in cmap: return cmap[k]
        return None

    col_reh   = pick("Rehearsal")
    col_date  = pick("Date")
    col_title = pick("Title")
    col_tir   = pick("Time in Rehearsal")  # left time column
    col_brst  = pick("Break Start (HH:MM)", "Break Start")
    col_brend = pick("Break End (HH:MM)", "Break End")

    # light validation
    need = [col_reh, col_date, col_title, col_tir]
    if any(c is None for c in need):
        raise ValueError("Missing required columns. Need at least: Rehearsal, Date, Title, Time in Rehearsal.")

    # normalize types
    df[col_date] = pd.to_datetime(df[col_date], errors="coerce")
    df = df.sort_values([col_reh, col_date, col_tir]).reset_index(drop=True)

    return df, dict(
        reh=col_reh, date=col_date, title=col_title,
        tir=col_tir, brs=col_brst, bre=col_brend
    )

def build_rehearsal_block(date_str: str, rows: list[dict]) -> Table:
    """
    Build a 2-column table:
      - Row 0: date header spanning both columns
      - Subsequent rows: [time, title] OR grey break row spanning both columns with (start–end)
    """
    data = []
    data.append([Paragraph(date_str, hdr_style), ""])  # header spans 2 cols

    # produce rows
    first_item = True
    for r in rows:
        title = str(r["title"])
        time  = to_hhmm(r["time"])
        is_break = title.strip().lower() == "break"

        if is_break:
            br_label = "Break"
            if r.get("br_start") and r.get("br_end"):
                br_label = f"Break  ({to_hhmm(r['br_start'])}–{to_hhmm(r['br_end'])})"
            data.append([Paragraph(br_label, break_style), ""])  # span 2 cols
        else:
            style = work_bold if first_item else work_style
            first_item = False
            data.append([Paragraph(time, time_style), Paragraph(title, style)])

    # set widths: time col fixed, right col fills
    table = Table(data, colWidths=[TIME_COL_W, None], hAlign="LEFT")

    # find break rows for styling/spans
    break_rows = []
    for i, row in enumerate(data):
        if isinstance(row[0], Paragraph) and row[0].style.name == "Break":
            break_rows.append(i)

    ts = TableStyle([
        # outer box
        ("BOX", (0,0), (-1,-1), 0.75, colors.black),
        # span header
        ("SPAN", (0,0), (-1,0)),
        ("TOPPADDING", (0,0), (-1,0), 4),
        ("BOTTOMPADDING", (0,0), (-1,0), 4),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        # vertical separator between time and title
        ("LINEBEFORE", (1,1), (1,-1), 0.25, colors.grey),
        # gentle horizontal rules between rows (like dotted look)
        *[("LINEABOVE", (0,r), (-1,r), 0.25, colors.lightgrey) for r in range(1, len(data))]
    ])

    # grey, bold, spanned break rows
    for br in break_rows:
        ts.add("SPAN", (0, br), (-1, br))
        ts.add("BACKGROUND", (0, br), (-1, br), colors.HexColor("#EEEEEE"))
        ts.add("LINEABOVE", (0, br), (-1, br), 0.25, colors.lightgrey)
        ts.add("LINEBELOW", (0, br), (-1, br), 0.25, colors.lightgrey)
        ts.add("LEFTPADDING", (0, br), (-1, br), 4)
        ts.add("RIGHTPADDING", (0, br), (-1, br), 4)

    table.setStyle(ts)
    return table

# =========================
# Title page
# =========================
def make_title_page(first_date, last_date):
    elems = []
    elems.append(Paragraph(TITLE, title_style))
    elems.append(Paragraph(SUBTITLE, subtitle_style))

    if pd.notna(first_date) and pd.notna(last_date):
        daterange = f"{first_date.strftime('%B')} – {last_date.strftime('%B %Y')}"
        elems.append(Paragraph(daterange, ParagraphStyle("Range",
            parent=styles["Normal"], fontName="Helvetica", fontSize=11,
            leading=14, alignment=1, spaceAfter=12)))
    elems.append(Paragraph(CONDUCTOR, conductor_style))
    elems.append(Spacer(1, 10*mm))
    return elems

# =========================
# Main
# =========================
def main():
    df, cols = read_schedule(INPUT_XLSX)

    doc = SimpleDocTemplate(
        OUTPUT_PDF, pagesize=PAGE_SIZE,
        leftMargin=MARGINS["left"], rightMargin=MARGINS["right"],
        topMargin=MARGINS["top"], bottomMargin=MARGINS["bottom"]
    )

    elements = []

    # Title page
    fdate = df[cols["date"]].min()
    ldate = df[cols["date"]].max()
    elements += make_title_page(fdate, ldate)
    elements.append(PageBreak())

    # Build each rehearsal block (keep together to avoid splitting mid-box)
    for reh, g in df.groupby(cols["reh"], sort=True):
        g = g.sort_values([cols["date"], cols["tir"]])
        date_str = format_pretty_date(g[cols["date"]].dropna().iloc[0])

        rows = []
        for _, r in g.iterrows():
            rows.append(dict(
                time=r[cols["tir"]],
                title=r[cols["title"]],
                br_start=(r[cols["brs"]] if cols["brs"] in g.columns else None),
                br_end=(r[cols["bre"]] if cols["bre"] in g.columns else None)
            ))

        block = build_rehearsal_block(date_str, rows)
        elements.append(KeepTogether([block]))
        elements.append(Spacer(1, 6*mm))

    doc.build(elements, onFirstPage=footer, onLaterPages=footer)
    print(f"Saved → {OUTPUT_PDF}")

if __name__ == "__main__":
    main()
