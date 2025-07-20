import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode and Parsing Functions ---

def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    """
    Converts a frame count to a timecode string. This version from the effects
    analyzer is used as it robustly handles fps and drop_frame arguments.
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
    """
    Finds the main sequence, its start timecode, edit rate, and drop frame status.
    This comprehensive version is from the effect analyzer script.
    """
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
                if "/" in rate_str:
                    num, den = map(float, rate_str.split('/'))
                    metadata["SourceEditRate"] = num / den if den != 0 else 0
                else: metadata["SourceEditRate"] = float(rate_str)
            except (ValueError, TypeError, ZeroDivisionError): pass
        elif node_name == "Drop" and len(n) > 2: metadata["IsDropFrame"] = bool(n[2])
        elif node_name == "TapeID" and len(n) > 3 and not metadata["TapeID"]:
            metadata["TapeID"] = next((c[2] for c in children if c[0] == "Value"), "")
        elif node_name in ("DiskLabel", "_IMPORTDISKLAB") and len(n) > 3 and not metadata["DiskLabel"]:
            metadata["DiskLabel"] = next((c[2] for c in children if c[0] == "Value"), "")
        elif node_name == "MobAttributeList":
             for attr in children:
                 if isinstance(attr, list) and len(attr) > 3:
                     attr_name = next((c[2] for c in attr[3] if c[0] == "Name"), "")
                     attr_val = next((c[2] for c in attr[3] if c[0] == "Value"), "")
                     if attr_name == "TapeID" and not metadata["TapeID"]: metadata["TapeID"] = attr_val
                     if attr_name == "DiskLabel" and not metadata["DiskLabel"]: metadata["DiskLabel"] = attr_val
        for child in children: recursive_extract(child)
    recursive_extract(mob_node)
    if all_starts:
        metadata["GenuineStartFrames"] = max(all_starts)
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

def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, dedupe_set=None):
    if results is None: results = []
    if dedupe_set is None: dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2: return results
    
    name, children = node[0], node[3] if len(node) > 3 else []
    
    if name in ["Data Track", "Sound"] or any(isinstance(name, str) and name.startswith(p) for p in ["A1", "A2", "A3", "A4"]):
        return results

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
            results.append({
                "MobID": mobid, "SourceTrackID": track_id,
                "TimelineStartFrame": timeline_offset, "SourceOffsetFrames": offset,
                "Length": next((int(c[2]) for c in children if c[0] == "Length"), 0),
                "TimelineEditRate": edit_rate
            })
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
                 results.append({
                    "MobID": "PanZoomFiller", "TimelineStartFrame": timeline_offset,
                    "SourceOffsetFrames": 0, "Length": length,
                    "TimelineEditRate": edit_rate, "FilePath": file_path
                })
        for c in children:
            recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
            
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

# --- NEW: Functions from simplefxGPT4.py for effect analysis ---

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
                        record = True; break
        if record:
            results_list.append({'node': node, 'start_frame': timeline_offset})
        return results_list
    else:
        for child in children:
            find_timeline_effects(child, timeline_offset, results_list)
    return results_list

def extract_effect_details(node):
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
    plugin_name  = all_attrs.get('_EFFECT_PLUGIN_NAME')
    plugin_class = all_attrs.get('_EFFECT_PLUGIN_CLASS')
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
                        t = next((x[2] for x in cp[3] if x[0]=='Time'), '0')
                        v = next((x[2] for x in cp[3] if x[0]=='Value'), 'N/A')
                        kfs.append({'time': t, 'value': v})
            if kfs: animated[pname] = kfs
    return {'effect_name': effect_name, 'length': length, 'animated_params': animated}

# --- Main Application Class ---

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Super EDL + FX Generator v1")
        self.root.geometry("1200x800")
        tk.Button(root, text="Load AAF Export (JSON) File", command=self.load_json).pack(pady=10)
        self.filename_label = tk.Label(root, text="No file loaded.", fg="grey")
        self.filename_label.pack(pady=2)
        self.generate_button = tk.Button(root, text="Generate Super EDL + FX", command=self.process, state=tk.DISABLED)
        self.generate_button.pack(pady=5)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(pady=5, padx=10, expand=True, fill=tk.BOTH)

    def log_msg(self, msg): self.log.insert(tk.END, msg + "\n"); self.log.see(tk.END)

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
                self.filename_label.config(text="Failed to load file.", fg="red")
                self.generate_button.config(state=tk.DISABLED)

    def process(self):
        if not hasattr(self, 'json_data') or not self.json_data:
            messagebox.showerror("Error", "Please load a file first."); return
        output_dir = filedialog.askdirectory(title="Select Output Directory")
        if not output_dir: self.log_msg("❌ Report generation cancelled."); return
        
        self.log.delete(1.0, tk.END)
        self.log_msg("1. Building Mob map..."); mob_map = create_mob_map(self.json_data)
        
        self.log_msg("2. Finding main sequence..."); 
        sequence_mob, start_tc, timeline_rate, is_drop_frame = find_main_sequence_mob_and_start_tc(self.json_data)
        if not sequence_mob: 
            self.log_msg("❌ Could not find main sequence."); return
        
        self.log_msg("3. Scanning for all timeline effects and keyframes...");
        all_effects = find_timeline_effects(sequence_mob, timeline_offset=start_tc)
        effects_by_frame = {}
        for effect in all_effects:
            details = extract_effect_details(effect['node'])
            start_frame = effect['start_frame']
            # Store details in the lookup dictionary
            effects_by_frame[start_frame] = details
        self.log_msg(f"   Found {len(effects_by_frame)} effects with keyframes.")

        self.log_msg("4. Extracting primary timeline events (EDL)..."); 
        events = recursive_search(sequence_mob, timeline_offset=start_tc, edit_rate=timeline_rate)
        
        unique_events = []
        seen_keys = set()
        for e in events:
            key = (e.get("MobID"), e.get("TimelineStartFrame"), e.get("SourceOffsetFrames"), e.get("Length"))
            if key not in seen_keys:
                unique_events.append(e)
                seen_keys.add(key)
        events = unique_events

        total_length_frames = sum(e['Length'] for e in events)

        summary_info = {
            "Timeline Name": next((c[2] for c in sequence_mob[3] if c[0] == "Name"), "N/A"),
            "Timeline Edit Rate": f"{timeline_rate} {'(DF)' if is_drop_frame else '(NDF)'}",
            "Timeline Start": frames_to_tc(start_tc, timeline_rate, is_drop_frame),
            "Timeline Length": frames_to_tc(total_length_frames, timeline_rate, is_drop_frame) + f" ({total_length_frames} frames)",
            "Total number of EDL events found": len(events),
            "Total number of unique sources": len({e["MobID"] for e in events if e.get("MobID") != "PanZoomFiller"})
        }
        
        self.log_msg("5. Enriching events with source and effect data...")
        enriched = []
        for idx, e in enumerate(events, 1):
            # --- NEW: Check for corresponding effect data ---
            event_start_frame = e["TimelineStartFrame"]
            effect_data = effects_by_frame.get(event_start_frame)
            effect_name = "N/A"
            keyframe_details = "N/A"

            if effect_data:
                effect_name = effect_data['effect_name']
                kf_strings = []
                for pname, kfs in effect_data['animated_params'].items():
                    kf_strings.append(f"  - Parameter: {pname} ({len(kfs)} keyframes)")
                    for kp in kfs:
                        try:
                            # Use the effect's own length for normalized time calculation
                            fx_len = effect_data['length']
                            t = float(kp['time'])
                            off = int(t * (fx_len - 1)) if fx_len > 1 else 0
                            abs_frame = event_start_frame + off
                            kf_strings.append(f"    Keyframe at {frames_to_tc(abs_frame, timeline_rate, is_drop_frame)} ({abs_frame}f) -> Value: {kp['value']}")
                        except:
                             kf_strings.append(f"    Keyframe at Time: {kp['time']} -> Value: {kp['value']}")
                keyframe_details = "\n".join(kf_strings) if kf_strings else "No animated parameters found."

            # --- Existing EDL Logic ---
            if e.get("MobID") == "PanZoomFiller":
                file_path = e.get("FilePath", "N/A")
                source_file_name = os.path.basename(file_path) if file_path != "N/A" and file_path else "Effect on Filler"
                enriched.append({
                    "Event": idx, "Event Name": "Pan & Zoom Effect", "Clip Name": source_file_name,
                    "Source File Name": source_file_name, "Source File Path": os.path.dirname(file_path).replace("\\", "/") if file_path != "N/A" and file_path else "N/A",
                    "DiskLabel": "N/A", "TapeID": "N/A", "SourceMobID": "Effect", "TrackID": "VFX",
                    "Source Clip EditRate": e["TimelineEditRate"],
                    "Timeline Start TC": frames_to_tc(e["TimelineStartFrame"], timeline_rate, is_drop_frame),
                    "Source Clip start time code": "N/A", "Source Clip offset": "N/A",
                    "StartTime": "N/A", "End Time": "N/A", "Event Length": e["Length"],
                    "Source Clip start (frames)": 0, "Source Clip offset (frames)": 0, "StartTime (frames)": 0,
                    "Effect Name": effect_name, "Keyframe Details": keyframe_details, # Add new data
                })
                continue

            initial_mob = mob_map.get(e["MobID"])
            final_source_mob = get_genuine_source_info(e["MobID"], mob_map)
            master_md = extract_metadata(initial_mob)
            final_md = extract_metadata(final_source_mob)
            md = final_md.copy()
            md["TapeID"] = master_md.get("TapeID") or final_md.get("TapeID")
            md["DiskLabel"] = master_md.get("DiskLabel") or final_md.get("DiskLabel")
            source_file_name, source_path = "N/A", "N/A"
            if final_source_mob:
                path_url = final_md.get("URLString", "")
                if path_url:
                    try:
                        decoded_path = urllib.parse.unquote(urllib.parse.urlparse(path_url).path)
                        source_file_name = os.path.basename(decoded_path)
                        source_path = os.path.dirname(decoded_path)
                    except: source_file_name = "Path Error"
            
            event_start_frames = md['GenuineStartFrames'] + e['SourceOffsetFrames']
            event_end_frames = event_start_frames + e['Length']
            source_edit_rate = md.get("SourceEditRate") or e["TimelineEditRate"]
            source_is_drop = md.get("IsDropFrame", False)
            
            enriched.append({
                "Event": idx, "Event Name": source_file_name, "Clip Name": source_file_name,
                "Source File Name": source_file_name, "Source File Path": source_path,
                "DiskLabel": md.get("DiskLabel"), "TapeID": md.get("TapeID"),
                "SourceMobID": e["MobID"], "TrackID": e.get("SourceTrackID", "N/A"),
                "Source Clip EditRate": source_edit_rate,
                "Timeline Start TC": frames_to_tc(e["TimelineStartFrame"], timeline_rate, is_drop_frame),
                "Source Clip start time code": frames_to_tc(md["GenuineStartFrames"], source_edit_rate, source_is_drop),
                "Source Clip offset": frames_to_tc(e["SourceOffsetFrames"], source_edit_rate, source_is_drop),
                "StartTime": frames_to_tc(event_start_frames, source_edit_rate, source_is_drop),
                "End Time": frames_to_tc(event_end_frames, source_edit_rate, source_is_drop),
                "Event Length": e["Length"],
                "Source Clip start (frames)": md["GenuineStartFrames"],
                "Source Clip offset (frames)": e["SourceOffsetFrames"],
                "StartTime (frames)": event_start_frames,
                "Effect Name": effect_name, "Keyframe Details": keyframe_details, # Add new data
            })
            
        self.log_msg("\n--- Timeline Summary ---")
        for key, value in summary_info.items(): self.log_msg(f"  {key}: {value}")
        
        self.log_msg("\n--- Event Details ---")
        for r in enriched:
            self.log_msg(f"----------------------------------------")
            if r.get("SourceMobID") == "Effect":
                 self.log_msg(f"Event: {r['Event']} | {r['Event Name']}")
            else:
                self.log_msg(f"Event: {r['Event']} | {r['Source File Name']}")
            self.log_msg(f"  Timeline In: {r['Timeline Start TC']} | Length: {r['Event Length']} frames")
            self.log_msg(f"  Effect Applied: {r['Effect Name']}") # Log the effect name
            if r["Keyframe Details"] != "N/A":
                self.log_msg(r["Keyframe Details"]) # Log keyframe data if it exists
            if r.get("SourceMobID") != "Effect":
                self.log_msg(f"  TapeID: {r['TapeID']} | DiskLabel: {r['DiskLabel']}")
                self.log_msg(f"  Source Path: {r['Source File Path']}")
                self.log_msg(f"  Event In (at source): {r['StartTime']} ({r['StartTime (frames)']}f)")
            self.log_msg("")

        out_path = os.path.join(output_dir, f"super_edl_fx_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        self.log_msg(f"✅ Analysis complete. Full report in:\n{os.path.basename(out_path)}")
        try:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Timeline Summary"])
                for key, value in summary_info.items(): writer.writerow([key, value])
                writer.writerow([])
                if enriched:
                    # Ensure new columns are in the header
                    header = enriched[0].keys()
                    writer.writerow(header)
                    for row in enriched: writer.writerow(row.values())
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save CSV report: {e}")
        else:
            messagebox.showinfo("Done", "Report generated successfully.")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()