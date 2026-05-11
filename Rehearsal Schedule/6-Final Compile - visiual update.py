import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
import os
from datetime import datetime

# =========== USER CONFIGURABLE ===========
EXCEL_FILE = "summary_rehearsals.xlsx"
PDF_FILE = "DCO_Summer_Schedule.pdf"
LOGO_FILE = "Jc_logo.png"  # Set to None if you don't want a logo
TITLE = "Derby Concert Orchestra"
SUBTITLE = "Autumn Season Rehearsal Schedule"
CONDUCTOR = "Jack Capstaff"
# ==========================================

styles = getSampleStyleSheet()

# Define custom paragraph styles
title_style = ParagraphStyle('TitleStyle',
                             parent=styles['Title'],
                             fontName='Helvetica', # Replaced Adobe Devanagari
                             fontSize=24,
                             textColor=colors.darkblue,
                             alignment=1,  # Centered
                             spaceAfter=24)
subtitle_style = ParagraphStyle('SubtitleStyle',
                                parent=styles['Normal'],
                                fontName='Helvetica', # Replaced Adobe Devanagari
                                fontSize=16,
                                alignment=1,
                                spaceAfter=36)  # Increased space after subtitle
bold = ParagraphStyle('bold',
                      parent=styles['Normal'],
                      fontName='Helvetica-Bold',
                      fontSize=11,
                      spaceAfter=2)
normal = ParagraphStyle('normal',
                        parent=styles['Normal'],
                        fontName='Helvetica',
                        fontSize=10)
header = ParagraphStyle('header',
                        parent=styles['Heading2'],
                        fontName='Helvetica-Bold',
                        fontSize=13,
                        textColor=colors.darkblue,
                        spaceAfter=10)
footer_style = ParagraphStyle('footer',
                             parent=styles['Normal'],
                             fontSize=8,
                             alignment=1,
                             textColor=colors.gray)


def footer(canvas, doc):
    canvas.saveState()
    w, h = A5  # Use A5 for footer calculations
    # Logo (if exists)
    if LOGO_FILE and os.path.exists(LOGO_FILE):
        canvas.drawImage(LOGO_FILE, x=0.5*inch, y=0.2*inch, width=1.3*inch, height=0.5*inch, preserveAspectRatio=True, mask='auto')

    # Contact details (edit as needed)
    footer_lines = [
        "Jack Capstaff   |   Conductor  |  Composer",
        "M 07805 165 842   |   E jack@jackcapstaff.com   |   W www.jackcapstaff.com"
    ]
    for i, line in enumerate(footer_lines):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.gray)
        canvas.drawCentredString(w/2, 0.35*inch - i*0.13*inch, line)
    canvas.restoreState()

def make_schedule_table(rehearsal_df):
    data = [[Paragraph("<b>Time</b>", bold), Paragraph("<b>Work</b>", bold)]]

    for _, row in rehearsal_df.iterrows():
        time = Paragraph(str(row['Start Time']), normal)
        title = str(row['Title'])

        if "break" in title.lower():
            work = Paragraph(title, normal)
        else:
            work = Paragraph(f"<b>{title}</b>", normal)
        data.append([time, work])

    t = Table(data, colWidths=[1.2*inch, 3*inch])  # Wider columns
    style = TableStyle([
        ('BOX', (0,0), (-1,-1), 1, colors.black),
        ('INNERGRID', (0,1), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,0), (-1,0), colors.lightskyblue),  # Distinct header color
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONT', (0,0), (-1,0), 'Helvetica-Bold'),
        ('LINEAFTER', (0,0), (0,-1), 0.5, colors.grey, None, [1,2]),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),  # Ensure Helvetica is used
    ])

    for i, row in enumerate(rehearsal_df.itertuples(), start=1):
        if "break" in str(row.Title).lower():
            style.add('BACKGROUND', (0, i), (-1, i), colors.lightgrey)  # Light grey for breaks
            style.add('FONT', (0, i), (-1, i), 'Helvetica-Oblique')  # Italic for breaks

    t.setStyle(style)
    return t

def estimate_table_height(table, row_height=0.25*inch): # Adjust row_height as needed
    """Estimates the height of a table."""
    return len(table._cellvalues) * row_height

def create_title_page(doc, title, subtitle, first_date, last_date):
    elements = []
    elements.append(Paragraph(title, title_style))
    elements.append(Paragraph(subtitle, subtitle_style))

    # Determine date range
    date_range_str = f"{first_date.strftime('%B')} - {last_date.strftime('%B %Y')}"  # e.g., "November - December 2024"

    date_range_style = ParagraphStyle('DateRange',
                                     parent=styles['Normal'],
                                     fontName='Helvetica',
                                     fontSize=14,
                                     alignment=1,
                                     spaceAfter=72)  # Increased space after date range

    elements.append(Paragraph(date_range_str, date_range_style))

    elements.append(Paragraph(CONDUCTOR, ParagraphStyle('ConductorStyle',
                                                       parent=styles['Normal'],
                                                       fontName='Helvetica',
                                                       fontSize=12,
                                                       alignment=1)))
    doc.build(elements, onFirstPage=footer, onLaterPages=footer)  # Build only the title page
    doc.handle_pageEnd()  # Force end of page
    return


def main():
    df = pd.read_excel(EXCEL_FILE)

    # Convert 'Date' to datetime objects and handle NaT
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

    # Drop rows where 'Date' is NaT after conversion
    df.dropna(subset=['Date'], inplace=True)

    # Determine date range BEFORE formatting 'Date' for display
    first_rehearsal_date = df['Date'].min()
    last_rehearsal_date = df['Date'].max()

    # Format 'Date' column for display
    df['Date'] = df['Date'].dt.strftime('%A %B %d, %Y')

    # Group by rehearsal number
    rehearsals = df.groupby('Rehearsal')

    # Start doc
    doc = SimpleDocTemplate(PDF_FILE, pagesize=A5, rightMargin=0.5*inch, leftMargin=0.5*inch, topMargin=0.75*inch, bottomMargin=0.5*inch)
    elements = []

    # Create title page
    create_title_page(doc, TITLE, SUBTITLE, first_rehearsal_date, last_rehearsal_date)

    # Initial available space (A5 height - top margin - bottom margin)
    available_space = A5[1] - 0.75*inch - 0.5*inch
    print(f"initial available space: {available_space}")
    # Go through each rehearsal
    for idx, (rehearsal_num, rehearsal_df) in enumerate(rehearsals):
        # Heading
        date = rehearsal_df['Date'].iloc[0] if 'Date' in rehearsal_df.columns else ''
        heading = Paragraph(f"Rehearsal {rehearsal_num} – {date}", header)
        heading_height = styles['Heading2'].fontSize * 1.2 #approx

        table = make_schedule_table(rehearsal_df)
        table_height = estimate_table_height(table)

        print(f"Table {rehearsal_num}: Heading Height={heading_height:.2f}, Table Height={table_height:.2f}, Available Space={available_space:.2f}")

        # Check if table will fit on the current page
        if available_space < heading_height + table_height:
            print(f"   Table {rehearsal_num} doesn't fit. Adding PageBreak.")
            elements.append(PageBreak())
            available_space = A5[1] - 0.75*inch - 0.5*inch  # Reset available space
        else:
            print(f"   Table {rehearsal_num} fits.")

        elements.append(heading)
        elements.append(Spacer(1, 6))
        elements.append(table)
        elements.append(Spacer(1, 36))

        available_space -= (heading_height + 6 + table_height + 36)
        print(f"   Available space after adding Table {rehearsal_num}: {available_space:.2f}")

    # Build the rest of the document
    doc.build(elements, onFirstPage=footer, onLaterPages=footer)

    print(f"PDF successfully created as: {PDF_FILE}")


if __name__ == "__main__":
    main()
