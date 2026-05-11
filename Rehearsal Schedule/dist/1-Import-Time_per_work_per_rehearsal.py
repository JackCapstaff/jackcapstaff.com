import pandas as pd
import numpy as np
import argparse


def load_works(file_path):
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

def allocate_rehearsal_time(works_df, total_rehearsal_time, rehearsals_df):
    """Allocate rehearsal time to works with independent scaling for first and last rehearsals."""
    df = works_df.copy()  # Work on a copy to preserve initial data

    total_duration = df['duration'].sum()
    # Introduce independent scaling factors for first and last rehearsals
    first_rehearsal_duration = rehearsals_df['Duration'].iloc[0]
    last_rehearsal_duration = rehearsals_df['Duration'].iloc[-1]
    
    # Determine scaling factors based on allotted rehearsal duration
    first_scaling_factor = first_rehearsal_duration / total_duration
    last_scaling_factor = last_rehearsal_duration / total_duration

    # Apply separate scaling for first and last rehearsals
    df['Scaled First'] = df['duration'] * first_scaling_factor
    df['Scaled Last'] = df['duration'] * last_scaling_factor
    
    # Initialize rehearsal times with scaled values
    df['Rehearsal 1'] = (np.round(df['Scaled First'] / 5) * 5).astype(float)
    df['Final Rehearsal'] = (np.round(df['Scaled Last'] / 5) * 5).astype(float)
    
    # Calculate differences after initial allocation
    df['Difference'] = df['duration'] - df['Rehearsal 1'] - df['Final Rehearsal']

    # Cap initial allocation for the first and last pieces
    df.at[0, 'Rehearsal 1'] = min(df.at[0, 'Rehearsal 1'], first_rehearsal_duration)
    if len(df) > 1:
        df.at[len(df) - 1, 'Final Rehearsal'] = min(df.at[len(df) - 1, 'Final Rehearsal'], last_rehearsal_duration)

    # Calculate spare time after initial allocation
    spare_time = total_rehearsal_time - df['Rehearsal 1'].sum() - df['Final Rehearsal'].sum()

    while spare_time > 0:
        # Adjust the first rehearsal when room exists
        first_rehearsal_index = 0
        if df.at[first_rehearsal_index, 'Rehearsal 1'] < first_rehearsal_duration:
            additional_time = min(5, first_rehearsal_duration - df.at[first_rehearsal_index, 'Rehearsal 1'])
            df.at[first_rehearsal_index, 'Rehearsal 1'] += additional_time
            df.at[first_rehearsal_index, 'Difference'] -= additional_time
            spare_time -= additional_time

        # Adjust the last rehearsal when room exists
        if len(df) > 1:
            last_rehearsal_index = len(df) - 1
            if df.at[last_rehearsal_index, 'Final Rehearsal'] < last_rehearsal_duration:
                additional_time = min(5, last_rehearsal_duration - df.at[last_rehearsal_index, 'Final Rehearsal'])
                df.at[last_rehearsal_index, 'Final Rehearsal'] += additional_time
                df.at[last_rehearsal_index, 'Difference'] -= additional_time
                spare_time -= additional_time

        # Distribute spare time to the highest difficult work if time remains
        if spare_time > 0:
            # Find the work with the highest difficulty that still needs time
            eligible_indices = df[df['Time Remaining'] > 0].index
            if not eligible_indices.empty:
                highest_difficulty_index = df.loc[eligible_indices, 'difficulty'].idxmax()
                additional_time_mid = min(5, df.at[highest_difficulty_index, 'Difference'], spare_time)
                
                df.at[highest_difficulty_index, 'Rehearsal 1'] += additional_time_mid / 2  # Adjust half to Rehearsal 1
                df.at[highest_difficulty_index, 'Final Rehearsal'] += additional_time_mid / 2  # Adjust half to Final Rehearsal
                df.at[highest_difficulty_index, 'Difference'] -= additional_time_mid  # Update the difference
                spare_time -= additional_time_mid  # Decrement the spare time

    # Calculate remaining time needed for each work
    df['Time Remaining'] = df['required_minutes'] - df['Rehearsal 1'] - df['Final Rehearsal']
    
    return df




# ALL REH TIME

# Define the music work class
class MusicWork:
    def __init__(self, composer, title, duration, difficulty, required_minutes, time_remaining, num_rehearsals):
        self.composer = composer
        self.title = title
        self.duration = duration
        self.difficulty = difficulty
        self.required_minutes = required_minutes
        self.time_remaining = time_remaining
        self.rehearsal_times = [0] * num_rehearsals  # Dynamically initialize based on num_rehearsals


# Function to find the nearest multiple of 5
def closest_multiple_of_five(n):
    return round(n / 5) * 5

# Function to distribute time among rehearsals
def distribute_time(music_works, num_rehearsals, total_rehearsal_time, rehearsals_df):
    rehearsals = [[] for _ in range(num_rehearsals)]
    rehearsal_durations = rehearsals_df['Duration'].values

    print("Loaded Rehearsal Durations:", rehearsal_durations)
    rehearsal_minutes = [0] * len(music_works)

    for rehearsal_index in range(1, num_rehearsals - 1):  # Start from 1 to exclude Rehearsal 1
        remaining_rehearsal_time = rehearsal_durations[rehearsal_index]
        print(f"Processing Rehearsal {rehearsal_index + 1}: Duration = {remaining_rehearsal_time}")

        # Sort works based on the percentage of total rehearsal time left
        music_works.sort(key=lambda work: (work.time_remaining / work.required_minutes if work.required_minutes > 0 else 0), reverse=True)

        for work in music_works:
            expected_completion_time = work.required_minutes * ((rehearsal_index + 1) / num_rehearsals)
            time_needed_now = closest_multiple_of_five(expected_completion_time - sum(work.rehearsal_times))

            min_time = closest_multiple_of_five(max(0.5 * work.duration, 5))
            max_time = closest_multiple_of_five(min(2 * work.duration, work.time_remaining, remaining_rehearsal_time))

            feasible_times = [t for t in range(min_time, max_time + 1, 5) if t <= remaining_rehearsal_time]

            if feasible_times:
                rehearsal_time = min(feasible_times[-1], remaining_rehearsal_time, time_needed_now)

                if rehearsal_time > 0:
                    rehearsals[rehearsal_index].append((work.title, rehearsal_time))
                    work.time_remaining -= rehearsal_time
                    work.rehearsal_times[rehearsal_index] += rehearsal_time
                    remaining_rehearsal_time -= rehearsal_time
                    rehearsal_minutes[music_works.index(work)] += rehearsal_time
            
            if remaining_rehearsal_time <= 0:
                break

        if remaining_rehearsal_time > 0:
            for work in music_works:
                if work.time_remaining > 0 and remaining_rehearsal_time > 0:
                    additional_time = min(work.time_remaining, remaining_rehearsal_time)
                    additional_time = closest_multiple_of_five(additional_time)
                    work.time_remaining -= additional_time
                    work.rehearsal_times[rehearsal_index] += additional_time
                    remaining_rehearsal_time -= additional_time
                    rehearsal_minutes[music_works.index(work)] += additional_time
                    rehearsals[rehearsal_index].append((work.title, additional_time))

        print(f"Remaining Rehearsal Time after Distribution for Rehearsal {rehearsal_index + 1}: {remaining_rehearsal_time}")
    
    return rehearsals






##Rehearsal Time Calculation##

def load_rehearsal_data(file_path='User_Input.xlsx'):
    """Load rehearsal data from the 'Rehearsals' sheet."""
    try:
        rehearsals_df = pd.read_excel(file_path, sheet_name='Rehearsals')
        print("Rehearsal data loaded successfully.")
        print(rehearsals_df.head())  # Print the first few rows of the DataFrame for verification
        return rehearsals_df
    except Exception as e:
        print(f"Error loading rehearsal data: {e}")
        return None

def calculate_rehearsal_details(rehearsals_df):
    """Calculate the number of rehearsals, total rehearsal time, and append duration to DataFrame."""
    # Count the number of unique rehearsal dates
    num_rehearsals = rehearsals_df['Date'].nunique()

    # Initialize a list to hold individual rehearsal durations
    rehearsal_durations = []

    # Calculate total rehearsal time in minutes
    total_rehearsal_time = 0
    for index, row in rehearsals_df.iterrows():
        # Combine the date with the time to create datetime objects
        date = pd.to_datetime(row['Date'])
        start_time = pd.to_datetime(row['Start Time'].strftime("%H:%M"), format="%H:%M").replace(year=date.year, month=date.month, day=date.day)
        end_time = pd.to_datetime(row['End Time'].strftime("%H:%M"), format="%H:%M").replace(year=date.year, month=date.month, day=date.day)
        break_time = row['Break']  # Break is in minutes
        
        # Calculate the rehearsal duration in minutes
        duration = (end_time - start_time).total_seconds() / 60 - break_time
        rehearsal_durations.append(duration)  # Append the duration to the list
        total_rehearsal_time += duration

    # Add the durations as a new column in the DataFrame
    rehearsals_df['Duration'] = rehearsal_durations

    return num_rehearsals, total_rehearsal_time


def main():
    # Prompt the user for the file path to the Excel document
    parser = argparse.ArgumentParser(description='Process rehearsal and works data from an Excel file.')
    parser.add_argument('file_path', type=str, help='The file path to the Excel document')
    args = parser.parse_args()

    excel_file_path = args.file_path
    # Load rehearsal data and calculate details
    rehearsals_df = load_rehearsal_data(excel_file_path)
    if rehearsals_df is not None:
        # Calculate number of rehearsals and total duration
        num_rehearsals, total_rehearsal_time = calculate_rehearsal_details(rehearsals_df)

        print(f"Number of rehearsals: {num_rehearsals}")
        print(f"Total rehearsal duration (in minutes): {total_rehearsal_time}")
        print(rehearsals_df.head())

        # Load works data
        works_df = load_works(excel_file_path)
        works_df = calculate_required_minutes(works_df, total_rehearsal_time)

        # Allocate total time for the rehearsals
        rehearsal_time_per_rehearsal = total_rehearsal_time / num_rehearsals
        FLRT = allocate_rehearsal_time(works_df, rehearsal_time_per_rehearsal, rehearsals_df)

        # Initialize MusicWork instances for further processing
        music_works = []
        for index, row in FLRT.iterrows():
            music_works.append(MusicWork(
                row['composer'],
                row['title'],
                row['duration'],
                row['difficulty'],
                row['required_minutes'],
                row['Time Remaining'],
                num_rehearsals
            ))

        # Calculate the rehearsal schedule for all rehearsals
        rehearsal_schedule = distribute_time(music_works, num_rehearsals, total_rehearsal_time, rehearsals_df)

        # Update FLRT DataFrame with the new assigned rehearsal times (only for 2-6)
        for work in music_works:
            for i in range(1, num_rehearsals - 1):  # This now specifically reflects rehearsals 2 to 6
                allocated_time = work.rehearsal_times[i] if i < len(work.rehearsal_times) else 0
                FLRT.loc[FLRT['title'] == work.title, f'Rehearsal {i + 1}'] = allocated_time

        # Fill NaN values with 0
        FLRT.fillna(0, inplace=True)

        # Reorder and save the updated DataFrame
        relevant_columns = ['composer', 'title', 'duration', 'difficulty', 'required_minutes', 
                            'Time Remaining', 'Rehearsal 1'] + \
                           [f'Rehearsal {i + 2}' for i in range(num_rehearsals-1)]
        
        final_rehearsal_new_name = f'Rehearsal {num_rehearsals}'  # "Rehearsal N" for N rehearsals
        FLRT.rename(columns={'Final Rehearsal': final_rehearsal_new_name}, inplace=True)

        FLRT = FLRT[relevant_columns]

        # Save the updated rehearsal times to an output file
        output_file_path = "All Rehearsal Times.xlsx"
        FLRT.to_excel(output_file_path, index=False)
        print(f"Updated rehearsal times have been saved to {output_file_path}.")
    else:
        print("Failed to load rehearsals data. Ensure the Excel file is available and properly formatted.")


if __name__ == "__main__":
    main()



