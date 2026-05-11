import pandas as pd
import numpy as np
import sys

# Get command line arguments
num_rehearsals = int(sys.argv[1])
rehearsal_duration = int(sys.argv[2])

# Your existing logic using num_rehearsals and rehearsal_length


def get_rehearsal_details():
    """Get the number of rehearsals and their duration from user input."""
    num_rehearsals = int(input("Please enter the number of rehearsals (e.g., 5): "))
    rehearsal_duration = int(input("Please enter the duration of each rehearsal in minutes (e.g., 120): "))
    return num_rehearsals, rehearsal_duration

def load_works(file_path='works.xlsx'):
    """Load the works from an Excel file."""
    return pd.read_excel(file_path)

def calculate_required_minutes(works_df, total_rehearsal_minutes):
    """Calculate required rehearsal minutes for each work."""
    works_df['duration_difficulty'] = works_df['duration'] * works_df['difficulty']
    total_duration_difficulty = works_df['duration_difficulty'].sum()
    
    # Calculate scale factor and required minutes
    scale_factor = total_rehearsal_minutes / total_duration_difficulty
    works_df['required_minutes'] = works_df['duration_difficulty'] * scale_factor
    works_df['rounded_minutes'] = np.ceil(works_df['required_minutes'] / 5) * 5
    works_df['required_minutes'] = works_df['rounded_minutes']

    # Introducing the difference calculation for handling minutes exceeding available time
    works_df['difference'] = works_df['rounded_minutes'] - works_df['required_minutes']

    total_required_minutes = works_df['required_minutes'].sum()
    if total_required_minutes > total_rehearsal_minutes:
        # Identify the top 6 works with the largest difference
        largest_differences = works_df.nlargest(6, 'difference')
        for index in largest_differences.index:
            works_df.at[index, 'required_minutes'] = max(works_df.at[index, 'required_minutes'] - 5, 0)

        # Ensure no required minutes fall below zero and round again
        works_df['required_minutes'] = np.ceil(works_df['required_minutes'] / 5) * 5

    return works_df

def allocate_rehearsal_time(works_df, total_rehearsal_time):
    """Allocate rehearsal time to works based on difficulty."""
    df = works_df.copy()  # Work on a copy to preserve initial data

    total_duration = df['duration'].sum()
    scaling_factor = total_rehearsal_time / total_duration

    df['Scaled'] = df['duration'] * scaling_factor
    df['Rehearsal 1'] = (np.round(df['Scaled'] / 5) * 5).astype(float)
    df['Difference'] = df['Scaled'] - df['Rehearsal 1']
    df['Final Rehearsal'] = df['Rehearsal 1']
    
    spare_time = df['Difference'].sum()

    while spare_time > 0:
        eligible_indices = df[df['Difference'] < 0].index
        if eligible_indices.empty:
            break
        
        highest_difficulty_index = df.loc[eligible_indices]['difficulty'].idxmax()
        df.at[highest_difficulty_index, 'Rehearsal 1'] += 5
        df.at[highest_difficulty_index, 'Final Rehearsal'] += 5
        df.at[highest_difficulty_index, 'Difference'] += 5 - df.at[highest_difficulty_index, 'Scaled']
        spare_time -= 5

    df['Time Remaining'] = df['required_minutes'] - df['Rehearsal 1'] - df['Final Rehearsal']
    return df

def main():
    num_rehearsals, rehearsal_duration = get_rehearsal_details()
    total_rehearsal_minutes = num_rehearsals * rehearsal_duration
    
    works_df = load_works()
    works_df = calculate_required_minutes(works_df, total_rehearsal_minutes)

    total_rehearsal_time = rehearsal_duration
    final_allocation = allocate_rehearsal_time(works_df, total_rehearsal_time)

    # Exporting results to Excel
    output_file_path = "rehearsal_allocation_output.xlsx"
    final_allocation[['composer', 'title', 'duration', 'difficulty', 'required_minutes', 
                      'Rehearsal 1', 'Final Rehearsal', 'Time Remaining']].to_excel(output_file_path, index=False)

    print(f"The rehearsal schedule has been saved to {output_file_path}.")

if __name__ == "__main__":
    main()
