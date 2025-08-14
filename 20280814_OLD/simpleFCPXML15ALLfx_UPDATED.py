#!/usr/bin/env python3
"""
simpleFCPXML.py — STRICT FORMAT-COMPATIBLE

A Tkinter GUI that:
  - Parses an AAF-style CSV report with keyframe data.
  - Generates FCPXML that matches known-good Resolve imports:
      • Attribute order harmonized with valid examples
      • Time units: /1s for sequence/clip/gap offsets & starts; /{fps}s for asset/video
      • media-rep attr order: src then kind
      • File URLs preserve drive letter colon and slashes (safe encoding)
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import csv, io, re
from urllib.parse import quote

# Globals (populated from CSV summary header)
timeline_name = ""
fps = 25.0
tc_format = "NDF"
timeline_start_frames = 0
timeline_length_frames = 0
events = []

# -------------------- Utilities --------------------

def sanitize_string(text: str) -> str:
    """Remove nulls/non-ASCII; return safe printable ASCII."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\x00", "")
    return text.encode("ascii", "ignore").decode("utf-8")

def tc_to_frames(tc: str) -> int:
    """Convert HH:MM:SS:FF to total frames at current fps."""
    if not tc or tc.strip().upper() == "N/A" or ":" not in tc:
        return 0
    parts = tc.split(":")
    while len(parts) < 4:
        parts.append("0")
    h, m, s, f = map(int, parts)
    return int((h * 3600 + m * 60 + s) * fps + f)

def frames_to_1s(frames: int) -> str:
    """Format as integer seconds with /1s (Resolve-friendly for offsets/starts on clips/gaps/sequence)."""
    return f"{int(frames // fps)}/1s"

def frames_to_fpss(frames: int) -> str:
    """Format as frames/{fps}s (assets & <video>)."""
    return f"{int(frames)}/{int(fps)}s"

def safe_file_url(native_path: str) -> str:
    """
    Build file://localhost URL preserving drive colon and slashes.
    - Keep ':' '/' '%' unencoded
    - Encode spaces and other unsafe chars
    """
    p = native_path.replace("\\", "/")
    # Do not encode ':', '/', '%'
    p_enc = quote(p, safe="/:%")
    return f"file://localhost{p_enc}"

def get_filename_for_check(event):
    """Prefer Source File Name; fallback to Clip Name."""
    source_name = sanitize_string(event.get("Source File Name", "")).strip()
    if source_name:
        return source_name
    clip_name = sanitize_string(event.get("Clip Name", "")).strip()
    return clip_name

def parse_keyframe_details(kf_string, clip_start_frames):
    """Parse the Keyframe Details block into {param:[{time,value},...]}."""
    if not kf_string or "No animated parameters" in kf_string:
        if not kf_string or "-> Value:" not in kf_string:
            return {}

    parsed = {}
    current = None
    for line in kf_string.strip().split("\n"):
        pm = re.search(r"Parameter:\s*(.+?)(?:\s*\(|\s*->)", line)
        if pm:
            current = pm.group(1).strip()
            parsed.setdefault(current, [])
            continue

        km = re.search(r"Keyframe at .*?\((\d+)f\)\s*->\s*Value:\s*(.*)", line)
        if km and current:
            frame = int(km.group(1))
            val_str = km.group(2).strip()
            try:
                if "/" in val_str:
                    num, den = val_str.split("/", 1)
                    value = float(num) / float(den)
                else:
                    value = float(val_str)
            except (ValueError, ZeroDivisionError):
                continue
            # Keyframes should be relative to the clip start in SEQUENCE time.
            rel = frame - clip_start_frames
            parsed[current].append({"time": rel, "value": value})
            continue

        sm = re.search(r"->\s*Value:\s*(.*)", line)
        if sm and current and not km:
            val_str = sm.group(1).strip()
            try:
                if "/" in val_str:
                    num, den = val_str.split("/", 1)
                    value = float(num) / float(den)
                else:
                    value = float(val_str)
            except (ValueError, ZeroDivisionError):
                continue
            parsed[current].append({"time": 0, "value": value})

    for k in parsed:
        parsed[k].sort(key=lambda x: x["time"])
    return parsed

def parse_timeline_summary(lines):
    """Fill globals from CSV header block (above the blank separator)."""
    global timeline_name, fps, tc_format, timeline_start_frames, timeline_length_frames
    for ln in lines:
        if ln.startswith("Timeline Name,"):
            timeline_name = sanitize_string(ln.split(",", 1)[1].strip())
        elif ln.startswith("Timeline Edit Rate,"):
            parts = ln.split(",", 1)[1].strip().split(" ")
            fps_val = parts[0]
            tc_format = parts[1].strip("()") if len(parts) > 1 else "NDF"
            try:
                fps = float(fps_val)
            except:
                fps = 25.0
        elif ln.startswith("Timeline Start,"):
            tc = ln.split(",", 1)[1].strip()
            timeline_start_frames = tc_to_frames(tc)
        elif ln.startswith("Timeline Length,"):
            m = re.search(r"\((\d+)\s+frames", ln)
            if m:
                timeline_length_frames = int(m.group(1))

# -------------------- GUI actions --------------------

def load_csv():
    global events
    path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")], title="Select CSV")
    if not path:
        return
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    try:
        idx = next(i for i, ln in enumerate(lines) if ln.strip() == "")
        parse_timeline_summary(lines[:idx])
        events = list(csv.DictReader(io.StringIO("".join(lines[idx + 1 : ]))))
        messagebox.showinfo("CSV Loaded", f"Timeline: {timeline_name}\nEvents: {len(events)}")
    except StopIteration:
        messagebox.showerror("CSV Format Error", "Could not find a blank line between summary and event rows.")

def create_fcpxml():
    if not events:
        messagebox.showwarning("No CSV", "Load CSV first.")
        return

    # Only process rows with an effect (like your prior logic)
    evts = [e for e in events if e.get("Effect Name") != "N/A"]
    evts.sort(key=lambda e: tc_to_frames(e.get("Timeline Start TC", "00:00:00:00")))

    save_path = filedialog.asksaveasfilename(defaultextension=".fcpxml",
                                             filetypes=[("FCPXML", "*.fcpxml")],
                                             title="Save FCPXML")
    if not save_path:
        return

    still_exts = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".dpx", ".psd", ".bmp")

    with open(save_path, "w", encoding="utf-8") as f:

        # --- XML header ---
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE fcpxml>\n')
        f.write('<fcpxml version="1.13">\n')

        # --- resources ---
        f.write("    <resources>\n")
        # Attribute order MUST match known-good: width, name, height, id, frameDuration
        # r0: 1920x1080 @ fps
        f.write(f'        <format width="1920" name="FFVideoFormat1080p{int(fps)}" height="1080" id="r0" frameDuration="1/{int(fps)}s"/>\n')
        # r1: 4096x2160 @ fps (safe default for UHD/4K; assets can still reference r0 where suitable)
        f.write(f'        <format width="4096" name="FFVideoFormat4096x2160p{int(fps)}" height="2160" id="r1" frameDuration="1/{int(fps)}s"/>\n')

        # Build assets, IDs starting after r1 → r2, r3, ...
        asset_id_counter = 2
        asset_map = {}

        for i, ev in enumerate(evts):
            src_path = sanitize_string(ev.get("Source File Path", "")).strip()
            if not src_path:
                continue

            filename = get_filename_for_check(ev)
            is_still = filename.lower().endswith(still_exts)

            # Use r0 for standard HD clips/images; r1 for 4K-ish. Without raster metadata, default video to r1 only if you prefer.
            # Here: use r0 for normal (most), stills also r0 (Resolve tolerates); adjust if you store raster in CSV.
            format_id = "r0"

            asset_id = f"r{asset_id_counter}"
            asset_map[i] = asset_id
            asset_id_counter += 1

            # Assets must use frames/{fps}s
            src_tc_frames = tc_to_frames(ev.get("Source Clip start time code", "00:00:00:00"))
            src_len_frames = 0
            try:
                src_len_frames = int(ev.get("Orig Source Clip length", "0").strip() or "0")
            except:
                src_len_frames = 0

            start_str = frames_to_fpss(src_tc_frames) if not is_still else "0/1s"
            duration_str = frames_to_fpss(src_len_frames) if not is_still else "0/1s"

            clip_name = sanitize_string(ev.get("Clip Name", "Untitled Asset")).strip()
            url = safe_file_url(src_path)

            # Order must be: duration, name, hasAudio, start, audioSources, hasVideo, format
            # For stills, omit audio attrs
            if is_still:
                f.write(
                    f'        <asset duration="{duration_str}" name="{clip_name}" hasVideo="1" format="{format_id}">\n'
                )
            else:
                f.write(
                    f'        <asset duration="{duration_str}" name="{clip_name}" hasAudio="1" start="{start_str}" audioSources="1" hasVideo="1" format="{format_id}">\n'
                )
            # media-rep order: src first, then kind
            f.write(f'            <media-rep src="{url}" kind="original-media"/>\n')
            f.write("        </asset>\n")

        f.write("    </resources>\n")

        # --- library / project / sequence ---
        f.write("    <library>\n")
        f.write(f'        <event name="{sanitize_string(timeline_name)}">\n')
        f.write(f'            <project name="{sanitize_string(timeline_name)}">\n')

        # sequence attrs order: tcStart, duration, tcFormat, format
        f.write(
            f'                <sequence tcStart="{frames_to_1s(timeline_start_frames)}" '
            f'duration="{frames_to_fpss(timeline_length_frames)}" tcFormat="{tc_format}" format="r0">\n'
        )
        f.write("                    <spine>\n")

        # Build spine: gaps + clips
        last_end = timeline_start_frames

        for i, ev in enumerate(evts):
            if i not in asset_map:
                continue

            evt_start = tc_to_frames(ev.get("Timeline Start TC", "00:00:00:00"))
            clip_len = int(ev.get("Event Length", "0") or 0)
            clip_in_src = int(ev.get("StartTime (frames)", "0") or 0)

            # Insert gap if needed; order: offset, duration, name, start
            gap_frames = evt_start - last_end
            if gap_frames > 0:
                f.write(
                    f'                        <gap offset="{frames_to_1s(last_end)}" '
                    f'duration="{frames_to_1s(gap_frames).replace("/1s","/1s")}" '
                    f'name="Gap" start="{frames_to_1s(timeline_start_frames)}"/>\n'
                )

            clip_name = sanitize_string(ev.get("Clip Name", "Untitled")).strip()
            asset_ref = asset_map[i]
            # A simple reel name heuristic (unchanged)
            reel_name = sanitize_string(
                max(
                    ev.get("DiskLabel", "") or "",
                    ev.get("TapeID", "") or "",
                    ev.get("Source File Name", "") or "",
                    key=len,
                )
            ).strip()

            # clip base attrs (order matches valid example)
            f.write(
                f'                        <clip offset="{frames_to_1s(evt_start)}" '
                f'duration="{frames_to_1s(clip_len)}" '
                f'name="{clip_name}" '
                f'start="{frames_to_1s(clip_in_src)}" '
                f'enabled="1" tcFormat="{tc_format}" format="r0">\n'
            )

            # ---- Effects / keyframes (unchanged behavior; only time formatting corrected) ----
            kfs = parse_keyframe_details(ev.get("Keyframe Details", ""), evt_start)

            transform_kfs, crop_kfs, opacity_kfs = {}, {}, {}
            if kfs:
                for param, lst in kfs.items():
                    up = param.upper()
                    if "POS_X" in up or up == "X":
                        transform_kfs.setdefault("position_x", []).extend(lst)
                    elif "POS_Y" in up or up == "Y":
                        transform_kfs.setdefault("position_y", []).extend(lst)
                    elif "SCALE_X" in up or "SCALE_Y" in up or "ZOOM FACTOR" in up:
                        transform_kfs.setdefault("scale", []).extend(lst)
                    elif "ROTATION" in up or "ROT_Z" in up:
                        transform_kfs.setdefault("rotation", []).extend(lst)
                    elif "CROP_LEFT" in up:
                        crop_kfs.setdefault("left", []).extend(lst)
                    elif "CROP_RIGHT" in up:
                        crop_kfs.setdefault("right", []).extend(lst)
                    elif "CROP_TOP" in up:
                        crop_kfs.setdefault("top", []).extend(lst)
                    elif "CROP_BOTTOM" in up:
                        crop_kfs.setdefault("bottom", []).extend(lst)
                    elif "OPACITY" in up:
                        opacity_kfs.setdefault("opacity", []).extend(lst)

            is_dve = "DVE_" in (ev.get("Effect Name", "") or "")
            is_panzoom = "PAN & ZOOM" in (ev.get("Effect Name", "") or "").upper()

            px = transform_kfs.get("position_x", [])
            py = transform_kfs.get("position_y", [])
            sc = transform_kfs.get("scale", [])
            rt = transform_kfs.get("rotation", [])

            first_px = px[0]["value"] if px else 0.0
            first_py_raw = py[0]["value"] if py else 0.0
            first_py = (-first_py_raw if is_dve else first_py_raw)

            # scale: pan&zoom uses absolute; others normalize percent
            first_scale_raw = sc[0]["value"] if sc else 1.0
            first_s = first_scale_raw if is_panzoom else (first_scale_raw / 100.0)
            first_sy = (-first_s if is_dve else first_s)

            first_rot = (-rt[0]["value"] if rt else 0.0)

            has_transform = px or py or sc or rt
            if has_transform:
                f.write(
                    f'                            <adjust-transform position="{first_px} {first_py}" '
                    f'scale="{first_s} {first_sy}" rotation="{first_rot}">\n'
                )
                if px or py:
                    all_frames = sorted(set([k["time"] for k in px] + [k["time"] for k in py]))
                    f.write('                                <param name="position" '
                            f'value="{first_px} {first_py}">\n'
                            '                                    <keyframeAnimation>\n')
                    for fr in all_frames:
                        vx = next((k["value"] for k in px if k["time"] == fr), first_px)
                        vy_raw = next((k["value"] for k in py if k["time"] == fr), first_py_raw)
                        vy = -vy_raw if is_dve else vy_raw
                        f.write(f'                                        <keyframe time="{frames_to_1s(fr)}" value="{vx} {vy}" curve="linear"/>\n')
                    f.write('                                    </keyframeAnimation>\n'
                            '                                </param>\n')
                if sc:
                    f.write(f'                                <param name="scale" value="{first_s} {first_sy}">\n'
                            '                                    <keyframeAnimation>\n')
                    for kf in sc:
                        val = kf["value"] if is_panzoom else (kf["value"] / 100.0)
                        vy = -val if is_dve else val
                        f.write(f'                                        <keyframe time="{frames_to_1s(kf["time"])}" value="{val} {vy}" curve="linear"/>\n')
                    f.write('                                    </keyframeAnimation>\n'
                            '                                </param>\n')
                if rt:
                    f.write('                                <param name="rotation" value="{}">\n'
                            '                                    <keyframeAnimation>\n'.format(first_rot))
                    for kf in rt:
                        f.write(f'                                        <keyframe time="{frames_to_1s(kf["time"])}" value="{-kf["value"]}" curve="linear"/>\n')
                    f.write('                                    </keyframeAnimation>\n'
                            '                                </param>\n')
                f.write('                            </adjust-transform>\n')
            else:
                # Minimal transform block (kept for structural parity)
                f.write('                            <adjust-transform position="0 0" scale="1 1" anchor="0 0"/>\n')

            if crop_kfs:
                left = crop_kfs.get("left", [{"value": 0}])[0]["value"]
                right = crop_kfs.get("right", [{"value": 0}])[0]["value"]
                top = crop_kfs.get("top", [{"value": 0}])[0]["value"]
                bottom = crop_kfs.get("bottom", [{"value": 0}])[0]["value"]
                f.write('                            <filter-video name="Crop">\n'
                        '                                <adjust-crop mode="trim">\n'
                        f'                                    <trim-rect left="{left}" top="{top}" right="{right}" bottom="{bottom}">\n')
                for pname in ["left", "right", "top", "bottom"]:
                    lst = sorted(crop_kfs.get(pname, []), key=lambda k: k["time"])
                    init_v = lst[0]["value"] if lst else 0
                    f.write(f'                                        <param name="{pname}" value="{init_v}">\n')
                    if lst:
                        f.write('                                            <keyframeAnimation>\n')
                        for kf in lst:
                            f.write(f'                                                <keyframe time="{frames_to_1s(kf["time"])}" value="{kf["value"]}" curve="linear"/>\n')
                        f.write('                                            </keyframeAnimation>\n')
                    f.write('                                        </param>\n')
                f.write('                                    </trim-rect>\n'
                        '                                </adjust-crop>\n'
                        '                            </filter-video>\n')

            if opacity_kfs:
                lst = sorted(opacity_kfs.get("opacity", []), key=lambda k: k["time"])
                init_v = lst[0]["value"] if lst else 100
                f.write('                            <filter-video name="Opacity">\n')
                f.write(f'                                <param name="opacity" value="{init_v}">\n')
                if lst:
                    f.write('                                    <keyframeAnimation>\n')
                    for kf in lst:
                        f.write(f'                                        <keyframe time="{frames_to_1s(kf["time"])}" value="{kf["value"]}" curve="linear"/>\n')
                    f.write('                                    </keyframeAnimation>\n')
                f.write('                                </param>\n')
                f.write('                            </filter-video>\n')

            # <video> must use frames/{fps}s; order: ref, offset, duration, start
            f.write(
                f'                            <video ref="{asset_ref}" '
                f'offset="{frames_to_fpss(clip_in_src)}" '
                f'duration="{frames_to_fpss(clip_len)}" '
                f'start="{frames_to_fpss(clip_in_src)}"/>\n'
            )

            # Reel metadata (kept from your logic)
            f.write("                            <metadata>\n")
            f.write(f'                                <md value="{reel_name}" key="com.apple.proapps.studio.reel"/>\n')
            f.write("                            </metadata>\n")

            f.write("                        </clip>\n")

            last_end = evt_start + clip_len

        f.write("                    </spine>\n")
        f.write("                </sequence>\n")
        f.write("            </project>\n")
        f.write("        </event>\n")
        f.write("    </library>\n")
        f.write("</fcpxml>\n")

    messagebox.showinfo("Done", f"FCPXML written:\n{save_path}")

# -------------------- Main --------------------

if __name__ == "__main__":
    root = tk.Tk()
    root.title("simpleFCPXML Creator — Strict Format")
    tk.Button(root, text="Load CSV", command=load_csv, width=25, height=2).pack(pady=10)
    tk.Button(root, text="Create XML", command=create_fcpxml, width=25, height=2).pack(pady=10)
    root.mainloop()
