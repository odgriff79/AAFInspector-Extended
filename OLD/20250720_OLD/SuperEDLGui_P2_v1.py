import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def frames_to_tc(frame_count, fps=25.0):
    if frame_count is None or fps is None or fps <= 0: return "N/A"
    try:
        fc, int_fps = int(frame_count), round(float(fps))
        if int_fps == 0: return "N/A"
        h, m, s, f = fc//(3600*int_fps), (fc%(3600*int_fps))//(60*int_fps), (fc%(60*int_fps))//int_fps, fc%int_fps
        return f"{h:02}:{m:02}:{s:02}:{f:02}"
    except (ValueError, TypeError): return "N/A"

def create_mob_map(node, mob_map=None):
    if mob_map is None: mob_map = {}
    if isinstance(node, list) and len(node) > 1:
        children = node[3] if len(node) > 3 else []
        if any(isinstance(c, list) and c[0] == "MobID" for c in children):
            mob_id = next((c[2] for c in children if isinstance(c, list) and c[0] == "MobID"), None)
            if mob_id: mob_map[mob_id] = node
        for c in children: create_mob_map(c, mob_map)
    return mob_map

def find_main_sequence_mob_and_start_tc(root_node):
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None, 0
    all_mobs = root_node[3]
    for mob in all_mobs:
        if not (isinstance(mob, list) and len(mob) > 3): continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not (slots_node and len(slots_node) > 3): continue
        is_sequence_mob = any(
            isinstance(s, list) and len(s) > 3 and
            isinstance(next((c for c in s[3] if c[0] == "Segment"), None), list) and
            len(seg_node := next((c for c in s[3] if c[0] == "Segment"), None)) > 3 and
            seg_node[3] and isinstance(seg_node[3][0], list) and seg_node[3][0][0] == "Sequence"
            for s in slots_node[3]
        )
        if is_sequence_mob:
            start_tc = 0
            for s in slots_node[3]:
                 if isinstance(s, list) and len(s) > 3:
                    seg = next((c for c in s[3] if c[0] == "Segment"), None)
                    if seg and len(seg) > 3 and seg[3] and isinstance(seg[3][0], list) and seg[3][0][0] == "Timecode":
                        tc_node = seg[3][0]
                        start_node = next((c for c in tc_node[3] if c[0] == "Start"), None)
                        if start_node and len(start_node) > 2:
                            try: start_tc = int(start_node[2]); break
                            except (ValueError, TypeError): continue
            return mob, start_tc
    return None, 0

def extract_metadata(mob_node):
    metadata = {"URLString": "", "TapeID": "", "DiskLabel": "", "SourceEditRate": None, "GenuineStartFrames": 0}
    if not mob_node: return metadata

    all_starts = []
    def recursive_extract(n):
        if not isinstance(n, list): return
        node_name, children = n[0], n[3] if len(n) > 3 else []
        
        if node_name in ("Start", "StartTime") and len(n) > 2:
            try: all_starts.append(int(n[2]))
            except (ValueError, TypeError): pass
        elif node_name == "URLString" and len(n) > 2: metadata["URLString"] = n[2]
        elif node_name == "EditRate" and len(n) > 2: metadata["SourceEditRate"] = float(n[2])
        elif node_name == "TapeID" and len(n) > 3 and not metadata["TapeID"]:
            metadata["TapeID"] = next((c[2] for c in children if c[0] == "Value"), "")
        elif node_name in ("DiskLabel", "_IMPORTDISKLAB") and len(n) > 3 and not metadata["DiskLabel"]:
            metadata["DiskLabel"] = next((c[2] for c in children if c[0] == "Value"), "")
        elif node_name == "MobAttributeList":
             for attr in children:
                 if isinstance(attr, list) and len(attr) > 3:
                     attr_name = next((c[2] for c in attr[3] if c[0] == "Name"), "")
                     attr_val = next((c[2] for c in attr[3] if c[0] == "Value"), "")
                     if attr_name == "TapeID" and not metadata["TapeID"]: metadata["TapeID"] = attr_val
                     if attr_name == "DiskLabel" and not metadata["DiskLabel"]: metadata["DiskLabel"] = attr_val
        for child in children: recursive_extract(child)

    recursive_extract(mob_node)
    if all_starts:
        metadata["GenuineStartFrames"] = max(all_starts)
    return metadata

def ref_has_url(node):
    """
    Checks if a given node tree contains a URLString.
    """
    if not isinstance(node, list): return False
    if node[0] == 'URLString': return True
    if len(node) > 3:
        for child in node[3]:
            if ref_has_url(child): return True
    return False

def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, dedupe_set=None):
    if results is None: results = []
    if dedupe_set is None: dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2: return results
    
    name, children = node[0], node[3] if len(node) > 3 else []
    
    if name in ["Data Track", "Sound"] or any(isinstance(name, str) and name.startswith(p) for p in ["A1", "A2", "A3", "A4"]):
        return results

    if name == "Sequence":
        components_node = next((c for c in children if c[0] == "Components"), None)
        if components_node and len(components_node) > 3:
            for comp in components_node[3]:
                recursive_search(comp, timeline_offset, edit_rate, results, dedupe_set)
                if isinstance(comp, list) and len(comp) > 3:
                    timeline_offset += next((int(x[2]) for x in comp[3] if x[0] == "Length"), 0)
    elif name == "SourceClip":
        # --- RULE IMPLEMENTED HERE ---
        # Only process this SourceClip if its referenced mob contains a URLString
        source_mob_ref = next((c for c in children if c[0] == "Source Mob Ref"), None)
        if source_mob_ref and ref_has_url(source_mob_ref):
            mobid = next((c[2] for c in children if c[0] == "SourceID"), None)
            offset = next((int(c[2]) for c in children if c[0] == "Start" or c[0] == "StartTime"), 0)
            dedupe_key = (mobid, timeline_offset, offset)
            if mobid and dedupe_key not in dedupe_set:
                dedupe_set.add(dedupe_key)
                results.append({
                    "MobID": mobid,
                    "TimelineStartFrame": timeline_offset, 
                    "SourceOffsetFrames": offset,
                    "Length": next((int(c[2]) for c in children if c[0] == "Length"), 0),
                    "TimelineEditRate": edit_rate
                })
    else:
        for c in children: recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    return results

def get_genuine_source_info(mob_id, mob_map, visited=None):
    if visited is None: visited = set()
    if mob_id in visited: return None
    visited.add(mob_id)

    mob = mob_map.get(mob_id)
    if not mob: return None
    
    next_mob_id = None
    slots_node = next((c for c in mob[3] if c[0] == "Slots"), None)
    if slots_node and len(slots_node) > 3:
        for slot in slots_node[3]:
            if len(slot) > 3:
                segment = next((c for c in slot[3] if c[0] == "Segment"), None)
                if segment and len(segment) > 3 and segment[3] and isinstance(segment[3][0], list) and segment[3][0][0] == "SourceClip":
                    next_mob_id = next((c[2] for c in segment[3][0][3] if c[0] == "SourceID"), None)
                    break
    
    if next_mob_id:
        final_mob = get_genuine_source_info(next_mob_id, mob_map, visited)
        if final_mob: return final_mob
    
    return mob

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

    def log_msg(self, msg): self.log.insert(tk.END, msg + "\n"); self.log.see(tk.END)
    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            self.json_path = path
            try:
                with open(path, "r", encoding="utf-8") as f: self.json_data = json.load(f)
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
            messagebox.showerror("Error", "Please load a file first."); return
        
        output_dir = filedialog.askdirectory(title="Select Output Directory")
        if not output_dir: self.log_msg("❌ Report generation cancelled."); return
            
        self.log.delete(1.0, tk.END)
        self.log_msg("1. Building Mob map..."); mob_map = create_mob_map(self.json_data)
        self.log_msg("2. Finding main sequence..."); sequence_mob, start_tc = find_main_sequence_mob_and_start_tc(self.json_data)
        if not sequence_mob: self.log_msg("❌ Could not find main sequence."); return
            
        timeline_rate = 25.0
        slots = next((c for c in sequence_mob[3] if c[0] == "Slots"), None)
        if slots and len(slots) > 3:
            for slot in slots[3]:
                if isinstance(slot, list) and len(slot) > 3:
                    rate_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "EditRate"), None)
                    if rate_node and len(rate_node) > 2:
                        try:
                            timeline_rate = float(rate_node[2])
                            break 
                        except (ValueError, TypeError): continue

        self.log_msg("3. Extracting timeline events..."); 
        events = recursive_search(sequence_mob, timeline_offset=start_tc, edit_rate=timeline_rate)
        
        enriched = []
        for idx, e in enumerate(events, 1):
            initial_mob = mob_map.get(e["MobID"])
            final_source_mob = get_genuine_source_info(e["MobID"], mob_map)
            
            master_md = extract_metadata(initial_mob)
            final_md = extract_metadata(final_source_mob)
            
            md = final_md.copy()
            md["TapeID"] = master_md.get("TapeID") or final_md.get("TapeID")
            md["DiskLabel"] = master_md.get("DiskLabel") or final_md.get("DiskLabel")

            source_file_name = "N/A"
            if final_source_mob:
                path_url = final_md.get("URLString", "")
                if path_url:
                    try: source_file_name = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(path_url).path))
                    except: source_file_name = "Path Error"
            
            enriched.append({
                "Event": idx, "Source File Name": source_file_name,
                "TapeID": md.get("TapeID", "N/A"),
                "Timeline TC": frames_to_tc(e["TimelineStartFrame"], e["TimelineEditRate"]),
                "Source In TC": frames_to_tc(md["GenuineStartFrames"] + e["SourceOffsetFrames"], e["TimelineEditRate"]),
                "Length": e["Length"],
            })
            
        # GUI Output
        self.log_msg("\n--- Event Details ---")
        for r in enriched:
            self.log_msg(f"----------------------------------------")
            self.log_msg(f"EVENT #{r['Event']} | {r['Source File Name']}")
            self.log_msg(f"  Timeline In : {r['Timeline TC']}")
            self.log_msg(f"  Source In   : {r['Source In TC']}")
            self.log_msg(f"  Length      : {r['Length']} frames")
            self.log_msg(f"  TapeID      : {r.get('TapeID', 'N/A')}")
            self.log_msg("")

        # CSV Output
        out_path = os.path.join(output_dir, f"super_edl_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        self.log_msg(f"✅ Analysis complete. Full report in:\n{out_path}")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            if enriched:
                writer = csv.DictWriter(f, fieldnames=enriched[0].keys())
                writer.writeheader()
                writer.writerows(enriched)
        messagebox.showinfo("Done", "Report generated successfully.")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()