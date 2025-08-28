import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox

def find_main_sequence_mob(root_node):
    """
    Traverses the JSON to find the main sequence mob.
    """
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None
    for mob in root_node[3]:
        if not (isinstance(mob, list) and len(mob) > 3): continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not (slots_node and len(slots_node) > 3): continue
        if any(isinstance(s,list) and len(s) > 3 and (seg := next((c for c in s[3] if c[0] == "Segment"),None)) and len(seg) > 3 and seg[3] and seg[3][0][0] == "Sequence" for s in slots_node[3]):
            return mob
    return None

def analyze_effect_parameters(op_group_node):
    """
    Analyzes a single OperationGroup node for its name and animated parameters.
    """
    effect_name = "Unknown Effect"
    animated_params = {}
    
    # --- CORRECTED: Look in ComponentAttributeList for the effect name ---
    comp_attrs = next((c for c in op_group_node[3] if isinstance(c, list) and c[0] == 'ComponentAttributeList'), None)
    if comp_attrs and len(comp_attrs) > 3:
        for p in comp_attrs[3]:
            if isinstance(p, list) and p[0] in ('_EFFECT_PLUGIN_NAME', '_EFFECT_NAME'):
                value_prop = next((v for v in p[3] if v[0] == 'Value'), None)
                if value_prop and len(value_prop) > 2:
                    effect_name = value_prop[2]

    params_node = next((c for c in op_group_node[3] if isinstance(c, list) and c[0] == 'Parameters'), None)
    if params_node and len(params_node) > 3:
        # Analyze DVE parameters for animation
        for param in params_node[3]:
            if isinstance(param, list) and isinstance(param[0], str) and param[0].startswith("DVE_"):
                param_name = param[0]
                point_list_node = next((c for c in param[3] if isinstance(c, list) and c[0] == "PointList"), None)
                
                keyframes = []
                if point_list_node and len(point_list_node) > 3:
                    for cp in point_list_node[3]:
                        if isinstance(cp, list) and cp[0] == "ControlPoint" and len(cp) > 3:
                            time = next((p[2] for p in cp[3] if p[0] == "Time"), "N/A")
                            value = next((p[2] for p in cp[3] if p[0] == "Value"), "N/A")
                            keyframes.append({"time": time, "value": value})
                
                if keyframes:
                    first_value = keyframes[0]['value']
                    is_animated = not all(kf['value'] == first_value for kf in keyframes)
                    if is_animated:
                        animated_params[param_name] = keyframes
                        
    if animated_params:
        return {"effect_name": effect_name, "animated_params": animated_params}
        
    return None


def find_events_and_effects(node, events_list=None):
    """
    Finds all SourceClip events and any OperationGroup that wraps them.
    """
    if events_list is None: events_list = []
    if not isinstance(node, list) or len(node) < 2: return

    node_name, children = node[0], node[3] if len(node) > 3 else []

    if node_name == 'OperationGroup':
        effect_data = analyze_effect_parameters(node)
        input_segments = next((c for c in children if isinstance(c, list) and c[0] == 'InputSegments'), None)
        if input_segments and len(input_segments) > 3:
            for segment in input_segments[3]:
                find_events_and_effects(segment, events_list)
                if events_list and effect_data:
                    # Apply the found effect to the last added event
                    if "effects" not in events_list[-1]:
                        events_list[-1]["effects"] = []
                    events_list[-1]["effects"].append(effect_data)

    elif node_name == 'Sequence':
        components_node = next((c for c in children if isinstance(c, list) and c[0] == 'Components'), None)
        if components_node and len(components_node) > 3:
            for comp in components_node[3]:
                find_events_and_effects(comp, events_list)
    
    elif node_name == 'SourceClip':
        clip_name = "Unknown Clip"
        mob_ref = next((c for c in children if isinstance(c, list) and c[0] == "Source Mob Ref"), None)
        if mob_ref and len(mob_ref) > 3 and mob_ref[3]:
             class_def = mob_ref[3][0]
             if class_def and len(class_def) > 1:
                 clip_name = class_def[0]
        
        events_list.append({"clip_name": clip_name, "effects": []})
        
    elif isinstance(children, list):
        for child in children:
            find_events_and_effects(child, events_list)


class EffectAnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Detailed Effect Analyzer")
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
            
        self.log_msg("--- Detailed Effect Report ---\n")
        
        main_mob = find_main_sequence_mob(json_data) 
        if not main_mob:
            self.log_msg("Could not find a main sequence mob in the file.")
            return

        events = []
        find_events_and_effects(main_mob, events)

        if not events:
            self.log_msg("No events found.")
            return

        for idx, event in enumerate(events, 1):
            self.log_msg(f"----------------------------------------")
            self.log_msg(f"EVENT #{idx}: {event['clip_name']}")
            if not event['effects']:
                self.log_msg("  - No effects with changing keyframes applied.")
            else:
                for effect in event['effects']:
                    self.log_msg(f"  └── EFFECT: {effect['effect_name']}")
                    for param_name, keyframes in effect['animated_params'].items():
                        self.log_msg(f"    - Animated Parameter: {param_name} ({len(keyframes)} keyframes)")
                        for kf in keyframes:
                            self.log_msg(f"      - Time: {str(kf['time']):<15} -> Value: {kf['value']}")
            self.log_msg("")
            
        messagebox.showinfo("Done", "Analysis complete.")

if __name__ == "__main__":
    root = tk.Tk()
    app = EffectAnalyzerApp(root)
    root.mainloop()