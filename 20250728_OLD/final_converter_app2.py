import os
import re
import csv
import tkinter as tk
from tkinter import filedialog, messagebox
from xml.etree.ElementTree import Element, SubElement, tostring, ElementTree

# Utility: convert timecode string to frames
def tc_to_frames(tc, fps):
    try:
        h, m, s, f = re.split('[:;]', tc)
        return (int(h)*3600 + int(m)*60 + int(s))*fps + int(f)
    except:
        return 0

# Parse CSV into list of event dicts
def load_events_from_csv(path):
    events = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            events.append(row)
    return events

# Extract scale and pos keyframes from "Keyframe Details"
def parse_keyframes(detail_str, duration_frames):
    # find all 'KF@...: scale=X, pos=Y'
    kfs = []
    for m in re.finditer(r"KF@[^:]+: scale=([0-9.]+), pos=([-0-9.]+)", detail_str):
        scale = float(m.group(1))
        pos = float(m.group(2))
        kfs.append((scale, pos))
    # Ensure two keyframes: start and end
    if not kfs:
        # default linear keys
        return [(0, 1.0, 0.0), (duration_frames, 1.0, 0.0)]
    if len(kfs) == 1:
        return [(0, kfs[0][0], kfs[0][1]), (duration_frames, kfs[0][0], kfs[0][1])]
    # use first and last
    return [(0, kfs[0][0], kfs[0][1]), (duration_frames, kfs[-1][0], kfs[-1][1])]

# Generate FCPXML document
def generate_full_fcpxml(events, output_path, fps=25.0):
    # root
    fcpxml = Element('fcpxml', version="1.8")
    resources = SubElement(fcpxml, 'resources')
    library = SubElement(fcpxml, 'library')
    event_node = SubElement(library, 'event', name="ConvertedSequence")
    project = SubElement(event_node, 'project', name="ConvertedProject")
    sequence = SubElement(project, 'sequence', format="r1")
    spine = SubElement(sequence, 'spine')

    # Add assets
    asset_ids = {}
    for ev in events:
        src = ev.get('Source File Path', '') + '/' + ev.get('Source File Name', '')
        if src not in asset_ids:
            aid = f"r{len(asset_ids)+1}"
            asset_ids[src] = aid
            SubElement(resources, 'asset', id=aid, src=src)

    # Add clips
    for ev in events:
        src = ev.get('Source File Path', '') + '/' + ev.get('Source File Name', '')
        aid = asset_ids.get(src)
        if not aid: continue
        # clip item
        clip = SubElement(spine, 'clip', name=ev.get('Clip Name',''), duration=f"{int(ev['Event Length'])}/{fps}", start=f"{tc_to_frames(ev['Timeline Start TC'], fps)}/{fps}")
        clipref = SubElement(clip, 'video', ref=aid)
        # keyframe metadata
        details = ev.get('Keyframe Details','')
        dur = int(ev.get('Event Length',0))
        kfs = parse_keyframes(details, dur)
        # Add parameters for scale and position
        param_scale = SubElement(clipref, 'param', name="scale", keyframeType="linear")
        param_pos = SubElement(clipref, 'param', name="position", keyframeType="linear")
        for frame_idx, scale_val, pos_val in kfs:
            t = frame_idx / fps
            SubElement(param_scale, 'key', time=f"{t}", value=f"{scale_val}")
            SubElement(param_pos, 'key', time=f"{t}", value=f"{pos_val}")

    # write XML
    tree = ElementTree(fcpxml)
    tree.write(output_path, encoding='utf-8', xml_declaration=True)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Final Converter GUI v2")
        self.geometry("400x200")
        tk.Button(self, text="Load CSV Events", command=self.load_csv).pack(pady=10)
        self.csv_label = tk.Label(self, text="No CSV loaded", fg="grey")
        self.csv_label.pack()
        tk.Button(self, text="Export FCPXML", command=self.export_xml, state=tk.DISABLED).pack(pady=10)
        self.events = []

    def load_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV Files","*.csv")])
        if not path: return
        try:
            self.events = load_events_from_csv(path)
            self.csv_label.config(text=os.path.basename(path), fg="black")
            self.csv_path = path
            self.children['!button2'].config(state=tk.NORMAL)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load CSV:\n{e}")

    def export_xml(self):
        path = filedialog.asksaveasfilename(defaultextension=".fcpxml", filetypes=[("FCPXML Files","*.fcpxml")])
        if not path: return
        try:
            generate_full_fcpxml(self.events, path)
            messagebox.showinfo("Success", f"FCPXML saved to {path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

if __name__ == '__main__':
    App().mainloop()
