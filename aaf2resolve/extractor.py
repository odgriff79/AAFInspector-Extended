# aaf2resolve/extractor.py
import os, re, json, traceback
from fractions import Fraction
from typing import Dict, List, Optional, Any, Tuple

import aaf2

from .utils import (
    log, safe_name, lower_str, starts_with_any, aaf_class_name,
    nearest_int, frames_to_fractional, json_dump_safe
)
from .models import Keyframe, Event, SequenceInfo

class AAFInMemoryExtractor:
    def __init__(self, aaf_path: str):
        self.path = aaf_path
        self.f = None
        self.slot_scan: List[Dict[str, Any]] = []
        self.traversal_trace: List[str] = []
        self.effects_index: List[Dict[str, Any]] = []
        self._mob_by_id: Dict[str, Any] = {}

    def __enter__(self):
        self.f = aaf2.open(self.path, 'r')
        try:
            self._index_all_mobs()
        except Exception as e:
            self._trace(f"mob index failed: {e}")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.f:
            self.f.close()

    # --------------------------------- logging ---------------------------------
    def _trace(self, msg: str):
        self.traversal_trace.append(msg)

    # ------------------------- reverse index of all mobs ------------------------
    def _mob_uid_of(self, mob) -> Optional[str]:
        for attr in ("mob_id","mobID","id","mobid","mob_uid"):
            try:
                v = getattr(mob, attr, None)
                if v: return str(v)
            except: pass
        try:
            r = repr(mob)
            m = re.search(r"([0-9a-fA-F-]{16,})", r)
            if m: return m.group(1)
        except: pass
        return None

    def _index_all_mobs(self):
        total = 0
        def add_all(seq):
            nonlocal total
            for m in (seq or []):
                mid = self._mob_uid_of(m)
                if mid and mid not in self._mob_by_id:
                    self._mob_by_id[mid] = m
                    total += 1

        try: add_all(list(self.f.content.mobs()))
        except: pass
        try: add_all(list(self.f.content.compositionmobs()))
        except: pass
        try: add_all(list(self.f.content.mastermobs()))
        except: pass
        # last-resort sweep
        try:
            storage = getattr(self.f.content, "storage", None)
            if storage:
                add_all(list(storage.mobs()))
        except: pass

        self._trace(f"mob index: {len(self._mob_by_id)} entries (added={total})")

    def _lookup_mob_by_umid(self, umid_like: Any):
        if not umid_like: return None
        key = str(umid_like)
        if key in self._mob_by_id:
            return self._mob_by_id[key]
        for k in self._mob_by_id.keys():
            if key.lower() in k.lower() or k.lower() in key.lower():
                return self._mob_by_id[k]
        return None

    # ----------------------------- slot helpers --------------------------------
    def _slot_kind(self, seg) -> str:
        k = getattr(seg, "media_kind", None)
        if k: return lower_str(k)
        try:
            dd = getattr(seg, "data_def", None)
            if dd and getattr(dd, "name", None):
                return lower_str(dd.name)
        except: pass
        return ""

    def _slot_name(self, slot) -> str:
        try:
            return str(getattr(slot, "name", "")) or ""
        except: return ""

    def _should_skip_track(self, slot) -> bool:
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

    # ----------------------------- top-level comps ------------------------------
    def get_top_level_sequences(self) -> List[Any]:
        try:
            tops = list(self.f.content.toplevel()); self._trace(f"toplevel count={len(tops)}"); return tops
        except Exception as e:
            self._trace(f"toplevel() failed: {e}")
            try:
                cm = list(self.f.content.compositionmobs()); self._trace(f"compositionmobs fallback count={len(cm)}"); return cm
            except Exception as e2:
                self._trace(f"compositionmobs() failed: {e2}")
                return []

    # ---------------------------- sequence metadata -----------------------------
    def _slot_fps(self, slot, default_fps: Fraction) -> Fraction:
        try:
            rate = getattr(slot, "edit_rate", None)
            if rate: return Fraction(rate.numerator, rate.denominator)
        except: pass
        return default_fps

    def _sequence_meta(self, comp_mob) -> Tuple[Fraction, int]:
        fps = Fraction(25,1); start_tc = 0
        for i, slot in enumerate(comp_mob.slots):
            try:
                fps = self._slot_fps(slot, fps)
                seg = slot.segment
                self.slot_scan.append({"slot_index": i, "slot_name": self._slot_name(slot),
                                       "media_kind": self._slot_kind(seg), "segment_class": aaf_class_name(seg)})
                self._trace(f"slot[{i}] name='{self._slot_name(slot)}' kind={self._slot_kind(seg)} seg={aaf_class_name(seg)}")
            except Exception as e:
                self._trace(f"slot[{i}] scan error: {e}")
        for slot in comp_mob.slots:
            try:
                seg = slot.segment
                if aaf_class_name(seg) == "Timecode":
                    start_tc = int(getattr(seg, "start", 0) or 0)
            except: pass
        return fps, start_tc

    # ---------------------------- common utilities ------------------------------
    def _comp_length_frames(self, comp, fps: Fraction) -> int:
        try:
            rate = getattr(comp, "edit_rate", None)
            r = Fraction(rate.numerator, rate.denominator) if rate else fps
            length = getattr(comp, "length", None)
            if length is not None:
                val = int(Fraction(length) * (fps / r))
                return max(0, val)
        except: pass
        return 0

    def _visit_len_and_rate(self, segment, fps: Fraction) -> Tuple[int, str]:
        rate = getattr(segment, "edit_rate", None)
        rate_s = f"{getattr(rate,'numerator',None)}/{getattr(rate,'denominator',None)}" if rate else "None"
        lnF = self._comp_length_frames(segment, fps)
        return lnF, rate_s

    # ------------------------------ source resolve ------------------------------
    def _sourceclip_start_mob(self, src_seg):
        try:
            src = getattr(src_seg, "source", None)
            return getattr(src, "mob", None) if src else None
        except Exception as e:
            self._trace(f"_sourceclip_start_mob error: {e}")
            return None

    def _resolve_to_filesource(self, mob):
        try:
            seen = set(); cur = mob; chain = [aaf_class_name(cur)] if cur else []
            while cur and id(cur) not in seen:
                seen.add(id(cur))
                if aaf_class_name(cur) == "FileSourceMob":
                    self._trace("  resolve chain: " + " → ".join(chain))
                    return cur
                next_mob = None
                for slot in getattr(cur, "slots", []) or []:
                    seg = getattr(slot, "segment", None)
                    src = getattr(seg, "source", None)
                    nmob = getattr(src, "mob", None) if src else None
                    if nmob: next_mob = nmob; break
                if not next_mob: break
                cur = next_mob; chain.append(aaf_class_name(cur))
            self._trace("  resolve chain (terminal): " + " → ".join(chain))
            return cur
        except Exception as e:
            self._trace(f"_resolve_to_filesource failed: {e}")
        return mob

    def _resolve_sourceclip_via_ids(self, src_seg):
        umid = None; slot_id = None

        # (1) direct subscript (low-level)
        for key in ("SourceID","MobID","SourceMobID"):
            try:
                v = src_seg[key].value
                if v: umid = str(v)
            except: pass
        try:
            slot_id = int(src_seg["SourceMobSlotID"].value)
        except: pass

        # (2) properties() fallback
        try:
            for p in getattr(src_seg, "properties", []) or []:
                pn = safe_name(getattr(p, "name", ""))
                if pn in ("SourceID","MobID","SourceMobID") and not umid:
                    umid = safe_name(getattr(p, "value", None))
                if pn == "SourceMobSlotID" and slot_id is None:
                    try: slot_id = int(getattr(p, "value", None))
                    except: pass
        except: pass

        self._trace(f"  fallback via ids: UMID={umid} slot={slot_id}")
        mob = self._lookup_mob_by_umid(umid)
        if not mob: return None
        return self._resolve_to_filesource(mob)

    def _essence_for_sourceclip(self, src_seg):
        try:
            start_mob = self._sourceclip_start_mob(src_seg)
            if not start_mob:
                self._trace("  SourceClip->source.mob is None; trying UMID/slot fallback")
                return self._resolve_sourceclip_via_ids(src_seg)
            return self._resolve_to_filesource(start_mob)
        except Exception as e:
            self._trace(f"_essence_for_sourceclip error: {e}")
            return None

    def _get_media_locator(self, mob) -> Tuple[Optional[str], Optional[str]]:
        try:
            desc = getattr(mob, "descriptor", None)
            if desc and getattr(desc, "locators", None):
                for loc in desc.locators:
                    try:
                        cdef = getattr(getattr(loc, "classdef", None), "name", "")
                    except: cdef = ""
                    if "NetworkLocator" in safe_name(cdef):
                        url = getattr(loc, "url", None) or getattr(loc, "URLString", None)
                        if url:
                            path = url.replace("file://", "")
                            return path, os.path.basename(path.replace("\\", "/"))
        except: pass
        return None, None

    def _dims_from_descriptor(self, mob) -> Tuple[int, int]:
        w, h = 1920, 1080
        try:
            desc = getattr(mob, "descriptor", None)
            if desc:
                w = int(getattr(desc, "stored_width", w))
                h = int(getattr(desc, "stored_height", h))
        except: pass
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
                    if "tape" in lower_str(getattr(c, "name", "")):
                        return safe_name(getattr(c, "value", None))
        except Exception as e:
            self._trace(f"tapeid extraction failed: {e}")
        return None

    # -------------------------------- AVX helpers -------------------------------
    def _to_py(self, v):
        try:
            from aaf2 import rational as _rat
            if isinstance(v, _rat.AAFRational):
                return {"num": v.numerator, "den": v.denominator}
        except Exception: pass
        if isinstance(v, (bytes, bytearray)): return bytes(v)
        if isinstance(v, (str, int, float, bool)) or v is None: return v
        if isinstance(v, (list, tuple)): return [self._to_py(x) for x in v]
        if hasattr(v, "items"):
            try: return {str(k): self._to_py(v[k]) for k in v}
            except Exception: pass
        return str(v)

    def _decode_utf16le_path(self, data) -> Optional[str]:
        try:
            raw = bytes(data) if isinstance(data, (bytes, bytearray)) else (bytes(data) if isinstance(data, list) else None)
            if raw is None: return None
            txt = raw.decode("utf-16-le", errors="ignore")
            idx = txt.find("\\"); idx = idx if idx != -1 else txt.find("/")
            if idx != -1: txt = txt[idx:]
            cleaned = txt.rstrip("\x00").replace("\\", "/")
            return cleaned or None
        except Exception: return None

    def _decode_avid_effect_id(self, data) -> Optional[str]:
        try:
            raw = bytes(data) if isinstance(data, (bytes, bytearray)) else (bytes(data) if isinstance(data, list) else None)
            if raw is None: return None
            txt = raw.decode("ascii", errors="ignore").strip("\x00").strip()
            return txt or None
        except Exception: return None

    def _iter_tagged_values(self, container) -> List[Tuple[str, Any]]:
        out = []
        if not container: return out

        # (A) dict-like
        try:
            if hasattr(container, "keys"):
                for k in list(container.keys()):
                    try:
                        v = container[k]
                        out.append((safe_name(k), self._to_py(v)))
                    except: continue
                if out: return out
        except: pass

        # (B) list of TV objects with .name/.value (or properties Name/Value)
        try:
            for tv in list(container) or []:
                nm, val = None, None
                try: nm = safe_name(getattr(tv, "name", None))
                except: pass
                try: val = getattr(tv, "value", None)
                except: pass
                if nm is not None and val is not None:
                    out.append((nm, self._to_py(val))); continue
                try:
                    props = getattr(tv, "properties", []) or []
                    nm_candidate = None; val_candidate = None
                    for p in props:
                        pn = safe_name(getattr(p, "name", ""))
                        if pn == "Name": nm_candidate = self._to_py(getattr(p, "value", None))
                        elif pn == "Value": val_candidate = self._to_py(getattr(p, "value", None))
                    if nm_candidate is not None:
                        out.append((safe_name(nm_candidate), val_candidate)); continue
                except: pass
                s = safe_name(tv)
                m1 = re.search(r"Name[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']", s)
                m2 = re.search(r"Value[\"']?\s*[:=]\s*[\"']?(.+?)[\"']?(?:,|]|$)", s)
                if m1: out.append((m1.group(1), m2.group(1) if m2 else None))
        except: pass

        # (C) low-level property vector access: node["ComponentAttributeList"]
        try:
            cal_prop = container if isinstance(container, aaf2.core.AAFObject) else None
            if cal_prop and "ComponentAttributeList" in [p.name for p in cal_prop.properties]:
                vec = cal_prop["ComponentAttributeList"].value
                for tv in list(vec) or []:
                    try:
                        k = safe_name(tv["Name"].value); v = self._to_py(tv["Value"].value)
                        out.append((k, v))
                    except: continue
        except: pass

        return out

    def _classify_from_params(self, static_params: Dict[str, Any]) -> Optional[str]:
        keys = set(static_params.keys())
        has_afx = any(k.startswith("AFX_") for k in keys)
        has_dve = any(k.startswith("DVE_") for k in keys)
        if has_afx: return "AVX2 Effect : Resize"
        if has_dve: return "AVX2 Effect : 3DWarp"
        return None

    def summarise_op_params(self, op_group) -> Dict[str, Any]:
        out = {"effect_name": "Unknown Effect", "length_edit_units": int(getattr(op_group, "length", 0) or 0),
               "animated_params": {}, "static_params": {}, "raw": {}}
        plugin_class = plugin_name = plugin_type = None
        avid_id_name = None

        # ComponentAttributeList (vector of TaggedValues)
        try:
            cal = getattr(op_group, "component_attribute_list", None)
            if cal is None and isinstance(op_group, aaf2.core.AAFObject):
                if "ComponentAttributeList" in [p.name for p in op_group.properties]:
                    cal = op_group["ComponentAttributeList"].value
            for (k, v) in self._iter_tagged_values(cal):
                out["raw"][f"cal:{k}"] = v
                lk = lower_str(k)
                if k == "_EFFECT_PLUGIN_CLASS": plugin_class = safe_name(v)
                elif k == "_EFFECT_PLUGIN_NAME": plugin_name  = safe_name(v)
                elif k == "_EFFECT_PLUGIN_TYPE": plugin_type  = safe_name(v)
                elif lk == "filepath":
                    pz = self._decode_utf16le_path(v)
                    if pz: out["static_params"]["Filepath"] = pz
                elif lk == "avideffectid":
                    avid_id_name = self._decode_avid_effect_id(v) or avid_id_name
        except Exception as e:
            self._trace(f"component_attribute_list parse error: {e}")

        # attributes (sometimes mirrored)
        try:
            attrs = getattr(op_group, "attributes", None)
            for (k, v) in self._iter_tagged_values(attrs):
                out["raw"][f"attr:{k}"] = v
                lk = lower_str(k)
                if k == "_EFFECT_PLUGIN_CLASS" and not plugin_class: plugin_class = safe_name(v)
                elif k == "_EFFECT_PLUGIN_NAME" and not plugin_name:  plugin_name  = safe_name(v)
                elif k == "_EFFECT_PLUGIN_TYPE" and not plugin_type:  plugin_type  = safe_name(v)
                elif lk == "avideffectid" and not avid_id_name:
                    avid_id_name = self._decode_avid_effect_id(v) or avid_id_name
        except Exception as e:
            self._trace(f"attributes parse error: {e}")

        # parameters
        try:
            params = getattr(op_group, "parameters", None) or []
            for p in params:
                pname = safe_name(getattr(p, "name", "Param"))
                is_vary = bool(getattr(p, "is_varying", False))
                if lower_str(pname) == "avideffectid":
                    kv = self._to_py(getattr(p, "value", None))
                    avid_id_name = self._decode_avid_effect_id(kv) or avid_id_name
                if is_vary:
                    pts = list(getattr(p, "points", []) or [])
                    rec = []
                    for cp in pts:
                        t = getattr(cp, "time", None); v = getattr(cp, "value", None)
                        if t is None or v is None: continue
                        rec.append({"time": self._to_py(t), "value": self._to_py(v)})
                    if rec: out["animated_params"][pname] = rec
                else:
                    v = getattr(p, "value", None)
                    if v is not None: out["static_params"][pname] = self._to_py(v)
        except Exception as e:
            self._trace(f"summarise_op_params params error: {e}")

        # OperationDef (preferred human label)
        opdef_name = None
        try:
            opdef = getattr(op_group, "operationdef", None) or getattr(op_group, "operation", None)
            if opdef and getattr(opdef, "name", None):
                opdef_name = safe_name(opdef.name)
        except: pass

        label = opdef_name or " : ".join([x for x in [plugin_class, plugin_name] if x]) or plugin_type
        if not label:
            guess = self._classify_from_params(out["static_params"])
            if guess: label = guess
        if not label: label = "Unknown Effect"
        if avid_id_name and avid_id_name not in label:
            label = f"{label} [{avid_id_name}]"
        out["effect_name"] = label
        return out

    # ------------------------------- inputs scan --------------------------------
    def _gather_inputs(self, op_group) -> Dict[str, Any]:
        info = {"scope_refs": [], "sourceclips": []}

        def harvest(seg):
            if aaf_class_name(seg) == "Sequence":
                for comp in list(getattr(seg, "components", []) or []):
                    c = aaf_class_name(comp)
                    if c == "ScopeReference":
                        rel = None
                        try:
                            for p in getattr(comp, "properties", []) or []:
                                if safe_name(getattr(p,"name","")) in ("RelativeScope","RelativeSlot","RelativeTrack"):
                                    rel = self._to_py(getattr(p, "value", None))
                        except: pass
                        info["scope_refs"].append({"relative": rel})
                    elif c == "SourceClip":
                        info["sourceclips"].append(comp)
            elif aaf_class_name(seg) == "SourceClip":
                info["sourceclips"].append(seg)

        # high-level
        for attr in ("input_segments","components"):
            try:
                for s in list(getattr(op_group, attr, []) or []):
                    harvest(s)
            except: pass

        # low-level property vector: InputSegments
        try:
            if isinstance(op_group, aaf2.core.AAFObject) and "InputSegments" in [p.name for p in op_group.properties]:
                vec = op_group["InputSegments"].value
                for s in list(vec) or []:
                    harvest(s)
        except: pass

        return info

    # ---------------------- pseudo-compressed snapshot dump ---------------------
    def _pseudo_comp_node(self, node, depth=0, max_depth=4):
        if depth > max_depth: return ["MAX_DEPTH","Meta",None,[]]
        cls = aaf_class_name(node); items = []
        for attr in ("components","input_segments","slots"):
            try:
                kids = list(getattr(node, attr, []) or [])
                for k in kids[:128]:
                    items.append(self._pseudo_comp_node(k, depth+1, max_depth))
            except: pass
        try:
            cal = getattr(node, "component_attribute_list", None)
            if cal:
                tvs = []
                for k,v in self._iter_tagged_values(cal): tvs.append([k,"Property",self._to_py(v)])
                items.append(["ComponentAttributeList","StrongRefVectorProperty",None,tvs])
        except: pass
        try:
            attrs = getattr(node, "attributes", None)
            if attrs:
                tvs = []
                for k,v in self._iter_tagged_values(attrs): tvs.append([k,"Property",self._to_py(v)])
                items.append(["Attributes","StrongRefVectorProperty",None,tvs])
        except: pass
        try:
            dd = getattr(node, "data_def", None)
            if dd and getattr(dd, "name", None): items.append(["DataDefinition","WeakRefProperty",safe_name(dd)])
        except: pass
        try:
            ln = getattr(node, "length", None)
            if ln is not None: items.append(["Length","Property",int(ln) if isinstance(ln,int) else safe_name(ln)])
        except: pass
        return [safe_name(cls),"ClassDefinition",None,items]

    # -------------------------------- traversal ---------------------------------
    def _bind_op_to_prior_source(self, result: List[Event], rec_cursor_f: int) -> Optional[Event]:
        prior = [ev for ev in result if (ev.source_path or ev.source_name) and ev.rec_in_f <= rec_cursor_f]
        if not prior: return None
        prior.sort(key=lambda e: (e.rec_in_f, e.rec_out_f))
        cand = prior[-1]
        self._trace(f"  bind OperationGroup to prior source event '{cand.name}' [{cand.rec_in_f}-{cand.rec_out_f}] path='{cand.source_path}'")
        return cand

    def _linearize(self, segment, fps: Fraction, rec_cursor_f: int, result: List[Event]):
        cname = aaf_class_name(segment)
        mkind = self._slot_kind(segment)
        data_def = safe_name(getattr(getattr(segment, "data_def", None), "name", ""))  # Picture/Sound/Data
        lnF, rate_s = self._visit_len_and_rate(segment, fps)
        self._trace(f"visit {cname} kind={mkind} data_def={data_def} lenF={lnF} edit_rate={rate_s} @rec={rec_cursor_f}")

        if cname == "Sequence":
            comps = list(getattr(segment, "components", []) or [])
            if comps:
                cur = rec_cursor_f; self._trace(f"  sequence has {len(comps)} components")
                for comp in comps:
                    cln = self._comp_length_frames(comp, fps)
                    self._linearize(comp, fps, cur, result); cur += cln
                return
            ins = list(getattr(segment, "input_segments", []) or [])
            if ins:
                cur = rec_cursor_f; self._trace(f"  sequence had no 'components' but {len(ins)} 'input_segments'")
                for comp in ins:
                    cln = self._comp_length_frames(comp, fps)
                    self._linearize(comp, fps, cur, result); cur += cln
                return

        if cname == "SourceClip":
            ev = self._build_event_from_source(segment, rec_cursor_f, rec_cursor_f+lnF, fps)
            result.append(ev); return

        if cname == "OperationGroup":
            eff_summary = self.summarise_op_params(segment)
            eff_summary["timeline_rec_in"] = rec_cursor_f
            eff_summary["length_frames_at_seq_rate"] = lnF
            try: eff_summary["pseudo_comp"] = self._pseudo_comp_node(segment, 0, 2)
            except: pass
            self.effects_index.append(self._to_py(eff_summary))

            inputs = self._gather_inputs(segment)
            pic_in = inputs["sourceclips"][0] if inputs["sourceclips"] else None

            if pic_in is not None:
                ev = self._build_event_from_source(pic_in, rec_cursor_f, rec_cursor_f+lnF, fps)
            else:
                cand = self._bind_op_to_prior_source(result, rec_cursor_f) if inputs["scope_refs"] else None
                if cand:
                    ev = Event(
                        name=cand.name, rec_in_f=rec_cursor_f, rec_out_f=rec_cursor_f+lnF, duration_f=lnF,
                        source_path=cand.source_path, source_name=cand.source_name, effect_name=None,
                        effect_convertible=True, keyframes=[], tape_id=cand.tape_id, disk_label=cand.disk_label,
                        width=cand.width, height=cand.height
                    )
                else:
                    ev = Event(
                        name="FX on Filler", rec_in_f=rec_cursor_f, rec_out_f=rec_cursor_f+lnF, duration_f=lnF,
                        source_path=None, source_name=None, effect_name=None, effect_convertible=False, keyframes=[],
                        tape_id=None, disk_label=None, width=1920, height=1080
                    )
                    try:
                        p = eff_summary.get("static_params", {}).get("Filepath")
                        if p: ev.filler_fx_file = p
                    except: pass

            ev.effect_name = eff_summary.get("effect_name") or "Unknown Effect"
            ev.keyframes = self._extract_keyframes_from_op(segment, ev.duration_f, fps)
            ev.effect_convertible = bool(ev.keyframes)
            self._trace(f"EVENT FX '{ev.effect_name}' on {'source' if ev.source_path or ev.source_name else 'filler'} "
                        f"start={ev.rec_in_f} dur={ev.duration_f} kf={len(ev.keyframes)}")
            result.append(ev)

            # also traverse nested ops without advancing time
            for attr in ("components","input_segments"):
                for child in list(getattr(segment, attr, []) or []):
                    self._linearize(child, fps, rec_cursor_f, result)
            return

        progressed = False
        for attr in ("components","input_segments"):
            kids = list(getattr(segment, attr, []) or []); 
            if kids:
                cur = rec_cursor_f
                for child in kids:
                    cln = self._comp_length_frames(child, fps)
                    self._linearize(child, fps, cur, result); cur += cln
                progressed = True
        if not progressed:
            ev = Event(name=cname or "Component", rec_in_f=rec_cursor_f, rec_out_f=rec_cursor_f+lnF, duration_f=lnF,
                       source_path=None, source_name=None, effect_name=None, effect_convertible=False, keyframes=[],
                       tape_id=None, disk_label=None, width=1920, height=1080, note="non-source")
            self._trace(f"EVENT NonSource '{cname}' start={ev.rec_in_f} dur={ev.duration_f}")
            result.append(ev)

    # -------------------------- event builders & KFs ----------------------------
    def _build_event_from_source(self, src_seg, rec_in_f, rec_out_f, fps: Fraction) -> Event:
        duration_f = max(0, rec_out_f - rec_in_f)
        source_path = source_name = None; disk_label = tape_id = None; width = 1920; height = 1080
        try:
            essence_mob = self._essence_for_sourceclip(src_seg)
            if essence_mob:
                self._trace(f"  Resolved to: {aaf_class_name(essence_mob)}")
                disk_label = self._extract_disklabel(essence_mob)
                tape_id    = self._extract_tapeid(essence_mob)
                source_path, source_name = self._get_media_locator(essence_mob)
                width, height = self._dims_from_descriptor(essence_mob)
            else:
                self._trace("  essence resolve = None")
        except Exception as e:
            self._trace(f"source resolution failed: {e}")
        name = safe_name(getattr(src_seg, "name", "")) or source_name or "Clip"
        ev = Event(name=name, rec_in_f=rec_in_f, rec_out_f=rec_out_f, duration_f=duration_f,
                   source_path=source_path, source_name=source_name, effect_name=None, effect_convertible=True,
                   keyframes=[], tape_id=tape_id, disk_label=disk_label, width=width, height=height)
        self._trace(f"EVENT SourceClip name='{name}' start={rec_in_f} dur={duration_f} src='{source_name}' path='{source_path}'")
        return ev

    def _kf_time_to_frames(self, t, fps: Fraction, ev_len: int) -> int:
        try:
            if isinstance(t, dict) and "num" in t and "den" in t and t["den"]:
                sec = float(t["num"]) / float(t["den"]); return nearest_int(sec * float(fps))
            if isinstance(t, int): return int(t)
            if isinstance(t, float):
                if t <= 1.5: return nearest_int(t * float(fps))
                return int(t)
        except: pass
        return 0

    def _extract_keyframes_from_op(self, op_group, ev_duration_f: int, fps: Fraction) -> List[Keyframe]:
        kfs: List[Keyframe] = []
        static_posx=0.0; static_posy=0.0; static_sx=1.0; static_sy=1.0; static_rot=0.0; saw_any=False

        def norm(v):
            if isinstance(v, dict) and "num" in v and "den" in v and v["den"]:
                return float(v["num"]) / float(v["den"])
            if isinstance(v, (list,tuple)) and v:
                return norm(v[0])
            try:
                return float(v)
            except:
                return 0.0

        params = getattr(op_group, "parameters", None) or []
        # varying + generic names
        for p in params:
            try:
                pname = safe_name(getattr(p, "name", "Param")); is_vary = bool(getattr(p, "is_varying", False))
                lname = lower_str(pname)
                if is_vary:
                    for cp in list(getattr(p, "points", []) or []):
                        t = getattr(cp, "time", None); v = getattr(cp, "value", None)
                        if t is None or v is None: continue
                        tF = self._kf_time_to_frames(self._to_py(t), fps, ev_duration_f)
                        tF = max(0, min(ev_duration_f-1, tF))
                        if isinstance(v, (list, tuple)) and len(v) >= 2 and ("pos" in lname or "center" in lname):
                            kfs.append(Keyframe(tF, pos_x=float(v[0]), pos_y=float(v[1]),
                                                scale_x=static_sx, scale_y=static_sy, rotation=static_rot)); saw_any=True
                        elif "scale" in lname and isinstance(v,(int,float)):
                            kfs.append(Keyframe(tF, pos_x=static_posx, pos_y=static_posy,
                                                scale_x=float(v), scale_y=static_sy, rotation=static_rot)); saw_any=True
                        elif "rotation" in lname and isinstance(v,(int,float)):
                            kfs.append(Keyframe(tF, pos_x=static_posx, pos_y=static_posy,
                                                scale_x=static_sx, scale_y=static_sy, rotation=float(v))); saw_any=True
                else:
                    v = getattr(p, "value", None)
                    if v is not None:
                        if isinstance(v,(list,tuple)) and len(v)>=2 and ("pos" in lname or "center" in lname):
                            static_posx, static_posy = float(v[0]), float(v[1]); saw_any=True
                        elif "scale" in lname:
                            if isinstance(v,(int,float)):
                                static_sx = static_sy = float(v); saw_any=True
                            elif isinstance(v,(list,tuple)) and len(v)>=2:
                                static_sx, static_sy = float(v[0]), float(v[1]); saw_any=True
                        elif "rotation" in lname and isinstance(v,(int,float)):
                            static_rot = float(v); saw_any=True
            except: pass

        # AFX/DVE static keys (from attributes/CAL)
        try:
            summary = self.summar
