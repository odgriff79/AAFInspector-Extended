#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AAF DiskLabel + Source Path extractor (save point)

What it does (current working state):
- Open an AAF via pyaaf2
- Locate a top-level CompositionMob (timeline), optionally by name
- Walk the picture Sequence, collect SourceClips with timeline offsets
- For each event:
    * DiskLabel := read ONLY from the Source Clip's "Source Mob Ref"
      by locating a node where Name == "_IMPORTDISKLAB" and reading sibling "Value"
      (deep BFS over the pyaaf2 mapping/sequence-like structures)
    * Resolve to the end import mob (UMID chain) and read Locator URL → Source File Path/Name
- Write a concise CSV

Usage:
  python aaf_disklabel_from_source_mob_ref.py /path/to/file.aaf \
      --timeline "AAF known 202508 08.Exported.01" \
      --out /path/to/output.csv
"""

import os
import sys
import csv
import argparse
import urllib.parse
from collections import deque

try:
    import aaf2
    from aaf2.components import Sequence, SourceClip, OperationGroup, Timecode
except Exception as e:
    print("Error: pyaaf2 is required in this environment.", file=sys.stderr)
    raise

# ----------------------------- Utils -----------------------------

def frames_to_tc(fc: int, rate: float, drop: bool = False) -> str:
    """Convert frames to timecode string (basic, non-drop vs drop separator only)."""
    try:
        fps = round(float(rate))
        fc = int(fc)
    except Exception:
        return "N/A"
    if fps <= 0:
        return "N/A"
    h = fc // (3600 * fps)
    m = (fc % (3600 * fps)) // (60 * fps)
    s = (fc % (60 * fps)) // fps
    f = fc % fps
    return f"{h:02}:{m:02}:{s:02}{';' if drop else ':'}{f:02}"

def _dec(v):
    """Unwrap pyaaf2 property wrappers and decode bytes / int-arrays → str when needed."""
    vv = getattr(v, "value", v)
    if isinstance(vv, (bytes, bytearray)):
        for enc in ("utf-16-le", "utf-8"):
            try:
                return vv.decode(enc).rstrip("\x00")
            except Exception:
                pass
        return repr(vv)
    if isinstance(vv, (list, tuple)) and vv and all(isinstance(x, int) for x in vv):
        b = bytes(vv)
        for enc in ("utf-16-le", "utf-8"):
            try:
                return b.decode(enc).rstrip("\x00")
            except Exception:
                pass
        return repr(vv)
    return vv

def locator_to_url(loc) -> str:
    """Best-effort read of a Locator's URL."""
    u = getattr(loc, "url", None) or getattr(loc, "path", None)
    if u:
        return str(u)
    try:
        prop = loc.get("URLString", None)
        if prop is not None:
            return str(_dec(prop))
    except Exception:
        pass
    return ""

# ---------------------- Timeline + Traversal ----------------------

def choose_timeline(f, preferred_name: str | None):
    """Pick a CompositionMob timeline; prefer by name if provided."""
    mobs = list(f.content.mobs)
    comp_by_name = None
    if preferred_name:
        for m in mobs:
            try:
                if getattr(m, "name", None) == preferred_name:
                    comp_by_name = m
                    break
            except Exception:
                pass
        if comp_by_name:
            return comp_by_name

    # Otherwise pick the first CompositionMob that has a Sequence in a slot
    for m in mobs:
        try:
            if getattr(getattr(m, "classdef", None), "name", "") == "CompositionMob":
                for s in (m.slots or []):
                    if isinstance(getattr(s, "segment", None), Sequence):
                        return m
        except Exception:
            pass

    # Fallback: any mob with a Sequence in slots
    for m in mobs:
        try:
            if any(isinstance(getattr(s, "segment", None), Sequence) for s in (m.slots or [])):
                return m
        except Exception:
            pass

    return None

def walk_sequence_for_sourceclips(root_seg, start_ofs_frames: int):
    """Yield (SourceClip, timeline_ofs_frames, clip_length). Deep walk over Sequences/OperationGroups."""
    items = []

    def walk(node, ofs):
        L = int(getattr(node, "length", 0) or 0)
        if isinstance(node, SourceClip):
            items.append((node, ofs, L))
            return L
        if isinstance(node, Sequence):
            total = 0
            for c in node.components:
                l = walk(c, ofs + total)
                total += l if l else int(getattr(c, "length", 0) or 0)
            return total
        if isinstance(node, OperationGroup):
            # Dive its inputs as well (effects can wrap real clips)
            for attr in ("segments", "input_segments"):
                it = getattr(node, attr, None)
                if it:
                    for seg in it:
                        walk(seg, ofs)
            return L
        # Generic descent into other segment containers
        for attr in ("components", "segments", "input_segments"):
            it = getattr(node, attr, None)
            if it:
                for seg in it:
                    walk(seg, ofs)
        return L

    walk(root_seg, start_ofs_frames)
    return items

# ----------------- DiskLabel from Source Mob Ref (BFS) -----------------

def bfs_get_importdisklab_value(root) -> str:
    """
    Walk arbitrary nested mapping/sequence-like pyaaf2 structures under 'root'.
    Return the first sibling Value where Name == '_IMPORTDISKLAB'.
    This is explicitly anchored to the Source Clip's 'Source Mob Ref'.
    """
    if root is None:
        return ""
    visited = set()
    dq = deque([root])
    while dq:
        node = dq.popleft()
        nid = id(node)
        if nid in visited:
            continue
        visited.add(nid)

        # dict-like keys
        try:
            keys = list(node.keys())
        except Exception:
            keys = []
        if "Name" in keys:
            try:
                name_val = _dec(node["Name"])
            except Exception:
                name_val = None
            if isinstance(name_val, (bytes, bytearray)):
                try:
                    name_val = name_val.decode("utf-8", "ignore")
                except Exception:
                    pass
            if str(name_val).strip() == "_IMPORTDISKLAB":
                try:
                    val = _dec(node["Value"]) if "Value" in keys else ""
                except Exception:
                    val = ""
                if isinstance(val, (bytes, bytearray)):
                    try:
                        val = val.decode("utf-8", "ignore")
                    except Exception:
                        pass
                return str(val).strip()

        # enqueue dict values
        for k in keys:
            try:
                child = node[k]
            except Exception:
                child = None
            if child is not None:
                dq.append(child)

        # enqueue sequence-like items
        try:
            for item in node:
                dq.append(item)
        except Exception:
            pass

    return ""

# ------------------ Resolve to end import mob (URL) ------------------

def resolve_end_import_mob(sc, mob_map):
    """
    Follow UMID/slot chain from the SourceClip's mob until a descriptor with a Locator exists.
    Return that mob (the 'true import' mob).
    """
    chain = [getattr(sc, "mob", None) or mob_map.get(getattr(sc, "mob_id", None))]
    mob = chain[0]
    visited = set(chain)

    while mob:
        desc = getattr(mob, "descriptor", None)
        has_locs = False
        if desc is not None:
            try:
                for _ in desc.locator:
                    has_locs = True
                    break
            except Exception:
                pass
        if has_locs:
            break

        nxt = None
        for s in getattr(mob, "slots", []):
            seg = s.segment
            if isinstance(seg, SourceClip):
                nxt = getattr(seg, "mob", None) or mob_map.get(getattr(seg, "mob_id", None))
                break
            if isinstance(seg, Sequence):
                for c in seg.components:
                    if isinstance(c, SourceClip):
                        nxt = getattr(c, "mob", None) or mob_map.get(getattr(c, "mob_id", None))
                        break
                if nxt:
                    break

        if not nxt or nxt in visited:
            break
        chain.append(nxt)
        visited.add(nxt)
        mob = nxt

    return mob

# ------------------------------- Main -------------------------------

def main():
    ap = argparse.ArgumentParser(description="Extract DiskLabel from Source Mob Ref and Source Paths from end import mob.")
    ap.add_argument("aaf", help="Path to the AAF file")
    ap.add_argument("--timeline", help="Exact timeline (CompositionMob) name to use", default=None)
    ap.add_argument("--out", help="CSV output path", default=None)
    args = ap.parse_args()

    aaf_path = args.aaf
    out_csv = args.out or (os.path.splitext(aaf_path)[0] + "_disklabel_sourcepaths.csv")

    with aaf2.open(aaf_path, "r") as f:
        mob_map = {m.mob_id: m for m in f.content.mobs}

        comp = choose_timeline(f, args.timeline)
        if not comp:
            raise RuntimeError("Could not find a suitable CompositionMob (timeline).")

        # timeline metadata
        start_tc, drop, edit_rate, seq = 0, False, 25.0, None
        for s in comp.slots:
            if isinstance(s.segment, Timecode):
                start_tc = int(getattr(s.segment, "start", 0) or 0)
                drop = bool(getattr(s.segment, "drop", False))
            if isinstance(s.segment, Sequence) and seq is None:
                seq = s.segment
                er = getattr(s, "edit_rate", None)
                try:
                    edit_rate = float(er) if er else edit_rate
                except Exception:
                    pass

        if seq is None:
            raise RuntimeError("No picture Sequence found on the chosen timeline.")

        # collect events
        events = walk_sequence_for_sourceclips(seq, start_tc)

        # assemble rows
        rows = []
        for idx, (sc, ofs, ln) in enumerate(events, start=1):
            # 1) DiskLabel from Source Mob Ref
            smob = getattr(sc, "mob", None)
            disklabel = bfs_get_importdisklab_value(smob) if smob else ""

            # 2) Source path from end import mob (locator URL)
            end_mob = resolve_end_import_mob(sc, mob_map)
            url = ""
            if getattr(end_mob, "descriptor", None) is not None:
                try:
                    for loc in end_mob.descriptor.locator:
                        url = locator_to_url(loc)
                        if url:
                            break
                except Exception:
                    pass

            dec_path = urllib.parse.unquote(urllib.parse.urlparse(url).path) if url else ""
            src_fname = os.path.basename(dec_path) if dec_path else "N/A"
            src_path = os.path.dirname(dec_path).replace("\\", "/") if dec_path else "N/A"

            rows.append({
                "Event": idx,
                "Timeline Name": getattr(comp, "name", "(unnamed)"),
                "Timeline Start TC": frames_to_tc(ofs, edit_rate, drop),
                "Source Mob Ref": getattr(smob, "name", "(unnamed)") if smob else "(none)",
                "DiskLabel (from Source Mob Ref)": disklabel,
                "Source File Name": src_fname,
                "Source File Path": src_path,
            })

    # write CSV
    fieldnames = [
        "Event",
        "Timeline Name",
        "Timeline Start TC",
        "Source Mob Ref",
        "DiskLabel (from Source Mob Ref)",
        "Source File Name",
        "Source File Path",
    ]
    with open(out_csv, "w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"✅ Wrote {len(rows)} rows → {out_csv}")


if __name__ == "__main__":
    main()
