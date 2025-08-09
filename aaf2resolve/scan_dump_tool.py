import os, re, json, csv, traceback
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from datetime import datetime
from fractions import Fraction
from urllib.parse import urlparse, unquote

# ---- AAF2 import --------------------------------------------------------------
# Tested with pyaaf2 (aaf2) wheels. We avoid hard-typing classes (e.g., aaf2.AAFFile),
# and stick to duck-typing + __class__.__name__ to stay compatible across builds.
import aaf2

# ==============================================================================
#                               LOG / UTILITIES
# ==============================================================================

class DebugLog:
    def __init__(self, path):
        self.path = path
        self._buf = []

    def write(self, line):
        s = line if line.endswith("\n") else line + "\n"
        self._buf.append(s)

    def flush(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8", errors="replace") as f:
            f.writelines(self._buf)

def aaf_class_name(obj):
    try:
        cn = getattr(obj, "class_name", None)
        if cn:
            return str(cn)
    except:
        pass
    try:
        return obj.__class__.__name__
    except:
        return "Unknown"

def safe_name(x):
    try:
        return str(x)
    except:
        return ""

def fraction_to_str(v):
    # aaf2 rationals often come as aaf2.rational.AAFRational
    try:
        from aaf2 import rational as _rat
        if isinstance(v, _rat.AAFRational):
            return f"{v.numerator}/{v.denominator}"
    except:
        pass
    if isinstance(v, Fraction):
        return f"{v.numerator}/{v.denominator}"
    return safe_name(v)

def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    try:
        fps = float(fps)
        if frame_count is None or fps <= 0:
            return "00:00:00:00"
        sep = ";" if is_drop_frame else ":"
        fc = int(frame_count)
        int_fps = round(fps)
        h = fc // (3600 * int_fps)
        m = (fc % (3600 * int_fps)) // (60 * int_fps)
        s = (fc % (60 * int_fps)) // int_fps
        f = fc % int_fps
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}"
    except:
        return "00:00:00:00"

def decode_locator_url_to_path(url):
    try:
        # Accept URLString or PathName; if already a file path return as-is
        s = safe_name(url)
        if s.lower().startswith("file://"):
            return unquote(urlparse(s).path)
        return s
    except:
        return safe_name(url)

# ==============================================================================
#                         COMPRESSED JSON "CIPHER" FORMAT
# ==============================================================================

def compress_node(obj, dbg: DebugLog, depth=0, max_depth=12, max_vec=400, _seen=None):
    """
    Output shape (like your prior tools):
      - Class/Object: [ <NameOrClass>, "ClassDefinition", null, [ <children...> ] ]
      - Scalar Property: [ <PropName>, "Property", <scalar_value> ]
      - Single Ref Property: [ <PropName>, "StrongRefProperty"|"WeakRefProperty", null, [ <ClassNode> ] ]
      - Vector Ref Property: [ <PropName>, "StrongRefVectorProperty"|"WeakRefVectorProperty", null, [ <ClassNodes...> ] ]
    """
    if _seen is None:
        _seen = set()
    if depth > max_depth or obj is None:
        return ["MAX_DEPTH", "Meta", None, []]

    # Prevent loops via object id
    oid = None
    try:
        oid = id(obj)
        if oid in _seen:
            return ["SEEN", "Meta", None, []]
        _seen.add(oid)
    except:
        pass

    nodename = aaf_class_name(obj)
    # Class/Object node
    node = [nodename, "ClassDefinition", None, []]

    # Try enumerate properties on any AAFObject-like
    props = []
    try:
        if hasattr(obj, "properties"):
            props = list(obj.properties())
    except:
        props = []

    for p in props:
        pname = safe_name(getattr(p, "name", "Property"))
        pval = getattr(p, "value", None)
        pcls = p.__class__.__name__

        # Simple scalar?
        if isinstance(pval, (str, int, float, bool)) or pval is None:
            # handle rationals explicitly
            s_val = fraction_to_str(pval)
            node[3].append([pname, "Property", s_val])
            continue

        # Bytes / bytearray
        if isinstance(pval, (bytes, bytearray)):
            node[3].append([pname, "Property", f"<bytes:{len(pval)}>"])
            continue

        # Single referenced object?
        if isinstance(pval, aaf2.core.AAFObject):
            node[3].append([pname, pcls, None, [compress_node(pval, dbg, depth+1, max_depth, max_vec, _seen)]])
            continue

        # Vectors: strong/weak ref vectors or Python list/tuple
        if isinstance(pval, (list, tuple, aaf2.properties.StrongRefVectorProperty, aaf2.properties.WeakRefVectorProperty)):
            children = []
            ctr = 0
            try:
                iterable = list(pval)
            except:
                iterable = []
            for item in iterable:
                if ctr >= max_vec:
                    break
                ctr += 1
                if isinstance(item, aaf2.core.AAFObject):
                    children.append(compress_node(item, dbg, depth+1, max_depth, max_vec, _seen))
                elif isinstance(item, (str, int, float, bool)) or item is None:
                    children.append(["Value", "Property", fraction_to_str(item)])
                elif isinstance(item, (bytes, bytearray)):
                    children.append(["Value", "Property", f"<bytes:{len(item)}>"])
                else:
                    # fallback
                    children.append([safe_name(type(item).__name__), "Property", safe_name(item)])
            vec_label = "StrongRefVectorProperty" if "Strong" in pcls else ("WeakRefVectorProperty" if "Weak" in pcls else "StrongRefVectorProperty")
            node[3].append([pname, vec_label, None, children])
            continue

        # Weak/Strong single refs implemented in pyaaf2 as AAFObject in pval, covered above.
        # Fallback: stringify
        node[3].append([pname, "Property", safe_name(pval)])

    return node


def write_readme_cipher(path):
    txt = """Compression Mapping Key / Cipher
================================
Node forms:
- Class/Object: [ <NameOrClass>, "ClassDefinition", null, [ <children...> ] ]
- Scalar Property: [ <PropName>, "Property", <scalar_value> ]
- Single Ref Property: [ <PropName>, "StrongRefProperty"|"WeakRefProperty", null, [ <ClassNode> ] ]
- Vector Ref Property: [ <PropName>, "StrongRefVectorProperty"|"WeakRefVectorProperty", null, [ <ClassNodes...> ] ]

Scope:
- JSON is the FULL SWEEP: we include a union of toplevel, composition, master, source, and storage mobs.
- Deduplication is by Python object id during compression recursion.

Notes:
- Vectors are capped (default 400 items) to avoid gigantic outputs. Increase in GUI if needed.
- Depth is capped (default 12). Increase with caution; files can grow very large.
- AAFRational values are encoded as "N/D" strings.
- Byte payloads render as "<bytes:N>".
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)

# ==============================================================================
#                       HIGH-LEVEL SEQUENCE / FX TRAVERSAL
# ==============================================================================

def slot_media_kind(slot):
    try:
        k = getattr(getattr(slot, "segment", None), "media_kind", None)
        if k:
            return str(k).lower()
    except:
        pass
    try:
        dd = getattr(getattr(slot, "segment", None), "data_def", None)
        if dd and getattr(dd, "name", None):
            return str(dd.name).lower()
    except:
        pass
    return ""

def comp_length_frames(comp, seq_fps):
    # translate component 'length' to frames at sequence edit rate
    try:
        rate = getattr(comp, "edit_rate", None)
        if rate:
            r = Fraction(rate.numerator, rate.denominator)
        else:
            r = seq_fps
        ln = getattr(comp, "length", None)
        if ln is not None:
            return int(Fraction(ln) * (seq_fps / r))
    except:
        pass
    return 0

def extract_timecode_from_slots(slots, dbg: DebugLog):
    start_tc_frames = None
    fps = None
    drop = False
    for slot in list(slots) or []:
        seg = getattr(slot, "segment", None)
        if aaf_class_name(seg) == "Timecode":
            start = getattr(seg, "start", None)
            fps = getattr(seg, "fps", None)
            drop = bool(getattr(seg, "drop", False))
            if start is not None:
                start_tc_frames = int(start)
                dbg.write("      • Timecode found in TopLevel slot")
                return start_tc_frames, fps, drop
        elif aaf_class_name(seg) == "Sequence":
            try:
                comps = list(getattr(seg, "components", []) or [])
                for c in comps:
                    if aaf_class_name(c) == "Timecode":
                        start = getattr(c, "start", None)
                        fps = getattr(c, "fps", None)
                        drop = bool(getattr(c, "drop", False))
                        if start is not None:
                            start_tc_frames = int(start)
                            dbg.write("      • Timecode found nested in Sequence")
                            return start_tc_frames, fps, drop
            except:
                pass
    dbg.write("      ⚠ No timecode found (defaulting to 0 @ 25)")
    return 0, Fraction(25,1), False

def extract_effect_name(op_group):
    # Prefer attributes _EFFECT_PLUGIN_CLASS/_EFFECT_PLUGIN_NAME, else opdef.name, else fallback
    plugin_class = None
    plugin_name = None
    try:
        attrs = getattr(op_group, "component_attribute_list", None)
        for tv in list(attrs or []):
            nm = safe_name(getattr(tv, "name", ""))
            if nm == "_EFFECT_PLUGIN_CLASS":
                plugin_class = safe_name(getattr(tv, "value", ""))
            elif nm == "_EFFECT_PLUGIN_NAME":
                plugin_name = safe_name(getattr(tv, "value", ""))
    except:
        pass
    if plugin_name and plugin_class:
        return f"{plugin_class} : {plugin_name}"
    if plugin_name:
        return plugin_name
    try:
        opdef = getattr(op_group, "operationdef", None) or getattr(op_group, "operation", None)
        if opdef and getattr(opdef, "name", None):
            nm = safe_name(opdef.name)
            return nm.replace("_v2","").replace("_2","").replace("_"," ").strip()
    except:
        pass
    return "Unknown Effect"

def extract_fx_params(op_group):
    """Return (animated_params: dict[name]->[{time,value}], static_params: dict[name]->value])"""
    animated = {}
    staticp = {}
    try:
        params = list(getattr(op_group, "parameters", []) or [])
        for p in params:
            pname = safe_name(getattr(p, "name", "Param"))
            is_vary = bool(getattr(p, "is_varying", False))
            if is_vary:
                pts = list(getattr(p, "points", []) or [])
                rec = []
                for cp in pts:
                    t = getattr(cp, "time", None)
                    v = getattr(cp, "value", None)
                    rec.append({"time": fraction_to_str(t), "value": fraction_to_str(v)})
                if rec:
                    animated[pname] = rec
            else:
                v = getattr(p, "value", None)
                staticp[pname] = fraction_to_str(v)
    except:
        pass
    return animated, staticp

def gather_inputs(op_group):
    """Return dict(scope_refs:bool, sourceclips:[SourceClip])"""
    info = {"scope_refs": False, "sourceclips": []}
    def harvest(seg):
        if aaf_class_name(seg) == "Sequence":
            for comp in list(getattr(seg, "components", []) or []):
                if aaf_class_name(comp) == "SourceClip":
                    info["sourceclips"].append(comp)
        elif aaf_class_name(seg) == "SourceClip":
            info["sourceclips"].append(seg)

    for attr in ("input_segments", "components"):
        try:
            for s in list(getattr(op_group, attr, []) or []):
                harvest(s)
        except:
            pass

    # Scope refs
    try:
        comps = list(getattr(op_group, "components", []) or [])
        for c in comps:
            if aaf_class_name(c) == "ScopeReference":
                info["scope_refs"] = True
                break
    except:
        pass
    return info

def resolve_filesource_mob_from_sourceclip(src_seg, dbg: DebugLog):
    """Follow SourceClip -> source.mob chain down to FileSourceMob if possible."""
    try:
        cur = None
        try:
            src = getattr(src_seg, "source", None)
            cur = getattr(src, "mob", None) if src else None
        except:
            cur = None
        chain = []
        seen = set()
        while cur and id(cur) not in seen:
            seen.add(id(cur))
            cname = aaf_class_name(cur)
            chain.append(cname)
            if cname == "FileSourceMob":
                dbg.write("      resolve chain: " + " → ".join(chain))
                return cur
            # step down one
            next_mob = None
            try:
                for slot in getattr(cur, "slots", []) or []:
                    seg = getattr(slot, "segment", None)
                    src = getattr(seg, "source", None)
                    nm = getattr(src, "mob", None) if src else None
                    if nm:
                        next_mob = nm
                        break
            except:
                pass
            if not next_mob:
                break
            cur = next_mob
        dbg.write("      resolve chain (terminal): " + " → ".join(chain))
        return cur
    except Exception as e:
        dbg.write(f"      resolve_filesource error: {e}")
        return None

def extract_mob_locators_tape_disk(mob, dbg: DebugLog):
    locs = []
    tape = ""
    disk = ""
    try:
        desc = getattr(mob, "descriptor", None)
        if desc:
            # Locators
            try:
                for loc in getattr(desc, "locators", []) or []:
                    url = getattr(loc, "url", None) or getattr(loc, "URLString", None)
                    if url:
                        dec = decode_locator_url_to_path(url)
                        if dec and dec not in locs:
                            locs.append(dec)
                            dbg.write(f"[locator] {dec}")
            except:
                pass
        # TapeID
        try:
            for c in getattr(mob, "user_comments", []) or []:
                if "tape" in safe_name(getattr(c, "name", "")).lower() and not tape:
                    tape = safe_name(getattr(c, "value", ""))
        except:
            pass
        # DiskLabel buried in attributes sometimes
        try:
            if hasattr(mob, "attributes"):
                for a in mob.attributes:
                    nm = safe_name(getattr(a, "name", "")).upper()
                    if "DISK" in nm or "DISKLABEL" in nm or "_IMPORTDISKLAB" in nm:
                        val = safe_name(getattr(a, "value", ""))
                        if val and not disk:
                            disk = val
        except:
            pass
    except:
        pass
    return locs, tape, disk

def deepdump_sequence(seq_mob, out_txt_path, dbg: DebugLog):
    with open(out_txt_path, "w", encoding="utf-8") as f:
        def w(line):
            f.write(line + "\n")

        name = safe_name(getattr(seq_mob, "name", "(unnamed)"))
        slots = getattr(seq_mob, "slots", []) or []
        start_tc, fps, drop = extract_timecode_from_slots(slots, dbg)
        fpsF = Fraction(getattr(fps, "numerator", 25), getattr(fps, "denominator", 1))
        w(f"Sequence: {name}")
        try:
            erate = getattr(slots[0], "edit_rate", None)
            er_str = fraction_to_str(erate) if erate else "25/1"
            w(f"Edit Rate: {er_str}  (≈ {float(Fraction(er_str)) if '/' in er_str else float(er_str)} fps)")
        except:
            w("Edit Rate: 25  (≈ 25.0 fps)")
        w(f"Start TC: {frames_to_tc(start_tc, float(fpsF), drop)}  ({start_tc} frames)")
        w("")

        # Walk picture slot sequences
        for idx, slot in enumerate(slots):
            kind = slot_media_kind(slot)
            seg = getattr(slot, "segment", None)
            w(f"Slot[{idx}] media_kind={kind.capitalize() or 'Unknown'}  segment={aaf_class_name(seg)}")
            rec_cursor = start_tc
            def visit(node, rec):
                cname = aaf_class_name(node)
                ln = comp_length_frames(node, fpsF)
                w(f"  visit {cname} @rec={rec} lenF={ln}")
                if cname == "Sequence":
                    comps = list(getattr(node, "components", []) or [])
                    cur = rec
                    for comp in comps:
                        cl = comp_length_frames(comp, fpsF)
                        visit(comp, cur)
                        cur += cl
                    return
                if cname == "SourceClip":
                    w(f"    SOURCE 'SourceClip' rec=[{rec}-{rec+ln}) dur={ln}")
                    return
                if cname == "OperationGroup":
                    eff_name = extract_effect_name(node)
                    w(f"    FX '{eff_name}' rec=[{rec}-{rec+ln}) dur={ln}")
                    anim, stat = extract_fx_params(node)
                    # Static
                    for k, v in stat.items():
                        w(f"      static: {k} = {v}")
                    # Animated (summarize)
                    for k, lst in anim.items():
                        w(f"      anim: {k} ({len(lst)} kfs)")
                    # Also walk children without advancing time to expose nested bits
                    for attr in ("components", "input_segments"):
                        for ch in list(getattr(node, attr, []) or []):
                            visit(ch, rec)
                    return
                # generic: dive if has components/input_segments
                progressed = False
                for attr in ("components", "input_segments"):
                    kids = list(getattr(node, attr, []) or []) 
                    if kids:
                        cur = rec
                        for ch in kids:
                            cl = comp_length_frames(ch, fpsF)
                            visit(ch, cur)
                            cur += cl
                        progressed = True
                if not progressed:
                    # leaf/filler
                    pass

            # Skip audio/data tracks
            if "sound" in kind or "data" in kind:
                continue
            visit(seg, rec_cursor)
            w("")

# ==============================================================================
#                         SUPER EDL CSV (sequence-focused)
# ==============================================================================

def build_mob_map_from_compressed(root_list):
    """For JSON compressed sweep: build MobID->node map (if needed elsewhere)."""
    mob_map = {}
    def walk(n):
        if not isinstance(n, list) or len(n) < 4:
            return
        kids = n[3]
        # record MobID => node
        for c in kids:
            if isinstance(c, list) and c[0] == "MobID":
                # this node *is* a mob; capture
                try:
                    mob_id = None
                    # c is ["MobID","ClassDefinition",None,[ ... ]]
                    # in this compressed form we often don't have the literal ID value printed.
                    # Some AAFs omit it or it's represented differently; tolerate missing.
                    # Keep structure in place for parity with your JSON pipeline.
                except:
                    pass
        for c in kids:
            walk(c)
    walk(root_list)
    return mob_map

def super_edl_from_sequence(seq_mob, out_csv_path, dbg: DebugLog):
    """Produce a CSV roughly comparable to your prior 'super EDL' for a single sequence."""
    name = safe_name(getattr(seq_mob, "name", "(unnamed)"))
    slots = getattr(seq_mob, "slots", []) or []
    start_tc, fps, drop = extract_timecode_from_slots(slots, dbg)
    fpsF = Fraction(getattr(fps, "numerator", 25), getattr(fps, "denominator", 1))
    seq_rate = float(fpsF)

    events = []  # accumulate dicts

    # index record-time effects by in frame for pairing
    fx_by_rec_in = {}

    def record_fx(rec_in, op_group):
        eff_name = extract_effect_name(op_group)
        anim, stat = extract_fx_params(op_group)
        keyframe_details_lines = []
        if anim:
            keyframe_details_lines.append('--- Animated Parameters ---')
            for pname, pts in anim.items():
                keyframe_details_lines.append(f"  - {pname} ({len(pts)} keyframes)")
                for kp in pts:
                    # kp['time'] is "N/D" or scalar; convert to offset frames heuristically (0..len-1 unknown here)
                    keyframe_details_lines.append(f"    Time: {kp['time']} -> Value: {kp['value']}")
        if stat:
            if keyframe_details_lines:
                keyframe_details_lines.append("")
            keyframe_details_lines.append('--- Static Parameters ---')
            for pname, val in stat.items():
                keyframe_details_lines.append(f"  - {pname}: {val}")
        fx_by_rec_in.setdefault(rec_in, []).append({
            "name": eff_name,
            "anim": anim,
            "stat": stat,
            "keyframe_details": "\n".join(keyframe_details_lines) if keyframe_details_lines else "No effect data found."
        })

    def visit(node, rec):
        cname = aaf_class_name(node)
        ln = comp_length_frames(node, fpsF)
        dbg.write(f"[visit] {cname} rec={rec} len={ln}")
        if cname == "Sequence":
            comps = list(getattr(node, "components", []) or [])
            cur = rec
            for comp in comps:
                cl = comp_length_frames(comp, fpsF)
                visit(comp, cur)
                cur += cl
            return
        if cname == "SourceClip":
            # Resolve its FileSourceMob for metadata
            fsm = resolve_filesource_mob_from_sourceclip(node, dbg)
            locs, tape, disk = ([], "", "")
            if fsm:
                locs, tape, disk = extract_mob_locators_tape_disk(fsm, dbg)
            # offsets
            off = 0
            try:
                # Avid often Start/StartTime on SourceClip (timeline-agnostic) in edit units of that source
                off = int(getattr(node, "start_time", getattr(node, "start", 0)) or 0)
            except:
                pass
            url_path = locs[0] if locs else ""
            src_fname = os.path.basename(url_path) if url_path else "N/A"
            src_dir = os.path.dirname(url_path) if url_path else "N/A"
            events.append({
                "Timeline Start (TC)": frames_to_tc(rec, seq_rate, drop),
                "Timeline Start (frames)": rec,
                "Length (frames)": ln,
                "Clip Name": src_fname,
                "Source File Path": src_dir,
                "TapeID": tape,
                "DiskLabel": disk,
                "Effect Name": "; ".join([fx["name"] for fx in fx_by_rec_in.get(rec, [])]) if rec in fx_by_rec_in else "",
                "Effect Details": "\n\n".join([fx["keyframe_details"] for fx in fx_by_rec_in.get(rec, [])]) if rec in fx_by_rec_in else "",
            })
            return
        if cname == "OperationGroup":
            record_fx(rec, node)
            # Do not advance time for nested inputs; also recurse to catch nested SourceClips if any
            for attr in ("components", "input_segments"):
                for ch in list(getattr(node, attr, []) or []):
                    visit(ch, rec)
            return
        # default dive
        for attr in ("components", "input_segments"):
            for ch in list(getattr(node, attr, []) or []):
                visit(ch, rec)

    # Walk only picture slots
    for slot in slots:
        if "sound" in slot_media_kind(slot) or "data" in slot_media_kind(slot):
            continue
        seg = getattr(slot, "segment", None)
        visit(seg, start_tc)

    # write CSV
    with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timeline Summary"])
        total_len = sum(e["Length (frames)"] for e in events)
        w.writerow(["Sequence Name", name])
        w.writerow(["Edit Rate", f"{seq_rate} (drop={drop})"])
        w.writerow(["Start TC", frames_to_tc(start_tc, seq_rate, drop)])
        w.writerow(["Timeline Length", f"{frames_to_tc(total_len, seq_rate, drop)} ({total_len}f)"])
        w.writerow([])
        if events:
            hdr = list(events[0].keys())
            w.writerow(hdr)
            for e in events:
                w.writerow([e.get(h, "") for h in hdr])

# ==============================================================================
#                            FULL SWEEP + DRIVER
# ==============================================================================

def full_compressed_sweep(aaf, dbg: DebugLog, out_json_path):
    dbg.write("[sweep] toplevel count=%s" % len(list(aaf.content.toplevel())))
    roots = []
    # Include union of toplevel, compositions, master, source
    try:
        for m in list(aaf.content.toplevel()) or []:
            dbg.write(f"[compress] toplevel: {aaf_class_name(m)} '{getattr(m,'name','')}'")
            roots.append(compress_node(m, dbg))
    except Exception as e:
        dbg.write(f"[compress] toplevel error: {e}")
    try:
        comps = list(aaf.content.compositionmobs()) or []
        dbg.write(f"[sweep] compositionmobs count={len(comps)}")
        for m in comps:
            dbg.write(f"[compress] composition: {aaf_class_name(m)} '{getattr(m,'name','')}'")
            roots.append(compress_node(m, dbg))
    except Exception as e:
        dbg.write(f"[compress] compositions error: {e}")
    try:
        masters = list(aaf.content.mastermobs()) or []
        dbg.write(f"[sweep] mastermobs count={len(masters)}")
        for m in masters:
            dbg.write(f"[compress] master: {aaf_class_name(m)} '{getattr(m,'name','')}'")
            roots.append(compress_node(m, dbg))
    except Exception as e:
        dbg.write(f"[compress] masters error: {e}")
    try:
        sources = list(aaf.content.sourcemobs()) or []
        dbg.write(f"[sweep] sourcemobs count={len(sources)}")
        for m in sources:
            dbg.write(f"[compress] source: {aaf_class_name(m)} '{getattr(m,'name','')}'")
            roots.append(compress_node(m, dbg))
    except Exception as e:
        dbg.write(f"[compress] sources error: {e}")

    # Write a single "list" root node for parity with your prior JSON consumer
    out = ["list", "list", None, roots]
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

def mob_metadata_table(aaf, dbg: DebugLog):
    rows = []
    # Crawl all mobs and collect quick facts
    def add_row(m):
        tape = ""
        disk = ""
        loc_count = 0
        try:
            desc = getattr(m, "descriptor", None)
            if desc:
                for loc in getattr(desc, "locators", []) or []:
                    url = getattr(loc, "url", None) or getattr(loc, "URLString", None)
                    if url:
                        loc_count += 1
        except:
            pass
        try:
            for c in getattr(m, "user_comments", []) or []:
                if "tape" in safe_name(getattr(c, "name", "")).lower():
                    tape = safe_name(getattr(c, "value", "")) or tape
        except:
            pass
        try:
            if hasattr(m, "attributes"):
                for a in m.attributes:
                    nm = safe_name(getattr(a, "name", "")).upper()
                    if "DISK" in nm or "DISKLABEL" in nm or "_IMPORTDISKLAB" in nm:
                        disk = safe_name(getattr(a, "value", "")) or disk
        except:
            pass
        rows.append([safe_name(getattr(m, "name", "")), aaf_class_name(m), tape, disk, loc_count])

    for it in (aaf.content.compositionmobs(), aaf.content.mastermobs(), aaf.content.sourcemobs()):
        try:
            for m in list(it) or []:
                add_row(m)
        except:
            pass
    return rows

def run_scan(aaf_path):
    base_dir = os.path.dirname(aaf_path)
    base_name = os.path.splitext(os.path.basename(aaf_path))[0]
    dbg_path = os.path.join(base_dir, f"{base_name}_FULL_debug.txt")
    deepdump_path = os.path.join(base_dir, f"{base_name}_FULL_deepdump.txt")
    readme_path = os.path.join(base_dir, f"{base_name}_README_mapping.txt")
    json_path = os.path.join(base_dir, f"{base_name}_FULL_compressed.json")

    dbg = DebugLog(dbg_path)
    try:
        print(f"Opening AAF: {aaf_path}")
        with aaf2.open(aaf_path, 'r') as f:
            # FULL compressed JSON sweep
            print("Building FULL compressed JSON sweep (this can be large)...")
            full_compressed_sweep(f, dbg, json_path)
            print(f"  • Wrote: {json_path}")

            # Deep scan all top-level sequences to text
            print("Traversing all Top-Level sequences...")
            tops = list(f.content.toplevel()) or []
            with open(deepdump_path, "w", encoding="utf-8") as txt:
                txt.write(f"Top-Level Composition Mobs: {len(tops)}\n\n")
            for i, mob in enumerate(tops):
                seq_name = safe_name(getattr(mob, "name", "(unnamed)"))
                with open(deepdump_path, "a", encoding="utf-8") as txt:
                    txt.write(f"=== Sequence [{i}] '{seq_name}' ===\n")
                deepdump_sequence(mob, deepdump_path, dbg)
                with open(deepdump_path, "a", encoding="utf-8") as txt:
                    txt.write("\n")

            # Per-mob metadata sweep (one table at bottom of deepdump)
            print("Sweeping per-mob metadata table...")
            rows = mob_metadata_table(f, dbg)
            with open(deepdump_path, "a", encoding="utf-8") as txt:
                txt.write("=== Mob Metadata Sweep (name | class | TapeID | DiskLabel | Locator count) ===\n")
                for r in rows:
                    txt.write(" | ".join(safe_name(x) for x in r) + "\n")

            # README cipher
            print("Writing deepdump...")
            write_readme_cipher(readme_path)

            print(f"  • Wrote: {deepdump_path}")
            print(f"  • Wrote: {readme_path}")
            print(f"  • Wrote: {dbg_path}")

    except Exception as e:
        dbg.write(f"\nFATAL: {e}\n{traceback.format_exc()}")
        raise
    finally:
        dbg.flush()

# ==============================================================================
#                                  GUI
# ==============================================================================

class App:
    def __init__(self, root):
        self.root = root
        root.title("AAF Full Sweep + DeepDump + CSV")
        self.aaf_path = tk.StringVar()
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, height=18, width=120, font=("Consolas", 10))
        frm = tk.Frame(root)
        frm.pack(padx=10, pady=10, fill="x")
        tk.Label(frm, text="AAF file:").grid(row=0, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.aaf_path, width=80).grid(row=0, column=1, padx=6)
        tk.Button(frm, text="Browse", command=self.browse).grid(row=0, column=2)
        tk.Button(frm, text="Run Scan", command=self.run).grid(row=1, column=1, pady=8, sticky="e")
        self.log.pack(padx=10, pady=8, fill="both", expand=True)

    def browse(self):
        p = filedialog.askopenfilename(title="Select AAF", filetypes=[("AAF files","*.aaf"),("All files","*.*")])
        if p:
            self.aaf_path.set(p)

    def run(self):
        path = self.aaf_path.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("Error", "Select a valid .aaf file.")
            return
        try:
            self.log.delete("1.0","end")
            self._echo(f"Opening AAF: {path}")
            run_scan(path)
            # Also write a per-sequence Super EDL CSV for each top-level mob
            with aaf2.open(path, 'r') as f:
                tops = list(f.content.toplevel()) or []
                for mob in tops:
                    name = safe_name(getattr(mob, "name", "Sequence"))
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_csv = os.path.join(os.path.dirname(path), f"{name}_super_edl_fx_{timestamp}.csv")
                    self._echo(f"Writing Super EDL CSV: {out_csv}")
                    dbg = DebugLog(os.path.join(os.path.dirname(path), f"{name}_super_edl_fx_{timestamp}.debug.txt"))
                    super_edl_from_sequence(mob, out_csv, dbg)
                    dbg.flush()
            self._echo("Done.")
            messagebox.showinfo("Done", "Scan complete. Check the folder for *_FULL_deepdump.txt, *_FULL_compressed.json, *_README_mapping.txt, *_FULL_debug.txt and per-sequence CSV + debug.")
        except Exception as e:
            self._echo(f"ERROR: {e}\n{traceback.format_exc()}")
            messagebox.showerror("Error", f"{e}\n\nSee console/log for details.")

    def _echo(self, s): 
        self.log.insert("end", s + "\n"); self.log.see("end"); self.root.update_idletasks()

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
