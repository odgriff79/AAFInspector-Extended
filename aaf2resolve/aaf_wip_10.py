#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AAF → CSV Extractor (wip_8)

Key rules implemented:
1) Source clip start = from the nearest upstream mob that actually carries the camera Timecode track.
2) FX on filler → placeholder PNG named from AVX effect type.
3) Special case: Avid Pan & Zoom
   - On filler: decode still path from the effect’s hidden/binary parameter (UTF-16LE), sanitize, use it (NEVER placeholder).
   - On a source clip: override clip path with the decoded still path, sanitize.
4) DiskLabel/TapeID from anchored import mob; Orig Source Clip length from the hop that points to the import mob.
5) CSV shape includes effect fields (kept close to your JSON workflow).

Tested with pyaaf2==1.4.0.
"""

import os
import re
import sys
import csv
import aaf2
import urllib.parse
from fractions import Fraction
from typing import Optional, Tuple, List, Dict, Any

# -----------------------
# File/path helpers
# -----------------------
ILLEGAL_CHARS = r'[\\/*?:"<>|\x00-\x1F]'

def sanitize_filename(name: str) -> str:
    if not name:
        return "unnamed"
    # strip URL query if present
    name = name.split("?")[0]
    base = os.path.basename(name)
    base = re.sub(ILLEGAL_CHARS, "_", base)
    # collapse repeats
    base = re.sub(r"_+", "_", base).strip("_")
    return base or "unnamed"

def sanitize_dirpath(path: str) -> str:
    if not path:
        return ""
    # percent-decode and normalize slashes
    dec = urllib.parse.unquote(path)
    dec = dec.replace("\\", "/")
    # remove control chars
    dec = re.sub(r'[\x00-\x1F]', "", dec)
    # collapse //
    dec = re.sub(r"/{2,}", "/", dec)
    return dec

def split_path(path: str) -> Tuple[str, str]:
    if not path:
        return "N/A", "N/A"
    path = sanitize_dirpath(path)
    fname = sanitize_filename(path)
    dirp = sanitize_dirpath(os.path.dirname(path))
    return fname or "N/A", dirp or "N/A"

def decode_url_path(url: str) -> Tuple[str, str]:
    try:
        u = urllib.parse.urlparse(url)
        p = urllib.parse.unquote(u.path)
        return split_path(p)
    except Exception:
        return "N/A", "N/A"

# -----------------------
# Timecode helpers
# -----------------------
def frames_to_tc(frames: int, fps: Fraction, drop: bool) -> str:
    if frames is None:
        return "N/A"
    neg = frames < 0
    if neg:
        frames = -frames
    fps_int = int(round(float(fps)))
    secs, ff = divmod(frames, fps_int)
    hh, rem = divmod(secs, 3600)
    mm, ss = divmod(rem, 60)
    # (Drop-frame formatting beyond 29.97/59.94 can be added if needed)
    sep = ";" if drop and float(fps) in (29.97, 59.94) else ":"
    return f"{'-' if neg else ''}{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"

# -----------------------
# Traversal: TC candidates
# -----------------------
def _gather_tc_from_segment(seg, slot_rate: Fraction) -> List[Tuple[int, Fraction, bool]]:
    out: List[Tuple[int, Fraction, bool]] = []
    try:
        # Sequence
        if hasattr(seg, "components"):
            for c in seg.components:
                out.extend(_gather_tc_from_segment(c, slot_rate))
            return out
        cname = seg.__class__.__name__
        if cname == "Timecode":
            start = getattr(seg, "start", None)
            drop = getattr(seg, "drop_frame", False)
            if start is not None:
                out.append((int(start), slot_rate, bool(drop)))
        elif cname == "SourceClip":
            st = getattr(seg, "start_time", None)
            if st is None:
                st = getattr(seg, "start", None)
            if st is not None:
                out.append((int(st), slot_rate, False))
    except Exception:
        pass
    return out

def _tc_candidates_from_mob(mob) -> List[Tuple[int, Fraction, bool]]:
    cands: List[Tuple[int, Fraction, bool]] = []
    try:
        for slot in getattr(mob, "slots", []):
            seg = getattr(slot, "segment", None)
            rate = getattr(slot, "edit_rate", Fraction(25, 1))
            if seg is not None:
                cands.extend(_gather_tc_from_segment(seg, rate))
    except Exception:
        pass
    return cands

def genuine_source_tc_from_chain(mob_chain: List[Any]) -> Tuple[Optional[int], Fraction, bool]:
    """Return (start_frames, fps, drop) from the nearest upstream mob holding camera timecode."""
    for mob in mob_chain:
        cands = _tc_candidates_from_mob(mob)
        if cands:
            # Match prior JSON workflow: take the maximum start frame
            sf, fps, drop = max(cands, key=lambda x: x[0])
            return int(sf), fps, bool(drop)
    return None, Fraction(25, 1), False

# -----------------------
# DiskLabel / TapeID extraction
# -----------------------
def extract_disklabel_from_mob(mob) -> Optional[str]:
    try:
        for att in getattr(mob, "attributes", []):
            # Attribute may expose name/value; fallbacks for dict-like
            name = getattr(att, "name", None) or (att.get("name") if isinstance(att, dict) else None)
            if name and name in ("_IMPORTSETTING", "DiskLabel", "_IMPORTDISKLAB"):
                # Dive into nested attributes if present
                for sub in getattr(att, "attributes", []):
                    sname = getattr(sub, "name", None) or (sub.get("name") if isinstance(sub, dict) else None)
                    if sname and sname in ("_IMPORTDISKLAB", "DiskLabel"):
                        val = getattr(sub, "value", None) or (sub.get("value") if isinstance(sub, dict) else None)
                        if val:
                            return str(val)
    except Exception:
        pass
    # Fallback: comments
    try:
        for k, v in getattr(mob, "comments", {}).items():
            if "disk" in k.lower():
                return str(v)
    except Exception:
        pass
    return None

def extract_tapeid_from_mob(mob) -> Optional[str]:
    try:
        for k, v in getattr(mob, "comments", {}).items():
            if "tape" in k.lower():
                return str(v)
    except Exception:
        pass
    # Attribute fallback
    try:
        for att in getattr(mob, "attributes", []):
            name = getattr(att, "name", None) or (att.get("name") if isinstance(att, dict) else None)
            if name and name.lower() == "tapeid":
                val = getattr(att, "value", None) or (att.get("value") if isinstance(att, dict) else None)
                if val:
                    return str(val)
    except Exception:
        pass
    return None

# -----------------------
# Build mob map and follow chain
# -----------------------
def build_mob_map(f) -> Dict[str, Any]:
    return {m.mob_id: m for m in f.content.mobs}

def follow_chain_to_import(start_mob, mob_map: Dict[str, Any]) -> List[Any]:
    """Follow UMID chain by looking at SourceClip references in slots until no further hop is found."""
    chain = [start_mob]
    cur = start_mob
    visited = {start_mob.mob_id}
    while True:
        next_id = None
        try:
            for s in getattr(cur, "slots", []):
                seg = getattr(s, "segment", None)
                # dig into Sequence to find SourceClip
                if hasattr(seg, "components"):
                    for comp in seg.components:
                        if comp.__class__.__name__ == "SourceClip":
                            next_id = getattr(comp, "source_id", None)
                            edge_len = getattr(comp, "length", None)
                            # attach the edge length for possible use later
                            setattr(cur, "_edge_to_next_length", int(edge_len) if edge_len is not None else None)
                            break
                elif seg and seg.__class__.__name__ == "SourceClip":
                    next_id = getattr(seg, "source_id", None)
                    edge_len = getattr(seg, "length", None)
                    setattr(cur, "_edge_to_next_length", int(edge_len) if edge_len is not None else None)
                if next_id:
                    break
        except Exception:
            next_id = None
        if not next_id or next_id in visited or next_id not in mob_map:
            break
        cur = mob_map[next_id]
        chain.append(cur)
        visited.add(next_id)
    return chain  # last item is the anchored import (or master) mob

def last_edge_length_along_chain(chain: List[Any]) -> int:
    """
    Orig Source Clip length = the SourceClip.length on the *hop that points to the end mob*.
    """
    if not chain or len(chain) == 1:
        # fallback unknown
        return 0
    prev = chain[-2]
    # when following we stored _edge_to_next_length on prev when we found the hop to next
    return int(getattr(prev, "_edge_to_next_length", 0) or 0)

# -----------------------
# Effect detection / extraction
# -----------------------
def effect_name_from_opgroup(opg) -> str:
    # Try Avid plugin attributes
    try:
        for att in getattr(opg, "attributes", []):
            nm = getattr(att, "name", None)
            val = getattr(att, "value", None)
            if nm == "_EFFECT_PLUGIN_NAME" and val:
                plugin_name = str(val)
                # class?
                cls = None
                for att2 in getattr(opg, "attributes", []):
                    if getattr(att2, "name", None) == "_EFFECT_PLUGIN_CLASS":
                        cls = getattr(att2, "value", None)
                        break
                if cls:
                    return f"{cls} : {plugin_name}"
                return plugin_name
    except Exception:
        pass
    # Fallback to operation definition name
    try:
        opdef = getattr(opg, "operation", None)
        if opdef is not None:
            nm = getattr(opdef, "name", None) or str(opdef)
            if nm:
                # normalize slightly
                return nm.replace("_v2", "").replace("_2", "").replace("_", " ").strip()
    except Exception:
        pass
    return "Unknown Effect"

_PZ_KEYS = ("Pan", "Zoom")  # loose detection across fields

def _maybe_decode_utf16le(raw) -> Optional[str]:
    try:
        if isinstance(raw, (bytes, bytearray)):
            txt = raw.decode("utf-16-le", errors="ignore")
            # strip leading noise before the first slash or drive letter
            m = re.search(r'([A-Za-z]:[\\/]|/|\\\\)', txt)
            if m:
                txt = txt[m.start():]
            txt = txt.rstrip("\x00")
            return txt.strip()
        if isinstance(raw, str):
            return raw
        # Some containers store list[int] bytes (from JSON dumps); handle generically
        if isinstance(raw, (list, tuple)) and raw and all(isinstance(b, int) for b in raw):
            return bytes(raw).decode("utf-16-le", errors="ignore")
    except Exception:
        return None
    return None

def extract_panzoom_filepath_from_opgroup(opg) -> Optional[str]:
    """
    Look for a still image path embedded in the OperationGroup for Avid Pan & Zoom.
    We scan parameters and attributes for anything that looks like a path/URL,
    decode UTF-16LE if necessary, then sanitize.
    """
    # 1) Parameters
    try:
        for p in getattr(opg, "parameters", []):
            # Common param names can vary; grab any value that looks like a path
            for attr_name in ("value", "val", "data", "data_value"):
                if hasattr(p, attr_name):
                    raw = getattr(p, attr_name)
                    txt = _maybe_decode_utf16le(raw)
                    if txt and ("/" in txt or "\\" in txt or txt.lower().startswith("file:")):
                        return sanitize_dirpath(txt)
            # Additionally, inspect a generic .value for nested structs
            v = getattr(p, "value", None)
            if v is not None:
                txt = _maybe_decode_utf16le(v)
                if txt and ("/" in txt or "\\" in txt or txt.lower().startswith("file:")):
                    return sanitize_dirpath(txt)
    except Exception:
        pass
    # 2) Attributes (ComponentAttributeList mirror)
    try:
        for att in getattr(opg, "attributes", []):
            val = getattr(att, "value", None)
            txt = _maybe_decode_utf16le(val)
            if txt and ("/" in txt or "\\" in txt or txt.lower().startswith("file:")):
                return sanitize_dirpath(txt)
    except Exception:
        pass
    # 3) Nothing found
    return None

def is_panzoom_effect(effect_name: str) -> bool:
    s = effect_name.lower()
    return "pan" in s and "zoom" in s  # robust to variations

def operationgroup_has_nested_source(seg) -> bool:
    """
    Return True if OperationGroup tree contains a SourceClip somewhere inside (i.e., not pure filler).
    """
    try:
        def walk(x):
            if x.__class__.__name__ == "SourceClip":
                return True
            if hasattr(x, "components"):
                for c in x.components:
                    if walk(c):
                        return True
            # OperationGroup.inputs may carry nested segments in some AAFs
            if hasattr(x, "input_segments"):
                for c in x.input_segments:
                    if c and walk(c):
                        return True
            return False
        return walk(seg)
    except Exception:
        return False

def collect_effects_and_events(top_seg, timeline_start_frames: int, top_rate: Fraction) -> Tuple[List[Dict], Dict[int, Dict]]:
    """
    Traverse the timeline; emit base events and gather effect nodes keyed by timeline start frame.
    Base events:
      - SourceClip events
      - FX_ON_FILLER pseudo-events for OperationGroups that contain no SourceClip
    """
    events: List[Dict] = []
    effects_by_frame: Dict[int, Dict] = {}

    def walk(seg, t_off: int, rate: Fraction):
        nonlocal events, effects_by_frame
        # Sequence
        if hasattr(seg, "components"):
            for comp in seg.components:
                walk(comp, t_off, rate)
                # accumulate timeline offset
                try:
                    ln = int(getattr(comp, "length", 0) or 0)
                    t_off += ln
                except Exception:
                    pass
            return

        cname = seg.__class__.__name__
        if cname == "OperationGroup":
            eff_name = effect_name_from_opgroup(seg)
            # record effect at this absolute timeline position
            effects_by_frame.setdefault(t_off, {"opg": seg, "name": eff_name, "length": int(getattr(seg, "length", 0) or 0)})
            # if no nested SourceClip → this is FX on filler → add pseudo-event
            if not operationgroup_has_nested_source(seg):
                events.append({
                    "kind": "FX_ON_FILLER",
                    "timeline_start": t_off,
                    "length": int(getattr(seg, "length", 0) or 0),
                    "rate": rate,
                })
            # Continue walking inputs in case there are nested segments (to advance t_off correctly)
            for c in getattr(seg, "input_segments", []):
                if c:
                    walk(c, t_off, rate)
            return

        if cname == "SourceClip":
            events.append({
                "kind": "SOURCE",
                "timeline_start": t_off,
                "length": int(getattr(seg, "length", 0) or 0),
                "rate": rate,
                "seg": seg,
            })
            return

        # Others: ignore

    walk(top_seg, timeline_start_frames, top_rate)
    return events, effects_by_frame

# -----------------------
# CSV extraction
# -----------------------
CSV_FIELDS = [
    "Event", "Event Name", "Clip Name",
    "Source File Name", "Source File Path",
    "DiskLabel", "TapeID",
    "SourceMobID", "TrackID", "Source Clip EditRate",
    "Timeline Start TC",
    "Source Clip start time code", "Source Clip offset",
    "StartTime", "End Time",
    "Event Length",
    "Source Clip start (frames)", "Source Clip offset (frames)", "StartTime (frames)",
    "Effect Name",
    "Orig Source Clip length",
]

def extract_aaf(aaf_path: str, csv_path: str):
    with aaf2.open(aaf_path) as f:
        mob_map = build_mob_map(f)
        comp_mobs = [m for m in mob_map.values() if m.__class__.__name__ == "CompositionMob"]
        if not comp_mobs:
            raise RuntimeError("No CompositionMob found.")
        # pick first video comp (heuristic)
        top_comp = comp_mobs[0]
        top_slot = top_comp.slots[0]
        top_rate = getattr(top_slot, "edit_rate", Fraction(25, 1))
        top_seg = top_slot.segment

        # Gather base events and timeline effects
        events, effects_by_frame = collect_effects_and_events(top_seg, 0, top_rate)

        rows = []
        ev_idx = 0

        # Build rows
        for e in events:
            ev_idx += 1
            startf = int(e["timeline_start"])
            length = int(e["length"])
            rate = e["rate"]
            eff = effects_by_frame.get(startf)  # may be None
            eff_name = eff["name"] if eff else "(none)"

            if e["kind"] == "FX_ON_FILLER":
                # If this is Pan & Zoom on filler → decode still path from opgroup binary, no placeholder
                if eff and is_panzoom_effect(eff_name):
                    still_path = extract_panzoom_filepath_from_opgroup(eff["opg"]) or ""
                    fname, dpath = split_path(still_path)
                    rows.append({
                        "Event": ev_idx,
                        "Event Name": f"(P&Z on Filler) {fname}",
                        "Clip Name": fname,
                        "Source File Name": fname,
                        "Source File Path": dpath,
                        "DiskLabel": "N/A",
                        "TapeID": "N/A",
                        "SourceMobID": "PZ_ON_FILLER",
                        "TrackID": "VFX",
                        "Source Clip EditRate": float(rate),
                        "Timeline Start TC": frames_to_tc(startf, rate, False),
                        "Source Clip start time code": "N/A",
                        "Source Clip offset": "N/A",
                        "StartTime": "N/A",
                        "End Time": "N/A",
                        "Event Length": length,
                        "Source Clip start (frames)": 0,
                        "Source Clip offset (frames)": 0,
                        "StartTime (frames)": 0,
                        "Effect Name": eff_name,
                        "Orig Source Clip length": length,
                    })
                else:
                    # Placeholder from AVX type
                    base = eff_name.split(":")[-1].strip().lower() if eff_name else "effect"
                    base = re.sub(r'[^0-9a-z]+', '_', base) or "effect"
                    placeholder = f"{base}_placeholder.png"
                    rows.append({
                        "Event": ev_idx,
                        "Event Name": f"{eff_name} on Filler",
                        "Clip Name": placeholder,
                        "Source File Name": placeholder,
                        "Source File Path": os.path.join("placeholders", placeholder),
                        "DiskLabel": "N/A",
                        "TapeID": "N/A",
                        "SourceMobID": "FX_ON_FILLER",
                        "TrackID": "VFX",
                        "Source Clip EditRate": float(rate),
                        "Timeline Start TC": frames_to_tc(startf, rate, False),
                        "Source Clip start time code": "01:00:00:00",
                        "Source Clip offset": "00:00:00:00",
                        "StartTime": "01:00:00:00",
                        "End Time": "01:00:00:00",
                        "Event Length": length,
                        "Source Clip start (frames)": 0,
                        "Source Clip offset (frames)": 0,
                        "StartTime (frames)": 0,
                        "Effect Name": eff_name,
                        "Orig Source Clip length": length,
                    })
                continue

            # SOURCE event
            seg = e["seg"]
            source_id = getattr(seg, "source_id", None)
            source_slot_id = getattr(seg, "source_slot_id", None) or getattr(seg, "source_mob_slot_id", None)
            src_offset = getattr(seg, "start_time", None)
            if src_offset is None:
                src_offset = getattr(seg, "start", 0)
            # get source mob and follow chain
            src_mob = mob_map.get(source_id)
            if not src_mob:
                # Graceful skip
                continue
            chain = follow_chain_to_import(src_mob, mob_map)
            end_mob = chain[-1]
            # DiskLabel / TapeID from end mob
            disklabel = extract_disklabel_from_mob(end_mob) or "N/A"
            tapeid = extract_tapeid_from_mob(end_mob) or "N/A"
            # Genuine source TC from nearest upstream mob with camera TC
            genuine_start_frames, sfps, sdrop = genuine_source_tc_from_chain(chain)
            genuine_start_frames = int(genuine_start_frames or 0)
            start_time_frames = genuine_start_frames + int(src_offset or 0)
            end_time_frames = start_time_frames + int(length)
            # original source clip length from hop to end mob
            orig_len = last_edge_length_along_chain(chain) or int(getattr(seg, "length", 0) or 0)

            # Resolve file path from end mob descriptor locators
            src_fname, src_path = "N/A", "N/A"
            try:
                desc = getattr(end_mob, "descriptor", None)
                locs = getattr(desc, "locators", []) if desc else []
                for loc in locs:
                    url = getattr(loc, "url", None)
                    if url:
                        src_fname, src_path = decode_url_path(url)
                        break
            except Exception:
                pass

            # If there is a Pan & Zoom effect at this timeline start and this is a real source,
            # override clip path with still path from binary parameter
            if eff and is_panzoom_effect(eff_name):
                still_path = extract_panzoom_filepath_from_opgroup(eff["opg"]) or ""
                if still_path:
                    pz_fname, pz_path = split_path(still_path)
                    src_fname, src_path = pz_fname, pz_path
                    source_id = "PZ_OVERRIDE"

            rows.append({
                "Event": ev_idx,
                "Event Name": src_fname,
                "Clip Name": src_fname,
                "Source File Name": src_fname,
                "Source File Path": src_path,
                "DiskLabel": disklabel,
                "TapeID": tapeid,
                "SourceMobID": str(source_id),
                "TrackID": str(source_slot_id or "N/A"),
                "Source Clip EditRate": float(sfps),
                "Timeline Start TC": frames_to_tc(startf, rate, False),
                "Source Clip start time code": frames_to_tc(genuine_start_frames, sfps, sdrop),
                "Source Clip offset": frames_to_tc(int(src_offset or 0), sfps, sdrop),
                "StartTime": frames_to_tc(start_time_frames, sfps, sdrop),
                "End Time": frames_to_tc(end_time_frames, sfps, sdrop),
                "Event Length": int(length),
                "Source Clip start (frames)": int(genuine_start_frames),
                "Source Clip offset (frames)": int(src_offset or 0),
                "StartTime (frames)": int(start_time_frames),
                "Effect Name": eff_name,
                "Orig Source Clip length": int(orig_len),
            })

    # Write CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})

# -----------------------
# Entry point
# -----------------------
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {os.path.basename(sys.argv[0])} input.aaf output.csv")
        sys.exit(1)
    extract_aaf(sys.argv[1], sys.argv[2])
