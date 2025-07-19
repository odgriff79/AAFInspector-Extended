import os
import json
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode and Parsing Functions ---
def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    """
    Converts a frame count to a timecode string.
    """
    if frame_count is None or fps is None or fps <= 0: return "N/A"
    try:
        separator = ";" if is_drop_frame else ":"
        fc, int_fps = int(frame_count), round(float(fps))
        if int_fps == 0: return "N/A"
        h, m, s, f = fc//(3600*int_fps), (fc%(3600*int_fps))//(60*int_fps), (fc%(60*int_fps))//int_fps, fc%int_fps
        return f"{h:02}:{m:02}:{s:02}{separator}{f:02}"
    except (ValueError, TypeError): return "N/A"

def find_main_sequence_mob_and_start_tc(root_node):
    """
    Finds the main sequence Mob in the AAF data and its starting timecode info.
    """
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None, 0, False
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
            start_tc, is_drop = 0, False
            for s in slots_node[3]:
                 if isinstance(s, list) and len(s) > 3:
                    seg = next((c for c in s[3] if c[0] == "Segment"), None)
                    if seg and len(seg) > 3 and seg[3] and isinstance(seg[3][0], list) and seg[3][0][0] == "Timecode":
                        tc_node = seg[3][0]
                        start_node = next((c for c in tc_node[3] if c[0] == "Start"), None)
                        drop_node = next((c for c in tc_node[3] if c[0] == "Drop"), None)
                        if drop_node and len(drop_node) > 2:
                            is_drop = bool(drop_node[2])
                        if start_node and len(start_node) > 2:
                            try: start_tc = int(start_node[2]); break
                            except (ValueError, TypeError): continue
            return mob, start_tc, is_drop
    return None, 0, False

def find_filler_effects(node, timeline_offset=0, results=None):
    """
    Recursively searches the AAF structure for OperationGroups that act on Filler,
    treating them as distinct events and tracking their timeline position.
    """
    if results is None: results = []
    if not isinstance(node, list) or len(node) < 2: return results

    name, children = node[0], node[3] if len(node) > 3 else []

    if name == "Sequence":
        components_node = next((c for c in children if c[0] == "Components"), None)
        if components_node and len(components_node) > 3:
            for comp in components_node[3]:
                find_filler_effects(comp, timeline_offset, results)
                if isinstance(comp, list) and len(comp) > 3:
                    timeline_offset += next((int(x[2]) for x in comp[3] if x[0] == "Length"), 0)

    elif name == "OperationGroup":
        # Check if the input to this effect is a Filler object
        segments = next((c for c in children if c[0] in ("Segments", "Components")), None)
        is_on_filler = (
            segments and len(segments) > 3 and segments[3] and
            isinstance(segments[3][0], list) and segments[3][0][0] == "Filler"
        )
        
        if is_on_filler:
            # This is an effect on filler, which is what we want to find.
            results.append({
                "node": node,
                "timeline_start_frame": timeline_offset
            })
        else:
            # It's an effect on something else (e.g., a SourceClip), so just keep searching inside.
            for child in children:
                find_filler_effects(child, timeline_offset, results)
    
    elif name not in ["SourceClip", "Filler"]:
        for child in children:
            find_filler_effects(child, timeline_offset, results)
    
    return results

def extract_keyframe_data(operation_group_node):
    """
    Extracts effect name, length, and detailed keyframe information
    from an OperationGroup node.
    """
    effect_info = {
        "Effect Name": "Unknown Effect",
        "Length": 0,
        "Parameters": []
    }

    if not operation_group_node or operation_group_node[0] != "OperationGroup":
        return effect_info

    children = operation_group_node[3] if len(operation_group_node) > 3 else []

    # Extract Effect Name from Definition
    definition_id = next((c[2] for c in children if c[0] == "Definition"), None)
    if definition_id:
        # This would ideally look up the definition, but for now we can label it
        effect_info["Effect Name"] = f"Effect ({definition_id.split('-')[-1]})"

    # Extract Length
    effect_info["Length"] = next((int(c[2]) for c in children if c[0] == "Length"), 0)

    # Find Parameters
    params_node = next((c for c in children if c[0] == "Parameters"), None)
    if not params_node or len(params_node) < 4:
        return effect_info

    for param in params_node[3]:
        if not (isinstance(param, list) and len(param) > 3): continue
        
        param_children = param[3]
        param_def_name = next((c[2] for c in param_children if c[0] == "Name"), "Unnamed Parameter")
        
        # Check for VaryingValue, which indicates animation
        varying_val_node = next((c for c in param_children if c[0] == "VaryingValue"), None)
        if not varying_val_node or len(varying_val_node) < 4: continue

        keyframes = []
        # Find ControlPoints (Keyframes)
        cp_list_node = next((c for c in varying_val_node[3] if c[0] == "PointList"), None)
        if cp_list_node and len(cp_list_node) > 3:
            for cp_node in cp_list_node[3]:
                if cp_node[0] == "ControlPoint":
                    time_val = next((c[2] for c in cp_node[3] if c[0] == "Time"), "N/A")
                    value_val = next((c[2] for c in cp_node[3] if c[0] == "Value"), "N/A")
                    keyframes.append({"Time": time_val, "Value": value_val})
        
        if keyframes:
            effect_info["Parameters"].append({
                "Name": param_def_name,
                "Keyframes": keyframes
            })

    return effect_info


# --- GUI Class ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Effects Extractor GUI")
        self.root.geometry("1000x700")
        
        tk.Button(root, text="Load AAF Export (JSON) File", command=self.load_json).pack(pady=10)
        self.filename_label = tk.Label(root, text="No file loaded.", fg="grey")
        self.filename_label.pack(pady=2)
        
        self.process_button = tk.Button(root, text="Find Pan & Zoom Effects on Filler", command=self.process, state=tk.DISABLED)
        self.process_button.pack(pady=5)
        
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg, indent=0):
        self.log.insert(tk.END, "  " * indent + msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            self.json_path = path
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.json_data = json.load(f)
                self.log.delete(1.0, tk.END)
                self.log_msg(f"✅ Loaded JSON file: {os.path.basename(path)}")
                self.filename_label.config(text=os.path.basename(path), fg="black")
                self.process_button.config(state=tk.NORMAL)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load or parse JSON file:\n{e}")
                self.filename_label.config(text="Failed to load file.", fg="red")
                self.process_button.config(state=tk.DISABLED)

    def process(self):
        if not hasattr(self, 'json_data'):
            messagebox.showerror("Error", "Please load a file first.")
            return

        self.log.delete(1.0, tk.END)
        self.log_msg("1. Finding main sequence...")
        
        sequence_mob, start_tc, is_drop_frame = find_main_sequence_mob_and_start_tc(self.json_data)
        if not sequence_mob:
            self.log_msg("❌ Could not find a main sequence Mob in the file.")
            return
        
        self.log_msg(f"✅ Found sequence: {next((c[2] for c in sequence_mob[3] if c[0] == 'Name'), 'N/A')}")
        self.log_msg("2. Finding all effects on 'Filler'...")
        
        filler_effects = find_filler_effects(sequence_mob, timeline_offset=start_tc)
        
        if not filler_effects:
            self.log_msg("\nNo 'Pan & Zoom' effects on Filler found on the main timeline track.")
            return
            
        self.log_msg(f"\nFound {len(filler_effects)} effects on Filler. Details:")
        
        for i, effect in enumerate(filler_effects, 1):
            op_group_node = effect["node"]
            start_frame = effect["timeline_start_frame"]
            
            effect_info = extract_keyframe_data(op_group_node)
            
            self.log_msg("------------------------------------------", 1)
            self.log_msg(f"Event #{i}: {effect_info['Effect Name']}", 1)
            self.log_msg(f"Timeline Start: {frames_to_tc(start_frame)} ({start_frame} frames)", 1)
            self.log_msg(f"Length: {effect_info['Length']} frames", 1)
            
            if effect_info["Parameters"]:
                self.log_msg("Animated Parameters:", 2)
                for param in effect_info["Parameters"]:
                    self.log_msg(f"Parameter: {param['Name']}", 3)
                    for kf in param["Keyframes"]:
                        self.log_msg(f"Time: {kf['Time']}, Value: {kf['Value']}", 4)
            else:
                self.log_msg("No keyframe animation found in this effect.", 2)
        
        messagebox.showinfo("Done", f"Processing complete. Found {len(filler_effects)} effects on filler.")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()