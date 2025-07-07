import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode and Parsing Functions ---

def frames_to_tc(frame_count, fps):
    if frame_count is None or fps is None or fps == 0: return "N/A"
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

def extract_metadata(mob_node):
    metadata = {"AvidBinStartFrames": None, "TrueSourceStartFrames": None, "URLString": ""}
    if not mob_node or not isinstance(mob_node, list) or len(mob_node) < 4: return metadata
    def find_url(n):
        if not isinstance(n, list): return
        if n[0] == "URLString" and len(n) > 2: metadata["URLString"] = n[2]; return True
        children = n[3] if len(n) > 3 else []
        for c in children:
            if find_url(c): return True
        return False
    find_url(mob_node)
    slots_node = next((c for c in mob_node[3] if isinstance(c, list) and c[0] == "Slots"), None)
    if slots_node and len(slots_node) >= 4:
        for slot in slots_node[3]:
            if not isinstance(slot, list) or len(slot) < 4: continue
            segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
            if not segment_node or len(segment_node) < 4: continue
            list_of_segments = segment_node[3]
            if isinstance(list_of_segments, list):
                for seg in list_of_segments:
                    if not isinstance(seg, list): continue
                    if seg[0] == "SourceClip":
                        children = seg[3] if len(seg) > 3 else []
                        start_time = next((c[2] for c in children if isinstance(c, list) and c[0] == "StartTime"), None)
                        if start_time is not None:
                            try: metadata["AvidBinStartFrames"] = int(start_time)
                            except (ValueError, TypeError): pass
                    elif seg[0] == "Timecode":
                        children = seg[3] if len(seg) > 3 else []
                        start_time = next((c[2] for c in children if isinstance(c, list) and c[0] == "Start"), None)
                        if start_time is not None:
                            try: metadata["TrueSourceStartFrames"] = int(start_time)
                            except (ValueError, TypeError): pass
    return metadata

def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, dedupe_set=None):
    if results is None: results = []
    if dedupe_set is None: dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2: return results
    name, children = node[0], node[3] if len(node) > 3 else []
    rate_node = next((c for c in children if isinstance(c, list) and c[0] in ("EditRate", "FPS")), None)
    if rate_node:
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
        source_start = next((int(c[2]) for c in children if isinstance(c, list) and c[0] in ("Start", "StartTime")), 0)
        length = next((int(x[2]) for x in children if isinstance(x, list) and x[0] == "Length"), 0)
        dedupe_key = (mobid, timeline_offset, source_start)
        if mobid and dedupe_key not in dedupe_set:
            dedupe_set.add(dedupe_key)
            results.append({
                "MobID": mobid, "TimelineStartFrame": timeline_offset, "SourceOffsetFrames": source_start,
                "Length": length, "EditRate": edit_rate
            })
    else:
        for c in children: recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    return results

def get_clip_name_from_mob_id(mob_map, mobid):
    mob_node = mob_map.get(mobid)
    if not mob_node or len(mob_node) < 4: return "Unknown"
    return next((c[2] for c in mob_node[3] if isinstance(c, list) and c[0] == "Name"), "Unknown")

class App:
    def __init__(self, root):
        self.root, self.json_path, self.json_data = root, None, None
        self.root.title("Super EDL Generator")
        self.root.geometry("1200x800")
        tk.Button(root, text="Load AAF Export (JSON) File", command=self.load_json).pack(pady=10)
        tk.Button(root, text="Generate Super EDL", command=self.process).pack(pady=5)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg): self.log.insert(tk.END, msg + "\n"); self.log.see(tk.END)
    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            self.json_data, self.json_path = json.load(open(path, "r", encoding="utf-8")), path
            self.log.delete(1.0, tk.END); self.log_msg(f"✅ Loaded JSON file:\n{path}")

    def process(self):
        if not self.json_data: messagebox.showerror("Error", "Please load the AAF file first."); return
        self.log.delete(1.0, tk.END)
        self.log_msg("1. Building Mob map..."); mob_map = create_mob_map(self.json_data)
        self.log_msg(f"   - Mobs indexed: {len(mob_map)}")
        self.log_msg("2. Finding main sequence..."); sequence_mob, start_tc = find_main_sequence_mob_and_start_tc(self.json_data)
        if not sequence_mob: self.log_msg("❌ Could not find a main sequence mob."); return
        self.log_msg(f"   - Sequence found. Master start TC: {start_tc} frames.")
        self.log_msg("3. Extracting timeline events..."); events = recursive_search(sequence_mob, timeline_offset=start_tc)
        self.log_msg(f"   - Events found: {len(events)}")
        if not events: self.log_msg("❌ No clip events were found."); return

        enriched = []
        for idx, e in enumerate(events, 1):
            fps = e["EditRate"]
            source_mob = mob_map.get(e["MobID"])
            md = extract_metadata(source_mob) if source_mob else {}
            
            # --- CORRECTED LOGIC ---
            # Prioritize Avid Bin TC, fallback to True Source TC, then fallback to 0
            base_start_frames = md.get("AvidBinStartFrames")
            if base_start_frames is None:
                base_start_frames = md.get("TrueSourceStartFrames")
            if base_start_frames is None: # Final check to prevent crash
                base_start_frames = 0
            # --- END CORRECTION ---

            source_offset_frames = e["SourceOffsetFrames"]
            event_source_in_frames = base_start_frames + source_offset_frames
            url = md.get("URLString", "")
            source_file = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path)) if url else get_clip_name_from_mob_id(mob_map, e["MobID"])

            enriched.append({
                "Event": idx, "SourceFile": source_file, "SourcePath": url,
                "TimelineInTC": frames_to_tc(e["TimelineStartFrame"], fps),
                "EDLSourceInTC": frames_to_tc(event_source_in_frames, fps),
                "SourceOffsetTC": frames_to_tc(source_offset_frames, fps),
                "AvidBinStartTC": frames_to_tc(md.get("AvidBinStartFrames"), fps),
                "TrueSourceStartTC": frames_to_tc(md.get("TrueSourceStartFrames"), fps),
                "Length": e["Length"]
            })

        out_csv = os.path.join(os.path.dirname(self.json_path), f"super_edl_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        self.log_msg(f"4. Saving report to {out_csv}...");
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(enriched[0].keys())); writer.writeheader(); writer.writerows(enriched)
        
        self.log_msg("\n--- Super EDL Report ---")
        header = f"{'#':<4} {'Timeline In':<15} {'Source In (EDL)':<20} {'Source File'}"
        self.log_msg(header); self.log_msg("-" * (len(header) + 20))
        for r in enriched:
            line = f"{r['Event']:<4} {r['TimelineInTC']:<15} {r['EDLSourceInTC']:<20} {r['SourceFile']}"
            self.log_msg(line)
        self.log_msg("\n✅ Analysis complete. Full details saved to CSV.")
        messagebox.showinfo("Done", "Super EDL report generated successfully.")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()