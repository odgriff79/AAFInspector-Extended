#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super EDL + FX Extractor (AAF → CSV)

FINAL CORRECTED VERSION: A direct and faithful translation of the user's proven
JSON-parsing logic from superEDLguiFX_UPDATED_v2.py to a pyaaf2-based script,
restoring all original functionality and fixing all bugs.
"""

import argparse
import csv
import os
import sys
import urllib.parse
from collections import deque
from datetime import datetime

# Corrected, final imports
import aaf2
from aaf2.components import Sequence, SourceClip, OperationGroup, Timecode
from aaf2.misc import Parameter, VaryingValue, ControlPoint

# ---------- Utilities ----------

def frames_to_tc(fc, fps, drop=False):
    """Formats frames to HH:MM:SS:FF, handling potential None inputs."""
    if fc is None: return "N/A"
    try: fc = int(fc)
    except (ValueError, TypeError): return "N/A"
    fps = int(round(float(fps or 25.0)))
    if fps <= 0: return "N/A"
    h = fc // (3600 * fps)
    m = (fc % (3600 * fps)) // (60 * fps)
    s = (fc % (60 * fps)) // fps
    f = fc % fps
    return f"{h:02}:{m:02}:{s:02}{';' if drop else ':'}{f:02}"

def unwrap(x):
    """Recursively unwraps pyaaf2 PropertyValue objects to their raw Python types."""
    try:
        if hasattr(x, "value"): return unwrap(x.value)
        return x
    except Exception: return x

# ---------- Timeline & Traversal (Logic restored from user's original script) ----------

def choose_timeline(f, preferred_name=None):
    """
    Finds the main timeline Mob. First by preferred name, then by finding the
    first CompositionMob with a picture sequence, with a final fallback to any
    mob with a sequence. This is a robust discovery method.
    """
    mobs = list(f.content.mobs)
    if preferred_name:
        for m in mobs:
            if getattr(m, "name", "") == preferred_name: return m
    for m in mobs:
        if getattr(getattr(m, "classdef", None), "name", "") == "CompositionMob":
            for s in (m.slots or []):
                if isinstance(getattr(s, "segment", None), Sequence): return m
    for m in mobs:
        for s in (m.slots or []):
            if isinstance(getattr(s, "segment", None), Sequence): return m
    return None

def find_picture_sequence_and_info(comp_mob):
    """Finds the main picture sequence and timeline properties from a CompositionMob."""
    edit_rate, drop, timeline_start, sequence = 25.0, False, 0, None
    for s in comp_mob.slots:
        seg = getattr(s, "segment", None)
        if isinstance(seg, Timecode):
            timeline_start = int(getattr(seg, "start", 0) or 0)
            drop = bool(getattr(seg, "drop", False))
        if isinstance(seg, Sequence) and "Picture" in str(getattr(s, "data_def", "")):
            sequence = seg
            try: edit_rate = float(getattr(s, "edit_rate", 25.0) or 25.0)
            except Exception: pass
    return sequence, edit_rate, drop, timeline_start

def walk_and_collect_events(root_seq, start_ofs_frames):
    """
    Faithful implementation of the original script's recursive, list-based timeline
    walker. This robustly finds all clips and their associated effects.
    THIS FIXES THE "1 EVENT" BUG.
    """
    events = []

    def find_parent_effect(component):
        """Walks up from a clip to find its parent effect, if any."""
        parent = getattr(component, 'parent', None)
        while parent:
            if isinstance(parent, OperationGroup):
                return parent
            parent = getattr(parent, 'parent', None)
        return None

    def walk(node, ofs):
        node_len = int(getattr(node, "length", 0) or 0)

        if isinstance(node, SourceClip):
            effect = find_parent_effect(node)
            events.append({"clip": node, "effect": effect, "ofs": ofs, "len": node_len})
            return node_len

        # This handles Sequences, Nests, and other containers with 'components'
        if hasattr(node, 'components'):
            total_len = 0
            for child in node.components:
                child_len = walk(child, ofs + total_len)
                total_len += child_len if child_len is not None else int(getattr(child, "length", 0) or 0)
            return total_len

        # This handles OperationGroups with 'segments'
        if hasattr(node, 'segments'):
            total_len = 0
            for child in node.segments:
                child_len = walk(child, ofs + total_len)
                total_len += child_len if child_len is not None else int(getattr(child, "length", 0) or 0)
            return total_len
        
        return node_len

    walk(root_seq, start_ofs_frames)
    return events

# ---------- Metadata & Effect Extraction (Logic translated from user's original script) ----------

def bfs_find_component(root_segment, component_class):
    """Utility to find the first instance of a component type (e.g., Timecode) in a segment tree."""
    if not root_segment: return None
    dq = deque([root_segment]); seen = {id(root_segment)}
    while dq:
        n = dq.popleft()
        if isinstance(n, component_class): return n
        for attr in ("components", "segments", "input_segments"):
            for s in getattr(n, attr, []):
                if id(s) not in seen: dq.append(s); seen.add(id(s))
    return None

def resolve_end_import_mob(start_mob):
    """Follows the mob chain to the end mob that has a file locator."""
    if not start_mob: return None
    chain = [start_mob]; seen = {id(start_mob)}; cur = start_mob
    while True:
        next_mob = None
        for slot in cur.slots or []:
            sc = bfs_find_component(slot.segment, SourceClip)
            if sc and sc.mob and id(sc.mob) not in seen:
                next_mob = sc.mob; break
        if not next_mob: break
        chain.append(next_mob); seen.add(id(next_mob)); cur = next_mob
    for mob in reversed(chain):
        desc = getattr(mob, "descriptor", None)
        if desc and hasattr(desc, 'locator') and len(list(desc.locator)) > 0: return mob
    return start_mob

def extract_metadata_from_mob(mob):
    """
    A direct pyaaf2 translation of the user's original 'extract_metadata' logic.
    It gathers all key properties from a mob in one comprehensive pass.
    """
    if not mob: return {}
    metadata = {"DiskLabel": "", "TapeID": "", "URLString": "", "GenuineStartFrames": None, 
                "SourceEditRate": None, "IsDropFrame": None, "OrigSourceLength": 0}
    try:
        # Search MobAttributeList for custom metadata like DiskLabel and TapeID
        for attr in getattr(mob, 'attributes', []):
            name = str(unwrap(getattr(attr, 'name', '')))
            value = str(unwrap(getattr(attr, 'value', '')))
            if name == "_IMPORTDISKLAB": metadata["DiskLabel"] = value
            elif name == "TapeID": metadata["TapeID"] = value
    except Exception: pass
    
    desc = getattr(mob, "descriptor", None)
    if desc:
        try: metadata["OrigSourceLength"] = int(unwrap(desc.get('Length', 0)))
        except Exception: pass
        try:
            for loc in desc.locator:
                url = str(unwrap(loc.get("URLString", "")))
                if url: metadata["URLString"] = url; break
        except Exception: pass

    for slot in mob.slots or []:
        tc_obj = bfs_find_component(slot.segment, Timecode)
        if tc_obj:
            metadata["GenuineStartFrames"] = int(unwrap(tc_obj.start))
            metadata["IsDropFrame"] = bool(unwrap(tc_obj.drop))
            try: metadata["SourceEditRate"] = float(unwrap(slot.edit_rate))
            except (TypeError, ValueError): pass
            break
            
    return metadata

def extract_effects_and_keyframes(op_group, edit_rate):
    """Extracts effect name, static parameters, and animated keyframes."""
    if not isinstance(op_group, OperationGroup): return "N/A", "No effect data."
    effect_name = getattr(op_group.operation, 'name', 'Unknown Effect')
    details, animated_params, static_params = [], [], []

    for param in op_group.parameters or []:
        param_name = getattr(param.parameter if isinstance(param, VaryingValue) else param, 'name', 'Unknown Parameter')
        if isinstance(param, VaryingValue):
            kfs = [f"Keyframe at {frames_to_tc(int(unwrap(cp.time)), edit_rate)} ({int(unwrap(cp.time))}f) -> Value: {unwrap(cp.value)}" for cp in param.points]
            if kfs: animated_params.append(f"  - Parameter: {param_name} ({len(kfs)} keyframes)\n    " + "\n    ".join(kfs))
        else:
            static_params.append(f"- Parameter: {param_name} -> Value: {unwrap(param.value)}")
    
    if animated_params: details.append("--- Animated Parameters ---\n" + "\n".join(animated_params))
    if static_params: details.append("--- Static Parameters ---\n" + "\n".join(static_params))
        
    return effect_name, "\n\n".join(details) if details else "No parameters found."

# ---------- Main Logic and Reporting ----------

def main():
    p = argparse.ArgumentParser(description="Super EDL + FX Extractor (AAF → CSV)")
    p.add_argument("aaf", help="Path to AAF file")
    p.add_argument("--comp", help="CompositionMob name (optional)")
    p.add_argument("--out", help="Output CSV path (optional)")
    args = p.parse_args()

    with aaf2.open(args.aaf, "r") as f:
        comp = choose_timeline(f, args.comp)
        if not comp: raise RuntimeError("Could not find a suitable CompositionMob.")
        seq, edit_rate, drop, timeline_start = find_picture_sequence_and_info(comp)
        if not seq: raise RuntimeError("No picture Sequence found on the timeline.")

        events = walk_and_collect_events(seq, timeline_start)
        rows = []
        for idx, e in enumerate(events, start=1):
            sc, ofs, length, effect_op = e["clip"], e["ofs"], e["len"], e["effect"]
            start_mob = getattr(sc, "mob", None)
            import_mob = resolve_end_import_mob(start_mob)
            final_md = extract_metadata_from_mob(import_mob)
            immediate_md = extract_metadata_from_mob(start_mob)
            
            disk_label = final_md.get("DiskLabel") or immediate_md.get("DiskLabel", "N/A")
            tape_id = final_md.get("TapeID") or immediate_md.get("TapeID", "N/A")
            genuine_start = final_md.get("GenuineStartFrames")
            source_rate = final_md.get("SourceEditRate") or edit_rate
            source_drop = final_md.get("IsDropFrame", False)
            orig_len = final_md.get("OrigSourceLength") or immediate_md.get("OrigSourceLength", 0)
            url = final_md.get("URLString", "")
            
            src_fname, src_path = "N/A", "N/A"
            if url:
                try:
                    path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
                    src_fname, src_path = os.path.basename(path), os.path.dirname(path)
                except Exception: pass
            
            offset_frames = int(unwrap(getattr(sc, 'start', 0)))
            start_frames = (genuine_start or 0) + offset_frames
            eff_name, eff_details = extract_effects_and_keyframes(effect_op, edit_rate)

            rows.append({
                "Event": idx, "Event Name": src_fname, "Clip Name": src_fname, "Source File Name": src_fname,
                "Source File Path": src_path, "DiskLabel": disk_label, "TapeID": tape_id,
                "SourceMobID": getattr(sc, 'mob_id', "N/A"), "TrackID": "N/A",
                "Source Clip EditRate": source_rate,
                "Timeline Start TC": frames_to_tc(ofs, edit_rate, drop),
                "Source Clip start time code": frames_to_tc(genuine_start, source_rate, source_drop),
                "Source Clip offset": frames_to_tc(offset_frames, source_rate, source_drop),
                "StartTime": frames_to_tc(start_frames, source_rate, source_drop),
                "End Time": frames_to_tc(start_frames + length, source_rate, source_drop),
                "Event Length": length, "Source Clip start (frames)": genuine_start or 0,
                "Source Clip offset (frames)": offset_frames, "StartTime (frames)": start_frames,
                "Effect Name": eff_name, "Keyframe Details": eff_details,
                "Orig Source Clip length": orig_len,
            })

    out_csv = args.out
    if not out_csv:
        sanitized = comp.name.replace("/", "_").replace("\\", "_")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv = f"{sanitized}_super_edl_fx_report_{ts}.csv"
        
    if not rows:
        print("Warning: No clip events were found on the timeline.")
        return

    with open(out_csv, "w", newline="", encoding="utf-8") as fcsv:
        writer = csv.DictWriter(fcsv, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"Wrote {len(rows)} events to: {out_csv}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)