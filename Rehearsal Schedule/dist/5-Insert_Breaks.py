import pandas as pd
from datetime import timedelta, datetime
import sys

# Load the Excel files
rehearsal_data = pd.read_excel("timed_rehearsal_no_breaks.xlsx")
break_data = pd.read_excel("break_time_per_rehearsal.xlsx")

# Convert 'Start Time' and 'End Time' to datetime
rehearsal_data['Start Time'] = pd.to_datetime(rehearsal_data['Start Time'], format='%H:%M:%S').dt.time
rehearsal_data['End Time'] = pd.to_datetime(rehearsal_data['End Time'], format='%H:%M:%S').dt.time

# Function to divide time into intervals and insert breaks
def process_rehearsals(rehearsal_data, break_data):
    updated_rehearsals = []
    
    for rehearsal in rehearsal_data['Rehearsal'].unique():
        rehearsal_subset = rehearsal_data[rehearsal_data['Rehearsal'] == rehearsal]
        
        date = rehearsal_subset['Date'].iloc[0]
        start_time = datetime.combine(date, rehearsal_subset['Start Time'].iloc[0])
        end_time = datetime.combine(date, rehearsal_subset['End Time'].iloc[0])
        break_duration = int(break_data.loc[break_data['Rehearsal'] == rehearsal, 'Break Duration'].values[0])

        current_time = start_time
        total_duration = end_time - start_time
        break_inserted = False

        # Allocate works in time slots and handle break
        for index, row in rehearsal_subset.iterrows():
            piece_duration = timedelta(minutes=row['Rehearsal Time'])
            piece_end_time = current_time + piece_duration
            
            # Append current piece details
            updated_rehearsals.append({
                'Rehearsal': rehearsal,
                'Date': date,
                'Start Time': current_time.strftime('%H:%M'),
                'End Time': piece_end_time.strftime('%H:%M'),
                'Break': '',
                'Title': row['Title'],
                'Rehearsal Time': row['Rehearsal Time'],
                'Orchestration Differences': row['Orchestration Differences'],
            })
            
            current_time = piece_end_time
            
            # Here, prefer to insert the break earlier, but only if break_duration > 0
            if break_duration > 0:
                one_third_duration = total_duration / 3
                half_duration = total_duration / 2
                
                # Insert break if it is past one-third and before half
                if not break_inserted and current_time >= start_time + one_third_duration and current_time < start_time + half_duration:
                    # Insert break
                    break_start = current_time
                    break_end = break_start + timedelta(minutes=break_duration)

                    updated_rehearsals.append({
                        'Rehearsal': rehearsal,
                        'Date': date,
                        'Start Time': break_start.strftime('%H:%M'),
                        'End Time': break_end.strftime('%H:%M'),
                        'Break': break_duration,
                        'Title': 'Break',
                        'Rehearsal Time': break_duration,
                        'Orchestration Differences': '',
                    })

                    # Move current_time to the end of the break
                    current_time = break_end
                    break_inserted = True  # Mark that the break has been added

        # After the break, update timings for subsequent pieces
        for i in range(len(updated_rehearsals)):
            if updated_rehearsals[i]['Rehearsal'] == rehearsal and updated_rehearsals[i]['Title'] != 'Break':
                piece_start = datetime.strptime(updated_rehearsals[i]['Start Time'], '%H:%M')
                # Adjust the start time only for pieces that come after the break
                if piece_start >= break_start:  
                    updated_rehearsals[i]['Start Time'] = current_time.strftime('%H:%M')
                    updated_rehearsals[i]['End Time'] = (current_time + timedelta(minutes=updated_rehearsals[i]['Rehearsal Time'])).strftime('%H:%M')
                    current_time += timedelta(minutes=updated_rehearsals[i]['Rehearsal Time'])  # Move current_time forward

    return pd.DataFrame(updated_rehearsals)

# Process rehearsals and store result
final_rehearsals = process_rehearsals(rehearsal_data, break_data)

# Create the first export with specified columns
detailed_columns = ['Rehearsal', 'Date', 'Start Time', 'End Time', 'Title', 'Rehearsal Time', 'Orchestration Differences']
detailed_export = final_rehearsals[detailed_columns]

# Save the detailed export to Excel
detailed_export.to_excel("detailed_rehearsals.xlsx", index=False)

# Create the second export with the specified columns
summary_columns = ['Rehearsal', 'Date', 'Start Time', 'Title']
summary_export = final_rehearsals[summary_columns]

# Save the summary export to Excel
summary_export.to_excel("summary_rehearsals.xlsx", index=False)

print("Rehearsals exported successfully!")