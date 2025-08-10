#!/usr/bin/env python3
# aaf_wip_8.py
# Stable AAF → Super EDL + FX extractor
# - Reverted to v5/v6-proven chain resolution + metadata crawl
# - Composition selection & CLI quality-of-life from v7
# - FX-on-filler placeholders restored; Pan&Zoom still-path hunt
#
# Usage:
#   python aaf_wip_8.py "path/to/file.aaf" --comp "My Edit.Exported.01"
#   python aaf_wip_8.py "path/to/file.aaf" --list-comps

import argparse
import csv
import os
import re
import sys
import urllib.parse
from datetime import datetime
from collections import deque

try:
    import aaf2
    from aaf2.components import Sequence, SourceClip, OperationGroup, Timecode
except Exception as e:
    print("ERROR: pyaaf2 (aaf2) must be installed.", file=sys.stderr)
    raise

# ---------------------------
# Small helpers
# ---------------------------

def safe_int(x, d=0):
    try:
        return int(x)
    except Exception:
        return d

def unwrap(x):
    try:
        if hasattr(x, "value"):
            return unwrap(x.value)
        return x
    except Exception:
        return x

def frames_to_tc(fc, fps=25.0, drop=False):
    """Format frames → HH:MM:SS:FF (or ; for drop)."""
    try:
        fc = int(fc)
        fps_i = int(round(float(fps or 25.0)))
        if fps_i <= 0:
            return "N/A"
    except Exception:
        return "N/A"
    h = fc // (3600 * fps_i)
    m = (fc % (3600 * fps_i)) // (60 * fps_i)
    s = (fc % (60 * fps_i)) // fps_i
    f = fc % fps_i
    return f"{h:02}:{m:02}:{s:02}{';' if drop else ':'}{f:02}"

def decode_url_to_path(url: str):
    """ImportDescriptor→Locator→URLString decoding, UNC-safe."""
    if not url:
        return "N/A", "N/A"
    try:
        u = urllib.parse.urlparse(url)
        p = urllib.parse.unquote(u.path or "")
        if u.netloc:
            full = f"//{u.netloc}{p}"
        else:
            full = p
        base = os.path.basename(p) or "N/A"
        directory = os.path.dirname(full) or "N/A"
        return base, directory
    except Exception:
        return "Path Error", "N/A"

def sanitize_placeholder_name(text: str) -> str:
    base = (text or "").strip().lower()
    base = re.sub(r"[^0-9a-z]+", "_", base).strip("_")
    return f"{base}_placeholder.png" if base else "fx_placeholder.png"

# ---------------------------
# Composition discovery
# ---------------------------

def _is_picture_sequence(seg):
    if not isinstance(seg, Sequence):
        return False
    try:
        dd = getattr(seg, "data_definition", None)
        return (dd is None) or ("Picture" in str(dd))
    except Exception:
        return True

def list_compositions(f):
    names = []
    for m in f.content.mobs:
        try:
            nm = getattr(m, "name", "") or ""
            if not nm:
                continue
            for s in getattr(m, "slots", []):
                if _is_picture_sequence(getattr(s, "segment", None)):
                    names.append(nm)
                    break
        except Exception:
            continue
    return sorted(set(names))

def pick_composition(f, requested=None):
    comps = list_compositions(f)
    if requested and requested in comps:
        chosen = requested
    else:
        exp = [c for c in comps if c.endswith(".Exported.01")]
        chosen = exp[0] if exp else (comps[0] if comps else None)
    if not chosen:
        return None, None, "ERROR: no composition with a picture sequence found."
    for m in f.content.mobs:
        if getattr(m, "name", "") == chosen:
            warn = None if (requested in (None, chosen)) else f'WARNING: requested comp "{requested}" not found. Using "{chosen}".'
            return m, chosen, warn
    return None, None, "ERROR: composition not retrievable."

def comp_timecode(comp):
    """Return (timeline_start_frames, fps, drop)."""
    if comp is None:
        return 0, 25.0, False
    for s in comp.slots:
        seg = getattr(s, "segment", None)
        if isinstance(seg, Timecode):
            return safe_int(getattr(seg, "start", 0), 0), float(getattr(seg, "fps", 25) or 25), bool(getattr(seg, "drop", False))
        if isinstance(seg, Sequence):
            for c in getattr(seg, "components", []):
                if isinstance(c, Timecode):
                    return safe_int(getattr(c, "start", 0), 0), float(getattr(c, "fps", 25) or 25), bool(getattr(c, "drop", False))
    return 0, 25.0, False

def choose_picture_slot(comp):
    if comp is None:
        return None
    # prefer sequence whose data_definition is Picture
    for s in comp.slots:
        seg = getattr(s, "segment", None)
        if _is_picture_sequence(seg):
            return s
    # fallback: any sequence
    for s in comp.slots:
        if isinstance(getattr(s, "segment", None), Sequence):
            return s
    return None

# ---------------------------
# Sequence traversal
# ---------------------------

def _has_nested_sourceclip(node):
    dq = deque([node])
    seen = set()
    while dq:
        n = dq.popleft()
        if id(n) in seen:
            continue
        seen.add(id(n))
        if isinstance(n, SourceClip):
            return True
        for attr in ("components", "segments", "input_segments"):
            it = getattr(n, attr, None)
            if it:
                for c in it:
                    dq.append(c)
    return False

def collect_picture_events(root_segment, base_ofs_frames):
    """
    Collect:
      - clip events: mobid, slotid, src offset, length, abs timeline ofs
      - filler_fx events: OperationGroup with no nested SourceClip
    """
    events = []
    dq = deque([(root_segment, base_ofs_frames)])
    while dq:
        node, ofs = dq.popleft()
        length = safe_int(getattr(node, "length", 0), 0)

        if isinstance(node, SourceClip):
            sid = str(unwrap(node.get("SourceID", ""))) if "SourceID" in node.keys() else ""
            slotid = safe_int(unwrap(node.get("SourceMobSlotID", 0)), 0) if "SourceMobSlotID" in node.keys() else 0
            src_off = safe_int(unwrap(node.get("StartTime", node.get("Start", 0))), 0)
            events.append({
                "type": "clip",
                "ofs": ofs,
                "len": length,
                "mobid": sid,
                "slotid": slotid,
                "src_off": src_off,
                "node": node
            })

        elif isinstance(node, OperationGroup):
            if not _has_nested_sourceclip(node) and length > 0:
                events.append({
                    "type": "filler_fx",
                    "ofs": ofs,
                    "len": length,
                    "node": node
                })

        # expand children with a running offset
        acc = 0
        for attr in ("components", "segments", "input_segments"):
            it = getattr(node, attr, None)
            if it:
                for c in it:
                    dq.append((c, ofs + acc))
                    acc += safe_int(getattr(c, "length", 0), 0)
    return events

# ---------------------------
# Mob map + chain resolution (v6 semantics)
# ---------------------------

def get_umid_str(mob):
    # best-effort for both dict-like and object-like
    try:
        return str(unwrap(mob["MobID"]))
    except Exception:
        pass
    try:
        return str(getattr(mob, "mob_id"))
    except Exception:
        return ""

def build_mob_map(f):
    mm = {}
    for mob in f.content.mobs:
        u = get_umid_str(mob)
        if u:
            mm[u] = mob
    return mm

def first_sourceclip_in(seg):
    dq = deque([seg]); seen=set()
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
                for x in it:
                    dq.append(x)
    return None

def pick_slot(mob, target_slotid):
    exact=None; picture=None; first=None
    for s in getattr(mob, "slots", []):
        sid = safe_int(getattr(s, "slot_id", getattr(s, "physical_track_number", 0)), 0)
        if first is None:
            first = s
        if target_slotid and sid == target_slotid:
            exact = s
        try:
            dd = getattr(getattr(s, "segment", None), "data_definition", None)
            if dd and "Picture" in str(dd) and picture is None:
                picture = s
        except Exception:
            pass
    return exact or picture or first

def scan_for_url_in_mob(mob):
    """ImportDescriptor → Locator → URLString."""
    try:
        if "EssenceDescription" not in mob.keys():
            return None
        ed = mob["EssenceDescription"]
        try:
            iterable = list(ed.value) if hasattr(ed.value, "__iter__") else [ed.value]
        except Exception:
            iterable = [ed]
        for desc in iterable:
            if hasattr(desc, "keys") and "Locator" in desc.keys():
                for loc in desc["Locator"]:
                    if hasattr(loc, "keys") and "URLString" in loc.keys():
                        return str(unwrap(loc["URLString"]))
        # rare: URLString on descriptor directly
        try:
            u = unwrap(ed["URLString"])
            if u:
                return str(u)
        except Exception:
            pass
    except Exception:
        pass
    return None

def resolve_chain_with_edges(mob_map, first_mobid, first_slotid, max_hops=64):
    """
    Follow SourceID chain until we land on an ImportDescriptor with a Locator URL.
    Return: end_mob, url, hops, chain(list first→end), last_edge_len (length of last SC hop)
    """
    chain = []
    cur = mob_map.get(first_mobid)
    last_slot = first_slotid
    hops = 0
    last_edge_len = 0

    while cur and hops < max_hops:
        chain.append(cur)
        url = scan_for_url_in_mob(cur)
        if url:
            return cur, url, hops, chain, last_edge_len

        chosen = pick_slot(cur, last_slot)
        seg = getattr(chosen, "segment", None) if chosen else None
        sc = first_sourceclip_in(seg) if seg else None
        if not sc:
            break

        # capture edge length of the hop we are about to take
        try:
            L = safe_int(getattr(sc, "length", 0), 0)
            if L > 0:
                last_edge_len = L
        except Exception:
            pass

        next_id = str(unwrap(sc.get("SourceID", ""))) if "SourceID" in sc.keys() else ""
        next_slot = safe_int(unwrap(sc.get("SourceMobSlotID", 0)), 0) if "SourceMobSlotID" in sc.keys() else 0

        cur = mob_map.get(next_id)
        last_slot = next_slot if next_slot else last_slot
        hops += 1

    return cur, None, hops, chain, last_edge_len

def tc_from_any_mob(mob, fallback_fps=25.0):
    s_start, s_fps, s_drop = 0, fallback_fps, False
    if mob is None:
        return s_start, s_fps, s_drop
    for s in getattr(mob, "slots", []):
        seg = getattr(s, "segment", None)
        if isinstance(seg, Timecode):
            return safe_int(getattr(seg, "start", 0), 0), float(getattr(seg, "fps", fallback_fps) or fallback_fps), bool(getattr(seg, "drop", False))
        if isinstance(seg, Sequence):
            for c in getattr(seg, "components", []):
                if isinstance(c, Timecode):
                    return safe_int(getattr(c, "start", 0), 0), float(getattr(c, "fps", fallback_fps) or fallback_fps), bool(getattr(c, "drop", False))
    return s_start, s_fps, s_drop

def source_tc_from_chain(chain, default_fps=25.0):
    """Scan chain from end→start and return first non-zero Timecode."""
    s_start, s_fps, s_drop = 0, default_fps, False
    for mob in reversed(chain):  # end→start preference
        s_start, s_fps, s_drop = tc_from_any_mob(mob, default_fps)
        if s_start != 0:
            break
    return s_start, s_fps, s_drop

# ---------------------------
# Deep metadata crawl (DiskLabel/TapeID) — restored
# ---------------------------

def _maybe_collect_tag(name_raw, val, acc):
    name = (str(name_raw) or "").strip().lower()
    if not name:
        return
    if name in ("disklabel", "_importdisklab", "disk label", "importdisklab"):
        if not acc.get("DiskLabel") and val not in (None, ""):
            acc["DiskLabel"] = str(val)
    if name in ("tapeid", "tape_id", "tape id", "reel", "reelname"):
        if not acc.get("TapeID") and val not in (None, ""):
            acc["TapeID"] = str(val)

def deep_metadata_from_chain(chain_mobs):
    """
    Walk chain nearest-end → farthest and collect DiskLabel/TapeID from:
      - MobAttributeList
      - TaggedValueAttributeList (including under _IMPORTSETTING)
      - UserComments (plaintext crawl)
    """
    acc = {"DiskLabel": "", "TapeID": ""}
    for mob in reversed(chain_mobs):  # prefer end side
        try:
            if "MobAttributeList" in mob.keys():
                for a in mob["MobAttributeList"]:
                    nm = unwrap(a.get("Name", getattr(a, "name", ""))) if hasattr(a, "get") else ""
                    val = unwrap(a.get("Value", getattr(a, "value", ""))) if hasattr(a, "get") else ""
                    _maybe_collect_tag(nm, val, acc)
                    if hasattr(a, "keys") and "TaggedValueAttributeList" in a.keys():
                        for tv in a["TaggedValueAttributeList"]:
                            nm2 = unwrap(tv.get("Name", getattr(tv, "name", "")))
                            val2 = unwrap(tv.get("Value", getattr(tv, "value", "")))
                            _maybe_collect_tag(nm2, val2, acc)

            for key in ("TaggedValueAttributeList", "UserComments"):
                if key in mob.keys():
                    for tv in mob[key]:
                        nm3 = unwrap(tv.get("Name", getattr(tv, "name", "")))
                        val3 = unwrap(tv.get("Value", getattr(tv, "value", "")))
                        _maybe_collect_tag(nm3, val3, acc)
        except Exception:
            continue
        if acc["DiskLabel"] and acc["TapeID"]:
            break
    return acc

# ---------------------------
# FX (names + params) and Pan&Zoom still hunt
# ---------------------------

def effect_name_from_og(og: OperationGroup):
    attrs = {}
    try:
        if "ComponentAttributeList" in og.keys():
            for a in og["ComponentAttributeList"]:
                v = a.get("Value") if hasattr(a, "get") else None
                if v is not None:
                    attrs[str(unwrap(a.get("Name", a.name)))] = unwrap(v)
    except Exception:
        pass
    plugin_name = attrs.get("_EFFECT_PLUGIN_NAME")
    plugin_class = attrs.get("_EFFECT_PLUGIN_CLASS")
    if plugin_class and plugin_name:
        return f"{plugin_class} : {plugin_name}"
    if plugin_name:
        return str(plugin_name)
    try:
        op = og.get("Operation")
        if op:
            raw = str(unwrap(op))
            part = raw.split(" ", 1)[-1]
            return part.replace("_v2", "").replace("_2", "").replace("_", " ").strip()
    except Exception:
        pass
    return "Unknown Effect"

def extract_parameters_from_og(og: OperationGroup):
    static_lines = []
    animated = {}
    try:
        if "Parameters" in og.keys():
            for p in og["Parameters"]:
                pname = str(unwrap(p.get("Name", getattr(p, "name", ""))) or p.__class__.__name__)
                plist = p.get("PointList") if hasattr(p, "get") else None
                if plist:
                    kfs = []
                    for cp in plist:
                        if hasattr(cp, "keys"):
                            t = unwrap(cp.get("Time", 0))
                            v = unwrap(cp.get("Value", None))
                            kfs.append({"time": t, "value": v})
                    if kfs:
                        animated[pname] = kfs
                else:
                    val = unwrap(p.get("Value", None)) if hasattr(p, "get") else None
                    if val is not None:
                        static_lines.append(f"- Parameter: {pname} -> Value: {val}")
    except Exception:
        pass
    return ("\n".join(static_lines), animated)

_PATH_LIKE = re.compile(r'([a-zA-Z]:[\\/].+)|(/[^/].+)|(\\\\[^\\]+[\\].+)|(^file://)', re.IGNORECASE)

def find_panzoom_still_path(og: OperationGroup):
    """Best-effort crawl for a still path on Avid Pan & Zoom."""
    # Look into attributes & parameters for any value that smells like a path/URL
    try:
        if "ComponentAttributeList" in og.keys():
            for a in og["ComponentAttributeList"]:
                v = unwrap(a.get("Value", None)) if hasattr(a, "get") else None
                if isinstance(v, str) and _PATH_LIKE.search(v):
                    return v
    except Exception:
        pass
    try:
        if "Parameters" in og.keys():
            for p in og["Parameters"]:
                v = unwrap(p.get("Value", None)) if hasattr(p, "get") else None
                if isinstance(v, str) and _PATH_LIKE.search(v):
                    return v
                plist = p.get("PointList") if hasattr(p, "get") else None
                if plist:
                    for cp in plist:
                        vv = unwrap(cp.get("Value", None)) if hasattr(cp, "get") else None
                        if isinstance(vv, str) and _PATH_LIKE.search(vv):
                            return vv
    except Exception:
        pass
    return None

# ---------------------------
# CSV writing (legacy-ish shape)
# ---------------------------

def write_csv(out_path, timeline_name, t_fps, t_drop, t_start, total_len, rows):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"Summary:"])
        w.writerow([f"   Timeline Name : {timeline_name}"])
        w.writerow([f"   Timeline Edit Rate : {t_fps} ({'DF' if t_drop else 'NDF'})"])
        w.writerow([f"   Timeline Start : {frames_to_tc(t_start, t_fps, t_drop)}"])
        w.writerow([f"   Timeline Length : {frames_to_tc(total_len, t_fps, t_drop)} ({total_len} frames)"])
        w.writerow([f"   Total number of EDL events found : {len(rows)}"])
        w.writerow([f"   Total number of unique sources : {len({r.get('SrcMobID') for r in rows if r.get('Kind')=='clip'})}"])
        w.writerow([])
        # Header (keep simple; your downstream can add/rename as needed)
        w.writerow([
            "Event #",
            "Kind",
            "Timeline Start (TC)",
            "Timeline Start (frames)",
            "Length (frames)",
            "Effect Name",
            "Effect Params (static)",
            "Source MobID",
            "Source URL",
            "Source File",
            "Source Dir",
            "DiskLabel",
            "TapeID",
            "Source TC Start (frames)",
            "Source Rate",
            "Source DF",
            "Source Offset (frames)",
            "StartTime (TC @ source)",
            "EndTime (TC @ source)",
            "Placeholder / Override"
        ])
        for i, r in enumerate(rows, start=1):
            w.writerow([
                i,
                r.get("Kind", ""),
                frames_to_tc(r.get("TLStart", 0), t_fps, t_drop),
                r.get("TLStart", 0),
                r.get("Len", 0),
                r.get("FXName", "(none)"),
                r.get("FXStatic", ""),
                r.get("SrcMobID", ""),
                r.get("SrcURL", "N/A"),
                r.get("SrcFile", "N/A"),
                r.get("SrcDir", "N/A"),
                r.get("DiskLabel", ""),
                r.get("TapeID", ""),
                r.get("SrcTCStart", 0),
                r.get("SrcRate", 25.0),
                "DF" if r.get("SrcDrop", False) else "NDF",
                r.get("SrcOffset", 0),
                r.get("StartTimeTC", "N/A"),
                r.get("EndTimeTC", "N/A"),
                r.get("Placeholder", "")
            ])

# ---------------------------
# Main
# ---------------------------

def main():
    ap = argparse.ArgumentParser(description="Stable AAF → Super EDL + FX extractor (v8)")
    ap.add_argument("aaf", help="Path to AAF file")
    ap.add_argument("--comp", help="Composition (timeline) name to use")
    ap.add_argument("--list-comps", action="store_true", help="List compositions and exit")
    args = ap.parse_args()

    aaf_path = args.aaf
    if not os.path.exists(aaf_path):
        print(f"ERROR: file not found: {aaf_path}", file=sys.stderr)
        sys.exit(2)

    with aaf2.open(aaf_path) as f:
        if args["list_comps"] if isinstance(args, dict) else args.list_comps:
            for nm in list_compositions(f):
                print(nm)
            return

        comp, comp_name, warn = pick_composition(f, args.comp)
        if warn:
            print(warn)
        if comp is None:
            print("ERROR: could not find a usable composition.", file=sys.stderr)
            sys.exit(3)

        tl_start, tl_fps, tl_drop = comp_timecode(comp)

        pic_slot = choose_picture_slot(comp)
        if pic_slot is None or not isinstance(getattr(pic_slot, "segment", None), Sequence):
            print("ERROR: no picture sequence on selected composition.", file=sys.stderr)
            sys.exit(4)

        events = collect_picture_events(pic_slot.segment, tl_start)
        total_len = sum(e["len"] for e in events)

        # Mob map for UMID resolution
        mob_map = build_mob_map(f)

        rows = []
        for e in events:
            if e["type"] == "clip":
                mobid = e["mobid"]
                slotid = e["slotid"]
                end_mob, url, hops, chain, last_edge_len = resolve_chain_with_edges(mob_map, mobid, slotid)
                # url/file/dir
                src_file, src_dir = decode_url_to_path(url) if url else ("N/A", "N/A")
                # labels
                md = deep_metadata_from_chain(chain)
                disklabel = md.get("DiskLabel", "")
                tapeid = md.get("TapeID", "")
                # source TC
                s_start, s_rate, s_drop = source_tc_from_chain(chain, default_fps=tl_fps)
                # offsets (in source rate domain)
                src_off = e["src_off"]  # frames, as stored on timeline SourceClip node
                start_tc = frames_to_tc(s_start + src_off, s_rate, s_drop)
                end_tc = frames_to_tc(s_start + src_off + (e["len"] - 1), s_rate, s_drop) if e["len"] > 0 else frames_to_tc(s_start + src_off, s_rate, s_drop)

                rows.append({
                    "Kind": "clip",
                    "TLStart": e["ofs"],
                    "Len": e["len"],
                    "FXName": "(none)",
                    "FXStatic": "",
                    "SrcMobID": mobid,
                    "SrcURL": url or "N/A",
                    "SrcFile": src_file,
                    "SrcDir": src_dir,
                    "DiskLabel": disklabel,
                    "TapeID": tapeid,
                    "SrcTCStart": s_start,
                    "SrcRate": s_rate,
                    "SrcDrop": s_drop,
                    "SrcOffset": src_off,
                    "StartTimeTC": start_tc,
                    "EndTimeTC": end_tc,
                    "Placeholder": ""  # not applicable for real sources
                })

            else:  # filler_fx
                og = e["node"]
                fx_name = effect_name_from_og(og)
                fx_static, _animated = extract_parameters_from_og(og)

                placeholder = sanitize_placeholder_name(fx_name)
                override_url = None
                if "pan" in fx_name.lower() and "zoom" in fx_name.lower():
                    cand = find_panzoom_still_path(og)
                    if cand:
                        override_url = cand
                        src_file, src_dir = decode_url_to_path(cand)
                        placeholder = src_file  # use real still’s name if found

                rows.append({
                    "Kind": "filler_fx",
                    "TLStart": e["ofs"],
                    "Len": e["len"],
                    "FXName": fx_name,
                    "FXStatic": fx_static,
                    "SrcMobID": "FX_ON_FILLER",
                    "SrcURL": override_url or "N/A",
                    "SrcFile": "N/A",
                    "SrcDir": "N/A",
                    "DiskLabel": "",
                    "TapeID": "",
                    "SrcTCStart": 0,
                    "SrcRate": tl_fps,
                    "SrcDrop": tl_drop,
                    "SrcOffset": 0,
                    "StartTimeTC": frames_to_tc(e["ofs"], tl_fps, tl_drop),
                    "EndTimeTC": frames_to_tc(e["ofs"] + (e["len"] - 1), tl_fps, tl_drop) if e["len"] > 0 else frames_to_tc(e["ofs"], tl_fps, tl_drop),
                    "Placeholder": placeholder
                })

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = f"{comp_name}_super_edl_fx_report_v2_{ts}.csv"
        out_path = os.path.join(os.path.dirname(aaf_path), out_name)
        write_csv(out_path, comp_name, tl_fps, tl_drop, tl_start, total_len, rows)
        print(f"\nWrote: {out_name}\n")
        print("Summary:\n")
        print(f"   Timeline Name : {comp_name}")
        print(f"   Timeline Edit Rate : {tl_fps} ({'DF' if tl_drop else 'NDF'})")
        print(f"   Timeline Start : {frames_to_tc(tl_start, tl_fps, tl_drop)}")
        print(f"   Timeline Length : {frames_to_tc(total_len, tl_fps, tl_drop)} ({total_len} frames)")
        print(f"   Total number of EDL events found : {len(rows)}")
        uniq = len({r.get('SrcMobID') for r in rows if r.get('Kind')=='clip'})
        print(f"   Total number of unique sources : {uniq}")

if __name__ == "__main__":
    main()
