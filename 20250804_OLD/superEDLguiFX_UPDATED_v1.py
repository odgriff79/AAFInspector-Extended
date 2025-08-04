import os
import re
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode and Parsing Functions ---

def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    """
    Converts a frame count to a timecode string.
    """
    if frame_count is None or fps is None or fps <= 0:
        return "N/A"
    try:
        separator = ";" if is_drop_frame else ":"
        fc = int(frame_count)
        int_fps = round(float(fps))
        if int_fps <= 0:
            return "N/A"
        h = fc // (3600 * int_fps)
        m = (fc % (3600 * int_fps)) // (60 * int_fps)
        s = (fc % (60 * int_fps)) // int_fps
        f = fc % int_fps
        return f"{h:02}:{m:02}:{s:02}{separator}{f:02}"
    except (ValueError, TypeError):
        return "N/A"

# --- Filepath Decoding ---

def decode_filepath(filepath_node):
    try:
        value_node = next(
            (c for c in (filepath_node[3] if len(filepath_node) > 3 else [])
             if c[0] == "Value" and isinstance(c[2], list)),
            None
        )
        if not value_node:
            return "Path data not found or in an unexpected format."

        raw_bytes = bytes(b for b in value_node[2] if isinstance(b, int))
        txt = raw_bytes.decode("utf-16-le", errors="ignore")
        idx = txt.find('\\')
        if idx != -1:
            txt = txt[idx:]
        cleaned = txt.rstrip('\x00').replace('\\', '/')
        return cleaned or "(decoded to an empty string)"
    except Exception as e:
        return f"An error occurred during decoding: {e}"

def find_filepath(subnode):
    if not isinstance(subnode, list):
        return None
    if subnode[0] == "Filepath":
        return decode_filepath(subnode)
    children = subnode[3] if len(subnode) > 3 else []
    for c in children:
        p = find_filepath(c)
        if p:
            return p
    return None

# --- Mob Map and Sequence Locators ---

def create_mob_map(node, mob_map=None):
    if mob_map is None:
        mob_map = {}
    if isinstance(node, list) and len(node) > 1:
        children = node[3] if len(node) > 3 else []
        if any(isinstance(c, list) and c[0] == "MobID" for c in children):
            mob_id = next((c[2] for c in children if isinstance(c, list) and c[0] == "MobID"), None)
            if mob_id:
                mob_map[mob_id] = node
        for c in children:
            create_mob_map(c, mob_map)
    return mob_map

def find_main_sequence_mob_and_start_tc(root_node):
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4:
        return None, 0, 25.0, False
    for mob in root_node[3]:
        if not (isinstance(mob, list) and len(mob) > 3):
            continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not (slots_node and len(slots_node) > 3):
            continue
        is_sequence = any(
            isinstance(s, list) and len(s) > 3 and
            (seg := next((c for c in s[3] if c[0] == "Segment"), None)) and
            isinstance(seg, list) and len(seg) > 3 and
            isinstance(seg[3], list) and seg[3] and seg[3][0][0] == "Sequence"
            for s in slots_node[3]
        )
        if not is_sequence:
            continue
        start_tc, is_drop, edit_rate = 0, False, 25.0
        for s in slots_node[3]:
            rate_node = next((c for c in s[3] if isinstance(c, list) and c[0] == "EditRate"), None)
            if rate_node and len(rate_node) > 2:
                try:
                    rs = str(rate_node[2])
                    if "/" in rs:
                        n, d = map(float, rs.split("/"))
                        if d:
                            edit_rate = n / d
                    else:
                        edit_rate = float(rs)
                except:
                    pass
            seg_tm = next((c for c in s[3] if c[0] == "Segment"), None)
            if seg_tm and isinstance(seg_tm, list) and len(seg_tm) > 3 and isinstance(seg_tm[3], list) and seg_tm[3] and seg_tm[3][0][0] == "Timecode":
                tc_node = seg_tm[3][0]
                start_node = next((c for c in tc_node[3] if c[0] == "Start"), None)
                drop_node = next((c for c in tc_node[3] if c[0] == "Drop"), None)
                if drop_node and len(drop_node) > 2:
                    is_drop = bool(drop_node[2])
                if start_node and len(start_node) > 2:
                    try:
                        start_tc = int(start_node[2])
                    except:
                        pass
        return mob, start_tc, edit_rate, is_drop
    return None, 0, 25.0, False

# --- Metadata Extraction Utilities ---

def extract_metadata(mob_node):
    metadata = {"URLString": "", "TapeID": "", "DiskLabel": "", "SourceEditRate": None, "GenuineStartFrames": 0, "IsDropFrame": False}
    if not mob_node:
        return metadata
    all_starts = []
    def recursive_extract(n):
        if not isinstance(n, list):
            return
        node_name = n[0]
        children = n[3] if len(n) > 3 else []
        if node_name in ("Start", "StartTime") and len(n) > 2:
            try:
                all_starts.append(int(n[2]))
            except:
                pass
        elif node_name == "URLString" and len(n) > 2:
            metadata["URLString"] = n[2]
        elif node_name == "EditRate" and len(n) > 2:
            try:
                rate_str = str(n[2])
                if "/" in rate_str:
                    num, den = map(float, rate_str.split("/"))
                    metadata["SourceEditRate"] = num / den if den != 0 else 0
                else:
                    metadata["SourceEditRate"] = float(rate_str)
            except:
                pass
        elif node_name == "Drop" and len(n) > 2:
            metadata["IsDropFrame"] = bool(n[2])
        elif node_name == "TapeID" and len(n) > 3 and not metadata["TapeID"]:
            metadata["TapeID"] = next((c[2] for c in children if c[0] == "Value"), "")
        elif node_name in ("DiskLabel", "_IMPORTDISKLAB") and len(n) > 3 and not metadata["DiskLabel"]:
            metadata["DiskLabel"] = next((c[2] for c in children if c[0] == "Value"), "")
        elif node_name == "MobAttributeList":
            for attr in children:
                if isinstance(attr, list) and len(attr) > 3:
                    attr_name = next((c[2] for c in attr[3] if c[0] == "Name"), "")
                    attr_val = next((c[2] for c in attr[3] if c[0] == "Value"), "")
                    if attr_name == "TapeID" and not metadata["TapeID"]:
                        metadata["TapeID"] = attr_val
                    if attr_name == "DiskLabel" and not metadata["DiskLabel"]:
                        metadata["DiskLabel"] = attr_val
        for child in children:
            recursive_extract(child)
    recursive_extract(mob_node)
    if all_starts:
        metadata["GenuineStartFrames"] = max(all_starts)
    return metadata

# --- Recursive Timeline Traversal for EDL Events ---

def has_nested_source_clip(node):
    if not isinstance(node, list):
        return False
    if node[0] == "SourceClip":
        return True
    children = node[3] if len(node) > 3 else []
    return any(has_nested_source_clip(child) for child in children)

def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, dedupe_set=None):
    if results is None:
        results = []
    if dedupe_set is None:
        dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2:
        return results
    name = node[0]
    children = node[3] if len(node) > 3 else []
    # Skip audio tracks
    if name in ["Data Track", "Sound"] or (isinstance(name, str) and any(name.startswith(p) for p in ["A1", "A2", "A3", "A4"])):
        return results
    # Sequence nodes: dive into components
    if name == "Sequence":
        comps = next((c for c in children if c[0] == "Components"), None)
        if comps and len(comps) > 3:
            for comp in comps[3]:
                recursive_search(comp, timeline_offset, edit_rate, results, dedupe_set)
                ln = next((x for x in comp[3] if x[0] == "Length"), None)
                if ln and len(ln) > 2:
                    try:
                        timeline_offset += int(ln[2])
                    except:
                        pass
    # SourceClip nodes: record as EDL event
    elif name == "SourceClip":
        mobid = next((c[2] for c in children if c[0] == "SourceID"), None)
        track_id = next((c[2] for c in children if c[0] == "SourceTrackID"), "N/A")
        offset = next((int(c[2]) for c in children if c[0] in ("Start", "StartTime")), 0)
        length = next((int(c[2]) for c in children if c[0] == "Length"), 0)
        key = (mobid, timeline_offset, offset, length)
        if mobid and key not in dedupe_set:
            dedupe_set.add(key)
            results.append({
                "MobID": mobid,
                "SourceTrackID": track_id,
                "TimelineStartFrame": timeline_offset,
                "SourceOffsetFrames": offset,
                "Length": length,
                "TimelineEditRate": edit_rate
            })
    # OperationGroup without nested source is an FX-on-filler event
    elif name == "OperationGroup":
        if not has_nested_source_clip(node):
            length = next((int(c[2]) for c in children if c[0] == "Length"), 0)
            file_path = find_filepath(node) or "N/A"
            if length > 0:
                results.append({
                    "MobID": "FX_ON_FILLER",
                    "TimelineStartFrame": timeline_offset,
                    "SourceOffsetFrames": 0,
                    "Length": length,
                    "TimelineEditRate": edit_rate,
                    "FilePath": file_path
                })
        for c in children:
            recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    else:
        for c in children:
            recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    return results

# --- Resolve Genuine Source Info for Clips ---

def get_genuine_source_info(mob_id, mob_map, visited=None):
    if visited is None:
        visited = set()
    if mob_id in visited:
        return None
    visited.add(mob_id)
    mob = mob_map.get(mob_id)
    if not mob:
        return None
    slots_node = next((c for c in mob[3] if c[0] == "Slots"), None)
    next_mob_id = None
    if slots_node and len(slots_node) > 3:
        for slot in slots_node[3]:
            segment = next((c for c in slot[3] if c[0] == "Segment"), None)
            if segment and isinstance(segment, list) and len(segment) > 3 and isinstance(segment[3], list) and segment[3] and isinstance(segment[3][0], list) and segment[3][0][0] == "SourceClip":
                next_mob_id = next((c[2] for c in segment[3][0][3] if c[0] == "SourceID"), None)
                break
    if next_mob_id:
        final = get_genuine_source_info(next_mob_id, mob_map, visited)
        return final or mob
    return mob

# --- Timeline FX Detection for Keyframes ---

def find_timeline_effects(node, timeline_offset=0, results_list=None):
    if results_list is None:
        results_list = []
    if not isinstance(node, list) or len(node) < 2:
        return results_list
    name = node[0]
    children = node[3] if len(node) > 3 else []
    if name == 'Sequence':
        comps = next((c for c in children if c[0] == 'Components'), None)
        if comps and len(comps) > 3:
            for comp in comps[3]:
                find_timeline_effects(comp, timeline_offset, results_list)
                ln = next((c for c in comp[3] if c[0] == 'Length'), None)
                if ln and len(ln) > 2:
                    try:
                        timeline_offset += int(ln[2])
                    except:
                        pass
    elif name == 'OperationGroup':
        record = False
        attrs = next((c for c in node[3] if c[0] == 'ComponentAttributeList'), None)
        if attrs and len(attrs) > 3:
            plugin_keys = [a[0] for a in attrs[3] if isinstance(a, list)]
            if '_EFFECT_PLUGIN_NAME' in plugin_keys or '_EFFECT_PLUGIN_CLASS' in plugin_keys:
                record = True
        if not record:
            op_def = next((c for c in node[3] if c[0] == 'Operation'), None)
            if op_def and len(op_def) > 2 and isinstance(op_def[2], str) and 'MatteKey' in op_def[2]:
                record = True
        if not record:
            params = next((c for c in node[3] if c[0] == 'Parameters'), None)
            if params and len(params) > 3:
                for p in params[3]:
                    pname = next((x[2] for x in p[3] if x[0] == 'Name'), p[0])
                    if 'KEY' in pname.upper():
                        record = True
                        break
        if record:
            results_list.append({'node': node, 'start_frame': timeline_offset})
        return results_list
    else:
        for child in children:
            find_timeline_effects(child, timeline_offset, results_list)
    return results_list

# --- Extract Effect Keyframe Details ---

def extract_effect_details(node):
    all_attrs = {}
    def collect(n):
        if not isinstance(n, list):
            return
        if n[0] == 'ComponentAttributeList' and len(n) > 3:
            for a in n[3]:
                if isinstance(a, list):
                    v = next((x for x in a[3] if x[0] == 'Value'), None)
                    if v and len(v) > 2:
                        all_attrs[a[0]] = v[2]
        for c in (n[3] if len(n) > 3 else []):
            collect(c)
    collect(node)
    plugin_name = all_attrs.get('_EFFECT_PLUGIN_NAME')
    plugin_class = all_attrs.get('_EFFECT_PLUGIN_CLASS')
    if plugin_class and plugin_name:
        effect_name = f"{plugin_class} : {plugin_name}"
    elif plugin_name:
        effect_name = plugin_name
    else:
        op = next((c for c in node[3] if c[0] == 'Operation'), None)
        if op and isinstance(op[2], str):
            raw = op[2]
            part = raw.split(" ")[1] if " " in raw else raw
            effect_name = part.replace('_v2', '').replace('_2', '').replace('_', ' ').strip()
        else:
            effect_name = 'Unknown Effect'
    ln = next((c for c in node[3] if c[0] == 'Length'), None)
    length = int(ln[2]) if ln and len(ln) > 2 else 0
    animated = {}
    
    # --- ADDED LOGIC to find STATIC parameters ---
    static_params_str_list = []
    # --- END OF ADDED LOGIC ---
    
    pn = next((c for c in node[3] if c[0] == 'Parameters'), None)
    if pn and len(pn) > 3:
        for p in pn[3]:
            pname = next((x[2] for x in p[3] if x[0] == 'Name'), p[0])
            plist = next((x for x in p[3] if x[0] == 'PointList'), None)
            kfs = []
            if plist and len(plist) > 3:
                for cp in plist[3]:
                    if isinstance(cp, list) and cp[0] == 'ControlPoint':
                        tval = next((x[2] for x in cp[3] if x[0] == 'Time'), '0')
                        vval = next((x[2] for x in cp[3] if x[0] == 'Value'), 'N/A')
                        kfs.append({'time': tval, 'value': vval})
            if kfs:
                animated[pname] = kfs
            # --- ADDED LOGIC to find STATIC parameters ---
            elif not plist: # If there's no PointList, it's a static parameter
                static_value = next((x[2] for x in p[3] if x[0] == 'Value'), None)
                if static_value is not None:
                    static_params_str_list.append(f"- Parameter: {pname} -> Value: {static_value}")
            # --- END OF ADDED LOGIC ---

    # --- ADDED LOGIC to assemble static params string ---
    static_params_str = "\n".join(static_params_str_list)
    return {'effect_name': effect_name, 'length': length, 'animated_params': animated, 'static_params_str': static_params_str}
    # --- END OF ADDED LOGIC ---

# --- GUI Application ---

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Super EDL + FX Generator v2")
        self.root.geometry("1200x800")
        tk.Button(root, text="Load AAF Export (JSON) File", command=self.load_json).pack(pady=10)
        self.filename_label = tk.Label(root, text="No file loaded.", fg="grey")
        self.filename_label.pack(pady=2)
        self.generate_button = tk.Button(root, text="Generate Super EDL + FX", command=self.process, state=tk.DISABLED)
        self.generate_button.pack(pady=5)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.json_data = json.load(f)
                self.json_path = path
                self.filename_label.config(text=os.path.basename(path), fg="black")
                self.generate_button.config(state=tk.NORMAL)
                self.log.delete(1.0, tk.END)
                self.log_msg(f"✅ Loaded JSON file:\n{path}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load or parse JSON file:\n{e}")
                self.filename_label.config(text="Failed to load file.", fg="red")
                self.generate_button.config(state=tk.DISABLED)

    def process(self):
        if not hasattr(self, 'json_data'):
            messagebox.showerror("Error", "Please load a file first.")
            return
        output_dir = filedialog.askdirectory(title="Select Output Directory")
        if not output_dir:
            self.log_msg("❌ Report generation cancelled.")
            return
        self.log.delete(1.0, tk.END)
        self.log_msg("1. Building Mob map...")
        mob_map = create_mob_map(self.json_data)
        
        self.log_msg("2. Finding main sequence...")
        sequence_mob, start_tc, timeline_rate, is_drop_frame = find_main_sequence_mob_and_start_tc(self.json_data)
        if not sequence_mob:
            self.log_msg("❌ Could not find main sequence.")
            return
        
        # --- ADDED: Get sequence name for filename ---
        sequence_name = next((c[2] for c in sequence_mob[3] if c[0] == "Name"), "N/A")
        
        self.log_msg("3. Scanning for all timeline effects and keyframes...")
        all_effects = find_timeline_effects(sequence_mob, timeline_offset=start_tc)
        effects_by_frame = {}
        for effect in all_effects:
            details = extract_effect_details(effect['node'])
            details['node'] = effect['node']
            effects_by_frame[effect['start_frame']] = details
        self.log_msg(f"   Found {len(effects_by_frame)} effects.")

        self.log_msg("4. Extracting primary timeline events (EDL)...")
        events = recursive_search(sequence_mob, timeline_offset=start_tc, edit_rate=timeline_rate)
        unique_events = []
        seen_keys = set()
        for e in events:
            key = (e.get("MobID"), e.get("TimelineStartFrame"), e.get("SourceOffsetFrames"), e.get("Length"))
            if key not in seen_keys:
                unique_events.append(e)
                seen_keys.add(key)
        events = unique_events
        total_length = sum(e['Length'] for e in events)

        summary_info = {
            "Timeline Name": sequence_name,
            "Timeline Edit Rate": f"{timeline_rate} {'(DF)' if is_drop_frame else '(NDF)'}",
            "Timeline Start": frames_to_tc(start_tc, timeline_rate, is_drop_frame),
            "Timeline Length": frames_to_tc(total_length, timeline_rate, is_drop_frame) + f" ({total_length} frames)",
            "Total number of EDL events found": len(events),
            "Total number of unique sources": len({e['MobID'] for e in events if e.get('MobID') not in ['FX_ON_FILLER', 'PZ_OVERRIDE']})
        }

        self.log_msg("5. Enriching events with source and effect data...")
        enriched = []
        for idx, e in enumerate(events, start=1):
            start_frame = e['TimelineStartFrame']
            effect_data = effects_by_frame.get(start_frame)
            effect_name = effect_data['effect_name'] if effect_data else 'N/A'
            is_override = effect_data and 'Avid Pan & Zoom' in effect_name and e.get('MobID') != 'FX_ON_FILLER'
            is_pz_filler = e.get('MobID') == 'FX_ON_FILLER' and effect_data and 'Avid Pan & Zoom' in effect_name
            is_generic_filler = e.get('MobID') == 'FX_ON_FILLER' and not is_pz_filler

            keyframe_details = 'No effect data found.'
            if effect_data:
                kfs_list = []
                if effect_data['animated_params']:
                    kfs_list.append('--- Animated Parameters ---')
                    for pname, pts in effect_data['animated_params'].items():
                        kfs_list.append(f"  - Parameter: {pname} ({len(pts)} keyframes)")
                        for kp in pts:
                            try:
                                t = float(kp['time'])
                                off = int(t * (effect_data['length'] - 1)) if effect_data['length'] > 1 else 0
                                absf = start_frame + off
                                kfs_list.append(f"    Keyframe at {frames_to_tc(absf, timeline_rate, is_drop_frame)} ({absf}f) -> Value: {kp['value']}")
                            except:
                                kfs_list.append(f"    Keyframe at Time: {kp['time']} -> Value: {kp['value']}")
                
                if effect_data.get('static_params_str'):
                    if kfs_list: kfs_list.append("\n--- Static Parameters ---")
                    else: kfs_list.append("--- Static Parameters ---")
                    kfs_list.append(effect_data['static_params_str'])
                
                if kfs_list:
                    keyframe_details = '\n'.join(kfs_list)

            if is_override:
                node = effect_data['node']
                fpath = find_filepath(node) or 'N/A'
                fname = os.path.basename(fpath) if fpath != 'N/A' else 'P&Z Source'
                enriched.append({
                    'Event': idx, 'Event Name': f"(P&Z Override) {fname}", 'Clip Name': fname, 'Source File Name': fname,
                    'Source File Path': os.path.dirname(fpath).replace('\\', '/') if fpath != 'N/A' else 'N/A',
                    'DiskLabel': 'N/A', 'TapeID': 'N/A', 'SourceMobID': 'PZ_OVERRIDE', 'TrackID': 'VFX',
                    'Source Clip EditRate': e['TimelineEditRate'],
                    'Timeline Start TC': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                    'Source Clip start time code': 'N/A', 'Source Clip offset': 'N/A', 'StartTime': 'N/A', 'End Time': 'N/A',
                    'Event Length': e['Length'], 'Source Clip start (frames)': 0, 'Source Clip offset (frames)': 0,
                    'StartTime (frames)': 0, 'Effect Name': effect_name, 'Keyframe Details': keyframe_details,
                    'Orig Source Clip length': e['Length'] # ADDED
                })
            elif is_pz_filler:
                fpath = e.get('FilePath', 'N/A')
                fname = os.path.basename(fpath) if fpath != 'N/A' else 'PanZoom_Filler'
                enriched.append({
                    'Event': idx, 'Event Name': 'Pan & Zoom on Filler', 'Clip Name': fname, 'Source File Name': fname,
                    'Source File Path': os.path.dirname(fpath).replace('\\', '/') if fpath != 'N/A' else 'N/A',
                    'DiskLabel': 'N/A', 'TapeID': 'N/A', 'SourceMobID': 'FX_ON_FILLER', 'TrackID': 'VFX',
                    'Source Clip EditRate': e['TimelineEditRate'],
                    'Timeline Start TC': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                    'Source Clip start time code': 'N/A', 'Source Clip offset': 'N/A', 'StartTime': 'N/A', 'End Time': 'N/A',
                    'Event Length': e['Length'], 'Source Clip start (frames)': 0, 'Source Clip offset (frames)': 0,
                    'StartTime (frames)': 0, 'Effect Name': effect_name, 'Keyframe Details': keyframe_details,
                    'Orig Source Clip length': e['Length'] # ADDED
                })
            elif is_generic_filler:
                fpath = e.get('FilePath', '')
                base = effect_name.split(':')[-1].strip().lower()
                base = re.sub(r'[^0-9a-z]+', '_', base)
                placeholder = f"{base}_placeholder.png"
                enriched.append({
                    'Event': idx, 'Event Name': f"{effect_name} on Filler", 'Clip Name': placeholder, 'Source File Name': placeholder,
                    'Source File Path': os.path.join('placeholders', placeholder),
                    'DiskLabel': 'N/A', 'TapeID': 'N/A', 'SourceMobID': 'FX_ON_FILLER', 'TrackID': 'VFX',
                    'Source Clip EditRate': e['TimelineEditRate'],
                    'Timeline Start TC': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                    'Source Clip start time code': '01:00:00:00', 'Source Clip offset': '0',
                    'StartTime': '01:00:00:00', 'End Time': '01:00:00:00', 'Event Length': e['Length'],
                    'Source Clip start (frames)': 0, 'Source Clip offset (frames)': 0, 'StartTime (frames)': 0,
                    'Effect Name': effect_name, 'Keyframe Details': keyframe_details,
                    'Orig Source Clip length': e['Length'] # ADDED
                })
            else:
                initial = mob_map.get(e['MobID'])
                final = get_genuine_source_info(e['MobID'], mob_map)
                md_master = extract_metadata(initial)
                md_final = extract_metadata(final)
                md = md_final.copy()
                md['TapeID'] = md_master.get('TapeID') or md_final.get('TapeID')
                md['DiskLabel'] = md_master.get('DiskLabel') or md_final.get('DiskLabel')
                
                # --- ADDED: Get source duration from final mob ---
                orig_length = 0
                if final:
                    slots = final[3] if len(final) > 3 else []
                    first_slot_list = next((s for s in slots if s[0] == 'Slots'), [None,None,None,[]])[3]
                    if first_slot_list:
                        orig_length = next((l[2] for l in first_slot_list[0][3] if l[0] == 'Length'), 0)
                # --- END OF ADDED LOGIC ---

                src_fname, src_path = 'N/A', 'N/A'
                url = md_final.get('URLString', '')
                if url:
                    try:
                        dec = urllib.parse.unquote(urllib.parse.urlparse(url).path)
                        src_fname = os.path.basename(dec)
                        src_path = os.path.dirname(dec)
                    except: src_fname = 'Path Error'
                gsfr = md_final['GenuineStartFrames']
                off = e['SourceOffsetFrames']
                start_frames = gsfr + off
                end_frames = start_frames + e['Length']
                serate = md_final.get('SourceEditRate') or e['TimelineEditRate']
                sdrop = md_final.get('IsDropFrame', False)
                enriched.append({
                    'Event': idx, 'Event Name': src_fname, 'Clip Name': src_fname, 'Source File Name': src_fname,
                    'Source File Path': src_path, 'DiskLabel': md.get('DiskLabel'), 'TapeID': md.get('TapeID'),
                    'SourceMobID': e['MobID'], 'TrackID': e.get('SourceTrackID', 'N/A'),
                    'Source Clip EditRate': serate,
                    'Timeline Start TC': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                    'Source Clip start time code': frames_to_tc(gsfr, serate, sdrop),
                    'Source Clip offset': frames_to_tc(off, serate, sdrop),
                    'StartTime': frames_to_tc(start_frames, serate, sdrop), 'End Time': frames_to_tc(end_frames, serate, sdrop),
                    'Event Length': e['Length'], 'Source Clip start (frames)': gsfr,
                    'Source Clip offset (frames)': off, 'StartTime (frames)': start_frames,
                    'Effect Name': effect_name, 'Keyframe Details': keyframe_details,
                    'Orig Source Clip length': orig_length
                })
        
        self.log_msg("\n--- Timeline Summary ---")
        for k, v in summary_info.items():
            self.log_msg(f"  {k}: {v}")
        
        # --- MODIFIED: Use sequence name in filename ---
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sanitized_seq_name = re.sub(r'[\\/*?:"<>|]', "", sequence_name)
        default_filename = f"{sanitized_seq_name}_super_edl_fx_report_v2_{timestamp}.csv"
        
        out_csv = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_filename, filetypes=[("CSV files", "*.csv")]
        )
        
        if not out_csv: self.log_msg("❌ Save cancelled."); return
        
        try:
            with open(out_csv, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Timeline Summary"])
                for k, v in summary_info.items(): writer.writerow([k, v])
                writer.writerow([])
                if enriched:
                    hdr = list(enriched[0].keys())
                    writer.writerow(hdr)
                    for row in enriched: writer.writerow([row.get(h) for h in hdr])
            self.log_msg(f"✅ Analysis complete. Full report saved to: {os.path.basename(out_csv)}")
            messagebox.showinfo("Done", "Report generated successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save CSV report: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()