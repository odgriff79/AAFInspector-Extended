"""
Super EDL + FX (direct AAF) — locked lookups

This script mirrors the CSV/JSON approach but interrogates a real AAF via pyaaf2 (aaf2).
Key behavior (based on our working logic):
  • Top-level CompositionMob detection and timeline summary (name, edit rate, DF/NDF, start).
  • Deep traversal of picture Sequence → OperationGroups → nested segments, recording timeline offset.
  • Source clip resolution via UMID + slot following until an ImportDescriptor with Locator URL is found.
  • DiskLabel/TapeID harvested from the Source Mob Ref by crawling tagged values (e.g., _IMPORTSETTING → _IMPORTDISKLAB → Value).
  • Source timecode start, edit rate and drop pulled from the Source Mob Ref Timecode slot (locked lookup).
  • Per-event offsets and Start/End TCs computed in the source clip’s own edit rate.
  • Effect names pulled from ComponentAttributeList (_EFFECT_PLUGIN_NAME/_EFFECT_PLUGIN_CLASS),
    falling back to operation definition token; static + animated parameters captured.
  • Original source clip length from descriptor.length (fallback to picture segment length if needed).
  • CSV written in the same shape as the JSON tool (summary block + event table).

Usage (inside this environment):
  python aaf_super_edl_fx_from_aaf.py \
      --aaf "/mnt/data/AAF known 202508 08.aaf" \
      --timeline "AAF known 202508 08.Exported.01" \
      --out "/mnt/data/AAF_known_20250808_progress_match.csv"
"""

from __future__ import annotations
import os, csv, re, urllib.parse, argparse
from collections import deque
from fractions import Fraction

try:
    import aaf2
    from aaf2.components import Sequence, SourceClip, OperationGroup, Timecode
except Exception as e:
    raise SystemExit("pyaaf2 (aaf2) must be installed in this environment: " + str(e))

# ---------------- Basic utilities ----------------

def frames_to_tc(frame_count: int, fps: float, is_drop: bool = False) -> str:
    try:
        fc = int(frame_count)
        int_fps = int(round(float(fps)))
        if int_fps <= 0:
            return "N/A"
        h = fc // (3600 * int_fps)
        m = (fc % (3600 * int_fps)) // (60 * int_fps)
        s = (fc % (60 * int_fps)) // int_fps
        f = fc % int_fps
        return f"{h:02}:{m:02}:{s:02}{';' if is_drop else ':'}{f:02}"
    except Exception:
        return "N/A"

def as_float_rate(er):
    try:
        if isinstance(er, Fraction):
            return float(er)
        return float(er) if er is not None else None
    except Exception:
        return None

def _dec(v):
    """Best-effort decode of aaf2 property-like values, including byte arrays."""
    vv = getattr(v, "value", v)
    try:
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
    except Exception:
        return vv

# ---------------- Composition & traversal ----------------

def choose_timeline(f, preferred_name: str | None = None):
    if preferred_name:
        for m in f.content.mobs:
            if getattr(m, "name", "") == preferred_name:
                return m
    # Prefer real CompositionMobs with a picture Sequence
    for m in f.content.mobs:
        try:
            if m.classdef.name == "CompositionMob":
                for s in (m.slots or []):
                    if isinstance(getattr(s, "segment", None), Sequence):
                        return m
        except Exception:
            pass
    # Fallback: any mob with a picture Sequence
    for m in f.content.mobs:
        try:
            if any(isinstance(getattr(s, "segment", None), Sequence) for s in (m.slots or [])):
                return m
        except Exception:
            pass
    return None


def walk_seq_collect(root_seg, start_ofs):
    """Walk the sequence and collect (kind, node, timeline_ofs, length) tuples.
    kind ∈ {"clip","fx"}.
    """
    items = []

    def walk(node, ofs):
        L = int(getattr(node, "length", 0) or 0)
        if isinstance(node, SourceClip):
            items.append(("clip", node, ofs, L))
            return L
        if isinstance(node, Sequence):
            total = 0
            for c in node.components:
                used = walk(c, ofs + total)
                total += used if used else int(getattr(c, "length", 0) or 0)
            return total
        if isinstance(node, OperationGroup):
            items.append(("fx", node, ofs, L))
            for attr in ("segments", "input_segments"):
                it = getattr(node, attr, None)
                if it:
                    for seg in it:
                        walk(seg, ofs)
            return L
        # Generic deepening for unforeseen containers
        for attr in ("components", "segments", "input_segments"):
            it = getattr(node, attr, None)
            if it:
                for seg in it:
                    walk(seg, ofs)
        return L

    walk(root_seg, start_ofs)
    return items

# ---------------- Source chain resolution ----------------

def resolve_end_import_mob(sc, mob_map):
    """Follow UMID/slot chain until we land on a mob with ImportDescriptor.locator (URL)."""
    mob = getattr(sc, "mob", None) or mob_map.get(getattr(sc, "mob_id", None))
    visited = set()
    while mob and id(mob) not in visited:
        visited.add(id(mob))
        desc = getattr(mob, "descriptor", None)
        if desc is not None:
            try:
                for _ in desc.locator:
                    return mob
            except Exception:
                pass
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
        mob = nxt
    return mob


def locator_to_url(loc) -> str:
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

# ---------------- Plain-text crawl for DiskLabel/TapeID (Source Mob Ref) ----------------

def bfs_find_named_value(root, names: set[str]) -> str:
    if not root:
        return ""
    targets = set(names)
    visited = set()
    dq = deque([root])
    while dq:
        node = dq.popleft()
        nid = id(node)
        if nid in visited:
            continue
        visited.add(nid)
        try:
            keys = list(node.keys())
        except Exception:
            keys = []
        if "Name" in keys:
            try:
                nm = _dec(node["Name"])  # aaf2 dict-like API
            except Exception:
                nm = None
            if isinstance(nm, (bytes, bytearray)):
                try:
                    nm = nm.decode("utf-8", "ignore")
                except Exception:
                    pass
            if str(nm).strip() in targets:
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
        for k in keys:
            try:
                dq.append(node[k])
            except Exception:
                pass
        # also iterate sequences/containers
        try:
            for item in node:
                dq.append(item)
        except Exception:
            pass
    return ""


def disklabel_from_smob(smob) -> str:
    # Specific JSON path analogue: _IMPORTSETTING → TaggedValueAttributeList → _IMPORTDISKLAB → Value
    return bfs_find_named_value(smob, {"_IMPORTDISKLAB"}) or ""


def tapeid_from_smob(smob) -> str:
    # Common textual variants
    return bfs_find_named_value(smob, {"TapeID", "Tape ID", "Reel", "ReelName"}) or ""

# ---------------- Effect harvesting ----------------

def effect_name_and_params(op: OperationGroup):
    plugin_name = ""
    plugin_class = ""
    params = []  # list of dicts: {name, (value | time,value)}

    dq = deque([op])
    visited = set()
    while dq:
        node = dq.popleft()
        if id(node) in visited:
            continue
        visited.add(id(node))
        # ComponentAttributeList for plugin IDs
        try:
            attrs = node.get("ComponentAttributeList", None)
        except Exception:
            attrs = None
        if attrs:
            for a in attrs:
                nm = str(_dec(getattr(a, "name", None) or getattr(a, "Name", None))).strip()
                val = _dec(getattr(a, "value", None) or getattr(a, "Value", None))
                if nm == "_EFFECT_PLUGIN_NAME":
                    plugin_name = str(val).strip() or plugin_name
                elif nm == "_EFFECT_PLUGIN_CLASS":
                    plugin_class = str(val).strip() or plugin_class
        # Parameters: static + animated
        try:
            plist = node.get("Parameters", None)
        except Exception:
            plist = None
        if plist:
            for p in plist:
                pname = str(_dec(p.get("Name", "") if hasattr(p, "get") else getattr(p, "Name", "")))
                ptlist = p.get("PointList", None) if hasattr(p, "get") else None
                if ptlist:
                    for cp in ptlist:
                        if getattr(getattr(cp, "classdef", None), "name", "") == "ControlPoint":
                            t = _dec(cp.get("Time", "") if hasattr(cp, "get") else "")
                            v = _dec(cp.get("Value", "") if hasattr(cp, "get") else "")
                            params.append({"name": pname, "time": str(t), "value": str(v)})
                else:
                    val = p.get("Value", None) if hasattr(p, "get") else None
                    if val is not None:
                        params.append({"name": pname, "value": str(_dec(val))})
        # enqueue deeper containers
        for key in ("segments", "input_segments", "components"):
            try:
                it = getattr(node, key, None)
            except Exception:
                it = None
            if it:
                for sub in it:
                    dq.append(sub)
        try:
            for item in node:
                dq.append(item)
        except Exception:
            pass

    if plugin_name or plugin_class:
        name = f"{plugin_class} : {plugin_name}".strip(" :")
    else:
        raw = str(getattr(op, "operation", None) or "")
        # Return operation token as-is (avoid hardcoding/tidying names)
        name = raw.split(" ")[-1] if " " in raw else raw
        name = name or "Unknown Effect"
    return name, params

# ---------------- Main extraction ----------------

def build_output(aaf_path: str, timeline_name: str, out_csv: str) -> str:
    with aaf2.open(aaf_path, "r") as f:
        mob_map = {m.mob_id: m for m in f.content.mobs}
        comp = choose_timeline(f, timeline_name)
        if not comp:
            raise RuntimeError("Timeline not found: " + timeline_name)
        start_tc = 0
        drop = False
        edit_rate = 25.0
        seq = None
        for s in comp.slots:
            if isinstance(s.segment, Timecode):
                start_tc = int(getattr(s.segment, "start", 0) or 0)
                drop = bool(getattr(s.segment, "drop", False))
            if isinstance(s.segment, Sequence) and seq is None:
                seq = s.segment
                er = as_float_rate(getattr(s, "edit_rate", None))
                if er:
                    edit_rate = er

        if seq is None:
            raise RuntimeError("No picture Sequence found on timeline")

        items = walk_seq_collect(seq, start_tc)

        # Index effects by timeline offset
        fx_map = {}
        for kind, node, ofs, L in items:
            if kind == "fx":
                nm, params = effect_name_and_params(node)
                fx_map.setdefault(ofs, []).append({"name": nm, "params": params, "len": int(L or 0)})

        rows = []
        for idx, (kind, node, ofs, L) in enumerate([x for x in items if x[0] == "clip"], start=1):
            sc: SourceClip = node
            smob = getattr(sc, "mob", None)

            # DiskLabel/TapeID strictly from Source Mob Ref
            disk = disklabel_from_smob(smob)
            tape = tapeid_from_smob(smob)

            # Source TC/rate from Source Mob Ref Timecode slot (locked lookup)
            src_rate = None
            src_drop = False
            src_tc_start = 0
            if smob is not None:
                for ss in (smob.slots or []):
                    if isinstance(ss.segment, Timecode):
                        src_tc_start = int(getattr(ss.segment, "start", 0) or 0)
                        r = as_float_rate(getattr(ss, "edit_rate", None))
                        if r is not None:
                            src_rate = r
                        src_drop = bool(getattr(ss.segment, "drop", False))
                        break
            if src_rate is None:
                src_rate = edit_rate  # fallback to timeline rate if missing

            # Clip offsets
            src_offset_frames = int(getattr(sc, "start_time", getattr(sc, "start", 0)) or 0)
            start_frames = src_tc_start + src_offset_frames
            end_frames = start_frames + int(L or 0)

            # End-of-chain import mob for URL + original length
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

            # Descriptor length for original source clip length
            orig_len = 0
            try:
                desc = getattr(end_mob, "descriptor", None)
                if desc:
                    lnode = getattr(desc, "length", None) or (desc.get("Length", None) if hasattr(desc, "get") else None)
                    if lnode is not None:
                        orig_len = int(_dec(lnode))
            except Exception:
                pass

            # FX at this timeline offset, with absolute timeline keyframes
            fx_list = fx_map.get(ofs, [])
            fx_names = ", ".join(fx["name"] for fx in fx_list) if fx_list else "N/A"
            if fx_list:
                kfs_lines = []
                for fx in fx_list:
                    flen = fx["len"]
                    if fx["params"]:
                        kfs_lines.append(f"[{fx['name']}]")
                        for p in fx["params"]:
                            if "time" in p:
                                try:
                                    tval = float(p["time"])
                                    rel = int(tval * (flen - 1)) if tval <= 2.0 and flen > 1 else int(tval)
                                except Exception:
                                    rel = 0
                                absf = ofs + rel
                                kfs_lines.append(
                                    f"  {p['name']} @ {frames_to_tc(absf, edit_rate, drop)} ({absf}f) -> {p['value']}"
                                )
                            else:
                                kfs_lines.append(f"  {p['name']} = {p['value']}")
                kfs = "\n".join(kfs_lines) if kfs_lines else "No effect data found."
            else:
                kfs = "No effect data found."

            smob_id = str(getattr(getattr(smob, "mob_id", None), "urn", "")) or str(getattr(smob, "mob_id", ""))
            try:
                track_id = getattr(sc, "source_slot_id", None) or getattr(sc, "source_track_id", None)
            except Exception:
                track_id = None

            rows.append({
                "Event": idx,
                "Event Name": src_fname,
                "Clip Name": src_fname,
                "Source File Name": src_fname,
                "Source File Path": src_path,
                "DiskLabel": disk,
                "TapeID": tape,
                "SourceMobID": smob_id or "N/A",
                "TrackID": track_id if track_id is not None else "N/A",
                "Source Clip EditRate": src_rate,
                "Timeline Start TC": frames_to_tc(ofs, edit_rate, drop),
                "Source Clip start time code": frames_to_tc(src_tc_start, src_rate, src_drop),
                "Source Clip offset": frames_to_tc(src_offset_frames, src_rate, src_drop),
                "StartTime": frames_to_tc(start_frames, src_rate, src_drop),
                "End Time": frames_to_tc(end_frames, src_rate, src_drop),
                "Event Length": int(L or 0),
                "Source Clip start (frames)": src_tc_start,
                "Source Clip offset (frames)": src_offset_frames,
                "StartTime (frames)": start_frames,
                "Effect Name": fx_names,
                "Keyframe Details": kfs,
                "Orig Source Clip length": orig_len,
            })

        # Timeline summary (top of CSV)
        total_len = sum(r["Event Length"] for r in rows)
        summary = [
            ["Timeline Summary"],
            ["Timeline Name", getattr(comp, "name", "(unnamed)")],
            ["Timeline Edit Rate", f"{edit_rate} {'(DF)' if drop else '(NDF)'}"],
            ["Timeline Start", frames_to_tc(start_tc, edit_rate, drop)],
            ["Timeline Length", f"{frames_to_tc(total_len, edit_rate, drop)} ({total_len} frames)"],
            ["Total number of EDL events found", len(rows)],
            ["Total number of unique sources", len({(r['Source File Path'], r['Source File Name']) for r in rows})],
            [],
        ]

    # Write CSV in the expected format
    with open(out_csv, "w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        w.writerows(summary)
        if rows:
            header = list(rows[0].keys())
            w.writerow(header)
            for r in rows:
                w.writerow([r[h] for h in header])
    return out_csv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aaf", required=True)
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = build_output(args.aaf, args.timeline, args.out)
    print("Saved:", out)


if __name__ == "__main__":
    main()
