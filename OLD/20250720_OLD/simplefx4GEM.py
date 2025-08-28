import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def find_animated_dve_params(node, effects_data=None):
    """
    Recursively finds all DVE parameters and extracts their keyframe data
    (ControlPoints) if they are animated.
    """
    if effects_data is None:
        effects_data = {}

    if not isinstance(node, list) or len(node) < 1:
        return effects_data

    node_name = node[0]
    
    # Check if the node is a DVE parameter
    if isinstance(node_name, str) and node_name.startswith("DVE_"):
        point_list_node = next((c for c in node[3] if isinstance(c, list) and c[0] == "PointList"), None)
        
        keyframes = []
        if point_list_node and len(point_list_node) > 3:
            for control_point in point_list_node[3]:
                if isinstance(control_point, list) and control_point[0] == "ControlPoint" and len(control_point) > 3:
                    time_node = next((p for p in control_point[3] if isinstance(p, list) and p[0] == "Time"), None)
                    value_node = next((p for p in control_point[3] if isinstance(p, list) and p[0] == "Value"), None)
                    
                    time = time_node[2] if time_node and len(time_node) > 2 else "N/A"
                    value = value_node[2] if value_node and len(value_node) > 2 else "N/A"
                    keyframes.append({"time": time, "value": value})
        
        if keyframes:
            effects_data[node_name] = keyframes

    # Recurse into children
    if len(node) > 3 and isinstance(node[3], list):
        for child in node[3]:
            find_animated_dve_params(child, effects_data)
    
    return effects_data


class KeyframeParserApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Keyframe Parser")
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
            
        self.log_msg("--- Animated DVE Parameter Report ---\n")
        
        animated_params = find_animated_dve_params(json_data)

        if not animated_params:
            self.log_msg("No animated DVE parameters found.")
            messagebox.showinfo("Done", "Scan complete. No animated DVE parameters found.")
            return

        for param_name, keyframes in animated_params.items():
            self.log_msg(f"Found Animated Parameter: {param_name}")
            self.log_msg(f"  - Keyframe Count: {len(keyframes)}")
            for kf in keyframes:
                self.log_msg(f"    - Keyframe at Time: {kf['time']:<15} -> Value: {kf['value']}")
            self.log_msg("-" * 50)

        messagebox.showinfo("Done", f"Analysis complete. Found {len(animated_params)} animated parameters.")

if __name__ == "__main__":
    root = tk.Tk()
    app = KeyframeParserApp(root)
    root.mainloop()