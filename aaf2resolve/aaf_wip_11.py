#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AAF → Super EDL + FX (WIP-11h)

Key points implemented:
- Source Clip start time code is taken from the nearest upstream mob that actually holds a Timecode segment
  (like your JSON workflow), not from the local SourceClip.start.
- StartTime (frames) = upstream_tc_start_frames + source_offset_frames.
- Source clips with effects are NEVER replaced by placeholders.
- FX ON FILLER → emit placeholder PNG named from the AVX effect (except Pan & Zoom special case).
- Pan & Zoom:
    - On a source clip: treat as a normal source event; do not replace; we still record effect name.
    - On filler: attempt to extract a binary URL (sanitize illegal filename characters). Only if no URL
      can be found (should be rare) do we fall back to a placeholder.

Notes:
- pyaaf2 tested with 1.4.0 in this sandbox.
- DiskLabel/TapeID extraction is conservative here (grabs from Mob comments/attributes if exposed by pyaaf2).
  If your environment exposes _IMPORTSETTING→TaggedValueAttributeList→_IMPORTDISKLAB, wire it in the
  `extract_labels` helper.

CLI:
    python aaf_superedl_wip11h.py --aaf "/path/to/sequence.aaf" --out "/path/to/out.csv"
"""

import argparse
import csv
import os
import re
from typing import Optional, Tuple, List, Dict, Any
import aaf2

# --------------------------
# Utilities
# --------------------------

def frames_to_tc(frame_count: Optional[int], fps: float = 25.0, drop: bool = False) -> str:
    if frame_count is None or fps is None or fps <= 0:
        return "N/A"
    try:
        fc = int(frame_count)
        int_fps = max(1, round(float(fps)))
        h = fc // (3600 * int_fps)
        m = (fc % (3600 * int_fps)) // (60 * int_fps)
        s = (fc % (60 * int_fps)) // int_fps
        f = fc % int_fps
        sep = ";" if drop else ":"
        return f"{h:02}:{m:02}:{s:02}{sep}{f:02}"
    except Exception:
        return "N/A"

_illegal = re.compile(r'[<>:"/\\|?*\x00-\x1F]')

def sanitize_filename(name: str) -> str:
    # Replace illegal characters with underscore; also strip trailing dots/spaces (Windows-safe).
    if not name:
        return "unknown"
    clean = _illegal.sub("_", name)
    return clean.strip(" .")

# --------------------------
# Effect naming / detection
# --------------------------

def op_effect_name(op) -> str:
    """
    Try to extract AVX effect name from ComponentAttributeList (_EFFECT_PLUGIN_NAME/_CLASS).
    Fallback to OperationDefinition name.
    """
    try:
        # pyaaf2 exposes op.operationdef (OperationDefinition)
        if hasattr(op, "operationdef") and getattr(op.operationdef, "name", None):
            fallback = op.operationdef.name
        else:
            fallback = "Unknown Effect"
    except Exception:
        fallback = "Unknown Effect"

    # pyaaf2 does not always expose component attribute list directly; try best-effort
    # Many AVX effects still have the opdef name readable; normalize a bit.
    if fallback:
        nice = fallback.replace("_v2", "").replace("_2", "").replace("_", " ").strip()
        return nice
    return "Unknown Effect"

def is_pan_and_zoom(effect_name: str) -> bool:
    e = effect_name.lower()
    return ("pan" in e and "zoom" in e) or ("avid" in e and "zoom" in e)

# --------------------------
# Sequence & traversal helpers
# --------------------------

def pick_picture_sequence_slot(comp) -> Optional[Any]:
    """Prefer a Sequence segment slot (picture). If multiple, pick the one with most components."""
    seq_slots = [s for s in getattr(comp, "slots", []) if getattr(s, "segment", None).__class__.__name__ == "Sequence"]
    if not seq_slots:
        return None
    # choose the one with most components (often the main picture track)
    seq_slots.sort(key=lambda s: len(getattr(s.segment, "components", [])), reverse=True)
    return seq_slots[0]

def has_nested_sourceclip(node) -> bool:
    cn = node.__class__.__name__
    if cn == "SourceClip":
        return True
    if cn == "Sequence":
        for i in range(len(node.components)):
            if has_nested_sourceclip(node.components.get(i)):
                return True
    if cn == "OperationGroup":
        segs = getattr(node, "segments", None)
        if segs:
            for i in range(len(segs)):
                if has_nested_sourceclip(segs.get(i)):
                    return True
    return False

def first_nested_sourceclip(node) -> Optional[Any]:
    """Find the first SourceClip under node by descending through Sequence/OperationGroup/segments."""
    cn = node.__class__.__name__
    if cn == "SourceClip":
        return node
    if cn == "Sequence":
        for i in range(len(node.components)):
            r = first_nested_sourceclip(node.components.get(i))
            if r:
                return r
    if cn == "OperationGroup":
        segs = getattr(node, "segments", None)
        if segs:
            for i in range(len(segs)):
                r = first_nested_sourceclip(segs.get(i))
                if r:
                    return r
    return None

def collect_all_nested_sourceclips(node, bag: List[Any]) -> None:
    cn = node.__class__.__name__
    if cn == "SourceClip":
        bag.append(node); return
    if cn == "Sequence":
        for i in range(len(node.components)):
            collect_all_nested_sourceclips(node.components.get(i), bag)
    if cn == "OperationGroup":
        segs = getattr(node, "segments", None)
        if segs:
            for i in range(len(segs)):
                collect_all_nested_sourceclips(segs.get(i), bag)

# --------------------------
# Upstream Timecode walk
# --------------------------

def find_upstream_tc_frames(start_mob, mob_map: Dict[str, Any]) -> Tuple[Optional[int], Optional[bool], Optional[float], Optional[Any]]:
    """
    BFS from start_mob following slots/segments to find the nearest mob with a Timecode segment.
    Returns (tc_start_frames, is_drop_frame, fps, mob_with_tc)
    """
    from collections import deque
    visited = set()
    q = deque([start_mob])
    while q:
        mob = q.popleft()
        if not mob or mob.mob_id in visited:
            continue
        visited.add(mob.mob_id)

        # Check for Timecode slot(s)
        try:
            for s in getattr(mob, "slots", []):
                seg = getattr(s, "segment", None)
                if seg and seg.__class__.__name__ == "Timecode":
                    tc_start = int(getattr(seg, "start", 0) or 0)
                    is_drop = bool(getattr(seg, "drop_frame", False))
                    # fps is not always present; assume 25 if not readable
                    fps = 25.0
                    # Some AAFs store edit rate on slots or timeline; if accessible, you can wire it here.
                    return tc_start, is_drop, fps, mob
        except Exception:
            pass

        # Enqueue outward references
        try:
            for s in getattr(mob, "slots", []):
                seg = getattr(s, "segment", None)
                def enqueue(seg):
                    if not seg: return
                    cn = seg.__class__.__name__
                    if cn == "SourceClip":
                        mid = getattr(seg, "mob_id", None)
                        if mid and mid in mob_map:
                            q.append(mob_map[mid])
                    elif cn == "Sequence":
                        for k in range(len(seg.components)):
                            enqueue(seg.components.get(k))
                    elif cn == "OperationGroup":
                        segs = getattr(seg, "segments", None)
                        if segs:
                            for k in range(len(segs)):
                                enqueue(segs.get(k))
                enqueue(seg)
        except Exception:
            pass
    return None, None, None, None

def find_any_import_url_in_chain(start_mob, mob_map: Dict[str, Any]) -> Optional[str]:
    """
    Walk outward from start_mob to find any ImportDescriptor→NetworkLocator URL (for sanity/path hints).
    """
    from collections import deque
    visited = set()
    q = deque([start_mob])
    while q:
        mob = q.popleft()
        if not mob or mob.mob_id in visited:
            continue
        visited.add(mob.mob_id)

        desc = getattr(mob, "descriptor", None)
        if desc and desc.__class__.__name__ == "ImportDescriptor":
            for loc in getattr(desc, "locators", []):
                if getattr(loc, "url", None):
                    return str(loc.url)

        # Walk outward via slots
        try:
            for s in getattr(mob, "slots", []):
                seg = getattr(s, "segment", None)
                def enqueue(seg):
                    if not seg: return
                    cn = seg.__class__.__name__
                    if cn == "SourceClip":
                        mid = getattr(seg, "mob_id", None)
                        if mid and mid in mob_map:
                            q.append(mob_map[mid])
                    elif cn == "Sequence":
                        for k in range(len(seg.components)):
                            enqueue(seg.components.get(k))
                    elif cn == "OperationGroup":
                        segs = getattr(seg, "segments", None)
                        if segs:
                            for k in range(len(segs)):
                                enqueue(segs.get(k))
                enqueue(seg)
        except Exception:
            pass
    return None

# --------------------------
# Labels (DiskLabel/TapeID) – conservative best-effort
# --------------------------

def extract_labels(mob) -> Tuple[str, str]:
    """
    Best-effort pull for DiskLabel & TapeID. If your env exposes _IMPORTSETTING->_IMPORTDISKLAB,
    swap this to crawl that path instead.
    """
    disk = ""
    tape = ""
    try:
        # Try mob.comments (UserComments) if present
        if hasattr(mob, "comments"):
            for c in mob.comments:
                name = str(getattr(c, "name", "") or "")
                value = str(getattr(c, "value", "") or "")
                if not disk and name.lower() in ("disklabel", "_importdisklab", "disk label"):
                    disk = value
                if not tape and name.lower() in ("tapeid", "tape id"):
                    tape = value
    except Exception:
        pass
    return disk, tape

# --------------------------
# Event building
# --------------------------

def build_events_from_sequence(f, comp, seq, timeline_fps: float = 25.0) -> List[Dict[str, Any]]:
    """
    Walk the top-level components of the Sequence (picture track), building EDL-like events.
    """
    # Base sequence timeline start (from any Timecode slot on the comp)
    t0_frames, is_drop = 0, False
    for s in getattr(comp, "slots", []):
        seg = getattr(s, "segment", None)
        if seg and seg.__class__.__name__ == "Timecode":
            t0_frames = int(getattr(seg, "start", 0) or 0)
            is_drop = bool(getattr(seg, "drop_frame", False))
            break

    mob_map = {m.mob_id: m for m in f.content.mobs.values()}

    events: List[Dict[str, Any]] = []
    timeline_offset = 0

    for i in range(len(seq.components)):
        top = seq.components.get(i)
        cn = top.__class__.__name__
        length = int(getattr(top, "length", 0) or 0)

        # Skip pure filler (still advance timeline)
        if cn == "Filler":
            timeline_offset += length
            continue

        # Detect FX on filler (OperationGroup without nested SourceClip)
        if cn == "OperationGroup" and not has_nested_sourceclip(top):
            eff = op_effect_name(top)
            # Pan & Zoom on filler: try to extract a URL; if found, emit a PZ source-like event (not placeholder)
            pz = is_pan_and_zoom(eff)
            file_name = ""
            file_path = ""
            if pz:
                # try to pull a URL by scanning outward from any referenced mob in segments (best-effort)
                # If no mob is directly referenced, keep as unknown.
                url = None
                segs = getattr(top, "segments", None)
                if segs:
                    for k in range(len(segs)):
                        seg = segs.get(k)
                        sc = first_nested_sourceclip(seg)
                        if sc:
                            url = find_any_import_url_in_chain(sc.mob, mob_map)
                            if url:
                                break
                if url:
                    # parse into dir/name
                    # URLs may be "file://server/share/dir/file.ext" — keep path-ish components
                    u = str(url)
                    # crude parsing without urlparse to keep unicode; split by "/" and take tail
                    tail = u.split("/")[-1]
                    file_name = sanitize_filename(tail)
                    file_path = "/".join(u.split("/")[:-1])
                    # Emit as a non-placeholder "PZ_ON_FILLER" with source-ish info
                    events.append({
                        "Event Name": f"(P&Z Filler) {file_name}",
                        "Clip Name": file_name,
                        "Effect Name": eff,
                        "Source File Name": file_name,
                        "Source File Path": file_path,
                        "DiskLabel": "",
                        "TapeID": "",
                        "SourceMobID": "PZ_ON_FILLER",
                        "TrackID": "VFX",
                        "Source Clip EditRate": timeline_fps,
                        "Timeline Start TC": frames_to_tc(t0_frames + timeline_offset, timeline_fps, is_drop),
                        "Source Clip start time code": "N/A",
                        "StartTime (frames)": None,
                        "Source Offset (frames)": 0,
                        "Orig Source Clip length": None,
                        "Length": length,
                        "IsDropFrame": is_drop
                    })
                else:
                    # Fallback (should be rare per your note)
                    ph_name = sanitize_filename(eff) + "_placeholder.png"
                    events.append({
                        "Event Name": f"(FX on Filler) {eff}",
                        "Clip Name": eff,
                        "Effect Name": eff,
                        "Source File Name": ph_name,
                        "Source File Path": "placeholder://",
                        "DiskLabel": "",
                        "TapeID": "",
                        "SourceMobID": "FX_ON_FILLER",
                        "TrackID": "VFX",
                        "Source Clip EditRate": timeline_fps,
                        "Timeline Start TC": frames_to_tc(t0_frames + timeline_offset, timeline_fps, is_drop),
                        "Source Clip start time code": "N/A",
                        "StartTime (frames)": None,
                        "Source Offset (frames)": 0,
                        "Orig Source Clip length": None,
                        "Length": length,
                        "IsDropFrame": is_drop
                    })
            else:
                # Generic FX on filler → placeholder named from effect
                ph_name = sanitize_filename(eff) + "_placeholder.png"
                events.append({
                    "Event Name": f"(FX on Filler) {eff}",
                    "Clip Name": eff,
                    "Effect Name": eff,
                    "Source File Name": ph_name,
                    "Source File Path": "placeholder://",
                    "DiskLabel": "",
                    "TapeID": "",
                    "SourceMobID": "FX_ON_FILLER",
                    "TrackID": "VFX",
                    "Source Clip EditRate": timeline_fps,
                    "Timeline Start TC": frames_to_tc(t0_frames + timeline_offset, timeline_fps, is_drop),
                    "Source Clip start time code": "N/A",
                    "StartTime (frames)": None,
                    "Source Offset (frames)": 0,
                    "Orig Source Clip length": None,
                    "Length": length,
                    "IsDropFrame": is_drop
                })

            timeline_offset += length
            continue

        # Otherwise: it’s a normal top-level component that (somewhere under it) contains a SourceClip
        sc = first_nested_sourceclip(top)
        if not sc:
            timeline_offset += length
            continue

        # Effect detection on the top-level container for labeling (OperationGroup wrapper)
        eff_name = ""
        if cn == "OperationGroup":
            eff_name = op_effect_name(top)

        # Resolve genuine (upstream) timecode and labels starting from the Master/Source mob
        start_mob = sc.mob  # master mob if sc lives under a master, else source mob
        tc_start_frames, upstream_drop, upstream_fps, tc_mob = find_upstream_tc_frames(start_mob, mob_map)

        # If we failed to find a TC upstream, fall back to comp's base (but still record 0/unknown)
        if tc_start_frames is None:
            tc_start_frames = 0
            upstream_drop = is_drop
            upstream_fps = timeline_fps
            tc_mob = start_mob

        # DiskLabel/TapeID (best effort on the mob that provided TC; if none, on start_mob)
        disk, tape = extract_labels(tc_mob or start_mob)

        # Compute StartTime (frames) and the TC string for source clip start
        source_offset = int(getattr(sc, "start", 0) or 0)
        start_frames = int(tc_start_frames) + source_offset

        # Source file hint (from any ImportDescriptor URL in the chain)
        src_url = find_any_import_url_in_chain(start_mob, mob_map)
        src_name = ""
        src_path = ""
        if src_url:
            tail = src_url.split("/")[-1]
            src_name = sanitize_filename(tail)
            src_path = "/".join(src_url.split("/")[:-1])

        # Compose event row
        clip_name = getattr(start_mob, "name", "") or src_name or "Clip"
        events.append({
            "Event Name": clip_name,
            "Clip Name": clip_name,
            "Effect Name": eff_name or "N/A",
            "Source File Name": src_name or "N/A",
            "Source File Path": src_path or "N/A",
            "DiskLabel": disk,
            "TapeID": tape,
            "SourceMobID": getattr(start_mob, "mob_id", ""),
            "TrackID": getattr(sc, "source_slot_id", "") or "",
            "Source Clip EditRate": upstream_fps or timeline_fps,
            "Timeline Start TC": frames_to_tc(t0_frames + timeline_offset, timeline_fps, is_drop),
            "Source Clip start time code": frames_to_tc(start_frames, upstream_fps or timeline_fps, bool(upstream_drop)),
            "StartTime (frames)": start_frames,
            "Source Offset (frames)": source_offset,
            "Orig Source Clip length": int(getattr(sc, "length", 0) or 0),
            "Length": length,
            "IsDropFrame": bool(upstream_drop)
        })

        timeline_offset += length

    # Inject Event number now to keep numbering contiguous after any skips
    for i, e in enumerate(events, 1):
        e["Event"] = i
    return events

# --------------------------
# CSV ordering
# --------------------------

REF_ORDER = [
    "Event",
    "Event Name",
    "Clip Name",
    "Effect Name",
    "Source File Name",
    "Source File Path",
    "DiskLabel",
    "TapeID",
    "SourceMobID",
    "TrackID",
    "Source Clip EditRate",
    "Timeline Start TC",
    "Source Clip start time code",
    "StartTime (frames)",
    "Source Offset (frames)",
    "Orig Source Clip length",
    "Length",
    "IsDropFrame",
]

def write_csv(rows: List[Dict[str, Any]], out_path: str) -> None:
    # Ensure all columns exist
    for r in rows:
        for k in REF_ORDER:
            if k not in r:
                r[k] = ""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REF_ORDER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

# --------------------------
# Main
# --------------------------

def main():
    ap = argparse.ArgumentParser(description="AAF → Super EDL + FX (WIP-11h)")
    ap.add_argument("--aaf", required=True, help="Path to AAF")
    ap.add_argument("--out", required=True, help="Output CSV")
    ap.add_argument("--slot", type=int, default=None, help="Force picture Sequence slot id (optional)")
    args = ap.parse_args()

    aaf_path = args.aaf
    out_path = args.out

    with aaf2.open(aaf_path, "r") as f:
        # choose composition (prefer the one with 'SEQ' in name)
        comps = [x for x in f.content.mobs.values() if x.__class__.__name__ == "CompositionMob"]
        comp = None
        for c in comps:
            nm = (getattr(c, "name", "") or "").lower()
            if "seq" in nm and "test" in nm:
                comp = c
                break
        if not comp:
            comp = comps[0] if comps else None
        if not comp:
            raise RuntimeError("No CompositionMob found.")

        # choose picture sequence slot
        slot = None
        if args.slot is not None:
            for s in getattr(comp, "slots", []):
                if getattr(s, "slot_id", None) == args.slot and getattr(s, "segment", None).__class__.__name__ == "Sequence":
                    slot = s; break
        if not slot:
            slot = pick_picture_sequence_slot(comp)
        if not slot:
            raise RuntimeError("No Sequence slot found on CompositionMob.")

        seq = slot.segment
        rows = build_events_from_sequence(f, comp, seq, timeline_fps=25.0)
        write_csv(rows, out_path)

        print(f"OK: wrote {len(rows)} rows → {out_path}")

if __name__ == "__main__":
    main()
