import os
import csv
import re
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent
from datetime import datetime

# --- Utility Functions ---

def tc_to_frames(tc_string, fps):
    """Converts HH:MM:SS:FF or HH:MM:SS;FF to a frame count."""
    if not isinstance(tc_string, str) or not tc_string.strip(): return 0
    try:
        parts = list(map(int, re.split('[:;]', tc_string.strip())))
        return (parts[0]*3600 + parts[1]*60 + parts[2]) * int(fps) + parts[3]
    except: return 0

def rational_to_float_str(s):
    """Converts a fractional string like '333/16' to a float string."""
    if isinstance(s, str) and "/" in s:
        try:
            num, den = map(float, s.split('/'))
            return str(num / den if den != 0 else 0)
        except: return "0"
    return str(s)

def parse_keyframes_from_string(details_string):
    """Parses the multi-line keyframe string from the CSV into structured data."""
    params = {}
    current_param = None
    if not isinstance(details_string, str): return {}
    
    for line in details_string.strip().split('\n'):
        line = line.strip()
        param_match = re.match(r"- Parameter: (\S+)", line)
        if param_match:
            current_param = param_match.group(1)
            if current_param not in params: params[current_param] = []
            continue
            
        kf_match = re.match(r"Keyframe at .*? -> Value: (.+)", line)
        if kf_match and current_param:
            params[current_param].append(kf_match.group(1))
            
    return params

class FCPXMLBuilderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF CSV to FCPXML Converter")
        self.root.geometry("800x600")
        tk.Button(root, text="Load Super EDL CSV File", command=self.load_and_process).pack(pady=10)
        self.log_widget = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log_widget.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log(self, message):
        self.log_to_gui(message)
        self.debug_log.append(f"[{datetime.now().time()}] {message}")

    def log_to_gui(self, msg):
        self.log_widget.insert(tk.END, msg + "\n"); self.log_widget.see(tk.END)

    def load_and_process(self):
        csv_path = filedialog.askopenfilename(title="Select Super EDL CSV", filetypes=[("CSV Files", "*.csv")])
        if not csv_path: return

        output_path = filedialog.asksaveasfilename(title="Save FCP7 XML As", defaultextension=".xml", filetypes=[("XML Files", "*.xml")])
        if not output_path: return

        self.debug_log = []
        self.log_widget.delete(1.0, tk.END)
        self.log(f"✅ Loaded CSV: {os.path.basename(csv_path)}")

        try:
            events, summary = self.load_csv_report(csv_path)
            if not events: self.log("❌ No valid events found."); return
            
            fps_match = re.search(r"(\d+\.?\d*)", summary.get("Timeline Edit Rate", "25.0"))
            fps = float(fps_match.group(1)) if fps_match else 25.0
            self.log(f"Found {len(events)} events. Timeline rate: {fps} fps.")

            self.build_fcpxml(events, summary, fps, output_path)
        except Exception as e:
            self.log(f"\n--- ERROR ---\n{e}")
            messagebox.showerror("Error", f"An unexpected error occurred:\n{e}")
        finally:
            log_path = os.path.join(os.path.dirname(output_path), f"fcpxml_conversion_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.debug_log))
            self.log_to_gui(f"\n✅ Detailed debug log saved to:\n{os.path.basename(log_path)}")

    def load_csv_report(self, filepath):
        with open(filepath, 'r', encoding='utf-8-sig') as f: lines = f.readlines()
        summary, events, in_summary, header = {}, [], True, None
        reader = csv.reader(lines)
        for row in reader:
            if not row:
                if header: in_summary = False
                continue
            if in_summary:
                if len(row) >= 2: summary[row[0]] = row[1]
            else:
                if not header: header = [h.strip() for h in row]
                else: events.append(dict(zip(header, row)))
        return events, summary

    def build_fcpxml(self, events, summary, fps, output_path):
        self.log("\nBuilding FCP7 XML (xmeml) structure...")
        xmeml = Element('xmeml', {'version': '5'})
        sequence = SubElement(xmeml, 'sequence')
        SubElement(sequence, 'name').text = summary.get("Timeline Name", "Converted Sequence")
        duration = tc_to_frames(summary.get("Timeline Length", "0").split(' ')[0], fps)
        SubElement(sequence, 'duration').text = str(duration)
        
        rate = SubElement(sequence, 'rate')
        SubElement(rate, 'timebase').text = str(int(fps))
        SubElement(rate, 'ntsc').text = 'FALSE'
        
        tc = SubElement(sequence, 'timecode')
        SubElement(tc, 'string').text = summary.get("Timeline Start", "00:00:00:00")
        SubElement(tc, 'frame').text = str(tc_to_frames(summary.get("Timeline Start", "0"), fps))
        SubElement(tc, 'displayformat').text = 'NDF'
        
        media = SubElement(sequence, 'media')
        video = SubElement(media, 'video')
        track = SubElement(video, 'track')

        self.log("\nProcessing timeline events...")
        for idx, event_data in enumerate(events):
            self.log(f"  - Processing Event #{idx+1}: {event_data.get('Source File Name')}")
            
            start_frames = tc_to_frames(event_data.get("Timeline In"), fps) - tc_to_frames(summary.get("Timeline Start", "0"), fps)
            end_frames = start_frames + int(event_data.get("Length", 0))
            in_frames = tc_to_frames(event_data.get("Event In (at source)"), fps)
            out_frames = in_frames + int(event_data.get("Length", 0))

            clipitem = SubElement(track, 'clipitem', id=f"clipitem-{idx+1}")
            SubElement(clipitem, 'name').text = event_data.get("Source File Name", "Filler")
            SubElement(clipitem, 'duration').text = str(duration)
            SubElement(clipitem, 'start').text = str(start_frames)
            SubElement(clipitem, 'end').text = str(end_frames)
            SubElement(clipitem, 'in').text = str(in_frames)
            SubElement(clipitem, 'out').text = str(out_frames)
            
            file_el = SubElement(clipitem, 'file', id=f"file-{idx+1}")
            SubElement(file_el, 'name').text = event_data.get("Source File Name")
            pathurl = SubElement(file_el, 'pathurl')
            pathurl.text = f"file://{event_data.get('Source File Path')}/{event_data.get('Source File Name')}"
            
            effect_name = event_data.get("Effect Applied")
            if effect_name and "No" not in effect_name:
                self.log(f"    - Found effect: {effect_name}")
                keyframes = parse_keyframes_from_string(event_data.get("Keyframe Details", ""))
                
                # Create Basic Motion filter
                filter_motion = SubElement(clipitem, 'filter')
                effect_motion = SubElement(filter_motion, 'effect')
                SubElement(effect_motion, 'name').text = 'Basic Motion'
                SubElement(effect_motion, 'effectid').text = 'basic'
                
                # Scale
                scale_param = SubElement(effect_motion, 'parameter', {'name': 'Scale', 'parameterid': 'scale'})
                if 'AFX_SCALE_X_U' in keyframes:
                    for val in keyframes['AFX_SCALE_X_U']:
                        # Simplified: Assumes timeline-relative keyframes from AAF, needs adjustment
                        SubElement(scale_param, 'keyframe', {'when': '0', 'value': rational_to_float_str(val)})
                
                # Center
                center_param = SubElement(effect_motion, 'parameter', {'name': 'Center', 'parameterid': 'center'})
                if 'AFX_POS_X_U' in keyframes and 'AFX_POS_Y_U' in keyframes:
                     for i in range(len(keyframes['AFX_POS_X_U'])):
                         h_val = rational_to_float_str(keyframes['AFX_POS_X_U'][i])
                         v_val = rational_to_float_str(keyframes['AFX_POS_Y_U'][i])
                         val_el = SubElement(center_param, 'value')
                         SubElement(val_el, 'horiz').text = h_val
                         SubElement(val_el, 'vert').text = v_val

        # This is a basic pretty-print
        def indent_xml(elem, level=0):
            i = "\n" + level*"    "
            if len(elem):
                if not elem.text or not elem.text.strip(): elem.text = i + "    "
                if not elem.tail or not elem.tail.strip(): elem.tail = i
                for elem in elem: indent_xml(elem, level+1)
                if not elem.tail or not elem.tail.strip(): elem.tail = i
            else:
                if level and (not elem.tail or not elem.tail.strip()): elem.tail = i
        
        indent_xml(xmeml)
        
        tree = ElementTree(xmeml)
        tree.write(output_path, encoding='utf-8', xml_declaration=True)
        self.log_to_gui(f"\n✅ FCPXML (FCP7 XML) successfully written to:\n{os.path.basename(output_path)}")
        messagebox.showinfo("Done", "FCPXML conversion complete.")

if __name__ == "__main__":
    root = tk.Tk()
    app = FCPXMLBuilderApp(root)
    root.mainloop()