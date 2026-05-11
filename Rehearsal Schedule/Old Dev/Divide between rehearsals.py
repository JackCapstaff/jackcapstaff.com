import pandas as pd
import numpy as np

# Step 1: Read data from "rehearsal_data.xlsx"
file_path = "rehearsal_data.xlsx"
df = pd.read_excel(file_path)

# Ensure the necessary columns are present
required_columns = ["composer", "title", "duration", "difficulty", "required_minutes"]
if not all(column in df.columns for column in required_columns):
    raise ValueError("Input Excel file must contain the following columns: " + ", ".join(required_columns))

# Step 2: Get total rehearsal time from user
try:
    total_rehearsal_time = float(input("Please enter the total rehearsal time in minutes (e.g., 120): "))
except ValueError:
    raise ValueError("Invalid input! Please enter a numeric value.")

# Step 3: Calculate total required minutes
total_duration = df['duration'].sum()

# Step 4: Calculate scaling factor
scaling_factor = total_rehearsal_time / total_duration

# Step 5: Scale and Round durations
df['Scaled'] = df['duration'] * scaling_factor
df['Rehearsal 1'] = (np.round(df['Scaled'] / 5) * 5).astype(float)  # Round to nearest 5
df['Difference'] = df['Scaled'] - df['Rehearsal 1']

# Step 6: Duplicate Rehearsal 1 to create Final Rehearsal
df['Final Rehearsal'] = df['Rehearsal 1']  # Duplicate values

# Step 7: Calculate spare time
spare_time = df['Difference'].sum()

# Step 8: Allocate spare time to works with highest difficulty
while spare_time > 0:
    # Get indices of the works that can still take more time
    eligible_indices = df[df['Difference'] < 0].index
    if eligible_indices.empty:
        break
      
    # Get the work with the highest difficulty
    highest_difficulty_index = df.loc[eligible_indices]['difficulty'].idxmax()
    
    # Increase the rehearsal time by 5 minutes for both Rehearsal 1 and Final Rehearsal
    df.at[highest_difficulty_index, 'Rehearsal 1'] += 5
    df.at[highest_difficulty_index, 'Final Rehearsal'] += 5  # Ensure the duplicate also reflects the change
    df.at[highest_difficulty_index, 'Difference'] += 5 - df.at[highest_difficulty_index, 'Scaled']
    
    # Decrease spare time
    spare_time -= 5

# Step 9: Calculate Time Remaining
df['Time Remaining'] = df['required_minutes'] - df['Rehearsal 1'] - df['Final Rehearsal']

# Export the final DataFrame to a new Excel file
output_file_path = "rehearsal_allocation_output.xlsx"
df[['composer', 'title', 'duration', 'difficulty', 'required_minutes', 
    'Rehearsal 1', 'Final Rehearsal', 'Time Remaining']].to_excel(output_file_path, index=False)

# Display the final DataFrame
print(df[['composer', 'title', 'duration', 'difficulty', 'required_minutes', 
           'Scaled', 'Rehearsal 1', 'Final Rehearsal', 'Time Remaining']])
