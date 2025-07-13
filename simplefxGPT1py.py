import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime


FILTER_PARAMS = {
    'DVE_POS_X_U',
    'DVE_POS_Y_U',
    'DVE_SCALE_X_U',
    'DVE_SCALE_Y_U'
}


def extract_attributes(component_node, label):
    attributes_info = {}
    if not component_node or len(component_node) < 4:
        return attributes_info
    comp_attrs = next((c for c in component_node[3] if isinstance(c, list) and c[0] == 'ComponentAttributeList'), None)
    if comp_attrs and len(comp_attrs) > 3:
        for p in comp_attrs[3]:
            if isinstance(p, list):
                name = p[0]
                value_prop = next((v for v in p[3] if v[0] == 'Value'), None)
                value = value_prop[2] if value_prop and len(value_prop) > 2 else "N/A"
                attributes_info[name] = value
    if attributes_info:
        print(f"[DEBUG] {label} attributes found: {list(attributes_info.keys())}")
    return attributes_info


def find_main_sequence_mob(root_node):
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None
    for mob in root_node[3]:
        if not (isinstance(mob, list) and len(mob) > 3): continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not (slots_node and len(slots_node) > 3): continue
        if any(isinstance(s, list) and len(s) > 3 and (seg := next((c for c in s[3] if c[0] == "Segment"), None)) and len(seg) > 3 and seg[3] and seg[3][0][0] == "Sequence" for s in slots_node[3]):
            return mob
    return None


def analyze_effect_parameters(op_group_node):
    effect_name = "Unknown Effect"
    animated_params = {}
    attributes_info = extract_attributes(op_group_node, "OperationGroup")

    if '_EFFECT_PLUGIN_NAME' in attributes_info:
        effect_name = attributes_info['_EFFECT_PLUGIN_NAME']
    print(f"[DEBUG] Found effect plugin name: {effect_name}")

    if effect_name == '3DWarp':
        params_node = next((c for c in op_group_node[3] if isinstance(c, list) and c[0] == 'Parameters'), None)
        if params_node and len(params_node) > 3:
            for param in params_node[3]:
                if isinstance(param, list) and isinstance(param[0], str) and param[0] in FILTER_PARAMS:
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
                        animated_params[param_name] = keyframes
                        print(f"[DEBUG] Found parameter: {param_name} with {len(keyframes)} keyframes")
                        for kf in keyframes:
                            print(f"         Keyframe: Time={kf['time']}  Value={kf['value']}")

    return {"effect_name": effect_name, "animated_params": animated_params, "attributes_info": attributes_info}


def find_events_and_effects(node, events_list=None):
    if events_list is None: events_list = []
    if not isinstance(node, list) or len(node) < 2: return

    node_name, children = node[0], node[3] if len(node) > 3 else []

    if node_name == 'OperationGroup':
        effect_data = analyze_effect_parameters(node)
        input_segments = next((c for c in children if isinstance(c, list) and c[0] == 'InputSegments'), None)
        if input_segments and len(input_segments) > 3:
            for segment in input_segments[3]:
                nested_events = []
                find_events_and_effects(segment, nested_events)
                for ev in nested_events:
                    if effect_data:
                        if "effects" not in ev:
                            ev["effects"] = []
                        ev["effects"].append(effect_data)
                        print(f"[DEBUG] Assigning effect '{effect_data['effect_name']}' to clip '{ev['clip_name']}'")
                    events_list.append(ev)
        return

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
        source_clip_attributes = extract_attributes(node, "SourceClip")
        events_list.append({"clip_name": clip_name, "effects": [], "source_clip_attributes": source_clip_attributes})

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

        output_lines = []

        for idx, event in enumerate(events, 1):
            self.log_msg(f"----------------------------------------")
            self.log_msg(f"EVENT #{idx}: {event['clip_name']}")
            output_lines.append(f"--- EVENT #{idx}: {event['clip_name']} ---")

            if event.get('source_clip_attributes'):
                output_lines.append("SourceClip Attributes:")
                for attr, val in event['source_clip_attributes'].items():
                    output_lines.append(f"{attr}: {val}")
                output_lines.append("")

            if not event['effects']:
                self.log_msg("  - No effects applied or effects have no animated parameters.")
                output_lines.append("No effects applied or no animated parameters.\n")
            else:
                for effect in event['effects']:
                    self.log_msg(f"  └── EFFECT: {effect['effect_name']}")
                    output_lines.append(f"Effect: {effect['effect_name']}")

                    if effect['attributes_info']:
                        output_lines.append("OperationGroup Attributes:")
                        for attr, val in effect['attributes_info'].items():
                            output_lines.append(f"{attr}: {val}")

                    if not effect['animated_params']:
                        self.log_msg("    - Effect is present but has no animated parameters.")
                        output_lines.append("No parameters found.\n")
                    else:
                        for param_name, keyframes in effect['animated_params'].items():
                            self.log_msg(f"    - Parameter: {param_name} ({len(keyframes)} keyframes)")
                            output_lines.append(f"Parameter: {param_name}")
                            for kf in keyframes:
                                self.log_msg(f"      - Time: {str(kf['time']):<15} -> Value: {kf['value']}")
                                output_lines.append(f"    Time: {kf['time']}, Value: {kf['value']}")
            self.log_msg("")
            output_lines.append("")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dump_filename = f"effect_parameters_dump_{timestamp}.txt"
        with open(dump_filename, "w", encoding="utf-8") as f:
            f.write("\n".join(output_lines))

        self.log_msg(f"✅ Detailed effect parameter dump saved to:\n{dump_filename}")

        messagebox.showinfo("Done", "Analysis complete.")


if __name__ == "__main__":
    root = tk.Tk()
    app = EffectAnalyzerApp(root)
    root.mainloop()
