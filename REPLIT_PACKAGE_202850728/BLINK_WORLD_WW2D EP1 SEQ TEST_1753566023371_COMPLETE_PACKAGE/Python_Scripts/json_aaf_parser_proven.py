#!/usr/bin/env python3
"""
JSON AAF Parser - Complete implementation of user's proven superEDLguiFX_v3.py logic
This is the EXACT logic from the working superEDL system that produces the correct CSV output
"""
import json
import re
import os
import urllib.parse
from typing import Dict, List, Any, Optional, Tuple

def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    """Convert frame count to timecode string - EXACT copy from superEDLguiFX_v3.py"""
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

def decode_filepath(filepath_node):
    """Decode filepath from node - EXACT copy from superEDLguiFX_v3.py"""
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
    """Find filepath in node - EXACT copy from superEDLguiFX_v3.py"""
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

def extract_filepath_from_node(node):
    """Extract filepath from UTF-16LE binary plugin data - Pan & Zoom special case"""
    if not isinstance(node, list):
        return None
    
    # First try the standard filepath method
    filepath = find_filepath(node)
    if filepath and filepath != "N/A":
        return filepath
    
    # Then try binary plugin data extraction for Pan & Zoom
    for child in node[3] if len(node) > 3 else []:
        if isinstance(child, list) and child[0] == "ComponentAttributeList":
            for attr in child[3] if len(child) > 3 else []:
                if (isinstance(attr, list) and len(attr) > 3 and 
                    attr[0] == "_PLUGIN_DATA_UTF16LE"):
                    for val in attr[3]:
                        if isinstance(val, list) and val[0] == "Value" and len(val) > 2:
                            try:
                                raw_binary = val[2]
                                if isinstance(raw_binary, bytes):
                                    decoded = raw_binary.decode('utf-16le', errors='ignore')
                                    decoded = decoded.replace('\x00', '')
                                    decoded = re.sub(r'[^\x20-\x7E]', '', decoded)
                                    if '/' in decoded or '\\' in decoded:
                                        return decoded.strip()
                            except:
                                pass
        # Recursively search in child nodes
        result = extract_filepath_from_node(child)
        if result:
            return result
    return None

def create_mob_map(node, mob_map=None):
    """Create mob map - EXACT copy from superEDLguiFX_v3.py"""
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
    """Find main sequence - EXACT copy from superEDLguiFX_v3.py"""
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
    """Extract metadata - EXACT copy from superEDLguiFX_v3.py"""
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

def has_nested_source_clip(node):
    """Check for nested source clip - EXACT copy from superEDLguiFX_v3.py"""
    if not isinstance(node, list):
        return False
    if node[0] == "SourceClip":
        return True
    children = node[3] if len(node) > 3 else []
    return any(has_nested_source_clip(child) for child in children)

def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, dedupe_set=None):
    """Recursive search for timeline events - EXACT copy from superEDLguiFX_v3.py"""
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
    # OperationGroup: Extract effects and check for source clips
    elif name == "OperationGroup":
        # Extract effect information
        effect_info = extract_effect_info(node)
        # Keyframe data will be extracted using effects_by_frame later
        keyframe_data = {}
        
        # Check if this has a nested source clip
        if has_nested_source_clip(node):
            # This is an effect ON a clip - find the source clip and add effect data
            def find_source_in_operation(op_node):
                for child in op_node[3] if len(op_node) > 3 else []:
                    if isinstance(child, list):
                        if child[0] == "SourceClip":
                            mobid = next((c[2] for c in child[3] if c[0] == "SourceID"), None)
                            track_id = next((c[2] for c in child[3] if c[0] == "SourceTrackID"), "N/A")
                            offset = next((int(c[2]) for c in child[3] if c[0] in ("Start", "StartTime")), 0)
                            length = next((int(c[2]) for c in child[3] if c[0] == "Length"), 0)
                            
                            # Check if this is Pan & Zoom ON a real clip (PZ_OVERRIDE case)
                            effect_name = effect_info.get('name', '') if effect_info else ''
                            is_pan_zoom_override = (effect_name and 
                                                  ('Pan & Zoom' in effect_name or 'Avid Pan & Zoom' in effect_name))
                            
                            if mobid and is_pan_zoom_override:
                                # Pan & Zoom Override - extract binary file path and mark as PZ_OVERRIDE
                                pz_file_path = extract_filepath_from_node(node)
                                return {
                                    "MobID": "PZ_OVERRIDE",  # Special MobID for Pan & Zoom override
                                    "OriginalMobID": mobid,  # Keep track of original clip
                                    "SourceTrackID": track_id,
                                    "TimelineStartFrame": timeline_offset,
                                    "SourceOffsetFrames": offset,
                                    "Length": length,
                                    "TimelineEditRate": edit_rate,
                                    "FilePath": pz_file_path or "N/A",
                                    "EffectInfo": effect_info,
                                    "KeyframeData": keyframe_data,
                                    "IsPanZoomOverride": True
                                }
                            elif mobid:
                                return {
                                    "MobID": mobid,
                                    "SourceTrackID": track_id,
                                    "TimelineStartFrame": timeline_offset,
                                    "SourceOffsetFrames": offset,
                                    "Length": length,
                                    "TimelineEditRate": edit_rate,
                                    "EffectInfo": effect_info,
                                    "KeyframeData": keyframe_data
                                }
                        else:
                            result = find_source_in_operation(child)
                            if result:
                                return result
                return None
            
            source_clip = find_source_in_operation(node)
            if source_clip:
                key = (source_clip["MobID"], timeline_offset, source_clip["SourceOffsetFrames"], source_clip["Length"])
                if key not in dedupe_set:
                    dedupe_set.add(key)
                    results.append(source_clip)
        else:
            # This is an FX-on-filler event
            length = next((int(c[2]) for c in children if c[0] == "Length"), 0)
            
            # Extract filepath using both standard and binary methods
            file_path = extract_filepath_from_node(node) or "N/A"
            
            # Determine if this is Pan & Zoom on filler (uses binary data) or generic effect (needs placeholder)
            effect_name = effect_info.get('name', '') if effect_info else ''
            is_pan_zoom = effect_name and ('Pan & Zoom' in effect_name or 'Avid Pan & Zoom' in effect_name)
            
            if is_pan_zoom and file_path != "N/A":
                # Pan & Zoom on filler - use binary extracted filepath
                mob_id = "FX_ON_FILLER"  # Keep as FX_ON_FILLER but with real file path
            else:
                # Generic effect on filler - create placeholder
                placeholder_info = create_placeholder_for_filler(effect_info, keyframe_data)
                if placeholder_info.get('filename'):
                    file_path = placeholder_info.get('file_path', 'N/A')
                mob_id = "FX_ON_FILLER"
            
            if length > 0:
                results.append({
                    "MobID": mob_id,
                    "TimelineStartFrame": timeline_offset,
                    "SourceOffsetFrames": 0,
                    "Length": length,
                    "TimelineEditRate": edit_rate,
                    "FilePath": file_path,
                    "EffectInfo": effect_info,
                    "KeyframeData": keyframe_data,
                    "IsPanZoom": is_pan_zoom
                })
        
        for c in children:
            recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    else:
        for c in children:
            recursive_search(c, timeline_offset, edit_rate, results, dedupe_set)
    return results

def get_genuine_source_info(mob_id, mob_map, visited=None):
    """Resolve genuine source info - EXACT copy from superEDLguiFX_v3.py"""
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

def find_timeline_effects(node, timeline_offset=0, results_list=None):
    """Find timeline effects using proven superEDLguiFX logic"""
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

def extract_effect_details(node):
    """Extract effect details with AFX keyframes using proven logic"""
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
    return {'effect_name': effect_name, 'length': length, 'animated_params': animated}

def extract_effect_info(node):
    """Extract effect plugin information from JSON node"""
    effect_info = {}
    
    def search_for_effects(n):
        if not isinstance(n, list):
            return
        
        if len(n) >= 3 and isinstance(n[0], str):
            if n[0] == "_EFFECT_PLUGIN_NAME" and len(n) > 2:
                effect_info['name'] = n[2]
            elif n[0] == "_EFFECT_PLUGIN_TYPE" and len(n) > 2:
                effect_info['type'] = n[2]
            elif n[0] == "_EFFECT_PLUGIN_CLASS" and len(n) > 2:
                effect_info['class'] = n[2]
        
        # Recursively search children
        if len(n) > 3 and isinstance(n[3], list):
            for child in n[3]:
                search_for_effects(child)
    
    search_for_effects(node)
    return effect_info

def process_events_to_clips_proven(events, mob_map, edit_rate, start_tc, is_drop_frame, effects_by_frame=None):
    """Process events using EXACT superEDLguiFX_v3.py logic for regular clips"""
    enriched = []
    
    for idx, e in enumerate(events, start=1):
        start_frame = e['TimelineStartFrame']
        
        if e.get('MobID') in ['FX_ON_FILLER', 'PZ_OVERRIDE']:
            continue  # Skip special cases for now, focus on regular clips
        
        # Real clip events - EXACT logic from superEDLguiFX_v3.py lines 567-612
        initial = mob_map.get(e['MobID'])
        final = get_genuine_source_info(e['MobID'], mob_map)
        md_master = extract_metadata(initial)
        md_final = extract_metadata(final)
        md = md_final.copy()
        md['TapeID'] = md_master.get('TapeID') or md_final.get('TapeID')
        md['DiskLabel'] = md_master.get('DiskLabel') or md_final.get('DiskLabel')
        
        src_fname, src_path = 'N/A', 'N/A'
        url = md_final.get('URLString', '')
        if url:
            try:
                dec = urllib.parse.unquote(urllib.parse.urlparse(url).path)
                src_fname = os.path.basename(dec)
                src_path = os.path.dirname(dec)
            except:
                src_fname = 'Path Error'
        
        gsfr = md['GenuineStartFrames']
        off = e['SourceOffsetFrames']
        start_frames = gsfr + off
        end_frames = start_frames + e['Length']
        serate = md.get('SourceEditRate') or e['TimelineEditRate']
        sdrop = md.get('IsDropFrame', False)
        
        # Generate Reel name using user's logic: longer of DiskLabel/TapeID, fallback to filename
        reel_name = src_fname  # fallback
        disk_label = md.get('DiskLabel', '')
        tape_id = md.get('TapeID', '')
        if disk_label and tape_id:
            reel_name = disk_label if len(disk_label) >= len(tape_id) else tape_id
        elif disk_label:
            reel_name = disk_label
        elif tape_id:
            reel_name = tape_id
        
        # Extract keyframe data and effects using proven effects_by_frame method
        start_frame = e['TimelineStartFrame']
        effect_data = effects_by_frame.get(start_frame) if effects_by_frame else None
        keyframe_data = effect_data['animated_params'] if effect_data else {}
        effect_info = e.get('EffectInfo', {})
        
        # Convert source timecode to DaVinci Resolve format (frames/25s)
        source_start_resolve_format = f"{gsfr}/{int(serate)}s"
        
        clip_name = src_fname.replace('.mov', '').replace('.R3D', '') if src_fname else 'Unknown'
        
        enriched.append({
            # CSV-compatible fields for complete data display
            'Event': idx,
            'Event Name': src_fname,
            'Clip Name': src_fname,
            'Source File Name': src_fname,
            'Source File Path': src_path,
            'DiskLabel': md.get('DiskLabel'),
            'TapeID': md.get('TapeID'),
            'Reel': reel_name,  # Proper Reel logic implemented
            'SourceMobID': e['MobID'],
            'TrackID': e.get('SourceTrackID', 'N/A'),
            'Source Clip EditRate': serate,
            'Timeline Start TC': frames_to_tc(start_frame, edit_rate, is_drop_frame),
            'Source Clip start time code': frames_to_tc(gsfr, serate, sdrop),
            'Source Clip offset': frames_to_tc(off, serate, sdrop),
            'StartTime': frames_to_tc(start_frames, serate, sdrop),
            'End Time': frames_to_tc(end_frames, serate, sdrop),
            'Event Length': e['Length'],
            'Source Clip start (frames)': gsfr,
            'Source Clip offset (frames)': off,
            'StartTime (frames)': start_frames,
            'Effect Name': effect_info.get('name', 'N/A'),
            'Keyframe Details': 'Found AFX keyframes' if keyframe_data else 'No animated parameters found.',
            
            # Simplified fields for XML generator compatibility
            'event_num': idx,
            'name': clip_name,
            'source_file': clip_name,
            'source_start_tc': frames_to_tc(gsfr, serate, sdrop),
            'source_offset_tc': frames_to_tc(off, serate, sdrop),
            'timeline_start_tc': frames_to_tc(start_frame, edit_rate, is_drop_frame),
            'source_edit_rate': serate,
            'disk_label': md.get('DiskLabel', 'N/A'),
            'tape_id': md.get('TapeID', 'N/A'),
            'reel': reel_name,
            'duration': e['Length'],
            'start_time': start_frame,
            'source_path': src_path,
            'source_mob_id': e['MobID'],
            'is_drop_frame': is_drop_frame,
            
            # DaVinci Resolve compatible format
            'source_start_resolve': source_start_resolve_format,
            
            # Effects and keyframe data
            'keyframe_data': keyframe_data,
            'effect_info': effect_info,
            'has_effects': bool(keyframe_data or effect_info),
            'effects': [effect_info.get('name')] if effect_info.get('name') else []
        })
    
    return enriched

def parse_json_aaf_proven(json_file_path):
    """Parse JSON AAF using complete proven superEDLguiFX_v3.py logic"""
    with open(json_file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    print("1. Building Mob map...")
    mob_map = create_mob_map(json_data)
    print(f"Created mob map with {len(mob_map)} entries")
    
    print("2. Finding main sequence...")
    sequence_mob, start_tc, timeline_rate, is_drop_frame = find_main_sequence_mob_and_start_tc(json_data)
    if not sequence_mob:
        raise ValueError("Could not find main sequence")
    
    print(f"Found main sequence: edit_rate={timeline_rate}, start_tc={start_tc}, is_drop={is_drop_frame}")
    
    print("3. Scanning for all timeline effects and keyframes...")
    all_effects = find_timeline_effects(sequence_mob, timeline_offset=start_tc)
    effects_by_frame = {}
    for effect in all_effects:
        details = extract_effect_details(effect['node'])
        details['node'] = effect['node']  # keep node for filepath extraction
        effects_by_frame[effect['start_frame']] = details
    print(f"   Found {len(effects_by_frame)} effects with keyframes.")
    
    print("4. Extracting primary timeline events (EDL)...")
    events = recursive_search(sequence_mob, timeline_offset=start_tc, edit_rate=timeline_rate)
    unique_events = []
    seen_keys = set()
    for e in events:
        key = (e.get("MobID"), e.get("TimelineStartFrame"), e.get("SourceOffsetFrames"), e.get("Length"))
        if key not in seen_keys:
            unique_events.append(e)
            seen_keys.add(key)
    events = unique_events
    
    print(f"Recursive search found {len(events)} events")
    
    # Separate clips and filler effects like in superEDLguiFX_v3.py
    clips = [e for e in events if e.get('MobID') != 'FX_ON_FILLER']
    filler_effects = [e for e in events if e.get('MobID') == 'FX_ON_FILLER']
    print(f"Separated: {len(clips)} clips, {len(filler_effects)} filler effects")
    
    print("5. Processing events with EXACT superEDLguiFX_v3.py logic...")
    processed_clips = process_events_with_exact_superEDL_logic(events, mob_map, timeline_rate, start_tc, is_drop_frame, effects_by_frame)
    
    return {
        'file_info': {
            'filename': next((c[2] for c in sequence_mob[3] if c[0] == "Name"), "Unknown_Sequence"),
            'edit_rate': timeline_rate,
            'start_timecode': start_tc,
            'is_drop_frame': is_drop_frame
        },
        'composition_info': {
            'edit_rate_numeric': timeline_rate,
            'timecode_format': 'DF' if is_drop_frame else 'NDF',
            'is_drop_frame': is_drop_frame,
            'start_frames': start_tc,
            'name': next((c[2] for c in sequence_mob[3] if c[0] == "Name"), "Unknown_Sequence"),
            'duration': sum(e['Length'] for e in events)
        },
        'clips': processed_clips,
        'raw_events': events,
        'clips_events': clips,
        'filler_events': filler_effects
    }

def create_placeholder_for_filler(effect_info, keyframe_data):
    """Create placeholder PNG info for filler effects - from superEDLguiFX_v3.py logic"""
    if not effect_info or not effect_info.get('name'):
        return {'file_path': 'N/A', 'filename': None}
    
    effect_name = effect_info.get('name', 'Unknown Effect')
    
    # Handle Pan & Zoom specially (should not create placeholder, uses binary data)
    if effect_name and ('Pan & Zoom' in effect_name or 'Avid Pan & Zoom' in effect_name):
        return {'file_path': 'N/A', 'filename': None}
    
    # Create placeholder filename from effect name - exact logic from superEDLguiFX_v3.py
    base = effect_name.split(':')[-1].strip().lower()
    base = re.sub(r'[^0-9a-z]+', '_', base)
    placeholder_filename = f"{base}_placeholder.png"
    placeholder_path = os.path.join('placeholders', placeholder_filename)
    
    return {
        'file_path': placeholder_path,
        'filename': placeholder_filename,
        'effect_name': effect_name
    }

def process_events_with_exact_superEDL_logic(events, mob_map, timeline_rate, start_tc, is_drop_frame, effects_by_frame=None):
    """EXACT copy of superEDLguiFX_v3.py enrichment logic from lines 455-612"""
    enriched = []
    
    for idx, e in enumerate(events, start=1):
        start_frame = e['TimelineStartFrame']
        effect_data = effects_by_frame.get(start_frame) if effects_by_frame else None
        effect_name = effect_data['effect_name'] if effect_data else 'N/A'
        
        # EXACT classification logic from superEDLguiFX_v3.py lines 461-463
        is_override = effect_data and 'Avid Pan & Zoom' in effect_name and e.get('MobID') != 'FX_ON_FILLER'
        is_pz_filler = e.get('MobID') == 'FX_ON_FILLER' and effect_data and 'Avid Pan & Zoom' in effect_name
        is_generic_filler = e.get('MobID') == 'FX_ON_FILLER' and not is_pz_filler

        # Gather keyframe details - EXACT logic from superEDLguiFX_v3.py lines 465-479
        keyframe_details = 'No animated parameters found.'
        if effect_data and effect_data.get('animated_params'):
            kfs_list = []
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
            keyframe_details = '\n'.join(kfs_list)

        # --- PanZoom Override (real clip) --- EXACT lines 481-508
        if is_override:
            node = effect_data['node']
            fpath = find_filepath(node) or 'N/A'
            fname = os.path.basename(fpath) if fpath != 'N/A' else 'P&Z Source'
            enriched.append({
                'Event': idx,
                'Event Name': f"(P&Z Override) {fname}",
                'Clip Name': fname,
                'Source File Name': fname,
                'Source File Path': os.path.dirname(fpath).replace('\\', '/') if fpath != 'N/A' else 'N/A',
                'DiskLabel': 'N/A',
                'TapeID': 'N/A',
                'SourceMobID': 'PZ_OVERRIDE',
                'TrackID': 'VFX',
                'Source Clip EditRate': e['TimelineEditRate'],
                'Timeline Start TC': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                'Source Clip start time code': 'N/A',
                'Source Clip offset': 'N/A',
                'StartTime': 'N/A',
                'End Time': 'N/A',
                'Event Length': e['Length'],
                'Source Clip start (frames)': 0,
                'Source Clip offset (frames)': 0,
                'StartTime (frames)': 0,
                'Effect Name': effect_name,
                'Keyframe Details': keyframe_details,
                # Compatibility fields
                'event_num': idx,
                'name': fname.replace('.mov', '').replace('.R3D', '').replace('.jpg', '').replace('.tif', ''),
                'source_file': fname,
                'source_start_tc': 'N/A',
                'source_offset_tc': 'N/A', 
                'timeline_start_tc': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                'source_edit_rate': e['TimelineEditRate'],
                'disk_label': 'N/A',
                'tape_id': 'N/A',
                'reel': 'PZ_OVERRIDE',
                'duration': e['Length'],
                'start_time': start_frame,
                'source_path': os.path.dirname(fpath).replace('\\', '/') if fpath != 'N/A' else 'N/A',
                'source_mob_id': 'PZ_OVERRIDE',
                'is_drop_frame': is_drop_frame,
                'keyframe_data': effect_data.get('animated_params', {}),
                'effect_info': {'name': effect_name},
                'has_effects': bool(effect_data),
                'effects': [effect_name]
            })
        # --- PanZoom on Filler --- EXACT lines 509-535
        elif is_pz_filler:
            fpath = e.get('FilePath', 'N/A')
            fname = os.path.basename(fpath) if fpath != 'N/A' else 'PanZoom_Filler'
            enriched.append({
                'Event': idx,
                'Event Name': 'Pan & Zoom on Filler',
                'Clip Name': fname,
                'Source File Name': fname,
                'Source File Path': os.path.dirname(fpath).replace('\\', '/') if fpath != 'N/A' else 'N/A',
                'DiskLabel': 'N/A',
                'TapeID': 'N/A',
                'SourceMobID': 'FX_ON_FILLER',
                'TrackID': 'VFX',
                'Source Clip EditRate': e['TimelineEditRate'],
                'Timeline Start TC': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                'Source Clip start time code': 'N/A',
                'Source Clip offset': 'N/A',
                'StartTime': 'N/A',
                'End Time': 'N/A',
                'Event Length': e['Length'],
                'Source Clip start (frames)': 0,
                'Source Clip offset (frames)': 0,
                'StartTime (frames)': 0,
                'Effect Name': effect_name,
                'Keyframe Details': keyframe_details,
                # Compatibility fields
                'event_num': idx,
                'name': fname.replace('.mov', '').replace('.R3D', '').replace('.jpg', '').replace('.tif', ''),
                'source_file': fname,
                'source_start_tc': 'N/A',
                'source_offset_tc': 'N/A',
                'timeline_start_tc': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                'source_edit_rate': e['TimelineEditRate'],
                'disk_label': 'N/A',
                'tape_id': 'N/A',
                'reel': 'PZ_FILLER',
                'duration': e['Length'],
                'start_time': start_frame,
                'source_path': os.path.dirname(fpath).replace('\\', '/') if fpath != 'N/A' else 'N/A',
                'source_mob_id': 'FX_ON_FILLER',
                'is_drop_frame': is_drop_frame,
                'keyframe_data': effect_data.get('animated_params', {}),
                'effect_info': {'name': effect_name},
                'has_effects': bool(effect_data),
                'effects': [effect_name]
            })
        # --- Generic FX on Filler --- EXACT lines 536-565
        elif is_generic_filler:
            fpath = e.get('FilePath', '')
            # derive placeholder name from effect - EXACT logic from superEDLguiFX_v3.py lines 540-542
            base = effect_name.split(':')[-1].strip().lower()
            base = re.sub(r'[^0-9a-z]+', '_', base)
            placeholder = f"{base}_placeholder.png"
            enriched.append({
                'Event': idx,
                'Event Name': f"{effect_name} on Filler",
                'Clip Name': placeholder,
                'Source File Name': placeholder,
                'Source File Path': os.path.join('placeholders', placeholder),
                'DiskLabel': 'N/A',
                'TapeID': 'N/A',
                'SourceMobID': 'FX_ON_FILLER',
                'TrackID': 'VFX',
                'Source Clip EditRate': e['TimelineEditRate'],
                'Timeline Start TC': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                'Source Clip start time code': '01:00:00:00',
                'Source Clip offset': '0',
                'StartTime': '01:00:00:00',
                'End Time': '01:00:00:00',
                'Event Length': e['Length'],
                'Source Clip start (frames)': 0,
                'Source Clip offset (frames)': 0,
                'StartTime (frames)': 0,
                'Effect Name': effect_name,
                'Keyframe Details': keyframe_details,
                # Compatibility fields
                'event_num': idx,
                'name': placeholder.replace('.png', ''),
                'source_file': placeholder,
                'source_start_tc': '01:00:00:00',
                'source_offset_tc': '0',
                'timeline_start_tc': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                'source_edit_rate': e['TimelineEditRate'],
                'disk_label': 'N/A',
                'tape_id': 'N/A',
                'reel': 'PLACEHOLDER',
                'duration': e['Length'],
                'start_time': start_frame,
                'source_path': 'placeholders',
                'source_mob_id': 'FX_ON_FILLER',
                'is_drop_frame': is_drop_frame,
                'keyframe_data': effect_data.get('animated_params', {}) if effect_data else {},
                'effect_info': {'name': effect_name},
                'has_effects': bool(effect_data),
                'effects': [effect_name]
            })
        # --- Real clip events --- EXACT lines 567-612
        else:
            initial = mob_map.get(e['MobID'])
            final = get_genuine_source_info(e['MobID'], mob_map)
            md_master = extract_metadata(initial)
            md_final = extract_metadata(final)
            md = md_final.copy()
            md['TapeID'] = md_master.get('TapeID') or md_final.get('TapeID')
            md['DiskLabel'] = md_master.get('DiskLabel') or md_final.get('DiskLabel')
            src_fname, src_path = 'N/A', 'N/A'
            url = md_final.get('URLString', '')
            if url:
                try:
                    dec = urllib.parse.unquote(urllib.parse.urlparse(url).path)
                    src_fname = os.path.basename(dec)
                    src_path = os.path.dirname(dec)
                except:
                    src_fname = 'Path Error'
            
            gsfr = md['GenuineStartFrames']
            off = e['SourceOffsetFrames']
            start_frames = gsfr + off
            end_frames = start_frames + e['Length']
            serate = md.get('SourceEditRate') or e['TimelineEditRate']
            sdrop = md.get('IsDropFrame', False)
            
            # Generate Reel name using user's logic: longer of DiskLabel/TapeID, fallback to filename
            reel_name = src_fname  # fallback
            disk_label = md.get('DiskLabel', '')
            tape_id = md.get('TapeID', '')
            if disk_label and tape_id:
                reel_name = disk_label if len(disk_label) >= len(tape_id) else tape_id
            elif disk_label:
                reel_name = disk_label
            elif tape_id:
                reel_name = tape_id
            
            # Extract keyframe data and effects using proven effects_by_frame method
            effect_data_clip = effects_by_frame.get(start_frame) if effects_by_frame else None
            keyframe_data_clip = effect_data_clip['animated_params'] if effect_data_clip else {}
            effect_info_clip = {'name': effect_data_clip['effect_name']} if effect_data_clip else {}
            
            # Convert source timecode to DaVinci Resolve format (frames/25s)
            source_start_resolve_format = f"{gsfr}/{int(serate)}s"
            
            clip_name = src_fname.replace('.mov', '').replace('.R3D', '') if src_fname else 'Unknown'
            
            enriched.append({
                # CSV-compatible fields for complete data display
                'Event': idx,
                'Event Name': src_fname,
                'Clip Name': src_fname,
                'Source File Name': src_fname,
                'Source File Path': src_path,
                'DiskLabel': md.get('DiskLabel'),
                'TapeID': md.get('TapeID'),
                'Reel': reel_name,  # Proper Reel logic implemented
                'SourceMobID': e['MobID'],
                'TrackID': e.get('SourceTrackID', 'N/A'),
                'Source Clip EditRate': serate,
                'Timeline Start TC': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                'Source Clip start time code': frames_to_tc(gsfr, serate, sdrop),
                'Source Clip offset': frames_to_tc(off, serate, sdrop),
                'StartTime': frames_to_tc(start_frames, serate, sdrop),
                'End Time': frames_to_tc(end_frames, serate, sdrop),
                'Event Length': e['Length'],
                'Source Clip start (frames)': gsfr,
                'Source Clip offset (frames)': off,
                'StartTime (frames)': start_frames,
                'Effect Name': effect_info_clip.get('name', 'N/A'),
                'Keyframe Details': 'Found AFX keyframes' if keyframe_data_clip else 'No animated parameters found.',
                
                # Simplified fields for XML generator compatibility
                'event_num': idx,
                'name': clip_name,
                'source_file': clip_name,
                'source_start_tc': frames_to_tc(gsfr, serate, sdrop),
                'source_offset_tc': frames_to_tc(off, serate, sdrop),
                'timeline_start_tc': frames_to_tc(start_frame, timeline_rate, is_drop_frame),
                'source_edit_rate': serate,
                'disk_label': md.get('DiskLabel', 'N/A'),
                'tape_id': md.get('TapeID', 'N/A'),
                'reel': reel_name,
                'duration': e['Length'],
                'start_time': start_frame,
                'source_path': src_path,
                'source_mob_id': e['MobID'],
                'is_drop_frame': is_drop_frame,
                
                # DaVinci Resolve compatible format
                'source_start_resolve': source_start_resolve_format,
                
                # Effects and keyframe data
                'keyframe_data': keyframe_data_clip,
                'effect_info': effect_info_clip,
                'has_effects': bool(keyframe_data_clip or effect_info_clip),
                'effects': [effect_info_clip.get('name')] if effect_info_clip.get('name') else []
            })
    
    return enriched