#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AAF → Super EDL + FX (pyaaf2)
--------------------------------
Match the proven JSON workflow, but parse the source AAF directly with pyaaf2.

Key rules (locked in):
- Walk the Sequence only to advance timeline time.
- OperationGroup may WRAP a SourceClip; unwrap before classifying.
- FX on filler = OperationGroup with NO nested SourceClip → placeholder PNG.
- For each SourceClip, follow SourceID + SourceMobSlotID upstream until the first
  mob with an ImportDescriptor/Locator (genuine source) and the nearest upstream
  mob that actually has a Timecode track.
- Source Clip start TC = that nearest upstream mob’s Timecode.start (frames),
  NOT a default/master assumption.
- StartTime(frames) = source_tc_start_frames + cumulative source offset frames.
- EndTime(frames) = StartTime + event Length.
- Orig Source Clip length = the SourceClip.length on the hop that points to the
  end/genuine mob (e.g., 3731 for RED case), never the timecode-track length.
- DiskLabel/TapeID: _IMPORTSETTING → TaggedValueAttributeList → _IMPORTDISKLAB
  with MobAttributeList/UserComments fallback.
- Pan & Zoom on a SourceClip → still override with real still path (binary/hidden param).
- Pan & Zoom on filler → still resolve a real still path (no placeholder).
- Other FX on filler → placeholder PNG named from the op/plugin.
- Never replace a real source clip with a placeholder just because it has FX.
- Honor DF/NDF as per upstream TC slot; keep CSV schema stable.

Note: This is a single-file library-style script; you can import and call `run()`
or execute directly to write a CSV.
"""

import os
import re
import csv
import math
import urllib.parse
from datetime import datetime

try:
    import aaf2
except Exception as e:
    raise RuntimeError("pyaaf2 must be installed and importable") from e


# ---------- Utilities ----------

ILLEGAL_FS_CHARS = r'<>:"|?*'
_ILLEGAL_RE = re.compile(r'[<>:"|?*]')

def sanitize_filename(name: str) -> str:
    if not name:
        return "unnamed"
    name = name.strip()
    name = _ILLEGAL_RE.sub("_", name)
    # collapse whitespace
    name = re.sub(r"\s+", " ", name)
    return name or "unnamed"

def url_to_path(u: str) -> str:
    if not u:
        return ""
    # Accept network URLs like "file://10.10.1.195/share/folder/file.ext"
    # Also accept "file:/Volumes/...". Decode % escapes.
    try:
        if u.lower().startswith("file:"):
            parsed = urllib.parse.urlparse(u)
            path = urllib.parse.unquote(parsed.path or "")
            if parsed.netloc and not path.startswith("//"):
                path = f"//{parsed.netloc}{path}"
            # Normalize slashes:
            path = path.replace("\\", "/")
            return path
        # if it's already a plain path
        return urllib.parse.unquote(u).replace("\\", "/")
    except Exception:
        return u.replace("\\", "/")

def frames_to_tc(frames: int, fps: float = 25.0, drop: bool = False) -> str:
    """Simple non-drop/drop display (drop treated as ';' separator)."""
    if frames is None or fps <= 0:
        return "N/A"
    try:
        fps_i = int(round(float(fps)))
        h = frames // (3600 * fps_i)
        m = (frames % (3600 * fps_i)) // (60 * fps_i)
        s = (frames % (60 * fps_i)) // fps_i
        f = frames % fps_i
        sep = ";" if drop else ":"
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}"
    except Exception:
        return "N/A"

def is_nonzero_umid(u) -> bool:
    """Detect a real (non-zero) UMID string."""
    if not u:
        return False
    s = str(u).lower().replace("urn:smpte:umid:", "")
    s = s.replace(".", "").replace("-", "").replace(":", "")
    s = re.sub(r"\s+", "", s)
    # any non-zero hex digit?
    return any(ch != "0" for ch in s)

def op_is_pan_zoom(op_name: str) -> bool:
    if not op_name:
        return False
    t = op_name.lower()
    return ("pan" in t and "zoom" in t) or "avid pan & zoom" in t or "pan & zoom" in t

def op_placeholder_png_name(op_name: str) -> str:
    if not op_name:
        return "FX_Placeholder.png"
    core = sanitize_filename(op_name)
    return f"{core}.png"


# ---------- Effect extraction (OperationGroup) ----------

def effect_name_from_operationgroup(og) -> str:
    """
    Prefer component attributes: _EFFECT_PLUGIN_CLASS / _EFFECT_PLUGIN_NAME.
    Fallback to operation definition.name.
    """
    try:
        # Component attribute list
        if hasattr(og, "component_attributes") and og.component_attributes:
            for a in og.component_attributes:
                try:
                    if a.name == "_EFFECT_PLUGIN_NAME" and a.value:
                        plug_name = str(a.value)
                    else:
                        plug_name = None
                    if a.name == "_EFFECT_PLUGIN_CLASS" and a.value:
                        plug_class = str(a.value)
                    else:
                        plug_class = None
                except Exception:
                    continue
            # second pass to gather both
            plug_name = None
            plug_class = None
            for a in og.component_attributes:
                if a.name == "_EFFECT_PLUGIN_NAME" and a.value:
                    plug_name = str(a.value)
                if a.name == "_EFFECT_PLUGIN_CLASS" and a.value:
                    plug_class = str(a.value)
            if plug_name and plug_class:
                return f"{plug_class} : {plug_name}"
            if plug_name:
                return plug_name
        # Operation def name
        if getattr(og, "operation", None) is not None:
            raw = getattr(og.operation, "name", None) or str(og.operation)
            if raw:
                # normalize
                name = raw.replace("_v2", "").replace("_2", "").replace("_", " ").strip()
                return name
    except Exception:
        pass
    return "Unknown Effect"

def extract_static_and_keyframed_params(og) -> (str, dict):
    """
    Best-effort: list animated params (with control points) and static params.
    """
    static_lines = []
    animated = {}
    try:
        params = getattr(og, "parameters", None)
        if not params:
            return "", {}
        for p in params:
            # Parameter name
            pname = getattr(p, "name", None) or getattr(p, "parameter_definition", None)
            pname = str(pname) if pname is not None else "Param"
            # Animated?
            cplist = getattr(p, "control_points", None)
            if cplist:
                kfs = []
                for cp in cplist:
                    try:
                        t = getattr(cp, "time", None)
                        v = getattr(cp, "value", None)
                        kfs.append({"time": t, "value": v})
                    except Exception:
                        continue
                if kfs:
                    animated[pname] = kfs
                    continue
            # Else, static attempt:
            val = None
            # p.value if present, else parameter_definition.default_value if available
            for key in ("value", "val", "default_value"):
                if hasattr(p, key):
                    try:
                        vv = getattr(p, key)
                        val = vv
                        break
                    except Exception:
                        pass
            if val is not None:
                static_lines.append(f"- Parameter: {pname} -> Value: {val}")
    except Exception:
        pass
    return ("\n".join(static_lines), animated)


def extract_panzoom_path(og) -> str:
    """
    Locate the hidden/binary 'Filepath' parameter and decode to a usable path.
    In many Avid P&Z exports, this is utf-16-le bytes or a string-like blob.
    """
    try:
        params = getattr(og, "parameters", None)
        if not params:
            return ""
        # Heuristics: look for 'Filepath', 'FilePath', 'Path', 'URLString'
        cand_names = {"filepath", "filePath", "Filepath", "FilePath", "path", "Path", "URLString"}
        for p in params:
            pname = str(getattr(p, "name", "") or getattr(p, "parameter_definition", "")).strip()
            if pname in cand_names or pname.lower() in {n.lower() for n in cand_names}:
                # Try p.value (could be bytes or str)
                val = None
                if hasattr(p, "value"):
                    try:
                        val = p.value
                    except Exception:
                        val = None
                # bytes? try utf-16-le then utf-8
                if isinstance(val, (bytes, bytearray)):
                    try:
                        txt = bytes(val).decode("utf-16-le", errors="ignore")
                    except Exception:
                        try:
                            txt = bytes(val).decode("utf-8", errors="ignore")
                        except Exception:
                            txt = ""
                else:
                    txt = str(val) if val is not None else ""
                # Clean and normalize to path-like
                txt = txt.strip().strip("\x00")
                # Many times the value may include NULs and random prefix — find first slash/backslash
                m = re.search(r"[\\/]", txt)
                if m:
                    txt = txt[m.start():]
                return url_to_path(txt)
    except Exception:
        pass
    return ""


# ---------- Traversal helpers ----------

def first_nested_sourceclip(node):
    """Depth-first: return the first nested SourceClip (with any UMID)."""
    if node is None:
        return None
    for attr in ("input_segments", "components", "segments"):
        vec = getattr(node, attr, None)
        if not vec:
            continue
        # Try indexable; otherwise iterable
        try:
            rng = range(len(vec))
            it = (vec.get(i) for i in rng)
        except Exception:
            it = iter(vec)
        for sub in it:
            if sub is None:
                continue
            if sub.__class__.__name__ == "SourceClip":
                return sub
            inner = first_nested_sourceclip(sub)
            if inner is not None:
                return inner
    return None

def has_nested_sourceclip(node) -> bool:
    return first_nested_sourceclip(node) is not None

def mob_timecode_info(mob):
    """Return (has_tc, start_frames, fps, drop)."""
    try:
        for s in mob.slots:
            seg = s.segment
            if seg.__class__.__name__ == "Timecode":
                return True, int(getattr(seg, "start", 0) or 0), getattr(seg, "fps", None), bool(getattr(seg, "drop", False))
    except Exception:
        pass
    return False, None, None, None

def mob_import_url(mob) -> str:
    try:
        desc = getattr(mob, "descriptor", None)
        if not desc:
            return ""
        locs = getattr(desc, "locators", None) or []
        for loc in locs:
            u = getattr(loc, "url", None) or getattr(loc, "path", None)
            if u:
                return url_to_path(str(u))
    except Exception:
        pass
    return ""

def extract_disklabel_tapeid(mob):
    """
    DiskLabel from _IMPORTSETTING → TaggedValueAttributeList → _IMPORTDISKLAB
    plus fallback via MobAttributeList / UserComments.
    TapeID via dedicated attribute or same fallbacks.
    """
    disk = ""
    tape = ""
    # Primary: component_attributes style crawl
    try:
        if hasattr(mob, "component_attributes") and mob.component_attributes:
            for a in mob.component_attributes:
                nm = getattr(a, "name", None)
                val = getattr(a, "value", None)
                if nm in ("DiskLabel", "_IMPORTDISKLAB") and not disk and val:
                    disk = str(val)
                if nm == "TapeID" and not tape and val:
                    tape = str(val)
    except Exception:
        pass
    # Descriptor deep refs (some vendors stash there)
    try:
        desc = getattr(mob, "descriptor", None)
        if desc and hasattr(desc, "component_attributes") and desc.component_attributes:
            for a in desc.component_attributes:
                nm = getattr(a, "name", None)
                val = getattr(a, "value", None)
                if nm in ("DiskLabel", "_IMPORTDISKLAB") and not disk and val:
                    disk = str(val)
                if nm == "TapeID" and not tape and val:
                    tape = str(val)
    except Exception:
        pass
    # Weak fallback: any attribute named like
    try:
        for a in getattr(mob, "component_attributes", []) or []:
            nm = getattr(a, "name", "")
            val = getattr(a, "value", "")
            if not disk and "disk" in nm.lower():
                disk = str(val)
            if not tape and "tape" in nm.lower():
                tape = str(val)
    except Exception:
        pass
    return disk, tape


# ---------- Chain climb for TC + original edge length ----------

def climb_chain_for_tc_and_edge(sc, max_hops=16):
    """
    From a (possibly wrapped) SourceClip:
      - cum_offset = sc.start + each nested SourceClip.start along chain
      - Stop when a mob has a Timecode slot → return its TC start frames, fps, drop
      - Track edge_len as the SourceClip.length at the hop that *points* to the end mob
      - Also return end_mob (genuine source with ImportDescriptor/Locator when found) and its URL.
    """
    cum = int(getattr(sc, "start", 0) or 0)
    edge_len = int(getattr(sc, "length", 0) or 0)
    cur = sc
    end_mob = None
    url_at_end = ""
    tc_frames = None
    fps = None
    drop = False

    hops = 0
    while cur is not None and hops < max_hops:
        hops += 1
        mob = getattr(cur, "mob", None)
        if mob is None:
            break

        # If this mob has Timecode, take it (nearest upstream)
        has_tc, tc, f, d = mob_timecode_info(mob)
        if has_tc and tc_frames is None:
            tc_frames = int(tc or 0)
            fps = f if f else fps
            drop = bool(d)

        # If this mob has ImportDescriptor/Locator, mark as end/genuine
        u = mob_import_url(mob)
        if u and not url_at_end:
            url_at_end = u
            end_mob = mob

        # Follow slot indicated by the current SourceClip to hop up the chain
        slotid = None
        try:
            if "SourceMobSlotID" in cur:
                slotid = cur["SourceMobSlotID"].value
        except Exception:
            slotid = getattr(cur, "slot_id", None)

        # Find the segment in that slot
        seg = None
        try:
            for s in mob.slots:
                if getattr(s, "slot_id", None) == slotid:
                    seg = s.segment
                    break
        except Exception:
            seg = None

        # Find the next genuine SourceClip inside that segment
        nxt = first_nested_sourceclip(seg) if seg is not None else None
        if nxt is None:
            # Nowhere else to climb
            break

        # Update cumulative offset & "edge" length at the hop that points to the next mob
        cum += int(getattr(nxt, "start", 0) or 0)
        edge_len = int(getattr(nxt, "length", 0) or 0)
        cur = nxt

    return {
        "nearest_tc_frames": tc_frames,
        "cum_offset_frames": cum,
        "edge_len_at_end": edge_len,
        "end_mob": end_mob,
        "end_url": url_at_end,
        "tc_fps": fps,
        "tc_drop": drop,
    }


# ---------- Event builder ----------

def choose_main_sequence(aaf_file):
    """
    Heuristic: pick the Composition with the largest Sequence (by component count).
    Returns (sequence_segment, composition_mob, slot_edit_rate_fps).
    """
    best_seq = None
    best_comp_mob = None
    best_count = -1
    best_rate = 25.0
    for mob in aaf_file.content.mobs:
        try:
            for slot in mob.slots:
                seg = slot.segment
                if seg.__class__.__name__ != "Sequence":
                    continue
                comps = getattr(seg, "components", None)
                cnt = len(comps) if comps else 0
                if cnt > best_count:
                    best_count = cnt
                    best_seq = seg
                    best_comp_mob = mob
                    # try slot edit rate if present (num/den)
                    rate = getattr(slot, "edit_rate", None)
                    if rate and hasattr(rate, "numerator") and hasattr(rate, "denominator") and rate.denominator:
                        best_rate = float(rate.numerator) / float(rate.denominator)
        except Exception:
            continue
    return best_seq, best_comp_mob, best_rate

def collect_events_from_sequence(seq):
    """
    Walk the sequence; build raw events list:
      - ("SOURCE", t_off, length, sc, None) for plain SourceClip
      - ("SOURCE_WRAPPED", t_off, length, sc_nested, og) when OperationGroup wraps a SourceClip
      - ("FX_ON_FILLER", t_off, length, og, None) for OperationGroup with NO SourceClip
    """
    events = []

    def walk(node, t_off):
        cname = node.__class__.__name__
        length = int(getattr(node, "length", 0) or 0)

        if cname == "Sequence":
            comps = getattr(node, "components", None)
            if comps:
                try:
                    for i in range(len(comps)):
                        c = comps.get(i)
                        walk(c, t_off)
                        t_off += int(getattr(c, "length", 0) or 0)
                except Exception:
                    for c in comps:
                        walk(c, t_off)
                        t_off += int(getattr(c, "length", 0) or 0)
            return

        if cname == "OperationGroup":
            sc_nested = first_nested_sourceclip(node)
            if sc_nested is not None:
                events.append(("SOURCE_WRAPPED", t_off, length, sc_nested, node))
            else:
                events.append(("FX_ON_FILLER", t_off, length, node, None))
            # Always dive into OG's internals (so deeper nested stuff is not missed)
            for attr in ("input_segments", "components", "segments"):
                vec = getattr(node, attr, None)
                if vec:
                    try:
                        for i in range(len(vec)):
                            sub = vec.get(i)
                            if sub:
                                walk(sub, t_off)
                    except Exception:
                        for sub in vec:
                            if sub:
                                walk(sub, t_off)
            return

        if cname == "SourceClip":
            events.append(("SOURCE", t_off, length, node, None))
            return

        # default: try to recurse through typical vector fields
        for attr in ("components", "segments", "input_segments"):
            vec = getattr(node, attr, None)
            if vec:
                try:
                    for i in range(len(vec)):
                        sub = vec.get(i)
                        if sub:
                            walk(sub, t_off)
                except Exception:
                    for sub in vec:
                        if sub:
                            walk(sub, t_off)

    walk(seq, 0)
    return events


# ---------- CSV writing (schema following your v2) ----------

CSV_HEADER = [
    "Event",
    "Event Name",
    "Clip Name",
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
    "EndTime (frames)",
    "Length (frames)",
    "Orig Source Clip length",
    "DropFrame?",
    "Effect Name",
    "Keyframe Details",
]

def build_csv_rows(aaf_path: str):
    rows = []
    with aaf2.open(aaf_path, 'r') as f:
        seq, comp_mob, timeline_fps = choose_main_sequence(f)
        if not seq:
            raise RuntimeError("No Sequence found in AAF.")

        # Composition timecode for Timeline Start TC (if any)
        comp_has_tc, comp_tc_start, _, comp_drop = mob_timecode_info(comp_mob)
        timeline_start_tc = frames_to_tc(comp_tc_start or 0, timeline_fps, comp_drop) if comp_mob else "N/A"

        # Traverse & collect events
        events = collect_events_from_sequence(seq)

        # Build output rows
        event_idx = 0
        for kind, t_off, ln, sc_or_og, og_or_none in events:
            event_idx += 1
            length_frames = int(ln or 0)
            timeline_start_tc_this = frames_to_tc((comp_tc_start or 0) + t_off, timeline_fps, comp_drop)

            # Defaults per row
            clip_name = ""
            file_name = ""
            file_path = ""
            disklabel = ""
            tapeid = ""
            src_mob_id = ""
            track_id = "V1"  # generic; AAF may expose slot_id but v2 CSV used fixed?
            src_edit_rate = timeline_fps
            src_clip_tc_str = "N/A"
            start_frames = ""
            end_frames = ""
            drop_str = ""
            effect_name = "N/A"
            keyframe_details = ""

            if kind in ("SOURCE", "SOURCE_WRAPPED"):
                sc = sc_or_og
                og = og_or_none
                mob = getattr(sc, "mob", None)
                if mob and getattr(mob, "mob_id", None):
                    src_mob_id = str(mob.mob_id)

                # effect name (attached at same t_off when wrapped)
                if og is not None:
                    effect_name = effect_name_from_operationgroup(og)
                    # Collect keyframes/static params (for reporting)
                    static_params, animated = extract_static_and_keyframed_params(og)
                    if animated:
                        lines = ["--- Animated Parameters ---"]
                        for pname, pts in animated.items():
                            lines.append(f"  - Parameter: {pname} ({len(pts)} keyframes)")
                            for kp in pts:
                                # We do not translate 'time' to absolute frames here (OG length != clip length always)
                                lines.append(f"    Keyframe at Time: {kp.get('time')} -> Value: {kp.get('value')}")
                    else:
                        lines = []
                    if static_params:
                        if lines:
                            lines.append("")
                            lines.append("--- Static Parameters ---")
                        else:
                            lines.append("--- Static Parameters ---")
                        lines.extend(static_params.splitlines())
                    keyframe_details = "\n".join(lines) if lines else ""

                # Chain climb to nearest TC, end mob, and orig edge length
                chain = climb_chain_for_tc_and_edge(sc)
                tc_frames = chain["nearest_tc_frames"]
                cum_offset = chain["cum_offset_frames"]
                edge_len = chain["edge_len_at_end"]
                end_mob = chain["end_mob"]
                end_url = chain["end_url"]
                fps = chain["tc_fps"] or timeline_fps
                drop = chain["tc_drop"]

                # Disk/Tape from end mob (if any)
                if end_mob:
                    dsk, tap = extract_disklabel_tapeid(end_mob)
                    disklabel = dsk or disklabel
                    tapeid = tap or tapeid

                # Names / paths
                file_path = end_url or file_path
                file_path = url_to_path(file_path)
                file_name = os.path.basename(file_path) if file_path else (getattr(mob, "name", "") or "")
                clip_name = getattr(mob, "name", "") or file_name

                # Start/End frames & TC string
                if tc_frames is not None:
                    start_frames_int = int(tc_frames) + int(cum_offset or 0)
                    end_frames_int = start_frames_int + int(length_frames or 0)
                    start_frames = str(start_frames_int)
                    end_frames = str(end_frames_int)
                    src_clip_tc_str = frames_to_tc(start_frames_int, fps, drop)
                    drop_str = "DF" if drop else "NDF"
                else:
                    # No TC anywhere upstream – report frames only
                    start_frames_int = int(cum_offset or 0)
                    end_frames_int = start_frames_int + int(length_frames or 0)
                    start_frames = str(start_frames_int)
                    end_frames = str(end_frames_int)
                    src_clip_tc_str = "N/A"
                    drop_str = ""

                # P&Z special rules (on a source clip): resolve still path but DO NOT replace the clip itself
                if og is not None and op_is_pan_zoom(effect_name):
                    pz_path = extract_panzoom_path(og)
                    if pz_path:
                        # In your v2 CSV you kept the video clip but annotated the P&Z still via name/path fields.
                        # Here, we put the still file name/path in the "Clip Name" / "Source File Name/Path" for clarity.
                        fname = sanitize_filename(os.path.basename(pz_path))
                        clip_name = f"(P&Z Override) {fname}"
                        file_name = fname
                        file_path = os.path.dirname(pz_path).replace("\\", "/")

                # Row
                rows.append([
                    event_idx,
                    clip_name,
                    clip_name,
                    file_name,
                    file_path,
                    disklabel,
                    tapeid,
                    src_mob_id,
                    track_id,
                    f"{fps:.3f}",
                    timeline_start_tc_this,
                    src_clip_tc_str,
                    start_frames,
                    end_frames,
                    length_frames,
                    edge_len,
                    drop_str,
                    effect_name,
                    keyframe_details,
                ])

            elif kind == "FX_ON_FILLER":
                og = sc_or_og
                effect_name = effect_name_from_operationgroup(og)
                static_params, animated = extract_static_and_keyframed_params(og)
                lines = []
                if animated:
                    lines.append("--- Animated Parameters ---")
                    for pname, pts in animated.items():
                        lines.append(f"  - Parameter: {pname} ({len(pts)} keyframes)")
                        for kp in pts:
                            lines.append(f"    Keyframe at Time: {kp.get('time')} -> Value: {kp.get('value')}")
                if static_params:
                    if lines:
                        lines.append("")
                        lines.append("--- Static Parameters ---")
                    else:
                        lines.append("--- Static Parameters ---")
                    lines.extend(static_params.splitlines())
                keyframe_details = "\n".join(lines) if lines else ""

                # Pan & Zoom on filler still resolves a real file path (no placeholder)
                if op_is_pan_zoom(effect_name):
                    pz_path = extract_panzoom_path(og)
                    if pz_path:
                        fname = sanitize_filename(os.path.basename(pz_path))
                        clip_name = f"(P&Z Still) {fname}"
                        file_name = fname
                        file_path = os.path.dirname(pz_path).replace("\\", "/")
                    else:
                        # Fallback if the binary path isn't reachable in this AAF
                        clip_name = "(P&Z Still)"
                        file_name = ""
                        file_path = ""
                else:
                    # Other FX on filler → placeholder PNG named from the op/plugin
                    placeholder = op_placeholder_png_name(effect_name)
                    clip_name = f"(FX on Filler) {placeholder}"
                    file_name = placeholder
                    file_path = ""

                # Row (limited source data by definition)
                rows.append([
                    event_idx,
                    clip_name,
                    clip_name,
                    file_name,
                    file_path,
                    "",     # DiskLabel
                    "",     # TapeID
                    "FX_ON_FILLER",
                    "VFX",
                    f"{timeline_fps:.3f}",
                    timeline_start_tc_this,
                    "N/A",                   # Source Clip start time code
                    str(t_off),              # StartTime (frames) – timeline frames from comp start
                    str(t_off + length_frames),
                    length_frames,
                    "",                      # Orig Source Clip length
                    "N/A",
                    effect_name,
                    keyframe_details,
                ])

        return rows, {
            "Timeline Name": getattr(comp_mob, "name", "N/A"),
            "Timeline Edit Rate": f"{timeline_fps:.3f} {'(DF)' if (comp_drop or False) else '(NDF)'}",
            "Timeline Start": timeline_start_tc,
            "Total number of EDL events found": len(rows),
        }

def write_csv(out_path: str, rows, summary: dict):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # Summary block (two-column)
        for k, v in summary.items():
            w.writerow([k, v])
        w.writerow([])  # spacer
        # Header + rows
        w.writerow(CSV_HEADER)
        for r in rows:
            w.writerow(r)

def run(aaf_path: str, out_csv_path: str = None):
    rows, summary = build_csv_rows(aaf_path)
    if not out_csv_path:
        base = os.path.splitext(os.path.basename(aaf_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv_path = f"{base}.super_edl_fx_report_v2_{timestamp}.csv"
    write_csv(out_csv_path, rows, summary)
    return out_csv_path


# ---------- CLI ----------

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="AAF → Super EDL + FX (pyaaf2)")
    ap.add_argument("aaf", help="Path to source AAF")
    ap.add_argument("-o", "--out", help="Output CSV path (optional)")
    args = ap.parse_args()
    out_path = run(args.aaf, args.out)
    print(f"✅ Wrote: {out_path}")
