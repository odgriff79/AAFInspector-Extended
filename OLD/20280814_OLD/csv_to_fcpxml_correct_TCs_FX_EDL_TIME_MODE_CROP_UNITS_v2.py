#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
csv_to_fcpxml_correct_TCs_FX_EDL_TIME_MODE_CROP_UNITS_v2.py

CSV → FCPXML (REL13) + Marker EDL (REL15)

- Defaults to Resolve-valid behavior:
    * Keyframe times: source_tc_start_seconds + (local_frames / fps),
      formatted as "{SECONDS}/1s" if an exact second, otherwise "{FRAMES}/{fps}s".
    * Position units: percent-of-height (small numbers like "3.7 -13.875").
- Accepts CLI flags --time-mode and --pos-units for compatibility:
    --time-mode: resolve_valid | abs | local
    --pos-units: percentH | pixels
- Emits <video ... start="0/1s"> and <param value="..." name="..."> (value-first).
- Adds curve="linear" to all emitted keyframes (transform, crop, opacity) to avoid "hold" interpretation.
- Animated position/scale/rotation/opacity + crop supported; flip/flop merged into scale sign.

"""

from __future__ import annotations
import csv, io, re, argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from collections import OrderedDict

# ---------------- Basics ----------------

def sanitize(s: Any) -> str:
    if s is None: return ""
    return str(s).replace("\x00","").encode("ascii","ignore").decode("utf-8")

def norm_ws(s: str) -> str:
    if not isinstance(s,str): s = "" if s is None else str(s)
    s = s.replace("\u00A0"," ").replace("\u2007"," ").replace("\u2009"," ")
    s = s.replace("\u2013","-").replace("\u2014","-").replace("•","-")
    return s

def fmt_float(v: float, places: int=3) -> str:
    s = f"{v:.{places}f}".rstrip("0").rstrip(".")
    return s or "0"

def fmt_pair(a: float, b: float, places: int=3) -> str:
    return f"{fmt_float(a,places)} {fmt_float(b,places)}"


# ---------------- Time ----------------

def tc_to_frames(tc: str, fps: float) -> int:
    if not tc or ":" not in tc: return 0
    h,m,s,f = (int(x) for x in tc.replace(";",":").split(":"))
    return int(round((h*3600 + m*60 + s)*fps + f))

def frames_to_den(frames: int, fps: float) -> Tuple[int,int]:
    den = int(round(fps))
    return int(frames), den

def frames_to_tc_ndf(frames: int, fps: float) -> str:
    frames = int(round(frames))
    f = frames % int(round(fps))
    sec_total = frames // int(round(fps))
    s = sec_total % 60
    mi = (sec_total // 60) % 60
    h = sec_total // 3600
    return f"{h:02d}:{mi:02d}:{s:02d}:{f:02d}"

def frames_to_tc_df(frames: int, fps: float) -> str:
    # 29.97/59.94 drop-frame formatting
    if abs(fps - 29.97) < 0.02:
        frames = int(round(frames))
        # classic DF calc for 29.97
        d = 17982; m = 1798
        ten = frames // d
        rem = frames % d
        drop = 18 * ten + (2 * ((rem - 2) // m) if rem >= 2 else 0)
        total = frames + drop
        f = int(total % 30)
        sec_total = int(total // 30)
        s = sec_total % 60
        mi = (sec_total // 60) % 60
        h = sec_total // 3600
        return f"{h:02d};{mi:02d};{s:02d};{f:02d}"
    if abs(fps - 59.94) < 0.02:
        half = frames // 2
        base = frames_to_tc_df(half, 29.97)
        h,m,s,f = [int(x) for x in base.replace(";",":").split(":")]
        f = f*2 + (frames % 2)
        return f"{h:02d};{m:02d};{s:02d};{f:02d}"
    return frames_to_tc_ndf(frames, fps).replace(":", ";")

def frames_to_tc(frames: int, fps: float, drop_frame: bool=False, df: Optional[bool]=None) -> str:
    if df is not None: drop_frame = df
    return frames_to_tc_df(frames, fps) if drop_frame else frames_to_tc_ndf(frames, fps)


# ---------------- CSV ----------------

def read_csv_summary_and_events(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(True)
    sep = None
    for i, ln in enumerate(lines):
        if ln.strip()=="":
            sep = i; break
    if sep is None:
        raise ValueError("CSV missing blank line between summary and table.")
    summary = [ln.strip() for ln in lines[:sep] if ln.strip()]
    events  = list(csv.DictReader(io.StringIO("".join(lines[sep+1:]))))
    return summary, events

def parse_summary(summary_lines: List[str]):
    name, fps, tcfmt, start_tc, length_frames = "Timeline", 25.0, "NDF", "00:00:00:00", 0
    for ln in summary_lines:
        if ln.startswith("Timeline Name,"):
            name = sanitize(ln.split(",",1)[1].strip())
        elif ln.startswith("Timeline Edit Rate,"):
            rhs = ln.split(",",1)[1].strip()
            parts = rhs.split()
            try: fps = float(parts[0])
            except: fps = 25.0
            if len(parts)>1: tcfmt = parts[1].strip("()").upper()
        elif ln.startswith("Timeline Start,"):
            start_tc = ln.split(",",1)[1].strip()
        elif ln.startswith("Timeline Length,"):
            m = re.search(r"\((\d+)\s*frames\)", ln)
            if m: length_frames = int(m.group(1))
    return name, fps, tcfmt, start_tc, length_frames


# ---------------- Param mapping ----------------

AFX_MAP = {
    "AFX_POS_X_U": ("pos","x"),
    "AFX_POS_Y_U": ("pos","y"),
    "AFX_CENTER_X_U": ("pos","x"),
    "AFX_CENTER_Y_U": ("pos","y"),

    "AFX_SCALE_X_U": ("scale","x"),
    "AFX_SCALE_Y_U": ("scale","y"),
    "AFX_SCALE_U":   ("scale",None),

    "AFX_ROTATION_U": ("rot", None),
    "AFX_OPACITY_U":  ("opacity", None),
    "Amount":         ("opacity", None),

    "AFX_CROP_LEFT_U":   ("crop","left"),
    "AFX_CROP_RIGHT_U":  ("crop","right"),
    "AFX_CROP_TOP_U":    ("crop","top"),
    "AFX_CROP_BOTTOM_U": ("crop","bottom"),
}

FUZZY = {
    "position x":("pos","x"), "posx":("pos","x"), "center x":("pos","x"),
    "position y":("pos","y"), "posy":("pos","y"), "center y":("pos","y"),
    "position":("pos",None), "center":("pos",None),

    "scale x":("scale","x"), "zoom x":("scale","x"),
    "scale y":("scale","y"), "zoom y":("scale","y"),
    "scale":("scale",None),  "zoom":("scale",None),

    "rotation":("rot",None), "rotate":("rot",None),

    "opacity":("opacity",None), "amount":("opacity",None),

    "crop left":("crop","left"), "crop right":("crop","right"),
    "crop top":("crop","top"),   "crop bottom":("crop","bottom"),
}

def classify_param_name(pname: str) -> Tuple[Optional[str], Optional[str]]:
    if not pname: return None, None
    key = pname.strip()
    if key in AFX_MAP: return AFX_MAP[key]
    low = key.lower()
    for needle, cls in FUZZY.items():
        if needle in low: return cls
    if low.endswith("_x") or low.endswith(" x"):
        return ("pos","x") if "pos" in low or "center" in low else ("scale","x")
    if low.endswith("_y") or low.endswith(" y"):
        return ("pos","y") if "pos" in low or "center" in low else ("scale","y")
    return None, None

def to_float_safe(v: Any) -> Optional[float]:
    try:
        s = str(v).strip()
        if "/" in s:
            a,b = s.split("/",1)
            return float(a)/float(b)
        return float(s)
    except:
        return None

def normalize_scale(v: Optional[float]) -> Optional[float]:
    if v is None: return None
    return v/100.0 if v>3.0 else v

def convert_for_fcpxml(group: str, raw: Any) -> Optional[float]:
    v = to_float_safe(raw)
    if v is None: return None
    if group=="opacity": return v/100.0 if v>1.0 else v
    if group=="scale":   return normalize_scale(v)
    return v


# ---------------- Position mapping ----------------

def avid_pos_to_pixels(px_raw: Any, py_raw: Any, width: int, height: int) -> Tuple[Optional[float], Optional[float]]:
    # Avid's 1000-unit grid, origin top-left, Y up → FCPXML/Resolve pixels (center origin, Y up).
    ax = to_float_safe(px_raw); ay = to_float_safe(py_raw)
    if ax is None and ay is None: return None, None
    rx = ax * (width/1000.0) if ax is not None else None
    ry = -(ay * (height/1000.0)) if ay is not None else None
    return rx, ry

def pixels_to_percentH(px: Optional[float], py: Optional[float], height: int) -> Tuple[Optional[float], Optional[float]]:
    if px is None and py is None: return None, None
    div = (height/100.0) if height else 10.8
    rx = (px/div) if px is not None else None
    ry = (py/div) if py is not None else None
    return rx, ry


# ---------------- Flip/Flop ----------------

def parse_flip_flags(effect_name: str) -> Tuple[bool,bool]:
    n = (effect_name or "").lower()
    if "flip flop" in n or "flip-flop" in n: return True, True
    h = ("flop" in n) or ("horizontal flip" in n)
    v = ("flip" in n and "flop" not in n) or ("vertical flip" in n)
    return h, v


# ---------------- Keyframe parsing ----------------

KF_PARAM_RE = re.compile(r"^-+\s*Parameter:\s*(.*?)\s*(?:\(\d+\s*keyframes?\))?\s*$", re.IGNORECASE)
KF_LINE_RE  = re.compile(r"Keyframe\s+at\s+.*?\((\d+)f\)\s*->\s*Value:\s*(.+?)\s*$", re.IGNORECASE)

def parse_keyframes_block(kf_text: str) -> Dict[str, List[Tuple[int,str]]]:
    txt = norm_ws(kf_text or "")
    animated: Dict[str, List[Tuple[int,str]]] = {}
    in_anim = False; cur = None
    for raw in txt.splitlines():
        line = raw.strip()
        if not line: continue
        if line.startswith("--- Animated Parameters ---"): in_anim=True; cur=None; continue
        if line.startswith("--- Static Parameters ---"):   in_anim=False; cur=None; continue
        m_param = KF_PARAM_RE.match(line)
        if m_param:
            cur = m_param.group(1).strip()
            if in_anim: animated.setdefault(cur, [])
            continue
        if in_anim and cur:
            m = KF_LINE_RE.search(line)
            if m:
                fr = int(m.group(1)); val = m.group(2).strip()
                animated[cur].append((fr, val))
    for k in animated: animated[k].sort(key=lambda t: t[0])
    return animated

def group_raw_kf_by_time(animated: Dict[str, List[Tuple[int,str]]]) -> "OrderedDict[int, Dict[str, Any]]":
    times = sorted({t for pts in animated.values() for t,_ in pts})
    grouped: "OrderedDict[int, Dict[str, Any]]" = OrderedDict(
        (t,{
            'posx':None,'posy':None,'scalex':None,'scaley':None,'rot':None,'op':None,
            'cropleft':None,'cropright':None,'croptop':None,'cropbottom':None
        }) for t in times
    )
    for pname, pts in animated.items():
        cls, axis = classify_param_name(pname)
        for t,val in pts:
            g = grouped[t]
            if cls=="pos":
                if axis=="x": g['posx']=val
                elif axis=="y": g['posy']=val
            elif cls=="scale":
                if axis=="x": g['scalex']=val
                elif axis=="y": g['scaley']=val
                else:
                    g['scalex']=val if g['scalex'] is None else g['scalex']
                    g['scaley']=val if g['scaley'] is None else g['scaley']
            elif cls=="rot":
                g['rot']=val
            elif cls=="opacity":
                g['op']=val
            elif cls=="crop":
                if axis=="left": g['cropleft']=val
                elif axis=="right": g['cropright']=val
                elif axis=="top": g['croptop']=val
                elif axis=="bottom": g['cropbottom']=val
    return grouped


# ---------------- Model ----------------

class EventModel:
    def __init__(self):
        self.clip_name = "Untitled"
        self.source_path = ""
        self.effect_name = "N/A"

        self.evt_abs = 0
        self.evt_len = 0
        self.src_off = 0

        self.asset_src_abs = 0
        self.asset_src_len = 0
        self.asset_tc_start_frames = 0  # source clip start TC in frames

        self.kf_raw_grouped: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
        self.tr_position: List[Tuple[int,str]] = []
        self.tr_scale:    List[Tuple[int,str]] = []
        self.tr_rotation: List[Tuple[int,str]] = []
        self.tr_opacity:  List[Tuple[int,str]] = []
        self.tr_crop_left:   List[Tuple[int,str]] = []
        self.tr_crop_right:  List[Tuple[int,str]] = []
        self.tr_crop_top:    List[Tuple[int,str]] = []
        self.tr_crop_bottom: List[Tuple[int,str]] = []
        self.scale_sign_x: int = 1
        self.scale_sign_y: int = 1


# ---------------- Build model ----------------

def build_model_from_csv(csv_path: Path, timeline_width: int=1920, timeline_height: int=1080,
                         pos_units: str="percentH") -> Tuple[Dict[str,Any], List[EventModel]]:
    summary_lines, events_csv = read_csv_summary_and_events(csv_path)
    name, fps, tc_format, seq_start_tc, seq_length_frames = parse_summary(summary_lines)
    drop_frame = (tc_format == "DF")
    seq_start_frames = tc_to_frames(seq_start_tc, fps)

    filler_flip_segments: List[Tuple[int,int,bool,bool]] = []

    def is_filler(row: Dict[str,str]) -> bool:
        nm = str(row.get("Event Name","") or "")
        mob = str(row.get("SourceMobID","") or "")
        clip= str(row.get("Clip Name","") or "")
        return ("Filler" in nm) or ("FX_ON_FILLER" in mob) or ("placeholder" in clip)

    rows: List[Dict[str,str]] = []
    for r in events_csv:
        eff = (r.get("Effect Name","") or "").strip()
        h,v = parse_flip_flags(eff)
        if is_filler(r):
            if h or v:
                abs_start = tc_to_frames(str(r.get("Timeline Start TC","00:00:00:00")), fps)
                try: dur = int(str(r.get("Event Length","0")).split(".")[0] or "0")
                except: dur = 0
                filler_flip_segments.append((abs_start, abs_start+max(0,dur), h, v))
            continue
        rows.append(r)

    rows.sort(key=lambda r: tc_to_frames(r.get("Timeline Start TC","00:00:00:00"), fps))
    first_abs = tc_to_frames(rows[0].get("Timeline Start TC","00:00:00:00"), fps) if rows else seq_start_frames
    head_gap_frames = max(0, first_abs - seq_start_frames)

    models: List[EventModel] = []
    for r in rows:
        M = EventModel()
        M.clip_name   = sanitize(r.get("Clip Name","Untitled"))
        M.source_path = sanitize(r.get("Source File Path","")).strip()
        M.effect_name = sanitize(r.get("Effect Name","N/A"))

        M.evt_abs = tc_to_frames(sanitize(r.get("Timeline Start TC","00:00:00:00")), fps)
        try: M.evt_len = int(str(r.get("Event Length","0")).split(".")[0] or "0")
        except: M.evt_len = 0

        try:
            M.src_off = int(str(r.get("Source Clip offset (frames)","0")).split(".")[0] or "0")
        except:
            st_frames = int(str(r.get("StartTime (frames)","0")).split(".")[0] or "0")
            src_start_frames = tc_to_frames(sanitize(r.get("Source Clip start time code","00:00:00:00")), fps)
            M.src_off = max(0, st_frames - src_start_frames)

        M.asset_src_abs = tc_to_frames(sanitize(r.get("Source Clip start time code","00:00:00:00")), fps)
        M.asset_tc_start_frames = M.asset_src_abs
        try:
            M.asset_src_len = int(str(r.get("Orig Source Clip length","0")).split(".")[0] or "0")
        except:
            M.asset_src_len = 0

        animated = parse_keyframes_block(r.get("Keyframe Details","") or "")
        M.kf_raw_grouped = group_raw_kf_by_time(animated)

        last_px = 0.0; last_py = 0.0
        last_sx = 1.0; last_sy = 1.0
        last_rot = 0.0

        for absf, comp in M.kf_raw_grouped.items():
            local = absf - M.evt_abs
            if local < 0: local = 0
            if local >= M.evt_len: local = max(0, M.evt_len-1)

            # position
            px_px, py_px = avid_pos_to_pixels(comp['posx'], comp['posy'], timeline_width, timeline_height)
            if px_px is None: px_px = last_px
            if py_px is None: py_px = last_py

            if pos_units == "percentH":
                px, py = pixels_to_percentH(px_px, py_px, timeline_height)
            else:  # pixels
                px, py = px_px, py_px

            M.tr_position.append((local, fmt_pair(px, py, 3)))
            last_px, last_py = px_px, py_px  # keep px for continuity

            # scale
            sx = convert_for_fcpxml("scale", comp['scalex']) if comp['scalex'] is not None else None
            sy = convert_for_fcpxml("scale", comp['scaley']) if comp['scaley'] is not None else None
            if sx is None and sy is None and comp['scalex'] is not None:
                s = convert_for_fcpxml("scale", comp['scalex']); sx, sy = s, s
            if sx is None: sx = last_sx
            if sy is None: sy = last_sy
            M.tr_scale.append((local, fmt_pair(sx, sy, 2)))
            last_sx, last_sy = sx, sy

            # rotation
            rv = convert_for_fcpxml("rot", comp['rot']) if comp['rot'] is not None else last_rot
            M.tr_rotation.append((local, fmt_float(rv, 2)))
            last_rot = rv

            # opacity
            if comp['op'] is not None:
                ov = convert_for_fcpxml("opacity", comp['op'])
                if ov is not None:
                    M.tr_opacity.append((local, fmt_float(ov, 3)))

            # crop
            def crop_val(v):
                f = to_float_safe(v)
                if f is None: return None
                if abs(f - int(round(f))) < 1e-6: return str(int(round(f)))
                return fmt_float(f, 3)

            if comp['cropleft']   is not None: M.tr_crop_left.append((local,   crop_val(comp['cropleft'])))
            if comp['cropright']  is not None: M.tr_crop_right.append((local,  crop_val(comp['cropright'])))
            if comp['croptop']    is not None: M.tr_crop_top.append((local,    crop_val(comp['croptop'])))
            if comp['cropbottom'] is not None: M.tr_crop_bottom.append((local, crop_val(comp['cropbottom'])))

        # flip/flop on clip and any overlapping filler
        h_clip, v_clip = parse_flip_flags(M.effect_name)
        h_sign, v_sign = (-1 if h_clip else 1), (-1 if v_clip else 1)
        for fs, fe, fh, fv in filler_flip_segments:
            if not (fe <= M.evt_abs or fs >= M.evt_abs + max(0,M.evt_len)):
                if fh: h_sign *= -1
                if fv: v_sign *= -1
        M.scale_sign_x, M.scale_sign_y = h_sign, v_sign

        if M.tr_scale:
            new = []
            for t, val in M.tr_scale:
                try:
                    sx_s, sy_s = val.split()
                    sx = float(sx_s); sy = float(sy_s)
                except:
                    sx, sy = 1.0, 1.0
                sx *= M.scale_sign_x; sy *= M.scale_sign_y
                new.append((t, fmt_pair(sx, sy, 2)))
            M.tr_scale = new

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
        "pos_units": pos_units,
    }
    return header, models


# ---------------- Writers ----------------

def write_fcpxml_from_model(header: Dict[str,Any], models: List[EventModel], out_path: Path,
                            time_mode: str="resolve_valid", export_crop: bool=True) -> None:
    fps = header["fps"]; tc_format = header["tc_format"]
    seq_start_frames = header["seq_start_frames"]; seq_length_frames = header["seq_length_frames"]
    head_gap_frames = header["head_gap_frames"]; name = header["name"]
    width = int(header.get("timeline_width",1920)); height=int(header.get("timeline_height",1080))

    def format_kf_time(M: EventModel, local_frames: int) -> str:
        """Resolve-valid, abs, or local time domains."""
        if time_mode == "resolve_valid":
            base_secs = M.asset_tc_start_frames / fps  # e.g., 01:00:00:00 -> 3600
            secs = base_secs + (local_frames / fps)
            # exact second?
            if abs(secs - round(secs)) < 1e-9:
                return f"{int(round(secs))}/1s"
            # otherwise frames/fps
            frames_total = int(round(secs * fps))
            return f"{frames_total}/{int(round(fps))}s"
        elif time_mode == "abs":
            t = M.evt_abs + local_frames
            if t >= M.evt_abs + max(0,M.evt_len): t = M.evt_abs + max(0, M.evt_len-1)
            if t < M.evt_abs: t = M.evt_abs
            n,d = frames_to_den(t, fps)
            return f"{n}/{d}s"
        else:  # local
            t = max(0, min(max(0,M.evt_len-1), local_frames))
            n,d = frames_to_den(t, fps)
            return f"{n}/{d}s"

    L: List[str] = []
    L += [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE fcpxml>',
        '<fcpxml version="1.13">',
        '    <resources>',
        f'        <format name="FFVideoFormat{height}p{int(round(fps))}" frameDuration="1/{int(round(fps))}s" id="r0" height="{height}" width="{width}"/>'
    ]
    rid = 2; asset_id_by_index: Dict[int,str] = {}
    for i,M in enumerate(models):
        if not M.source_path: continue
        n1,d1 = frames_to_den(M.asset_src_abs, fps)
        n2,d2 = frames_to_den(M.asset_src_len, fps)
        r = f"r{rid}"; rid += 1; asset_id_by_index[i]=r
        url = "file://localhost" + M.source_path.replace("\\","/")
        L += [
            f'        <asset hasVideo="1" name="{M.clip_name}" format="r0" start="{n1}/{d1}s" duration="{n2}/{d2}s" id="{r}">',
            f'            <media-rep kind="original-media" src="{url}"/>',
            '        </asset>'
        ]
    L += ['    </resources>', '    <library>', f'        <event name="{sanitize(name)}">', f'            <project name="{sanitize(name)}">']

    nseq,dseq = frames_to_den(seq_start_frames, fps)
    nlen,dlen = frames_to_den(seq_length_frames, fps)
    L += [f'                <sequence tcStart="{nseq}/{dseq}s" format="r0" tcFormat="{tc_format}" duration="{nlen}/{dlen}s">',
          '                    <spine>']

    # Optional head gap (if seq start precedes first event)
    if head_gap_frames > 0:
        ng,dg = frames_to_den(head_gap_frames, fps)
        L.append(f'                        <gap name="Gap" offset="{nseq}/{dseq}s" start="{nseq}/{dseq}s" duration="{ng}/{dg}s"/>')

    def write_kf_param(M: EventModel, name: str, track: List[Tuple[int,str]]):
        if not track: return
        L.append(f'                                <param value="{track[0][1]}" name="{name}">')
        L.append('                                    <keyframeAnimation>')
        for local, val in track:
            ts = format_kf_time(M, local)
            # <- add curve="linear" to ensure Resolve animates (no holds)
            L.append(f'                                        <keyframe time="{ts}" curve="linear" value="{val}"/>')
        L.append('                                    </keyframeAnimation>')
        L.append('                                </param>')

    for i,M in enumerate(models):
        asset_id = asset_id_by_index.get(i)
        if not asset_id: continue
        off_n,off_d = frames_to_den(M.evt_abs, fps)
        dur_n,dur_d = frames_to_den(M.evt_len, fps)
        st_n, st_d = frames_to_den(0, fps)  # FORCE start="0/1s"
        L.append(f'                        <video name="{M.clip_name}" offset="{off_n}/{off_d}s" start="{st_n}/{st_d}s" duration="{dur_n}/{dur_d}s" enabled="1" ref="{asset_id}">')

        # CROP
        if export_crop and (M.tr_crop_left or M.tr_crop_right or M.tr_crop_top or M.tr_crop_bottom):
            L.append('                            <adjust-crop mode="trim">')
            L.append('                                <trim-rect bottom="0" right="0" top="0" left="0">')
            def write_crop(side: str, track: List[Tuple[int,str]]):
                if not track: return
                L.append(f'                                    <param value="{track[0][1]}" name="{side}">')
                L.append('                                        <keyframeAnimation>')
                for local, val in track:
                    ts = format_kf_time(M, local)
                    L.append(f'                                            <keyframe time="{ts}" curve="linear" value="{val}"/>')
                L.append('                                        </keyframeAnimation>')
                L.append('                                    </param>')
            write_crop("left",   M.tr_crop_left)
            write_crop("right",  M.tr_crop_right)
            write_crop("top",    M.tr_crop_top)
            write_crop("bottom", M.tr_crop_bottom)
            L.append('                                </trim-rect>')
            L.append('                            </adjust-crop>')

        # TRANSFORM (anchor first, then scale & position base attrs)
        base_scale_attr = M.tr_scale[0][1] if M.tr_scale else f"{M.scale_sign_x:g} {M.scale_sign_y:g}"
        base_pos_attr   = M.tr_position[0][1] if M.tr_position else "0 0"
        L.append(f'                            <adjust-transform anchor="0 0" scale="{base_scale_attr}" position="{base_pos_attr}">')
        # Emit params in the "valid" order: scale → position → rotation
        write_kf_param(M, "scale",    M.tr_scale)
        write_kf_param(M, "position", M.tr_position)
        write_kf_param(M, "rotation", M.tr_rotation)
        L.append('                            </adjust-transform>')

        # OPACITY
        if M.tr_opacity:
            base_op = M.tr_opacity[0][1]
            L.append(f'                            <adjust-blend amount="{base_op}">')
            L.append(f'                                <param value="{base_op}" name="amount">')
            L.append('                                    <keyframeAnimation>')
            for local, val in M.tr_opacity:
                ts = format_kf_time(M, local)
                L.append(f'                                        <keyframe time="{ts}" curve="linear" value="{val}"/>')
            L.append('                                    </keyframeAnimation>')
            L.append('                                </param>')
            L.append('                            </adjust-blend>')
        else:
            L.append('                            <adjust-blend amount="1"/>')

        L.append('                        </video>')

    L += ['                    </spine>', '                </sequence>', '            </project>', '        </event>', '    </library>', '</fcpxml>']
    out_path.write_text("\n".join(L), encoding="utf-8")


def write_marker_edl_from_model(header: Dict[str,Any], models: List[EventModel],
                                out_path: Path, max_kf_per_event: Optional[int]=None) -> None:
    fps = header["fps"]; df = header["drop_frame"]; name = header["name"]
    L: List[str] = []
    L.append(f"TITLE: {name}_TIMELINE_MARKERS_FROM_CSV")
    L.append(f"FCM: {'DROP FRAME' if df else 'NON-DROP FRAME'}")
    L.append("")
    for idx, M in enumerate(models, start=1):
        rec_in_tc  = frames_to_tc(M.evt_abs, fps, df=df)
        rec_out_tc = frames_to_tc(M.evt_abs+1, fps, df=df)
        L.append(f"{idx:03d}  001      V     C        {rec_in_tc} {rec_out_tc} {rec_in_tc} {rec_out_tc}  ")
        L.append(f"# offset={rec_in_tc} dur={frames_to_tc(M.evt_len, fps, df=df)}")

        mirrors = []
        if M.scale_sign_x < 0: mirrors.append("H")
        if M.scale_sign_y < 0: mirrors.append("V")
        if mirrors: L.append(f"# MIRROR: {'&'.join(mirrors)}")

        # Build absolute lookup for reporting
        pos_at = {M.evt_abs + t: v for t, v in M.tr_position}
        scl_at = {M.evt_abs + t: v for t, v in M.tr_scale}
        rot_at = {M.evt_abs + t: v for t, v in M.tr_rotation}

        kf_count = 0
        for t, comp in M.kf_raw_grouped.items():
            avid_tc = frames_to_tc(t, fps, df=df)
            rel_tc  = frames_to_tc(max(0, min(M.evt_len-1, t - M.evt_abs)), fps, df=df)
            avid_parts = []
            if comp['posx'] is not None or comp['posy'] is not None:
                avid_parts.append(f"pos=({comp['posx'] or '-'}, {comp['posy'] or '-'})")
            if comp['scalex'] is not None or comp['scaley'] is not None:
                avid_parts.append(f"scale=({comp['scalex'] or '-'}, {comp['scaley'] or '-'})")
            if comp['rot'] is not None: avid_parts.append(f"rotation={comp['rot']}")
            res_parts = []
            if t in pos_at: res_parts.append(f'position="{pos_at[t]}"')
            if t in scl_at: res_parts.append(f'scale="{scl_at[t]}"')
            if t in rot_at: res_parts.append(f'rotation="{rot_at[t]}"')
            L.append(f"KF @ {avid_tc} (rel {rel_tc}) AVID: " + (", ".join(avid_parts) if avid_parts else "-"))
            L.append("         FCPXML: " + (", ".join(res_parts) if res_parts else "-"))
            kf_count += 1
            if max_kf_per_event and kf_count >= max_kf_per_event:
                break

        L.append(f"|C:ResolveColorBlue |M:EVENT{idx} - {M.clip_name} |D:1")
        L.append("")
    out_path.write_text("\n".join(L), encoding="utf-8")


# ---------------- CLI ----------------

def main() -> None:
    ap = argparse.ArgumentParser(description="CSV → FCPXML (REL13) + Marker EDL (REL15)")
    ap.add_argument("-i","--input", required=True, type=Path, help="Input CSV (SuperEDL v2 export)")
    ap.add_argument("-x","--xml",   required=True, type=Path, help="Output FCPXML path")
    ap.add_argument("-e","--edl",   required=True, type=Path, help="Output marker EDL path")
    ap.add_argument("--width",  type=int, default=1920, help="Timeline width (px)")
    ap.add_argument("--height", type=int, default=1080, help="Timeline height (px)")
    ap.add_argument("--max-kf", type=int, default=None, help="Cap markers per event in EDL (optional)")
    ap.add_argument("--time-mode", choices=["resolve_valid","abs","local"], default="resolve_valid",
                    help="Keyframe time domain: resolve_valid (default), abs, or local")
    ap.add_argument("--pos-units", choices=["percentH","pixels"], default="percentH",
                    help="Position units: percentH (default) or pixels")

    args = ap.parse_args()

    header, models = build_model_from_csv(args.input,
                                          timeline_width=args.width,
                                          timeline_height=args.height,
                                          pos_units=args.pos_units)
    write_fcpxml_from_model(header, models, args.xml,
                            time_mode=args.time_mode,
                            export_crop=True)
    write_marker_edl_from_model(header, models, args.edl, max_kf_per_event=args.max_kf)
    print(f"Wrote FCPXML: {args.xml}")
    print(f"Wrote EDL   : {args.edl}")
    print(f"Options     : time_mode={args.time_mode}, pos_units={args.pos_units}, size={args.width}x{args.height}")

if __name__ == "__main__":
    main()
