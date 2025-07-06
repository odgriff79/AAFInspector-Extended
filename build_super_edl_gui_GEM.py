import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from urllib.parse import urlparse

# --- Timecode Conversion Utilities ---
def tc_to_frames(tc, frame_rate):
    if not isinstance(tc, str) or tc.count(':') != 3: return 0
    try:
        H, M, S, F = map(int, tc.split(':'))
        return (H * 3600 + M * 60 + S) * int(frame_rate) + F
    except (ValueError, TypeError):
        return 0

def frames_to_tc(frames, frame_rate):
    if not frame_rate or frame_rate == 0: return "00:00:00:00"
    fr = int(frame_rate)
    H, M, S, F = int(frames / (3600*fr)), int((frames / (60*fr))%60), int((frames / fr)%60), int(frames%fr)
    return f"{H:02d}:{M:02d}:{S:02d}:{F:02d}"

# --- Parser Class ---
class AafJsonParser:
    def __init__(self, json_data, log_func):
        self.json_data = json_data
        self.log = log_func
        self.mob_map = {}
        self.source_mob_start_times = {}
        self.events = []
        self.timeline_cursor = 0
        self.sequence_start_tc = "01:00:00:00"

    def parse(self):
        self.log("--- PARSING INITIATED ---")
        for top_level_obj in self.json_data:
            self._build_mob_map_recursive(top_level_obj)
        self.log(f"-> Found {len(self.mob_map)} total Mobs.")
        self._get_all_source_mob_start_times()
        self.log(f"-> Found start times for {len(self.source_mob_start_times)} Source Mobs.")
        main_sequence_node = self.json_data[0]
        self._find_sequence_start_tc(main_sequence_node)
        self.log(f"-> Sequence Start TC set to: {self.sequence_start_tc}")
        self._find_events_recursive(main_sequence_node, 25.0)
        self.log(f"-> Found {len(self.events)} events.")
        self.log("--- PARSING COMPLETE ---")
        return self.events

    def _build_mob_map_recursive(self, node):
        if not isinstance(node, list): return
        if len(node) > 2 and node[1] in ("SourceMob", "CompositionMob", "MasterMob"):
            mob_id = next((c[2] for c in node[3] if c[0] == "MobID"), None)
            if mob_id: self.mob_map[mob_id] = node
        if len(node) > 3 and isinstance(node[3], list):
            for child in node[3]: self._build_mob_map_recursive(child)

    def _get_all_source_mob_start_times(self):
        for mob_id, mob_node in self.mob_map.items():
            if mob_node[1] == "SourceMob":
                try:
                    slots = next((c[3] for c in mob_node[3] if c[0] == "Slots"), [])
                    tc_segment = slots[0][3][2][3][0]
                    if tc_segment[0] == 'Timecode':
                        start_frames = int(tc_segment[3][1][2]); fps = int(tc_segment[3][2][2])
                        self.source_mob_start_times[mob_id] = frames_to_tc(start_frames, fps)
                except (IndexError, TypeError, StopIteration):
                    self.source_mob_start_times[mob_id] = "00:00:00:00"
    
    def _find_sequence_start_tc(self, node):
        try:
            slots = next((c[3] for c in node[3] if c[0] == "Slots"), [])
            tc_slot = next((s for s in slots if s[3][2][3][0][0] == 'Timecode'), None)
            if tc_slot:
                tc_segment = tc_slot[3][2][3][0]
                start_frames = int(tc_segment[3][1][2]); fps = int(tc_segment[3][2][2])
                self.sequence_start_tc = frames_to_tc(start_frames, fps)
        except (IndexError, TypeError, StopIteration):
            pass

    def _find_events_recursive(self, node, edit_rate):
        if not isinstance(node, list) or len(node) < 1: return
        
        name = node[0]
        children = node[3] if len(node) > 3 else []
        current_rate = next((float(c[2].split('/')[0]) for c in children if c[0] == "EditRate"), edit_rate)

        if name in ("Sequence", "OperationGroup"):
            components = next((c[3] for c in children if c[0] in ("Components", "InputSegments")), [])
            for comp in components: self._find_events_recursive(comp, current_rate)
            return

        if name == "SourceClip":
            # --- START: THIS SECTION HAS BEEN FIXED ---
            source_id = next((c[2] for c in children if c[0] == "SourceID"), "Unknown")
            length = int(next((c[2] for c in children if c[0] == "Length"), 0))
            source_offset = int(next((c[2] for c in children if c[0] == "Start"), 0))

            source_file = "Unknown"
            url_string = "Unknown"
            
            source_mob_node = self.mob_map.get(source_id) # Use the map to find the Source Mob
            if source_mob_node:
                self.log(f"-> Found SourceClip, looking up MobID: {source_id[:25]}...")
                try:
                    # Get URL from the descriptor within the Source Mob
                    slots = next((c[3] for c in source_mob_node[3] if c[0] == "Slots"), [])
                    descriptor = slots[0][3][3][3][0]
                    url_string = next((c[2] for c in descriptor[3] if c[0] == "URLString"), "Unknown")
                    if url_string != "Unknown":
                        source_file = os.path.basename(urlparse(url_string).path)
                except (IndexError, TypeError, StopIteration):
                    self.log(f"   (Could not find URLString for MobID: {source_id[:25]}...)")
                    pass # Keep defaults if path doesn't exist

            self.events.append({
                "Source": source_id,
                "SourceFile": source_file,
                "URLString": url_string,
                "Length": length,
                "SourceStartFrame": source_offset,
                "EditRate": current_rate,
                "TimelineStartFrame": self.timeline_cursor,
                "SourceMobStartTC": self.source_mob_start_times.get(source_id, "00:00:00:00")
            })
            # --- END: FIX ---
            
            self.timeline_cursor += length
            return

        for child in children:
            self._find_events_recursive(child, current_rate)

# --- Main GUI Application ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Super EDL Generator (v5 - Fixed)")
        self.root.geometry("800x600")
        tk.Button(root, text="Select AAF as JSON File", command=self.load_json).pack(pady=10)
        tk.Button(root, text="Generate Super EDL", command=self.process).pack(pady=5)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=90, height=30)
        self.log.pack(pady=10, padx=10)
        self.json_data, self.json_dir = None, None

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n"); self.log.see(tk.END); self.root.update_idletasks()

    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files","*.json")])
        if not path: return
        self.json_dir = os.path.dirname(path)
        try:
            with open(path, 'r', encoding='utf-8') as f: self.json_data = json.load(f)
            self.log_msg(f"✅ Loaded JSON:\n{path}")
        except Exception as e:
            self.log_msg(f"❌ ERROR reading JSON: {e}")

    def process(self):
        if not self.json_data: self.log_msg("❌ Please load a JSON file first."); return
        self.log.delete(1.0, tk.END)
        try:
            parser = AafJsonParser(self.json_data, self.log_msg)
            events = parser.parse()

            if not events:
                self.log_msg("⚠️ Parsing finished but no events were found. Check JSON file content.")
                messagebox.showwarning("Warning", "No events found in the JSON file.")
                return

            txt_out_path = os.path.join(self.json_dir, "super_edl_details_output.txt")
            csv_out_path = os.path.join(self.json_dir, "super_edl_summary_output.csv")

            with open(txt_out_path, "w", encoding="utf-8") as txt_file, \
                 open(csv_out_path, "w", newline="", encoding="utf-8") as csv_file:
                
                csv_headers = ["Event", "Source", "SourceFile", "TimelineIn", "TimelineOut", "DurationFrames", "URLString"]
                writer = csv.DictWriter(csv_file, fieldnames=csv_headers)
                writer.writeheader()

                txt_file.write(f"SUPER EDL REPORT\nTimeline Start: {parser.sequence_start_tc}\n" + "="*40 + "\n\n")
                for i, event in enumerate(events):
                    event_num = i + 1; length = event['Length']; edit_rate = event['EditRate']
                    timeline_cursor, source_offset, source_mob_start_tc = event['TimelineStartFrame'], event['SourceStartFrame'], event['SourceMobStartTC']
                    seq_start_fr = tc_to_frames(parser.sequence_start_tc, edit_rate)
                    src_start_fr = tc_to_frames(source_mob_start_tc, edit_rate)
                    abs_timeline_in = seq_start_fr + timeline_cursor
                    abs_source_in = src_start_fr + source_offset
                    timeline_in_tc = frames_to_tc(abs_timeline_in, edit_rate)
                    timeline_out_tc = frames_to_tc(abs_timeline_in + length, edit_rate)

                    txt_file.write(f"Event {event_num:03d}\n")
                    txt_file.write(f'Source: "{event["Source"]}"\n')
                    txt_file.write(f'SourceFile: "{event["SourceFile"]}"\n')
                    txt_file.write(f'URLString: "{event["URLString"]}"\n')
                    txt_file.write(f"Timeline In: {timeline_in_tc}\n")
                    txt_file.write(f"Timeline Out: {timeline_out_tc}\n\n")

                    writer.writerow({
                        "Event": event_num, "Source": event["Source"], "SourceFile": event["SourceFile"],
                        "TimelineIn": timeline_in_tc, "TimelineOut": timeline_out_tc, 
                        "DurationFrames": length, "URLString": event["URLString"]
                    })

            self.log_msg(f"✅ Successfully generated Super EDL files.")
            messagebox.showinfo("Success", f"Files generated in:\n{self.json_dir}")
        except Exception as e:
            self.log_msg(f"❌ An unexpected error occurred: {e}")
            messagebox.showerror("Error", f"An error occurred during processing: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()