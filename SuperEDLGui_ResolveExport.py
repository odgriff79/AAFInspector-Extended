# SuperEDLGui_ResolveExport.py
# ✅ PHASE 1: Start from `superEDLguiFX_v3.py`
# ✅ PHASE 2: Add proper keyframe conversion to Resolve structure

import os
import json
import re
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from superEDLguiFX_v3 import App as BaseApp, frames_to_tc, create_mob_map, find_main_sequence_mob_and_start_tc, find_timeline_effects, extract_effect_details, recursive_search, get_genuine_source_info, extract_metadata, find_filepath
from datetime import datetime

# --- Resolve-Compatible JSON Export Extension ---
def build_resolve_json(enriched_events, timeline_name, total_duration):
    clips = []
    fillers = []
    markers = []

    for evt in enriched_events:
        frame_start = evt.get("StartTime (frames)", 0)
        duration = evt.get("Event Length", 0)
        kf_str = evt.get("Keyframe Details", "")
        effect = evt.get("Effect Name", "")
        is_placeholder = evt['SourceMobID'] in ["FX_ON_FILLER"]

        keyframe_data = convert_keyframes_to_resolve(kf_str, frame_start)
        
        marker_color = resolve_marker_color(effect)
        markers.append({
            "frame": frame_start,
            "label": effect,
            "color": marker_color
        })

        if is_placeholder:
            fillers.append({
                "effect_type": effect,
                "timeline_start": frame_start,
                "duration": duration,
                "keyframe_data": keyframe_data
            })
        else:
            clips.append({
                "source_file": os.path.join(evt['Source File Path'], evt['Source File Name']),
                "source_name": evt['Clip Name'],
                "timeline_start": frame_start,
                "duration": duration,
                "source_in": evt.get("Source Clip offset (frames)", 0),
                "keyframe_data": keyframe_data
            })

    return {
        "clips": clips,
        "filler_effects": fillers,
        "composition_info": {
            "name": timeline_name,
            "duration": total_duration
        },
        "markers": markers
    }


def convert_keyframes_to_resolve(kf_str, timeline_start):
    if not kf_str: return {}
    keyframe_data = {}
    current_param = None
    for line in kf_str.splitlines():
        line = line.strip()
        param_match = re.match(r'-\s*Parameter:\s*(.+?)\s*\(', line)
        kf_match = re.search(r'\((\d+)f\).*?Value:\s*(.*)', line)
        if param_match:
            current_param = param_match.group(1).strip()
            keyframe_data[current_param] = []
        elif kf_match and current_param:
            frame = int(kf_match.group(1))
            val_raw = kf_match.group(2).strip()
            try:
                val = float(val_raw.split('/')[0]) / float(val_raw.split('/')[1]) if '/' in val_raw else float(val_raw)
                relative_time = frame - timeline_start
                keyframe_data[current_param].append({"time": relative_time, "value": val})
            except: pass
    return keyframe_data


def resolve_marker_color(effect):
    effect = effect.lower()
    if "zoom" in effect: return "Yellow"
    if "resize" in effect: return "Cyan"
    if "3d" in effect: return "Green"
    if "paint" in effect: return "Red"
    if "matte" in effect: return "Purple"
    return "Blue"


# --- Extended GUI Class ---
class ResolveExportApp(BaseApp):
    def __init__(self, root):
        super().__init__(root)
        tk.Button(root, text="Export Resolve-Compatible JSON", command=self.export_resolve_json).pack(pady=5)

    def export_resolve_json(self):
        if not hasattr(self, 'json_data') or not hasattr(self, 'json_path'):
            messagebox.showerror("Error", "Please load an AAF JSON first.")
            return

        output_dir = filedialog.askdirectory(title="Select Output Directory")
        if not output_dir:
            self.log_msg("❌ Resolve export cancelled.")
            return

        self.log_msg("🔧 Building Resolve-compatible JSON output...")

        mob_map = create_mob_map(self.json_data)
        sequence_mob, start_tc, timeline_rate, is_drop_frame = find_main_sequence_mob_and_start_tc(self.json_data)
        all_effects = find_timeline_effects(sequence_mob, timeline_offset=start_tc)
        effects_by_frame = {}
        for effect in all_effects:
            details = extract_effect_details(effect['node'])
            details['node'] = effect['node']
            effects_by_frame[effect['start_frame']] = details
        events = recursive_search(sequence_mob, timeline_offset=start_tc, edit_rate=timeline_rate)

        enriched = []
        seen = set()
        for idx, e in enumerate(events):
            key = (e.get("MobID"), e.get("TimelineStartFrame"), e.get("SourceOffsetFrames"), e.get("Length"))
            if key in seen: continue
            seen.add(key)
            effect_data = effects_by_frame.get(e['TimelineStartFrame'])
            kf_detail_str = ""
            if effect_data and effect_data['animated_params']:
                for pname, pts in effect_data['animated_params'].items():
                    kf_detail_str += f"- Parameter: {pname} ({len(pts)} keyframes)\n"
                    for kp in pts:
                        try:
                            t = float(kp['time'])
                            off = int(t * (e['Length'] - 1)) if e['Length'] > 1 else 0
                            absf = e['TimelineStartFrame'] + off
                            kf_detail_str += f"  Keyframe at ({absf}f) -> Value: {kp['value']}\n"
                        except:
                            kf_detail_str += f"  Keyframe at Time: {kp['time']} -> Value: {kp['value']}\n"
            enriched.append({
                "Event": idx + 1,
                "Clip Name": e.get("MobID", "Clip"),
                "Source File Name": e.get("MobID", "Clip") + ".mov",
                "Source File Path": "/path/to/media",
                "Timeline Start TC": frames_to_tc(e['TimelineStartFrame'], timeline_rate, is_drop_frame),
                "StartTime (frames)": e['TimelineStartFrame'],
                "Event Length": e['Length'],
                "Source Clip offset (frames)": e.get("SourceOffsetFrames", 0),
                "SourceMobID": e.get("MobID"),
                "Effect Name": effect_data['effect_name'] if effect_data else "",
                "Keyframe Details": kf_detail_str
            })

        json_data = build_resolve_json(enriched, "AAF Composition", sum(e['Event Length'] for e in enriched))
        json_path = os.path.join(output_dir, f"resolve_export_v1_{datetime.now():%Y%m%d_%H%M%S}.json")

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=2)
            self.log_msg(f"✅ Resolve JSON saved to: {json_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save JSON: {e}")
            self.log_msg("❌ Failed to write resolve JSON")


if __name__ == "__main__":
    root = tk.Tk()
    app = ResolveExportApp(root)
    root.mainloop()
