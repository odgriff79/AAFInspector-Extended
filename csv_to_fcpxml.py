# python csv_to_fcpxml.py --in "BLINK_WORLD_WW2D EP1 SEQ TEST_MEMORY_TEST_3.csv" --out "export.fcpxml"


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
csv_to_fcpxml.py

Converts a SuperEDL CSV (your strict v2 export) into FCPXML 1.13.

Key points:
- Reads summary + events from the CSV (no JSON needed)
- Offsets are sequence-relative (spine starts at 0)
- Keyframes are clip-relative (in-bounds), using rational seconds N/D
- Skips "filler with no effect" rows (e.g., 'N/A on Filler' / placeholder)
- Maps Avid-ish parameter names to FCPXML: scale, position, rotation, opacity
- Attribute order mirrors your reference (Info.fcpxml style)

Usage:
    python csv_to_fcpxml.py --in INPUT.csv --out OUTPUT.fcpxml
"""

from __future__ import annotations
import argparse
import csv
import io
import math
import re
from pathlib import Path
from typing import Dict, List, Tuple, Iterable


# ---------- CSV parsing ----------

def read_csv_summary_and_events(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    # find blank line separator between summary and table
    sep_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == "":
            sep_idx = i
            break
    if sep_idx is None:
        raise ValueError("CSV missing blank separator line between summary and events table.")
    summary_lines = [ln.strip() for ln in lines[:sep_idx] if ln.strip()]
    events = list(csv.DictReader(io.StringIO("".join(lines[sep_idx+1:]))))
    return summary_lines, events


def sanitize_string(text: str) -> str:
    if text is None:
        return ""
    text = str(text).replace("\x00", "")
    # keep ASCII-only for FCPXML safety (paths/names often include UTF-8, but Resolve is fine with ASCII)
    return text.encode("ascii", "ignore").decode("utf-8")


def parse_summary(summary_lines: List[str]):
    """Return (timeline_name, fps, tc_format, start_tc, length_frames)."""
    timeline_name, fps, tc_format, start_tc, length_frames = "Timeline", 25.0, "NDF", "00:00:00:00", 0
    for ln in summary_lines:
        if ln.startswith("Timeline Name,"):
            timeline_name = sanitize_string(ln.split(",", 1)[1].strip())
        elif ln.startswith("Timeline Edit Rate,"):
            parts = ln.split(",", 1)[1].strip().split()
            try:
                fps = float(parts[0])
            except Exception:
                fps = 25.0
            if len(parts) > 1:
                tc_format = parts[1].strip("()")
        elif ln.startswith("Timeline Start,"):
            start_tc = ln.split(",", 1)[1].strip()
        elif ln.startswith("Timeline Length,"):
            m = re.search(r"\((\d+)\s+frames", ln)
            if m:
                length_frames = int(m.group(1))
    return timeline_name, fps, tc_format, start_tc, length_frames


def tc_to_frames(tc: str, fps: float) -> int:
    """HH:MM:SS:FF (or HH:MM:SS;FF); no drop logic here (CSV is already normalized)."""
    if not tc or tc.strip().upper() == "N/A" or ":" not in tc:
        return 0
    parts = tc.replace(";", ":").split(":")
    parts = (parts + ["0", "0", "0", "0"])[:4]
    h, m, s, f = (int(x) for x in parts)
    return int(round((h * 3600 + m * 60 + s) * fps + f))


# ---------- time conversion ----------

def choose_time_denominator(fps: float) -> int:
    # Denominator choices to look like pro interchange:
    if abs(fps - 25.0) < 0.01:
        return 3000
    if abs(fps - 24.0) < 0.01:
        return 2400
    if abs(fps - 30.0) < 0.01:
        return 3000
    if abs(fps - 23.976) < 0.01:
        return 23976
    if abs(fps - 29.97) < 0.01:
        return 29970
    # fallback (nice round-ish)
    return int(round(fps)) * 120


def frames_to_nd(frames: int, fps: float) -> Tuple[int, int]:
    den = choose_time_denominator(fps)
    num = int(round(frames * den / fps))
    return num, den


# ---------- keyframe parsing ----------

def parse_keyframes_block(kf_string: str) -> Dict[str, List[Tuple[int, str]]]:
    """
    Parse the 'Keyframe Details' blob into animated params:
      { param_name: [(abs_frame, value_str), ...], ... }
    """
    if not isinstance(kf_string, str):
        kf_string = ""
    animated: Dict[str, List[Tuple[int, str]]] = {}
    in_anim = False
    current = None
    for line in kf_string.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("--- Animated Parameters ---"):
            in_anim = True
            current = None
            continue
        if line.startswith("--- Static Parameters ---"):
            in_anim = False
            current = None
            continue
        m_hdr = re.search(r"^-+\s*Parameter:\s*(.*?)\s*(?:\(|->|$)", line)
        if m_hdr:
            current = m_hdr.group(1).strip()
            if in_anim:
                animated.setdefault(current, [])
            continue
        m_kf = re.search(r"\((\d+)f\)\s*->\s*Value:\s*(.*)$", line)
        if in_anim and current and m_kf:
            absf = int(m_kf.group(1))
            value = m_kf.group(2).strip()
            animated[current].append((absf, value))
    for k in animated:
        animated[k].sort(key=lambda x: x[0])
    return animated


def to_float_safe(value_str: str):
    try:
        s = str(value_str).strip()
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / float(b)
        return float(s)
    except Exception:
        return None


def normalize_scale(v: float | None) -> float | None:
    """Heuristic: if >3, treat as percent [0..100] → 0..1 factor."""
    if v is None:
        return None
    return v / 100.0 if v > 3.0 else v


# ---------- name matching ----------

SCALE_NAMES = ("scale", "zoom", "size", "resize", "pan & zoom: zoom")
POS_NAMES   = ("position", "center", "pan & zoom: center", "pan & zoom: position")
ROT_NAMES   = ("rotation", "rotate", "angle")

def name_is(name: str, group: str, axis: str | None = None) -> bool:
    n = str(name).lower()
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


# ---------- filters ----------

def is_filler(row: dict) -> bool:
    name = str(row.get("Event Name", ""))
    mob  = str(row.get("SourceMobID", ""))
    clip = str(row.get("Clip Name", ""))
    return ("Filler" in name) or ("FX_ON_FILLER" in mob) or ("placeholder" in clip)


def is_filler_to_skip(row: dict) -> bool:
    """Skip 'filler with no effect' so placeholders don't cover the start."""
    eff = str(row.get("Effect Name", "N/A") or "N/A").strip().upper()
    return is_filler(row) and (eff in ("N/A", "", "NONE", "N A"))


# ---------- FCPXML builder ----------

def build_fcpxml_from_csv(csv_path: Path, out_path: Path) -> None:
    summary, events = read_csv_summary_and_events(csv_path)
    timeline_name, fps, tc_format, seq_tc_start, seq_length_frames = parse_summary(summary)

    seq_start_frames = tc_to_frames(seq_tc_start, fps)

    # sort by start TC and remove filler-without-effect
    evts = sorted(events, key=lambda e: tc_to_frames(e.get("Timeline Start TC", "00:00:00:00"), fps))
    evts = [e for e in evts if not is_filler_to_skip(e)]

    L: List[str] = []
    # Header
    L.append('<?xml version="1.0" encoding="UTF-8"?>')
    L.append('<!DOCTYPE fcpxml>')
    L.append('<fcpxml version="1.13">')

    # resources
    L.append('    <resources>')
    # format attribute order: height, width, id, name, frameDuration
    L.append(f'        <format height="1080" width="1920" id="r0" name="FFVideoFormat1080p{int(round(fps*100)):04d}" frameDuration="1/{int(round(fps))}s"/>')
    L.append(f'        <format height="2160" width="4096" id="r1" name="FFVideoFormat4096x2160p{int(round(fps*100)):04d}" frameDuration="1/{int(round(fps))}s"/>')

    # assets (only for rows with a Source File Path)
    asset_id = 2
    asset_refs: Dict[int, str] = {}
    for idx, ev in enumerate(evts):
        src_path = sanitize_string(ev.get("Source File Path", "")).strip()
        if not src_path:
            continue
        clip_name = sanitize_string(ev.get("Clip Name", "Untitled")).strip()
        src_tc = sanitize_string(ev.get("Source Clip start time code", "00:00:00:00"))
        src_tc_frames = tc_to_frames(src_tc, fps)
        try:
            orig_len = int(str(ev.get("Orig Source Clip length", "0")).strip() or "0")
        except Exception:
            orig_len = 0

        n1, d1 = frames_to_nd(src_tc_frames, fps)
        n2, d2 = frames_to_nd(orig_len, fps)
        aid = f"r{asset_id}"; asset_id += 1
        asset_refs[idx] = aid
        url = "file://localhost" + src_path.replace("\\", "/")
        L.append(f'        <asset format="r1" id="{aid}" audioChannels="1" audioSources="1" hasAudio="1" hasVideo="1" name="{clip_name}" start="{n1}/{d1}s" duration="{n2}/{d2}s">')
        L.append(f'            <media-rep src="{url}" kind="original-media"/>')
        L.append('        </asset>')
    L.append('    </resources>')

    # library → event → project → sequence
    L.append('    <library>')
    L.append(f'        <event name="{sanitize_string(timeline_name)}">')
    L.append(f'            <project name="{sanitize_string(timeline_name)}">')

    nseq, dseq = frames_to_nd(seq_start_frames, fps)
    nlen, dlen = frames_to_nd(seq_length_frames, fps)
    L.append(f'                <sequence format="r0" tcStart="{nseq}/{dseq}s" duration="{nlen}/{dlen}s" tcFormat="{tc_format}">')
    L.append('                    <spine>')

    # spine items
    last_end_rel = 0
    for idx, ev in enumerate(evts):
        aid = asset_refs.get(idx)
        if not aid:
            # no asset created (likely no file path) → skip clip
            continue

        evt_start_abs = tc_to_frames(ev.get("Timeline Start TC", "00:00:00:00"), fps)
        evt_start_rel = max(0, evt_start_abs - seq_start_frames)
        clip_len_frames = int(str(ev.get("Event Length", "0")).split(".")[0] or "0")
        clip_in_src = int(str(ev.get("StartTime (frames)", "0")).split(".")[0] or "0")

        # Insert a gap if there's a hole before this event
        if evt_start_rel > last_end_rel:
            ng, dg = frames_to_nd(evt_start_rel - last_end_rel, fps)
            ns, ds = frames_to_nd(last_end_rel, fps)
            L.append(f'                        <gap start="{ns}/{ds}s" offset="{ns}/{ds}s" duration="{ng}/{dg}s" name="Gap"/>')

        clip_name = sanitize_string(ev.get("Clip Name", "Untitled")).strip()
        no, do = frames_to_nd(evt_start_rel, fps)
        ns, ds = frames_to_nd(clip_in_src, fps)
        nd, dd = frames_to_nd(clip_len_frames, fps)
        L.append(f'                        <clip format="r0" enabled="1" offset="{no}/{do}s" name="{clip_name}" start="{ns}/{ds}s" duration="{nd}/{dd}s" tcFormat="{tc_format}">')

        # -------- keyframes (clip-relative) --------
        animated = parse_keyframes_block(ev.get("Keyframe Details", "") or "")

        # build tracks at local frame times
        sx, sy, s, px, py, rot, op = {}, {}, {}, {}, {}, {}, {}
        for pname, pts in animated.items():
            for absf, val in pts:
                local = absf - evt_start_abs
                if local < 0:
                    local = 0
                if local > clip_len_frames:
                    local = clip_len_frames
                fv = to_float_safe(val)
                if fv is None:
                    continue
                if name_is(pname, "scale", "x"):
                    sx[local] = normalize_scale(fv)
                elif name_is(pname, "scale", "y"):
                    sy[local] = normalize_scale(fv)
                elif name_is(pname, "scale"):
                    s[local] = normalize_scale(fv)
                elif name_is(pname, "pos", "x"):
                    px[local] = fv
                elif name_is(pname, "pos", "y"):
                    py[local] = fv
                elif name_is(pname, "rot"):
                    rot[local] = fv
                elif name_is(pname, "opacity"):
                    op[local] = (fv / 100.0) if fv > 1.0 else fv

        times = sorted(set(sx) | set(sy) | set(s) | set(px) | set(py) | set(rot) | set(op))
        tracks = {"scale": [], "position": [], "rotation": [], "opacity": []}
        last_sx, last_sy = 1.0, 1.0
        last_px, last_py = 0.0, 0.0
        last_rot = 0.0

        for t in times:
            # scale
            vx = sx.get(t); vy = sy.get(t); vs = s.get(t)
            if vx is None and vy is None and vs is not None:
                vx = vy = vs
            if vx is None:
                vx = last_sx
            if vy is None:
                vy = last_sy
            n, d = frames_to_nd(t, fps)
            tracks["scale"].append((n, d, f"{vx:g} {vy:g}"))
            last_sx, last_sy = vx, vy

            # position
            pxv = px.get(t, last_px); pyv = py.get(t, last_py)
            tracks["position"].append((n, d, f"{pxv:g} {pyv:g}"))
            last_px, last_py = pxv, pyv

            # rotation
            rv = rot.get(t, last_rot)
            tracks["rotation"].append((n, d, f"{rv:g}"))
            last_rot = rv

            # opacity (sparse)
            if t in op:
                tracks["opacity"].append((n, d, f"{op[t]:g}"))

        # Emit transform block
        if any(tracks[k] for k in ("scale", "position", "rotation")):
            base_scale = tracks["scale"][0][2] if tracks["scale"] else "1 1"
            base_pos   = tracks["position"][0][2] if tracks["position"] else "0 0"
            L.append(f'                            <adjust-transform scale="{base_scale}" position="{base_pos}" anchor="0 0">')
            if tracks["scale"]:
                v0 = tracks["scale"][0][2]
                L.append(f'                                <param name="scale" value="{v0}">')
                L.append('                                    <keyframeAnimation>')
                for n, d, val in tracks["scale"]:
                    L.append(f'                                        <keyframe time="{n}/{d}s" value="{val}"/>')
                L.append('                                    </keyframeAnimation>')
                L.append('                                </param>')
            if tracks["position"]:
                v0 = tracks["position"][0][2]
                L.append(f'                                <param name="position" value="{v0}">')
                L.append('                                    <keyframeAnimation>')
                for n, d, val in tracks["position"]:
                    L.append(f'                                        <keyframe time="{n}/{d}s" value="{val}"/>')
                L.append('                                    </keyframeAnimation>')
                L.append('                                </param>')
            if tracks["rotation"]:
                v0 = tracks["rotation"][0][2]
                L.append(f'                                <param name="rotation" value="{v0}">')
                L.append('                                    <keyframeAnimation>')
                for n, d, val in tracks["rotation"]:
                    L.append(f'                                        <keyframe time="{n}/{d}s" value="{val}"/>')
                L.append('                                    </keyframeAnimation>')
                L.append('                                </param>')
            L.append('                            </adjust-transform>')
        else:
            L.append('                            <adjust-transform scale="1 1" position="0 0" anchor="0 0"/>')

        # Opacity block
        if tracks["opacity"]:
            base = tracks["opacity"][0][2]
            L.append(f'                            <adjust-blend amount="{base}">')
            L.append(f'                                <param name="amount" value="{base}">')
            L.append('                                    <keyframeAnimation>')
            for n, d, val in tracks["opacity"]:
                L.append(f'                                        <keyframe time="{n}/{d}s" value="{val}"/>')
            L.append('                                    </keyframeAnimation>')
            L.append('                                </param>')
            L.append('                            </adjust-blend>')
        else:
            L.append('                            <adjust-blend amount="1"/>')

        # video leaf (attribute order: ref, offset, start, duration)
        nvs, dvs = frames_to_nd(clip_in_src, fps)
        nvd, dvd = frames_to_nd(clip_len_frames, fps)
        L.append(f'                            <video ref="{aid}" offset="{nvs}/{dvs}s" start="{nvs}/{dvs}s" duration="{nvd}/{dvd}s"/>')

        # minimal metadata (reel)
        reel_name = sanitize_string(max(ev.get("DiskLabel", "") or "",
                                        ev.get("TapeID", "") or "",
                                        ev.get("Source File Name", "") or "",
                                        key=len))
        L.append('                            <metadata>')
        L.append(f'                                <md key="com.apple.proapps.studio.reel" value="{reel_name}"/>')
        L.append('                            </metadata>')

        L.append('                        </clip>')

        last_end_rel = evt_start_rel + clip_len_frames

    L.append('                    </spine>')
    L.append('                </sequence>')
    L.append('            </project>')
    L.append('        </event>')
    L.append('    </library>')
    L.append('</fcpxml>')

    out_path.write_text("\n".join(L), encoding="utf-8")


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(description="Convert SuperEDL CSV to FCPXML 1.13 (with keyframes).")
    ap.add_argument("--in", dest="csv_in", required=True, help="Input CSV path")
    ap.add_argument("--out", dest="xml_out", required=True, help="Output FCPXML path")
    args = ap.parse_args()

    csv_path = Path(args.csv_in)
    xml_out = Path(args.xml_out)
    build_fcpxml_from_csv(csv_path, xml_out)
    print(f"✅ Wrote FCPXML: {xml_out}")

if __name__ == "__main__":
    main()
