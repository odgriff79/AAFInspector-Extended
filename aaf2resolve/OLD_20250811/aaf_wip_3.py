#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super EDL + FX Extractor (AAF → CSV)
State: 2025-08-10 (matches reference CSV core fields for events 1–5)

What this does now (verified):
- Finds the top CompositionMob (by name or picture sequence).
- Walks the picture Sequence to collect SourceClip events with timeline offsets.
- Resolves the genuine source via UMID + SourceMobSlotID chain until an
  ImportDescriptor with Locator(URLString) is found.
- Pulls DiskLabel via plain-text crawl for `_IMPORTDISKLAB` near the anchor import mob
  (fallback: immediate upstream mob). TapeID via similar crawl for “TapeID”.
- Reads the source clip’s *genuine* start timecode frames from the anchor SourceMob’s
  nested Timecode component (Sequence → Components → Timecode).
- Reads the event’s SourceClip offset (frames) directly from the *timeline* SourceClip
  (Start / StartTime).
- Computes StartTime/EndTime in source rate; formats TCs.
- Reads orig source length from the first upstream descriptor exposing `Length`.

Next to add:
- Full effect discovery (plain-text AVX crawl) and keyframe harvesting.
"""

import argparse
import csv
import os
import sys
import urllib.parse
from collections import deque
from datetime import datetime

import aaf2
from aaf2.components import Sequence, SourceClip, OperationGroup, Timecode


# ---------- Utilities ----------

def frames_to_tc(fc, fps, drop=False):
    """Format frames to HH:MM:SS:FF (or ; for drop)."""
    if fc is None:
        return "N/A"
    try:
        fc = int(fc)
    except Exception:
        return "N/A"
    fps = int(round(float(fps or 25.0)))
    if fps <= 0:
        return "N/A"
    h = fc // (3600 * fps)
    m = (fc % (3600 * fps)) // (60 * fps)
    s = (fc % (60 * fps)) // fps
    f = fc % fps
    return f"{h:02}:{m:02}:{s:02}{';' if drop else ':'}{f:02}"


def unwrap(x):
    """Unwrap aaf2 Property/Value wrappers to raw python types."""
    try:
        if hasattr(x, "value"):
            return unwrap(x.value)
        return x
    except Exception:
        return x


# ---------- Timeline & traversal ----------

def choose_timeline(f, preferred_name=None):
    """Pick the CompositionMob by explicit name or the first with a picture Sequence."""
    if preferred_name:
        for m in f.content.mobs:
            if getattr(m, "name", "") == preferred_name:
                return m
    for m in f.content.mobs:
        if getattr(getattr(m, "classdef", None), "name", "") == "CompositionMob":
            for s in (m.slots or []):
                if isinstance(getattr(s, "segment", None), Sequence):
                    return m
    return None


def find_picture_sequence_and_info(comp_mob):
    """Return (sequence, edit_rate, drop, timeline_start_frames)."""
    edit_rate = 25.0
    drop = False
    timeline_start = 0
    sequence = None

    for s in comp_mob.slots:
        seg = getattr(s, "segment", None)
        # Timeline start (timecode track)
        if isinstance(seg, Timecode):
            timeline_start = int(getattr(seg, "start", 0) or 0)
            drop = bool(getattr(seg, "drop", False))
        # First picture sequence as main
        if isinstance(seg, Sequence) and sequence is None:
            sequence = seg
            try:
                edit_rate = float(getattr(s, "edit_rate", 25.0) or 25.0)
            except Exception:
                pass

    return sequence, edit_rate, drop, timeline_start


def walk_seq_collect(root_seq, start_ofs_frames):
    """
    Deep-walk the sequence and collect SourceClip events with their
    absolute timeline start (in frames) and event length (frames).
    """
    items = []

    def walk(node, ofs):
        L = int(getattr(node, "length", 0) or 0)

        if isinstance(node, SourceClip):
            items.append({"kind": "clip", "node": node, "ofs": ofs, "len": L})
            return L

        if isinstance(node, Sequence):
            total = 0
            for c in node.components:
                used = walk(c, ofs + total)
                total += used if used else int(getattr(c, "length", 0) or 0)
            return total

        if isinstance(node, OperationGroup):
            # Explore inputs (we don't record FX events here yet)
            for attr in ("segments", "input_segments"):
                it = getattr(node, attr, None)
                if it:
                    for seg in it:
                        walk(seg, ofs)
            return L

        return L

    walk(root_seq, start_ofs_frames)
    return [e for e in items if e["kind"] == "clip"]


# ---------- UMID chain → genuine source (ImportDescriptor with Locator) ----------

def deep_first_sourceclip(seg):
    dq = deque([seg])
    seen = set()
    while dq:
        n = dq.popleft()
        if id(n) in seen:
            continue
        seen.add(id(n))
        if isinstance(n, SourceClip):
            return n
        for attr in ("components", "segments", "input_segments"):
            it = getattr(n, attr, None)
            if it:
                for s in it:
                    dq.append(s)
    return None


def resolve_chain_mobs(start_mob):
    """Follow SourceClip references (by slot→segment) building a Mob chain."""
    chain = []
    if not start_mob:
        return chain
    chain.append(start_mob)
    seen = {id(start_mob)}
    cur = start_mob

    while True:
        nxt = None
        for ss in (cur.slots or []):
            seg = ss.segment
            sc = deep_first_sourceclip(seg)
            if sc is not None:
                m = getattr(sc, "mob", None)
                if m and id(m) not in seen:
                    nxt = m
                    break
        if nxt is None:
            break
        chain.append(nxt)
        seen.add(id(nxt))
        cur = nxt

    return chain


def resolve_end_import_mob_from(start_mob):
    """
    From a timeline SourceClip’s mob, walk forward to the first mob whose descriptor
    has at least one Locator. That mob represents the actual imported media file.
    """
    for m in resolve_chain_mobs(start_mob):
        desc = getattr(m, "descriptor", None)
        if desc is not None:
            try:
                # Will raise if no "locator" vector exists
                for _ in desc.locator:
                    return m
            except Exception:
                pass
    return None


# ---------- Genuine source timecode ----------

def bfs_find_timecode_in_mob(mob):
    """
    Search inside each slot's segment tree for a Timecode component.
    Return {start, rate, drop}.
    """
    if mob is None:
        return {"start": None, "rate": None, "drop": None}

    for ss in (mob.slots or []):
        seg = getattr(ss, "segment", None)
        dq = deque([seg])
        seen = set()
        while dq:
            n = dq.popleft()
            if id(n) in seen:
                continue
            seen.add(id(n))
            if isinstance(n, Timecode):
                start = int(getattr(n, "start", 0) or 0)
                drop = bool(getattr(n, "drop", False))
                rate = float(getattr(ss, "edit_rate", 25.0) or 25.0)
                return {"start": start, "rate": rate, "drop": drop}
            for attr in ("components", "segments", "input_segments"):
                it = getattr(n, attr, None)
                if it:
                    for s in it:
                        dq.append(s)
    return {"start": None, "rate": None, "drop": None}


def sourceclip_offset_frames(sc):
    """
    The event’s source offset (frames) lives on the *timeline* SourceClip node.
    Read StartTime/Start; do not descend into referenced mobs.
    """
    for attr in ("start_time", "start"):
        try:
            v = getattr(sc, attr, None)
            if v is not None:
                return int(v)
        except Exception:
            pass
    for key in ("StartTime", "Start"):
        try:
            v = unwrap(sc[key])
            if isinstance(v, (int, float)):
                return int(v)
        except Exception:
            pass
    return 0


# ---------- Metadata crawling (DiskLabel, TapeID, path, orig length) ----------

def bfs_named_value(root, names):
    """
    Find the first dict-like node where node["Name"] is one of `names`,
    and return its sibling node["Value"] as string.
    """
    targets = {n.strip() for n in names}
    dq = deque([root])
    seen = set()

    while dq:
        node = dq.popleft()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))

        # dict-like access
        try:
            keys = list(node.keys())
        except Exception:
            keys = []

        if "Name" in keys and "Value" in keys:
            try:
                nm = str(unwrap(node["Name"])).strip()
                if nm in targets:
                    return str(unwrap(node["Value"])).strip()
            except Exception:
                pass

        # enqueue nested structures
        for k in keys:
            try:
                dq.append(node[k])
            except Exception:
                pass

        try:
            for it in node:
                dq.append(it)
        except Exception:
            pass

        try:
            for ref in node.walk_references():
                dq.append(ref)
        except Exception:
            pass

    return ""


def descriptor_length_if_any(mob):
    """Try descriptor['Length'] then nested children with 'Length'."""
    desc = getattr(mob, "descriptor", None)
    if desc is None:
        return 0

    try:
        if "Length" in list(desc.keys()):
            return int(unwrap(desc["Length"]))
    except Exception:
        pass

    try:
        for k in desc.keys():
            child = desc[k]
            try:
                if "Length" in list(child.keys()):
                    return int(unwrap(child["Length"]))
            except Exception:
                pass
    except Exception:
        pass

    return 0


def decode_url_from_import_mob(import_mob):
    """Return (src_fname, src_path) using ImportDescriptor → Locator → URLString."""
    src_fname, src_path = "N/A", "N/A"
    if import_mob is None:
        return src_fname, src_path
    try:
        for loc in import_mob.descriptor.locator:
            try:
                url = str(unwrap(loc["URLString"]))
                p = urllib.parse.unquote(urllib.parse.urlparse(url).path)
                src_fname = os.path.basename(p)
                src_path = os.path.dirname(p)
                break
            except Exception:
                continue
    except Exception:
        pass
    return src_fname, src_path


# ---------- Row assembly ----------

def event_to_row(idx, node, ofs, length, edit_rate, timeline_drop):
    """Build a single CSV row for one SourceClip event."""
    # Source UMID (timeline SourceClip)
    try:
        src_umid = str(unwrap(node["SourceID"]))
    except Exception:
        src_umid = "N/A"

    # Resolve mob chain & genuine import mob
    smob = getattr(node, "mob", None)
    chain = resolve_chain_mobs(smob) if smob else []
    import_mob = resolve_end_import_mob_from(smob) if smob else None

    # File path (decoded)
    src_fname, src_path = decode_url_from_import_mob(import_mob)

    # DiskLabel & TapeID (prefer near import mob)
    disk_label = bfs_named_value(import_mob, {"_IMPORTDISKLAB"}) if import_mob else ""
    tape_id = bfs_named_value(import_mob, {"TapeID"}) if import_mob else ""
    if not disk_label and smob:
        disk_label = bfs_named_value(smob, {"_IMPORTDISKLAB"}) or ""
    if not tape_id and smob:
        tape_id = bfs_named_value(smob, {"TapeID"}) or ""

    # Genuine source start TC from nested Timecode in import mob
    tc = bfs_find_timecode_in_mob(import_mob)
    genuine_start = tc["start"]
    source_rate = tc["rate"] or edit_rate
    source_drop = tc["drop"] if tc["drop"] is not None else False

    # Offset from timeline SourceClip
    offset_frames = sourceclip_offset_frames(node)

    # Start/End in source domain
    start_frames = (genuine_start or 0) + offset_frames
    end_frames = start_frames + length

    # Original source length from first upstream descriptor exposing it
    orig_len = 0
    for m in chain:
        v = descriptor_length_if_any(m)
        if v:
            orig_len = v
            break

    # Effects (placeholder; to be implemented next)
    eff_name = "N/A"
    eff_detail = "No effect data found."

    return {
        "Event": idx,
        "Event Name": src_fname,
        "Clip Name": src_fname,
        "Source File Name": src_fname,
        "Source File Path": src_path,
        "DiskLabel": disk_label or "N/A",
        "TapeID": tape_id or "N/A",
        "SourceMobID": src_umid,
        "TrackID": "N/A",
        "Source Clip EditRate": source_rate,
        "Timeline Start TC": frames_to_tc(ofs, edit_rate, timeline_drop),
        "Source Clip start time code": frames_to_tc(
            genuine_start, source_rate, source_drop
        ) if genuine_start is not None else "N/A",
        "Source Clip offset": frames_to_tc(offset_frames, source_rate, source_drop),
        "StartTime": frames_to_tc(start_frames, source_rate, source_drop),
        "End Time": frames_to_tc(end_frames, source_rate, source_drop),
        "Event Length": length,
        "Source Clip start (frames)": genuine_start or 0,
        "Source Clip offset (frames)": offset_frames,
        "StartTime (frames)": start_frames,
        "Effect Name": eff_name,
        "Keyframe Details": eff_detail,
        "Orig Source Clip length": orig_len,
    }


# ---------- Report writer ----------

def write_report(aaf_path, timeline_name=None, out_csv=None):
    with aaf2.open(aaf_path, "r") as f:
        comp = choose_timeline(f, timeline_name)
        if not comp:
            raise RuntimeError("Could not find a suitable CompositionMob.")

        sequence, edit_rate, drop, timeline_start = find_picture_sequence_and_info(comp)
        if not sequence:
            raise RuntimeError("No picture Sequence found on the timeline.")

        events = walk_seq_collect(sequence, timeline_start)
        clips = [{"node": e["node"], "ofs": e["ofs"], "len": e["len"]} for e in events]

        rows = []
        total_len = 0
        for idx, e in enumerate(clips, start=1):
            total_len += e["len"]
            row = event_to_row(idx, e["node"], e["ofs"], e["len"], edit_rate, drop)
            rows.append(row)

        seq_name = getattr(comp, "name", "Timeline")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not out_csv:
            sanitized = seq_name.replace("/", "_")
            out_csv = f"{sanitized}_super_edl_fx_report_v2_{ts}.csv"

        # Summary then data
        summary = [
            ("Timeline Summary", ""),
            ("Timeline Name", seq_name),
            ("Timeline Edit Rate", f"{edit_rate} ({'DF' if drop else 'NDF'})"),
            ("Timeline Start", frames_to_tc(timeline_start, edit_rate, drop)),
            ("Timeline Length", f"{frames_to_tc(total_len, edit_rate, drop)} ({total_len} frames)"),
            ("Total number of EDL events found", len(clips)),
            ("Total number of unique sources", len({r["SourceMobID"] for r in rows})),
        ]

        headers = [
            "Event", "Event Name", "Clip Name", "Source File Name", "Source File Path",
            "DiskLabel", "TapeID", "SourceMobID", "TrackID", "Source Clip EditRate",
            "Timeline Start TC", "Source Clip start time code", "Source Clip offset",
            "StartTime", "End Time", "Event Length", "Source Clip start (frames)",
            "Source Clip offset (frames)", "StartTime (frames)", "Effect Name",
            "Keyframe Details", "Orig Source Clip length",
        ]

        with open(out_csv, "w", newline="", encoding="utf-8") as fcsv:
            w = csv.writer(fcsv)
            for k, v in summary:
                w.writerow([k, v])
            w.writerow([])  # spacer
            w.writerow(headers)
            for r in rows:
                w.writerow([r[h] for h in headers])

        return out_csv


# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(description="Super EDL + FX Extractor (AAF → CSV)")
    p.add_argument("aaf", help="Path to AAF file")
    p.add_argument("--timeline", help="CompositionMob name (optional)")
    p.add_argument("--out", help="Output CSV path (optional)")
    args = p.parse_args()

    out = write_report(args.aaf, args.timeline, args.out)
    print(out)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
