import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading

def select_file():
    """Open a file dialog to select an Excel file."""
    file_path = filedialog.askopenfilename(title="Select an Excel file", filetypes=[("Excel Files", "*.xlsx")])
    if file_path:  # If a file is selected
        excel_file_var.set(file_path)  # Update the variable with the selected file path

def run_scripts():
    """Run the predefined scripts with the selected Excel file."""
    excel_file_name = excel_file_var.get()  
    if not excel_file_name:  # Check if a file has been selected
        messagebox.showwarning("No File Selected", "Please select an Excel file first.")
        return

    # Clear the log window
    log_text.delete(1.0, tk.END)

    # Disable the button to prevent re-running during execution
    run_scripts_button.config(state=tk.DISABLED)
    progress_bar.start()  # Start the progress bar animation

    # Using a thread to run scripts so that the GUI remains responsive
    threading.Thread(target=execute_scripts, args=(excel_file_name,)).start()

def execute_scripts(excel_file_name):
    """Execute each script in order and update the log window."""
    scripts = [
        "1-Import-Time_per_work_per_rehearsal.py",
        "2-Orchestration_organisation.py",
        "3-Organised_rehearsal_with_time.py",
        "4-Remove_breaks.py",
        "5-Insert_Breaks.py",
        "6-Final Compile.py"
    ]

    for script in scripts:
        try:
            log_text.insert(tk.END, f"Running {script}...\n")
            log_text.yview(tk.END)

            # If the script is your granularity-enabled script
            if script == "1-Import-Time_per_work_per_rehearsal.py":
                subprocess.run(["python", script, excel_file_name, "--granularity", granularity_var.get()], check=True)
            else:
                subprocess.run(["python", script, excel_file_name], check=True)

        except subprocess.CalledProcessError as e:
            log_text.insert(tk.END, f"An error occurred while running {script}: {e}\n")
            log_text.yview(tk.END)
            break

    progress_bar.stop()  # Stop the progress bar
    run_scripts_button.config(state=tk.NORMAL)  # Re-enable the button
    messagebox.showinfo("Success", "All scripts executed successfully!")

def main():
    """Create a simple Tkinter window."""
    global excel_file_var, run_scripts_button, log_text, progress_bar, granularity_var
    root = tk.Tk()
    root.title("Excel File Selector")

    excel_file_var = tk.StringVar()  # Variable to store the selected Excel file path

    # Excel file selection button
    select_file_button = tk.Button(root, text='Select Excel File', command=select_file)
    select_file_button.pack(pady=10)

    # Label to display selected file
    selected_file_label = tk.Label(root, textvariable=excel_file_var, wraplength=300, justify="center")
    selected_file_label.pack(pady=5)

    granularity_var = tk.StringVar(value="5")  # Default value

    # Granularity selection
    granularity_label = tk.Label(root, text="Select Time Rounding Granularity (minutes):")
    granularity_label.pack()

    granularity_dropdown = ttk.Combobox(root, textvariable=
                                        granularity_var, values=["1", "3", "5", "10", "15", "30"], state="readonly")
    granularity_dropdown.pack(pady=5)

    # Button to execute the scripts
    run_scripts_button = tk.Button(root, text='Run Scripts', command=run_scripts)
    run_scripts_button.pack(pady=10)

    # Progress bar
    progress_bar = ttk.Progressbar(root, mode='indeterminate')
    progress_bar.pack(pady=10, fill=tk.X)

    # Log window
    log_text = tk.Text(root, height=10, width=50)
    log_text.pack(pady=10)

    # Start the Tkinter event loop
    root.mainloop()

if __name__ == "__main__":
    main()
