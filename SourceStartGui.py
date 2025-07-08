import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox

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
    """Finds all unique SourceClips within a sequence."""
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

def find_all_start_values(node, start_values=None):
    """Recursively finds all properties named 'Start' or 'StartTime'."""
    if start_values is None: start_values = []
    if not isinstance(node, list): return start_values

    # Check the current node itself for a "Start" or "StartTime" property
    if node[0] in ("Start", "StartTime") and len(node) > 2:
        try:
            frames = int(node[2])
            if frames not in [val['Frames'] for val in start_values]: # Avoid duplicates
                start_values.append({"Frames": frames, "TC": frames_to_tc(frames)})
        except (ValueError, TypeError):
            pass # Ignore if value is not a valid integer

    # Recurse through all children
    children = node[3] if len(node) > 3 else []
    for child in children:
        find_all_start_values(child, start_values)

    return start_values

def get_clip_name_from_mob_id(mob_node):
    """Finds the 'Name' property of a Mob."""
    if not mob_node or len(mob_node) < 4: return "Unknown"
    return next((c[2] for c in mob_node[3] if isinstance(c, list) and c[0] == "Name"), "Unknown")


class App:
    def __init__(self, root):
        self.root, self.json_path, self.json_data = root, None, None
        self.root.title("AAF Timecode Scanner")
        self.root.geometry("1200x800")
        tk.Button(root, text="Load AAF Export (JSON) File", command=self.load_json).pack(pady=10)
        tk.Button(root, text="Scan for Start Timecodes", command=self.process).pack(pady=5)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg, indent=0):
        self.log.insert(tk.END, " " * indent + msg + "\n"); self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            self.json_data, self.json_path = json.load(open(path, "r", encoding="utf-8")), path
            self.log.delete(1.0, tk.END); self.log_msg(f"✅ Loaded JSON file:\n{path}")

    def process(self):
        if not self.json_data:
            messagebox.showerror("Error", "Please load the AAF file first.")
            return
        self.log.delete(1.0, tk.END)

        self.log_msg("1. Building Mob map...")
        mob_map = create_mob_map(self.json_data)
        self.log_msg(f"   - Mobs indexed: {len(mob_map)}")

        self.log_msg("\n2. Finding unique source clips in sequences...")
        sequence_clips = find_sequence_clips(self.json_data)
        self.log_msg(f"   - Found {len(sequence_clips)} unique clips used in sequences.")
        if not sequence_clips:
            self.log_msg("❌ No clips were found in any sequence."); return

        self.log_msg("\n3. Scanning each source clip for 'Start' timecodes...")
        self.log_msg("-" * 50)

        for clip in sequence_clips:
            mob_id = clip["MobID"]
            mob_node = mob_map.get(mob_id)
            clip_name = get_clip_name_from_mob_id(mob_node) if mob_node else "Unknown Mob"
            
            self.log_msg(f"CLIP: {clip_name}")
            
            if mob_node:
                start_values = find_all_start_values(mob_node)
                if start_values:
                    for val in start_values:
                        self.log_msg(f"  - Found 'Start' value: {val['Frames']} frames  ->  {val['TC']}", indent=2)
                else:
                    self.log_msg("  - No 'Start' or 'StartTime' properties found in this mob's definition.", indent=2)
            else:
                self.log_msg(f"  - ERROR: Could not find Mob definition for MobID: {mob_id}", indent=2)
            
            self.log_msg("-" * 50)
            
        self.log_msg("\n✅ Scan complete.")
        messagebox.showinfo("Done", "Finished scanning for timecodes.")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()