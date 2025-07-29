import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
import re

# --- Helper Functions ---

def sanitize_for_xml(text):
    if not isinstance(text, str): return ""
    clean = text.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[\x00-\x1F\x7F]', '', clean).strip()

def frames_to_fcpxml_time(frames, rate):
    return f"{int(frames)}/{int(round(rate))}s"

def parse_keyframe_details_for_xml(kf_string):
    if not kf_string or kf_string in ["N/A", "No animated parameters found."]: return {}
    params = {}
    current_param = None
    for line in kf_string.strip().split('\n'):
        line = line.strip()
        param_match = re.match(r'-\s*Parameter:\s*(.+?)\s*\(', line)
        kf_match = re.search(r'Keyframe at .*?\((\d+)f\)\s*->\s*Value:\s*(.*)', line)
        if param_match:
            current_param = sanitize_for_xml(param_match.group(1))
            params[current_param] = []
        elif kf_match and current_param:
            frame, value_str = kf_match.groups()
            try:
                value = float(value_str.split('/')[0]) / float(value_str.split('/')[1]) if '/' in value_str else float(value_str)
            except (ValueError, ZeroDivisionError): value = value_str
            params[current_param].append({'frame': int(frame), 'value': value})
    return params

def get_frames_from_tc_string(tc_string):
    match = re.search(r'\((\d+)f\)', tc_string)
    return int(match.group(1)) if match else 0

def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    if frame_count is None or fps is None or fps <= 0: return "N/A"
    try:
        separator = ";" if is_drop_frame else ":"
        fc, int_fps = int(frame_count), round(float(fps))
        if int_fps == 0: return "N/A"
        h, m, s, f = fc//(3600*int_fps), (fc%(3600*int_fps))//(60*int_fps), (fc%(60*int_fps))//int_fps, fc%int_fps
        return f"{h:02}:{m:02}:{s:02}{separator}{f:02}"
    except (ValueError, TypeError): return "N/A"

# --- Core AAF Parsing (Based on your superEDLguiFX_v2.py)---

def create_mob_map(node, mob_map=None):
    if mob_map is None: mob_map = {}
    if isinstance(node, list) and len(node) > 1:
        children = node[3] if len(node) > 3 else []
        if any(isinstance(c, list) and c[0] == "MobID" for c in children):
            mob_id = next((c[2] for c in children if isinstance(c, list) and c[0] == "MobID"), None)
            if mob_id: mob_map[mob_id] = node
        for c in children: create_mob_map(c, mob_map)
    return mob_map

def find_main_sequence_mob_and_start_tc(root_node):
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None, 0, False
    for mob in root_node[3]:
        if not (isinstance(mob, list) and len(mob) > 3): continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not (slots_node and len(slots_node) > 3): continue
        is_sequence_mob = any(isinstance(s, list) and len(s) > 3 and isinstance(next((c for c in s[3] if c[0] == "Segment"), None), list) and len(seg_node := next((c for c in s[3] if c[0] == "Segment"), None)) > 3 and seg_node[3] and isinstance(seg_node[3][0], list) and seg_node[3][0][0] == "Sequence" for s in slots_node[3])
        if is_sequence_mob:
            start_tc, is_drop = 0, False
            for s in slots_node[3]:
                 if isinstance(s, list) and len(s) > 3:
                    seg = next((c for c in s[3] if c[0] == "Segment"), None)
                    if seg and len(seg) > 3 and seg[3] and isinstance(seg[3][0], list) and seg[3][0][0] == "Timecode":
                        tc_node = seg[3][0]
                        start_node = next((c for c in tc_node[3] if c[0] == "Start"), None)
                        drop_node = next((c for c in tc_node[3] if c[0] == "Drop"), None)
                        if drop_node and len(drop_node) > 2: is_drop = bool(drop_node[2])
                        if start_node and len(start_node) > 2:
                            try: start_tc = int(start_node[2]); break
                            except (ValueError, TypeError): continue
            return mob, start_tc, is_drop
    return None, 0, False

def extract_metadata(mob_node):
    metadata = {"URLString": "", "TapeID": "", "DiskLabel": "", "SourceEditRate": None, "GenuineStartFrames": 0, "IsDropFrame": False}
    if not mob_node: return metadata
    all_starts = []
    def recursive_extract(n):
        if not isinstance(n, list): return
        node_name, children = n[0], n[3] if len(n) > 3 else []
        if node_name in ("Start", "StartTime") and len(n) > 2:
            try: all_starts.append(int(n[2]))
            except (ValueError, TypeError): pass
        elif node_name == "URLString" and len(n) > 2: metadata["URLString"] = n[2]
        elif node_name == "EditRate" and len(n) > 2:
            try:
                rate_str = str(n[2])
                if "/" in rate_str: num, den = map(float, rate_str.split('/')); metadata["SourceEditRate"] = num / den if den != 0 else 0
                else: metadata["SourceEditRate"] = float(rate_str)
            except (ValueError, TypeError, ZeroDivisionError): pass
        elif node_name == "Drop" and len(n) > 2: metadata["IsDropFrame"] = bool(n[2])
        elif node_name == "TapeID" and len(n) > 3 and not metadata["TapeID"]: metadata["TapeID"] = next((c[2] for c in children if c[0] == "Value"), "")
        elif node_name in ("DiskLabel", "_IMPORTDISKLAB") and len(n) > 3 and not metadata["DiskLabel"]: metadata["DiskLabel"] = next((c[2] for c in children if c[0] == "Value"), "")
        elif node_name == "MobAttributeList":
             for attr in children:
                 if isinstance(attr, list) and len(attr) > 3:
                     attr_name = next((c[2] for c in attr[3] if c[0] == "Name"), "")
                     attr_val = next((c[2] for c in attr[3] if c[0] == "Value"), "")
                     if attr_name == "TapeID" and not metadata["TapeID"]: metadata["TapeID"] = attr_val
                     if attr_name == "DiskLabel" and not metadata["DiskLabel"]: metadata["DiskLabel"] = attr_val
        for child in children: recursive_extract(child)
    recursive_extract(mob_node)
    if all_starts: metadata["GenuineStartFrames"] = max(all_starts)
    return metadata

def has_nested_source_clip(node):
    if not isinstance(node, list): return False
    if node[0] == "SourceClip": return True
    children = node[3] if len(node) > 3 else []
    for child in children:
        if has_nested_source_clip(child): return True
    return False

def decode_filepath(filepath_node):
    try:
        value_node = next((c for c in (filepath_node[3] if len(filepath_node) > 3 else []) if c[0] == "Value" and isinstance(c[2], list)), None)
        if not value_node: return "Path data not found."
        raw_bytes = bytes(b for b in value_node[2] if isinstance(b, int))
        txt = raw_bytes.decode("utf-16-le", errors="ignore")
        idx = txt.find('\\')
        if idx != -1: txt = txt[idx:]
        cleaned = txt.rstrip('\x00').replace('\\', '/')
        return cleaned or "(decoded to empty string)"
    except Exception as e: return f"Error decoding path: {e}"

def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, dedupe_set=None):
    if results is None: results = []
    if dedupe_set is None: dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2: return results
    name, children = node[0], node[3] if len(node) > 3 else []
    if name in ["Data Track", "Sound"] or any(isinstance(name, str) and name.startswith(p) for p in ["A1", "A2", "A3", "A4"]): return results
    if name == "Sequence":
        components_node = next((c for c in children if c[0] == "Components"), None)
        if components_node and len(components_node) > 3:
            for comp in components_node[3]:
                recursive_search(comp, timeline_offset, edit_rate, results, dedupe_set)
                if isinstance(comp, list) and len(comp) > 3:
                    timeline_offset += next((int(x[2]) for x in comp[3] if x[0] == "Length"), 0)
    elif name == "SourceClip":
        mobid = next((c[2] for c in children if c[0] == "SourceID"), None)
        track_id = next((c[2] for c in children if c[0] == "SourceTrackID"), "N/A")
        offset = next((int(c[2]) for c in children if c[0] == "Start" or c[0] == "StartTime"), 0)
        dedupe_key = (mobid, timeline_offset, offset)
        if mobid and dedupe_key not in dedupe_set:
            dedupe_set.add(dedupe_key)
            results.append({"MobID": mobid, "SourceTrackID": track_id, "TimelineStartFrame": timeline_offset, "SourceOffsetFrames": offset, "Length": next((int(c[2]) for c in children if c[0] == "Length"), 0), "TimelineEditRate": edit_rate})
    elif name == "OperationGroup":
        def find_filepath(subnode):
            if not isinstance(subnode, list): return None
            if subnode[0] == "Filepath": return decode_filepath(subnode)
            children = subnode[3] if len(subnode) > 3 else []
            for c in children:
                p = find_filepath(c)
                if p: return p
            return None
        if not has_nested_source_clip(node):
            length = next((int(c[2]) for c in children if c[0] == "Length"), 0)
            file_path = find_filepath(node) or "N/A"
            if length > 0:
                 results.append({"MobID": "PanZoomFiller", "TimelineStartFrame": timeline_offset, "SourceOffsetFrames": 0, "Length": length, "TimelineEditRate": edit_rate, "FilePath": file_path})
        for c in children: recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    else:
        for c in children: recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    return results

def get_genuine_source_info(mob_id, mob_map, visited=None):
    if visited is None: visited = set()
    if mob_id in visited: return None
    visited.add(mob_id)
    mob = mob_map.get(mob_id)
    if not mob: return None
    next_mob_id = None
    slots_node = next((c for c in mob[3] if c[0] == "Slots"), None)
    if slots_node and len(slots_node) > 3:
        for slot in slots_node[3]:
            if len(slot) > 3:
                segment = next((c for c in slot[3] if c[0] == "Segment"), None)
                if segment and len(segment) > 3 and segment[3] and isinstance(segment[3][0], list) and segment[3][0][0] == "SourceClip":
                    next_mob_id = next((c[2] for c in segment[3][0][3] if c[0] == "SourceID"), None)
                    break
    if next_mob_id:
        final_mob = get_genuine_source_info(next_mob_id, mob_map, visited)
        if final_mob: return final_mob
    return mob

# --- NEW: Effect finding and parsing functions ---
def find_timeline_effects(node, timeline_offset=0, results_list=None):
    if results_list is None: results_list = []
    if not isinstance(node, list) or len(node) < 2: return results_list
    name, children = node[0], node[3] if len(node) > 3 else []
    if name == 'Sequence':
        comps = next((c for c in children if c[0] == 'Components'), None)
        if comps and len(comps) > 3:
            for comp in comps[3]:
                find_timeline_effects(comp, timeline_offset, results_list)
                ln = next((c for c in comp[3] if c[0] == 'Length'), None)
                if ln and len(ln) > 2:
                    try: timeline_offset += int(ln[2])
                    except: pass
    elif name == 'OperationGroup':
        results_list.append({'node': node, 'start_frame': timeline_offset})
        return results_list
    else:
        for child in children: find_timeline_effects(child, timeline_offset, results_list)
    return results_list

def extract_effect_details(node):
    if node is None: return {'effect_name': 'N/A', 'length': 0, 'animated_params': {}}
    all_attrs = {}
    def collect(n):
        if not isinstance(n, list): return
        if n[0] == 'ComponentAttributeList' and len(n) > 3:
            for a in n[3]:
                if isinstance(a, list):
                    v = next((x for x in a[3] if x[0]=='Value'), None)
                    if v and len(v)>2: all_attrs[a[0]] = v[2]
        for c in (n[3] if len(n)>3 else []): collect(c)
    collect(node)
    plugin_name, plugin_class = all_attrs.get('_EFFECT_PLUGIN_NAME'), all_attrs.get('_EFFECT_PLUGIN_CLASS')
    if plugin_class and plugin_name: effect_name = f"{plugin_class} : {plugin_name}"
    elif plugin_name: effect_name = plugin_name
    else:
        op = next((c for c in node[3] if c[0]=='Operation'), None)
        if op and len(op)>2 and isinstance(op[2], str):
            raw = op[2]
            name_part = raw.split(" ")[1] if " " in raw else raw
            effect_name = name_part.replace('_v2','').replace('_2','').replace('_',' ').strip()
        else: effect_name = 'Unknown Effect'
    length = 0
    ln = next((c for c in node[3] if c[0]=='Length'), None)
    if ln and len(ln)>2:
        try: length = int(ln[2])
        except: pass
    animated = {}
    pn = next((c for c in node[3] if c[0]=='Parameters'), None)
    if pn and len(pn)>3:
        for p in pn[3]:
            pname = next((x[2] for x in p[3] if x[0]=='Name'), p[0])
            plist = next((x for x in p[3] if x[0]=='PointList'), None)
            kfs = []
            if plist and len(plist)>3:
                for cp in plist[3]:
                    if isinstance(cp, list) and cp[0]=='ControlPoint':
                        t, v = next((x[2] for x in cp[3] if x[0]=='Time'), '0'), next((x[2] for x in cp[3] if x[0]=='Value'), 'N/A')
                        kfs.append({'time': t, 'value': v})
            if kfs: animated[pname] = kfs
    return {'effect_name': effect_name, 'length': length, 'animated_params': animated}

# --- FCPXML Snippet Generation ---

def generate_fcpxml_snippet(event, asset_map, timeline_rate):
    src_path = event.get("Source File Path")
    asset_id_ref = asset_map.get(src_path)
    clip_name = sanitize_for_xml(event.get("Clip Name"))
    clip_duration_frames = int(event.get('Event Length', 0))
    clip_timeline_start_frames = get_frames_from_tc_string(event['Timeline Start TC'])
    
    clip_element = None
    if asset_id_ref:
        clip_source_start_frames = int(event.get('StartTime (frames)', 0))
        clip_element = Element('asset-clip', ref=asset_id_ref, name=clip_name,
                                duration=frames_to_fcpxml_time(clip_duration_frames, timeline_rate),
                                start=frames_to_fcpxml_time(clip_source_start_frames, timeline_rate),
                                offset=frames_to_fcpxml_time(clip_timeline_start_frames, timeline_rate))
    else:
        clip_element = Element('gap', name=clip_name,
                                duration=frames_to_fcpxml_time(clip_duration_frames, timeline_rate))

    animated_params = parse_keyframe_details_for_xml(event.get('Keyframe Details', ''))
    if animated_params and clip_element is not None:
        pos_x_param, pos_y_param, scale_x_param, scale_y_param = None, None, None, None
        for p_name, kfs in animated_params.items():
            if 'POS_X' in p_name: pos_x_param = (p_name, kfs)
            if 'POS_Y' in p_name: pos_y_param = (p_name, kfs)
            if 'SCALE_X' in p_name or 'Zoom Factor' in p_name: scale_x_param = (p_name, kfs)
            if 'SCALE_Y' in p_name or 'Zoom Factor' in p_name: scale_y_param = (p_name, kfs)

        if pos_x_param and pos_y_param:
            param_pos = SubElement(clip_element, 'param', name='Position', key='9999/999166631/1/80/2')
            for kf_x in pos_x_param[1]:
                y_val = next((kf_y['value'] for kf_y in pos_y_param[1] if kf_y['frame'] == kf_x['frame']), 0)
                if 'DVE_' in pos_y_param[0]: y_val *= -1
                kf_time_relative = kf_x['frame'] - clip_timeline_start_frames
                kf_time = frames_to_fcpxml_time(kf_time_relative, timeline_rate)
                SubElement(param_pos, 'keyframe', time=kf_time, value=f"{kf_x['value']} {y_val}", interp='linear')
        
        if scale_x_param and scale_y_param:
            param_scale = SubElement(clip_element, 'param', name='Scale', key='9999/999166631/1/83/2')
            for kf_x in scale_x_param[1]:
                is_zoom_factor = 'Zoom Factor' in scale_x_param[0]
                scale_val = kf_x['value'] if is_zoom_factor else kf_x['value'] / 100.0
                kf_time_relative = kf_x['frame'] - clip_timeline_start_frames
                kf_time = frames_to_fcpxml_time(kf_time_relative, timeline_rate)
                SubElement(param_scale, 'keyframe', time=kf_time, value=f"{scale_val*100} {scale_val*100}", interp='linear')
    
    xml_string = tostring(clip_element, encoding='unicode')
    return minidom.parseString(xml_string).toprettyxml(indent="  ").replace('<?xml version="1.0" ?>\n', '')


# --- GUI Application ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("SuperEDL + FCPXML Snippet Generator (v5 FINAL)")
        # ... (rest of GUI is the same as previous versions)
        self.root.geometry("1200x800")
        tk.Button(root, text="Load AAF Export (JSON) File", command=self.load_json).pack(pady=10)
        self.filename_label = tk.Label(root, text="No file loaded.", fg="grey")
        self.filename_label.pack(pady=2)
        self.generate_button = tk.Button(root, text="Generate Super EDL + Snippets", command=self.process, state=tk.DISABLED)
        self.generate_button.pack(pady=5)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select AAF as JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            self.json_path = path
            try:
                with open(path, "r", encoding="utf-8") as f: self.json_data = json.load(f)
                self.log.delete(1.0, tk.END)
                self.log_msg(f"✅ Loaded JSON file:\n{path}")
                self.filename_label.config(text=os.path.basename(path), fg="black")
                self.generate_button.config(state=tk.NORMAL)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load or parse JSON file:\n{e}")

    def process(self):
        if not hasattr(self, 'json_data'):
            messagebox.showerror("Error", "Please load a file first."); return
        output_dir = filedialog.askdirectory(title="Select Output Directory for CSV Report")
        if not output_dir: self.log_msg("❌ Report generation cancelled."); return
        
        self.log.delete(1.0, tk.END)
        self.log_msg("1. Building Mob map...")
        mob_map = create_mob_map(self.json_data)
        
        self.log_msg("2. Finding main sequence...")
        sequence_mob, start_tc, is_drop_frame = find_main_sequence_mob_and_start_tc(self.json_data)
        if not sequence_mob: self.log_msg("❌ Could not find main sequence."); return
        
        timeline_rate = 25.0
        slots = next((c for c in sequence_mob[3] if c[0] == "Slots"), None)
        if slots and len(slots) > 3:
            for slot in slots[3]:
                if isinstance(slot, list) and len(slot) > 3:
                    rate_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "EditRate"), None)
                    if rate_node and len(rate_node) > 2:
                        try:
                            rate_str = str(rate_node[2])
                            if "/" in rate_str: num, den = map(float, rate_str.split('/')); timeline_rate = num / den if den != 0 else 0
                            else: timeline_rate = float(rate_str)
                            break 
                        except: continue

        self.log_msg("3. Extracting timeline events...")
        events = recursive_search(sequence_mob, timeline_offset=start_tc, edit_rate=timeline_rate)
        
        unique_events = []
        seen_keys = set()
        for e in events:
            key = (e.get("MobID"), e.get("TimelineStartFrame"), e.get("SourceOffsetFrames"), e.get("Length"))
            if key not in seen_keys: unique_events.append(e); seen_keys.add(key)
        events = unique_events

        self.log_msg("4. Enriching events with SuperEDL data...")
        enriched = []
        for idx, e in enumerate(events, 1):
            # Original, full enrichment from your v2 script
            if e.get("MobID") == "PanZoomFiller":
                file_path = e.get("FilePath", "N/A")
                source_file_name = os.path.basename(file_path) if file_path != "N/A" and file_path else "Effect on Filler"
                enriched.append({"Event": idx, "Event Name": "Pan & Zoom Effect", "Clip Name": source_file_name, "Source File Name": source_file_name, "Source File Path": os.path.dirname(file_path).replace("\\", "/") if file_path != "N/A" and file_path else "N/A", "DiskLabel": "N/A", "TapeID": "N/A", "SourceMobID": "Effect", "TrackID": "VFX", "Source Clip EditRate": e["TimelineEditRate"], "Timeline Start TC": frames_to_tc(e["TimelineStartFrame"], timeline_rate, is_drop_frame), "Source Clip start time code": "N/A", "Source Clip offset": "N/A", "StartTime": "N/A", "End Time": "N/A", "Event Length": e["Length"], "Source Clip start (frames)": 0, "Source Clip offset (frames)": 0, "StartTime (frames)": 0})
                continue
            initial_mob, final_source_mob = mob_map.get(e["MobID"]), get_genuine_source_info(e["MobID"], mob_map)
            master_md, final_md = extract_metadata(initial_mob), extract_metadata(final_source_mob)
            md = {**final_md, "TapeID": master_md.get("TapeID") or final_md.get("TapeID"), "DiskLabel": master_md.get("DiskLabel") or final_md.get("DiskLabel")}
            source_file_name, source_path = "N/A", "N/A"
            if final_source_mob:
                path_url = final_md.get("URLString", "")
                if path_url:
                    try: decoded_path = urllib.parse.unquote(urllib.parse.urlparse(path_url).path); source_file_name, source_path = os.path.basename(decoded_path), os.path.dirname(decoded_path)
                    except: source_file_name = "Path Error"
            event_start_frames, source_edit_rate, source_is_drop = md['GenuineStartFrames'] + e['SourceOffsetFrames'], md.get("SourceEditRate") or e["TimelineEditRate"], md.get("IsDropFrame", False)
            enriched.append({"Event": idx, "Event Name": source_file_name, "Clip Name": source_file_name, "Source File Name": source_file_name, "Source File Path": source_path, "DiskLabel": md.get("DiskLabel"), "TapeID": md.get("TapeID"), "SourceMobID": e["MobID"], "TrackID": e.get("SourceTrackID", "N/A"), "Source Clip EditRate": source_edit_rate, "Timeline Start TC": frames_to_tc(e["TimelineStartFrame"], timeline_rate, is_drop_frame), "Source Clip start time code": frames_to_tc(md["GenuineStartFrames"], source_edit_rate, source_is_drop), "Source Clip offset": frames_to_tc(e["SourceOffsetFrames"], source_edit_rate, source_is_drop), "StartTime": frames_to_tc(event_start_frames, source_edit_rate, source_is_drop), "End Time": frames_to_tc(event_start_frames + e["Length"], source_edit_rate, source_is_drop), "Event Length": e["Length"], "Source Clip start (frames)": md["GenuineStartFrames"], "Source Clip offset (frames)": e["SourceOffsetFrames"], "StartTime (frames)": event_start_frames})

        self.log_msg("5. Adding effect data and FCPXML Snippets...")
        all_effects = find_timeline_effects(sequence_mob, timeline_offset=start_tc)
        effects_by_frame = {effect['start_frame']: extract_effect_details(effect['node']) for effect in all_effects}
        asset_map = {event.get("Source File Path"): f'r{i+1}' for i, event in enumerate(enriched) if event.get("Source File Path") and event.get("Source File Path") != "N/A"}

        for event in enriched:
            event_start_frame = get_frames_from_tc_string(event['Timeline Start TC'])
            effect_data = effects_by_frame.get(event_start_frame, {})
            event['Effect Name'] = effect_data.get('effect_name', 'N/A')
            kf_strings = []
            if effect_data.get('animated_params'):
                for pname, kfs in effect_data['animated_params'].items():
                    kf_strings.append(f"  - Parameter: {pname} ({len(kfs)} keyframes)")
                    for kp in kfs:
                        try:
                            fx_len = effect_data.get('length', event['Event Length'])
                            t, v = float(kp['time']), kp['value']
                            off = int(t * (fx_len - 1)) if fx_len > 1 else 0
                            abs_frame = event_start_frame + off
                            kf_strings.append(f"    Keyframe at {frames_to_tc(abs_frame, timeline_rate, is_drop_frame)} ({abs_frame}f) -> Value: {v}")
                        except: kf_strings.append(f"    Keyframe at Time: {kp['time']} -> Value: {kp['value']}")
            event['Keyframe Details'] = "\n".join(kf_strings) or "N/A"
            try:
                event['FCPXML Snippet'] = generate_fcpxml_snippet(event, asset_map, timeline_rate)
            except Exception as ex:
                event['FCPXML Snippet'] = f"<error>Could not generate snippet: {ex}</error>"

        self.log_msg("\n--- Event Details with FCPXML Snippets ---")
        for r in enriched:
            self.log_msg(f"----------------------------------------")
            self.log_msg(f"Event: {r['Event']} | {r['Event Name']}")
            # Restore all original fields to the log
            if r.get("SourceMobID") != "Effect":
                 self.log_msg(f"  TapeID: {r.get('TapeID', 'N/A')} | DiskLabel: {r.get('DiskLabel', 'N/A')}")
                 self.log_msg(f"  Source Path: {r.get('Source File Path', 'N/A')}")
                 self.log_msg(f"  ...")
                 self.log_msg(f"  Source Start: {r.get('Source Clip start time code', 'N/A')}")
                 self.log_msg(f"    + Offset: {r.get('Source Clip offset', 'N/A')}")
                 self.log_msg(f"  = Event In: {r.get('StartTime', 'N/A')}")
            self.log_msg(f"  Timeline In: {r['Timeline Start TC']} | Length: {r['Event Length']} frames")
            self.log_msg(f"  Effect Applied: {r['Effect Name']}")
            self.log_msg(f"--- Keyframe Details ---\n{r.get('Keyframe Details', 'N/A')}")
            self.log_msg(f"--- FCPXML Snippet ---\n{r['FCPXML Snippet']}")

        out_path = os.path.join(output_dir, f"super_edl_report_with_snippets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        try:
            if enriched:
                header = list(enriched[0].keys())
                with open(out_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=header)
                    writer.writeheader()
                    writer.writerows(enriched)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save CSV report: {e}")
        else:
            self.log_msg(f"\n✅ Analysis complete. Full report with snippets in:\n{os.path.basename(out_path)}")
            messagebox.showinfo("Done", "Report with FCPXML snippets generated successfully.")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()