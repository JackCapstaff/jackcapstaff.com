import pandas as pd

# Define the music work class
class MusicWork:
    def __init__(self, composer, title, duration, difficulty, required_minutes, time_remaining):
        self.composer = composer
        self.title = title
        self.duration = duration
        self.difficulty = difficulty
        self.required_minutes = required_minutes
        self.time_remaining = time_remaining
        self.rehearsal_times = [0, 0, 0]  # Initialize rehearsal times for rehearsals

# Function to find the nearest multiple of 5
def closest_multiple_of_five(n):
    return round(n / 5) * 5

# Function to distribute time among rehearsals
def distribute_time(music_works, num_rehearsals, total_rehearsal_time):
    rehearsals = [[] for _ in range(num_rehearsals)]
    
    for rehearsal_index in range(num_rehearsals):
        remaining_rehearsal_time = closest_multiple_of_five(total_rehearsal_time // num_rehearsals)

        # Calculate the target completion percentage for the current rehearsal
        target_completion_ratio = (rehearsal_index + 1) / num_rehearsals

        # Sort works based on time remaining, in descending order
        music_works.sort(key=lambda work: work.time_remaining, reverse=True)

        # Prioritize works with small amounts of time remaining
        for work in music_works:
            if work.time_remaining > 0:
                expected_completion_time = work.required_minutes * target_completion_ratio
                time_needed_now = closest_multiple_of_five(expected_completion_time - sum(work.rehearsal_times))

                if work.time_remaining <= 10 and rehearsal_index in [num_rehearsals // 2]:
                    # Special handling for works with minimal remaining time
                    feasible_times = [5] # Assign 5 minutes to ensure allocation
                    work.time_remaining -= 5
                    work.rehearsal_times[rehearsal_index] += 5
                    remaining_rehearsal_time -= 5
                    rehearsals[rehearsal_index].append((work.title, 5))
                    continue

                if time_needed_now > 0:
                    min_time = closest_multiple_of_five(max(0.5 * work.duration, 5))
                    max_time = closest_multiple_of_five(min(2.5 * work.duration, work.time_remaining))
                    
                    feasible_times = [t for t in range(min_time, max_time + 1, 5)]

                    if feasible_times:
                        rehearsal_time = min(feasible_times[-1], remaining_rehearsal_time, time_needed_now)

                        if rehearsal_time > 0 and remaining_rehearsal_time >= rehearsal_time:
                            rehearsals[rehearsal_index].append((work.title, rehearsal_time))
                            work.time_remaining -= rehearsal_time
                            work.rehearsal_times[rehearsal_index] += rehearsal_time
                            remaining_rehearsal_time -= rehearsal_time

            # Break early if rehearsal time for the session is exhausted
            if remaining_rehearsal_time <= 0:
                break

    # Spread minimal time works across the rehearsals
    # Prioritize works with less than or equal to 5 minutes remaining
    for rehearsal_index in range(1, num_rehearsals, 2):
        remaining_rehearsal_time = closest_multiple_of_five(total_rehearsal_time // num_rehearsals)
        for work in music_works:
            if work.time_remaining > 0 and work.time_remaining <= 5:
                if remaining_rehearsal_time >= 5:
                    rehearsals[rehearsal_index].append((work.title, 5))
                    work.time_remaining -= 5
                    work.rehearsal_times[rehearsal_index] += 5
                    remaining_rehearsal_time -= 5

    return rehearsals









# Load music works from the Excel file
excel_file = "rehearsal_allocation_output.xlsx"
df = pd.read_excel(excel_file)

# Create a list of music works from the DataFrame
music_works = []
for index, row in df.iterrows():
    music_works.append(MusicWork(row['composer'], row['title'], row['duration'], row['difficulty'], row['required_minutes'], row['Time Remaining']))

# Prompt user for input
num_rehearsals = int(input("Enter the number of rehearsals: ")) - 2  # Subtract 2 from user input
rehearsal_length = int(input("Enter the length of each rehearsal (in minutes): "))

# Calculate total rehearsal time available
total_rehearsal_time = num_rehearsals * rehearsal_length

# Distribute rehearsal times
rehearsal_schedule = distribute_time(music_works, num_rehearsals, total_rehearsal_time)

# Update the DataFrame with rehearsal times
for work in music_works:
    for i in range(num_rehearsals):
        column_name = f'Rehearsal {i + 2}'
        if column_name not in df.columns:
            df[column_name] = 0
        df.loc[df['title'] == work.title, column_name] = work.rehearsal_times[i]

# Fill in the remaining NaN values with 0
df.fillna(0, inplace=True)

# Reorder the columns as specified
df = df[['composer', 'title', 'duration', 'difficulty', 'required_minutes', 'Time Remaining', 'Rehearsal 1'] + 
         [f'Rehearsal {i + 2}' for i in range(num_rehearsals)] + ['Final Rehearsal']]

# Save the updated DataFrame back to Excel
df.to_excel(excel_file, index=False)

print(f"Updated rehearsal times have been added to {excel_file}.")