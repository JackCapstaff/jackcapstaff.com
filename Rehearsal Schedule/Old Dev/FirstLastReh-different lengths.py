import pandas as pd
import numpy as np

def get_rehearsal_details():
    """Get the total rehearsal time and the durations for the first and last rehearsals from user input."""
    total_rehearsal_minutes = int(input("Please enter the total rehearsal time in minutes (e.g., 600): "))
    rehearsal_duration_first = int(input("Please enter the duration of the first rehearsal in minutes (e.g., 120): "))
    rehearsal_duration_last = int(input("Please enter the duration of the last rehearsal in minutes (e.g., 90): "))
    return total_rehearsal_minutes, rehearsal_duration_first, rehearsal_duration_last

def load_works(file_path='User_Input.xlsx'):
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

    # Introduce the difference calculation for handling minutes exceeding available time
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

def allocate_rehearsal_time(works_df, total_rehearsal_time_first, total_rehearsal_time_last, total_rehearsal_minutes):
    """Allocate rehearsal time to works based on difficulty, using different lengths for the first and last rehearsals."""
    df = works_df.copy()  # Work on a copy to preserve initial data

    num_rehearsals = len(df)

    # Calculate total duration of works and scaling factor
    total_duration = df['duration'].sum()
    scaling_factor = total_rehearsal_minutes / total_duration

    # Scale works based on difficulty and total durations
    df['Scaled'] = df['duration'] * scaling_factor
    df['Rehearsal 1'] = 0
    df['Final Rehearsal'] = 0

    # Allocate time for the first rehearsal
    if num_rehearsals > 0:
        df.at[0, 'Rehearsal 1'] = min(df.at[0, 'Scaled'], total_rehearsal_time_first)  # Give the first work the first rehearsal time

    # Allocate time for the last rehearsal
    if num_rehearsals > 1:
        df.at[num_rehearsals - 1, 'Final Rehearsal'] = min(df.at[num_rehearsals - 1, 'Scaled'], total_rehearsal_time_last)  # Give the last work the last rehearsal time

    # Remaining available time for middle rehearsal works
    middle_rehearsal_time = total_rehearsal_minutes - df['Rehearsal 1'].sum() - df['Final Rehearsal'].sum()
    
    # Calculate the remaining time allocation for middle works
    for i in range(1, num_rehearsals - 1):  # Start from the second work to the second to last work
        if i < len(df):
            df.at[i, 'Rehearsal 1'] = min(df.at[i, 'Scaled'], middle_rehearsal_time / (num_rehearsals - 2))  # Allocate evenly

    # Calculate how much time is remaining after allocations
    df['Time Remaining'] = df['Scaled'] - df['Rehearsal 1']
    
    return df



def main():
    total_rehearsal_minutes, rehearsal_duration_first, rehearsal_duration_last = get_rehearsal_details()
    
    works_df = load_works()
    works_df = calculate_required_minutes(works_df, total_rehearsal_minutes)

    final_allocation = allocate_rehearsal_time(works_df, rehearsal_duration_first, rehearsal_duration_last, total_rehearsal_minutes)

    # Exporting results to Excel
    output_file_path = "rehearsal_allocation_output.xlsx"
    final_allocation[['composer', 'title', 'duration', 'difficulty', 'required_minutes', 
                      'Rehearsal 1', 'Final Rehearsal', 'Time Remaining']].to_excel(output_file_path, index=False)

    print(f"The rehearsal schedule has been saved to {output_file_path}.")

if __name__ == "__main__":
    main()

