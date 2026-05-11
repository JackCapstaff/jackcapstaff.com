import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5, portrait
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
import sys
import tkinter as tk
from tkinter import filedialog

def save_pdf_dialog():
    # Open a save dialog to specify the PDF file location
    root = tk.Tk()
    root.withdraw()  # Hide the root window
    pdf_file = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")],
                                              title="Save PDF As")
    return pdf_file

if len(sys.argv) != 2:
    print("Usage: python <script_name.py> <excel_file_path>")
    sys.exit(1)

# Read command-line arguments
user_input = sys.argv[1]  # The Excel file path

# Prompt for the PDF output filename using a file dialog
pdf_file = save_pdf_dialog()
if not pdf_file:
    print("No file chosen. Exiting...")
    sys.exit(1)

# Check if font files exist
print("Checking font files:")
print(os.path.exists('AdobeDevanagari-Regular.ttf'))
print(os.path.exists('AdobeDevanagari-Bold.ttf'))

# Register Adobe Devanagari fonts
pdfmetrics.registerFont(TTFont('AdobeDevanagari', 'AdobeDevanagari-Regular.ttf'))
pdfmetrics.registerFont(TTFont('AdobeDevanagari-Bold', 'AdobeDevanagari-Bold.ttf'))

# Read the Excel files
summary_rehearsals = pd.read_excel('summary_rehearsals.xlsx')
user_input = pd.read_excel(user_input, sheet_name="Rehearsals")

# Convert date formats
summary_rehearsals['Date'] = pd.to_datetime(summary_rehearsals['Date']).dt.strftime('%d/%m/%Y')
user_input['Date'] = pd.to_datetime(user_input['Date'], format='%d/%m/%Y').dt.strftime('%d/%m/%Y')

# Create a PDF document
doc = SimpleDocTemplate(pdf_file, pagesize=portrait(A5))

elements = []

# Create custom styles using Adobe Devanagari font
styles = getSampleStyleSheet()
title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontName='AdobeDevanagari', fontSize=14)
normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontName='AdobeDevanagari', fontSize=6)
bold_style = ParagraphStyle('Bold', parent=normal_style, fontName='AdobeDevanagari-Bold')

# Define light blue colors
light_blue = colors.Color(0.85, 0.9, 1)
darker_blue = colors.Color(0.7, 0.8, 1)

# Add title
elements.append(Paragraph("Rehearsal Schedule", title_style))
elements.append(Spacer(1, 12))

# Function to add footer
def add_footer(canvas, doc):
    canvas.saveState()
    # Draw the PNG image
    canvas.drawImage('Jc_logo.png', 40, 10, width=100, height=50, preserveAspectRatio=True)  # Adjust width/height as necessary
    # Set the position for the text and use the Adobe Devanagari font
    canvas.setFont("AdobeDevanagari", 6)
    canvas.drawString(150, 25, "Jack Capstaff")
    canvas.drawString(150, 10, "jack@jackcapstaff.com")
    canvas.drawString(150, -5, "07805165842")
    canvas.restoreState()

# Iterate through each rehearsal
for _, rehearsal in user_input.iterrows():
    # Prepare general details
    general_details = [
        [Paragraph(f"<b>Rehearsal {rehearsal['Rehearsl']}</b>", bold_style)],
        [Paragraph(f"Date: {rehearsal['Date']}", normal_style)],
        [Paragraph(f"Start Time: {rehearsal['Start Time']}", normal_style)],
        [Paragraph(f"End Time: {rehearsal['End Time']}", normal_style)],
        [Paragraph(f"Break: {rehearsal['Break']} min", normal_style)]
    ]

    # Prepare detailed schedule with appropriate font styles
    detailed_data = summary_rehearsals[summary_rehearsals['Rehearsal'] == rehearsal['Rehearsl']][['Start Time', 'Title']].values.tolist()
    detailed_data_paragraphs = [
        [Paragraph(str(time), normal_style), Paragraph(str(title), normal_style)] for time, title in detailed_data
    ]

    # Set headers using bold style
    header_row = [Paragraph("<b>Time</b>", bold_style), Paragraph("<b>Work</b>", bold_style)]
    detailed_table_data = [header_row] + detailed_data_paragraphs

    # Create a table with two columns
    general_table = Table(general_details)
    # Create a table with two columns and set row heights
    detailed_table = Table(detailed_table_data, colWidths=[doc.width / 8, 5 * doc.width / 8], rowHeights=10)  # Adjust the value for spacing

    data = [[general_table, detailed_table]]
    combined_table = Table(data, colWidths=[doc.width / 4, 3 * doc.width / 4])
    combined_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (0, 0), light_blue),
        ('BACKGROUND', (1, 0), (1, 0), darker_blue),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('LINEBELOW', (0, 0), (-1, -1), 1, colors.black),
        ('LINEAFTER', (0, 0), (0, -1), 1, colors.black),
    ]))

    elements.append(combined_table)
    elements.append(Spacer(1, 24))

# Build the PDF document with the footer on each page
doc.build(elements, onFirstPage=add_footer, onLaterPages=add_footer)

print(f"PDF created successfully: {pdf_file}")
