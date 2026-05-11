import pandas as pd
from sklearn.metrics import pairwise_distances
import heapq
import sys


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



def select_file_dialog(title):
    # Open a file dialog for user to select an Excel file
    root = tk.Tk()
    root.withdraw()  # Hide the root window
    file_path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx;*.xls")], title=title)
    return file_path

# Get the Excel file path from the command line argument
file_path_works = select_file_dialog("Select the User Input Excel file for works")
sheet_name_works = "Works"
file_path_works = select_file_dialog("Select the User Input Excel file for works")
sheet_name_rehearsal_time = "Rehearsals"


file_path_rehearsal = "All Rehearsal Times.xlsx"
sheet_name_rehearsal = "Sheet1"


file_path_rehearsal = "All Rehearsal Times.xlsx"
sheet_name_rehearsal = "Sheet1"



# Load data for works (same as before)
data = pd.read_excel(file_path_works, sheet_name=sheet_name_works)
instrument_columns = data.columns[data.columns.get_loc("Flute"):data.columns.get_loc("Bass") + 1]
data["Total Players"] = data[instrument_columns].sum(axis=1)
orchestration_data = data.iloc[:, 4:-1]
distance_matrix = pairwise_distances(orchestration_data)
similarity_matrix = 1 / (1 + distance_matrix)
similarity_df = pd.DataFrame(similarity_matrix, index=data['title'], columns=data['title'])

# Load rehearsal times (assuming multiple rehearsal columns)
rehearsal_data = pd.read_excel(file_path_rehearsal, sheet_name=sheet_name_rehearsal)

# Function to create a schedule for a single rehearsal
# Function to create a schedule for a single rehearsal with similarity scores
# Function to create a schedule for a single rehearsal with detailed orchestration differences
# Function to create a schedule for a single rehearsal without similarity scores
def create_rehearsal_schedule_single(similarity_df, data, rehearsal_column):
    schedule = []
    used_titles = set()
    priority_queue = []

    # Filter pieces with non-zero rehearsal time for the specific rehearsal
    available_pieces = data[data[rehearsal_column] > 0].sort_values(by=['Total Players', rehearsal_column], ascending=[False, False]).copy()
    available_pieces['similarity_score'] = 0

    orchestration_columns = data.columns[data.columns.get_loc("Flute"):data.columns.get_loc("Bass") + 1]
    
    last_piece_title = None
    last_piece_orchestration = None

    if not available_pieces.empty:  # Check if there are any pieces to schedule
        starting_piece = available_pieces.iloc[0]
        heapq.heappush(priority_queue, (-starting_piece['Total Players'], -starting_piece[rehearsal_column], starting_piece['title']))

        while priority_queue:
            _, _, current_piece_title = heapq.heappop(priority_queue)

            if current_piece_title not in used_titles:
                rehearsal_time = data[data['title'] == current_piece_title][rehearsal_column].iloc[0]
                orchestration_differences = ""

                current_piece_orchestration = data.loc[data['title'] == current_piece_title, orchestration_columns].values.flatten()

                differences = []
                if last_piece_orchestration is not None:
                    for instrument, current_num, last_num in zip(orchestration_columns, current_piece_orchestration, last_piece_orchestration):
                        if current_num != last_num:
                            if current_num > last_num:
                                differences.append(f"{instrument}: +{current_num - last_num}")
                            else:
                                differences.append(f"{instrument}: -{last_num - current_num}")

                orchestration_differences = ", ".join(differences) if differences else "None"

                # Append the details to the schedule, excluding similarity score
                schedule.append((current_piece_title, rehearsal_time, orchestration_differences))
                used_titles.add(current_piece_title)
                last_piece_title = current_piece_title
                last_piece_orchestration = current_piece_orchestration
                available_pieces = available_pieces[~available_pieces['title'].isin(used_titles)]

                if not available_pieces.empty:
                    available_pieces['similarity_score'] = similarity_df.loc[current_piece_title, available_pieces['title']].values
                    for index, row in available_pieces.iterrows():
                        heapq.heappush(priority_queue, (-row['Total Players'], -row['similarity_score'], row['title']))
    return schedule

# Create a list of DataFrames, one for each rehearsal
# Define rehearsal columns based on available columns in rehearsal data
rehearsal_columns = [col for col in rehearsal_data.columns if 'Rehearsal' in col]

# Merge data with rehearsal times
data = pd.merge(data, rehearsal_data, on='title', how='left')

# Initialize the rehearsal_schedules dictionary
rehearsal_schedules = {}

# Create schedules for each rehearsal and store them in the dictionary
for col in rehearsal_columns:
    rehearsal_schedules[col] = create_rehearsal_schedule_single(similarity_df, data, col)

# Now, access and use rehearsal_schedules
dfs = []
for col, schedule in rehearsal_schedules.items():
    # Include orchestration differences in the DataFrame
    df_schedule = pd.DataFrame(schedule, columns=['Title', 'Rehearsal Time', 'Orchestration Differences'])
    df_schedule['Rehearsal'] = col
    dfs.append(df_schedule)

# Concatenate the DataFrames into a single DataFrame
final_df = pd.concat(dfs, ignore_index=True)

# Export to Excel
output_file = "rehearsal_schedule.xlsx"
final_df.to_excel(output_file, index=False)
print(f"\nRehearsal schedule with orchestration differences exported to {output_file}")
