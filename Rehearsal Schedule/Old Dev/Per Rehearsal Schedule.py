import pandas as pd

# Load rehearsal data from Excel file "All Rehearsal Times.xlsx"
rehearsal_df = pd.read_excel("All Rehearsal Times.xlsx")

# Load rehearsal schedule from "User_Input.xlsx" sheet "Rehearsals"
schedule_df = pd.read_excel("User_Input.xlsx", sheet_name="Rehearsals")

# Function to convert time string or datetime time to minutes since midnight
def time_to_minutes(time_value):
    if isinstance(time_value, str):
        try:
            hours, minutes = map(int, time_value.split(':')[:2])
            return hours * 60 + minutes
        except ValueError:
            print(f"Error converting time string '{time_value}'. Skipping.")
            return 0
    elif isinstance(time_value, pd.Timestamp):
        return time_value.hour * 60 + time_value.minute
    elif hasattr(time_value, 'hour') and hasattr(time_value, 'minute'):
        return time_value.hour * 60 + time_value.minute
    else:
        print(f"Unexpected time format: {time_value}. Skipping.")
        return 0

# Function to convert minutes to HH:MM time format
def time_to_minutes_to_time(minutes):
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours:02d}:{minutes:02d}"

# Main allocation loop
rehearsal_plan = {}
for index, row in schedule_df.iterrows():
    rehearsal_number = index + 1
    start_time_minutes = time_to_minutes(row['Start Time'])
    end_time_minutes = time_to_minutes(row['End Time'])
    total_rehearsal_time = end_time_minutes - start_time_minutes
    break_duration = row['Break']  # Get break duration for THIS rehearsal

    if start_time_minutes == 0 or end_time_minutes == 0:
        continue

    rehearsal_plan[f"Rehearsal {rehearsal_number}"] = []

    # Allocate music based on the rehearsal column
    for i in range(len(rehearsal_df)):
        piece_time = rehearsal_df.iloc[i][f'Rehearsal {rehearsal_number}']
        if piece_time > 0:
            rehearsal_plan[f"Rehearsal {rehearsal_number}"].append((rehearsal_df.iloc[i]['title'], piece_time))

# Output the rehearsal plan with breaks
rehearsal_plan = {}
for index, row in schedule_df.iterrows():
    rehearsal_number = index + 1
    start_time_minutes = time_to_minutes(row['Start Time'])
    end_time_minutes = time_to_minutes(row['End Time'])
    total_rehearsal_time = end_time_minutes - start_time_minutes
    break_duration = row['Break']

    if start_time_minutes == 0 or end_time_minutes == 0:
        continue

    rehearsal_plan[f"Rehearsal {rehearsal_number}"] = []

    # Allocate music based on the rehearsal column
    for i in range(len(rehearsal_df)):
        piece_time = rehearsal_df.iloc[i][f'Rehearsal {rehearsal_number}']
        if piece_time > 0:
            rehearsal_plan[f"Rehearsal {rehearsal_number}"].append((rehearsal_df.iloc[i]['title'], piece_time))


# Output the rehearsal plan with breaks
for rehearsal, pieces in rehearsal_plan.items():
    print(f"{rehearsal}:")
    rehearsal_index = int(rehearsal.split()[1]) - 1
    start_time_minutes = time_to_minutes(schedule_df.iloc[rehearsal_index]['Start Time'])
    end_time_minutes = time_to_minutes(schedule_df.iloc[rehearsal_index]['End Time'])
    total_rehearsal_time = end_time_minutes - start_time_minutes
    break_duration = schedule_df.iloc[rehearsal_index]['Break']
    current_time = start_time_minutes
    remaining_time = total_rehearsal_time
    cumulative_piece_time = 0

    for i, (piece, duration) in enumerate(pieces):
        cumulative_piece_time += duration  # Track the cumulative time

        #Check if there is enough time for the piece and the break
        if remaining_time >= cumulative_piece_time + break_duration:
            print(f"{time_to_minutes_to_time(current_time)} - {piece}")
            current_time += duration
            remaining_time -= duration
        else:
            #If not enough time for both piece and break, insert break if possible, then piece.
            if remaining_time >= break_duration:
                print(f"{time_to_minutes_to_time(current_time)} - Break")
                current_time += break_duration
                remaining_time -= break_duration
            print(f"{time_to_minutes_to_time(current_time)} - {piece}")
            current_time += duration
            remaining_time -= duration

    print("---")
