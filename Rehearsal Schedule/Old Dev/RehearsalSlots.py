import pandas as pd
from datetime import datetime, timedelta

def generate_rehearsal_slots(num_rehearsals, start_time, end_time):
    start = datetime.strptime(start_time, '%H:%M')
    end = datetime.strptime(end_time, '%H:%M')

    if start >= end:
        raise ValueError("Start time must be before end time.")

    # Generate the 5-minute time slots based on start and end times
    rehearsal_slots = {}
    slots = []

    current_time = start
    while current_time < end:
        slots.append(current_time.strftime('%H:%M'))
        current_time += timedelta(minutes=5)

    # Assign the same slots to the specified number of rehearsals
    for i in range(num_rehearsals):
        rehearsal_number = f'Rehearsal {i + 1}'
        rehearsal_slots[rehearsal_number] = slots.copy()  # Use .copy() to avoid referencing the same list
    
    return rehearsal_slots

def export_to_excel(slots, filename):
    # Create a list of dictionaries for the DataFrame
    data = []
    for rehearsal, times in slots.items():
        for time in times:
            data.append({"Rehearsal": rehearsal, "Time": time})
    
    # Create a DataFrame from the list of dictionaries
    df = pd.DataFrame(data)

    # Write the DataFrame to an Excel file
    df.to_excel(filename, index=False)

# Example usage
num_rehearsals =   # Specify the number of rehearsal sessions
start_time = '19:15'  # Specify the start time in HH:MM format
end_time = '21:30'    # Specify the end time in HH:MM format

slots = generate_rehearsal_slots(num_rehearsals, start_time, end_time)

output_filename = 'rehearsal_slots.xlsx'
export_to_excel(slots, output_filename)

print(f"Rehearsal slots exported to {output_filename}.")
