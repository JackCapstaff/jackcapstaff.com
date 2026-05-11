import pandas as pd

# Load the Excel file (change the file name and sheet name as necessary)
excel_file = 'timed_rehearsal.xlsx'
sheet_name = 'Sheet1'

# Read the Excel file into a DataFrame
df = pd.read_excel(excel_file, sheet_name=sheet_name)

# Filter rows where the 'Title' column contains "Break"
breaks_df = df[df['Title'].str.contains("Break", na=False)]

# Combine 'Date' with 'Start Time' and 'End Time' to create full datetime entries
breaks_df['Start Datetime'] = pd.to_datetime(breaks_df['Date']) + pd.to_timedelta(breaks_df['Start Time'].astype(str))
breaks_df['End Datetime'] = pd.to_datetime(breaks_df['Date']) + pd.to_timedelta(breaks_df['End Time'].astype(str))

# Calculate the duration of each break in minutes
breaks_df['Break Duration'] = (breaks_df['End Datetime'] - breaks_df['Start Datetime']).dt.total_seconds() / 60

# Group by 'Rehearsal' and sum the break durations
break_time_per_rehearsal = breaks_df.groupby('Rehearsal')['Break Duration'].sum().reset_index()

# Print or save the break time per rehearsal
print(break_time_per_rehearsal)

# Optionally, drop rows where 'Title' contains "Break" if you don't need them in the cleaned data
df_cleaned = df[~df['Title'].str.contains("Break", na=False)]

# Save the cleaned DataFrame back to an Excel file
df_cleaned.to_excel('timed_rehearsal_no_breaks.xlsx', index=False)

print("Rows with 'Break' removed and new file saved as 'cleaned_file.xlsx'")

# Save the break time data to a new Excel file or sheet if needed
break_time_per_rehearsal.to_excel('break_time_per_rehearsal.xlsx', index=False)
