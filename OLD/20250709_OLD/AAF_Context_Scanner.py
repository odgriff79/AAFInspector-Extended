import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode and Parsing Functions ---

def frames_to_tc(frame_count, fps=25.0):
    """Converts a frame count to HH:MM:SS:FF timecode string."""
    if frame_count is None or fps is None or fps == 0:
        return "N/A"
    try:
        frame_count, fps, int_fps = int(frame_count), float(fps), round(float(fps))
        if int_fps == 0: return "N/A"
        h, m, s, f = frame_count // (3600 * int_fps), (frame_count % (3600 * int_fps)) // (60 * int_fps), (frame_count % (60 * int_fps)) // int_fps, frame_count % int_fps
        return f"{h:02}:{m:02}:{s:02}:{f:02}"
    except (ValueError, TypeError):
        return "N/A"

def create_mob_map(node, mob_map=None):
    """Recursively builds a dictionary mapping MobIDs to their corresponding nodes."""
    if mob_map is None: mob_map = {}
    if not isinstance(node, list) or len(node) < 2: return mob_map
    children = node[3] if len(node) > 3 else []
    if any(isinstance(c, list) and c[0] == "MobID" for c in children):
        mobid = next((c[2] for c in children if isinstance(c, list) and c[0] == "MobID"), None)
        if mobid: mob_map[mobid] = node
    for c in children: create_mob_map(c, mob_map)
    return mob_map

def find_sequence_clips(node, results=None, dedupe_set=None):
    """Finds all unique SourceClips within a sequence to identify which mobs to scan."""
    if results is None: results = []
    if dedupe_set is None: dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2: return results

    name = node[0]
    children = node[3] if len(node) > 3 else []

    if name == "Sequence":
        components_node = next((c for c in children if isinstance(c, list) and c[0] == "Components"), None)
        if components_node and len(components_node) > 3:
            for component in components_node[3]:
                find_sequence_clips(component, results, dedupe_set)
    elif name == "SourceClip":
        mobid = next((c[2] for c in children if isinstance(c, list) and c[0] == "SourceID"), None)
        if mobid and mobid not in dedupe_set:
            dedupe_set.add(mobid)
            results.append({"MobID": mobid})
    else:
        for child in children:
            find_sequence_clips(child, results, dedupe_set)
    return results

def find_start_candidates(node, candidates=None):
    """
    Recursively finds all properties named 'Start' or 'StartTime' and captures
    their value and their immediate parent object as context.
    """
    if candidates is None: candidates = []
    if not isinstance(node, list): return candidates

    # Check children of the current node to see if any are a 'Start' property
    children = node[3] if len(node) > 3 else []
    found_start_in_this_level = False
    for i, child in enumerate(children):
        if isinstance(child, list) and child[0] in ("Start", "StartTime") and len(child) > 2:
            found_start_in_this_level = True
            try:
                frames = int(child[2])
                # Capture the parent object (the list of properties) as context
                context_obj = {
                    "Parent_Object_Type": node[0],
                    "Properties": children
                }
                candidates.append({
                    "Frames": frames,
                    "TC": frames_to_tc(frames),
                    "Context": json.dumps(context_obj, indent=2)
                })
            except (ValueError, TypeError):
                pass # Ignore if value is not a valid integer

    # Recurse through all children, but only if we didn't find a start time at this level
    # to avoid capturing both a parent and its child that both contain 'start'
    if not found_start_in_this_level:
        for child in children:
            find_start_candidates(child, candidates)

    return candidates

def get_clip_name_from_mob_id(mob_node):
    """Finds the 'Name' property of a Mob."""
    if not mob_node or len(mob_node) < 4: return "Unknown"
    return next((c[2] for c in mob_node[3] if isinstance(c, list) and c[0] == "Name"), "Unknown")


class App:
    def __init__(self, root):
        self.root, self.json_path, self.json_data = root, None, None
        self.root.title("AAF Start Timecode Scanner")
        self.root.geometry("1200x800")
        tk.Button(root, text="Load JSON and Scan for Candidates", command=self.process).pack(pady=10)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg, indent=0):
        self.log.insert(tk.END, " " * indent + msg + "\n"); self.log.see(tk.END)

    def process(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if not path: return
        
        with open(path, "r", encoding="utf-8") as f:
            self.json_data = json.load(f)
        self.json_path = path
        self.log.delete(1.0, tk.END)
        self.log_msg(f"✅ Loaded JSON file:\n{path}")

        self.log_msg("\n1. Building Mob map...")
        mob_map = create_mob_map(self.json_data)
        self.log_msg(f"   - Mobs indexed: {len(mob_map)}")

        self.log_msg("\n2. Finding unique source clips in sequences...")
        sequence_clips = find_sequence_clips(self.json_data)
        self.log_msg(f"   - Found {len(sequence_clips)} unique clips used in sequences.")
        if not sequence_clips:
            self.log_msg("❌ No clips were found in any sequence."); return

        self.log_msg("\n3. Scanning each source clip for all 'Start' timecode candidates...")
        
        full_report = []
        for clip in sequence_clips:
            mob_id = clip["MobID"]
            mob_node = mob_map.get(mob_id)
            clip_name = get_clip_name_from_mob_id(mob_node) if mob_node else "Unknown Mob"
            
            report_entry = f"CLIP: {clip_name}\n"
            
            if mob_node:
                candidates = find_start_candidates(mob_node)
                if candidates:
                    seen_contexts = set()
                    for cand in candidates:
                        if cand["Context"] not in seen_contexts:
                            report_entry += f"  - Found Candidate Value: {cand['Frames']:<10} frames  ->  {cand['TC']}\n"
                            report_entry += f"    Context Object Type: {json.loads(cand['Context'])['Parent_Object_Type']}\n"
                            report_entry += f"    Context Properties:\n{json.dumps(json.loads(cand['Context'])['Properties'], indent=4)}\n"
                            report_entry += "    ---------------------------------\n"
                            seen_contexts.add(cand["Context"])
                else:
                    report_entry += "  - No 'Start' or 'StartTime' properties found.\n"
            else:
                report_entry += f"  - ERROR: Could not find Mob definition for MobID: {mob_id}\n"
            
            report_entry += "="*60 + "\n"
            full_report.append(report_entry)
            self.log_msg(report_entry)

        # Save the full report to a text file
        out_path = os.path.join(os.path.dirname(self.json_path), f"timecode_scan_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            for entry in full_report:
                f.write(entry)

        self.log_msg(f"\n✅ Scan complete. Full diagnostic report saved to:\n{out_path}")
        messagebox.showinfo("Done", f"Finished scanning. Report saved to:\n{out_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
