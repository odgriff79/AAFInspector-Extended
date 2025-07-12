import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from collections import Counter
from datetime import datetime

def find_avx_effects(node, effects_counter=None, debug_log=None):
    """
    Recursively searches the JSON for ComponentAttributeLists and identifies
    AVX effects based on their plugin type and class.
    """
    if effects_counter is None: effect_counter = Counter()
    if debug_log is None: debug_log = []

    if not isinstance(node, list):
        return

    # The target is the list of attributes for a component
    if node and node[0] == "ComponentAttributeList" and len(node) > 3:
        plugin_class = None
        plugin_type = None
        
        # --- Create Debug Dump for this component ---
        debug_log.append("="*50)
        debug_log.append("Found ComponentAttributeList. Dumping Attributes:")
        for attr in node[3]:
            if isinstance(attr, list) and len(attr) > 3:
                name = next((c[2] for c in attr[3] if c[0] == "Name"), "N/A")
                value = next((c[2] for c in attr[3] if c[0] == "Value"), "N/A")
                debug_log.append(f"  - {name}: {value}")
                
                # Extract the relevant plugin info
                if name == "_EFFECT_PLUGIN_TYPE":
                    plugin_type = value
                elif name == "_EFFECT_PLUGIN_CLASS":
                    plugin_class = value
        debug_log.append("="*50 + "\n")

        # Now, check if it's an AVX effect and count it
        if plugin_type in ("AVX", "AVX2"):
            # Use the most specific name available
            key = plugin_class.split("::")[-1] if plugin_class else "Unknown_" + plugin_type
            effect_counter[key] += 1

    # Recurse into all children to find all attribute lists
    if len(node) > 3:
        for child in node[3]:
            find_avx_effects(child, effect_counter, debug_log)
    
    return effect_counter, debug_log


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
        if not path: return

        self.log.delete(1.0, tk.END)
        self.log_msg(f"✅ Loaded JSON file: {os.path.basename(path)}\n")

        try:
            with open(path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load or parse JSON file:\n{e}")
            return
            
        self.log_msg("--- AVX Effect Report ---")
        
        effect_counts, debug_log = find_avx_effects(json_data)

        if not effect_counts:
            self.log_msg("No identifiable AVX/AVX2 effects found.")
        else:
            header = f"{'Effect Name':<40} | {'Count'}"
            self.log_msg(header)
            self.log_msg("-" * (len(header) + 2))
            for effect_name, count in sorted(effect_counts.items()):
                self.log_msg(f"{effect_name:<40} | {count}")
        
        # Save Debug Log
        output_dir = os.path.dirname(path)
        debug_path = os.path.join(output_dir, f"AVX_deep_dump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write("\n".join(debug_log))
            self.log_msg(f"\n✅ Detailed AVX debug log saved to:\n{os.path.basename(debug_path)}")
        except Exception as e:
             self.log_msg(f"\n❌ Failed to save debug log: {e}")
            
        messagebox.showinfo("Done", "Analysis complete.")

if __name__ == "__main__":
    root = tk.Tk()
    app = EffectCounterApp(root)
    root.mainloop()