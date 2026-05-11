import pandas as pd
from sklearn.metrics import pairwise_distances
import heapq

file_path_works = "User_Input.xlsx"
sheet_name_works = "Works"
file_path_rehearsal = "All Rehearsal Times.xlsx"
sheet_name_rehearsal = "Sheet1"
file_path_rehearsals_times = "User_Input.xlsx"
sheet_name_rehearsal_times = "Rehearsals"


# Load data for works (same as before)
data = pd.read_excel(file_path_works, sheet_name=sheet_name_works)
instrument_columns = data.columns[data.columns.get_loc("Flute"):data.columns.get_loc("Bass") + 1]
data["Total Players"] = data[instrument_columns].sum(axis=1)
orchestration_data = data.iloc[:, 4:-1]
distance_matrix = pairwise_distances(orchestration_data)
similarity_matrix = 1 / (1 + distance_matrix)
similarity_df = pd.DataFrame(similarity_matrix, index=data['title'], columns=data['title'])

# Load rehearsal times (assuming multiple rehearsal columns)
rehearsal_data = pd.read_excel(file_path_rehearsal, sheet_name=sheet_name_rehearsal)

# Function to create a schedule for a single rehearsal
def create_rehearsal_schedule_single(similarity_df, data, rehearsal_column):
    schedule = []
    used_titles = set()
    priority_queue = []

    #Filter out pieces that don't have rehearsal time for this specific rehearsal
    available_pieces = data[data[rehearsal_column] > 0].sort_values(by=['Total Players', rehearsal_column], ascending=[False, False]).copy()
    available_pieces['similarity_score'] = 0
    
    if not available_pieces.empty: # Check if there are any pieces to schedule
        starting_piece = available_pieces.iloc[0]
        heapq.heappush(priority_queue, (-starting_piece['Total Players'], -starting_piece[rehearsal_column], starting_piece['title']))

        while priority_queue:
            _, _, current_piece_title = heapq.heappop(priority_queue)

            if current_piece_title not in used_titles:
                rehearsal_time = data[data['title'] == current_piece_title][rehearsal_column].iloc[0]
                schedule.append((current_piece_title, rehearsal_time))
                used_titles.add(current_piece_title)
                available_pieces = available_pieces[~available_pieces['title'].isin(used_titles)]

                if not available_pieces.empty:
                    available_pieces['similarity_score'] = similarity_df.loc[current_piece_title, available_pieces['title']].values
                    for index, row in available_pieces.iterrows():
                        heapq.heappush(priority_queue, (-row['Total Players'], -row['similarity_score'], row['title']))
    return schedule


rehearsal_columns = [col for col in rehearsal_data.columns if 'Rehearsal' in col]

# Merge data with rehearsal times
data = pd.merge(data, rehearsal_data, on='title', how='left')


# Create schedules for each rehearsal and store them in a dictionary
rehearsal_schedules = {}
for col in rehearsal_columns:
    rehearsal_schedules[col] = create_rehearsal_schedule_single(similarity_df, data, col)

# Create a list of DataFrames, one for each rehearsal
dfs = []
for col, schedule in rehearsal_schedules.items():
    df_schedule = pd.DataFrame(schedule, columns=['Title', 'Rehearsal Time'])
    df_schedule['Rehearsal'] = col
    dfs.append(df_schedule)

# Concatenate the DataFrames into a single DataFrame
final_df = pd.concat(dfs, ignore_index=True)

# Export to Excel
output_file = "rehearsal_schedule.xlsx"
final_df.to_excel(output_file, index=False)
print(f"\nRehearsal schedule exported to {output_file}")