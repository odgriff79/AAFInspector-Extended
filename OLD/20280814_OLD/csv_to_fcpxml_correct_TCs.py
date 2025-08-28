#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV → FCPXML (REL13) exporter

This matches the "correct" mapping you approved:
- sequence.tcStart          ← CSV Summary "Timeline Start"
- sequence.duration         ← CSV Summary "Timeline Length (frames)"
- head <gap>:
    offset & start          ← sequence.tcStart
    duration                ← first_event_abs - sequence.tcStart
- each <video> (no <clip> wrapper):
    offset                  ← row["Timeline Start TC"]  (absolute timeline time)
    start                   ← row["Source Clip offset (frames)"]
                               (fallback: StartTime(frames) - Source Clip start (frames))
    duration                ← row["Event Length"]
- each <asset>:
    start                   ← row["Source Clip start time code"]
    duration                ← row["Orig Source Clip length"]
- All time rationals use denominator = fps (e.g., /25s)
- Filler rows with no effect are skipped.

Usage:
    python csv_to_fcpxml_REL13.py -i mem1.csv -o out.fcpxml
"""

from __future__ import annotations
import argparse
import csv
import io
import re
from pathlib import Path
from typing import List, Tuple, Dict, Any


# ---------- IO & Parsing ----------

def read_csv_summary_and_events(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    # split summary and table at the first completely blank line
    lines = text.splitlines(True)
    sep_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == "":
            sep_idx = i
            break
    if sep_idx is None:
        raise ValueError("CSV missing blank line separator between summary and data table.")
    summary = [ln.strip() for ln in lines[:sep_idx] if ln.strip()]
    events = list(csv.DictReader(io.StringIO("".join(lines[sep_idx + 1:]))))
    return summary, events


def sanitize(s: Any) -> str:
    if s is None:
        return ""
    return str(s).replace("\x00", "").encode("ascii", "ignore").decode("utf-8")


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
                tc_format = parts[1].strip("()")
        elif ln.startswith("Timeline Start,"):
            start_tc = ln.split(",", 1)[1].strip()
        elif ln.startswith("Timeline Length,"):
            m = re.search(r"\((\d+)\s*frames\)", ln)
            if m:
                length_frames = int(m.group(1))
    return name, fps, tc_format, start_tc, length_frames


def tc_to_frames(tc: str, fps: float) -> int:
    """
    SMPTE timecode to frames (non-drop for 24/25/30 family).
    """
    if not tc or ":" not in tc:
        return 0
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return int(round((h * 3600 + m * 60 + s) * fps + f))


def frames_to_den(frames: int, fps: float) -> Tuple[int, int]:
    """
    Represent 'frames' at frame rate 'fps' as N/D seconds
    using denominator equal to fps, so one frame = 1/fps s.
    """
    den = int(round(fps))
    num = frames
    return num, den


def is_filler_to_skip(row: Dict[str, str]) -> bool:
    nm = str(row.get("Event Name", "") or "")
    mob = str(row.get("SourceMobID", "") or "")
    clip = str(row.get("Clip Name", "") or "")
    eff = str(row.get("Effect Name", "") or "").upper()
    filler = ("Filler" in nm) or ("FX_ON_FILLER" in mob) or ("placeholder" in clip)
    return filler and (eff in ("", "N/A", "NONE", "N A"))


# ---------- Core Builder ----------

def build_fcpxml_from_csv(csv_path: Path, out_path: Path) -> None:
    summary, events = read_csv_summary_and_events(csv_path)
    name, fps, tc_format, seq_start_tc, seq_length_frames = parse_summary(summary)

    # numeric sequence start (frames)
    seq_start_frames = tc_to_frames(seq_start_tc, fps)

    # filter + sort events by absolute timeline start
    events = [e for e in events if not is_filler_to_skip(e)]
    events.sort(key=lambda e: tc_to_frames(e.get("Timeline Start TC", "00:00:00:00"), fps))

    # head gap duration (frames) between sequence start and first event
    first_abs = tc_to_frames(events[0].get("Timeline Start TC", "00:00:00:00"), fps) if events else seq_start_frames
    head_gap_frames = max(0, first_abs - seq_start_frames)

    # ---- XML build ----
    L: List[str] = []
    L.append('<?xml version="1.0" encoding="UTF-8"?>')
    L.append("<!DOCTYPE fcpxml>")
    L.append('<fcpxml version="1.13">')
    L.append("    <resources>")
    L.append(
        f'        <format name="FFVideoFormat1080p{int(round(fps))}" frameDuration="1/{int(round(fps))}s" id="r0" height="1080" width="1920"/>'
    )

    # assets (one per event; simple and Resolve-safe)
    rid = 2
    asset_refs: Dict[int, str] = {}
    for i, e in enumerate(events):
        path = sanitize(e.get("Source File Path", "")).strip()
        if not path:
            continue
        clipname = sanitize(e.get("Clip Name", "Untitled"))
        src_tc = sanitize(e.get("Source Clip start time code", "00:00:00:00"))
        src_abs = tc_to_frames(src_tc, fps)
        try:
            src_len = int(str(e.get("Orig Source Clip length", "0")).split(".")[0] or "0")
        except Exception:
            src_len = 0

        n1, d1 = frames_to_den(src_abs, fps)
        n2, d2 = frames_to_den(src_len, fps)
        r = f"r{rid}"
        rid += 1
        asset_refs[i] = r
        url = "file://localhost" + path.replace("\\", "/")
        L.append(
            f'        <asset hasVideo="1" name="{clipname}" format="r0" start="{n1}/{d1}s" duration="{n2}/{d2}s" id="{r}">'
        )
        L.append(f'            <media-rep kind="original-media" src="{url}"/>')
        L.append("        </asset>")

    L.append("    </resources>")
    L.append("    <library>")
    L.append(f'        <event name="{sanitize(name)}">')
    L.append(f'            <project name="{sanitize(name)}">')

    nseq, dseq = frames_to_den(seq_start_frames, fps)
    nlen, dlen = frames_to_den(seq_length_frames, fps)
    L.append(
        f'                <sequence tcStart="{nseq}/{dseq}s" format="r0" tcFormat="{tc_format}" duration="{nlen}/{dlen}s">'
    )
    L.append("                    <spine>")

    # explicit head gap (ensures visible black AND plays nice with Resolve’s ruler)
    if head_gap_frames > 0:
        ng, dg = frames_to_den(head_gap_frames, fps)
        L.append(
            f'                        <gap name="Gap" offset="{nseq}/{dseq}s" start="{nseq}/{dseq}s" duration="{ng}/{dg}s"/>'
        )

    # emit each event as a direct <video> item (no <clip>), using absolute offsets
    for i, e in enumerate(events):
        rref = asset_refs.get(i)
        if not rref:
            continue

        evt_abs = tc_to_frames(e.get("Timeline Start TC", "00:00:00:00"), fps)
        try:
            evt_len = int(str(e.get("Event Length", "0")).split(".")[0] or "0")
        except Exception:
            evt_len = 0

        # Correct in-point: Source Clip offset (frames). Fallback: StartTime - Source Clip start
        try:
            src_off = int(str(e.get("Source Clip offset (frames)", "0")).split(".")[0] or "0")
        except Exception:
            st_frames = int(str(e.get("StartTime (frames)", "0")).split(".")[0] or "0")
            src_start_frames = tc_to_frames(sanitize(e.get("Source Clip start time code", "00:00:00:00")), fps)
            src_off = max(0, st_frames - src_start_frames)

        off_n, off_d = frames_to_den(evt_abs, fps)
        dur_n, dur_d = frames_to_den(evt_len, fps)
        st_n, st_d = frames_to_den(src_off, fps)
        clipname = sanitize(e.get("Clip Name", "Untitled"))

        L.append(
            f'                        <video name="{clipname}" offset="{off_n}/{off_d}s" start="{st_n}/{st_d}s" duration="{dur_n}/{dur_d}s" enabled="1" ref="{rref}">'
        )
        # neutral transform (kept minimal; your keyframe exporter can add real transforms later)
        L.append('                            <adjust-transform position="0 0" scale="1 1" anchor="0 0"/>')
        L.append("                        </video>")

    L.append("                    </spine>")
    L.append("                </sequence>")
    L.append("            </project>")
    L.append("        </event>")
    L.append("    </library>")
    L.append("</fcpxml>")

    out_path.write_text("\n".join(L), encoding="utf-8")


# ---------- CLI ----------

def main() -> None:
    ap = argparse.ArgumentParser(description="CSV → FCPXML (REL13 mapping)")
    ap.add_argument("-i", "--input", required=True, type=Path, help="Input CSV (SuperEDL export)")
    ap.add_argument("-o", "--output", required=True, type=Path, help="Output FCPXML path")
    args = ap.parse_args()

    build_fcpxml_from_csv(args.input, args.output)
    print(f"✅ Wrote FCPXML: {args.output}")


if __name__ == "__main__":
    main()
