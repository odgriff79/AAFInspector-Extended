import csv
import os
import re
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
from datetime import datetime
from pathlib import Path

# --- Helper Functions ---

def sanitize_for_xml(text):
    """Aggressively removes any non-ASCII characters to prevent XML errors."""
    if not isinstance(text, str):
        return ""
    return text.encode('ascii', 'ignore').decode('ascii').strip()

def parse_rate_string(rate_string):
    """Extracts float rate from strings like '25.0 (NDF)'."""
    match = re.match(r'([0-9\.]+)', rate_string)
    return float(match.group(1)) if match else 25.0

def frames_to_fcpxml_time(frames, rate):
    """Converts frame count to FCPXML time string format '123/25s'."""
    return f"{int(frames)}/{int(round(rate))}s"

def parse_keyframe_details(kf_string):
    """Parses the multi-line keyframe string from the CSV back into a structured dict."""
    if not kf_string or kf_string in ["N/A", "No animated parameters found."]: return {}
    params = {}
    current_param = None
    for line in kf_string.strip().split('\n'):
        line = line.strip()
        param_match = re.match(r'-\s*Parameter:\s*(.+?)\s*\(', line)
        kf_match = re.search(r'Keyframe at .*?\((\d+)f\)\s*->\s*Value:\s*(.*)', line)
        if param_match:
            current_param = sanitize_for_xml(param_match.group(1))
            params[current_param] = []
        elif kf_match and current_param:
            frame, value_str = kf_match.groups()
            try:
                value = float(value_str.split('/')[0]) / float(value_str.split('/')[1]) if '/' in value_str else float(value_str)
            except (ValueError, ZeroDivisionError): value = value_str
            params[current_param].append({'frame': int(frame), 'value': value})
    return params

def get_frames_from_tc_string(tc_string):
    """Extracts frame count from 'HH:MM:SS:FF (framef)' format."""
    match = re.search(r'\((\d+)f\)', tc_string)
    return int(match.group(1)) if match else 0

def is_valid_abs_path(path_str):
    """Checks if a string is a valid, absolute path."""
    if not path_str or path_str == "N/A": return False
    try:
        return Path(path_str).is_absolute()
    except (TypeError, ValueError):
        return False

# --- XML Generation Core ---

def generate_fcpxml(summary_info, events_data, output_path):
    if not events_data: raise ValueError("Cannot generate FCPXML from empty event list.")
        
    timeline_rate = parse_rate_string(summary_info.get("Timeline Edit Rate", "25.0"))
    
    fcpxml = Element('fcpxml', version='1.9')
    resources = SubElement(fcpxml, 'resources')
    fmt = SubElement(resources, 'format', id='r0', name=f'FFVideoFormat{int(timeline_rate)}p',
                     width='1920', height='1080', frameDuration=f'1/{int(timeline_rate)}s')

    asset_map = {}
    asset_id_counter = 1
    for event in events_data:
        src_path = event.get("Source File Path")
        if is_valid_abs_path(src_path) and src_path not in asset_map:
            asset_id = f'r{asset_id_counter}'
            asset_map[src_path] = asset_id
            asset_name = sanitize_for_xml(event.get("Source File Name"))
            asset = SubElement(resources, 'asset', id=asset_id, name=asset_name, hasVideo='1', format='r0')
            
            # --- THIS IS THE FIX for network paths ---
            media_uri = Path(src_path).as_uri()
            if media_uri.startswith('file:////'):
                media_uri = 'file://' + media_uri[len('file:////'):]
            SubElement(asset, 'media-rep', kind='original-media', src=media_uri)
            asset_id_counter += 1

    lib = SubElement(fcpxml, 'library')
    evt_name = sanitize_for_xml(summary_info.get("Timeline Name", "Imported Sequence"))
    evt = SubElement(lib, 'event', name=evt_name)
    proj = SubElement(evt, 'project', name=evt_name)
    
    sorted_events = sorted(events_data, key=lambda x: get_frames_from_tc_string(x.get('Timeline Start TC', '')))
    
    seq_start_frames = get_frames_from_tc_string(sorted_events[0]['Timeline Start TC'])
    last_event = max(sorted_events, key=lambda x: get_frames_from_tc_string(x.get('Timeline Start TC', '0')) + int(x.get('Event Length', 0)))
    seq_end_frames = get_frames_from_tc_string(last_event['Timeline Start TC']) + int(last_event.get('Event Length', 0))
    seq_len_frames = seq_end_frames - seq_start_frames
    
    sequence = SubElement(proj, 'sequence', format='r0',
                          duration=frames_to_fcpxml_time(seq_len_frames, timeline_rate),
                          tcStart=frames_to_fcpxml_time(seq_start_frames, timeline_rate),
                          tcFormat='NDF')
    spine = SubElement(sequence, 'spine')

    current_timeline_pos = seq_start_frames
    
    for event in sorted_events:
        clip_timeline_start_frames = get_frames_from_tc_string(event['Timeline Start TC'])
        clip_duration_frames = int(event.get('Event Length', 0))

        if clip_timeline_start_frames > current_timeline_pos:
            gap_duration = clip_timeline_start_frames - current_timeline_pos
            SubElement(spine, 'gap', duration=frames_to_fcpxml_time(gap_duration, timeline_rate))
        
        src_path = event.get("Source File Path")
        asset_id_ref = asset_map.get(src_path)
        clip_name = sanitize_for_xml(event.get("Clip Name"))

        clip_element = None
        if asset_id_ref:
            clip_source_start_frames = int(event.get('StartTime (frames)', 0))
            clip_element = SubElement(spine, 'asset-clip', ref=asset_id_ref, name=clip_name,
                                    duration=frames_to_fcpxml_time(clip_duration_frames, timeline_rate),
                                    start=frames_to_fcpxml_time(clip_source_start_frames, timeline_rate))
        else:
            clip_element = SubElement(spine, 'gap', name=clip_name,
                                    duration=frames_to_fcpxml_time(clip_duration_frames, timeline_rate))

        animated_params = parse_keyframe_details(event.get('Keyframe Details', ''))
        if animated_params and clip_element is not None:
            pos_x_param, pos_y_param, scale_x_param, scale_y_param = None, None, None, None
            for p_name, kfs in animated_params.items():
                if 'POS_X' in p_name: pos_x_param = (p_name, kfs)
                if 'POS_Y' in p_name: pos_y_param = (p_name, kfs)
                if 'SCALE_X' in p_name or 'Zoom Factor' in p_name: scale_x_param = (p_name, kfs)
                if 'SCALE_Y' in p_name or 'Zoom Factor' in p_name: scale_y_param = (p_name, kfs)

            if pos_x_param and pos_y_param:
                param_pos = SubElement(clip_element, 'param', name='Position', key='9999/999166631/1/80/2')
                for kf_x in pos_x_param[1]:
                    y_val = next((kf_y['value'] for kf_y in pos_y_param[1] if kf_y['frame'] == kf_x['frame']), 0)
                    if 'DVE_' in pos_y_param[0]: y_val *= -1
                    kf_time_relative = kf_x['frame'] - clip_timeline_start_frames
                    kf_time = frames_to_fcpxml_time(kf_time_relative, timeline_rate)
                    SubElement(param_pos, 'keyframe', time=kf_time, value=f"{kf_x['value']} {y_val}", interp='linear')
            
            if scale_x_param and scale_y_param:
                param_scale = SubElement(clip_element, 'param', name='Scale', key='9999/999166631/1/83/2')
                for kf_x in scale_x_param[1]:
                    is_zoom_factor = 'Zoom Factor' in scale_x_param[0]
                    scale_val = kf_x['value'] if is_zoom_factor else kf_x['value'] / 100.0
                    kf_time_relative = kf_x['frame'] - clip_timeline_start_frames
                    kf_time = frames_to_fcpxml_time(kf_time_relative, timeline_rate)
                    SubElement(param_scale, 'keyframe', time=kf_time, value=f"{scale_val*100} {scale_val*100}", interp='linear')

        current_timeline_pos = clip_timeline_start_frames + clip_duration_frames

    xml_string = tostring(fcpxml, 'utf-8')
    pretty_xml = minidom.parseString(xml_string).toprettyxml(indent="  ")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(pretty_xml)

# --- GUI Application ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("FINAL CONVERTER (v3)")
        self.root.geometry("800x600")
        
        tk.Button(root, text="Load SuperEDL CSV File", command=self.load_csv).pack(pady=10)
        self.filename_label = tk.Label(root, text="No CSV file loaded.", fg="grey")
        self.filename_label.pack(pady=2)
        
        self.generate_button = tk.Button(root, text="Generate FCPXML for Resolve", command=self.process, state=tk.DISABLED)
        self.generate_button.pack(pady=5)
        
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

        self.summary_info = {}
        self.events_data = []

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_csv(self):
        path = filedialog.askopenfilename(title="Select SuperEDL CSV Report", filetypes=[("CSV Files", "*.csv")])
        if not path: return

        self.summary_info.clear()
        self.events_data.clear()
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                is_summary_section = True
                header = []
                for row in reader:
                    if not row: # Blank row is the separator
                        is_summary_section = False
                        continue
                    if is_summary_section:
                        if len(row) > 1 and row[0]: self.summary_info[row[0]] = row[1]
                    else:
                        if not header:
                            header = row
                        elif any(field.strip() for field in row):
                            self.events_data.append(dict(zip(header, row)))
            
            self.filename_label.config(text=os.path.basename(path), fg="black")
            self.log.delete(1.0, tk.END)
            self.log_msg(f"✅ Successfully loaded and parsed CSV:\n{path}")
            self.log_msg(f"   Found {len(self.events_data)} events.")
            self.generate_button.config(state=tk.NORMAL)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load or parse CSV file:\n{e}")
            self.filename_label.config(text="Failed to load file.", fg="red")
            self.generate_button.config(state=tk.DISABLED)

    def process(self):
        if not self.events_data:
            messagebox.showerror("Error", "No event data loaded from CSV."); return

        out_name = f"resolve_timeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xml"
        output_path = filedialog.asksaveasfilename(
            title="Save FCPXML File", initialfile=out_name, defaultextension=".xml",
            filetypes=[("FCPXML Files", "*.xml")])
        if not output_path:
            self.log_msg("❌ FCPXML generation cancelled."); return

        try:
            self.log_msg("\nGenerating FCPXML...")
            generate_fcpxml(self.summary_info, self.events_data, output_path)
            self.log_msg(f"✅ FCPXML successfully generated:\n{output_path}")
            messagebox.showinfo("Done", "FCPXML file generated successfully.")
        except Exception as e:
            self.log_msg(f"❌ An error occurred during FCPXML generation:\n{e}")
            messagebox.showerror("Error", f"An error occurred during FCPXML generation:\n{e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()