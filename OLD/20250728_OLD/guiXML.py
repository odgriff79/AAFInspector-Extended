import tkinter as tk
from tkinter import filedialog, ttk, scrolledtext, messagebox
import csv
import re
import os
from datetime import datetime

# --- FCPXML Conversion Logic ---

def get_frames_from_tc_string(tc_string):
    """Extracts frame count from 'HH:MM:SS:FF (framef)' format."""
    if not isinstance(tc_string, str): return 0
    match = re.search(r'\((\d+)f\)', tc_string)
    return int(match.group(1)) if match else 0

def frames_to_fcpxml_time(frames, rate):
    """Converts frame count to FCPXML time string format '123/25s'."""
    return f"{int(frames)}/{int(round(rate))}s"

def process_event_for_fcp_values(event, timeline_rate):
    """
    Parses an event's keyframe details and calculates the FCPXML-compatible values.
    Returns a list of dictionaries, one for each keyframe.
    """
    kf_details_str = event.get('Keyframe Details', '')
    if not kf_details_str or kf_details_str == 'N/A':
        return []

    clip_start_frames = get_frames_from_tc_string(event.get('Timeline Start TC', ''))
    converted_keyframes = []
    
    # Simple parser for the keyframe details string
    params = {}
    current_param = None
    for line in kf_details_str.strip().split('\n'):
        line = line.strip()
        param_match = re.match(r'-\s*Parameter:\s*(.+?)\s*\(', line)
        kf_match = re.search(r'Keyframe at .*?\((\d+)f\)\s*->\s*Value:\s*(.*)', line)
        if param_match:
            current_param = param_match.group(1)
            params[current_param] = []
        elif kf_match and current_param:
            frame, value_str = kf_match.groups()
            try:
                value = float(value_str.split('/')[0]) / float(value_str.split('/')[1]) if '/' in value_str else float(value_str)
                params[current_param].append({'frame': int(frame), 'value': value})
            except (ValueError, ZeroDivisionError):
                pass # Ignore values that can't be converted to float

    # Combine X and Y values into single keyframe entries
    pos_x_kfs = next((v for k, v in params.items() if 'POS_X' in k), [])
    pos_y_kfs = next((v for k, v in params.items() if 'POS_Y' in k), [])
    scale_x_kfs = next((v for k, v in params.items() if 'SCALE_X' in k or 'Zoom Factor' in k), [])
    scale_y_kfs = next((v for k, v in params.items() if 'SCALE_Y' in k or 'Zoom Factor' in k), [])

    # Get the original parameter names to check for DVE_ prefix etc.
    y_param_name = next((k for k in params if 'POS_Y' in k), "")
    scale_param_name = next((k for k in params if 'SCALE' in k or 'Zoom' in k), "")
    
    all_frames = sorted(list(set(kf['frame'] for kf in pos_x_kfs + scale_x_kfs)))

    for frame in all_frames:
        kf_data = {}
        # Time Conversion
        relative_frames = frame - clip_start_frames
        kf_data['FCPXML_Time'] = frames_to_fcpxml_time(relative_frames, timeline_rate)
        
        # Position Conversion
        pos_x = next((kf['value'] for kf in pos_x_kfs if kf['frame'] == frame), None)
        pos_y = next((kf['value'] for kf in pos_y_kfs if kf['frame'] == frame), None)
        if pos_x is not None: kf_data['FCPXML_Position_X'] = pos_x
        if pos_y is not None:
            kf_data['FCPXML_Position_Y'] = pos_y * -1 if 'DVE_' in y_param_name else pos_y

        # Scale Conversion
        scale_x = next((kf['value'] for kf in scale_x_kfs if kf['frame'] == frame), None)
        if scale_x is not None:
            is_zoom = 'Zoom Factor' in scale_param_name
            kf_data['FCPXML_Scale_X'] = scale_x if is_zoom else scale_x / 100.0
            kf_data['FCPXML_Scale_Y'] = kf_data['FCPXML_Scale_X'] # Assume uniform scale

        if len(kf_data) > 1: # Only add if we have more than just time
            converted_keyframes.append(kf_data)
            
    return converted_keyframes

# --- GUI Application ---
class CsvViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SuperEDL Viewer & FCPXML Converter")
        self.root.geometry("1400x900")

        self.full_event_data = []

        # --- Top Frame ---
        top_frame = ttk.Frame(root, padding="10")
        top_frame.pack(side="top", fill="x")
        ttk.Button(top_frame, text="Load SuperEDL CSV File", command=self.load_csv).pack(side="left", padx=(0,10))
        self.filename_label = ttk.Label(top_frame, text="No CSV file loaded.")
        self.filename_label.pack(side="left", padx=10)
        self.generate_csv_button = ttk.Button(top_frame, text="Save Extended CSV...", command=self.save_extended_csv, state="disabled")
        self.generate_csv_button.pack(side="right")
        
        main_pane = ttk.PanedWindow(root, orient="vertical")
        main_pane.pack(expand=True, fill="both", padx=10, pady=5)

        top_half_pane = ttk.PanedWindow(main_pane, orient="horizontal")
        main_pane.add(top_half_pane, weight=1)

        details_frame = ttk.LabelFrame(top_half_pane, text="Selected Event Details (from SuperEDL)", padding="10")
        top_half_pane.add(details_frame, weight=2)
        self.detail_vars = {k: tk.StringVar() for k in ["Event", "Event Name", "TapeID", "DiskLabel", "Source File Path", "Source Clip start time code", "Source Clip offset", "StartTime", "Effect Name"]}
        for i, (key, var) in enumerate(self.detail_vars.items()):
            ttk.Label(details_frame, text=f"{key}:").grid(row=i, column=0, sticky="nsew", padx=5, pady=2)
            ttk.Entry(details_frame, textvariable=var, state="readonly", width=80).grid(row=i, column=1, sticky="ew", padx=5)
        details_frame.columnconfigure(1, weight=1)

        bottom_half_pane = ttk.PanedWindow(main_pane, orient="horizontal")
        main_pane.add(bottom_half_pane, weight=2)
        
        tree_frame = ttk.Frame(bottom_half_pane)
        bottom_half_pane.add(tree_frame, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("Event", "Event Name", "Timeline Start TC"), show="headings")
        self.tree.heading("Event", text="Event #")
        self.tree.heading("Event Name", text="Event Name")
        self.tree.heading("Timeline Start TC", text="Timeline Start TC")
        self.tree.column("Event", width=60, anchor="center")
        self.tree.pack(expand=True, fill="both")
        
        conversion_frame = ttk.LabelFrame(bottom_half_pane, text="FCPXML Converted Keyframe Values", padding="10")
        bottom_half_pane.add(conversion_frame, weight=2)
        self.conversion_text = scrolledtext.ScrolledText(conversion_frame, wrap=tk.WORD, font=("Courier New", 10))
        self.conversion_text.pack(expand=True, fill="both")
        
        self.tree.bind("<<TreeviewSelect>>", self.on_event_select)

    def load_csv(self):
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
        if not file_path: return
        self.filename_label.config(text=os.path.basename(file_path))
        self.load_and_parse_csv(file_path)

    def load_and_parse_csv(self, file_path):
        summary_data = {}
        event_data = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                is_summary = True; header = []
                for row in reader:
                    if not row: is_summary = False; continue
                    if is_summary:
                        if len(row) > 1: summary_data[row[0].strip()] = row[1].strip()
                    else:
                        if not header: header = [h.strip() for h in row]
                        else: event_data.append(dict(zip(header, row)))
            
            # Perform conversions and add to data
            timeline_rate_str = summary_data.get('Timeline Edit Rate', '25.0')
            timeline_rate = float(re.match(r'([0-9\.]+)', timeline_rate_str).group(1))
            
            for event in event_data:
                event['FCPXML_Converted_Keyframes'] = process_event_for_fcp_values(event, timeline_rate)

            self.full_event_data = event_data
            
            for item in self.tree.get_children(): self.tree.delete(item)
            for event in self.full_event_data:
                self.tree.insert("", "end", values=(event.get('Event'), event.get('Event Name'), event.get('Timeline Start TC')))
            
            self.generate_csv_button.config(state="normal")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse CSV: {e}")

    def on_event_select(self, event):
        selected_item = self.tree.selection()
        if not selected_item: return
            
        item_values = self.tree.item(selected_item[0])['values']
        event_num_str = str(item_values[0])
        
        selected_event_data = next((e for e in self.full_event_data if e.get('Event') == event_num_str), {})
        
        for key, var in self.detail_vars.items():
            var.set(selected_event_data.get(key, ''))
        
        self.conversion_text.delete('1.0', tk.END)
        converted_kfs = selected_event_data.get('FCPXML_Converted_Keyframes', [])
        if converted_kfs:
            display_text = "[\n"
            for kf in converted_kfs:
                display_text += "  {\n"
                for key, val in kf.items():
                    display_text += f"    '{key}': {val},\n"
                display_text += "  },\n"
            display_text += "]"
            self.conversion_text.insert('1.0', display_text)
        else:
            self.conversion_text.insert('1.0', 'No keyframes to convert.')

    def save_extended_csv(self):
        output_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")], title="Save Extended CSV Report")
        if not output_path: return
        try:
            # Flatten the keyframe data for CSV export
            export_data = []
            for event in self.full_event_data:
                new_event = event.copy()
                # Store converted keyframes as a simple string for readability in CSV
                new_event['FCPXML_Converted_Keyframes'] = str(new_event['FCPXML_Converted_Keyframes'])
                export_data.append(new_event)
                
            header = list(export_data[0].keys())
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()
                writer.writerows(export_data)
            messagebox.showinfo("Success", f"Extended CSV file saved successfully to:\n{output_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save extended CSV: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = CsvViewerApp(root)
    root.mainloop()