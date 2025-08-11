"""
Super EDL + FX (AAF → CSV) — aaf_wip_15_deep_locator

This version ports your proven JSON-walker logic to direct AAF parsing via pyaaf2.
Key points implemented exactly per your rules:

- Timeline traversal
  • Walk Sequence only to advance timeline.
  • Recurse into nested containers.
  • Record SourceClip events.
  • Record "FX on filler" ONLY when an OperationGroup has no nested SourceClip.

- Genuine source resolution
  • For each SourceClip, follow UMID (SourceID + SourceMobSlotID) upstream.
  • Prefer the *deepest* mob in the chain that has an ImportDescriptor/Locator (camera master),
    NOT the first render/.new.01 you encounter.
  • Orig Source Clip length = the SourceClip.length of the hop that *points to* that deepest locator mob.

- Source timecode rule
  • Source Clip start TC = nearest upstream mob that actually has a Timecode segment on the picture track.
  • StartTime(frames) = source_tc_start_frames + per-event SourceClip.start (offset).
  • EndTime(frames)   = StartTime + Length.

- Effects handling
  • Never replace a real source clip with a placeholder.
  • OperationGroups that *wrap* a SourceClip are normal source events with an effect name attached.
  • OperationGroups with no SourceClip = FX on filler → placeholder PNG named from plugin/op.
  • Pan & Zoom on a SourceClip → treat as still override (extract hidden/binary file path, sanitize).
  • Pan & Zoom on filler → still resolve real still path (no placeholder).

- Labels
  • DiskLabel/TapeID: best-effort crawl of _IMPORTSETTING → TaggedValueAttributeList → _IMPORTDISKLAB,
    with fallbacks to MobAttributeList/UserComments.

Note: pyaaf2 structure availability differs by file, so some attribute walks are defensive.
"""
from __future__ import annotations

import os
import io
import math
import urllib.parse
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any

# Lazy import aaf2; install from local tarball if needed.
def _ensure_aaf2():
    try:
        import aaf2  # type: ignore
        return aaf2
    except Exception:
        # Try local install (offline) if tarball provided
        tb = "/mnt/data/pyaaf2-1.4.0.tar.gz"
        if os.path.exists(tb):
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", tb])
            import aaf2  # type: ignore
            return aaf2
        raise

aaf2 = _ensure_aaf2()

# ---- Formatting helpers ----

def frames_to_tc(frames: int, fps: float = 25.0, drop: bool = False) -> str:
    try:
        f = int(frames)
        r = int(round(float(fps))) if fps else 25
        sep = ";" if drop else ":"
        h = f // (3600 * r)
        m = (f % (3600 * r)) // (60 * r)
        s = (f % (60 * r)) // r
        fr = f % r
        return f"{h:02}:{m:02}:{s:02}{sep}{fr:02}"
    except Exception:
        return "N/A"

# ---- CSV schema ----
CSV_HEADER = [
    "Event", "Event Name", "Clip Name", "Source File Name", "Source File Path",
    "DiskLabel", "TapeID", "SourceMobID", "TrackID", "Source Clip EditRate",
    "Timeline Start TC", "Source Clip start time code", "StartTime (frames)",
    "EndTime (frames)", "Event Length (frames)", "Orig Source Clip length",
    "Effect Name", "Keyframe Details"
]

# ---- Core model shims ----

def build_mob_map(f) -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    for mob in f.content.mobs():
        try:
            d[str(getattr(mob, "mob_id", ""))] = mob
        except Exception:
            continue
    return d

# Choose a main sequence (CompositionMob with a Sequence in a video slot)

def choose_main_sequence(f):
    from aaf2.components import Sequence
    for mob in f.content.mobs():
        try:
            # Heuristic: composition mobs typically have slots containing a Sequence
            for slot in getattr(mob, "slots", []):
                seg = getattr(slot, "segment", None)
                if isinstance(seg, Sequence):
                    return mob, slot, seg
        except Exception:
            continue
    return None, None, None

# ---- Effect helpers ----

def operation_group_effect_name(og) -> str:
    # Prefer plugin attrs if exposed; otherwise, fallback to operation def name
    try:
        # ComponentAttributeList may expose _EFFECT_PLUGIN_NAME / _EFFECT_PLUGIN_CLASS
        attrs = getattr(og, "attributes", None) or {}
        if isinstance(attrs, dict):
            name = attrs.get("_EFFECT_PLUGIN_NAME")
            klass = attrs.get("_EFFECT_PLUGIN_CLASS")
            if name and klass:
                return f"{klass} : {name}"
            if name:
                return str(name)
    except Exception:
        pass
    try:
        opdef = getattr(og, "operation", None)
        n = getattr(opdef, "name", None) or getattr(opdef, "identification", None)
        if n:
            return str(n)
    except Exception:
        pass
    return "Unknown Effect"


def operation_group_has_nested_sourceclip(og) -> bool:
    from aaf2.components import SourceClip, Sequence, OperationGroup
    try:
        for c in getattr(og, "components", []) or []:
            if isinstance(c, SourceClip):
                return True
            if isinstance(c, Sequence):
                for cc in getattr(c, "components", []) or []:
                    if isinstance(cc, SourceClip):
                        return True
                    if isinstance(cc, OperationGroup):
                        for c3 in getattr(cc, "components", []) or []:
                            if isinstance(c3, SourceClip):
                                return True
    except Exception:
        pass
    return False


def first_nested_sourceclip(node):
    from aaf2.components import SourceClip, Sequence, OperationGroup
    try:
        if isinstance(node, SourceClip):
            return node
        if isinstance(node, OperationGroup):
            for c in getattr(node, "components", []) or []:
                sc = first_nested_sourceclip(c)
                if sc is not None:
                    return sc
        if isinstance(node, Sequence):
            for c in getattr(node, "components", []) or []:
                sc = first_nested_sourceclip(c)
                if sc is not None:
                    return sc
    except Exception:
        pass
    return None

# ---- Chain traversal (deepest locator preference) ----

def climb_chain_deepest_locator(start_sc, mob_map, max_hops: int = 128):
    """
    Walk UMID chain via SourceClip.source_id + source_slot_id.

    Returns:
      chain: list of mobs we land on at each hop
      offset_sum: sum of SourceClip.start along the path (per-event source offset)
      edge_len_to_last: SourceClip.length on the hop that *points to* the deepest locator mob
      last_locator_mob: the deepest mob in the chain with a Locator (camera master preferred)
      last_url: URL string from the chosen Locator
    """
    chain: List[Any] = []
    cur_sc = start_sc
    offset_sum = int(getattr(cur_sc, "start", 0) or 0)

    last_locator_mob = None
    edge_len_to_last = int(getattr(cur_sc, "length", 0) or 0)
    last_url = None

    for _ in range(max_hops):
        try:
            target_id = getattr(cur_sc, "source_id", None)
            target_slot_id = getattr(cur_sc, "source_slot_id", None)
        except Exception:
            break
        if not target_id:
            break
        mob = mob_map.get(str(target_id))
        if not mob:
            break
        chain.append(mob)

        # If mob has a locator, keep it as the *current best* (we prefer the deepest one)
        try:
            ess = getattr(mob, "essence_descriptor", None)
            locs = getattr(ess, "locators", None) if ess else None
            if locs and len(locs) > 0:
                last_locator_mob = mob
                edge_len_to_last = int(getattr(cur_sc, "length", 0) or 0)
                try:
                    url = getattr(locs[0], "url", None) or getattr(locs[0], "url_string", None)
                except Exception:
                    url = None
                last_url = url
        except Exception:
            pass

        # Move to the referenced slot in this mob and keep following
        next_sc = None
        try:
            from aaf2.components import SourceClip, Sequence, OperationGroup
            for slot in getattr(mob, "slots", []) or []:
                sid = getattr(slot, "slot_id", None)
                if target_slot_id is not None and sid != target_slot_id:
                    continue
                seg = getattr(slot, "segment", None)
                if isinstance(seg, SourceClip):
                    next_sc = seg
                    break
                if isinstance(seg, Sequence):
                    for comp in getattr(seg, "components", []) or []:
                        if isinstance(comp, SourceClip):
                            next_sc = comp; break
                        if isinstance(comp, OperationGroup):
                            # unwrap shallow OG
                            for c2 in getattr(comp, "components", []) or []:
                                if isinstance(c2, SourceClip):
                                    next_sc = c2; break
                            if next_sc: break
                    if next_sc: break
        except Exception:
            next_sc = None

        if not next_sc:
            break
        offset_sum += int(getattr(next_sc, "start", 0) or 0)
        cur_sc = next_sc

    return chain, offset_sum, edge_len_to_last, last_locator_mob, last_url

# ---- Upstream timecode search (nearest picture TC) ----

def nearest_upstream_timecode_frames(start_sc, mob_map) -> Tuple[int, float, bool]:
    """Return (tc_start_frames, edit_rate, is_drop) from the closest mob in the chain
    that carries a Timecode segment on a video slot. If none found, (0, 25.0, False).
    """
    from aaf2.components import Sequence, Timecode

    # Follow the same path as chain traversal but look for Timecode on each mob we touch
    cur_sc = start_sc
    for _ in range(128):
        target_id = getattr(cur_sc, "source_id", None)
        target_slot_id = getattr(cur_sc, "source_slot_id", None)
        mob = mob_map.get(str(target_id)) if target_id else None
        if not mob:
            break

        # Scan slots for Timecode
        try:
            for slot in getattr(mob, "slots", []) or []:
                seg = getattr(slot, "segment", None)
                if isinstance(seg, Timecode):
                    # Collect Start, EditRate, Drop
                    try:
                        start = int(getattr(seg, "start", 0) or 0)
                    except Exception:
                        start = 0
                    try:
                        rate = float(getattr(seg, "fps", None) or getattr(seg, "edit_rate", 25.0) or 25.0)
                    except Exception:
                        rate = 25.0
                    try:
                        drop = bool(getattr(seg, "drop", False))
                    except Exception:
                        drop = False
                    return start, rate, drop
        except Exception:
            pass

        # Advance to next hop (similar to chain)
        next_sc = None
        try:
            from aaf2.components import SourceClip, OperationGroup
            for slot in getattr(mob, "slots", []) or []:
                if getattr(slot, "slot_id", None) == target_slot_id or target_slot_id is None:
                    seg = getattr(slot, "segment", None)
                    if isinstance(seg, SourceClip):
                        next_sc = seg
                        break
                    if isinstance(seg, Sequence):
                        for comp in getattr(seg, "components", []) or []:
                            if isinstance(comp, SourceClip):
                                next_sc = comp; break
                            if isinstance(comp, OperationGroup):
                                for c2 in getattr(comp, "components", []) or []:
                                    if isinstance(c2, SourceClip):
                                        next_sc = c2; break
                                if next_sc: break
                        if next_sc: break
        except Exception:
            next_sc = None
        if not next_sc:
            break
        cur_sc = next_sc

    return 0, 25.0, False

# ---- DiskLabel / TapeID best-effort ----

def extract_labels(mob) -> Tuple[str, str]:
    disk = ""
    tape = ""
    # Crawl attributes dict if present
    try:
        attrs = getattr(mob, "attributes", None)
        if isinstance(attrs, dict):
            disk = disk or attrs.get("_IMPORTDISKLAB", "") or attrs.get("DiskLabel", "")
            tape = tape or attrs.get("TapeID", "")
    except Exception:
        pass
    # Try MobAttributeList-like structures
    try:
        for k in ("mob_attributes", "user_comments", "comments"):
            seq = getattr(mob, k, None)
            if seq:
                for item in seq:
                    try:
                        n = getattr(item, "name", "")
                        v = getattr(item, "value", "")
                        if not disk and n in ("_IMPORTDISKLAB", "DiskLabel"):
                            disk = v
                        if not tape and n == "TapeID":
                            tape = v
                    except Exception:
                        continue
    except Exception:
        pass
    return disk or "N/A", tape or "N/A"

# ---- FX placeholder PNG naming ----

def placeholder_png_for_effect(name: str) -> str:
    safe = name.strip().lower().replace(" ", "_")
    safe = "".join(ch for ch in safe if ch.isalnum() or ch in ("_", "-"))
    return f"{safe or 'effect'}_placeholder.png"

# ---- Timeline traversal ----

def collect_events_from_sequence(seq, timeline_start_frames: int = 0, edit_rate: float = 25.0):
    from aaf2.components import Sequence, SourceClip, OperationGroup
    events: List[Dict[str, Any]] = []
    t = int(timeline_start_frames)

    comps = getattr(seq, "components", []) or []
    for comp in comps:
        length = int(getattr(comp, "length", 0) or 0)
        if isinstance(comp, SourceClip):
            events.append({
                "kind": "source",
                "node": comp,
                "timeline_start": t,
                "length": length,
                "edit_rate": edit_rate,
                "effect": None
            })
        elif isinstance(comp, OperationGroup):
            # Try to unwrap; if there is a SourceClip inside, treat as source+effect
            sc = first_nested_sourceclip(comp)
            if sc is not None:
                events.append({
                    "kind": "source",
                    "node": sc,
                    "timeline_start": t,
                    "length": int(getattr(sc, "length", 0) or 0),
                    "edit_rate": edit_rate,
                    "effect": operation_group_effect_name(comp),
                    "effect_node": comp
                })
            else:
                # True FX on filler
                events.append({
                    "kind": "filler_fx",
                    "node": comp,
                    "timeline_start": t,
                    "length": length,
                    "edit_rate": edit_rate,
                    "effect": operation_group_effect_name(comp),
                    "effect_node": comp
                })
        elif isinstance(comp, Sequence):
            # Recurse
            sub = collect_events_from_sequence(comp, t, edit_rate)
            events.extend(sub)
        # advance timeline by this component length
        t += length
    return events

# ---- P&Z hidden file path (best effort) ----

def find_panzoom_still_path(effect_node) -> Optional[str]:
    # Heuristics: inspect parameters/attributes for a stored file path/URL
    try:
        attrs = getattr(effect_node, "attributes", None)
        if isinstance(attrs, dict):
            for k, v in attrs.items():
                if isinstance(v, str) and ("file://" in v or v.startswith("/")):
                    return v
        # Some Avid builds stash a bytes-ish URL string
        for k, v in (attrs or {}).items():
            if isinstance(v, (bytes, bytearray)):
                try:
                    txt = v.decode("utf-16-le", errors="ignore")
                    if "file://" in txt:
                        return txt
                except Exception:
                    pass
    except Exception:
        pass
    # Fall back: search common parameter containers if exposed
    try:
        params = getattr(effect_node, "parameters", None)
        if params:
            for p in params:
                for attr_name in ("value", "default", "name"):
                    try:
                        val = getattr(p, attr_name, None)
                        if isinstance(val, str) and "file://" in val:
                            return val
                    except Exception:
                        continue
    except Exception:
        pass
    return None

# ---- Builder ----

def build_csv_rows(aaf_path: str):
    rows: List[List[Any]] = []
    summary: Dict[str, Any] = {}

    with aaf2.open(aaf_path) as f:
        mob_map = build_mob_map(f)
        comp_mob, comp_slot, comp_seq = choose_main_sequence(f)
        if not comp_seq:
            raise RuntimeError("No main Sequence found in this AAF")

        # Timeline base TC from sequence timecode track if present (not critical for rows)
        # We keep per-event source TC as per rules.
        timeline_rate = float(getattr(comp_slot, "edit_rate", 25.0) or 25.0)
        timeline_start_tc = 0
        try:
            # If composition has its own timecode track
            from aaf2.components import Timecode
            for slot in getattr(comp_mob, "slots", []) or []:
                seg = getattr(slot, "segment", None)
                if isinstance(seg, Timecode):
                    timeline_start_tc = int(getattr(seg, "start", 0) or 0)
                    break
        except Exception:
            pass

        events = collect_events_from_sequence(comp_seq, timeline_start_tc, timeline_rate)

        # Build rows
        ev_no = 0
        for ev in events:
            kind = ev["kind"]
            t0 = int(ev["timeline_start"])  # timeline start (frames)
            elen = int(ev["length"])        # event length
            edit_rate = float(ev["edit_rate"]) or 25.0

            if kind == "filler_fx":
                eff_name = ev.get("effect") or "Unknown Effect"
                # Try P&Z on filler special-case: resolve still path (no placeholder)
                if "pan" in eff_name.lower() and "zoom" in eff_name.lower():
                    still = find_panzoom_still_path(ev.get("effect_node"))
                    path = urllib.parse.unquote(still or "N/A")
                    name = os.path.basename(path) if path != "N/A" else "PanZoomStill"
                    ev_no += 1
                    rows.append([
                        ev_no, f"(FX) {name}", name, name, os.path.dirname(path) if path != "N/A" else "N/A",
                        "N/A", "N/A", "FX_ON_FILLER", "V1", edit_rate,
                        frames_to_tc(t0, edit_rate, False), "N/A", t0, t0 + elen, elen,
                        0, eff_name, "Effect on filler (P&Z)"
                    ])
                else:
                    # General filler → placeholder
                    png = placeholder_png_for_effect(eff_name)
                    ev_no += 1
                    rows.append([
                        ev_no, f"(FX Placeholder) {png}", png, png, "N/A",
                        "N/A", "N/A", "FX_ON_FILLER", "V1", edit_rate,
                        frames_to_tc(t0, edit_rate, False), "N/A", t0, t0 + elen, elen,
                        0, eff_name, "Effect on filler"
                    ])
                continue

            # Source event (possibly wrapped by OG with an effect)
            sc = ev["node"]
            eff_name = ev.get("effect") or "N/A"

            # Resolve deepest locator source + offsets
            chain, src_off, edge_len, end_mob, end_url = climb_chain_deepest_locator(sc, mob_map)
            # Upstream picture timecode (nearest)
            tc_frames, tc_rate, is_df = nearest_upstream_timecode_frames(sc, mob_map)
            start_frames = int(tc_frames) + int(src_off)
            end_frames = start_frames + elen

            # Labels
            disk, tape = ("N/A", "N/A")
            if end_mob:
                d, t = extract_labels(end_mob)
                disk = d or "N/A"; tape = t or "N/A"

            # File path
            path = urllib.parse.unquote(end_url or "N/A")
            name = os.path.basename(path) if path != "N/A" else getattr(end_mob, "name", "Unknown")

            # Pan & Zoom override on a *source* clip: treat as real still override (keep real path)
            if "pan" in str(eff_name).lower() and "zoom" in str(eff_name).lower():
                still = find_panzoom_still_path(ev.get("effect_node")) or path
                if still:
                    path = urllib.parse.unquote(still)
                    name = os.path.basename(path)

            ev_no += 1
            rows.append([
                ev_no,
                name,
                name,
                name,
                os.path.dirname(path) if path != "N/A" else "N/A",
                disk,
                tape,
                str(getattr(end_mob, "mob_id", "N/A")) if end_mob else "N/A",
                "V1",
                edit_rate,
                frames_to_tc(t0, edit_rate, False),
                frames_to_tc(tc_frames, tc_rate, is_df),
                start_frames,
                end_frames,
                elen,
                int(edge_len or 0),
                eff_name,
                ""
            ])

        # Summary (loose)
        total_len = sum(int(r[14]) for r in rows)
        summary = {
            "Timeline Edit Rate": f"{timeline_rate}",
            "Timeline Start (frames)": timeline_start_tc,
            "Timeline Length (frames)": total_len,
            "Events": len(rows),
        }

    return rows, summary

if __name__ == "__main__":
    import argparse, csv
    ap = argparse.ArgumentParser()
    ap.add_argument("aaf", help="Input AAF path")
    ap.add_argument("--out", default="super_edl_fx.csv")
    args = ap.parse_args()

    rows, summary = build_csv_rows(args.aaf)
    # print summary
    for k, v in summary.items():
        print(f"{k}: {v}")
    # write CSV
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        w.writerows(rows)
    print(f"Wrote {args.out}")
