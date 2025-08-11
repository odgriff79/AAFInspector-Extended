# super_edl_fx_from_aaf.py
# AAF → Super EDL + FX (headless)
# - UMID + SourceMobSlotID resolver to the *end* mob with ImportDescriptor/Locator
# - Source TC = first non-zero Timecode.Start found when scanning chain (end→start)
# - DiskLabel from deep scan of _IMPORTSETTING/TaggedValueAttributeList/_IMPORTDISKLAB (plaintext crawl)
# - Orig Source Clip length = length of the *last hop* SourceClip that points into the end mob
# - Effects discovery from ComponentAttributeList (_EFFECT_PLUGIN_NAME/CLASS) with fallback to Operation name
# - Pan & Zoom (on filler or clip) treated as a *known source* via filepath extraction; filler placeholders for other FX
# - DF/NDF respected on timecode formatting

import os
import re
import csv
import sys
import math
import json
import urllib.parse
from collections import deque, defaultdict
from datetime import datetime

# Requires pyaaf2 >= 1.4 (works with 1.7.1 too)
import aaf2
from aaf2.components import Timecode, Sequence, SourceClip, OperationGroup
from aaf2.mobs import CompositionMob

# -----------------------------
# Helpers: timecode + formatting
# -----------------------------

def frames_to_tc(frame_count, fps=25.0, drop=False):
    """Convert frames → TC string. df/ndf aware."""
    try:
        fc = int(frame_count)
        fps = float(fps)
        if fps <= 0:
            return "N/A"
    except Exception:
        return "N/A"
    sep = ";" if drop else ":"
    ip = int(round(fps))
    h = fc // (3600 * ip)
    m = (fc % (3600 * ip)) // (60 * ip)
    s = (fc % (60 * ip)) // ip
    f = fc % ip
    return f"{h:02}:{m:02}:{s:02}{sep}{f:02}"

def safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def unwrap(x):
    """pyaaf2 Value -> native."""
    try:
        v = getattr(x, "value", x)
        # Recursively unwrap if nested
        if hasattr(v, "value"):
            return unwrap(v)
        return v
    except Exception:
        return x

# -------------------------------------------------
# Path utilities (preserve UNC/URL semantics safely)
# -------------------------------------------------

def decode_url_to_path(url: str):
    """
    AAF ImportDescriptor → Locator → URLString:
    - file://10.10.1.195/nearline/...  -> //10.10.1.195/nearline/...
    - file:///U%3A/...                 -> /U:/...
    Return (basename, dir_path)
    """
    try:
        if not url:
            return "N/A", "N/A"
        u = urllib.parse.urlparse(url)
        # keep the netloc as UNC if present
        path = urllib.parse.unquote(u.path or "")
        if u.netloc:
            full = f"//{u.netloc}{path}"
        else:
            full = path
        base = os.path.basename(path) or "N/A"
        directory = os.path.dirname(full) or "N/A"
        return base, directory
    except Exception:
        return "Path Error", "N/A"

def sanitize_placeholder_name(text: str) -> str:
    """For filler-FX placeholders only; keep name readable."""
    base = text.strip().lower()
    base = re.sub(r"[^0-9a-z]+", "_", base).strip("_")
    return f"{base}_placeholder.png" if base else "fx_placeholder.png"

# --------------------------------
# Composition + slot identification
# --------------------------------

def pick_top_comp(f):
    """Prefer named *.Exported.01 composition; else first CompositionMob."""
    for m in f.content.mobs:
        if isinstance(m, CompositionMob) and getattr(m, "name", "").endswith(".Exported.01"):
            return m
    for m in f.content.mobs:
        if isinstance(m, CompositionMob):
            return m
    return None

def comp_timecode(comp: CompositionMob):
    """Return (start_frames, fps, drop) of top composition timecode."""
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

def choose_picture_slot(comp: CompositionMob):
    """Pick primary picture track slot."""
    if comp is None:
        return None
    for s in comp.slots:
        seg = getattr(s, "segment", None)
        try:
            dd = getattr(seg, "data_definition", None)
            if dd and "Picture" in str(dd):
                return s
        except Exception:
            continue
    # fallback: first sequence slot
    for s in comp.slots:
        if isinstance(getattr(s, "segment", None), Sequence):
            return s
    return None

# ---------------------------
# Deep traversal of sequences
# ---------------------------

def first_sourceclip_in(node):
    """Breadth search: find first SourceClip inside node (Sequence/OperationGroup)."""
    dq = deque([node])
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
                for c in it:
                    dq.append(c)
    return None

def has_nested_sourceclip(node):
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
    Walk Sequence graph, collecting:
      - SourceClip events: mobid, slotid, src offset, length, timeline ofs
      - FX on filler: OperationGroup with no nested SourceClip
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
                "track_id": safe_int(getattr(getattr(node, "slot", None), "slot_id", 0), 0)
            })

        elif isinstance(node, OperationGroup):
            if not has_nested_sourceclip(node) and length > 0:
                events.append({
                    "type": "filler_fx",
                    "ofs": ofs,
                    "len": length,
                    "node": node
                })

        # expand children with cumulative offset
        acc = 0
        for attr in ("components", "segments", "input_segments"):
            it = getattr(node, attr, None)
            if it:
                for c in it:
                    dq.append((c, ofs + acc))
                    acc += safe_int(getattr(c, "length", 0), 0)
    return events

# -----------------------------------------
# Mob chain resolution to genuine end-mob
# -----------------------------------------

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
    except Exception:
        pass
    return None

def pick_slot(mob, target_slotid):
    """Prefer the referenced slot id; else picture; else first slot."""
    exact = None
    picture = None
    first = None
    for s in mob.slots:
        if first is None:
            first = s
        sid = safe_int(getattr(s, "slot_id", getattr(s, "physical_track_number", 0)), 0)
        if target_slotid and sid == target_slotid:
            exact = s
        try:
            dd = getattr(getattr(s, "segment", None), "data_definition", None)
            if dd and "Picture" in str(dd) and picture is None:
                picture = s
        except Exception:
            pass
    return exact or picture or first

def resolve_chain_with_edges(mob_map, first_mobid, first_slotid, max_hops=64):
    """
    Follow SourceID chain until we land on an ImportDescriptor with a Locator URL.
    Return:
      end_mob, url, hops, chain(list from first→end), last_edge_len (length of last SourceClip hop)
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

    # ended without url; return what we have
    return cur, None, hops, chain, last_edge_len

def tc_from_any_mob(mob, fallback_fps=25.0):
    """Extract (start, fps, drop) from any Timecode in mob."""
    s_start = 0
    s_fps = fallback_fps
    s_drop = False
    if mob is None:
        return s_start, s_fps, s_drop
    for s in mob.slots:
        seg = getattr(s, "segment", None)
        if isinstance(seg, Timecode):
            return safe_int(getattr(seg, "start", 0), 0), float(getattr(seg, "fps", fallback_fps) or fallback_fps), bool(getattr(seg, "drop", False))
        if isinstance(seg, Sequence):
            for c in getattr(seg, "components", []):
                if isinstance(c, Timecode):
                    return safe_int(getattr(c, "start", 0), 0), float(getattr(c, "fps", fallback_fps) or fallback_fps), bool(getattr(c, "drop", False))
    return s_start, s_fps, s_drop

def source_tc_from_chain(chain, default_fps=25.0):
    """
    Scan chain from end→start and return first non-zero timecode.
    """
    s_start, s_fps, s_drop = 0, default_fps, False
    for mob in reversed(chain):  # end→start
        s_start, s_fps, s_drop = tc_from_any_mob(mob, default_fps)
        if s_start != 0:
            break
    return s_start, s_fps, s_drop

# ---------------------------------------------
# Deep metadata crawl (DiskLabel / TapeID etc.)
# ---------------------------------------------

def _maybe_collect_tag(name_raw, val, acc):
    name = (str(name_raw) or "").strip().lower()
    if not name:
        return
    # normalize common keys
    if name in ("disklabel", "_importdisklab", "disk label", "importdisklab"):
        if not acc.get("DiskLabel") and val not in (None, ""):
            acc["DiskLabel"] = str(val)
    if name in ("tapeid", "tape_id", "tape id"):
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
            # MobAttributeList
            if "MobAttributeList" in mob.keys():
                for a in mob["MobAttributeList"]:
                    # Name/Value pairs
                    nm = unwrap(a.get("Name", getattr(a, "name", ""))) if hasattr(a, "get") else ""
                    val = unwrap(a.get("Value", getattr(a, "value", ""))) if hasattr(a, "get") else ""
                    _maybe_collect_tag(nm, val, acc)

                    # Tagged sublist
                    if hasattr(a, "keys") and "TaggedValueAttributeList" in a.keys():
                        for tv in a["TaggedValueAttributeList"]:
                            nm2 = unwrap(tv.get("Name", getattr(tv, "name", "")))
                            val2 = unwrap(tv.get("Value", getattr(tv, "value", "")))
                            _maybe_collect_tag(nm2, val2, acc)

            # Top-level TaggedValue vectors (some AAFs)
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

# ---------------------------------------
# Effects parsing (plain text, no hardcode)
# ---------------------------------------

def effect_name_from_og(og: OperationGroup):
    # prefer ComponentAttributeList: _EFFECT_PLUGIN_NAME/_EFFECT_PLUGIN_CLASS
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

    # fallback: OperationDefinition name (tidy up)
    try:
        op = og.get("Operation")
        if op:
            raw = str(unwrap(op))
            # Example "OperationDefinition Avid::Something"
            part = raw.split(" ", 1)[-1]
            return part.replace("_v2", "").replace("_2", "").replace("_", " ").strip()
    except Exception:
        pass

    return "Unknown Effect"

def extract_parameters_from_og(og: OperationGroup):
    """Return (static_params_str, animated_dict)."""
    static_lines = []
    animated = {}
    try:
        if "Parameters" in og.keys():
            for p in og["Parameters"]:
                pname = str(unwrap(p.get("Name", getattr(p, "name", ""))) or p.__class__.__name__)
                plist = p.get("PointList") if hasattr(p, "get") else None
                if plist and hasattr(plist, "__iter__"):
                    kfs = []
                    for cp in plist:
                        if hasattr(cp, "keys"):
                            t = unwrap(cp.get("Time", 0))
                            v = unwrap(cp.get("Value", "N/A"))
                            kfs.append({"time": t, "value": v})
                    if kfs:
                        animated[pname] = kfs
                else:
                    val = unwrap(p.get("Value", None)) if hasattr(p, "get") else None
                    if val is not None:
                        static_lines.append(f"- Parameter: {pname} -> Value: {val}")
    except Exception:
        pass
    static_str = "\n".join(static_lines)
    return static_str, animated

def find_binary_filepath_in_attrs(og: OperationGroup):
    """
    Heuristic: some Pan & Zoom variants stash a UTF-16LE filename inside a
    Value blob (ComponentAttributeList or Parameters). Try to decode bytes.
    """
    def try_decode_node(n):
        val = None
        try:
            if hasattr(n, "get") and "Value" in n.keys():
                val = n["Value"].value
        except Exception:
            pass
        if isinstance(val, (bytes, bytearray)):
            # try utf-16-le then utf-8
            for enc in ("utf-16-le", "utf-8", "latin-1"):
                try:
                    s = bytes(val).decode(enc, errors="ignore")
                    # strip leading junk before first slash/backslash
                    s2 = s[s.find("\\"):] if "\\" in s else s[s.find("/"):] if "/" in s else s
                    s2 = s2.replace("\\", "/")
                    if s2 and ("/" in s2):
                        return s2
                except Exception:
                    continue
        if isinstance(val, str):
            if ("/" in val) or ("\\" in val):
                return val.replace("\\", "/")
        return None

    # CAL
    try:
        if "ComponentAttributeList" in og.keys():
            for a in og["ComponentAttributeList"]:
                got = try_decode_node(a)
                if got:
                    return got
    except Exception:
        pass
    # Parameters
    try:
        if "Parameters" in og.keys():
            for p in og["Parameters"]:
                got = try_decode_node(p)
                if got:
                    return got
    except Exception:
        pass
    return None

def collect_timeline_fx(root_segment, base_ofs_frames):
    """
    Return dict: timeline_ofs -> { effect_name, length, static_params_str, animated_params, node }
    (also used to detect P&Z on filler)
    """
    results = {}
    dq = deque([(root_segment, base_ofs_frames)])
    while dq:
        node, ofs = dq.popleft()
        length = safe_int(getattr(node, "length", 0), 0)
        if isinstance(node, OperationGroup):
            # record any OperationGroup; later we'll decide if it's on filler
            ename = effect_name_from_og(node)
            sparams, animated = extract_parameters_from_og(node)
            results[ofs] = {
                "effect_name": ename,
                "length": length,
                "static_params_str": sparams,
                "animated_params": animated,
                "node": node
            }
        # expand children
        acc = 0
        for attr in ("components", "segments", "input_segments"):
            it = getattr(node, attr, None)
            if it:
                for c in it:
                    dq.append((c, ofs + acc))
                    acc += safe_int(getattr(c, "length", 0), 0)
    return results

def is_panzoom_name(name: str) -> bool:
    n = (name or "").lower()
    return ("pan" in n and "zoom" in n) or "pan&zoom" in n or "pan & zoom" in n

# ------------------------
# CSV row building / export
# ------------------------

CSV_COLUMNS = [
    'Event', 'Event Name', 'Clip Name', 'Source File Name', 'Source File Path',
    'DiskLabel', 'TapeID', 'SourceMobID', 'TrackID', 'Source Clip EditRate',
    'Timeline Start TC', 'Source Clip start time code', 'Source Clip offset',
    'StartTime', 'End Time', 'Event Length',
    'Source Clip start (frames)', 'Source Clip offset (frames)', 'StartTime (frames)',
    'Effect Name', 'Keyframe Details', 'Orig Source Clip length'
]

def build_keyframe_details(rec, timeline_ofs, seq_rate, seq_drop):
    lines = []
    if rec.get("animated_params"):
        lines.append('--- Animated Parameters ---')
        for pname, pts in rec["animated_params"].items():
            lines.append(f"  - Parameter: {pname} ({len(pts)} keyframes)")
            for kp in pts:
                t = kp.get("time", 0)
                try:
                    # if param time is normalized [0..1], approximate on duration
                    if rec["length"] > 1 and isinstance(t, (int, float)) and 0 <= t <= 1:
                        off = int(t * (rec["length"] - 1))
                    else:
                        off = safe_int(t, 0)
                except Exception:
                    off = 0
                absf = timeline_ofs + off
                lines.append(f"    Keyframe at {frames_to_tc(absf, seq_rate, seq_drop)} ({absf}f) -> Value: {kp.get('value','N/A')}")
    if rec.get("static_params_str"):
        if lines:
            lines.append("")
        lines.append('--- Static Parameters ---')
        lines.append(rec["static_params_str"])
    return "\n".join(lines) if lines else "No effect data found."

def process_aaf(aaf_path: str, out_csv: str = None, prefer_comp_name: str = None):
    with aaf2.open(aaf_path, "r") as f:
        comp = None
        if prefer_comp_name:
            for m in f.content.mobs:
                if isinstance(m, CompositionMob) and getattr(m, "name", "") == prefer_comp_name:
                    comp = m
                    break
        if comp is None:
            comp = pick_top_comp(f)
        if comp is None:
            raise RuntimeError("No CompositionMob found.")

        seq_start, seq_rate, seq_drop = comp_timecode(comp)
        slot = choose_picture_slot(comp)
        if slot is None:
            raise RuntimeError("No picture slot found in composition.")
        root_seg = getattr(slot, "segment", None)

        # Build mob map
        mob_map = {str(m.mob_id): m for m in f.content.mobs}

        # Collect timeline FX (for naming & params)
        fx_by_ofs = collect_timeline_fx(root_seg, seq_start)

        # Collect events
        events = collect_picture_events(root_seg, seq_start)

        # Dedupe exact duplicates (mobid, ofs, src_off, len)
        seen = set()
        uniq = []
        for e in events:
            key = (e.get("type"), e.get("mobid"), e.get("ofs"), e.get("src_off"), e.get("len"))
            if key not in seen:
                seen.add(key)
                uniq.append(e)
        events = sorted(uniq, key=lambda x: x["ofs"])

        # CSV rows
        rows = []
        for idx, e in enumerate(events, start=1):
            if e["type"] == "filler_fx":
                # Inspect effect record at timeline start
                fx = fx_by_ofs.get(e["ofs"])
                fx_name = fx["effect_name"] if fx else "N/A"
                kf = build_keyframe_details(fx or {}, e["ofs"], seq_rate, seq_drop)

                # If it’s Pan & Zoom on filler, treat it as a real source (binary filepath)
                fpath = None
                if fx:
                    if is_panzoom_name(fx_name):
                        # try to extract filepath from effect attributes
                        fpath = find_binary_filepath_in_attrs(fx["node"])

                if fpath:
                    # Known source from P&Z
                    fname = os.path.basename(fpath.replace("\\", "/"))
                    src_dir = os.path.dirname(fpath.replace("\\", "/")) or "N/A"
                    rows.append({
                        'Event': idx, 'Event Name': f"(P&Z Override) {fname}", 'Clip Name': fname,
                        'Source File Name': fname, 'Source File Path': src_dir,
                        'DiskLabel': 'N/A', 'TapeID': 'N/A', 'SourceMobID': 'PZ_OVERRIDE', 'TrackID': 'VFX',
                        'Source Clip EditRate': seq_rate,
                        'Timeline Start TC': frames_to_tc(e["ofs"], seq_rate, seq_drop),
                        'Source Clip start time code': 'N/A', 'Source Clip offset': 'N/A',
                        'StartTime': 'N/A', 'End Time': 'N/A', 'Event Length': e["len"],
                        'Source Clip start (frames)': 0, 'Source Clip offset (frames)': 0, 'StartTime (frames)': 0,
                        'Effect Name': fx_name, 'Keyframe Details': kf, 'Orig Source Clip length': e["len"]
                    })
                else:
                    # Generic FX on filler → placeholder
                    placeholder = sanitize_placeholder_name(fx_name if fx_name != "N/A" else "fx")
                    rows.append({
                        'Event': idx, 'Event Name': f"{fx_name} on Filler", 'Clip Name': placeholder,
                        'Source File Name': placeholder, 'Source File Path': os.path.join("placeholders", placeholder),
                        'DiskLabel': 'N/A', 'TapeID': 'N/A', 'SourceMobID': 'FX_ON_FILLER', 'TrackID': 'VFX',
                        'Source Clip EditRate': seq_rate,
                        'Timeline Start TC': frames_to_tc(e["ofs"], seq_rate, seq_drop),
                        'Source Clip start time code': '01:00:00:00', 'Source Clip offset': '00:00:00:00',
                        'StartTime': '01:00:00:00', 'End Time': '01:00:00:00', 'Event Length': e["len"],
                        'Source Clip start (frames)': 0, 'Source Clip offset (frames)': 0, 'StartTime (frames)': 0,
                        'Effect Name': fx_name, 'Keyframe Details': kf, 'Orig Source Clip length': e["len"]
                    })
                continue  # next event

            # Genuine clip case
            fx = fx_by_ofs.get(e["ofs"])
            fx_name = fx["effect_name"] if fx else "N/A"
            kf = build_keyframe_details(fx or {}, e["ofs"], seq_rate, seq_drop)

            end_mob, url, hops, chain, last_edge_len = resolve_chain_with_edges(
                mob_map, e["mobid"], e["slotid"]
            )

            # Source timecode: first non-zero along chain (end→start)
            s_start, s_rate, s_drop = source_tc_from_chain(chain, default_fps=seq_rate)
            src_off = safe_int(e["src_off"], 0)
            start_frames = s_start + src_off
            end_frames = start_frames + safe_int(e["len"], 0)

            # Metadata (DiskLabel/TapeID)
            md = deep_metadata_from_chain(chain)

            # Path
            src_fname, src_dir = decode_url_to_path(url or "")
            # Fallback name from end mob if no URL
            if not url and end_mob is not None and not src_fname or src_fname == "N/A":
                src_fname = getattr(end_mob, "name", "N/A") or "N/A"

            rows.append({
                'Event': idx,
                'Event Name': src_fname,
                'Clip Name': src_fname,
                'Source File Name': src_fname,
                'Source File Path': src_dir,
                'DiskLabel': md.get("DiskLabel") or '',
                'TapeID': md.get("TapeID") or '',
                'SourceMobID': e["mobid"],
                'TrackID': e.get("track_id", "N/A"),
                'Source Clip EditRate': s_rate or seq_rate,
                'Timeline Start TC': frames_to_tc(e["ofs"], seq_rate, seq_drop),
                'Source Clip start time code': frames_to_tc(s_start, s_rate or seq_rate, s_drop),
                'Source Clip offset': frames_to_tc(src_off, s_rate or seq_rate, s_drop),
                'StartTime': frames_to_tc(start_frames, s_rate or seq_rate, s_drop),
                'End Time': frames_to_tc(end_frames, s_rate or seq_rate, s_drop),
                'Event Length': safe_int(e["len"], 0),
                'Source Clip start (frames)': s_start,
                'Source Clip offset (frames)': src_off,
                'StartTime (frames)': start_frames,
                'Effect Name': fx_name,
                'Keyframe Details': kf,
                # Correct: edge length that *targets* the end mob (genuine source)
                'Orig Source Clip length': last_edge_len or 0
            })

        # Timeline summary (optional logging)
        total_length = sum(r['Event Length'] for r in rows if isinstance(r, dict))
        summary = {
            "Timeline Name": getattr(comp, "name", "N/A"),
            "Timeline Edit Rate": f"{seq_rate} ({'DF' if seq_drop else 'NDF'})",
            "Timeline Start": frames_to_tc(seq_start, seq_rate, seq_drop),
            "Timeline Length": f"{frames_to_tc(total_length, seq_rate, seq_drop)} ({total_length} frames)",
            "Total number of EDL events found": len([r for r in rows if isinstance(r, dict)]),
            "Total number of unique sources": len({(r['Source File Name'], r['Source File Path'])
                                                  for r in rows
                                                  if r.get('Source File Name') not in ('FX_ON_FILLER', 'PZ_OVERRIDE')})
        }

        # Write CSV
        if out_csv is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = (getattr(comp, "name", "AAF_Sequence") or "AAF_Sequence")
            base = re.sub(r'[\\/*?:"<>|]', "", base)
            out_csv = f"{base}_super_edl_fx_report_v2_{ts}.csv"

        with open(out_csv, "w", newline="", encoding="utf-8") as fcsv:
            w = csv.writer(fcsv)
            # summary block
            w.writerow(["Timeline Summary"])
            for k, v in summary.items():
                w.writerow([k, v])
            w.writerow([])
            # header
            w.writerow(CSV_COLUMNS)
            for r in rows:
                w.writerow([r.get(col, "") for col in CSV_COLUMNS])

        return out_csv, summary, rows


# ----------------
# CLI entry point
# ----------------

def main():
    import argparse
    p = argparse.ArgumentParser(description="AAF → Super EDL + FX (headless)")
    p.add_argument("aaf", help="Path to AAF file")
    p.add_argument("-o", "--out", help="Output CSV path (optional)")
    p.add_argument("--comp", help="Exact composition name to use (optional)")
    args = p.parse_args()

    out_csv, summary, _ = process_aaf(args.aaf, args.out, args.comp)
    print("Wrote:", out_csv)
    print("Summary:")
    for k, v in summary.items():
        print("  ", k, ":", v)

if __name__ == "__main__":
    main()
