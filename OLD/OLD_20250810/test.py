#!/usr/bin/env python3
# AAF→Resolve FCPXML Builder (direct, no compressed JSON)
# v0.7.2 — Mirrors superEDL recursive logic for AAF, correct op→clip binding,
#           effect naming from Operation/_EFFECT_PLUGIN_*, UTF-16LE Filepath (P&Z),
#           robust SourceClip→FileSourceMob, safe JSON logs, slot tree dump, verbose trace.

import os, re, json, traceback
from fractions import Fraction
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from dataclasses import dataclass

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import aaf2  # pyaaf2 tested with 1.4.x
except Exception as e:
    raise SystemExit("pyaaf2>=1.4.0 required. pip install pyaaf2") from e

import xml.etree.ElementTree as ET
from xml.dom import minidom

APP_NAME = "AAF2ResolveFCPXML_GUI_v0_7_2"
LOG_PATH = os.path.join(os.path.expanduser("~"), "Documents", f"{APP_NAME}_log.txt")

# ---------------- Utilities ----------------

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def safe_name(s: Any) -> str:
    return str(s) if s is not None else ""

def lower_str(x):
    try: return str(x).lower()
    except: return ""

def starts_with_any(name: str, prefixes: List[str]) -> bool:
    n = (name or "").upper()
    return any(n.startswith(p) for p in prefixes)

def aaf_class_name(obj) -> str:
    try:
        cn = getattr(obj, "class_name", None)
        if cn: return str(cn)
    except:
        pass
    try: return obj.__class__.__name__
    except: return "Unknown"

def sanitize_filename(name: str) -> str:
    n = re.sub(r"[^0-9A-Za-z._ -]+", "_", (name or "placeholder")).strip()
    n = re.sub(r"_+", "_", n)
    return n[:120] or "placeholder"

def norm_media_uri(path: str) -> str:
    if not path: return ""
    if path.lower().startswith("file://"): return path
    p = path.replace("\\", "/")
    if p.startswith("//localhost"): return "file://" + p[2:]
    if p.startswith("/"): return f"file://localhost{p}"
    if len(p) > 1 and p[1] == ":": p = "/" + p
    return f"file://localhost{p}"

def nearest_int(x: float) -> int:
    return int(round(x))

def frames_to_fractional(frames: int, fps: Fraction) -> str:
    A = int(frames) * int(fps.denominator)
    B = int(fps.numerator)
    return f"{A}/{B}s"

def df_flag_from_rate(fps: Fraction) -> str:
    # simple NDF; extend if 30000/1001 DF is needed
    return "NDF"

def json_dump_safe(path: str, data: Any):
    def default(o):
        try:
            from aaf2 import rational as _rat
            if isinstance(o, _rat.AAFRational):
                return {"num": o.numerator, "den": o.denominator}
        except Exception:
            pass
        try:
            return str(o)
        except Exception:
            return "<unserializable>"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=default)

# ---------------- Data models ----------------

@dataclass
class Keyframe:
    time_frames: int
    pos_x: float = 0.0
    pos_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0

@dataclass
class Event:
    name: str
    rec_in_f: int
    rec_out_f: int
    duration_f: int
    source_path: Optional[str]
    source_name: Optional[str]
    effect_name: Optional[str]
    effect_convertible: bool
    keyframes: List[Keyframe]
    tape_id: Optional[str]
    disk_label: Optional[str]
    width: int
    height: int
    note: str = ""
    filler_fx_file: Optional[str] = None  # decoded path for Pan&Zoom on filler (rare)

@dataclass
class SequenceInfo:
    name: str
    start_tc_f: int
    fps: Fraction
    width: int
    height: int
    events: List[Event]

# ---------------- In-memory AAF extractor ----------------

class AAFInMemoryExtractor:
    def __init__(self, aaf_path: str):
        self.path = aaf_path
        self.f = None
        self.slot_scan: List[Dict[str, Any]] = []
        self.traversal_trace: List[str] = []
        self.effects_index: List[Dict[str, Any]] = []

    def __enter__(self):
        self.f = aaf2.open(self.path, 'r')
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.f:
            self.f.close()

    def _trace(self, msg: str):
        self.traversal_trace.append(msg)

    # ---- Slot helpers / skip rules ----

    def _slot_kind(self, seg) -> str:
        k = getattr(seg, "media_kind", None)
        if k: return lower_str(k)
        try:
            dd = getattr(seg, "data_def", None)
            if dd and getattr(dd, "name", None):
                return lower_str(dd.name)
        except:
            pass
        return ""

    def _slot_name(self, slot) -> str:
        try:
            return str(getattr(slot, "name", "")) or ""
        except:
            return ""

    def _should_skip_track(self, slot) -> bool:
        # mirror your original: skip Data track and A1..A8
        name_u = self._slot_name(slot).upper()
        if starts_with_any(name_u, [f"A{i}" for i in range(1,9)]):
            self._trace(f"skip slot by name '{name_u}' (audio A1..A8)")
            return True
        if "DATA TRACK" in name_u:
            self._trace("skip slot by label 'Data Track'")
            return True
        seg = getattr(slot, "segment", None)
        kind = self._slot_kind(seg)
        if "sound" in kind or "data" in kind:
            self._trace(f"skip slot by kind '{kind}'")
            return True
        return False

    # ---- Top level / meta ----

    def get_top_level_sequences(self) -> List[Any]:
        try:
            tops = list(self.f.content.toplevel())
            self._trace(f"toplevel count={len(tops)}")
            return tops
        except Exception as e:
            self._trace(f"toplevel() failed: {e}")
            try:
                cm = list(self.f.content.compositionmobs())
                self._trace(f"compositionmobs fallback count={len(cm)}")
                return cm
            except Exception as e2:
                self._trace(f"compositionmobs() failed: {e2}")
                return []

    def _slot_fps(self, slot, default_fps: Fraction) -> Fraction:
        try:
            rate = getattr(slot, "edit_rate", None)
            if rate:
                return Fraction(rate.numerator, rate.denominator)
        except:
            pass
        return default_fps

    def _sequence_meta(self, comp_mob) -> Tuple[Fraction, int]:
        fps = Fraction(25, 1)
        start_tc = 0
        for i, slot in enumerate(comp_mob.slots):
            try:
                fps = self._slot_fps(slot, fps)
                seg = slot.segment
                seg_class = aaf_class_name(seg)
                mk = self._slot_kind(seg)
                sname = self._slot_name(slot)
                self.slot_scan.append({"slot_index": i, "slot_name": sname, "media_kind": mk, "segment_class": seg_class})
                self._trace(f"slot[{i}] name='{sname}' kind={mk} seg={seg_class}")
            except Exception as e:
                self._trace(f"slot[{i}] scan error: {e}")
        for slot in comp_mob.slots:
            try:
                seg = slot.segment
                if aaf_class_name(seg) == "Timecode":
                    st = getattr(seg, "start", 0)
                    start_tc = int(st or 0)
            except:
                pass
        return fps, start_tc

    # ---- Common helpers ----

    def _comp_length_frames(self, comp, fps: Fraction) -> int:
        try:
            rate = getattr(comp, "edit_rate", None)
            r = Fraction(rate.numerator, rate.denominator) if rate else fps
            length = getattr(comp, "length", None)
            if length is not None:
                val = int(Fraction(length) * (fps / r))
                return max(0, val)
        except:
            pass
        return 0

    def _visit_len_and_rate(self, segment, fps: Fraction) -> Tuple[int, str]:
        rate = getattr(segment, "edit_rate", None)
        rate_s = f"{getattr(rate,'numerator',None)}/{getattr(rate,'denominator',None)}" if rate else "None"
        lnF = self._comp_length_frames(segment, fps)
        return lnF, rate_s

    # --- Source resolution (mob chain) ---

    def _is_class(self, mob, name: str) -> bool:
        try:
            return aaf_class_name(mob) == name
        except:
            return False

    def _sourceclip_start_mob(self, src_seg):
        try:
            src = getattr(src_seg, "source", None)
            return getattr(src, "mob", None) if src else None
        except Exception as e:
            self._trace(f"_sourceclip_start_mob error: {e}")
            return None

    def _resolve_to_filesource(self, mob):
        try:
            seen = set()
            cur = mob
            chain = [aaf_class_name(cur)] if cur else []
            while cur and id(cur) not in seen:
                seen.add(id(cur))
                if self._is_class(cur, "FileSourceMob"):
                    self._trace("  resolve chain: " + " → ".join(chain))
                    return cur
                next_mob = None
                for slot in getattr(cur, "slots", []) or []:
                    seg = getattr(slot, "segment", None)
                    src = getattr(seg, "source", None)
                    nmob = getattr(src, "mob", None) if src else None
                    if nmob:
                        next_mob = nmob
                        break
                if next_mob:
                    cur = next_mob
                    chain.append(aaf_class_name(cur))
                    continue
                self._trace("  resolve chain (terminal): " + " → ".join(chain))
                return cur
        except Exception as e:
            self._trace(f"_resolve_to_filesource failed: {e}")
        return mob

    def _essence_for_sourceclip(self, src_seg):
        try:
            start_mob = self._sourceclip_start_mob(src_seg)
            if not start_mob:
                self._trace("  SourceClip->source.mob is None")
                return None
            return self._resolve_to_filesource(start_mob)
        except Exception as e:
            self._trace(f"_essence_for_sourceclip error: {e}")
            return None

    def _get_media_locator(self, mob) -> Tuple[Optional[str], Optional[str]]:
        try:
            desc = getattr(mob, "descriptor", None)
            if desc and getattr(desc, "locators", None):
                for loc in desc.locators:
                    if "NetworkLocator" in safe_name(getattr(loc, "classdef", None).name):
                        url = getattr(loc, "url", None) or getattr(loc, "URLString", None)
                        if url:
                            path = url.replace("file://", "")
                            return path, os.path.basename(path.replace("\\", "/"))
        except:
            pass
        return None, None

    def _dims_from_descriptor(self, mob) -> Tuple[int, int]:
        w, h = 1920, 1080
        try:
            desc = getattr(mob, "descriptor", None)
            if desc:
                w = int(getattr(desc, "stored_width", w))
                h = int(getattr(desc, "stored_height", h))
        except:
            pass
        return w, h

    def _extract_disklabel(self, mob) -> Optional[str]:
        try:
            if hasattr(mob, "attributes"):
                for a in mob.attributes:
                    if safe_name(getattr(a, "name", "")).upper() == "_IMPORTSETTING":
                        s = str(getattr(a, "value", ""))
                        if "_IMPORTDISKLABEL" in s:
                            tail = s.split("_IMPORTDISKLABEL", 1)[-1][:200]
                            m = re.search(r"Value[^:]*:\s*'([^']+)'", tail) or re.search(r'Value[^:]*:\s*"([^"]+)"', tail)
                            return m.group(1) if m else tail.strip(" :[]'")
        except Exception as e:
            self._trace(f"disklabel extraction failed: {e}")
        return None

    def _extract_tapeid(self, mob) -> Optional[str]:
        try:
            if hasattr(mob, "user_comments"):
                for c in mob.user_comments:
                    nm = safe_name(getattr(c, "name", ""))
                    if "tape" in nm.lower():
                        return safe_name(getattr(c, "value", None))
        except Exception as e:
            self._trace(f"tapeid extraction failed: {e}")
        return None

    # ---- AVX helpers ----

    def _to_py(self, v):
        try:
            from aaf2 import rational as _rat
            if isinstance(v, _rat.AAFRational):
                return {"num": v.numerator, "den": v.denominator}
        except Exception:
            pass
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        if isinstance(v, (list, tuple)):
            return [self._to_py(x) for x in v]
        if hasattr(v, "items"):
            try:
                return {str(k): self._to_py(v[k]) for k in v}
            except Exception:
                pass
        return str(v)

    def _decode_utf16le_path(self, data) -> Optional[str]:
        try:
            if isinstance(data, (bytes, bytearray)):
                raw = bytes(data)
            elif isinstance(data, list) and all(isinstance(b, int) for b in data):
                raw = bytes(data)
            else:
                return None
            txt = raw.decode("utf-16-le", errors="ignore")
            idx = txt.find("\\"); idx = idx if idx != -1 else txt.find("/")
            if idx != -1: txt = txt[idx:]
            cleaned = txt.rstrip("\x00").replace("\\", "/")
            return cleaned or None
        except Exception:
            return None

    def _decode_avid_effect_id(self, data) -> Optional[str]:
        try:
            if isinstance(data, (bytes, bytearray)):
                raw = bytes(data)
            elif isinstance(data, list) and all(isinstance(b, int) for b in data):
                raw = bytes(data)
            else:
                return None
            txt = raw.decode("ascii", errors="ignore").strip("\x00").strip()
            return txt or None
        except Exception:
            return None

    def summarise_op_params(self, op_group) -> Dict[str, Any]:
        out = {
            "effect_name": "Unknown Effect",
            "length_edit_units": int(getattr(op_group, "length", 0) or 0),
            "animated_params": {},
            "static_params": {},
            "raw": {}
        }
        plugin_class = None
        plugin_name  = None
        avid_id_name = None

        # attributes
        try:
            attrs = getattr(op_group, "attributes", None)
            if attrs:
                for k in list(attrs.keys()):
                    kv = self._to_py(attrs[k])
                    out["raw"][f"attr:{k}"] = kv
                    if str(k) == "_EFFECT_PLUGIN_CLASS":
                        plugin_class = str(kv)
                    if str(k) == "_EFFECT_PLUGIN_NAME":
                        plugin_name = str(kv)
                    if str(k).lower() == "avideffectid":
                        avid_id_name = self._decode_avid_effect_id(kv) or avid_id_name
        except Exception:
            pass

        # component_attribute_list
        try:
            cal = getattr(op_group, "component_attribute_list", None)
            if cal:
                for k in list(cal.keys()):
                    kv = self._to_py(cal[k])
                    out["raw"][f"cal:{k}"] = kv
                    ks = str(k)
                    if ks == "_EFFECT_PLUGIN_CLASS" and not plugin_class:
                        plugin_class = str(kv)
                    if ks == "_EFFECT_PLUGIN_NAME" and not plugin_name:
                        plugin_name = str(kv)
                    if ks.lower() == "filepath":
                        pz = self._decode_utf16le_path(kv)
                        if pz: out["static_params"]["Filepath"] = pz
                    if ks.lower() == "avideffectid":
                        avid_id_name = self._decode_avid_effect_id(kv) or avid_id_name
        except Exception:
            pass

        # parameters (IMPORTANT: many Avids put AvidEffectID here)
        try:
            params = getattr(op_group, "parameters", None) or []
            for p in params:
                pname = safe_name(getattr(p, "name", "Param"))
                is_vary = bool(getattr(p, "is_varying", False))
                if pname.lower() == "avideffectid":
                    kv = self._to_py(getattr(p, "value", None))
                    avid_id_name = self._decode_avid_effect_id(kv) or avid_id_name
                if is_vary:
                    pts = list(getattr(p, "points", []) or [])
                    rec = []
                    for cp in pts:
                        t = getattr(cp, "time", None)
                        v = getattr(cp, "value", None)
                        if t is None or v is None:
                            continue
                        t_val = self._to_py(t); v_val = self._to_py(v)
                        rec.append({"time": t_val, "value": v_val})
                    if rec: out["animated_params"][pname] = rec
                else:
                    v = getattr(p, "value", None)
                    if v is not None:
                        out["static_params"][pname] = self._to_py(v)
        except Exception as e:
            self._trace(f"summarise_op_params params error: {e}")

        # operationdef fallback / preferred label
        try:
            opdef = getattr(op_group, "operationdef", None)
            if opdef and getattr(opdef, "name", None):
                opdef_name = safe_name(opdef.name)
                # Preferred: OperationDef human name (e.g., PaintResize_v2)
                out["effect_name"] = opdef_name
        except Exception:
            pass
        # If plugin info exists, use it to refine label
        if plugin_name or plugin_class:
            out["effect_name"] = " : ".join([x for x in [plugin_class, plugin_name] if x])
        # If we still want extra clarity and have AvidEffectID, append
        if avid_id_name and avid_id_name not in out["effect_name"]:
            out["effect_name"] = f"{out['effect_name']} [{avid_id_name}]".strip()

        return out

    # ---- Event builders / effect parsing ----

    def _build_event_from_source(self, src_seg, rec_in_f, rec_out_f, fps: Fraction) -> Event:
        duration_f = max(0, rec_out_f - rec_in_f)
        source_path, source_name = None, None
        disk_label, tape_id = None, None
        width, height = 1920, 1080
        try:
            start_mob = self._sourceclip_start_mob(src_seg)
            if start_mob:
                self._trace(f"  SourceClip start mob: {aaf_class_name(start_mob)}")
            essence_mob = self._resolve_to_filesource(start_mob) if start_mob else None
            if essence_mob:
                self._trace(f"  Resolved to: {aaf_class_name(essence_mob)}")
                disk_label = self._extract_disklabel(essence_mob)
                tape_id    = self._extract_tapeid(essence_mob)
                source_path, source_name = self._get_media_locator(essence_mob)
                width, height = self._dims_from_descriptor(essence_mob)
        except Exception as e:
            self._trace(f"source resolution failed: {e}")
        name = safe_name(getattr(src_seg, "name", "")) or source_name or "Clip"
        ev = Event(
            name=name, rec_in_f=rec_in_f, rec_out_f=rec_out_f, duration_f=duration_f,
            source_path=source_path, source_name=source_name,
            effect_name=None, effect_convertible=True, keyframes=[],
            tape_id=tape_id, disk_label=disk_label, width=width, height=height
        )
        self._trace(f"EVENT SourceClip name='{name}' start={rec_in_f} dur={duration_f} src='{source_name}' path='{source_path}'")
        return ev

    def _kf_time_to_frames(self, t, fps: Fraction, ev_len: int) -> int:
        try:
            if isinstance(t, dict) and "num" in t and "den" in t and t["den"]:
                sec = float(t["num"]) / float(t["den"])
                return nearest_int(sec * float(fps))
            if isinstance(t, int): return int(t)
            if isinstance(t, float):
                if t <= 1.5: return nearest_int(t * float(fps))
                return int(t)
        except:
            pass
        return 0

    def _extract_keyframes_from_op(self, op_group, ev_duration_f: int, fps: Fraction) -> List[Keyframe]:
        kfs: List[Keyframe] = []
        static_posx = 0.0; static_posy = 0.0
        static_sx   = 1.0; static_sy   = 1.0
        static_rot  = 0.0
        saw_any     = False
        params = getattr(op_group, "parameters", None)
        if not params:
            return [Keyframe(0), Keyframe(max(0, ev_duration_f-1))]
        for p in params:
            try:
                pname = safe_name(getattr(p, "name", "Param"))
                is_vary = bool(getattr(p, "is_varying", False))
                if is_vary:
                    pts = list(getattr(p, "points", []) or [])
                    for cp in pts:
                        t = getattr(cp, "time", None); v = getattr(cp, "value", None)
                        if t is None or v is None: continue
                        tF = self._kf_time_to_frames(self._to_py(t), fps, ev_duration_f)
                        tF = max(0, min(ev_duration_f-1, tF))
                        if isinstance(v, (list, tuple)) and len(v) >= 2 and "pos" in pname.lower():
                            kfs.append(Keyframe(tF, pos_x=float(v[0]), pos_y=float(v[1]),
                                                scale_x=static_sx, scale_y=static_sy, rotation=static_rot))
                            saw_any = True
                        elif "scale" in pname.lower() and isinstance(v, (int,float)):
                            kfs.append(Keyframe(tF, pos_x=static_posx, pos_y=static_posy,
                                                scale_x=float(v), scale_y=static_sy, rotation=static_rot))
                            saw_any = True
                        elif "rotation" in pname.lower() and isinstance(v,(int,float)):
                            kfs.append(Keyframe(tF, pos_x=static_posx, pos_y=static_posy,
                                                scale_x=static_sx, scale_y=static_sy, rotation=float(v)))
                            saw_any = True
                else:
                    v = getattr(p, "value", None)
                    if v is not None:
                        if isinstance(v, (list, tuple)) and len(v) >= 2 and "pos" in pname.lower():
                            static_posx, static_posy = float(v[0]), float(v[1]); saw_any = True
                        elif "scale" in pname.lower():
                            if isinstance(v,(int,float)):
                                static_sx = static_sy = float(v); saw_any = True
                            elif isinstance(v,(list,tuple)) and len(v)>=2:
                                static_sx, static_sy = float(v[0]), float(v[1]); saw_any = True
                        elif "rotation" in pname.lower() and isinstance(v,(int,float)):
                            static_rot = float(v); saw_any = True
            except:
                continue
        if not kfs and saw_any:
            kfs = [Keyframe(0, static_posx, static_posy, static_sx, static_sy, static_rot),
                   Keyframe(max(0, ev_duration_f-1), static_posx, static_posy, static_sx, static_sy, static_rot)]
        if not kfs:
            kfs = [Keyframe(0), Keyframe(max(0, ev_duration_f-1))]
        return sorted(kfs, key=lambda k: k.time_frames)

    # ---- OperationGroup input binding (mirror SuperEDL intent) ----

    def _bind_op_to_prior_source(self, result: List[Event], rec_cursor_f: int) -> Optional[Event]:
        """Find the nearest preceding SourceClip-derived event on the same timeline (in `result`)."""
        prior = [ev for ev in result if (ev.source_path or ev.source_name) and ev.rec_in_f <= rec_cursor_f]
        if not prior: return None
        prior.sort(key=lambda e: (e.rec_in_f, e.rec_out_f))
        cand = prior[-1]
        self._trace(f"  bind OperationGroup to prior source event '{cand.name}' [{cand.rec_in_f}-{cand.rec_out_f}] path='{cand.source_path}'")
        return cand

    # ---- Slot tree for debugging ----

    def _shallow_tree(self, node, depth=0, max_depth=6):
        try:
            if depth > max_depth:
                return {"type": "MAX_DEPTH"}
            cls = aaf_class_name(node)
            kind = self._slot_kind(node)
            length = getattr(node, "length", None)
            rate = getattr(node, "edit_rate", None)
            data_def = safe_name(getattr(getattr(node, "data_def", None), "name", ""))

            out = {
                "class": cls, "media_kind": kind, "data_def": data_def,
                "length": int(length) if isinstance(length, int) else safe_name(length),
                "edit_rate": {"num": getattr(rate,'numerator',None), "den": getattr(rate,'denominator',None)} if rate else None,
                "children": []
            }
            for attr in ("components", "input_segments", "slots"):
                try:
                    kids = list(getattr(node, attr, []) or [])
                    out["children"].append({"attr": attr, "count": len(kids),
                                            "items": [self._shallow_tree(k, depth+1, max_depth) for k in kids[:200]]})
                except:
                    pass
            return out
        except Exception as e:
            return {"error": f"{e}"}

    def write_slots_tree(self, comp_mob, out_base_path: str):
        slots_dump = []
        for i, slot in enumerate(comp_mob.slots):
            try:
                slots_dump.append({
                    "slot_index": i,
                    "slot_name": self._slot_name(slot),
                    "segment_tree": self._shallow_tree(slot.segment, 0, 6)
                })
            except Exception as e:
                slots_dump.append({"slot_index": i, "error": str(e)})
        json_dump_safe(out_base_path + "_slots_tree.json", slots_dump)

    # ---- Recursive linearizer ----

    def _linearize(self, segment, fps: Fraction, rec_cursor_f: int, result: List[Event]):
        cname = aaf_class_name(segment)
        mkind = self._slot_kind(segment)
        data_def = safe_name(getattr(getattr(segment, "data_def", None), "name", ""))
        lnF, rate_s = self._visit_len_and_rate(segment, fps)
        if cname == "ClassDefinition":
            self._trace("WARN: got meta-class 'ClassDefinition' — check node binding or class_name access.")
        self._trace(f"visit {cname} kind={mkind} data_def={data_def} lenF={lnF} edit_rate={rate_s} @rec={rec_cursor_f}")

        if cname == "Sequence":
            comps = list(getattr(segment, "components", []) or [])
            if comps:
                cur = rec_cursor_f
                self._trace(f"  sequence has {len(comps)} components")
                for comp in comps:
                    cln = self._comp_length_frames(comp, fps)
                    self._linearize(comp, fps, cur, result)
                    cur += cln
                return
            ins = list(getattr(segment, "input_segments", []) or [])
            if ins:
                cur = rec_cursor_f
                self._trace(f"  sequence had no 'components' but {len(ins)} 'input_segments'")
                for comp in ins:
                    cln = self._comp_length_frames(comp, fps)
                    self._linearize(comp, fps, cur, result)
                    cur += cln
                return

        if cname == "SourceClip":
            ev = self._build_event_from_source(segment, rec_cursor_f, rec_cursor_f+lnF, fps)
            result.append(ev)
            return

        if cname == "OperationGroup":
            eff_summary = self.summarise_op_params(segment)
            eff_summary["timeline_rec_in"] = rec_cursor_f
            eff_summary["length_frames_at_seq_rate"] = lnF
            self.effects_index.append(self._to_py(eff_summary))

            # pyaaf2 sometimes exposes no input_segments; in your test these ops sit on the clip.
            # Try children first…
            pic_in = None
            try:
                for s in list(getattr(segment, "input_segments", []) or []):
                    if aaf_class_name(s) == "SourceClip":
                        pic_in = s; break
            except:
                pass
            if not pic_in:
                try:
                    for s in list(getattr(segment, "components", []) or []):
                        if aaf_class_name(s) == "SourceClip":
                            pic_in = s; break
                except:
                    pass

            if pic_in is not None:
                ev = self._build_event_from_source(pic_in, rec_cursor_f, rec_cursor_f+lnF, fps)
            else:
                # Bind to nearest prior source event on the same track (mirrors superEDL behavior for segment FX)
                prior = self._bind_op_to_prior_source(result, rec_cursor_f)
                if prior:
                    ev = Event(
                        name=prior.name,
                        rec_in_f=rec_cursor_f, rec_out_f=rec_cursor_f+lnF, duration_f=lnF,
                        source_path=prior.source_path, source_name=prior.source_name,
                        effect_name=None, effect_convertible=True, keyframes=[],
                        tape_id=prior.tape_id, disk_label=prior.disk_label,
                        width=prior.width, height=prior.height
                    )
                else:
                    # true filler FX (rare in your projects)
                    ev = Event(
                        name="FX on Filler",
                        rec_in_f=rec_cursor_f, rec_out_f=rec_cursor_f+lnF, duration_f=lnF,
                        source_path=None, source_name=None,
                        effect_name=None, effect_convertible=False, keyframes=[],
                        tape_id=None, disk_label=None, width=1920, height=1080
                    )
                    try:
                        p = eff_summary.get("static_params", {}).get("Filepath")
                        if p: ev.filler_fx_file = p
                    except Exception:
                        pass

            fxname = eff_summary.get("effect_name") or "Unknown Effect"
            ev.effect_name = fxname
            ev.keyframes = self._extract_keyframes_from_op(segment, ev.duration_f, fps)
            ev.effect_convertible = bool(ev.keyframes)
            self._trace(f"EVENT FX '{ev.effect_name}' on {'source' if ev.source_path or ev.source_name else 'filler'} "
                        f"start={ev.rec_in_f} dur={ev.duration_f} kf={len(ev.keyframes)}")
            result.append(ev)

            # Keep walking to catch nested ops
            for attr in ("components", "input_segments"):
                for child in list(getattr(segment, attr, []) or []):
                    self._linearize(child, fps, rec_cursor_f, result)
            return

        # Generic containers: recurse children if any
        progressed = False
        for attr in ("components", "input_segments"):
            kids = list(getattr(segment, attr, []) or [])
            if kids:
                cur = rec_cursor_f
                for child in kids:
                    cln = self._comp_length_frames(child, fps)
                    self._linearize(child, fps, cur, result)
                    cur += cln
                progressed = True
        if not progressed:
            ev = Event(
                name=cname or "Component",
                rec_in_f=rec_cursor_f,
                rec_out_f=rec_cursor_f+lnF,
                duration_f=lnF,
                source_path=None, source_name=None, effect_name=None,
                effect_convertible=False, keyframes=[], tape_id=None, disk_label=None,
                width=1920, height=1080, note="non-source"
            )
            self._trace(f"EVENT NonSource '{cname}' start={ev.rec_in_f} dur={ev.duration_f}")
            result.append(ev)

    def _has_nested_sourceclip(self, node) -> bool:
        try:
            if aaf_class_name(node) == "SourceClip": return True
            for attr in ("components", "input_segments"):
                for c in list(getattr(node, attr, []) or []):
                    if self._has_nested_sourceclip(c): return True
        except:
            pass
        return False

    # ---- Public sequence extraction ----

    def extract_sequence(self, comp_mob) -> SequenceInfo:
        seq_name = safe_name(getattr(comp_mob, "name", "")) or "Sequence"
        fps, start_tc = self._sequence_meta(comp_mob)

        events: List[Event] = []
        for i, slot in enumerate(comp_mob.slots):
            try:
                if self._should_skip_track(slot): continue
                if "picture" not in self._slot_kind(slot.segment):
                    self._trace(f"skip slot[{i}] not picture-ish kind={self._slot_kind(slot.segment)}")
                    continue
            except:
                continue
            try:
                self._linearize(slot.segment, fps, 0, events)
            except Exception as e:
                self._trace(f"slot[{i}] traversal failed: {e}\n{traceback.format_exc()}")

        width, height = 1920, 1080
        for ev in events:
            if ev.width and ev.height:
                width, height = ev.width, ev.height
                break

        return SequenceInfo(
            name=seq_name, start_tc_f=start_tc, fps=fps, width=width, height=height, events=events
        )

# ---------------- AVX → Resolve transform ----------------

def avid_to_resolve_transform(ev: Event) -> List[Dict[str, Any]]:
    w = max(1, ev.width or 1920)
    h = max(1, ev.height or 1080)
    out = []
    seen = set()
    for k in ev.keyframes:
        t = max(0, min(ev.duration_f-1, k.time_frames))
        if t in seen: continue
        seen.add(t)
        sx = k.scale_x if k.scale_x <= 3.0 else (k.scale_x / 100.0)
        sy = k.scale_y if k.scale_y <= 3.0 else (k.scale_y / 100.0)
        out.append({
            "timeFrames": t, "x": (k.pos_x / w), "y": -(k.pos_y / h),
            "scaleX": sx, "scaleY": sy, "rotation": k.rotation or 0.0,
        })
    if not out:
        out = [
            {"timeFrames": 0, "x": 0.0, "y": 0.0, "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0},
            {"timeFrames": max(0, ev.duration_f-1), "x": 0.0, "y": 0.0, "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0},
        ]
    return sorted(out, key=lambda d: d["timeFrames"])

# ---------------- FCPXML builder ----------------

class FCPXMLBuilder:
    def __init__(self, seq: SequenceInfo, placeholder_ok: Optional[str], placeholder_bad: Optional[str]):
        self.seq = seq
        self.placeholder_ok = placeholder_ok if placeholder_ok and os.path.exists(placeholder_ok) else None
        self.placeholder_bad = placeholder_bad if placeholder_bad and os.path.exists(placeholder_bad) else None

        self.doc = ET.Element("fcpxml", version="1.13")
        self.resources = ET.SubElement(self.doc, "resources")
        self.asset_ids: Dict[str, str] = {}
        self.asset_counter = 0

        self.format_rid = "r0"
        self._declare_formats()

    def _declare_formats(self):
        ET.SubElement(self.resources, "format", {
            "id": self.format_rid,
            "name": f"FFVideoFormat{self.seq.width}x{self.seq.height}p{float(self.seq.fps):g}",
            "width": str(self.seq.width), "height": str(self.seq.height),
            "frameDuration": f"{self.seq.fps.denominator}/{self.seq.fps.numerator}s"
        })

    def _asset_for_path(self, path: str, name_hint: str, duration_f: int) -> str:
        key = path or f"__offline__/{name_hint}"
        if key in self.asset_ids:
            return self.asset_ids[key]
        self.asset_counter += 1
        rid = f"r{self.asset_counter+10}"
        attrs = {
            "id": rid, "name": safe_name(name_hint or os.path.basename(path or "Offline.png")),
            "hasVideo": "1", "format": self.format_rid,
            "duration": frames_to_fractional(max(1, duration_f), self.seq.fps),
        }
        asset = ET.SubElement(self.resources, "asset", attrs)
        if path:
            ET.SubElement(asset, "media-rep", {"src": norm_media_uri(path), "kind": "original-media"})
        self.asset_ids[key] = rid
        return rid

    def _asset_for_placeholder_fx(self, fx_name: str, convertible: bool, duration_f: int) -> str:
        png = self.placeholder_ok if convertible else self.placeholder_bad
        if png:
            return self._asset_for_path(png, os.path.basename(png), duration_f)
        base = sanitize_filename((fx_name or "FX") + "_placeholder.png")
        key = f"__placeholder__/{base}"
        if key in self.asset_ids: return self.asset_ids[key]
        self.asset_counter += 1
        rid = f"r{self.asset_counter+10}"
        ET.SubElement(self.resources, "asset", {
            "id": rid, "name": base, "hasVideo":"1", "format": self.format_rid,
            "duration": frames_to_fractional(max(1, duration_f), self.seq.fps)
        })
        self.asset_ids[key] = rid
        return rid

    def build(self) -> ET.ElementTree:
        lib = ET.SubElement(self.doc, "library")
        event = ET.SubElement(lib, "event", {"name": self.seq.name or "Sequence"})
        proj = ET.SubElement(event, "project", {"name": self.seq.name or "Sequence"})
        seq_el = ET.SubElement(proj, "sequence", {
            "format": self.format_rid,
            "tcStart": frames_to_fractional(self.seq.start_tc_f, self.seq.fps),
            "tcFormat": df_flag_from_rate(self.seq.fps)
        })
        spine = ET.SubElement(seq_el, "spine")

        playhead = 0
        for ev in self.seq.events:
            if ev.filler_fx_file:
                rid = self._asset_for_path(ev.filler_fx_file, os.path.basename(ev.filler_fx_file), ev.duration_f)
            elif ev.source_path:
                rid = self._asset_for_path(ev.source_path, ev.source_name or ev.name, ev.duration_f)
            else:
                rid = self._asset_for_placeholder_fx(ev.effect_name or ev.name, ev.effect_convertible, ev.duration_f)

            clip = ET.SubElement(spine, "asset-clip", {
                "name": ev.name or "Clip",
                "ref": rid,
                "duration": frames_to_fractional(ev.duration_f, self.seq.fps),
                "start": frames_to_fractional(playhead, self.seq.fps),
                "offset": frames_to_fractional(playhead, self.seq.fps),
                "format": self.format_rid
            })

            if ev.effect_name:
                kfs = avid_to_resolve_transform(ev)
                if kfs:
                    filt = ET.SubElement(clip, "filter", {"name": "transform"})
                    def add_anim_param(name, series):
                        param = ET.SubElement(filt, "param", {"name": name})
                        for kp in series:
                            ET.SubElement(param, "keyframe", {
                                "time": frames_to_fractional(playhead + kp["timeFrames"], self.seq.fps),
                                "value": f"{kp[name]:.6f}" if isinstance(kp[name], float) else str(kp[name])
                            })
                    add_anim_param("x",       [{"timeFrames": k["timeFrames"], "x": k["x"]} for k in kfs])
                    add_anim_param("y",       [{"timeFrames": k["timeFrames"], "y": k["y"]} for k in kfs])
                    add_anim_param("scaleX",  [{"timeFrames": k["timeFrames"], "scaleX": k["scaleX"]} for k in kfs])
                    add_anim_param("scaleY",  [{"timeFrames": k["timeFrames"], "scaleY": k["scaleY"]} for k in kfs])
                    add_anim_param("rotation",[{"timeFrames": k["timeFrames"], "rotation": k["rotation"]} for k in kfs])

            playhead += ev.duration_f
        return ET.ElementTree(self.doc)

    @staticmethod
    def serialize(tree: ET.ElementTree) -> str:
        ugly = ET.tostring(tree.getroot(), encoding="utf-8")
        pretty = minidom.parseString(ugly).toprettyxml(indent="    ", encoding="utf-8")
        text = pretty.decode("utf-8")
        return "\n".join([ln for ln in text.splitlines() if ln.strip() != ""])

# ---------------- Debug writers ----------------

def write_debug_jsons(out_fcpxml_path: str, seq: SequenceInfo, extractor: AAFInMemoryExtractor):
    base = os.path.splitext(out_fcpxml_path)[0]
    seq_dbg_path   = base + "_sequence_debug.json"
    ev_dbg_path    = base + "_events_debug.json"
    fx_idx_path    = base + "_effects_index.json"
    trace_txt_path = base + "_trace.txt"

    seq_dbg = {
        "sequence_name": seq.name,
        "fps_fraction": {"numerator": seq.fps.numerator, "denominator": seq.fps.denominator},
        "fps_float": float(seq.fps),
        "start_tc_frames": seq.start_tc_f,
        "dimensions": {"width": seq.width, "height": seq.height},
        "slots_scan": extractor.slot_scan,
        "traversal_trace_first_200": extractor.traversal_trace[:200],
        "events_count": len(seq.events),
    }
    json_dump_safe(seq_dbg_path, seq_dbg)

    events_dump = []
    for i, ev in enumerate(seq.events):
        events_dump.append({
            "index": i,
            "name": ev.name,
            "rec_in_frames": ev.rec_in_f,
            "rec_out_frames": ev.rec_out_f,
            "duration_frames": ev.duration_f,
            "source_path": ev.source_path,
            "source_name": ev.source_name,
            "tape_id": ev.tape_id,
            "disk_label": ev.disk_label,
            "width": ev.width, "height": ev.height,
            "effect_name": ev.effect_name,
            "effect_convertible": ev.effect_convertible,
            "filler_fx_file": ev.filler_fx_file,
            "keyframes": [
                {"time_frames": k.time_frames, "pos_x": k.pos_x, "pos_y": k.pos_y,
                 "scale_x": k.scale_x, "scale_y": k.scale_y, "rotation": k.rotation}
                for k in ev.keyframes
            ]
        })
    json_dump_safe(ev_dbg_path, events_dump)

    json_dump_safe(fx_idx_path, extractor.effects_index)

    with open(trace_txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(extractor.traversal_trace))

    return {
        "sequence_debug": seq_dbg_path,
        "events_debug": ev_dbg_path,
        "effects_index": fx_idx_path,
        "trace_txt": trace_txt_path,
    }

# ---------------- GUI ----------------

class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        self.aaf_path = tk.StringVar()
        self.out_path = tk.StringVar()
        self.placeholder_ok = tk.StringVar()
        self.placeholder_bad = tk.StringVar()

        frm = ttk.Frame(root, padding=10); frm.pack(fill="both", expand=True)
        def row(label, var, btn_text, cmd):
            r = ttk.Frame(frm); r.pack(fill="x", pady=4)
            ttk.Label(r, text=label, width=28).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(r, text=btn_text, command=cmd).pack(side="left")
            return r

        row("AAF file:", self.aaf_path, "Browse", self.browse_aaf)
        row("Output FCPXML:", self.out_path, "Save As", self.browse_out)
        row("Placeholder (convertible OK):", self.placeholder_ok, "Browse", self.browse_ok)
        row("Placeholder (other/non-conv):", self.placeholder_bad, "Browse", self.browse_bad)

        self.logbox = tk.Text(frm, height=20, width=112); self.logbox.pack(fill="both", expand=True, pady=6)
        run_row = ttk.Frame(frm); run_row.pack(fill="x")
        ttk.Button(run_row, text="Build FCPXML + Debug Logs", command=self.run).pack(side="left")
        ttk.Button(run_row, text="Open Log", command=self.open_log).pack(side="left", padx=6)

    def browse_aaf(self):
        p = filedialog.askopenfilename(title="Select AAF", filetypes=[("AAF files","*.aaf"),("All files","*.*")])
        if p: self.aaf_path.set(p)

    def browse_out(self):
        p = filedialog.asksaveasfilename(title="Save FCPXML", defaultextension=".fcpxml", filetypes=[("FCPXML","*.fcpxml")])
        if p: self.out_path.set(p)

    def browse_ok(self):
        p = filedialog.askopenfilename(title="Placeholder OK PNG", filetypes=[("PNG","*.png"),("All files","*.*")])
        if p: self.placeholder_ok.set(p)

    def browse_bad(self):
        p = filedialog.askopenfilename(title="Placeholder Other PNG", filetypes=[("PNG","*.png"),("All files","*.*")])
        if p: self.placeholder_bad.set(p)

    def _echo(self, s: str):
        log(s)
        try:
            self.logbox.insert("end", s + "\n"); self.logbox.see("end"); self.root.update_idletasks()
        except:
            pass

    def open_log(self):
        try: os.startfile(LOG_PATH)
        except: messagebox.showinfo("Log", LOG_PATH)

    def run(self):
        try:
            self.logbox.delete("1.0","end")
            aaf = self.aaf_path.get().strip()
            out = self.out_path.get().strip()
            okp = self.placeholder_ok.get().strip() or None
            badp = self.placeholder_bad.get().strip() or None

            if not aaf or not os.path.exists(aaf):
                messagebox.showerror("Error", "Select a valid AAF file."); return
            if not out:
                messagebox.showerror("Error", "Choose an output .fcpxml path."); return

            self._echo(f"Opening AAF: {aaf}")
            with AAFInMemoryExtractor(aaf) as ex:
                comps = ex.get_top_level_sequences()
                if not comps:
                    raise RuntimeError("No Top-Level Composition Mobs found")
                self._echo(f"Top-Level comps found: {len(comps)}")
                for idx, m in enumerate(comps):
                    self._echo(f"  [{idx}] name={getattr(m, 'name', '(unnamed)')} class={aaf_class_name(m)}")
                comp = comps[0]
                self._echo(f"Sequence: {getattr(comp, 'name','(unnamed)')}  class={aaf_class_name(comp)}")
                seq = ex.extract_sequence(comp)

                base = os.path.splitext(out)[0]
                ex.write_slots_tree(comp, base)
                self._echo(f"  - slots_tree: {base}_slots_tree.json")

            self._echo(f"Events: {len(seq.events)}  FPS={float(seq.fps)}  Size={seq.width}x{seq.height}")
            for i, ev in enumerate(seq.events[:25]):
                self._echo(f"  [{i}] {ev.name} recIn={ev.rec_in_f} recOut={ev.rec_out_f} durF={ev.duration_f} "
                           f"eff={ev.effect_name} src={ev.source_name or ev.filler_fx_file or 'N/A'}")

            dbg_paths = write_debug_jsons(out, seq, ex)
            self._echo("Wrote debug files:")
            for k, p in dbg_paths.items():
                self._echo(f"  - {k}: {p}")

            self._echo("Building FCPXML...")
            builder = FCPXMLBuilder(seq, okp, badp)
            tree = builder.build()
            xml_text = builder.serialize(tree)

            with open(out, "w", encoding="utf-8") as f:
                f.write(xml_text)
            self._echo(f"OK: wrote {out}")

        except Exception as e:
            tb = traceback.format_exc()
            self._echo(f"ERROR: {e}\n{tb}")
            messagebox.showerror("Error", f"{e}\n\nSee log for details.")

def main():
    log(f"=== {APP_NAME} start ===")
    root = tk.Tk(); App(root); root.mainloop()
    log(f"=== {APP_NAME} end ===")

if __name__ == "__main__":
    main()
