import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

DVE_DEFAULTS = {
    "DVE_POS_X_U": "0", "DVE_POS_Y_U": "0", "DVE_POS_Z_U": "0",
    "DVE_SCALE_X_U": "100", "DVE_SCALE_Y_U": "100",
    "DVE_ROT_X_U": "0", "DVE_ROT_Y_U": "0", "DVE_ROT_Z_U": "0",
    "DVE_CROP_LEFT_U": "0", "DVE_CROP_RIGHT_U": "0", "DVE_CROP_TOP_U": "0", "DVE_CROP_BOTTOM_U": "0",
}

def find_main_sequence_mob(root_node):
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None
    for mob in root_node[3]:
        if not (isinstance(mob, list) and len(mob) > 3): continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not (slots_node and len(slots_node) > 3): continue
        if any(isinstance(s,list) and len(s) > 3 and (seg := next((c for c in s[3] if c[0] == "Segment"),None)) and len(seg) > 3 and seg[3] and seg[3][0][0] == "Sequence" for s in slots_node[3]):
            return mob
    return None

def analyze_effect_parameters(op_group_node):
    effect_name = "Unknown Effect"
    found_params = {}
    
    comp_attrs = next((c for c in op_group_node[3] if isinstance(c, list) and c[0] == 'ComponentAttributeList'), None)
    if comp_attrs and len(comp_attrs) > 3:
        for p in comp_attrs[3]:
            if isinstance(p, list) and len(p) > 3:
                name_node = next((v for v in p[3] if isinstance(v, list) and v[0] == 'Name'), None)
                if name_node and len(name_node) > 2 and name_node[2] in ('_EFFECT_PLUGIN_NAME', '_EFFECT_NAME'):
                    value_node = next((v for v in p[3] if isinstance(v, list) and v[0] == 'Value'), None)
                    if value_node and len(value_node) > 2:
                        effect_name = value_node[2]
                        break

    params_node = next((c for c in op_group_node[3] if isinstance(c, list) and c[0] == 'Parameters'), None)
    if params_node and len(params_node) > 3:
        for param in params_node[3]:
            if isinstance(param, list) and isinstance(param[0], str) and (param[0].startswith("DVE_") or param[0].startswith("AFX_")):
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
                    is_default = str(first_value) == str(DVE_DEFAULTS.get(param_name))

                    if is_animated:
                        found_params[param_name] = {"status": "Animated", "keyframes": keyframes}
                    elif not is_default:
                        found_params[param_name] = {"status": f"Static (Value: {first_value})", "keyframes": keyframes}
    
    if effect_name != "Unknown Effect" or found_params:
        return {"effect_name": effect_name, "found_params": found_params}
        
    return None

def find_events_and_effects(node):
    if not isinstance(node, list) or len(node) < 2: return []
    node_name, children = node[0], node[3] if len(node) > 3 else []
    
    if node_name == 'OperationGroup':
        effect_data = analyze_effect_parameters(node)
        all_sub_events = []
        input_segments = next((c for c in children if c[0] == 'InputSegments'), None)
        if input_segments and len(input_segments) > 3:
            for segment in input_segments[3]:
                sub_events = find_events_and_effects(segment)
                if effect_data:
                    for event in sub_events:
                        if "effects" not in event: event["effects"] = []
                        event["effects"].append(effect_data)
                all_sub_events.extend(sub_events)
        return all_sub_events

    elif node_name == 'Sequence':
        all_sub_events = []
        components_node = next((c for c in children if c[0] == 'Components'), None)
        if components_node and len(components_node) > 3:
            for comp in components_node[3]:
                all_sub_events.extend(find_events_and_effects(comp))
        return all_sub_events
    
    elif node_name == 'SourceClip':
        clip_name = "Unknown Clip"
        mob_ref = next((c for c in children if c[0] == "Source Mob Ref"), None)
        if mob_ref and len(mob_ref) > 3 and mob_ref[3]:
             class_def = mob_ref[3][0]
             if class_def and len(class_def) > 1:
                 clip_name = class_def[0]
        return [{"clip_name": clip_name, "effects": []}]
        
    elif isinstance(children, list):
        all_sub_events = []
        for child in children:
            all_sub_events.extend(find_events_and_effects(child))
        return all_sub_events
    
    return []

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

        events = find_events_and_effects(main_mob)

        if not events:
            self.log_msg("No events found.")
            return

        for idx, event in enumerate(events, 1):
            self.log_msg(f"----------------------------------------")
            self.log_msg(f"EVENT #{idx}: {event['clip_name']}")
            if not event['effects']:
                self.log_msg("  - No modified effects applied.")
            else:
                for effect in event['effects']:
                    self.log_msg(f"  └── EFFECT: {effect['effect_name']}")
                    if not effect['found_params']:
                         self.log_msg("    - No modified parameters found.")
                    else:
                        for param_name, data in effect['found_params'].items():
                            if data['status'] == "Animated":
                                self.log_msg(f"    - {param_name}: Animated ({len(data['keyframes'])} keyframes)")
                            else:
                                self.log_msg(f"    - {param_name}: {data['status']}")
            self.log_msg("")
            
        messagebox.showinfo("Done", "Analysis complete.")

if __name__ == "__main__":
    root = tk.Tk()
    app = EffectAnalyzerApp(root)
    root.mainloop()