#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cutlass CSV→JSON Converter (with GUI)
-------------------------------------

- Parses Avid "super_edl_fx_report" CSV (effects/keyframes inside a single
  quoted CSV cell on each event row).
- Extracts transforms (Position + Scale) from AFX/DVE parameter families.
- Uses precise FPS from CSV header (DF/NDF respected; DF only for nominal 30/60).
- Timecode math supports DF & NDF using nominal fps.
- Optional EDL verification: first/last KF must match clip edges.
- GUI toggles:
    * Verbose audit (warn about ignored AFX/DVE params)
    * Force edge KFs at recIn/recOut-1 if a track is animated
    * Emit Cutlass params (position/scale as FCP-style keyframe arrays with
      time="N/Ds" and value="x y") — times are RELATIVE to clip start.

Output:
- Analysis JSON (always present): project/assets/clips + transforms in tc.
- If "Emit Cutlass params" is ON, per-clip:
  clip["adjustTransform"] = { "params": [ {name, keyframes/static} ... ] }
"""

import csv
import io
import json
import os
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

# ---------- Tk GUI ----------
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------- Helpers: read text ----------
def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

# ---------- Find the table header row ----------
TABLE_HEADER_PREFIX = "Event,Event Name,Clip Name,"

def find_table_header_line_index(csv_text: str) -> int:
    lines = csv_text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(TABLE_HEADER_PREFIX):
            return i
    return -1

def read_table_records(csv_text: str) -> Tuple[List[str], List[List[str]]]:
    """
    Return (header_row, records) for the events table only.
    We slice the CSV content starting from the header row and let csv.reader
    reconstruct rows with embedded newlines correctly.
    """
    idx = find_table_header_line_index(csv_text)
    if idx < 0:
        return [], []
    sliced = "\n".join(csv_text.splitlines()[idx:])  # from header → end
    reader = csv.reader(io.StringIO(sliced))
    try:
        header = next(reader)
    except StopIteration:
        return [], []
    rows = list(reader)
    return header, rows

def build_header_index(header_row: List[str]) -> Dict[str, int]:
    return {name.strip(): i for i, name in enumerate(header_row)}

# ---------- FPS / Timecode parsing ----------

@dataclass
class RateInfo:
    fps_frac: Fraction     # e.g., Fraction(24000, 1001) or Fraction(25, 1)
    nominal: int           # nominal frame rate used by timecode (24,25,30,50,60, etc.)
    dropframe: bool        # DF timecode? (only meaningful for nominal 30/60)

def parse_timeline_rate(csv_text: str) -> RateInfo:
    """
    Parses the header line: 'Timeline Edit Rate,25.0 (NDF)'.
    Maps common fractional rates to exact SMPTE ratios.
    DF is only honored for nominal 30/60; otherwise forced to NDF.
    """
    m = re.search(r'^Timeline Edit Rate,([0-9.]+)\s*(\(([^)]+)\))?', csv_text, flags=re.MULTILINE)
    if not m:
        return RateInfo(Fraction(25,1), 25, False)

    rate_f = float(m.group(1))
    tag = (m.group(3) or "").strip().upper()

    # exact tag parse
    if tag == "DF":
        is_df = True
    elif tag in ("NDF", ""):
        is_df = False
    else:
        is_df = False  # unknown tag → assume NDF

    def approx(x, target, tol=0.01): return abs(x-target) < tol

    # map to canonical fractions/nominals
    if approx(rate_f, 23.976) or approx(rate_f, 23.98) or approx(rate_f, 23.97):
        ri = RateInfo(Fraction(24000,1001), 24, is_df)
    elif approx(rate_f, 29.97):
        ri = RateInfo(Fraction(30000,1001), 30, is_df)
    elif approx(rate_f, 59.94):
        ri = RateInfo(Fraction(60000,1001), 60, is_df)
    elif approx(rate_f, 47.952) or approx(rate_f, 47.95):
        ri = RateInfo(Fraction(48000,1001), 48, is_df)
    else:
        # Integer-ish or other
        for cand in (24, 25, 30, 48, 50, 60):
            if approx(rate_f, cand, tol=0.001):
                ri = RateInfo(Fraction(cand,1), int(cand), is_df)
                break
        else:
            # Keep the exact fraction if possible
            ri = RateInfo(Fraction(rate_f).limit_denominator(1000000), int(round(rate_f)), is_df)

    # DF is only meaningful for nominal 30/60
    if ri.nominal not in (30, 60):
        ri.dropframe = False

    return ri

# ----- Timecode math (NDF + DF) using nominal fps -----

def tc_to_frames_ndf(tc: str, nominal: int) -> int:
    h, m, s, f = map(int, tc.split(":"))
    return ((h*3600 + m*60 + s) * nominal) + f

def frames_to_tc_ndf(frames: int, nominal: int) -> str:
    s, f = divmod(frames, nominal)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

def tc_to_frames_df(tc: str, nominal: int) -> int:
    """
    SMPTE DF mapping (applies to nominal 30 or 60).
    Drops 2 frames/minute for 29.97, 4 frames/minute for 59.94, except every 10th minute.
    """
    h, m, s, f = map(int, tc.split(":"))
    if nominal not in (30, 60):
        return tc_to_frames_ndf(tc, nominal)

    drop = 2 if nominal == 30 else 4
    total_minutes = 60*h + m
    dropped_frames = drop * (total_minutes - total_minutes // 10)
    base = ((h * 3600) + (m * 60) + s) * nominal + f
    return base - dropped_frames

def frames_to_tc_df(frames: int, nominal: int) -> str:
    """
    Inverse of tc_to_frames_df. Iterative minute-walk (safe for editor-length clips).
    """
    if nominal not in (30, 60):
        return frames_to_tc_ndf(frames, nominal)

    drop = 2 if nominal == 30 else 4
    fps = nominal

    def frames_in_minute(minute_index: int) -> int:
        return fps*60 if (minute_index % 10 == 0) else fps*60 - drop

    # Hours
    h = 0
    while True:
        frames_per_hour = sum(frames_in_minute(mm) for mm in range(60))
        if frames >= frames_per_hour:
            frames -= frames_per_hour
            h += 1
        else:
            break

    # Minutes
    m = 0
    for mm in range(60):
        fim = frames_in_minute(mm)
        if frames >= fim:
            frames -= fim
            m += 1
        else:
            break

    s, f = divmod(frames, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

def tc_to_frames(tc: str, rate: RateInfo) -> int:
    return tc_to_frames_df(tc, rate.nominal) if rate.dropframe else tc_to_frames_ndf(tc, rate.nominal)

def frames_to_tc(frames: int, rate: RateInfo) -> str:
    return frames_to_tc_df(frames, rate.nominal) if rate.dropframe else frames_to_tc_ndf(frames, rate.nominal)

# ---- FCP/Cutlass time string helper: frames → "N/Ds" (relative seconds) ----
def frames_to_fcpx_time_str(frames: int, rate: RateInfo) -> str:
    """
    Convert a frame offset (relative to clip start) into FCP-style rational seconds.
    fps = rate.fps_frac = num/den. One frame = den/num seconds.
    time = frames * (den/num) seconds => "frames*den / num s"
    """
    num = rate.fps_frac.numerator
    den = rate.fps_frac.denominator
    N = frames * den
    D = num
    return f"{N}/{D}s"

# ---------- Numbers ----------
def frac_to_float(s: str) -> float:
    s = s.strip()
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            return float(a) / float(b)
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        if s.lower() == "true":
            return 1.0
        if s.lower() == "false":
            return 0.0
        return 0.0

def percent_to_ratio(v: float) -> float:
    return v / 100.0

# ---------- Data containers ----------
@dataclass
class Keyframe:
    tc: str
    x: float
    y: float
    curve: Optional[str] = None  # only used for scale ("linear")

@dataclass
class TransformOut:
    position_static: Optional[Tuple[float, float]] = None
    position_kf: List[Keyframe] = field(default_factory=list)
    scale_static: Optional[Tuple[float, float]] = None
    scale_kf: List[Keyframe] = field(default_factory=list)
    all_param_names: List[str] = field(default_factory=list)  # for auditing
    used_param_names: List[str] = field(default_factory=list)

# ---------- EDL parsing (optional) ----------
EDL_LINE_RE = re.compile(
    r'^\s*(\d{3,6})\s+(\S+)\s+V\s+\S+\s+'
    r'(\d{2}:\d{2}:\d{2}:\d{2})\s+'   # src in
    r'(\d{2}:\d{2}:\d{2}:\d{2})\s+'   # src out
    r'(\d{2}:\d{2}:\d{2}:\d{2})\s+'   # rec in
    r'(\d{2}:\d{2}:\d{2}:\d{2})'      # rec out
)

def parse_edl(edl_text: str) -> Dict[str, Dict[str, str]]:
    events: Dict[str, Dict[str, str]] = {}
    for line in edl_text.splitlines():
        m = EDL_LINE_RE.match(line)
        if m:
            evt = m.group(1)
            events[evt] = {
                "reel": m.group(2),
                "src_in": m.group(3),
                "src_out": m.group(4),
                "rec_in": m.group(5),
                "rec_out": m.group(6),
            }
    return events

# ---------- Keyframe Details parsing ----------
ANIM_TOKENS = ("Animated Parameters", "ANIMATED PARAMETERS")
STAT_TOKENS  = ("Static Parameters", "STATIC PARAMETERS")

KF_RE = re.compile(r'Keyframe at (\d{2}:\d{2}:\d{2}:\d{2}) .*?-> Value:\s*(.+)')
PARAM_HDR_RE = re.compile(r'^\s*-\s*Parameter:\s*([A-Z0-9_]+)\s*\((\d+)\s*keyframes?\)\s*$', re.I)
PARAM_STATIC_RE = re.compile(r'^\s*-\s*Parameter:\s*([A-Z0-9_]+)\s*->\s*Value:\s*(.+)\s*$')

# Family matching (transform-only)
POS_X_RE = re.compile(r'^(?:AFX|DVE)_(?:POS|PAN)_X_U$', re.I)
POS_Y_RE = re.compile(r'^(?:AFX|DVE)_(?:POS|PAN)_Y_U$', re.I)
SCL_X_RE = re.compile(r'^(?:AFX|DVE)_(?:SCALE|ZOOM|SIZE)_X_U$', re.I)
SCL_Y_RE = re.compile(r'^(?:AFX|DVE)_(?:SCALE|ZOOM|SIZE)_Y_U$', re.I)
SCL_SINGLE_RE = re.compile(r'^(?:AFX|DVE)_(?:SCALE|ZOOM)_U$', re.I)

def normalize_line_in_cell(line: str) -> str:
    s = line.replace("\xa0", " ").strip()
    QUOTES = ['"', '“', '”']
    while len(s) > 0 and s[0] in QUOTES:
        s = s[1:]
    while len(s) > 0 and s[-1] in QUOTES:
        s = s[:-1]
    return s.strip()

def parse_keyframe_details_cell(cell_text: str) -> Tuple[Dict[str, List[Tuple[str, str]]], Dict[str, str], List[str]]:
    """
    Parse the contents of the "Keyframe Details" cell.
    Returns (kf_by_param, static_values, all_param_names)
    """
    in_anim = False
    in_stat = False
    current: Optional[str] = None
    kf_by_param: Dict[str, List[Tuple[str, str]]] = {}
    stat: Dict[str, str] = {}
    names: List[str] = []

    for raw in cell_text.splitlines():
        line = normalize_line_in_cell(raw)
        if not line:
            continue

        if any(t in line for t in ANIM_TOKENS):
            in_anim, in_stat = True, False
            current = None
            continue
        if any(t in line for t in STAT_TOKENS):
            in_stat, in_anim = True, False
            current = None
            continue

        if in_anim:
            m_hdr = PARAM_HDR_RE.match(line)
            if m_hdr:
                current = m_hdr.group(1).strip()
                names.append(current)
                kf_by_param.setdefault(current, [])
                continue
            m_kf = KF_RE.search(line)
            if m_kf and current:
                tc = m_kf.group(1)
                val = m_kf.group(2).strip()
                kf_by_param[current].append((tc, val))
                continue

        if in_stat:
            m_stat = PARAM_STATIC_RE.match(line)
            if m_stat:
                pname = m_stat.group(1).strip()
                pval = m_stat.group(2).strip()
                names.append(pname)
                stat[pname] = pval
                continue

    return kf_by_param, stat, names

def pick_param_names(all_params: set) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    def find(match: re.Pattern) -> Optional[str]:
        for p in all_params:
            if match.match(p):
                return p
        return None
    px = find(POS_X_RE)
    py = find(POS_Y_RE)
    sx = find(SCL_X_RE)
    sy = find(SCL_Y_RE)
    s1 = find(SCL_SINGLE_RE)
    return px, py, sx, sy, s1

def transform_from_keyframe_cell(cell_text: str, rate: RateInfo) -> TransformOut:
    kf_by_param, static_values, all_names = parse_keyframe_details_cell(cell_text)
    out = TransformOut()
    out.all_param_names = list(sorted(set(all_names)))

    all_params = set(kf_by_param.keys()) | set(static_values.keys())
    pos_x, pos_y, scl_x, scl_y, scl_1 = pick_param_names(all_params)

    used_names = set()

    # Position (animated first)
    if (pos_x in kf_by_param) or (pos_y in kf_by_param):
        by_tc: Dict[str, Dict[str, float]] = {}
        if pos_x in kf_by_param:
            used_names.add(pos_x)
            for tc, v in kf_by_param[pos_x]:
                by_tc.setdefault(tc, {})["x"] = frac_to_float(v)
        if pos_y in kf_by_param:
            used_names.add(pos_y)
            for tc, v in kf_by_param[pos_y]:
                by_tc.setdefault(tc, {})["y"] = frac_to_float(v)

        for tc in sorted(by_tc.keys(), key=lambda t: tc_to_frames(t, rate)):
            xy = by_tc[tc]
            if "x" in xy and "y" in xy:
                out.position_kf.append(Keyframe(tc=tc, x=xy["x"], y=xy["y"]))
    # Position static
    elif (pos_x in static_values) and (pos_y in static_values):
        used_names.update([pos_x, pos_y])
        out.position_static = (frac_to_float(static_values[pos_x]),
                               frac_to_float(static_values[pos_y]))

    # Scale (animated pair)
    if (scl_x in kf_by_param) or (scl_y in kf_by_param):
        by_tc: Dict[str, Dict[str, float]] = {}
        if scl_x in kf_by_param:
            used_names.add(scl_x)
            for tc, v in kf_by_param[scl_x]:
                by_tc.setdefault(tc, {})["x"] = percent_to_ratio(frac_to_float(v))
        if scl_y in kf_by_param:
            used_names.add(scl_y)
            for tc, v in kf_by_param[scl_y]:
                by_tc.setdefault(tc, {})["y"] = percent_to_ratio(frac_to_float(v))

        for tc in sorted(by_tc.keys(), key=lambda t: tc_to_frames(t, rate)):
            xy = by_tc[tc]
            if "x" in xy and "y" in xy:
                out.scale_kf.append(Keyframe(tc=tc, x=xy["x"], y=xy["y"], curve="linear"))

    # Scale (animated single)
    elif scl_1 in kf_by_param:
        used_names.add(scl_1)
        kfs = sorted(kf_by_param[scl_1], key=lambda kv: tc_to_frames(kv[0], rate))
        for tc, v in kfs:
            r = percent_to_ratio(frac_to_float(v))
            out.scale_kf.append(Keyframe(tc=tc, x=r, y=r, curve="linear"))

    # Scale static (pair)
    elif (scl_x in static_values) and (scl_y in static_values):
        used_names.update([scl_x, scl_y])
        out.scale_static = (percent_to_ratio(frac_to_float(static_values[scl_x])),
                            percent_to_ratio(frac_to_float(static_values[scl_y])))
    # Scale static (single)
    elif scl_1 in static_values:
        used_names.add(scl_1)
        r = percent_to_ratio(frac_to_float(static_values[scl_1]))
        out.scale_static = (r, r)

    out.used_param_names = list(sorted(used_names))
    return out

# ---------- Project header (name/start) ----------
def parse_project_header(csv_text: str) -> Dict[str, Any]:
    info = {"name": "Untitled", "startTC": "00:00:00:00"}
    m_name = re.search(r'^Timeline Name,([^\r\n]+)$', csv_text, flags=re.MULTILINE)
    if m_name:
        info["name"] = m_name.group(1).strip()
    m_start = re.search(r'^Timeline Start,(\d{2}:\d{2}:\d{2}:\d{2})', csv_text, flags=re.MULTILINE)
    if m_start:
        info["startTC"] = m_start.group(1)
    return info

# ---------- CSV → Cutlass JSON ----------
def build_cutlass_json(csv_path: str,
                       edl_path: Optional[str],
                       verbose_audit: bool,
                       force_edge_kfs: bool,
                       emit_cutlass_params: bool) -> Dict[str, Any]:
    csv_text = read_text(csv_path)
    header_row, rows = read_table_records(csv_text)
    if not header_row:
        raise RuntimeError("Could not find events table header in CSV.")
    idx = build_header_index(header_row)

    proj_hdr = parse_project_header(csv_text)
    rate = parse_timeline_rate(csv_text)

    # EDL optional
    edl_events = parse_edl(read_text(edl_path)) if (edl_path and os.path.exists(edl_path)) else {}

    results: Dict[str, Any] = {
        "project": {
            "name": proj_hdr["name"],
            "rate": float(rate.fps_frac),   # numeric for visibility
            "rate_num": rate.fps_frac.numerator,
            "rate_den": rate.fps_frac.denominator,
            "nominal_fps": rate.nominal,
            "dropframe": rate.dropframe,
            "startTC": proj_hdr["startTC"],
        },
        "assets": [],
        "clips": [],
        "warnings": [],
    }

    asset_ids: Dict[str, str] = {}
    next_asset_idx = 1

    # Iterate event rows
    for row in rows:
        if len(row) < len(header_row):
            continue

        clip_name = row[idx.get("Clip Name")]
        src_file_name = row[idx.get("Source File Name")]
        src_file_path = row[idx.get("Source File Path")]
        rec_in = row[idx.get("Timeline Start TC")]
        dur_frames = int(row[idx.get("Event Length")] or 0)
        rec_out = frames_to_tc(tc_to_frames(rec_in, rate) + dur_frames, rate) if rec_in else rec_in

        # Asset registry
        src_url = f"file://{(src_file_path or '').rstrip('/')}/{src_file_name}".replace("\\", "/")
        if clip_name not in asset_ids:
            aid = f"r{next_asset_idx}"; next_asset_idx += 1
            asset_ids[clip_name] = aid
            results["assets"].append({"id": aid, "name": clip_name, "src": src_url})
        asset_id = asset_ids[clip_name]

        # Transform
        kf_cell = row[idx.get("Keyframe Details")] if idx.get("Keyframe Details") is not None else ""
        transform = transform_from_keyframe_cell(kf_cell or "", rate)

        # Optional: Force edge keyframes (only if already animated)
        rec_out_minus1_tc = frames_to_tc(tc_to_frames(rec_out, rate) - 1, rate)

        if force_edge_kfs:
            # Position
            if transform.position_kf:
                if transform.position_kf[0].tc != rec_in:
                    first = transform.position_kf[0]
                    transform.position_kf.insert(0, Keyframe(tc=rec_in, x=first.x, y=first.y))
                if transform.position_kf[-1].tc != rec_out_minus1_tc:
                    last = transform.position_kf[-1]
                    transform.position_kf.append(Keyframe(tc=rec_out_minus1_tc, x=last.x, y=last.y))
            # Scale
            if transform.scale_kf:
                if transform.scale_kf[0].tc != rec_in:
                    first = transform.scale_kf[0]
                    transform.scale_kf.insert(0, Keyframe(tc=rec_in, x=first.x, y=first.y, curve="linear"))
                if transform.scale_kf[-1].tc != rec_out_minus1_tc:
                    last = transform.scale_kf[-1]
                    transform.scale_kf.append(Keyframe(tc=rec_out_minus1_tc, x=last.x, y=last.y, curve="linear"))

        # EDL edge verification
        if edl_events and rec_in:
            # Find matching EDL event by rec_in
            edl_last_tc = None
            for evt_id, ev in edl_events.items():
                if ev["rec_in"] == rec_in:
                    edl_last_tc = frames_to_tc(tc_to_frames(ev["rec_out"], rate) - 1, rate)
                    break
            if edl_last_tc:
                if transform.position_kf:
                    if transform.position_kf[0].tc != rec_in:
                        results["warnings"].append(f"{clip_name}: first position KF {transform.position_kf[0].tc} != rec_in {rec_in}")
                    if transform.position_kf[-1].tc != edl_last_tc:
                        results["warnings"].append(f"{clip_name}: last position KF {transform.position_kf[-1].tc} != rec_out-1 {edl_last_tc}")
                if transform.scale_kf:
                    if transform.scale_kf[0].tc != rec_in:
                        results["warnings"].append(f"{clip_name}: first scale KF {transform.scale_kf[0].tc} != rec_in {rec_in}")
                    if transform.scale_kf[-1].tc != edl_last_tc:
                        results["warnings"].append(f"{clip_name}: last scale KF {transform.scale_kf[-1].tc} != rec_out-1 {edl_last_tc}")

        # Verbose audit: show ignored transform-ish params
        if verbose_audit:
            used = set(transform.used_param_names)
            allp = set(transform.all_param_names)
            ignored = sorted([p for p in allp if p not in used and (
                p.upper().startswith("AFX_") or p.upper().startswith("DVE_")
            )])
            if ignored:
                results["warnings"].append(f"{clip_name}: ignored params -> {', '.join(ignored[:20])}" + (" ..." if len(ignored)>20 else ""))

        # Emit clip object (analysis JSON)
        clip_out: Dict[str, Any] = {
            "assetId": asset_id,
            "clipName": clip_name,
            "recIn": rec_in,
            "recOut": rec_out,
            "durationFrames": dur_frames,
            "transform": {}
        }

        # Position
        if transform.position_kf:
            clip_out["transform"]["position"] = {
                "keyframes": [kf.__dict__ for kf in transform.position_kf]  # tc-based; no attrs
            }
        elif transform.position_static is not None:
            x, y = transform.position_static
            clip_out["transform"]["position"] = {"static": {"x": x, "y": y}}

        # Scale
        if transform.scale_kf:
            clip_out["transform"]["scale"] = {
                "keyframes": [kf.__dict__ for kf in transform.scale_kf]  # curve="linear" already set
            }
        elif transform.scale_static is not None:
            x, y = transform.scale_static
            clip_out["transform"]["scale"] = {"static": {"x": x, "y": y}}

        # ---- Optional: Cutlass params schema (FCP-style) ----
        if emit_cutlass_params:
            params: List[Dict[str, Any]] = []

            rec_in_frames = tc_to_frames(rec_in, rate)

            # Position param
            if transform.position_kf:
                kfs = []
                for k in transform.position_kf:
                    f_offset = tc_to_frames(k.tc, rate) - rec_in_frames
                    kfs.append({
                        "time": frames_to_fcpx_time_str(f_offset, rate),  # "N/Ds"
                        "value": f"{k.x:g} {k.y:g}",                      # "x y"
                        # position: NO curve/interp attrs
                    })
                params.append({"name": "position", "keyframes": kfs})
            elif transform.position_static is not None:
                x, y = transform.position_static
                params.append({"name": "position", "value": f"{x:g} {y:g}"})

            # Scale param
            if transform.scale_kf:
                kfs = []
                for k in transform.scale_kf:
                    f_offset = tc_to_frames(k.tc, rate) - rec_in_frames
                    kfs.append({
                        "time": frames_to_fcpx_time_str(f_offset, rate),  # "N/Ds"
                        "value": f"{k.x:g} {k.y:g}",
                        "curve": "linear"                                   # scale supports curve only
                    })
                params.append({"name": "scale", "keyframes": kfs})
            elif transform.scale_static is not None:
                x, y = transform.scale_static
                params.append({"name": "scale", "value": f"{x:g} {y:g}"})

            if params:
                clip_out["adjustTransform"] = {"params": params}

        results["clips"].append(clip_out)

    return results

# ---------- Filename helpers ----------
def slugify_filename(name: str) -> str:
    # Keep dots and underscores; replace spaces with underscores; drop unsafe chars
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^\w.\-]", "_", name)  # \w includes [A-Za-z0-9_]
    # Avoid accidental multiple underscores
    name = re.sub(r"_+", "_", name)
    return name.strip("_")

def suggested_output_filename(timeline_name: str) -> str:
    base = slugify_filename(timeline_name or "cutlass_export")
    # Append local date-time stamp
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{stamp}.json"

# ---------- GUI ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cutlass CSV → JSON Converter (precise FPS, DF/NDF)")
        self.geometry("1040x760")
        self.resizable(True, True)

        self.csv_path = tk.StringVar()
        self.edl_path = tk.StringVar()
        self.verbose_audit = tk.BooleanVar(value=False)
        self.force_edge_kfs = tk.BooleanVar(value=False)
        self.emit_cutlass_params = tk.BooleanVar(value=True)  # default ON per your request
        self.timeline_name = "cutlass_export"

        frm = ttk.Frame(self); frm.pack(fill="both", expand=True, padx=12, pady=12)

        # Row: CSV
        r1 = ttk.Frame(frm); r1.pack(fill="x", pady=6)
        ttk.Label(r1, text="CSV:").pack(side="left")
        ttk.Entry(r1, textvariable=self.csv_path).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r1, text="Browse…", command=self.browse_csv).pack(side="left")

        # Row: EDL (optional)
        r2 = ttk.Frame(frm); r2.pack(fill="x", pady=6)
        ttk.Label(r2, text="EDL (optional):").pack(side="left")
        ttk.Entry(r2, textvariable=self.edl_path).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r2, text="Browse…", command=self.browse_edl).pack(side="left")

        # Options
        rOpt = ttk.Frame(frm); rOpt.pack(fill="x", pady=6)
        ttk.Checkbutton(rOpt, text="Verbose audit (warn about ignored AFX/DVE params)", variable=self.verbose_audit).pack(side="left")
        ttk.Checkbutton(rOpt, text="Force edge keyframes at recIn / recOut-1", variable=self.force_edge_kfs).pack(side="left", padx=12)
        ttk.Checkbutton(rOpt, text="Emit Cutlass params (FCP-style time/value)", variable=self.emit_cutlass_params).pack(side="left", padx=12)

        # Buttons
        r3 = ttk.Frame(frm); r3.pack(fill="x", pady=6)
        ttk.Button(r3, text="Parse & Preview", command=self.parse_preview).pack(side="left")
        ttk.Button(r3, text="Export JSON…", command=self.export_json).pack(side="left", padx=8)

        # Output
        self.text = tk.Text(frm, wrap="word", height=30)
        self.text.pack(fill="both", expand=True, pady=8)

    # ---- GUI handlers ----
    def browse_csv(self):
        path = filedialog.askopenfilename(title="Select CSV", filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if path:
            self.csv_path.set(path)
            # read timeline name for suggested filename
            try:
                csv_text = read_text(path)
                m_name = re.search(r'^Timeline Name,([^\r\n]+)$', csv_text, flags=re.MULTILINE)
                if m_name:
                    self.timeline_name = m_name.group(1).strip()
            except Exception:
                self.timeline_name = "cutlass_export"

    def browse_edl(self):
        path = filedialog.askopenfilename(title="Select EDL", filetypes=[("EDL", "*.edl;*.txt"), ("All files", "*.*")])
        if path:
            self.edl_path.set(path)

    def parse_preview(self):
        csvp = self.csv_path.get().strip()
        if not csvp or not os.path.exists(csvp):
            messagebox.showerror("Missing CSV", "Please choose a CSV file.")
            return
        edlp = self.edl_path.get().strip() or None
        try:
            data = build_cutlass_json(
                csvp, edlp,
                self.verbose_audit.get(),
                self.force_edge_kfs.get(),
                self.emit_cutlass_params.get()
            )
        except Exception as e:
            messagebox.showerror("Parse error", str(e))
            return

        # Print summary
        self.text.delete("1.0", "end")
        proj = data["project"]
        fps_desc = f"{proj['rate_num']}/{proj['rate_den']} ({proj['nominal_fps']} fps {'DF' if proj['dropframe'] else 'NDF'})"
        self.text.insert("end", f"Project: {proj['name']} @ {fps_desc}, start {proj['startTC']}\n")
        self.text.insert("end", f"Assets: {len(data['assets'])}\n")
        self.text.insert("end", f"Clips:  {len(data['clips'])}\n")

        if data.get("warnings"):
            self.text.insert("end", "\nWARNINGS:\n")
            for w in data["warnings"]:
                self.text.insert("end", f"  - {w}\n")

        self.text.insert("end", "\nClips:\n")
        for c in data["clips"]:
            self.text.insert("end", f"- {c['clipName']} [{c['recIn']} → {c['recOut']}] dur {c['durationFrames']}f\n")
            tr = c.get("transform", {})
            if "position" in tr:
                pos = tr["position"]
                if "static" in pos:
                    self.text.insert("end", f"    position: static {pos['static']}\n")
                else:
                    kfs = pos.get("keyframes", [])
                    if kfs:
                        ksum = ", ".join([f"{k['tc']} ({k['x']:.6g},{k['y']:.6g})" for k in kfs])
                        self.text.insert("end", f"    position: {ksum}\n")
            if "scale" in tr:
                sc = tr["scale"]
                if "static" in sc:
                    self.text.insert("end", f"    scale:    static {sc['static']}\n")
                else:
                    kfs = sc.get("keyframes", [])
                    if kfs:
                        ksum = ", ".join([f"{k['tc']} ({k['x']:.6g},{k['y']:.6g}) curve=linear" for k in kfs])
                        self.text.insert("end", f"    scale:    {ksum}\n")

            # Show Cutlass params if present
            if "adjustTransform" in c:
                self.text.insert("end", f"    (cutlass params)\n")
                for p in c["adjustTransform"].get("params", []):
                    if "keyframes" in p:
                        times = ", ".join([f"{k['time']} value={k['value']}" + (f" curve={k['curve']}" if 'curve' in k else "") for k in p["keyframes"]])
                        self.text.insert("end", f"      {p['name']}: {times}\n")
                    else:
                        self.text.insert("end", f"      {p['name']}: value={p['value']}\n")

    def export_json(self):
        csvp = self.csv_path.get().strip()
        if not csvp or not os.path.exists(csvp):
            messagebox.showerror("Missing CSV", "Please choose a CSV file.")
            return
        edlp = self.edl_path.get().strip() or None
        data = build_cutlass_json(
            csvp, edlp,
            self.verbose_audit.get(),
            self.force_edge_kfs.get(),
            self.emit_cutlass_params.get()
        )

        # Suggested filename, initial dir (same as CSV)
        initdir = os.path.dirname(csvp) if os.path.isdir(os.path.dirname(csvp)) else os.getcwd()
        initialfile = suggested_output_filename(self.timeline_name)

        out_path = filedialog.asksaveasfilename(
            title="Save JSON",
            defaultextension=".json",
            initialdir=initdir,
            initialfile=initialfile,
            filetypes=[("JSON", "*.json")]
        )
        if not out_path:
            return
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        messagebox.showinfo("Done", f"Saved JSON to:\n{out_path}")

# ---------- Main ----------
if __name__ == "__main__":
    App().mainloop()
