import pandas as pd

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
    # Load the rehearsal data from the Excel file
    rehearsals_df = load_rehearsal_data()

    if rehearsals_df is not None:  # Proceed only if the data was loaded successfully
        # Calculate the total number of rehearsals and total rehearsal duration
        num_rehearsals, rehearsal_duration = calculate_rehearsal_details(rehearsals_df)

        print(f"Number of rehearsals: {num_rehearsals}")
        print(f"Total rehearsal duration (in minutes): {rehearsal_duration}")
        print(rehearsals_df.head())  # Print the updated DataFrame to verify the new column

if __name__ == "__main__":
    main()
