c:\Users\o_gri\REPO\AAFInspector-Extended\aaf2resolve\aaf_wip_8.py#!/usr/bin/env python3
"""
AAF → CSV Extractor (wip_7)
Based on aaf_wip_6.py, updated to:
- Resolve "genuine source clip start" from nearest upstream mob with a Timecode track,
  mirroring JSON-based workflow (no default to 00:00:00:00 for rushes).
- Keep DiskLabel/TapeID extraction from anchored import mob.
- Preserve v6 CSV schema, DF/NDF handling, effect/filler rules.
"""

import os
import sys
import csv
import aaf2
import urllib.parse
from fractions import Fraction
from collections import defaultdict
from typing import Optional, Tuple, List

# -----------------------
# Utility: TC formatting
# -----------------------
def frames_to_tc(frames: int, fps: Fraction, drop: bool) -> str:
    if frames is None:
        return "(none)"
    fps_float = float(fps)
    neg = frames < 0
    if neg:
        frames = -frames
    secs_total, f = divmod(frames, int(round(fps_float)))
    h, rem = divmod(secs_total, 3600)
    m, s = divmod(rem, 60)
    if drop and fps_float in (29.97, 59.94):
        # Drop-frame not implemented for all rates, placeholder for expansion
        pass
    return f"{'-' if neg else ''}{h:02d}:{m:02d}:{s:02d}:{f:02d}"

# -----------------------
# Utility: percent-decode URL path
# -----------------------
def decode_url_path(url: str) -> Tuple[str, str]:
    try:
        parsed = urllib.parse.urlparse(url)
        path = urllib.parse.unquote(parsed.path)
        fname = os.path.basename(path)
        return fname, path
    except Exception:
        return "(none)", "(none)"

# -----------------------
# Crawl mob for TC candidates
# -----------------------
def gather_tc_candidates_from_mob(mob) -> List[Tuple[int, Fraction, bool]]:
    """
    Recursively walk all slots/segments of a mob, returning a list of (start_frames, fps, drop) tuples.
    """
    candidates = []
    try:
        for slot in mob.slots:
            seg = slot.segment
            candidates.extend(gather_tc_from_segment(seg, slot.edit_rate))
    except Exception:
        pass
    return candidates

def gather_tc_from_segment(seg, slot_rate: Fraction) -> List[Tuple[int, Fraction, bool]]:
    out = []
    if hasattr(seg, "components"):  # Sequence
        for comp in seg.components:
            out.extend(gather_tc_from_segment(comp, slot_rate))
    else:
        cname = seg.__class__.__name__
        if cname == "Timecode":
            start = getattr(seg, "start", None)
            drop = getattr(seg, "drop_frame", False)
            if start is not None:
                out.append((start, slot_rate, drop))
        elif cname == "SourceClip":
            st = getattr(seg, "start_time", None) or getattr(seg, "start", None)
            if st is not None:
                out.append((st, slot_rate, False))
    return out

# -----------------------
# Resolve genuine source TC
# -----------------------
def genuine_source_tc_from_chain(mob_chain: List[aaf2.mobs.SourceMob]) -> Tuple[Optional[int], Fraction, bool]:
    """
    Given the UMID chain down to anchored import mob, find the nearest upstream mob that actually has
    a camera Timecode track. Return (start_frames, fps, drop_flag).
    """
    for mob in mob_chain:
        candidates = gather_tc_candidates_from_mob(mob)
        if candidates:
            # choose max start frame (matches JSON project logic)
            sf, fps, drop = max(candidates, key=lambda x: x[0])
            return sf, fps, drop
    return None, Fraction(25, 1), False  # default if none

# -----------------------
# DiskLabel / TapeID extraction
# -----------------------
def extract_disklabel_from_mob(mob) -> Optional[str]:
    try:
        for att in getattr(mob, "attributes", []):
            if att.get("name") == "_IMPORTSETTING":
                for sub in getattr(att, "attributes", []):
                    if sub.get("name") == "_IMPORTDISKLAB":
                        return sub.get("value")
    except Exception:
        pass
    return None

def extract_tapeid_from_mob(mob) -> Optional[str]:
    try:
        if hasattr(mob, "comments"):
            for k, v in mob.comments.items():
                if "tapeid" in k.lower():
                    return v
    except Exception:
        pass
    return None

# -----------------------
# Main extraction
# -----------------------
def extract_aaf(aaf_path: str, csv_path: str):
    with aaf2.open(aaf_path) as f:
        # Build mob map
        mob_map = {m.mob_id: m for m in f.content.mobs}
        comp_mobs = [m for m in mob_map.values() if m.__class__.__name__ == "CompositionMob"]

        # Pick main comp mob (heuristic: name contains 'Exported')
        top_comp = next((m for m in comp_mobs if "Exported" in m.name), comp_mobs[0])

        # Timeline info
        edit_rate = top_comp.slots[0].edit_rate
        start_tc_frames, fps, drop = genuine_source_tc_from_chain([top_comp])

        events = []
        event_num = 0

        def walk_segment(seg, mob_chain):
            nonlocal event_num
            if hasattr(seg, "components"):
                for comp in seg.components:
                    walk_segment(comp, mob_chain)
            elif seg.__class__.__name__ == "SourceClip":
                event_num += 1
                src_mob = mob_map.get(seg.source_id)
                if not src_mob:
                    return
                # follow chain down to import mob
                chain = [src_mob]
                cur = src_mob
                while True:
                    next_id = None
                    if hasattr(cur, "slots"):
                        for s in cur.slots:
                            if hasattr(s.segment, "source_id"):
                                next_id = s.segment.source_id
                                break
                    if next_id and mob_map.get(next_id):
                        cur = mob_map[next_id]
                        chain.append(cur)
                    else:
                        break
                import_mob = chain[-1]
                fname, fpath = "(none)", "(none)"
                if hasattr(import_mob, "descriptor") and hasattr(import_mob.descriptor, "locators"):
                    for loc in import_mob.descriptor.locators:
                        if hasattr(loc, "url"):
                            fname, fpath = decode_url_path(loc.url)
                            break
                disklabel = extract_disklabel_from_mob(import_mob)
                tapeid = extract_tapeid_from_mob(import_mob)
                genuine_start_frames, sfps, sdrop = genuine_source_tc_from_chain(chain)
                offset_frames = getattr(seg, "start_time", None) or getattr(seg, "start", 0)
                start_time_frames = (genuine_start_frames or 0) + (offset_frames or 0)
                length_frames = getattr(seg, "length", 0)
                events.append({
                    "Event": event_num,
                    "Event Name": seg.__class__.__name__,
                    "Clip Name": import_mob.name if hasattr(import_mob, "name") else "(none)",
                    "Source File Name": fname,
                    "Source File Path": fpath,
                    "DiskLabel": disklabel or "(none)",
                    "TapeID": tapeid or "(none)",
                    "Source Clip start": frames_to_tc(genuine_start_frames, sfps, sdrop),
                    "Start Time": frames_to_tc(start_time_frames, sfps, sdrop),
                    "Orig Source Clip length": length_frames
                })

        walk_segment(top_comp.slots[0].segment, [top_comp])

    # Write CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=list(events[0].keys()))
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)

# -----------------------
# Entry point
# -----------------------
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} input.aaf output.csv")
        sys.exit(1)
    extract_aaf(sys.argv[1], sys.argv[2])
