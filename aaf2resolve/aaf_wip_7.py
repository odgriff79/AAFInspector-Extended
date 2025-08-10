#!/usr/bin/env python3
# aaf_wip_7.py
# Robust AAF → Super EDL + FX extractor (composition-aware, end-mob resolution)
# Focus in this patch:
#  - UMID + SourceMobSlotID chain to the ImportDescriptor/Locator end mob
#  - Orig Source Clip length = length of the SourceClip on the final hop
#  - DiskLabel/TapeID anchored on end mob
#  - Timeline length via picture sequence total length
#  - Composition selection (--comp / --list-comps) with explicit fallback

import argparse
import csv
import os
import sys
import urllib.parse
from datetime import datetime

try:
    import aaf2
except Exception as e:
    print("ERROR: pyaaf2 is not installed in this environment.", file=sys.stderr)
    raise

# ---------------------------
# Helpers
# ---------------------------

def frames_to_tc(frame_count, fps=25.0, is_drop=False):
    try:
        fc = int(frame_count)
        fps_i = int(round(float(fps)))
        sep = ";" if is_drop else ":"
        h = fc // (3600 * fps_i)
        m = (fc % (3600 * fps_i)) // (60 * fps_i)
        s = (fc % (60 * fps_i)) // fps_i
        f = fc % fps_i
        return f"{h:02}:{m:02}:{s:02}{sep}{f:02}"
    except Exception:
        return "N/A"

def prop_val(node, key, default=None):
    try:
        return node[key].value
    except Exception:
        return default

def has_key(node, key):
    try:
        _ = node[key]
        return True
    except Exception:
        return False

def is_sequence(segval):
    try:
        # Sequence has "Components"
        return has_key(segval, "Components")
    except Exception:
        return False

def is_timecode(segval):
    try:
        return has_key(segval, "Start") and (has_key(segval, "FPS") or has_key(segval, "EditRate"))
    except Exception:
        return False

def is_sourceclip(segval):
    try:
        return has_key(segval, "SourceID")
    except Exception:
        return False

def is_operationgroup(segval):
    try:
        return has_key(segval, "InputSegments") or has_key(segval, "Operation")
    except Exception:
        return False

def is_picture_datadef(node):
    try:
        dd = prop_val(node, "DataDefinition", None)
        if dd is None:
            return True  # assume picture if missing; we'll filter by slot later
        # aaf2 prints repr like "<aaf2.dictionary.DataDef Picture ...>"
        dd_str = str(dd)
        return "DataDef Picture" in dd_str
    except Exception:
        return True

def list_compositions(f):
    out = []
    for mob in f.content.mobs:
        name = prop_val(mob, "Name", "")
        if not name:
            continue
        # check if mob has a picture sequence slot
        try:
            for s in mob["Slots"]:
                segv = s["Segment"].value
                if is_sequence(segv) and is_picture_datadef(segv):
                    out.append(name)
                    break
        except Exception:
            continue
    return sorted(set(out))

def find_comp(f, requested_name=None):
    comps = list_compositions(f)
    if requested_name and requested_name in comps:
        return next(m for m in f.content.mobs if prop_val(m, "Name", "") == requested_name), requested_name, None
    # fallback heuristic: pick *.Exported.01 if present
    exported = [c for c in comps if c.endswith(".Exported.01")]
    if exported:
        chosen = exported[0]
        mob = next(m for m in f.content.mobs if prop_val(m, "Name", "") == chosen)
        warn = None if (requested_name in (None, chosen)) else f'WARNING: requested comp "{requested_name}" not found. Using "{chosen}".'
        return mob, chosen, warn
    # final fallback: first composition
    if comps:
        chosen = comps[0]
        mob = next(m for m in f.content.mobs if prop_val(m, "Name", "") == chosen)
        warn = None if (requested_name in (None, chosen)) else f'WARNING: requested comp "{requested_name}" not found. Using "{chosen}".'
        return mob, chosen, warn
    return None, None, "ERROR: no composition with a picture sequence found."

def get_timecode_from_mob(mob):
    """
    Return (start_frames, fps, drop) for the first timecode segment
    found in the mob's slots (direct Timecode, or Sequence containing Timecode).
    """
    try:
        for s in mob["Slots"]:
            segv = s["Segment"].value
            # direct timecode
            if is_timecode(segv):
                st = int(prop_val(segv, "Start", 0) or 0)
                fps = float(prop_val(segv, "FPS", prop_val(segv, "EditRate", 25.0)) or 25.0)
                drop = bool(prop_val(segv, "Drop", False))
                return st, fps, drop
            # timecode nested in a sequence
            if is_sequence(segv):
                for c in segv["Components"]:
                    if is_timecode(c):
                        st = int(prop_val(c, "Start", 0) or 0)
                        fps = float(prop_val(c, "FPS", prop_val(c, "EditRate", 25.0)) or 25.0)
                        drop = bool(prop_val(c, "Drop", False))
                        return st, fps, drop
    except Exception:
        pass
    return 0, 25.0, False

def sum_sequence_length(seq):
    try:
        total = 0
        for c in seq["Components"]:
            total += int(prop_val(c, "Length", 0) or 0)
        return total
    except Exception:
        return 0

def deep_tag_scan_for_labels(node):
    """
    Return (disklabel, tapeid) by scanning:
      - MobAttributeList (Name/Value)
      - TaggedValueAttributeList within those
      - Accepts keys '_IMPORTDISKLAB' or 'DiskLabel' (case-insensitive)
    """
    disklabel = ""
    tapeid = ""
    try:
        for tv in node["MobAttributeList"]:
            name = str(prop_val(tv, "Name", "") or "").strip()
            val = prop_val(tv, "Value", "")
            nlow = name.lower()
            if not disklabel and nlow in ("disklabel", "_importdisklab"):
                disklabel = str(val)
            if not tapeid and nlow == "tapeid":
                tapeid = str(val)
            # nested tagged value list
            try:
                for tv2 in tv["TaggedValueAttributeList"]:
                    name2 = str(prop_val(tv2, "Name", "") or "").strip()
                    val2 = prop_val(tv2, "Value", "")
                    n2low = name2.lower()
                    if not disklabel and n2low in ("disklabel", "_importdisklab"):
                        disklabel = str(val2)
                    if not tapeid and n2low == "tapeid":
                        tapeid = str(val2)
            except Exception:
                pass
    except Exception:
        pass
    return disklabel, tapeid

def get_import_url(mob):
    """
    Find URLString under EssenceDescription → ImportDescriptor → Locator.
    """
    try:
        ed = mob["EssenceDescription"].value
        if ed is None:
            return None
        # Most Avid imports store URLString under one or more Locator entries
        try:
            for loc in ed["Locator"]:
                url = prop_val(loc, "URLString", None)
                if url:
                    return str(url)
        except Exception:
            pass
        # Some descriptors may have URLString directly (rare)
        url = prop_val(ed, "URLString", None)
        if url:
            return str(url)
    except Exception:
        pass
    return None

def build_mob_map(f):
    mm = {}
    for mob in f.content.mobs:
        umid = str(prop_val(mob, "MobID", "") or "")
        if umid:
            mm[umid] = mob
    return mm

def find_slot(mob, slot_id):
    try:
        for s in mob["Slots"]:
            sid = int(prop_val(s, "SlotID", 0) or 0)
            if sid == int(slot_id):
                return s
    except Exception:
        pass
    return None

def first_sourceclip_in_segment(segval):
    """
    Given a slot's Segment value (may be Sequence|SourceClip|OperationGroup),
    return (sourceclip_obj or None).
    """
    try:
        if is_sourceclip(segval):
            return segval
        if is_sequence(segval):
            for c in segval["Components"]:
                if is_sourceclip(c):
                    return c
        if is_operationgroup(segval):
            # walk all inputs; pick the first SC encountered
            try:
                for ins in segval["InputSegments"]:
                    sc = first_sourceclip_in_segment(ins)
                    if sc:
                        return sc
            except Exception:
                pass
    except Exception:
        pass
    return None

def resolve_chain_track_aware(mob_map, start_umid, start_source_slot_id):
    """
    Track-aware chain:
      - At each hop, find the targeted mob by UMID
      - Use the provided SourceMobSlotID to pick the correct slot in that mob
      - Inside that slot's segment, find the first SourceClip
      - Capture the SC's Length (edge length) and next (UMID, SourceMobSlotID)
      - Stop when the targeted mob has an ImportDescriptor/Locator (end mob)
    Returns a dict with:
      url, disklabel, tapeid,
      source_tc_start, source_tc_fps, source_tc_drop,
      orig_source_length (edge length INTO the end mob),
      hops
    """
    out = {
        "url": None,
        "disklabel": "",
        "tapeid": "",
        "source_tc_start": 0,
        "source_tc_fps": 25.0,
        "source_tc_drop": False,
        "orig_source_length": 0,
        "hops": 0,
    }

    visited = set()
    current_umid = str(start_umid or "")
    current_slot_id = int(start_source_slot_id or 0)
    last_edge_len = 0

    while current_umid and current_umid not in visited:
        visited.add(current_umid)
        mob = mob_map.get(current_umid)
        if mob is None:
            break

        # Update source TC if present (we keep the deepest non-zero we see)
        st, fps, dr = get_timecode_from_mob(mob)
        if st:
            out["source_tc_start"] = st
            out["source_tc_fps"] = fps
            out["source_tc_drop"] = dr

        # If this mob has a URL, it's the end mob -> store URL and stop
        url = get_import_url(mob)
        if url:
            out["url"] = url
            # Use the last captured edge length as the original source length
            out["orig_source_length"] = int(last_edge_len or 0)
            # Anchor labels on the end mob
            dl, tp = deep_tag_scan_for_labels(mob)
            if dl: out["disklabel"] = dl
            if tp: out["tapeid"] = tp
            break

        # Otherwise, hop: pick the slot indicated by current_slot_id (track-aware)
        next_umid = None
        next_slotid = None
        edge_len = 0

        slot = find_slot(mob, current_slot_id)
        if slot is not None:
            segv = slot["Segment"].value
            sc = first_sourceclip_in_segment(segv)
            if sc is not None:
                next_umid = str(prop_val(sc, "SourceID", "") or "")
                next_slotid = int(prop_val(sc, "SourceMobSlotID", 0) or 0)
                edge_len = int(prop_val(sc, "Length", 0) or 0)

        # If we couldn't use the track-aware route (missing slot), do a best-effort fallback
        if not next_umid:
            try:
                # Try first SC we can find from any picture slot
                for s in mob["Slots"]:
                    segv = s["Segment"].value
                    sc = first_sourceclip_in_segment(segv)
                    if sc is not None:
                        next_umid = str(prop_val(sc, "SourceID", "") or "")
                        next_slotid = int(prop_val(sc, "SourceMobSlotID", 0) or 0)
                        edge_len = int(prop_val(sc, "Length", 0) or 0)
                        break
            except Exception:
                pass

        if not next_umid:
            # No further hop possible
            break

        # We are about to hop to next_umid; record the edge length we are crossing now.
        last_edge_len = edge_len
        out["hops"] += 1

        # Also try to capture DiskLabel/TapeID while traversing (only if still empty)
        if not out["disklabel"] or not out["tapeid"]:
            dl, tp = deep_tag_scan_for_labels(mob)
            if (not out["disklabel"]) and dl:
                out["disklabel"] = dl
            if (not out["tapeid"]) and tp:
                out["tapeid"] = tp

        # move to next hop
        current_umid = next_umid
        current_slot_id = next_slotid

    # If we never hit a URL, try labels on the last mob anyway (best-effort)
    if not out["url"]:
        mob = mob_map.get(current_umid)
        if mob is not None:
            dl, tp = deep_tag_scan_for_labels(mob)
            if dl and not out["disklabel"]:
                out["disklabel"] = dl
            if tp and not out["tapeid"]:
                out["tapeid"] = tp

    return out

# ---------------------------
# Traversal (collect events)
# ---------------------------

def collect_events_from_comp(comp_mob):
    """
    Walk the main picture Sequence of the composition and collect SourceClips
    with absolute timeline offsets. We descend into OperationGroups (all inputs).
    """
    events = []

    # find the picture slot's top sequence
    main_seq = None
    tc_start, tc_fps, tc_drop = get_timecode_from_mob(comp_mob)

    try:
        for s in comp_mob["Slots"]:
            segv = s["Segment"].value
            if is_sequence(segv) and is_picture_datadef(segv):
                main_seq = segv
                break
    except Exception:
        pass

    if main_seq is None:
        return [], tc_start, tc_fps, tc_drop, 0

    def walk(segv, t_off):
        if is_sequence(segv):
            off = t_off
            for comp in segv["Components"]:
                walk(comp, off)
                off += int(prop_val(comp, "Length", 0) or 0)
            return
        if is_operationgroup(segv):
            # Recurse all inputs but don't advance offset; OG doesn't consume time itself here
            try:
                for ins in segv["InputSegments"]:
                    walk(ins, t_off)
            except Exception:
                pass
            return
        if is_sourceclip(segv) and is_picture_datadef(segv):
            ev = {
                "timeline_start": int(tc_start or 0) + int(t_off or 0),
                "timeline_fps": float(tc_fps or 25.0),
                "timeline_drop": bool(tc_drop or False),
                "length": int(prop_val(segv, "Length", 0) or 0),
                "source_offset": int(prop_val(segv, "StartTime", 0) or 0),
                "umid": str(prop_val(segv, "SourceID", "") or ""),
                "slotid": int(prop_val(segv, "SourceMobSlotID", 0) or 0),
            }
            events.append(ev)
            return

    walk(main_seq, 0)

    # compute timeline length from sequence total
    seq_total = sum_sequence_length(main_seq)

    # dedupe: (umid, timeline_start, source_offset, length)
    seen = set()
    unique = []
    for e in events:
        key = (e["umid"], e["timeline_start"], e["source_offset"], e["length"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)

    return unique, tc_start, tc_fps, tc_drop, seq_total

# ---------------------------
# CSV writer
# ---------------------------

def write_csv(output_path, timeline_name, t_fps, t_drop, t_start_frames, t_length_frames, rows):
    """
    rows: list of dicts with all columns already computed
    """
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timeline Summary"])
        w.writerow(["Timeline Name", timeline_name])
        w.writerow(["Timeline Edit Rate", f"{t_fps} ({'DF' if t_drop else 'NDF'})"])
        w.writerow(["Timeline Start", frames_to_tc(t_start_frames, t_fps, t_drop)])
        w.writerow(["Timeline Length", f"{frames_to_tc(t_length_frames, t_fps, t_drop)} ({t_length_frames} frames)"])
        w.writerow(["Total number of EDL events found", len(rows)])
        # simple "unique sources" by (Source File Path, Source File Name)
        uniq = set((r.get("Source File Path",""), r.get("Source File Name","")) for r in rows)
        w.writerow(["Total number of unique sources", len(uniq)])
        w.writerow([])
        header = [
            "Event","Event Name","Clip Name","Source File Name","Source File Path",
            "DiskLabel","TapeID","SourceMobID","TrackID",
            "Source Clip EditRate","Timeline Start TC","Source Clip start time code","Source Clip offset",
            "StartTime","End Time","Event Length",
            "Source Clip start (frames)","Source Clip offset (frames)","StartTime (frames)",
            "Effect Name","Keyframe Details","Orig Source Clip length"
        ]
        w.writerow(header)
        for r in rows:
            w.writerow([
                r.get("Event",""),
                r.get("Event Name",""),
                r.get("Clip Name",""),
                r.get("Source File Name",""),
                r.get("Source File Path",""),
                r.get("DiskLabel",""),
                r.get("TapeID",""),
                r.get("SourceMobID",""),
                r.get("TrackID",""),
                r.get("Source Clip EditRate",""),
                r.get("Timeline Start TC",""),
                r.get("Source Clip start time code",""),
                r.get("Source Clip offset",""),
                r.get("StartTime",""),
                r.get("End Time",""),
                r.get("Event Length",""),
                r.get("Source Clip start (frames)",""),
                r.get("Source Clip offset (frames)",""),
                r.get("StartTime (frames)",""),
                r.get("Effect Name","N/A"),
                r.get("Keyframe Details","No effect data found."),
                r.get("Orig Source Clip length",""),
            ])

# ---------------------------
# Main
# ---------------------------

def main():
    ap = argparse.ArgumentParser(description="AAF → Super EDL + FX (end-mob aware)")
    ap.add_argument("aaf", help="Path to AAF file")
    ap.add_argument("--comp", help="Composition name to use", default=None)
    ap.add_argument("--list-comps", action="store_true", help="List available composition names and exit")
    args = ap.parse_args()

    aaf_path = args.aaf

    if not os.path.exists(aaf_path):
        print(f"ERROR: AAF not found: {aaf_path}", file=sys.stderr)
        sys.exit(2)

    with aaf2.open(aaf_path) as f:
        if args.list_comps:
            comps = list_compositions(f)
            if not comps:
                print("No compositions with a picture sequence found.")
            else:
                print("Compositions:")
                for c in comps:
                    print("  ", c)
            return

        comp_mob, comp_name, warn = find_comp(f, args.comp)
        if warn:
            print(warn)
        if comp_mob is None:
            print("ERROR: could not select a composition.", file=sys.stderr)
            sys.exit(3)

        events, t_start, t_fps, t_drop, t_total = collect_events_from_comp(comp_mob)
        mob_map = build_mob_map(f)

        rows = []
        for i, ev in enumerate(events, start=1):
            # Resolve end mob with track-aware chain
            res = resolve_chain_track_aware(mob_map, ev["umid"], ev["slotid"])

            # Filepath split with UNC preservation for file://server/path
            src_fname, src_path = "N/A", "N/A"
            if res["url"]:
                p = urllib.parse.urlparse(res["url"])
                path = urllib.parse.unquote(p.path or "")
                # UNC-friendly join of netloc and path
                if p.netloc and path.startswith("/"):
                    path = f"//{p.netloc}{path}"
                elif p.netloc and not path.startswith("/"):
                    path = f"//{p.netloc}/{path}"
                src_fname = os.path.basename(path) or "N/A"
                src_path = os.path.dirname(path) or "N/A"

            st = int(res["source_tc_start"] or 0)
            sfps = float(res["source_tc_fps"] or 25.0)
            sdrop = bool(res["source_tc_drop"] or False)

            start_frames = st + int(ev["source_offset"] or 0)
            end_frames = start_frames + int(ev["length"] or 0)

            rows.append({
                "Event": i,
                "Event Name": src_fname,
                "Clip Name": src_fname,
                "Source File Name": src_fname,
                "Source File Path": src_path,
                "DiskLabel": res["disklabel"],
                "TapeID": res["tapeid"],
                "SourceMobID": ev["umid"],
                "TrackID": ev["slotid"],
                "Source Clip EditRate": sfps,
                "Timeline Start TC": frames_to_tc(ev["timeline_start"], t_fps, t_drop),
                "Source Clip start time code": frames_to_tc(st, sfps, sdrop),
                "Source Clip offset": frames_to_tc(ev["source_offset"], sfps, sdrop),
                "StartTime": frames_to_tc(start_frames, sfps, sdrop),
                "End Time": frames_to_tc(end_frames, sfps, sdrop),
                "Event Length": ev["length"],
                "Source Clip start (frames)": st,
                "Source Clip offset (frames)": ev["source_offset"],
                "StartTime (frames)": start_frames,
                "Effect Name": "N/A",
                "Keyframe Details": "No effect data found.",
                "Orig Source Clip length": res["orig_source_length"],
            })

        # Output path
        base = os.path.splitext(os.path.basename(aaf_path))[0]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = f"{comp_name}_super_edl_fx_report_v2_{ts}.csv"
        out_path = os.path.join(os.getcwd(), out_name)

        write_csv(out_path, comp_name, t_fps, t_drop, t_start, t_total, rows)

        print(f"\nWrote: {out_name}\n")
        print("Summary:\n")
        print(f"   Timeline Name : {comp_name}")
        print(f"   Timeline Edit Rate : {t_fps} ({'DF' if t_drop else 'NDF'})")
        print(f"   Timeline Start : {frames_to_tc(t_start, t_fps, t_drop)}")
        print(f"   Timeline Length : {frames_to_tc(t_total, t_fps, t_drop)} ({t_total} frames)")
        print(f"   Total number of EDL events found : {len(rows)}")
        uniq = set((r.get('Source File Path',''), r.get('Source File Name','')) for r in rows)
        print(f"   Total number of unique sources : {len(uniq)}")

if __name__ == "__main__":
    main()
