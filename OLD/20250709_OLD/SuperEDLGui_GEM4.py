# SuperEDLGui_GEM5.py
import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode and Parsing Functions ---

def frames_to_tc(frame_count, fps=25.0):
    if frame_count is None or fps is None or fps == 0: return "N/A"
    try:
        frame_count, fps, int_fps = int(frame_count), float(fps), round(float(fps))
        h, m, s, f = frame_count // (3600 * int_fps), (frame_count % (3600 * int_fps)) // (60 * int_fps), (frame_count % (60 * int_fps)) // int_fps, frame_count % int_fps
        return f"{h:02}:{m:02}:{s:02}:{f:02}"
    except: return "N/A"

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
    all_mobs = root_node[3]
    for mob in all_mobs:
        if not isinstance(mob, list) or len(mob) < 4: continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not slots_node or len(slots_node) < 4: continue
        for slot in slots_node[3]:
            segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
            if segment_node and segment_node[3] and segment_node[3][0][0] == "Sequence":
                timecode_node = segment_node[3][0]
                if timecode_node[0] == "Timecode":
                    start = next((c[2] for c in timecode_node[3] if c[0] == "Start"), None)
                    try: return mob, int(start)
                    except: return mob, 0
    return None, 0

def extract_metadata(mob_node):
    metadata = {"SourceClipStartFrames": 0, "URLString": ""}
    if not mob_node or not isinstance(mob_node, list): return metadata

    def find_url(n):
        if not isinstance(n, list): return
        if n[0] == "URLString" and len(n) > 2:
            metadata["URLString"] = n[2]; return True
        for c in n[3] if len(n) > 3 else []:
            if find_url(c): return True
        return False
    find_url(mob_node)

    all_starts = []
    def find_all_start_values(n):
        if not isinstance(n, list): return
        if n[0] in ("Start", "StartTime") and len(n) > 2:
            try:
                frames = int(n[2])
                if frames not in all_starts: all_starts.append(frames)
            except: pass
        for child in n[3] if len(n) > 3 else []: find_all_start_values(child)
    find_all_start_values(mob_node)

    if all_starts:
        metadata["SourceClipStartFrames"] = max(all_starts)
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
        source_offset_frames = next((int(c[2]) for c in children if isinstance(c, list) and c[0] in ("Start", "StartTime")), 0)
        length = next((int(x[2]) for x in children if isinstance(x, list) and x[0] == "Length"), 0)
        dedupe_key = (mobid, timeline_offset, source_offset_frames)
        if mobid and dedupe_key not in dedupe_set:
            dedupe_set.add(dedupe_key)
            results.append({
                "MobID": mobid, "TimelineStartFrame": timeline_offset, "SourceOffsetFrames": source_offset_frames,
                "Length": length, "EditRate": edit_rate
            })
    else:
        for c in children: recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    return results

def get_clip_name(mob_map, mobid):
    source_mob = mob_map.get(mobid)
    if not source_mob: return "Unknown"
    md = extract_metadata(source_mob)
    url = md.get("URLString", "")
    if url: return os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path))
    return next((c[2] for c in source_mob[3] if isinstance(c, list) and c[0] == "Name"), "Unknown")

# --- GUI App ---

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
            source_clip_start_frames = md.get("SourceClipStartFrames", 0)
            source_offset_frames = e["SourceOffsetFrames"]
            source_clip_event_in_frames = source_clip_start_frames + source_offset_frames
            enriched.append({
                "Event": idx,
                "SourceFile": get_clip_name(mob_map, e["MobID"]),
                "TimelineInTC": frames_to_tc(e["TimelineStartFrame"], fps),
                "SourceClipStartTC": frames_to_tc(source_clip_start_frames, fps),
                "SourceOffsetTC": frames_to_tc(source_offset_frames, fps),
                "SourceClipEventInTC": frames_to_tc(source_clip_event_in_frames, fps)
            })

        out_csv = os.path.join(os.path.dirname(self.json_path), f"super_edl_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        self.log_msg(f"4. Saving report to {out_csv}...")
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(enriched[0].keys())); writer.writeheader(); writer.writerows(enriched)

        self.log_msg("\n--- Super EDL Report ---")
        header = f"{'#':<4} {'Timeline In':<15} {'Source Clip Start':<20} {'Offset':<15} {'Event Source In':<20} {'Source File'}"
        self.log_msg(header); self.log_msg("-" * (len(header) + 5))
        for r in enriched:
            line = f"{r['Event']:<4} {r['TimelineInTC']:<15} {r['SourceClipStartTC']:<20} {r['SourceOffsetTC']:<15} {r['SourceClipEventInTC']:<20} {r['SourceFile']}"
            self.log_msg(line)

        self.log_msg("\n✅ Analysis complete. Full details saved to CSV.")
        messagebox.showinfo("Done", "Super EDL report generated successfully.")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
