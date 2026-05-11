import pandas as pd
import sys
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



def select_file_dialog(title):
    # Open a file dialog for user to select an Excel file
    root = tk.Tk()
    root.withdraw()  # Hide the root window
    file_path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx;*.xls")], title=title)
    return file_path

# Get the Excel file path from the command line argument
file_path_works = select_file_dialog("Select the User Input Excel file for works")
sheet_name_rehearsals = "Rehearsals"

# Load the datasets

rehearsals = pd.read_excel(file_path_works, sheet_name=sheet_name_rehearsals)
schedule = pd.read_excel("Rehearsal_schedule.xlsx")

# Rename 'Rehearsl' column for clarity
rehearsals = rehearsals.rename(columns={'Rehearsl': 'Rehearsal'})

# Extract rehearsal numbers (if needed -  already numeric in this example)
schedule['Rehearsal'] = schedule['Rehearsal'].str.extract('(\d+)').astype(int)

# Merge the datasets
merged_data = pd.merge(rehearsals, schedule, on=['Rehearsal'], how='left')

merged_data['Start Time'] = pd.to_datetime(merged_data['Start Time'], format='%H:%M:%S', errors='coerce')
merged_data['Start Time'] = merged_data['Start Time'].fillna(merged_data['Start Time'].mode()[0])

#Convert Start Time to datetime objects and handle errors
merged_data['Start Time'] = pd.to_datetime(merged_data['Start Time'], format='%H:%M:%S', errors='coerce')
merged_data['Start Time'] = merged_data['Start Time'].fillna(merged_data['Start Time'].mode()[0])

#Convert Rehearsal Time and Break to minutes
merged_data['Rehearsal Time (minutes)'] = pd.to_timedelta(merged_data['Rehearsal Time'].astype(str) + ' minutes').dt.total_seconds() / 60
merged_data['Break (minutes)'] = merged_data['Break']

# Calculate cumulative time (excluding break)
merged_data['Cumulative Time (minutes)'] = merged_data.groupby('Rehearsal')['Rehearsal Time (minutes)'].cumsum()

# Calculate total rehearsal time
total_rehearsal_time = merged_data.groupby('Rehearsal')['Rehearsal Time (minutes)'].sum() + merged_data.groupby('Rehearsal')['Break (minutes)'].sum()

# Create a new DataFrame for breaks and insert it correctly

break_df = pd.DataFrame({
    'Rehearsal': total_rehearsal_time.index,
    'Date': merged_data.groupby('Rehearsal')['Date'].first().values,
    'Start Time': merged_data.groupby('Rehearsal')['Start Time'].first().values + pd.to_timedelta(total_rehearsal_time.values / 2 - merged_data.groupby('Rehearsal')['Break (minutes)'].first().values/2, unit='m'),
    'End Time': merged_data.groupby('Rehearsal')['Start Time'].first().values + pd.to_timedelta(total_rehearsal_time.values / 2 + merged_data.groupby('Rehearsal')['Break (minutes)'].first().values/2, unit='m'),
    'Break': total_rehearsal_time.index,
    'Title': 'Break',
    'Rehearsal Time': merged_data.groupby('Rehearsal')['Break (minutes)'].first().values
})

# Concatenate the break DataFrame with the original DataFrame
merged_data = pd.concat([merged_data, break_df], ignore_index=True)

#Sort the dataframe by rehearsal and cumulative time
merged_data = merged_data.sort_values(['Rehearsal', 'Cumulative Time (minutes)'])

# Calculate start time for each piece, including break - CORRECTED
merged_data['Time Delta'] = merged_data.groupby('Rehearsal')['Rehearsal Time (minutes)'].cumsum() - merged_data['Rehearsal Time (minutes)']
merged_data['Time Delta'] = pd.to_timedelta(merged_data['Time Delta'], unit='m')

#Correct Start Time Calculation
merged_data['Time in Rehearsal'] = merged_data['Start Time'] + merged_data['Time Delta']
merged_data['Time in Rehearsal'] = merged_data['Time in Rehearsal'].dt.strftime('%H:%M')

# Drop the intermediate columns
merged_data = merged_data.drop(columns=['Rehearsal Time (minutes)', 'Break (minutes)', 'Cumulative Time (minutes)', 'Time Delta'])

# Display and save the final data
print(merged_data)
merged_data.to_excel("timed_rehearsal.xlsx", index=False)