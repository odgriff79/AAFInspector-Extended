import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# Filter to only show these important DVE parameters for a 3DWarp for a cleaner report
FILTER_3DWARP_PARAMS = {
    'DVE_POS_X_U', 'DVE_POS_Y_U', 'DVE_SCALE_X_U', 'DVE_SCALE_Y_U'
}

def find_main_sequence_mob(root_node):
    """Traverses the JSON to find the main sequence mob."""
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None
    for mob in root_node[3]:
        if not (isinstance(mob, list) and len(mob) > 3): continue
        slots_node = next((c for c in mob[3] if c[0] == "Slots"), None)
        if not (slots_node and len(slots_node) > 3): continue
        if any(isinstance(s,list) and len(s) > 3 and (seg := next((c for c in s[3] if c[0] == "Segment"),None)) and len(seg) > 3 and seg[3] and seg[3][0][0] == "Sequence" for s in slots_node[3]):
            return mob
    return None

def analyze_effect_parameters(op_group_node, debug_log):
    """Analyzes a single OperationGroup for its name and modified parameters."""
    effect_name = "Unknown Effect"
    found_params = {}
    
    debug_log.append("="*50)
    debug_log.append(f"Analyzing new OperationGroup:")
    
    # Get all attributes and parameters into one list for analysis
    all_params_nodes = []
    comp_attrs = next((c for c in op_group_node[3] if c[0] == 'ComponentAttributeList'), None)
    if comp_attrs and len(comp_attrs) > 3:
        all_params_nodes.extend(comp_attrs[3])
        
    params_node = next((c for c in op_group_node[3] if c[0] == 'Parameters'), None)
    if params_node and len(params_node) > 3:
        all_params_nodes.extend(params_node[3])

    debug_log.append("  Dumping all found attributes/parameters:")
    for p in all_params_nodes:
        if isinstance(p, list) and len(p) > 3:
            name_node = next((v for v in p[3] if v[0] == 'Name'), None)
            value_node = next((v for v in p[3] if v[0] == 'Value'), None)
            if name_node and len(name_node) > 2:
                name = name_node[2]
                value = value_node[2] if value_node and len(value_node) > 2 else "N/A"
                debug_log.append(f"    - {name}: {value}")
                if name in ('_EFFECT_PLUGIN_NAME', '_EFFECT_NAME'):
                    effect_name = value
    
    # Analyze parameters for animation or static changes
    for param in all_params_nodes:
        if isinstance(param, list) and isinstance(param[0], str) and (param[0].startswith("DVE_") or param[0].startswith("AFX_")):
            param_name = param[0]
            point_list_node = next((c for c in param[3] if c[0] == "PointList"), None)
            
            if point_list_node and len(point_list_node) > 3:
                keyframes = [{"time": next((p[2] for p in cp[3] if p[0] == "Time"),"N/A"), "value": next((p[2] for p in cp[3] if p[0] == "Value"),"N/A")} for cp in point_list_node[3] if isinstance(cp, list)]
                if keyframes:
                    first_val = keyframes[0]['value']
                    if not all(kf['value'] == first_val for kf in keyframes):
                        found_params[param_name] = {"status": "Animated", "keyframes": keyframes}

    if effect_name != "Unknown Effect" or found_params:
        return {"effect_name": effect_name, "found_params": found_params}
        
    return None

class EffectAnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Detailed Effect Analyzer")
        self.root.geometry("800x600")
        tk.Button(root, text="Load AAF as JSON File", command=self.load_and_process).pack(pady=10)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n"); self.log.see(tk.END)

    def load_and_process(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if not path: return

        self.log.delete(1.0, tk.END)
        self.log_msg(f"✅ Loaded JSON file: {os.path.basename(path)}\n")

        try:
            with open(path, "r", encoding="utf-8") as f: json_data = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load or parse JSON file:\n{e}"); return
            
        self.log_msg("--- Detailed Effect Report ---\n")
        
        main_mob = find_main_sequence_mob(json_data) 
        if not main_mob: self.log_msg("Could not find a main sequence mob."); return

        events = []
        debug_log = []
        
        components_node = next((c for c in main_mob[3] if c[0] == 'Components'), None)
        if components_node and len(components_node) > 3:
            components = components_node[3]
            i = 0
            while i < len(components):
                comp = components[i]
                event_clip = None
                effect_data = None

                if comp[0] == 'OperationGroup':
                    effect_data = analyze_effect_parameters(comp, debug_log)
                    if (i + 1) < len(components) and components[i+1][0] == 'SourceClip':
                        event_clip = components[i+1]
                        i += 1 
                elif comp[0] == 'SourceClip':
                    event_clip = comp
                
                if event_clip:
                    clip_name = "Unknown Clip"
                    mob_ref = next((c for c in event_clip[3] if c[0] == "Source Mob Ref"), None)
                    if mob_ref and len(mob_ref) > 3 and mob_ref[3]:
                         class_def = mob_ref[3][0]
                         if class_def and len(class_def) > 1:
                             clip_name = class_def[0]
                    
                    event_obj = {"clip_name": clip_name, "effects": []}
                    if effect_data:
                        event_obj["effects"].append(effect_data)
                    events.append(event_obj)
                i += 1

        if not events: self.log_msg("No events found."); return

        for idx, event in enumerate(events, 1):
            self.log_msg(f"----------------------------------------")
            self.log_msg(f"EVENT #{idx}: {event['clip_name']}")
            if not event['effects']:
                self.log_msg("  - No modified effects applied.")
            else:
                for effect in event['effects']:
                    self.log_msg(f"  └── EFFECT: {effect['effect_name']}")
                    if not effect['found_params']:
                         self.log_msg("    - No animated parameters found for this effect.")
                    else:
                        params_to_show = effect['found_params']
                        # Apply filter for 3DWarp
                        if effect['effect_name'] == '3DWarp':
                            params_to_show = {k: v for k, v in effect['found_params'].items() if k in FILTER_3DWARP_PARAMS}

                        for param_name, data in params_to_show.items():
                            self.log_msg(f"    - {param_name}: {data['status']} ({len(data['keyframes'])} keyframes)")
            self.log_msg("")

        # Save Debug Log
        output_dir = os.path.dirname(path)
        debug_path = os.path.join(output_dir, f"effect_parameters_dump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        try:
            with open(debug_path, "w", encoding="utf-8") as f: f.write("\n".join(debug_log))
            self.log_msg(f"\n✅ Detailed effect parameter dump saved to:\n{os.path.basename(debug_path)}")
        except Exception as e:
             self.log_msg(f"\n❌ Failed to save debug log: {e}")

        messagebox.showinfo("Done", "Analysis complete.")

if __name__ == "__main__":
    root = tk.Tk()
    app = EffectAnalyzerApp(root)
    root.mainloop()