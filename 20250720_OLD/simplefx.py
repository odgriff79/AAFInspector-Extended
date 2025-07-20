import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from collections import Counter

def find_effects_in_sequence(node, effects_list=None):
    """
    Recursively searches a sequence component tree and collects the names
    of nodes that represent effects.
    """
    if effects_list is None:
        effects_list = []

    if not isinstance(node, list) or len(node) < 2:
        return effects_list

    node_name, children = node[0], node[3] if len(node) > 3 else []

    # These are structural or basic clip nodes, not effects, so we ignore them.
    ignore_list = ["SourceClip", "Filler", "Sequence", "EdgeCode", "Timecode", "Pulldown"]

    if node_name not in ignore_list:
        # We consider any other component type to be an effect for this report.
        effects_list.append(node_name)

    # Recurse into the children of the current node
    # This is important for nested effects and structural nodes like 'Sequence'
    if node_name == "Sequence":
         components_node = next((c for c in children if isinstance(c, list) and c[0] == "Components"), None)
         if components_node and len(components_node) > 3:
             for comp in components_node[3]:
                 find_effects_in_sequence(comp, effects_list)

    elif isinstance(children, list):
        for child in children:
            find_effects_in_sequence(child, effects_list)
    
    return effects_list


class EffectCounterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Effect Counter")
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
            
        self.log_msg("--- Video Effect Report ---")
        
        # We assume the main sequence mob is the first object in the file
        main_mob = json_data[3][0] 
        
        # Find the actual sequence component within the mob's slots
        sequence_node = None
        slots_node = next((c for c in main_mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if slots_node:
            for slot in slots_node[3]:
                segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
                if segment_node and len(segment_node) > 3 and segment_node[3] and isinstance(segment_node[3][0], list) and segment_node[3][0][0] == "Sequence":
                    sequence_node = segment_node[3][0]
                    break
        
        if not sequence_node:
            self.log_msg("Could not find a main Sequence component in the file.")
            return

        effects = find_effects_in_sequence(sequence_node)
        effect_counts = Counter(effects)

        if not effect_counts:
            self.log_msg("No effects found in the timeline.")
            return
            
        header = f"{'Effect Name':<30} | {'Count'}"
        self.log_msg(header)
        self.log_msg("-" * (len(header) + 2))

        for effect_name, count in sorted(effect_counts.items()):
            self.log_msg(f"{effect_name:<30} | {count}")
            
        messagebox.showinfo("Done", f"Analysis complete. Found {len(effect_counts)} unique effect types.")

if __name__ == "__main__":
    root = tk.Tk()
    app = EffectCounterApp(root)
    root.mainloop()