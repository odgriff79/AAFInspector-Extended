#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV → FCPXML (REL13) + Resolve Marker EDL (REL15)
Single-source-of-truth model + configurable Avid→Resolve position mapping.

- REL13 timing locked as agreed.
- EDL markers show AVID raw fractions + the exact FCPXML values emitted.
- Position mapping modes:
    aaf1000        : X = AvidX * (width/1000),  Y = -AvidY * (height/1000)   [default]
    percent_height : X = AvidX * (height/100), Y = -AvidY * (height/100)
    percent_width  : X = AvidX * (width/100),  Y = -AvidY * (width/100)
  + pos_mult scalar at the end (default 1.0)

GUI compatibility:
- build_model_from_csv(csv_path) still works (uses defaults).
CLI:
  python csv_to_fcpxml_correct_TCs_FX_EDL.py -i mem1.csv -x out.fcpxml -e out.edl
  python csv_to_fcpxml_correct_TCs_FX_EDL.py -i mem1.csv -x out.fcpxml -e out.edl --pos-mode aaf1000 --pos-mult 1.0
"""

from __future__ import annotations
import argparse
import csv
import io
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional


# ========== hygiene ==========

def sanitize(s: Any) -> str:
    if s is None:
        return ""
    return str(s).replace("\x00", "").encode("ascii", "ignore").decode("utf-8")


# ========== frames / timecode ==========

def tc_to_frames(tc: str, fps: float) -> int:
    """SMPTE -> frames. Treat ';' as ':' for parsing."""
    if not tc or ":" not in tc:
        return 0
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return int(round((h * 3600 + m * 60 + s) * fps + f))


def frames_to_den(frames: int, fps: float) -> Tuple[int, int]:
    den = int(round(fps))
    num = int(frames)
    return num, den


def frames_to_tc_ndf(frames: int, fps: float) -> str:
    f = int(frames % fps)
    sec_total = int(frames // fps)
    s = sec_total % 60
    m = (sec_total // 60) % 60
    h = sec_total // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def frames_to_tc_df(frames: int, fps: float) -> str:
    """Drop-frame for 29.97/59.94. Others: NDF layout with semicolons."""
    if abs(fps - 29.97) < 0.01:
        frames = int(round(frames))
        d = 17982  # frames per 10 minutes
        m = 1798   # frames per minute, except each 10th
        ten = frames // d
        rem = frames % d
        drop = 18 * ten + (2 * ((rem - 2) // m) if rem >= 2 else 0)
        total = frames + drop
        return frames_to_tc_ndf(total, 30.0).replace(":", ";")
    if abs(fps - 59.94) < 0.01:
        half = frames // 2
        tc30 = frames_to_tc_df(half, 29.97)
        h, m, s, f = [int(x) for x in tc30.replace(";", ":").split(":")]
        f = (f * 2) + (frames % 2)
        return f"{h:02d};{m:02d};{s:02d};{f:02d}"
    return frames_to_tc_ndf(frames, fps).replace(":", ";")


def frames_to_tc(frames: int, fps: float, drop_frame: bool = False, df: Optional[bool] = None) -> str:
    """Accepts both drop_frame= and df= keywords."""
    if df is not None:
        drop_frame = df
    return frames_to_tc_df(frames, fps) if drop_frame else frames_to_tc_ndf(frames, fps)


# ========== CSV parsing ==========

def read_csv_summary_and_events(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(True)
    sep = None
    for i, ln in enumerate(lines):
        if ln.strip() == "":
            sep = i
            break
    if sep is None:
        raise ValueError("CSV missing blank line separator between summary and table.")
    summary = [ln.strip() for ln in lines[:sep] if ln.strip()]
    events = list(csv.DictReader(io.StringIO("".join(lines[sep + 1:]))))
    return summary, events


def parse_summary(summary_lines: List[str]):
    """Return (timeline_name, fps, tc_format, start_tc, length_frames)."""
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


# ========== Effect names / detection / conversion ==========

SCALE_NAMES = ("scale", "zoom", "size", "resize", "pan & zoom: zoom")
POS_NAMES   = ("position", "center", "pos", "pan & zoom: center", "pan & zoom: position")
ROT_NAMES   = ("rotation", "rotate", "angle")

def name_is(pname: str, group: str, axis: Optional[str] = None) -> bool:
    n = str(pname or "").lower()
    if group == "scale":
        if any(k in n for k in SCALE_NAMES):
            if axis == "x":
                return (" x" in n) or n.endswith("x") or "_x" in n
            if axis == "y":
                return (" y" in n) or n.endswith("y") or "_y" in n
            return True
    if group == "pos":
        if any(k in n for k in POS_NAMES):
            if axis == "x":
                return (" x" in n) or n.endswith("x") or "_x" in n or "center x" in n
            if axis == "y":
                return (" y" in n) or n.endswith("y") or "_y" in n or "center y" in n
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
    return v  # position/rotation handled separately


# ========== Avid→Resolve position mapping ==========

def avid_pos_to_resolve(px_raw: Any, py_raw: Any, width: int, height: int,
                        mode: str = "aaf1000", mult: float = 1.0) -> Tuple[Optional[float], Optional[float]]:
    """
    Map Avid AVX2 position units to Resolve edit-transform pixels.

    mode:
      - "aaf1000"       : X = AvidX * (width/1000),  Y = -AvidY * (height/1000)
      - "percent_height": X = AvidX * (height/100), Y = -AvidY * (height/100)
      - "percent_width" : X = AvidX * (width/100),  Y = -AvidY * (width/100)

    mult: final scalar applied to both X and Y (default 1.0).
    """
    ax = to_float_safe(px_raw)
    ay = to_float_safe(py_raw)
    if ax is None and ay is None:
        return None, None

    if mode == "percent_height":
        fx_x = height / 100.0
        fx_y = height / 100.0
    elif mode == "percent_width":
        fx_x = width / 100.0
        fx_y = width / 100.0
    else:  # "aaf1000"
        fx_x = width / 1000.0
        fx_y = height / 1000.0

    rx = (ax * fx_x * mult) if ax is not None else None
    ry = (-(ay * fx_y * mult)) if ay is not None else None
    return rx, ry


# ========== Keyframes parsing/grouping ==========

def parse_keyframes_block(kf: str) -> Dict[str, List[Tuple[int, str]]]:
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
    times = sorted({t for pts in animated.values() for t, _ in pts})
    grouped: "OrderedDict[int, Dict[str, Any]]" = OrderedDict(
        (t, {'posx': None, 'posy': None, 'scalex': None, 'scaley': None, 'rot': None, 'op': None})
        for t in times
    )
    for pname, pts in animated.items():
        for t, val in pts:
            g = grouped[t]
            if name_is(pname, "pos", "x"): g['posx'] = val
            elif name_is(pname, "pos", "y"): g['posy'] = val
            elif name_is(pname, "scale", "x"): g['scalex'] = val
            elif name_is(pname, "scale", "y"): g['scaley'] = val
            elif name_is(pname, "scale"): g['scalex'] = val; g['scaley'] = val
            elif name_is(pname, "rot"): g['rot'] = val
            elif name_is(pname, "opacity"): g['op'] = val
    return grouped


# ========== Canonical model ==========

class EventModel:
    def __init__(self):
        self.clip_name: str = "Untitled"
        self.source_path: str = ""
        self.effect_name: str = ""
        self.evt_abs: int = 0
        self.evt_len: int = 0
        self.src_off: int = 0
        self.asset_src_abs: int = 0
        self.asset_src_len: int = 0
        self.kf_raw_grouped: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
        self.tr_position: List[Tuple[int, str]] = []  # (local_frames, "x y")
        self.tr_scale:    List[Tuple[int, str]] = []  # (local_frames, "sx sy")
        self.tr_rotation: List[Tuple[int, str]] = []  # (local_frames, "r")
        self.tr_opacity:  List[Tuple[int, str]] = []  # (local_frames, "a")


def build_model_from_csv(csv_path: Path,
                         timeline_width: int = 1920, timeline_height: int = 1080,
                         pos_mode: str = "aaf1000", pos_mult: float = 1.0
                         ) -> Tuple[Dict[str, Any], List[EventModel]]:
    """
    Build (header, models) from CSV.
    Defaults keep GUI compatibility (it only passes the CSV path).
    """
    summary_lines, events_csv = read_csv_summary_and_events(csv_path)
    name, fps, tc_format, seq_start_tc, seq_length_frames = parse_summary(summary_lines)
    drop_frame = (tc_format == "DF")
    seq_start_frames = tc_to_frames(seq_start_tc, fps)

    def is_filler_to_skip(row: Dict[str, str]) -> bool:
        nm = str(row.get("Event Name", "") or "")
        mob = str(row.get("SourceMobID", "") or "")
        clip = str(row.get("Clip Name", "") or "")
        eff  = str(row.get("Effect Name", "") or "").upper()
        filler = ("Filler" in nm) or ("FX_ON_FILLER" in mob) or ("placeholder" in clip)
        return filler and (eff in ("", "N/A", "NONE", "N A"))

    rows = [r for r in events_csv if not is_filler_to_skip(r)]
    rows.sort(key=lambda r: tc_to_frames(r.get("Timeline Start TC", "00:00:00:00"), fps))

    first_abs = tc_to_frames(rows[0].get("Timeline Start TC", "00:00:00:00"), fps) if rows else seq_start_frames
    head_gap_frames = max(0, first_abs - seq_start_frames)

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

        animated = parse_keyframes_block(r.get("Keyframe Details", "") or "")
        M.kf_raw_grouped = group_raw_kf_by_time(animated)

        # Build converted tracks (LOCAL frames), using the chosen position mapping
        last_sx, last_sy = 1.0, 1.0
        last_px, last_py = 0.0, 0.0
        last_rot = 0.0

        for absf, comp in M.kf_raw_grouped.items():
            local = absf - M.evt_abs
            if local < 0: local = 0
            if local > M.evt_len: local = M.evt_len

            # position
            px, py = avid_pos_to_resolve(
                comp['posx'], comp['posy'],
                timeline_width, timeline_height,
                mode=pos_mode, mult=pos_mult
            )
            if px is None: px = last_px
            if py is None: py = last_py
            M.tr_position.append((local, f"{px:g} {py:g}"))
            last_px, last_py = px, py

            # scale
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
        "timeline_width": int(timeline_width),
        "timeline_height": int(timeline_height),
        "pos_mode": pos_mode,
        "pos_mult": float(pos_mult),
    }
    return header, models


# ========== Writers ==========

def write_fcpxml_from_model(header: Dict[str, Any], models: List[EventModel], out_path: Path) -> None:
    fps = header["fps"]
    tc_format = header["tc_format"]
    seq_start_frames = header["seq_start_frames"]
    seq_length_frames = header["seq_length_frames"]
    head_gap_frames = header["head_gap_frames"]
    name = header["name"]
    width = int(header.get("timeline_width", 1920))
    height = int(header.get("timeline_height", 1080))

    L: List[str] = []
    L.append('<?xml version="1.0" encoding="UTF-8"?>')
    L.append('<!DOCTYPE fcpxml>')
    L.append('<fcpxml version="1.13">')
    L.append('    <resources>')
    L.append(f'        <format name="FFVideoFormat{height}p{int(round(fps))}" frameDuration="1/{int(round(fps))}s" id="r0" height="{height}" width="{width}"/>')

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
    pos_mode = header.get("pos_mode", "aaf1000")
    pos_mult = header.get("pos_mult", 1.0)

    L: List[str] = []
    L.append(f"TITLE: {name}_TIMELINE_MARKERS_FROM_CSV")
    L.append(f"FCM: {'DROP FRAME' if df else 'NON-DROP FRAME'}")
    L.append(f"# position mapping: mode={pos_mode} ×{pos_mult}")
    L.append("")

    for idx, M in enumerate(models, start=1):
        rec_in_tc = frames_to_tc(M.evt_abs, fps, df=df)
        rec_out_tc = frames_to_tc(M.evt_abs + 1, fps, df=df)
        L.append(f"{idx:03d}  001      V     C        {rec_in_tc} {rec_out_tc} {rec_in_tc} {rec_out_tc}  ")

        # simple count for info only
        kf_count = 0
        for comp in M.kf_raw_grouped.values():
            kf_count += int(comp['posx'] is not None) + int(comp['posy'] is not None) \
                      + int(comp['scalex'] is not None) + int(comp['scaley'] is not None) \
                      + int(comp['rot'] is not None) + int(comp['op'] is not None)
        L.append(f"{M.effect_name} - KEYFRAMES: {kf_count}")

        times = list(M.kf_raw_grouped.items())
        if max_kf_per_event is not None:
            times = times[:max_kf_per_event]

        pos_at = {t: v for t, v in M.tr_position}
        scl_at = {t: v for t, v in M.tr_scale}
        rot_at = {t: v for t, v in M.tr_rotation}
        op_at  = {t: v for t, v in M.tr_opacity}

        for kf_idx, (absf, raw) in enumerate(times, start=1):
            avid_tc = frames_to_tc(absf, fps, df=df)
            local = max(0, min(M.evt_len, absf - M.evt_abs))
            rel_tc = frames_to_tc(local, fps, df=df)

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

            res_parts: List[str] = []
            if local in pos_at: res_parts.append(f'position="{pos_at[local]}"')
            if local in scl_at: res_parts.append(f'scale="{scl_at[local]}"')
            if local in rot_at: res_parts.append(f'rotation="{rot_at[local]}"')
            if local in op_at:  res_parts.append(f'opacity="{op_at[local]}"')

            L.append(f"KF{kf_idx} @ {avid_tc} (rel {rel_tc}) AVID: " + ", ".join(avid_parts))
            L.append("         FCPXML: " + (", ".join(res_parts) if res_parts else "-"))
        L.append(f"|C:ResolveColorBlue |M:EVENT{idx} - {M.clip_name} |D:1")
        L.append("")

    out_path.write_text("\n".join(L), encoding="utf-8")


# ========== CLI ==========

def main() -> None:
    ap = argparse.ArgumentParser(description="CSV → FCPXML (REL13) + Marker EDL (REL15)")
    ap.add_argument("-i", "--input", required=True, type=Path, help="Input CSV (SuperEDL export)")
    ap.add_argument("-x", "--xml", required=True, type=Path, help="Output FCPXML path")
    ap.add_argument("-e", "--edl", required=True, type=Path, help="Output Marker EDL path")
    ap.add_argument("--width", type=int, default=1920, help="Timeline width (pixels)")
    ap.add_argument("--height", type=int, default=1080, help="Timeline height (pixels)")
    ap.add_argument("--pos-mode", choices=["aaf1000", "percent_height", "percent_width"], default="aaf1000",
                    help="Avid→Resolve position mapping mode")
    ap.add_argument("--pos-mult", type=float, default=1.0, help="Final multiplier applied to mapped position")
    ap.add_argument("--max-kf", type=int, default=None, help="Limit keyframe times per event in EDL")
    args = ap.parse_args()

    header, models = build_model_from_csv(
        args.input,
        timeline_width=args.width,
        timeline_height=args.height,
        pos_mode=args.pos_mode,
        pos_mult=args.pos_mult,
    )
    write_fcpxml_from_model(header, models, args.xml)
    write_marker_edl_from_model(header, models, args.edl, max_kf_per_event=args.max_kf)
    print(f"✅ Wrote FCPXML: {args.xml}")
    print(f"✅ Wrote Marker EDL: {args.edl}")


if __name__ == "__main__":
    main()
