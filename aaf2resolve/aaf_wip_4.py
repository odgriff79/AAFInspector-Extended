#!/usr/bin/env python3
# superedl_aaf_extractor.py
# Requires: pyaaf2>=1.4.0

import os
import re
import csv
import sys
import urllib.parse
from collections import deque, defaultdict

import aaf2
from aaf2.components import Sequence, SourceClip, OperationGroup, Timecode
from aaf2.mobs import CompositionMob

# -------------------- Formatting helpers --------------------

def frames_to_tc(fc, fps, drop=False):
    if fc is None:
        return "N/A"
    fc = int(fc)
    fps = int(round(float(fps or 25.0)))
    h = fc // (3600 * fps)
    m = (fc % (3600 * fps)) // (60 * fps)
    s = (fc % (60 * fps)) // fps
    f = fc % fps
    return f"{h:02}:{m:02}:{s:02}{';' if drop else ':'}{f:02}"

def unwrap(x):
    try:
        if hasattr(x, "value"):
            return unwrap(x.value)
        return x
    except Exception:
        return x

def clean_label(s):
    s = str(s)
    s = re.sub(r"<aaf2[^>]*>", "", s)
    s = re.sub(r", 0x[0-9a-fA-F]+>", "", s)
    s = s.replace(">", "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

# -------------------- Timeline / sequence discovery --------------------

def choose_timeline_by_name(f, preferred_substring="Exported.01"):
    # Prefer CompositionMob whose name contains preferred substring
    for m in f.content.mobs:
        if isinstance(m, CompositionMob):
            nm = (getattr(m, "name", "") or "").lower()
            if preferred_substring.lower() in nm:
                return m
    # Otherwise first CompositionMob that has a Sequence in any slot
    for m in f.content.mobs:
        if isinstance(m, CompositionMob):
            for s in (m.slots or []):
                if isinstance(getattr(s, "segment", None), Sequence):
                    return m
    return None

def find_picture_sequence_and_info(comp_mob):
    edit_rate = 25.0
    drop = False
    timeline_start = 0
    sequence = None
    for s in comp_mob.slots:
        seg = getattr(s, "segment", None)
        if isinstance(seg, Timecode):
            timeline_start = int(getattr(seg, "start", 0) or 0)
            drop = bool(getattr(seg, "drop", False))
        if isinstance(seg, Sequence) and sequence is None:
            sequence = seg
            try:
                edit_rate = float(getattr(s, "edit_rate", 25.0) or 25.0)
            except Exception:
                pass
    return sequence, edit_rate, drop, timeline_start

# -------------------- Sequence walk (clips + effects) --------------------

def deep_first_sourceclip(seg):
    dq = deque([seg])
    seen = set()
    while dq:
        n = dq.popleft()
        if id(n) in seen:
            continue
        seen.add(id(n))
        if isinstance(n, SourceClip):
            return n
        for attr in ("components", "segments", "input_segments"):
            it = getattr(n, attr, None)
            if it:
                for s in it:
                    dq.append(s)
    return None

def walk_seq_collect_both(root_seq, start_ofs_frames):
    """
    Returns:
      clips:  [{"node": SourceClip, "ofs": timeline_ofs_frames, "len": length}, ...]
      effects:[{"node": OperationGroup, "ofs": timeline_ofs_frames, "len": length}, ...]
    """
    clips, effects = [], []

    def walk(node, ofs):
        L = int(getattr(node, "length", 0) or 0)
        if isinstance(node, SourceClip):
            clips.append({"node": node, "ofs": ofs, "len": L})
            return L
        if isinstance(node, Sequence):
            total = 0
            for c in node.components:
                used = walk(c, ofs + total)
                total += used if used else int(getattr(c, "length", 0) or 0)
            return total
        if isinstance(node, OperationGroup):
            effects.append({"node": node, "ofs": ofs, "len": L})
            for attr in ("segments", "input_segments"):
                it = getattr(node, attr, None)
                if it:
                    for seg in it:
                        walk(seg, ofs)
            return L
        # generic node: try to descend
        for attr in ("components", "segments", "input_segments"):
            it = getattr(node, attr, None)
            if it:
                for seg in it:
                    walk(seg, ofs)
        return L

    walk(root_seq, start_ofs_frames)
    return clips, effects

# -------------------- UMID chain resolution --------------------

def resolve_chain_mobs(start_mob):
    """
    Follow SourceClip references through slots:
      Timeline mob -> Master mob -> (possibly more) -> Source/Import mob
    """
    chain = []
    if not start_mob:
        return chain
    chain.append(start_mob)
    seen = {id(start_mob)}
    cur = start_mob
    while True:
        nxt = None
        for ss in (cur.slots or []):
            seg = ss.segment
            sc = deep_first_sourceclip(seg)
            if sc is not None:
                m = getattr(sc, "mob", None)
                if m and id(m) not in seen:
                    nxt = m
                    break
        if nxt is None:
            break
        chain.append(nxt)
        seen.add(id(nxt))
        cur = nxt
    return chain

def resolve_end_import_mob_from(start_mob):
    """
    First mob in the chain whose descriptor contains a Locator -> Import mob with URL.
    """
    for m in resolve_chain_mobs(start_mob):
        desc = getattr(m, "descriptor", None)
        if desc is not None:
            try:
                # presence of any locator is enough
                for _ in desc.locator:
                    return m
            except Exception:
                pass
    return None

# -------------------- Metadata: timecode, tape/disk, descriptor length --------------------

def bfs_find_timecode_in_mob(mob):
    """
    Find a Timecode segment in the given mob's slots and return start/rate/drop.
    """
    if mob is None:
        return {"start": None, "rate": None, "drop": None}
    for ss in (mob.slots or []):
        seg = getattr(ss, "segment", None)
        dq = deque([seg])
        seen = set()
        while dq:
            n = dq.popleft()
            if id(n) in seen:
                continue
            seen.add(id(n))
            if isinstance(n, Timecode):
                start = int(getattr(n, "start", 0) or 0)
                drop = bool(getattr(n, "drop", False))
                rate = float(getattr(ss, "edit_rate", 25.0) or 25.0)
                return {"start": start, "rate": rate, "drop": drop}
            for attr in ("components", "segments", "input_segments"):
                it = getattr(n, attr, None)
                if it:
                    for s in it:
                        dq.append(s)
    return {"start": None, "rate": None, "drop": None}

def bfs_named_value(root, names):
    """
    Plain-text crawl for Name/Value pairs anywhere under 'root'.
    Used for TapeID and _IMPORTDISKLAB (DiskLabel).
    """
    targets = {n.strip() for n in names}
    dq = deque([root])
    seen = set()
    while dq:
        node = dq.popleft()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))

        # dictionary-like nodes
        try:
            keys = list(node.keys())
        except Exception:
            keys = []

        if "Name" in keys and "Value" in keys:
            try:
                nm = str(unwrap(node["Name"])).strip()
                if nm in targets:
                    return str(unwrap(node["Value"])).strip()
            except Exception:
                pass

        # dive fields
        for k in keys:
            try:
                dq.append(node[k])
            except Exception:
                pass

        # iterate like a sequence if supported
        try:
            for it in node:
                dq.append(it)
        except Exception:
            pass

        # follow references if supported
        try:
            for ref in node.walk_references():
                dq.append(ref)
        except Exception:
            pass

    return ""

def descriptor_length_if_any(m):
    desc = getattr(m, "descriptor", None)
    if desc is None:
        return 0
    # direct 'Length'
    try:
        if "Length" in list(desc.keys()):
            v = desc["Length"]
            return int(v.value if hasattr(v, "value") else v)
    except Exception:
        pass
    # look inside first-level child descriptors
    try:
        for k in desc.keys():
            child = desc[k]
            try:
                if "Length" in list(child.keys()):
                    v = child["Length"]
                    return int(v.value if hasattr(v, "value") else v)
            except Exception:
                continue
    except Exception:
        pass
    return 0

def orig_len_by_chain(start_mob):
    """
    MasterMob → EssenceDescriptor → Length (first non-zero along the chain).
    """
    for m in resolve_chain_mobs(start_mob):
        v = descriptor_length_if_any(m)
        if v:
            return v
    return 0

# -------------------- URL + offset helpers --------------------

def decode_url_from_import_mob(import_mob):
    src_fname, src_path = "N/A", "N/A"
    if import_mob is None:
        return src_fname, src_path
    try:
        for loc in import_mob.descriptor.locator:
            url = str(unwrap(loc["URLString"]))
            p = urllib.parse.unquote(urllib.parse.urlparse(url).path)
            src_fname = os.path.basename(p)
            src_path = os.path.dirname(p)
            break
    except Exception:
        pass
    return src_fname, src_path

def sourceclip_offset_frames(sc):
    # attributes first
    for attr in ("start_time", "start"):
        try:
            v = getattr(sc, attr, None)
            if v is not None:
                return int(v)
        except Exception:
            pass
    # dict-like fallback
    for key in ("StartTime", "Start"):
        try:
            v = unwrap(sc[key])
            if isinstance(v, (int, float)):
                return int(v)
        except Exception:
            pass
    return 0

# -------------------- FX naming + params --------------------

def op_name_from_repr2(op):
    try:
        if "Operation" in op.keys():
            raw = str(unwrap(op["Operation"]))
            m = re.search(r"OperationDef\s+(.+?)\s+[0-9a-fA-F\-]{8,}", raw)
            if m:
                name = m.group(1)
                name = name.replace("_v2", "").replace("_", " ").strip()
                return name
    except Exception:
        pass
    return None

def collect_attrs_on_op(op: OperationGroup):
    out = {}
    try:
        if "ComponentAttributeList" in list(op.keys()):
            for a in op["ComponentAttributeList"]:
                try:
                    nm = str(unwrap(a["Name"]))
                    val = str(unwrap(a["Value"])) if "Value" in a.keys() else ""
                    out[nm] = val
                except Exception:
                    pass
    except Exception:
        pass
    return out

def extract_effect_details_opstrict2(op):
    attrs = collect_attrs_on_op(op)
    plugin = clean_label(attrs.get("_EFFECT_PLUGIN_NAME") or attrs.get("_PLUGIN_NAME") or "")
    klass  = clean_label(attrs.get("_EFFECT_PLUGIN_CLASS") or "")
    opn    = op_name_from_repr2(op)
    if not plugin and opn:
        plugin = opn
    if not plugin:
        plugin = "Unknown Effect"
    if not klass:
        klass = "AVX2 Effect"
    effect_name = f"{klass} : {plugin}"

    animated = {}
    static_list = []
    try:
        for p in getattr(op, "parameters", []):
            pd = getattr(p, "parameterdef", None)
            pname = clean_label(getattr(pd, "name", None) or getattr(p, "name", None) or "Parameter")
            cps = getattr(p, "control_points", None)
            if cps is not None:
                kfs = [{"time": unwrap(getattr(cp, "time", None)),
                        "value": unwrap(getattr(cp, "value", None))} for cp in cps]
                if kfs:
                    animated[pname] = kfs
            else:
                val = unwrap(getattr(p, "value", None))
                if val is not None:
                    static_list.append(f"- Parameter: {pname} -> Value: {val}")
    except Exception:
        pass

    return {
        "effect_name": effect_name,
        "animated_params": animated,
        "static_params_str": "\n".join(static_list)
    }

# -------------------- Extraction --------------------

CSV_COLUMNS = [
    "Event","Event Name","Clip Name","Source File Name","Source File Path","DiskLabel","TapeID",
    "SourceMobID","TrackID","Source Clip EditRate","Timeline Start TC","Source Clip start time code",
    "Source Clip offset","StartTime","End Time","Event Length","Source Clip start (frames)",
    "Source Clip offset (frames)","StartTime (frames)","Effect Name","Keyframe Details","Orig Source Clip length"
]

def extract_aaf(aaf_path, preferred_timeline_hint="Exported.01"):
    rows = []
    summary = {}

    with aaf2.open(aaf_path, "r") as f:
        comp = choose_timeline_by_name(f, preferred_timeline_hint)
        if comp is None:
            comps = [m for m in f.content.mobs if isinstance(m, CompositionMob)]
            comp = comps[0] if comps else None
        if comp is None:
            raise RuntimeError("No CompositionMob found.")

        seq, edit_rate, drop, timeline_start = find_picture_sequence_and_info(comp)
        if seq is None:
            raise RuntimeError("No picture Sequence found in the CompositionMob.")

        clips, effects = walk_seq_collect_both(seq, timeline_start)

        # map all effects by timeline offset
        effects_by_ofs = defaultdict(list)
        for e in effects:
            details = extract_effect_details_opstrict2(e["node"])
            details["node"] = e["node"]
            effects_by_ofs[e["ofs"]].append(details)

        # build rows
        for idx, e in enumerate(clips, start=1):
            node = e["node"]; ofs = e["ofs"]; L = e["len"]
            smob = getattr(node, "mob", None)               # timeline mob
            import_mob = resolve_end_import_mob_from(smob)  # end mob with URL

            # URL → filenames/paths
            src_fname, src_path = decode_url_from_import_mob(import_mob)

            # UMID for the SourceClip on the timeline
            try:
                src_umid = str(unwrap(node["SourceID"]))
            except Exception:
                src_umid = "N/A"

            # DiskLabel / TapeID (deep plain-text crawl for Name/Value)
            disk_label = bfs_named_value(import_mob, {"_IMPORTDISKLAB"}) if import_mob else ""
            tape_id    = bfs_named_value(import_mob, {"TapeID"})        if import_mob else ""

            # Source timecode (from import mob)
            tc = bfs_find_timecode_in_mob(import_mob)
            genuine_start = tc["start"]
            source_rate   = tc["rate"] or edit_rate
            source_drop   = tc["drop"] if tc["drop"] is not None else False

            # Offset inside the SourceClip on timeline
            offset_frames = sourceclip_offset_frames(node)

            # Absolute source frame start/end used
            start_frames = (genuine_start or 0) + offset_frames
            end_frames   = start_frames + L

            # Effects at this event start offset
            effs = effects_by_ofs.get(ofs, [])
            eff_name = ", ".join(d["effect_name"] for d in effs) if effs else "N/A"

            parts = []
            for d in effs:
                if d["animated_params"]:
                    parts.append("--- Animated Parameters ---")
                    for pname, kfs in d["animated_params"].items():
                        parts.append(f"  - Parameter: {pname} ({len(kfs)} keyframes)")
                        for cp in kfs:
                            parts.append(f"    Keyframe at Time: {cp.get('time')} -> Value: {cp.get('value')}")
                if d.get("static_params_str"):
                    if parts:
                        parts.append("\n--- Static Parameters ---")
                    else:
                        parts.append("--- Static Parameters ---")
                    parts.append(d["static_params_str"])
            eff_detail = "\n".join(parts) if parts else "No effect data found."

            # Original source clip length (from chain; prefer MasterMob descriptor Length)
            orig_len = orig_len_by_chain(smob)

            rows.append({
                "Event": idx,
                "Event Name": src_fname,
                "Clip Name": src_fname,
                "Source File Name": src_fname,
                "Source File Path": src_path,
                "DiskLabel": disk_label or "N/A",
                "TapeID": tape_id or "N/A",
                "SourceMobID": src_umid,
                "TrackID": "N/A",
                "Source Clip EditRate": source_rate,
                "Timeline Start TC": frames_to_tc(ofs, edit_rate, drop),
                "Source Clip start time code": frames_to_tc(genuine_start, source_rate, source_drop) if genuine_start is not None else "N/A",
                "Source Clip offset": frames_to_tc(offset_frames, source_rate, source_drop),
                "StartTime": frames_to_tc(start_frames, source_rate, source_drop),
                "End Time": frames_to_tc(end_frames, source_rate, source_drop),
                "Event Length": L,
                "Source Clip start (frames)": genuine_start or 0,
                "Source Clip offset (frames)": offset_frames,
                "StartTime (frames)": start_frames,
                "Effect Name": eff_name,
                "Keyframe Details": eff_detail,
                "Orig Source Clip length": orig_len
            })

        total_len = sum(c["len"] for c in clips)
        summary = {
            "Timeline Name": getattr(comp, "name", "(unnamed)"),
            "Timeline Edit Rate": f"{edit_rate} ({'DF' if drop else 'NDF'})",
            "Timeline Start": frames_to_tc(timeline_start, edit_rate, drop),
            "Timeline Length": f"{frames_to_tc(total_len, edit_rate, drop)} ({total_len} frames)",
            "Total number of EDL events found": len(clips),
            "Total number of unique sources": len({r["SourceMobID"] for r in rows if r["SourceMobID"] != "N/A"}),
        }

    return rows, summary

# -------------------- CSV writer --------------------

def write_csv(rows, summary, out_csv_path):
    os.makedirs(os.path.dirname(out_csv_path), exist_ok=True)
    with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # Timeline summary header
        w.writerow(["Timeline Summary"])
        for k in ["Timeline Name","Timeline Edit Rate","Timeline Start","Timeline Length",
                  "Total number of EDL events found","Total number of unique sources"]:
            w.writerow([k, summary.get(k, "")])
        w.writerow([])

        # Table header
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow([r.get(col, "") for col in CSV_COLUMNS])

# -------------------- CLI --------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python superedl_aaf_extractor.py <path_to.aaf> [preferred_timeline_hint]")
        sys.exit(1)
    aaf_path = sys.argv[1]
    hint = sys.argv[2] if len(sys.argv) > 2 else "Exported.01"

    rows, summary = extract_aaf(aaf_path, hint)

    base = os.path.splitext(os.path.basename(aaf_path))[0]
    out_csv = os.path.join(os.path.dirname(aaf_path), f"{base}_super_edl_fx_report_v2.csv")
    write_csv(rows, summary, out_csv)

    print("Wrote:", out_csv)
    print("Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

if __name__ == "__main__":
    main()
