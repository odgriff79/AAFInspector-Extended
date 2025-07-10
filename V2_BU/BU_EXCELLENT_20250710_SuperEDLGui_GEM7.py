import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Filtering Function ---

# List of node names or prefixes to completely remove.
NODE_FILTER_LIST = [
    "Data Track", "color", "colour", "LUT", "FrameFlex", 
    "Manufacturer", "CDL", "Picture", "Sound"
]

def filter_data(node):
    """
    Recursively filters out unwanted nodes from the compressed JSON data structure.
    """
    if not isinstance(node, list):
        return node

    node_name = node[0]
    if isinstance(node_name, str):
        if node_name in NODE_FILTER_LIST:
            return None
        if any(node_name.startswith(prefix) for prefix in ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]):
            return None

    if len(node) > 3 and isinstance(node[3], list):
        filtered_children = [filter_data(child) for child in node[3]]
        node[3] = [child for child in filtered_children if child is not None]

    return node


# --- Timecode and Parsing Functions ---

def frames_to_tc(frame_count, fps=25.0):
    if frame_count is None or fps is None or fps <= 0: return "N/A"
    try:
        frame_count, fps, int_fps = int(frame_count), float(fps), round(float(fps))
        if int_fps == 0: return "N/A"
        h, m, s, f = frame_count // (3600 * int_fps), (frame_count % (3600 * int_fps)) // (60 * int_fps), (frame_count % (60 * int_fps)) // int_fps, frame_count % int_fps
        return f"{h:02}:{m:02}:{s:02}:{f:02}"
    except (ValueError, TypeError): return "N/A"

def create_mob_map(node, mob_map=None):
    if mob_map is None: mob_map = {}
    if not isinstance(node, list) or len(node) < 2: return mob_map
    children = node[3] if len(node) > 3 else []
    if any(isinstance(c, list) and c[0] == "MobID" for c in children):
        mobid = next((c[2] for c in children if isinstance(c, list) and c[0] == "MobID"), None)
        if mobid: mob_map[mobid] = node
    for c in children: create_mob_map(c, mob_map)
    return mob_map

def find_main_sequence_mob_and_start_tc(root_node):
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None, 0
    all_mobs, sequence_mob = root_node[3], None
    for mob in all_mobs:
        if not isinstance(mob, list) or len(mob) < 4: continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not slots_node or len(slots_node) < 4: continue
        is_sequence_mob = False
        for slot in slots_node[3]:
            if not isinstance(slot, list) or len(slot) < 4: continue
            segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
            if not segment_node or len(segment_node) < 4: continue
            list_of_segments = segment_node[3]
            if isinstance(list_of_segments, list) and len(list_of_segments) > 0:
                if isinstance(list_of_segments[0], list) and list_of_segments[0][0] == "Sequence":
                    is_sequence_mob = True; break
        if is_sequence_mob: sequence_mob = mob; break
    if not sequence_mob: return None, 0
    start_frame_count = 0
    slots_node = next((c for c in sequence_mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
    if slots_node and len(slots_node) >= 4:
        for slot in slots_node[3]:
            if not isinstance(slot, list) or len(slot) < 4: continue
            segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
            if not segment_node or len(segment_node) < 4: continue
            list_of_segments = segment_node[3]
            if not (isinstance(list_of_segments, list) and len(list_of_segments) > 0): continue
            timecode_node = list_of_segments[0]
            if isinstance(timecode_node, list) and timecode_node[0] == "Timecode":
                tc_children = timecode_node[3] if len(timecode_node) > 3 else []
                start_frame = next((c for c in tc_children if isinstance(c, list) and c[0] == "Start"), None)
                if start_frame and len(start_frame) > 2:
                    try: start_frame_count = int(start_frame[2]); break
                    except (ValueError, TypeError): continue
    return sequence_mob, start_frame_count

def extract_metadata_with_debug(mob_node):
    metadata = {
        "SourceClipStartFrames": 0, "URLString": "", "SourceEditRate": None,
        "DiskLabel": "", "TapeID": ""
    }
    if not mob_node or not isinstance(mob_node, list): return metadata, []

    all_starts = []
    
    def recursive_extract(n):
        if not isinstance(n, list): return
        
        node_name = n[0]
        if node_name in ("Start", "StartTime") and len(n) > 2:
            try:
                frames = int(n[2])
                if frames not in all_starts: all_starts.append(frames)
            except (ValueError, TypeError): pass
        elif node_name == "URLString" and len(n) > 2:
            metadata["URLString"] = n[2]
        elif node_name == "EditRate" and len(n) > 2:
            try: metadata["SourceEditRate"] = float(n[2])
            except: pass
        
        elif node_name == "TapeID" and not metadata["TapeID"]:
            value_node = next((c for c in n[3] if isinstance(c, list) and c[0] == "Value"), None)
            if value_node and len(value_node) > 2:
                metadata["TapeID"] = value_node[2]
        
        elif node_name in ("DiskLabel", "_IMPORTDISKLAB") and not metadata["DiskLabel"]:
            value_node = next((c for c in n[3] if isinstance(c, list) and c[0] == "Value"), None)
            if value_node and len(value_node) > 2:
                metadata["DiskLabel"] = value_node[2]
        
        elif node_name == "UserComments" and len(n) > 3:
            for comment in n[3]:
                if not isinstance(comment, list) or len(comment) < 3: continue
                comment_value = comment[2]
                if isinstance(comment_value, str):
                    if "TapeID=" in comment_value and not metadata["TapeID"]:
                        metadata["TapeID"] = comment_value.split("TapeID=")[1].split("\n")[0].strip()
        
        elif node_name == "MobAttributeList" and len(n) > 3:
             for attr in n[3]:
                 if isinstance(attr, list) and len(attr) > 3:
                     attr_name = next((c[2] for c in attr[3] if isinstance(c,list) and c[0] == "Name"), None)
                     attr_value = next((c[2] for c in attr[3] if isinstance(c,list) and c[0] == "Value"), "")
                     if attr_name == "TapeID" and not metadata["TapeID"]:
                         metadata["TapeID"] = attr_value
                     elif attr_name == "DiskLabel" and not metadata["DiskLabel"]:
                         metadata["DiskLabel"] = attr_value

        children = n[3] if len(n) > 3 else []
        for child in children:
            recursive_extract(child)

    recursive_extract(mob_node)
    
    if all_starts:
        metadata["SourceClipStartFrames"] = max(all_starts)
        
    return metadata, all_starts


def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, dedupe_set=None):
    if results is None: results = []
    if dedupe_set is None: dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2: return results
    
    name, children = node[0], node[3] if len(node) > 3 else []
    
    rate_node = next((c for c in children if isinstance(c, list) and c[0] in ("EditRate", "FPS")), None)
    if rate_node and len(rate_node) > 2:
        try: edit_rate = float(rate_node[2])
        except: pass
        
    if name == "Sequence":
        components_node = next((c for c in children if isinstance(c, list) and c[0] == "Components"), None)
        if components_node and len(components_node) > 3:
            for comp in components_node[3]:
                recursive_search(comp, timeline_offset, edit_rate, results, dedupe_set)
                if isinstance(comp, list) and len(comp) > 3:
                    length = next((int(x[2]) for x in comp[3] if isinstance(x, list) and x[0] == "Length"), 0)
                    timeline_offset += length
    elif name == "SourceClip":
        mobid = next((c[2] for c in children if isinstance(c, list) and c[0] == "SourceID"), None)
        track_id = next((c[2] for c in children if isinstance(c, list) and c[0] == "SourceTrackID"), "N/A")
        source_offset_frames = next((int(c[2]) for c in children if isinstance(c, list) and c[0] in ("Start", "StartTime")), 0)
        length = next((int(c[2]) for c in children if isinstance(c, list) and c[0] == "Length"), 0)
        dedupe_key = (mobid, timeline_offset, source_offset_frames)
        if mobid and dedupe_key not in dedupe_set:
            dedupe_set.add(dedupe_key)
            results.append({
                "MobID": mobid, "SourceTrackID": track_id,
                "TimelineStartFrame": timeline_offset, 
                "SourceOffsetFrames": source_offset_frames,
                "Length": length, "TimelineEditRate": edit_rate
            })
    else:
        for c in children:
            recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
            
    return results

def get_clip_name(mob_node):
    if not mob_node: return "Unknown"
    
    url = ""
    def find_url(n):
        nonlocal url
        if not isinstance(n, list): return
        if n[0] == "URLString" and len(n) > 2: url = n[2]; return True
        children = n[3] if len(n) > 3 else []
        for c in children:
            if find_url(c): return True
        return False
    find_url(mob_node)
    
    if url: 
        try: return os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path))
        except: pass
    return next((c[2] for c in mob_node[3] if isinstance(c, list) and c[0] == "Name"), "Unknown")

def resolve_source_mob(mob_id, mob_map, visited=None):
    if visited is None: visited = set()
    if mob_id in visited: return None
    visited.add(mob_id)

    mob = mob_map.get(mob_id)
    if not mob: return None

    url_string = ""
    def find_url(n):
        nonlocal url_string
        if not isinstance(n, list): return False
        if n[0] == "URLString" and len(n) > 2: url_string = n[2]; return True
        children = n[3] if len(n) > 3 else []
        for child in children:
            if find_url(child): return True
        return False

    if find_url(mob):
        return mob

    slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
    if slots_node:
        for slot in slots_node[3]:
            segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
            if segment_node and len(segment_node) > 3 and segment_node[3] and isinstance(segment_node[3][0], list):
                source_clip = segment_node[3][0]
                if source_clip and source_clip[0] == "SourceClip":
                    next_mob_id = next((c[2] for c in source_clip[3] if isinstance(c, list) and c[0] == "SourceID"), None)
                    if next_mob_id:
                        return resolve_source_mob(next_mob_id, mob_map, visited)
    
    return mob

class App:
    def __init__(self, root):
        self.root, self.json_path, self.json_data = root, None, None
        self.root.title("Super EDL Generator")
        self.root.geometry("1200x800")
        
        tk.Button(root, text="Load AAF Export (JSON) File", command=self.load_json).pack(pady=10)
        
        self.filename_label = tk.Label(root, text="No file loaded.", fg="grey")
        self.filename_label.pack(pady=2)

        self.generate_button = tk.Button(root, text="Generate Super EDL", command=self.process, state=tk.DISABLED)
        self.generate_button.pack(pady=5)
        
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg): self.log.insert(tk.END, msg + "\n"); self.log.see(tk.END)
    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.json_data = json.load(f)
                self.json_path = path
                self.log.delete(1.0, tk.END)
                self.log_msg(f"✅ Loaded JSON file:\n{path}")
                self.filename_label.config(text=os.path.basename(path), fg="black")
                self.generate_button.config(state=tk.NORMAL)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load or parse JSON file:\n{e}")
                self.json_data = None; self.json_path = None
                self.filename_label.config(text="Failed to load file.", fg="red")
                self.generate_button.config(state=tk.DISABLED)

    def process(self):
        if not self.json_data:
            messagebox.showerror("Error", "Please load the AAF file first.")
            return
        
        output_dir = filedialog.askdirectory(title="Select Output Directory")
        if not output_dir:
            self.log_msg("❌ Report generation cancelled by user.")
            return
            
        self.log.delete(1.0, tk.END)

        self.log_msg("0. Filtering out noisy data...");
        filtered_data = filter_data(self.json_data)
        self.log_msg("   - Filtering complete.")

        self.log_msg("1. Building Mob map..."); mob_map = create_mob_map(filtered_data)
        self.log_msg(f"   - Mobs indexed: {len(mob_map)}")
        self.log_msg("2. Finding main sequence..."); sequence_mob, start_tc = find_main_sequence_mob_and_start_tc(filtered_data)
        if not sequence_mob:
            self.log_msg("❌ Could not find a main sequence mob."); messagebox.showerror("Error", "Could not find a main sequence mob.")
            return

        total_timeline_length_frames = 0
        main_sequence_node = None
        slots_node = next((c for c in sequence_mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if slots_node:
            for slot in slots_node[3]:
                segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
                if segment_node and len(segment_node) > 3 and segment_node[3] and isinstance(segment_node[3][0], list) and segment_node[3][0][0] == "Sequence":
                    main_sequence_node = segment_node[3][0]
                    break
        if main_sequence_node:
            components_node = next((c for c in main_sequence_node[3] if isinstance(c, list) and c[0] == "Components"), None)
            if components_node and len(components_node) > 3:
                for comp in components_node[3]:
                    length = next((int(x[2]) for x in comp[3] if isinstance(x, list) and x[0] == "Length"), 0)
                    total_timeline_length_frames += length
        
        timeline_rate = None
        slots_node = next((c for c in sequence_mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if slots_node and len(slots_node) > 3:
            for slot in slots_node[3]:
                if isinstance(slot, list) and len(slot) > 3:
                    rate_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "EditRate"), None)
                    if rate_node and len(rate_node) > 2:
                        try:
                            timeline_rate = float(rate_node[2])
                            break 
                        except (ValueError, TypeError):
                            continue
        if timeline_rate is None:
            timeline_rate = 25.0
            mob_id_for_log = next((c[2] for c in sequence_mob[3] if c[0] == "MobID"), "N/A")
            self.log_msg(f"⚠️ Could not find Timeline Edit Rate in mob {mob_id_for_log}. Using fallback: 25.0")

        summary_info = {
            "Timeline Name": next((c[2] for c in sequence_mob[3] if isinstance(c, list) and c[0] == "Name"), "N/A"),
            "Timeline Edit Rate": timeline_rate,
            "Timeline Start TC": frames_to_tc(start_tc, timeline_rate),
            "Timeline Length": f"{frames_to_tc(total_timeline_length_frames, timeline_rate)} ({total_timeline_length_frames} frames)"
        }
        
        self.log_msg("3. Extracting timeline events..."); events = recursive_search(sequence_mob, timeline_offset=start_tc, edit_rate=timeline_rate)
        
        summary_info["Total Timeline Events"] = len(events)
        summary_info["Total Unique Source Clips"] = len({e["MobID"] for e in events})

        self.log_msg(f"   - Events found: {len(events)}")
        if not events:
            messagebox.showwarning("Warning", "No clip events were found.")
            return

        enriched = []
        debug_log_lines = []
        
        for idx, e in enumerate(events, 1):
            initial_mob = mob_map.get(e["MobID"])
            final_source_mob = resolve_source_mob(e["MobID"], mob_map)

            if not final_source_mob:
                self.log_msg(f"⚠️ MobID {e['MobID']} for event {idx} could not be resolved. Skipping.")
                continue
            
            master_md, _ = extract_metadata_with_debug(initial_mob)
            source_md, all_starts = extract_metadata_with_debug(final_source_mob)
            
            md = master_md.copy()
            md.update(source_md)
            
            clip_name = get_clip_name(final_source_mob)

            if not md.get("SourceEditRate"):
                self.log_msg(f"⚠️ Missing EditRate for '{clip_name}' (Clip {idx}) - using Timeline Rate ({timeline_rate})")

            source_clip_start_frames = md.get("SourceClipStartFrames", 0)
            source_edit_rate = md.get("SourceEditRate") or e["TimelineEditRate"]
            source_offset_frames = e["SourceOffsetFrames"]
            event_len_frames = e["Length"]
            start_time_frames = source_clip_start_frames + source_offset_frames
            end_time_frames = start_time_frames + event_len_frames
            
            debug_log_lines.extend([f"Event: {idx} | Clip Name: {clip_name}", f"MobID: {e['MobID']}", f"Found Start Values (frames): {sorted(all_starts)}", f"Selected Value (Max): {source_clip_start_frames}", "-" * 50])
            
            enriched.append({
                "Event Number": idx, "Clip Name": clip_name, "Source File Name": clip_name,
                "Source File Path (URL)": md.get("URLString", ""), "DiskLabel": md.get("DiskLabel", ""),
                "TapeID": md.get("TapeID", ""), "SourceMobID": e["MobID"],
                "Source TrackID": e["SourceTrackID"], "Source Edit Rate": source_edit_rate,
                "Source Clip Start TC": frames_to_tc(source_clip_start_frames, source_edit_rate),
                "Source Offset TC": frames_to_tc(source_offset_frames, source_edit_rate),
                "Event Start Time (from source)": frames_to_tc(start_time_frames, source_edit_rate),
                "Event End Time (from source)": frames_to_tc(end_time_frames, source_edit_rate),
                "Event Length (frames)": event_len_frames,
                "Timeline Start TC": frames_to_tc(e["TimelineStartFrame"], e["TimelineEditRate"]),
                "Source Clip Start (frames)": source_clip_start_frames,
                "Source Offset (frames)": source_offset_frames,
                "Event Start Time (frames)": start_time_frames,
            })
        
        time_stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_debug = os.path.join(output_dir, f"{time_stamp}_timecode_debug_log.txt")
        out_csv = os.path.join(output_dir, f"{time_stamp}_super_edl.csv")

        self.log_msg(f"4. Saving TC debug log...");
        with open(out_debug, "w", encoding="utf-8") as f: f.write("\n".join(debug_log_lines))

        self.log_msg(f"5. Saving full report to {os.path.basename(out_csv)}...");
        try:
            with open(out_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Timeline Summary"])
                for key, value in summary_info.items(): writer.writerow([key, value])
                writer.writerow([])
                if enriched:
                    header = enriched[0].keys()
                    writer.writerow(header)
                    for row in enriched: writer.writerow(row.values())
        except Exception as e:
            self.log_msg(f"❌ Error saving CSV file: {e}"); messagebox.showerror("Error", f"Could not save the CSV report:\n{e}")
            return

        self.log_msg("\n--- Timeline Summary ---")
        for key, value in summary_info.items():
            self.log_msg(f"{key}: {value}")
            
        self.log_msg("\n--- Event Details ---")
        if not enriched:
            self.log_msg("No events found to display.")
        else:
            for r in enriched:
                self.log_msg(f"--------------------------------------------------")
                self.log_msg(f"EVENT #{r['Event Number']} | {r['Clip Name']}")
                self.log_msg(f"--------------------------------------------------")
                self.log_msg(f"  Timeline In : {r['Timeline Start TC']}")
                self.log_msg(f"  Length      : {r['Event Length (frames)']} frames")
                self.log_msg(f"  TapeID      : {r.get('TapeID', 'N/A')}")
                self.log_msg(f"  DiskLabel   : {r.get('DiskLabel', 'N/A')}")
                self.log_msg(f"  Source Path : {r.get('Source File Path (URL)', 'N/A')}")
                self.log_msg(f"...")
                self.log_msg(f"  Source Start: {r['Source Clip Start TC']} ({r['Source Clip Start (frames)']}f)")
                self.log_msg(f"     + Offset : {r['Source Offset TC']} ({r['Source Offset (frames)']}f)")
                self.log_msg(f"  = Event In  : {r['Event Start Time (from source)']} ({r['Event Start Time (frames)']}f)")
                self.log_msg("")

        self.log_msg(f"\n✅ Analysis complete. Full detailed report saved to:\n{os.path.basename(out_csv)}")
        messagebox.showinfo("Done", "Super EDL report generated successfully.")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()