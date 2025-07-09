import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode and Parsing Functions ---

def frames_to_tc(frame_count, fps):
    if frame_count is None or fps is None or fps == 0: return "N/A"
    try:
        frame_count, fps, int_fps = int(frame_count), float(fps), round(float(fps))
        if int_fps == 0: return "N/A"
        h, m, s, f = frame_count // (3600 * int_fps), (frame_count % (3600 * int_fps)) // (60 * int_fps), (frame_count % (60 * int_fps)) // int_fps, frame_count % int_fps
        return f"{h:02}:{m:02}:{s:02}:{f:02}"
    except (ValueError, TypeError): return "N/A"

def create_mob_map(node, mob_map=None):
    if mob_map is None: mob_map = {}
    if not isinstance(node, list) or len(node) < 2: return mob_map
    children = node[3] if len(node) > 3 else []
    if any(isinstance(c, list) and c[0] == "MobID" for c in children):
        mobid = next((c[2] for c in children if isinstance(c, list) and c[0] == "MobID"), None)
        if mobid: mob_map[mobid] = node
    for c in children: create_mob_map(c, mob_map)
    return mob_map

def find_main_sequence_mob_and_start_tc(root_node):
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4: return None, 0
    all_mobs, sequence_mob = root_node[3], None
    for mob in all_mobs:
        if not isinstance(mob, list) or len(mob) < 4: continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not slots_node or len(slots_node) < 4: continue
        is_sequence_mob = False
        for slot in slots_node[3]:
            if not isinstance(slot, list) or len(slot) < 4: continue
            segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
            if not segment_node or len(segment_node) < 4: continue
            list_of_segments = segment_node[3]
            if isinstance(list_of_segments, list) and len(list_of_segments) > 0:
                if isinstance(list_of_segments[0], list) and list_of_segments[0][0] == "Sequence":
                    is_sequence_mob = True; break
        if is_sequence_mob: sequence_mob = mob; break
    if not sequence_mob: return None, 0
    start_frame_count = 0
    slots_node = next((c for c in sequence_mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
    if slots_node and len(slots_node) >= 4:
        for slot in slots_node[3]:
            if not isinstance(slot, list) or len(slot) < 4: continue
            segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
            if not segment_node or len(segment_node) < 4: continue
            list_of_segments = segment_node[3]
            if not (isinstance(list_of_segments, list) and len(list_of_segments) > 0): continue
            timecode_node = list_of_segments[0]
            if isinstance(timecode_node, list) and timecode_node[0] == "Timecode":
                tc_children = timecode_node[3] if len(timecode_node) > 3 else []
                start_frame = next((c for c in tc_children if isinstance(c, list) and c[0] == "Start"), None)
                if start_frame and len(start_frame) > 2:
                    try: start_frame_count = int(start_frame[2]); break
                    except (ValueError, TypeError): continue
    return sequence_mob, start_frame_count

def extract_metadata(mob_node):
    metadata = {"AvidBinStartFrames": None, "TrueSourceStartFrames": None, "URLString": ""}
    if not mob_node or not isinstance(mob_node, list) or len(mob_node) < 4: return metadata
    def find_url(n):
        if not isinstance(n, list): return
        if n[0] == "URLString" and len(n) > 2: metadata["URLString"] = n[2]; return True
        children = n[3] if len(n) > 3 else []
        for c in children:
            if find_url(c): return True
        return False
    def find_timecode_start(n):
        if not isinstance(n, list): return
        if n[0] == "Timecode":
            for item in n[3] if len(n) > 3 else []:
                if isinstance(item, list) and item[0] == "Start":
                    try: metadata["TrueSourceStartFrames"] = int(item[2])
                    except: pass
        children = n[3] if len(n) > 3 else []
        for c in children: find_timecode_start(c)
    find_url(mob_node)
    find_timecode_start(mob_node)
    slots_node = next((c for c in mob_node[3] if isinstance(c, list) and c[0] == "Slots"), None)
    if slots_node and len(slots_node) >= 4:
        for slot in slots_node[3]:
            if not isinstance(slot, list) or len(slot) < 4: continue
            segment_node = next((c for c in slot[3] if isinstance(c, list) and c[0] == "Segment"), None)
            if not segment_node or len(segment_node) < 4: continue
            list_of_segments = segment_node[3]
            if isinstance(list_of_segments, list):
                for seg in list_of_segments:
                    if not isinstance(seg, list): continue
                    if seg[0] == "SourceClip":
                        children = seg[3] if len(seg) > 3 else []
                        start_time = next((c[2] for c in children if isinstance(c, list) and c[0] == "StartTime"), None)
                        if start_time is not None:
                            try: metadata["AvidBinStartFrames"] = int(start_time)
                            except (ValueError, TypeError): pass
    return metadata
