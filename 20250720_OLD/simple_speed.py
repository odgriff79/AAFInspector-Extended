import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox

def find_speed_effects(node, results=None):
    """
    Recursively searches a JSON structure for SourceClips and any
    enclosing OperationGroup with a SpeedRatio.
    """
    if results is None:
        results = []

    if not isinstance(node, list) or len(node) < 2:
        return results

    node_name, children = node[0], node[3] if len(node) > 3 else []

    # If this is an OperationGroup, check for a SpeedRatio
    if node_name == 'OperationGroup':
        speed_ratio = 1.0  # Default if not found
        params_node = next((c for c in children if isinstance(c, list) and c[0] == 'Parameters'), None)
        if params_node:
            ratio_node = next((p for p in params_node[3] if isinstance(p, list) and p[0] == 'SpeedRatio'), None)
            if ratio_node and len(ratio_node) > 2:
                try:
                    speed_ratio = float(ratio_node[2])
                except (ValueError, TypeError):
                    speed_ratio = 1.0
        
        # Now find the SourceClip inside this group
        input_segments = next((c for c in children if isinstance(c, list) and c[0] == 'InputSegments'), None)
        if input_segments and len(input_segments) > 3:
            for segment in input_segments[3]:
                # Pass the found speed_ratio down to the search
                find_speed_effects(segment, results)
                # Apply the found speed to the last added clip(s)
                for res in results:
                    if "speed" not in res: # Apply only once
                         res["speed"] = speed_ratio
        return results

    # If it's a Sequence, traverse its components
    elif node_name == 'Sequence':
        components_node = next((c for c in children if isinstance(c, list) and c[0] == 'Components'), None)
        if components_node and len(components_node) > 3:
            for comp in components_node[3]:
                find_speed_effects(comp, results)
        return results

    # If it's a SourceClip, this is our event
    elif node_name == 'SourceClip':
        clip_name = "Unknown Clip"
        mob_ref = next((c for c in children if isinstance(c, list) and c[0] == "Source Mob Ref"), None)
        if mob_ref and len(mob_ref) > 3:
             # Get name from the first class definition inside the ref
             class_def = mob_ref[3][0]
             if class_def and len(class_def) > 1:
                 clip_name = class_def[0]

        event_data = {
            "name": clip_name,
            "speed": 1.0 # Default speed, will be overwritten if found in an OperationGroup
        }
        results.append(event_data)
        return results

    # For other node types, just recurse
    for child in children:
        find_speed_effects(child, results)

    return results


class SpeedCheckApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Speed Effect Checker")
        self.root.geometry("800x600")
        
        tk.Button(root, text="Load AAF as JSON File", command=self.load_and_process).pack(pady=10)
        
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_and_process(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if not path:
            return

        self.log.delete(1.0, tk.END)
        self.log_msg(f"✅ Loaded JSON file: {os.path.basename(path)}\n")

        try:
            with open(path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load or parse JSON file:\n{e}")
            return
            
        self.log_msg("--- Speed Effect Report ---")
        
        # We assume the main sequence is the first major component
        main_sequence = json_data[3][0] 
        
        events = find_speed_effects(main_sequence)

        if not events:
            self.log_msg("No events found.")
            return

        for idx, event in enumerate(events, 1):
            speed_percent = event.get("speed", 1.0) * 100
            self.log_msg(f"Event #{idx}: {event['name']}")
            if speed_percent != 100.0:
                self.log_msg(f"  └── 🚀 Speed Effect Detected: {speed_percent:.0f}%")
            else:
                 self.log_msg(f"  └── Normal Speed (100%)")
            self.log_msg("-" * 30)
            
        messagebox.showinfo("Done", f"Analysis complete. Found {len(events)} events.")

if __name__ == "__main__":
    root = tk.Tk()
    app = SpeedCheckApp(root)
    root.mainloop()