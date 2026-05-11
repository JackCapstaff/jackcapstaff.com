import pandas as pd
import numpy as np

# Specify the number of rehearsals
num_rehearsals = 5  # Example: 5 rehearsals
total_rehearsal_minutes = num_rehearsals * 120  # Total minutes available for rehearsals

# Load the works from the Excel file
works_df = pd.read_excel('works.xlsx')

# Calculate duration * difficulty for each work
works_df['duration_difficulty'] = works_df['duration'] * works_df['difficulty']

# Calculate the sum of (duration * difficulty)
total_duration_difficulty = works_df['duration_difficulty'].sum()

# Calculate scale factor
scale_factor = total_rehearsal_minutes / total_duration_difficulty

# Calculate required rehearsal time for each work
works_df['required_minutes'] = works_df['duration_difficulty'] * scale_factor

# Calculate the difference between original required minutes and the rounded value
works_df['rounded_minutes'] = np.ceil(works_df['required_minutes'] / 5) * 5
works_df['difference'] = works_df['rounded_minutes'] - works_df['required_minutes']

# Round to the nearest multiple of 5
works_df['required_minutes'] = works_df['rounded_minutes']

# Check if total required minutes exceed total available minutes
total_required_minutes = works_df['required_minutes'].sum()

if total_required_minutes > total_rehearsal_minutes:
    # Identify the top 6 works with the largest difference
    largest_differences = works_df.nlargest(6, 'difference')

    # Reduce the required minutes of these works by 5 each
    for index in largest_differences.index:
        works_df.at[index, 'required_minutes'] -= 5

    # Ensure that no required minutes fall below zero
    works_df['required_minutes'] = works_df['required_minutes'].clip(lower=0)

# Round to nearest multiple of 5 again to maintain consistency
works_df['required_minutes'] = np.ceil(works_df['required_minutes'] / 5) * 5

# Select only the necessary columns for output
output_df = works_df[['composer', 'title', 'duration', 'difficulty', 'required_minutes']]

# Output to a new Excel file
output_file = 'rehearsal_schedule.xlsx'
output_df.to_excel(output_file, index=False)

print(f"The rehearsal schedule has been saved to {output_file}.")
