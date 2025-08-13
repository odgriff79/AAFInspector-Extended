#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV → FCPXML (REL13) + Resolve Marker EDL (REL15) — single, unified build

Key points:
- Single pass over the CSV builds a canonical in-memory model (events, timing, assets, keyframes).
- FCPXML writer uses that model to emit the timeline (REL13 timing and in-point mapping).
- Marker EDL writer reuses the very same computed values (no re-calculation) so markers
  display *exactly* what the XML uses (plus AVID raw values for comparison).

REL13 timing mapping (unchanged, locked):
- sequence.tcStart          ← CSV Summary "Timeline Start"
- sequence.duration         ← CSV Summary "Timeline Length (frames)"
- head <gap>:
    offset & start          ← sequence.tcStart
    duration                ← (first event abs start) - sequence.tcStart
- each <video> (no <clip> wrapper):
    offset                  ← row["Timeline Start TC"]   (absolute timeline time)
    start                   ← row["Source Clip offset (frames)"]
                               fallback: StartTime(frames) - Source Clip start (frames)
    duration                ← row["Event Length"]
- each <asset>:
    start                   ← row["Source Clip start time code"]
    duration                ← row["Orig Source Clip length"]
- All time rationals use denominator = fps (e.g., /25s)
- Filler rows with no effect are skipped.

REL15 markers:
- One 1-frame EDL event per CSV row at its Timeline Start TC.
- Per keyframe *time*, show:
    AVID raw pairs (pos=(x,y), scale=(sx,sy), rotation, opacity)
    FCPXML values (the same converted decimals / pairs used in XML), with relative KF time.
- FCM derived from CSV summary (NDF/DF). DF TC strings use ';'; includes proper 29.97/59.94 DF math.

Usage:
  python csv_to_fcpxml_and_markers_REL13_REL15.py -i mem1.csv -x out.fcpxml -e out_markers.edl
  # Optional: limit keyframes per event in marker EDL for readability
  python csv_to_fcpxml_and_markers_REL13_REL15.py -i mem1.csv -x out.fcpxml -e out_markers.edl --max-kf 8
"""

from __future__ import annotations
import argparse
import csv
import io
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional


# ---------- Basic utilities ----------

def sanitize(s: Any) -> str:
    if s is None:
        return ""
    return str(s).replace("\x00", "").encode("ascii", "ignore").decode("utf-8")


def tc_to_frames(tc: str, fps: float) -> int:
    """SMPTE -> frames. Treat ';' as ':' for parsing."""
    if not tc or ":" not in tc:
        return 0
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return int(round((h * 3600 + m * 60 + s) * fps + f))


def frames_to_den(frames: int, fps: float) -> Tuple[int, int]:
    """Represent 'frames' at rate 'fps' as N/D seconds with D=fps (1 frame = 1/D s)."""
    den = int(round(fps))
    num = int(frames)
    return num, den


# ---------- DF/NDF timecode rendering ----------

def frames_to_tc_ndf(frames: int, fps: float) -> str:
    f = int(frames % fps)
    sec_total = int(frames // fps)
    s = sec_total % 60
    m = (sec_total // 60) % 60
    h = sec_total // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def frames_to_tc_df(frames: int, fps: float) -> str:
    """
    Proper drop-frame accounting for 29.97 and 59.94.
    For other rates, emit NDF layout with ';' separators.
    """
    if abs(fps - 29.97) < 0.01:
        # SMPTE DF (29.97): drop 2 frames every minute except every 10th minute.
        frames = int(round(frames))
        d = 17982  # frames per 10 minutes @29.97
        m = 1798   # frames per minute (except each 10th)
        ten_min_chunks = frames // d
        rem = frames % d
        drop = 18 * ten_min_chunks + (2 * ((rem - 2) // m) if rem >= 2 else 0)
        total = frames + drop
        s = frames_to_tc_ndf(total, 30.0).replace(":", ";")
        return s
    if abs(fps - 59.94) < 0.01:
        half = frames // 2
        tc30 = frames_to_tc_df(half, 29.97)
        h, m, s, f = [int(x) for x in tc30.replace(";", ":").split(":")]
        f = (f * 2) + (frames % 2)
        return f"{h:02d};{m:02d};{s:02d};{f:02d}"
    # Fallback: NDF math with semicolons
    return frames_to_tc_ndf(frames, fps).replace(":", ";")


def frames_to_tc(frames: int, fps: float, drop_frame: bool) -> str:
    return frames_to_tc_df(frames, fps) if drop_frame else frames_to_tc_ndf(frames, fps)


# ---------- CSV parsing ----------

def read_csv_summary_and_events(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(True)
    sep_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == "":
            sep_idx = i
            break
    if sep_idx is None:
        raise ValueError("CSV missing blank line separator between summary and table.")
    summary = [ln.strip() for ln in lines[:sep_idx] if ln.strip()]
    events = list(csv.DictReader(io.StringIO("".join(lines[sep_idx + 1:]))))
    return summary, events


def parse_summary(summary_lines: List[str]) -> Tuple[str, float, str, str, int]:
    """
    Returns (timeline_name, fps, tc_format, start_tc, length_frames)
    Expects lines like:
      Timeline Name,My Timeline
      Timeline Edit Rate,25.0 (NDF)
      Timeline Start,10:04:17:09
      Timeline Length,00:05:34:16 (8366 frames)
    """
    name, fps, tc_format, start_tc, length_frames = "Timeline", 25.0, "NDF", "00:00:00:00", 0
    for ln in summary_lines:
        if ln.startswith("Timeline Name,"):
            name = sanitize(ln.split(",", 1)[1].strip())
        elif ln.startswith("Timeline Edit Rate,"):
            rhs = ln.split(",", 1)[1].strip()
            parts = rhs.split()
            try:
                fps = float(parts[0])
            except Exception:
                fps = 25.0
            if len(parts) > 1:
                tc_format = parts[1].strip("()").upper()
        elif ln.startswith("Timeline Start,"):
            start_tc = ln.split(",", 1)[1].strip()
        elif ln.startswith("Timeline Length,"):
            m = re.search(r"\((\d+)\s*frames\)", ln)
            if m:
                length_frames = int(m.group(1))
    return name, fps, tc_format, start_tc, length_frames


# ---------- Effect parameter detection & conversion ----------

SCALE_NAMES = ("scale", "zoom", "size", "resize", "pan & zoom: zoom")
POS_NAMES   = ("position", "center", "pan & zoom: center", "pan & zoom: position")
ROT_NAMES   = ("rotation", "rotate", "angle")

def name_is(pname: str, group: str, axis: Optional[str] = None) -> bool:
    n = str(pname or "").lower()
    if group == "scale":
        if any(k in n for k in SCALE_NAMES):
            if axis == "x": return (" x" in n) or n.endswith("x") or "_x" in n
            if axis == "y": return (" y" in n) or n.endswith("y") or "_y" in n
            return True
    if group == "pos":
        if any(k in n for k in POS_NAMES):
            if axis == "x": return (" x" in n) or n.endswith("x") or "_x" in n or "center x" in n
            if axis == "y": return (" y" in n) or n.endswith("y") or "_y" in n or "center y" in n
            return True
    if group == "rot":
        return any(k in n for k in ROT_NAMES)
    if group == "opacity":
        return ("opacity" in n) or (n.strip() == "amount") or ("blend" in n)
    return False


def to_float_safe(v: Any) -> Optional[float]:
    try:
        s = str(v).strip()
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / float(b)
        return float(s)
    except Exception:
        return None


def normalize_scale(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return v / 100.0 if v > 3.0 else v


def convert_for_fcpxml(param_name: str, raw_value: Any) -> Optional[float]:
    n = str(param_name or "").lower()
    v = to_float_safe(raw_value)
    if v is None:
        return None
    if ("opacity" in n) or (n.strip() == "amount") or ("blend" in n):
        return (v / 100.0 if v > 1.0 else v)
    if ("scale" in n) or ("zoom" in n) or ("size" in n) or ("resize" in n) or ("pan & zoom: zoom" in n):
        return normalize_scale(v)
    return v  # position/rotation passthrough


# ---------- Keyframe parsing ----------

def parse_keyframes_block(kf: str) -> Dict[str, List[Tuple[int, str]]]:
    """
    Parse the 'Keyframe Details' blob (SuperEDL) into:
      { parameter_name: [(frame_abs, value_str), ...], ... }, animated only.
    """
    if not isinstance(kf, str):
        kf = ""
    animated: Dict[str, List[Tuple[int, str]]] = {}
    in_anim = False
    cur = None
    for line in kf.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("--- Animated Parameters ---"):
            in_anim, cur = True, None
            continue
        if line.startswith("--- Static Parameters ---"):
            in_anim, cur = False, None
            continue
        m = re.search(r"^-+\s*Parameter:\s*(.*?)\s*(?:\(|->|$)", line)
        if m:
            cur = m.group(1).strip()
            if in_anim:
                animated.setdefault(cur, [])
            continue
        m = re.search(r"\((\d+)f\)\s*->\s*Value:\s*(.*)$", line)
        if in_anim and cur and m:
            animated[cur].append((int(m.group(1)), m.group(2).strip()))
    for k in animated:
        animated[k].sort(key=lambda t: t[0])
    return animated


def group_raw_kf_by_time(animated: Dict[str, List[Tuple[int, str]]]) -> "OrderedDict[int, Dict[str, Any]]":
    """
    Ordered mapping of absolute-frame times -> raw AVID values per component:
      {'posx','posy','scalex','scaley','rot','op'}
    Scalar 'scale' is mirrored to both X and Y.
    """
    times = set()
    for pname, pts in animated.items():
        for t, _ in pts:
            times.add(t)
    times = sorted(times)
    grouped: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
    for t in times:
        grouped[t] = {'posx': None, 'posy': None, 'scalex': None, 'scaley': None, 'rot': None, 'op': None}
    for pname, pts in animated.items():
        for t, val in pts:
            g = grouped.get(t)
            if g is None:
                continue
            if name_is(pname, "pos", "x"): g['posx'] = val
            elif name_is(pname, "pos", "y"): g['posy'] = val
            elif name_is(pname, "scale", "x"): g['scalex'] = val
            elif name_is(pname, "scale", "y"): g['scaley'] = val
            elif name_is(pname, "scale"):
                g['scalex'] = val
                g['scaley'] = val
            elif name_is(pname, "rot"): g['rot'] = val
            elif name_is(pname, "opacity"): g['op'] = val
    return grouped


# ---------- Canonical event model (single computation) ----------

class EventModel:
    def __init__(self):
        self.clip_name: str = "Untitled"
        self.source_path: str = ""
        self.effect_name: str = ""
        # timing
        self.evt_abs: int = 0          # absolute timeline start (frames)
        self.evt_len: int = 0          # duration in frames
        self.src_off: int = 0          # in-point in source (frames) — used as <video start>
        self.asset_src_abs: int = 0    # asset start TC in frames (source clip start tc)
        self.asset_src_len: int = 0    # asset duration (orig source clip length)
        # keyframes
        self.kf_raw_grouped: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()  # absf -> raw per component
        # converted tracks for XML (times are LOCAL frames; values are decimals as strings)
        self.tr_position: List[Tuple[int, str]] = []  # (local_frames, "x y")
        self.tr_scale:    List[Tuple[int, str]] = []  # (local_frames, "sx sy")
        self.tr_rotation: List[Tuple[int, str]] = []  # (local_frames, "r")
        self.tr_opacity:  List[Tuple[int, str]] = []  # (local_frames, "a")


def build_model_from_csv(csv_path: Path) -> Tuple[Dict[str, Any], List[EventModel]]:
    # Parse CSV
    summary_lines, events_csv = read_csv_summary_and_events(csv_path)
    name, fps, tc_format, seq_start_tc, seq_length_frames = parse_summary(summary_lines)
    drop_frame = (tc_format == "DF")
    seq_start_frames = tc_to_frames(seq_start_tc, fps)

    # Filter/sort rows
    def is_filler_to_skip(row: Dict[str, str]) -> bool:
        nm = str(row.get("Event Name", "") or "")
        mob = str(row.get("SourceMobID", "") or "")
        clip = str(row.get("Clip Name", "") or "")
        eff = str(row.get("Effect Name", "") or "").upper()
        filler = ("Filler" in nm) or ("FX_ON_FILLER" in mob) or ("placeholder" in clip)
        return filler and (eff in ("", "N/A", "NONE", "N A"))

    rows = [r for r in events_csv if not is_filler_to_skip(r)]
    rows.sort(key=lambda r: tc_to_frames(r.get("Timeline Start TC", "00:00:00:00"), fps))

    # Head gap
    first_abs = tc_to_frames(rows[0].get("Timeline Start TC", "00:00:00:00"), fps) if rows else seq_start_frames
    head_gap_frames = max(0, first_abs - seq_start_frames)

    # Build event models
    models: List[EventModel] = []

    for r in rows:
        M = EventModel()
        M.clip_name = sanitize(r.get("Clip Name", "Untitled"))
        M.source_path = sanitize(r.get("Source File Path", "")).strip()
        M.effect_name = sanitize(r.get("Effect Name", "N/A"))

        M.evt_abs = tc_to_frames(sanitize(r.get("Timeline Start TC", "00:00:00:00")), fps)
        try:
            M.evt_len = int(str(r.get("Event Length", "0")).split(".")[0] or "0")
        except Exception:
            M.evt_len = 0

        # Correct in-point for <video start>: Source Clip offset (frames); fallback
        try:
            M.src_off = int(str(r.get("Source Clip offset (frames)", "0")).split(".")[0] or "0")
        except Exception:
            st_frames = int(str(r.get("StartTime (frames)", "0")).split(".")[0] or "0")
            src_start_frames = tc_to_frames(sanitize(r.get("Source Clip start time code", "00:00:00:00")), fps)
            M.src_off = max(0, st_frames - src_start_frames)

        M.asset_src_abs = tc_to_frames(sanitize(r.get("Source Clip start time code", "00:00:00:00")), fps)
        try:
            M.asset_src_len = int(str(r.get("Orig Source Clip length", "0")).split(".")[0] or "0")
        except Exception:
            M.asset_src_len = 0

        # Parse keyframes and group raw values by absolute time
        kf_blob = r.get("Keyframe Details", "") or ""
        animated = parse_keyframes_block(kf_blob)
        M.kf_raw_grouped = group_raw_kf_by_time(animated)

        # Build converted tracks (LOCAL times, decimals) used by XML *and* EDL
        # We clamp local times into [0, evt_len]
        last_sx, last_sy = 1.0, 1.0
        last_px, last_py = 0.0, 0.0
        last_rot = 0.0

        for absf, comp in M.kf_raw_grouped.items():
            local = absf - M.evt_abs
            if local < 0: local = 0
            if local > M.evt_len: local = M.evt_len

            # position
            px = convert_for_fcpxml("position x", comp['posx']) if comp['posx'] is not None else None
            py = convert_for_fcpxml("position y", comp['posy']) if comp['posy'] is not None else None
            if px is None: px = last_px
            if py is None: py = last_py
            M.tr_position.append((local, f"{px:g} {py:g}"))
            last_px, last_py = px, py

            # scale (scalar mirrored)
            sx = convert_for_fcpxml("scale x", comp['scalex']) if comp['scalex'] is not None else None
            sy = convert_for_fcpxml("scale y", comp['scaley']) if comp['scaley'] is not None else None
            if sx is None and sy is None and comp['scalex'] is not None:
                sv = convert_for_fcpxml("scale", comp['scalex'])
                sx, sy = sv, sv
            if sx is None: sx = last_sx
            if sy is None: sy = last_sy
            M.tr_scale.append((local, f"{sx:g} {sy:g}"))
            last_sx, last_sy = sx, sy

            # rotation
            rv = convert_for_fcpxml("rotation", comp['rot']) if comp['rot'] is not None else last_rot
            M.tr_rotation.append((local, f"{rv:g}"))
            last_rot = rv

            # opacity (optional)
            if comp['op'] is not None:
                ov = convert_for_fcpxml("opacity", comp['op'])
                if ov is not None:
                    M.tr_opacity.append((local, f"{ov:g}"))

        models.append(M)

    header = {
        "name": name,
        "fps": fps,
        "tc_format": tc_format,
        "drop_frame": drop_frame,
        "seq_start_tc": seq_start_tc,
        "seq_start_frames": seq_start_frames,
        "seq_length_frames": seq_length_frames,
        "head_gap_frames": head_gap_frames,
    }
    return header, models


# ---------- Writers (use the single model) ----------

def write_fcpxml_from_model(header: Dict[str, Any], models: List[EventModel], out_path: Path) -> None:
    fps = header["fps"]
    tc_format = header["tc_format"]
    seq_start_frames = header["seq_start_frames"]
    seq_length_frames = header["seq_length_frames"]
    head_gap_frames = header["head_gap_frames"]
    name = header["name"]

    L: List[str] = []
    L.append('<?xml version="1.0" encoding="UTF-8"?>')
    L.append('<!DOCTYPE fcpxml>')
    L.append('<fcpxml version="1.13">')
    L.append('    <resources>')
    L.append(f'        <format name="FFVideoFormat1080p{int(round(fps))}" frameDuration="1/{int(round(fps))}s" id="r0" height="1080" width="1920"/>')

    # Assets
    rid = 2
    asset_id_by_index: Dict[int, str] = {}
    for i, M in enumerate(models):
        if not M.source_path:
            continue
        n1, d1 = frames_to_den(M.asset_src_abs, fps)
        n2, d2 = frames_to_den(M.asset_src_len, fps)
        r = f"r{rid}"; rid += 1
        asset_id_by_index[i] = r
        url = "file://localhost" + M.source_path.replace("\\", "/")
        L.append(f'        <asset hasVideo="1" name="{M.clip_name}" format="r0" start="{n1}/{d1}s" duration="{n2}/{d2}s" id="{r}">')
        L.append(f'            <media-rep kind="original-media" src="{url}"/>')
        L.append('        </asset>')

    L.append('    </resources>')
    L.append('    <library>')
    L.append(f'        <event name="{sanitize(name)}">')
    L.append(f'            <project name="{sanitize(name)}">')

    nseq, dseq = frames_to_den(seq_start_frames, fps)
    nlen, dlen = frames_to_den(seq_length_frames, fps)
    L.append(f'                <sequence tcStart="{nseq}/{dseq}s" format="r0" tcFormat="{tc_format}" duration="{nlen}/{dlen}s">')
    L.append('                    <spine>')

    if head_gap_frames > 0:
        ng, dg = frames_to_den(head_gap_frames, fps)
        L.append(f'                        <gap name="Gap" offset="{nseq}/{dseq}s" start="{nseq}/{dseq}s" duration="{ng}/{dg}s"/>')

    for i, M in enumerate(models):
        asset_id = asset_id_by_index.get(i)
        if not asset_id:
            continue

        off_n, off_d = frames_to_den(M.evt_abs, fps)
        dur_n, dur_d = frames_to_den(M.evt_len, fps)
        st_n, st_d = frames_to_den(M.src_off, fps)

        L.append(f'                        <video name="{M.clip_name}" offset="{off_n}/{off_d}s" start="{st_n}/{st_d}s" duration="{dur_n}/{dur_d}s" enabled="1" ref="{asset_id}">')

        # Transform (position/scale/rotation) with keyframes from the *computed* tracks
        base_pos = M.tr_position[0][1] if M.tr_position else "0 0"
        base_scl = M.tr_scale[0][1] if M.tr_scale else "1 1"
        L.append(f'                            <adjust-transform scale="{base_scl}" position="{base_pos}" anchor="0 0">')

        if M.tr_scale:
            v0 = M.tr_scale[0][1]
            L.append(f'                                <param name="scale" value="{v0}">')
            L.append('                                    <keyframeAnimation>')
            for local, val in M.tr_scale:
                n, d = frames_to_den(local, fps)
                L.append(f'                                        <keyframe time="{n}/{d}s" value="{val}"/>')
            L.append('                                    </keyframeAnimation>')
            L.append('                                </param>')

        if M.tr_position:
            v0 = M.tr_position[0][1]
            L.append(f'                                <param name="position" value="{v0}">')
            L.append('                                    <keyframeAnimation>')
            for local, val in M.tr_position:
                n, d = frames_to_den(local, fps)
                L.append(f'                                        <keyframe time="{n}/{d}s" value="{val}"/>')
            L.append('                                    </keyframeAnimation>')
            L.append('                                </param>')

        if M.tr_rotation:
            v0 = M.tr_rotation[0][1]
            L.append(f'                                <param name="rotation" value="{v0}">')
            L.append('                                    <keyframeAnimation>')
            for local, val in M.tr_rotation:
                n, d = frames_to_den(local, fps)
                L.append(f'                                        <keyframe time="{n}/{d}s" value="{val}"/>')
            L.append('                                    </keyframeAnimation>')
            L.append('                                </param>')

        L.append('                            </adjust-transform>')

        if M.tr_opacity:
            base = M.tr_opacity[0][1]
            L.append(f'                            <adjust-blend amount="{base}">')
            L.append(f'                                <param name="amount" value="{base}">')
            L.append('                                    <keyframeAnimation>')
            for local, val in M.tr_opacity:
                n, d = frames_to_den(local, fps)
                L.append(f'                                        <keyframe time="{n}/{d}s" value="{val}"/>')
            L.append('                                    </keyframeAnimation>')
            L.append('                                </param>')
            L.append('                            </adjust-blend>')
        else:
            L.append('                            <adjust-blend amount="1"/>')

        L.append('                        </video>')

    L.append('                    </spine>')
    L.append('                </sequence>')
    L.append('            </project>')
    L.append('        </event>')
    L.append('    </library>')
    L.append('</fcpxml>')

    out_path.write_text("\n".join(L), encoding="utf-8")


def write_marker_edl_from_model(header: Dict[str, Any], models: List[EventModel],
                                out_path: Path, max_kf_per_event: Optional[int] = None) -> None:
    fps = header["fps"]
    df = header["drop_frame"]
    name = header["name"]

    L: List[str] = []
    L.append(f"TITLE: {name}_TIMELINE_MARKERS_FROM_CSV")
    L.append(f"FCM: {'DROP FRAME' if df else 'NON-DROP FRAME'}")
    L.append("")

    for idx, M in enumerate(models, start=1):
        rec_in_tc = frames_to_tc(M.evt_abs, fps, df=df)
        rec_out_tc = frames_to_tc(M.evt_abs + 1, fps, df=df)

        # Event header (1-frame “marker” event)
        L.append(f"{idx:03d}  001      V     C        {rec_in_tc} {rec_out_tc} {rec_in_tc} {rec_out_tc}  ")
        # Effect & count
        kf_count = sum(
            (1 for _ in M.kf_raw_grouped.items())
        ) if M.kf_raw_grouped else 0  # “times with any param”
        L.append(f"{M.effect_name} - KEYFRAMES: {sum(len(v) for v in group_raw_kf_by_time_inv(M.kf_raw_grouped).values()) if False else kf_count}")

        # Emit keyframes grouped per (absolute) time
        times = list(M.kf_raw_grouped.items())
        if max_kf_per_event is not None:
            times = times[:max_kf_per_event]

        # Build dicts for quick lookup of our computed (local, value) pairs
        pos_at = {t: v for t, v in M.tr_position}   # local_frames -> "x y"
        scl_at = {t: v for t, v in M.tr_scale}
        rot_at = {t: v for t, v in M.tr_rotation}
        op_at  = {t: v for t, v in M.tr_opacity}

        for kf_idx, (absf, raw) in enumerate(times, start=1):
            avid_tc = frames_to_tc(absf, fps, df=df)
            local = absf - M.evt_abs
            if local < 0: local = 0
            if local > M.evt_len: local = M.evt_len
            rel_tc = frames_to_tc(local, fps, df=df)

            # AVID raw pairs
            avid_parts: List[str] = []
            if raw['posx'] is not None or raw['posy'] is not None:
                avid_parts.append(f"pos=({raw['posx'] or '-'}, {raw['posy'] or '-'})")
            if raw['scalex'] is not None or raw['scaley'] is not None:
                avid_parts.append(f"scale=({raw['scalex'] or '-'}, {raw['scaley'] or '-'})")
            if raw['rot'] is not None:
                avid_parts.append(f"rotation={raw['rot']}")
            if raw['op'] is not None:
                avid_parts.append(f"opacity={raw['op']}")
            if not avid_parts:
                avid_parts.append("-")

            # FCPXML values — exactly those used by XML (from computed tracks)
            res_parts: List[str] = []
            if local in pos_at:
                res_parts.append(f'position="{pos_at[local]}"')
            if local in scl_at:
                res_parts.append(f'scale="{scl_at[local]}"')
            if local in rot_at:
                res_parts.append(f'rotation="{rot_at[local]}"')
            if local in op_at:
                res_parts.append(f'opacity="{op_at[local]}"')

            L.append(f"KF{kf_idx} @ {avid_tc} (rel {rel_tc}) AVID: " + ", ".join(avid_parts))
            L.append("         FCPXML: " + (", ".join(res_parts) if res_parts else "-"))

        # Marker tag line
        L.append(f"|C:ResolveColorBlue |M:EVENT{idx} - {M.clip_name} |D:1")
        L.append("")

    out_path.write_text("\n".join(L), encoding="utf-8")


# helper used only to show total KF count when needed (kept off by default)
def group_raw_kf_by_time_inv(grouped: "OrderedDict[int, Dict[str, Any]]") -> Dict[int, List[str]]:
    # returns absf -> list of component keys present
    out: Dict[int, List[str]] = {}
    for t, comp in grouped.items():
        present = [k for k, v in comp.items() if v is not None]
        out[t] = present
    return out


# ---------- CLI ----------

def main() -> None:
    ap = argparse.ArgumentParser(description="CSV → FCPXML (REL13) + Marker EDL (REL15)")
    ap.add_argument("-i", "--input", required=True, type=Path, help="Input CSV (SuperEDL export)")
    ap.add_argument("-x", "--xml", required=True, type=Path, help="Output FCPXML path")
    ap.add_argument("-e", "--edl", required=True, type=Path, help="Output Marker EDL path")
    ap.add_argument("--max-kf", type=int, default=None, help="Limit keyframe times per event in EDL")
    args = ap.parse_args()

    header, models = build_model_from_csv(args.input)
    write_fcpxml_from_model(header, models, args.xml)
    write_marker_edl_from_model(header, models, args.edl, max_kf_per_event=args.max_kf)
    print(f"✅ Wrote FCPXML: {args.xml}")
    print(f"✅ Wrote Marker EDL: {args.edl}")


if __name__ == "__main__":
    main()
