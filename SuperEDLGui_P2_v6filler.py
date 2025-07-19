# Pasteable full script begins here

import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode conversion utility ---
def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    if frame_count is None or fps is None or fps <= 0:
        return "N/A"
    separator = ";" if is_drop_frame else ":"
    try:
        fc, int_fps = int(frame_count), round(float(fps))
        h = fc // (3600 * int_fps)
        m = (fc % (3600 * int_fps)) // (60 * int_fps)
        s = (fc % (60 * int_fps)) // int_fps
        f = fc % int_fps
        return f"{h:02}:{m:02}:{s:02}{separator}{f:02}"
    except Exception:
        return "N/A"

# --- Mob mapping ---
def create_mob_map(data):
    mob_map = {}
    for mob in data:
        if isinstance(mob, list) and len(mob) >= 4:
            mob_id = next((c[2] for c in mob[3] if c[0] == "MobID"), None)
            if mob_id:
                mob_map[mob_id] = mob
    return mob_map

def find_main_sequence_mob_and_start_tc(data):
    for mob in data:
        if isinstance(mob, list) and len(mob) >= 4:
            name = next((c[2] for c in mob[3] if c[0] == "Name"), None)
            if name and ".Exported." in name:
                slots_node = next((c for c in mob[3] if c[0] == "Slots"), None)
                if not slots_node or len(slots_node) < 4:
                    continue
                for slot in slots_node[3]:
                    if isinstance(slot, list):
                        seg = next((c for c in slot[3] if c[0] == "Segment"), None)
                        if seg and len(seg) > 3 and isinstance(seg[3], list):
                            first = seg[3][0]
                            if first and isinstance(first, list) and first[0] == "Timecode":
                                start_node = next((c for c in first[3] if c[0] == "Start"), None)
                                drop_node = next((c for c in first[3] if c[0] == "Drop"), None)
                                start_tc = int(start_node[2]) if start_node and len(start_node) > 2 else 0
                                is_drop = bool(drop_node[2]) if drop_node and len(drop_node) > 2 else False
                                return mob, start_tc, is_drop
    return None, 0, False

# --- Search logic ---
def has_nested_source_clip(node):
    if not isinstance(node, list):
        return False
    if node[0] == "SourceClip":
        return True
    children = node[3] if len(node) > 3 else []
    return any(has_nested_source_clip(c) for c in children)

def recursive_search(node, timeline_offset=0, edit_rate=25.0, results=None, dedupe_set=None):
    if results is None:
        results = []
    if dedupe_set is None:
        dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2:
        return results

    name = node[0]
    children = node[3] if len(node) > 3 else []

    if name == "Sequence":
        for c in children:
            recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
            if isinstance(c, list) and len(c) > 3:
                timeline_offset += next((int(x[2]) for x in c[3] if x[0] == "Length"), 0)

    elif name == "SourceClip":
        mobid = next((c[2] for c in children if c[0] == "SourceID"), None)
        offset = next((int(c[2]) for c in children if c[0] in ("Start", "StartTime")), 0)
        length = next((int(c[2]) for c in children if c[0] == "Length"), 0)
        key = (mobid, timeline_offset, offset)
        if mobid and key not in dedupe_set:
            dedupe_set.add(key)
            results.append({
                "MobID": mobid,
                "TimelineStartFrame": timeline_offset,
                "SourceOffsetFrames": offset,
                "Length": length,
                "TimelineEditRate": edit_rate
            })

    elif name == "OperationGroup":
        if has_nested_source_clip(node):
            for c in children:
                recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
        else:
            length = next((int(c[2]) for c in children if c[0] == "Length"), 0)
            file_path = "N/A"
            filepath_node = next((c for c in children if c[0] == "Filepath"), None)
            if filepath_node and isinstance(filepath_node[2], list):
                try:
                    byte_vals = filepath_node[2]
                    if all(isinstance(b, int) and 0 <= b <= 255 for b in byte_vals):
                        decoded = bytes(byte_vals).decode("utf-16le", errors="ignore").strip('\x00')
                        file_path = decoded.replace("\\", "/")
                except Exception:
                    file_path = "Decode error"
            results.append({
                "MobID": "PanZoomFiller",
                "TimelineStartFrame": timeline_offset,
                "SourceOffsetFrames": 0,
                "Length": length,
                "TimelineEditRate": edit_rate,
                "FilePath": file_path
            })

    else:
        for c in children:
            recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)

    return results

# --- GUI & Output ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Super EDL Generator")
        self.root.geometry("1200x800")
        tk.Button(root, text="Load AAF Export (JSON) File", command=self.load_json).pack(pady=10)
        self.filename_label = tk.Label(root, text="No file loaded.", fg="grey")
        self.filename_label.pack(pady=2)
        self.generate_button = tk.Button(root, text="Generate Super EDL", command=self.process, state=tk.DISABLED)
        self.generate_button.pack(pady=5)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            self.json_path = path
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.json_data = json.load(f)
                self.log.delete(1.0, tk.END)
                self.log_msg(f"✅ Loaded JSON file:\n{path}")
                self.filename_label.config(text=os.path.basename(path), fg="black")
                self.generate_button.config(state=tk.NORMAL)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load or parse JSON file:\n{e}")
                self.filename_label.config(text="Failed to load file.", fg="red")
                self.generate_button.config(state=tk.DISABLED)

    def process(self):
        if not hasattr(self, 'json_data') or not self.json_data:
            messagebox.showerror("Error", "Please load a file first.")
            return
        output_dir = filedialog.askdirectory(title="Select Output Directory")
        if not output_dir:
            self.log_msg("❌ Report generation cancelled.")
            return
        self.log.delete(1.0, tk.END)
        self.log_msg("1. Building Mob map...")
        mob_map = create_mob_map(self.json_data)

        self.log_msg("2. Finding main sequence...")
        sequence_mob, start_tc, is_drop_frame = find_main_sequence_mob_and_start_tc(self.json_data)
        if not sequence_mob:
            self.log_msg("❌ Could not find main sequence.")
            return

        timeline_rate = 25.0
        slots = next((c for c in sequence_mob[3] if c[0] == "Slots"), None)
        if slots and len(slots) > 3:
            for slot in slots[3]:
                if isinstance(slot, list) and len(slot) > 3:
                    rate_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "EditRate"), None)
                    if rate_node and len(rate_node) > 2:
                        try:
                            rate_str = str(rate_node[2])
                            if "/" in rate_str:
                                num, den = map(float, rate_str.split('/'))
                                timeline_rate = num / den if den != 0 else 0
                            else:
                                timeline_rate = float(rate_str)
                            break
                        except Exception:
                            continue

        self.log_msg("3. Extracting timeline events...")
        events = recursive_search(sequence_mob, timeline_offset=start_tc, edit_rate=timeline_rate)

        # ✅ Corrected timeline length logic
        timeline_len = max((e["TimelineStartFrame"] + e["Length"] for e in events), default=0) - start_tc

        summary_info = {
            "Timeline Name": next((c[2] for c in sequence_mob[3] if c[0] == "Name"), "N/A"),
            "Timeline Edit Rate": f"{timeline_rate} {'(DF)' if is_drop_frame else '(NDF)'}",
            "Timeline Start": frames_to_tc(start_tc, timeline_rate, is_drop_frame),
            "Timeline Length": frames_to_tc(timeline_len, timeline_rate, is_drop_frame) + f" ({timeline_len} frames)",
            "Total number of events found": len(events),
            "Total number of sources": len({e["MobID"] for e in events if e.get("MobID") != "PanZoomFiller"})
        }

        self.log_msg("\n--- Timeline Summary ---")
        for key, value in summary_info.items():
            self.log_msg(f"  {key}: {value}")

        self.log_msg("\n--- Event Details ---")
        for i, event in enumerate(events, start=1):
            mobid = event["MobID"]
            self.log_msg(f"\nEvent: {i} | {mobid}")
            self.log_msg(f"  Timeline In: {frames_to_tc(event['TimelineStartFrame'], timeline_rate, is_drop_frame)}")
            self.log_msg(f"  Length: {event['Length']} frames")
            if mobid == "PanZoomFiller":
                self.log_msg(f"  Effect Path: {event.get('FilePath', 'N/A')}")
            else:
                self.log_msg(f"  Source Offset: {event['SourceOffsetFrames']}")

        filename = f"super_edl_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        output_path = os.path.join(output_dir, filename)

        try:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Timeline Summary"])
                for key, val in summary_info.items():
                    writer.writerow([key, val])
                writer.writerow([])
                writer.writerow(["Event#", "MobID", "TimelineIn", "Length", "Offset", "FilePath"])
                for i, e in enumerate(events, start=1):
                    writer.writerow([
                        i,
                        e["MobID"],
                        frames_to_tc(e["TimelineStartFrame"], timeline_rate, is_drop_frame),
                        e["Length"],
                        e.get("SourceOffsetFrames", ""),
                        e.get("FilePath", "")
                    ])
            self.log_msg(f"\n✅ Analysis complete. Full report in:\n{output_path}")
        except Exception as e:
            self.log_msg(f"\n❌ Failed to write CSV: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
