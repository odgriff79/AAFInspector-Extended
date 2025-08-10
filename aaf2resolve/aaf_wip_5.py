import aaf2, urllib.parse, os
from collections import deque

# -------- Timecode helpers --------
def frames_to_tc(fc, fps=25.0, drop=False):
    if fc is None: return "N/A"
    try:
        fc = int(fc); fps = float(fps)
    except: 
        return "N/A"
    sep = ";" if drop else ":"
    h = fc // int(3600*fps)
    m = (fc % int(3600*fps)) // int(60*fps)
    s = (fc % int(60*fps)) // int(fps)
    f = fc % int(fps)
    return f"{h:02}:{m:02}:{s:02}{sep}{f:02}"

def unwrap(x):
    try:
        if hasattr(x,"value"): return unwrap(x.value)
        return x
    except Exception:
        return x

# -------- Composition / slot picking --------
def tc_start_from_comp(comp):
    from aaf2.components import Timecode
    for s in comp.slots:
        seg = getattr(s,"segment",None)
        if isinstance(seg, Timecode):
            try:
                start = int(getattr(seg,"start",0) or 0)
                fps = float(getattr(seg,"fps",25) or 25)
                drop = bool(getattr(seg,"drop",False))
                return start, fps, drop
            except Exception:
                pass
    return 0, 25.0, False

def collect_source_events(seg, start_ofs):
    """Return list of SourceClip events with absolute timeline offsets (frames)."""
    from aaf2.components import SourceClip
    evs=[]; dq=deque([(seg, start_ofs)])
    while dq:
        n, ofs = dq.popleft()
        L = int(getattr(n,"length",0) or 0)
        if isinstance(n, SourceClip):
            sid = str(n.get("SourceID","").value) if "SourceID" in n.keys() else ""
            slot = int(n.get("SourceMobSlotID",0).value) if "SourceMobSlotID" in n.keys() else 0
            start = int((n.get("StartTime") or n.get("Start")).value) if (n.get("StartTime") or n.get("Start")) else 0
            evs.append({"ofs": ofs, "len": L, "mobid": sid, "slotid": slot, "src_off": start, "node": n})
        acc=0
        for attr in ("components","segments","input_segments"):
            it=getattr(n,attr,None)
            if it:
                for c in it:
                    dq.append((c, ofs+acc))
                    acc += int(getattr(c,"length",0) or 0)
    return evs

def choose_background_slot(comp, tstart):
    """Heuristic: picture Sequences whose first event starts at timeline start; choose longest."""
    from aaf2.components import Sequence
    candidates=[]
    for s in comp.slots:
        seg = getattr(s,"segment",None)
        if not isinstance(seg, Sequence): 
            continue
        try:
            dd = getattr(seg,"data_definition",None)
            if dd and "Picture" not in str(dd):
                continue
        except Exception:
            pass
        evs = collect_source_events(seg, tstart)
        if not evs: 
            continue
        first_ofs = min(e["ofs"] for e in evs)
        total_len = sum(e["len"] for e in evs)
        candidates.append((s, first_ofs, total_len, len(evs)))
    exact = [c for c in candidates if c[1] == tstart]
    pool = exact if exact else candidates
    if not pool: return None
    pool.sort(key=lambda x:(-x[2], -x[3]))
    return pool[0][0]

# -------- Mob resolution + metadata --------
def first_sourceclip_in(seg):
    from aaf2.components import SourceClip
    dq=deque([seg]); seen=set()
    while dq:
        n=dq.popleft()
        if id(n) in seen: 
            continue
        seen.add(id(n))
        if isinstance(n, SourceClip): 
            return n
        for attr in ("components","segments","input_segments"):
            it=getattr(n,attr,None)
            if it:
                for x in it: dq.append(x)
    return None

def pick_slot(mob, target_slotid):
    exact=None; picture=None; first=None
    for s in mob.slots:
        sid = int(getattr(s,"slot_id", getattr(s,"physical_track_number",0)) or 0)
        if first is None: first=s
        if target_slotid and sid==target_slotid: exact=s
        try:
            dd = getattr(getattr(s,"segment",None),"data_definition",None)
            if dd and "Picture" in str(dd):
                if picture is None: picture = s
        except Exception:
            pass
    return exact or picture or first

def scan_for_url_in_mob(mob):
    if "EssenceDescription" not in mob.keys(): return None
    ed = mob["EssenceDescription"]
    try:
        it = list(ed.value) if hasattr(ed.value,"__iter__") else [ed.value]
    except Exception:
        it = [ed]
    for desc in it:
        try:
            if hasattr(desc,"keys") and "Locator" in desc.keys():
                for loc in desc["Locator"]:
                    if hasattr(loc,"keys") and "URLString" in loc.keys():
                        return str(unwrap(loc["URLString"]))
        except Exception:
            pass
    return None

def resolve_end_mob(mob_map, first_mobid, first_slotid, max_hops=20):
    cur = mob_map.get(first_mobid); last_slot = first_slotid; hops=0
    while cur and hops < max_hops:
        url = scan_for_url_in_mob(cur)
        if url: return cur, url, hops
        chosen = pick_slot(cur, last_slot)
        seg = getattr(chosen,"segment",None) if chosen else None
        sc = first_sourceclip_in(seg) if seg else None
        if not sc: break
        next_id = str(unwrap(sc.get("SourceID","")))
        next_slot = int(unwrap(sc.get("SourceMobSlotID",0)))
        cur = mob_map.get(next_id)
        last_slot = next_slot if next_slot else last_slot
        hops += 1
    return cur, None, hops

# Deep metadata scan (TapeID / DiskLabel, including _IMPORTDISKLAB)
def _collect_tagpairs(container):
    out=[]
    try:
        for item in container:
            try:
                name = None; value=None
                if hasattr(item, "keys"):
                    if "Name" in item.keys(): name = unwrap(item["Name"])
                    if "Value" in item.keys(): value = unwrap(item["Value"])
                if name is None and hasattr(item, "name"): name = unwrap(getattr(item,"name"))
                if value is None and hasattr(item, "value"): value = unwrap(getattr(item,"value"))
                if name is not None:
                    out.append((str(name), value))
            except Exception:
                continue
    except Exception:
        pass
    return out

def deep_disklabel_and_tapeid(obj, maxdepth=6):
    disk=None; tape=None
    seen=set()
    dq=deque([(obj,0)])
    while dq:
        n, d = dq.popleft()
        if id(n) in seen or d>maxdepth: 
            continue
        seen.add(id(n))
        # direct attribute-name pairs
        try:
            if hasattr(n,"keys"):
                if "MobAttributeList" in n.keys():
                    for a in n["MobAttributeList"]:
                        pairs = _collect_tagpairs(a.get("TaggedValueAttributeList", [])) if hasattr(a,"keys") and "TaggedValueAttributeList" in a.keys() else _collect_tagpairs([a])
                        for k,v in pairs:
                            lk = k.strip().lower()
                            if lk in ("disklabel","_importdisklab") and not disk:
                                disk = str(v) if v is not None else disk
                            if lk == "tapeid" and not tape:
                                tape = str(v) if v is not None else tape
                if "UserComments" in n.keys():
                    for tv in n["UserComments"]:
                        name = str(unwrap(tv.get("Name", getattr(tv,"name",""))) or "").strip().lower()
                        val  = unwrap(tv.get("Value", getattr(tv,"value",None)))
                        if name in ("disklabel","_importdisklab") and not disk:
                            disk = str(val) if val is not None else disk
                        if name == "tapeid" and not tape:
                            tape = str(val) if val is not None else tape
                if "TaggedValueAttributeList" in n.keys():
                    for tv in n["TaggedValueAttributeList"]:
                        name = str(unwrap(tv.get("Name", getattr(tv,"name",""))) or "").strip().lower()
                        val  = unwrap(tv.get("Value", getattr(tv,"value",None)))
                        if name in ("disklabel","_importdisklab") and not disk:
                            disk = str(val) if val is not None else disk
                        if name == "tapeid" and not tape:
                            tape = str(val) if val is not None else tape
        except Exception:
            pass
        # enqueue children-ish
        for key in ("EssenceDescription","Slots","MobAttributeList","UserComments","TaggedValueAttributeList"):
            try:
                if hasattr(n,"keys") and key in n.keys():
                    cont = n[key]
                    try:
                        for sub in cont:
                            dq.append((sub,d+1))
                    except TypeError:
                        dq.append((cont,d+1))
            except Exception:
                pass
        # general: iterate over slot segments
        try:
            for s in getattr(n,"slots",[]):
                dq.append((s,d+1))
                seg = getattr(s,"segment",None)
                if seg: dq.append((seg,d+1))
        except Exception:
            pass
        for attr in ("components","segments","input_segments"):
            try:
                it=getattr(n,attr,None)
                if it:
                    for c in it: dq.append((c,d+1))
            except Exception:
                pass
    return disk, tape

def source_tc_from_mob(mob):
    """Return (start_frames, fps, drop) from a source Timecode component if present."""
    from aaf2.components import Timecode, Sequence
    # search slots -> Sequence -> Timecode
    for s in mob.slots:
        seg = getattr(s,"segment",None)
        if isinstance(seg, Timecode):
            start = int(getattr(seg,"start",0) or 0)
            fps = float(getattr(seg,"fps",25) or 25)
            drop = bool(getattr(seg,"drop",False))
            return start, fps, drop
        if isinstance(seg, Sequence):
            # search components for Timecode
            try:
                for c in seg.components:
                    if isinstance(c, Timecode):
                        start = int(getattr(c,"start",0) or 0)
                        fps = float(getattr(c,"fps",25) or 25)
                        drop = bool(getattr(c,"drop",False))
                        return start, fps, drop
            except Exception:
                pass
    return 0, 25.0, False

def descriptor_length(mob):
    if "EssenceDescription" not in mob.keys():
        return 0
    try:
        ed = mob["EssenceDescription"]
        seq = list(ed.value) if hasattr(ed.value,"__iter__") else [ed.value]
        for d in seq:
            if hasattr(d,"keys") and "Length" in d.keys():
                try:
                    return int(unwrap(d["Length"]))
                except Exception:
                    pass
    except Exception:
        pass
    return 0
import aaf2, urllib.parse, os
from collections import deque

# -------- Timecode helpers --------
def frames_to_tc(fc, fps=25.0, drop=False):
    if fc is None: return "N/A"
    try:
        fc = int(fc); fps = float(fps)
    except: 
        return "N/A"
    sep = ";" if drop else ":"
    h = fc // int(3600*fps)
    m = (fc % int(3600*fps)) // int(60*fps)
    s = (fc % int(60*fps)) // int(fps)
    f = fc % int(fps)
    return f"{h:02}:{m:02}:{s:02}{sep}{f:02}"

def unwrap(x):
    try:
        if hasattr(x,"value"): return unwrap(x.value)
        return x
    except Exception:
        return x

# -------- Composition / slot picking --------
def tc_start_from_comp(comp):
    from aaf2.components import Timecode
    for s in comp.slots:
        seg = getattr(s,"segment",None)
        if isinstance(seg, Timecode):
            try:
                start = int(getattr(seg,"start",0) or 0)
                fps = float(getattr(seg,"fps",25) or 25)
                drop = bool(getattr(seg,"drop",False))
                return start, fps, drop
            except Exception:
                pass
    return 0, 25.0, False

def collect_source_events(seg, start_ofs):
    """Return list of SourceClip events with absolute timeline offsets (frames)."""
    from aaf2.components import SourceClip
    evs=[]; dq=deque([(seg, start_ofs)])
    while dq:
        n, ofs = dq.popleft()
        L = int(getattr(n,"length",0) or 0)
        if isinstance(n, SourceClip):
            sid = str(n.get("SourceID","").value) if "SourceID" in n.keys() else ""
            slot = int(n.get("SourceMobSlotID",0).value) if "SourceMobSlotID" in n.keys() else 0
            start = int((n.get("StartTime") or n.get("Start")).value) if (n.get("StartTime") or n.get("Start")) else 0
            evs.append({"ofs": ofs, "len": L, "mobid": sid, "slotid": slot, "src_off": start, "node": n})
        acc=0
        for attr in ("components","segments","input_segments"):
            it=getattr(n,attr,None)
            if it:
                for c in it:
                    dq.append((c, ofs+acc))
                    acc += int(getattr(c,"length",0) or 0)
    return evs

def choose_background_slot(comp, tstart):
    """Heuristic: picture Sequences whose first event starts at timeline start; choose longest."""
    from aaf2.components import Sequence
    candidates=[]
    for s in comp.slots:
        seg = getattr(s,"segment",None)
        if not isinstance(seg, Sequence): 
            continue
        try:
            dd = getattr(seg,"data_definition",None)
            if dd and "Picture" not in str(dd):
                continue
        except Exception:
            pass
        evs = collect_source_events(seg, tstart)
        if not evs: 
            continue
        first_ofs = min(e["ofs"] for e in evs)
        total_len = sum(e["len"] for e in evs)
        candidates.append((s, first_ofs, total_len, len(evs)))
    exact = [c for c in candidates if c[1] == tstart]
    pool = exact if exact else candidates
    if not pool: return None
    pool.sort(key=lambda x:(-x[2], -x[3]))
    return pool[0][0]

# -------- Mob resolution + metadata --------
def first_sourceclip_in(seg):
    from aaf2.components import SourceClip
    dq=deque([seg]); seen=set()
    while dq:
        n=dq.popleft()
        if id(n) in seen: 
            continue
        seen.add(id(n))
        if isinstance(n, SourceClip): 
            return n
        for attr in ("components","segments","input_segments"):
            it=getattr(n,attr,None)
            if it:
                for x in it: dq.append(x)
    return None

def pick_slot(mob, target_slotid):
    exact=None; picture=None; first=None
    for s in mob.slots:
        sid = int(getattr(s,"slot_id", getattr(s,"physical_track_number",0)) or 0)
        if first is None: first=s
        if target_slotid and sid==target_slotid: exact=s
        try:
            dd = getattr(getattr(s,"segment",None),"data_definition",None)
            if dd and "Picture" in str(dd):
                if picture is None: picture = s
        except Exception:
            pass
    return exact or picture or first

def scan_for_url_in_mob(mob):
    if "EssenceDescription" not in mob.keys(): return None
    ed = mob["EssenceDescription"]
    try:
        it = list(ed.value) if hasattr(ed.value,"__iter__") else [ed.value]
    except Exception:
        it = [ed]
    for desc in it:
        try:
            if hasattr(desc,"keys") and "Locator" in desc.keys():
                for loc in desc["Locator"]:
                    if hasattr(loc,"keys") and "URLString" in loc.keys():
                        return str(unwrap(loc["URLString"]))
        except Exception:
            pass
    return None

def resolve_end_mob(mob_map, first_mobid, first_slotid, max_hops=20):
    cur = mob_map.get(first_mobid); last_slot = first_slotid; hops=0
    while cur and hops < max_hops:
        url = scan_for_url_in_mob(cur)
        if url: return cur, url, hops
        chosen = pick_slot(cur, last_slot)
        seg = getattr(chosen,"segment",None) if chosen else None
        sc = first_sourceclip_in(seg) if seg else None
        if not sc: break
        next_id = str(unwrap(sc.get("SourceID","")))
        next_slot = int(unwrap(sc.get("SourceMobSlotID",0)))
        cur = mob_map.get(next_id)
        last_slot = next_slot if next_slot else last_slot
        hops += 1
    return cur, None, hops

# Deep metadata scan (TapeID / DiskLabel, including _IMPORTDISKLAB)
def _collect_tagpairs(container):
    out=[]
    try:
        for item in container:
            try:
                name = None; value=None
                if hasattr(item, "keys"):
                    if "Name" in item.keys(): name = unwrap(item["Name"])
                    if "Value" in item.keys(): value = unwrap(item["Value"])
                if name is None and hasattr(item, "name"): name = unwrap(getattr(item,"name"))
                if value is None and hasattr(item, "value"): value = unwrap(getattr(item,"value"))
                if name is not None:
                    out.append((str(name), value))
            except Exception:
                continue
    except Exception:
        pass
    return out

def deep_disklabel_and_tapeid(obj, maxdepth=6):
    disk=None; tape=None
    seen=set()
    dq=deque([(obj,0)])
    while dq:
        n, d = dq.popleft()
        if id(n) in seen or d>maxdepth: 
            continue
        seen.add(id(n))
        # direct attribute-name pairs
        try:
            if hasattr(n,"keys"):
                if "MobAttributeList" in n.keys():
                    for a in n["MobAttributeList"]:
                        pairs = _collect_tagpairs(a.get("TaggedValueAttributeList", [])) if hasattr(a,"keys") and "TaggedValueAttributeList" in a.keys() else _collect_tagpairs([a])
                        for k,v in pairs:
                            lk = k.strip().lower()
                            if lk in ("disklabel","_importdisklab") and not disk:
                                disk = str(v) if v is not None else disk
                            if lk == "tapeid" and not tape:
                                tape = str(v) if v is not None else tape
                if "UserComments" in n.keys():
                    for tv in n["UserComments"]:
                        name = str(unwrap(tv.get("Name", getattr(tv,"name",""))) or "").strip().lower()
                        val  = unwrap(tv.get("Value", getattr(tv,"value",None)))
                        if name in ("disklabel","_importdisklab") and not disk:
                            disk = str(val) if val is not None else disk
                        if name == "tapeid" and not tape:
                            tape = str(val) if val is not None else tape
                if "TaggedValueAttributeList" in n.keys():
                    for tv in n["TaggedValueAttributeList"]:
                        name = str(unwrap(tv.get("Name", getattr(tv,"name",""))) or "").strip().lower()
                        val  = unwrap(tv.get("Value", getattr(tv,"value",None)))
                        if name in ("disklabel","_importdisklab") and not disk:
                            disk = str(val) if val is not None else disk
                        if name == "tapeid" and not tape:
                            tape = str(val) if val is not None else tape
        except Exception:
            pass
        # enqueue children-ish
        for key in ("EssenceDescription","Slots","MobAttributeList","UserComments","TaggedValueAttributeList"):
            try:
                if hasattr(n,"keys") and key in n.keys():
                    cont = n[key]
                    try:
                        for sub in cont:
                            dq.append((sub,d+1))
                    except TypeError:
                        dq.append((cont,d+1))
            except Exception:
                pass
        # general: iterate over slot segments
        try:
            for s in getattr(n,"slots",[]):
                dq.append((s,d+1))
                seg = getattr(s,"segment",None)
                if seg: dq.append((seg,d+1))
        except Exception:
            pass
        for attr in ("components","segments","input_segments"):
            try:
                it=getattr(n,attr,None)
                if it:
                    for c in it: dq.append((c,d+1))
            except Exception:
                pass
    return disk, tape

def source_tc_from_mob(mob):
    """Return (start_frames, fps, drop) from a source Timecode component if present."""
    from aaf2.components import Timecode, Sequence
    # search slots -> Sequence -> Timecode
    for s in mob.slots:
        seg = getattr(s,"segment",None)
        if isinstance(seg, Timecode):
            start = int(getattr(seg,"start",0) or 0)
            fps = float(getattr(seg,"fps",25) or 25)
            drop = bool(getattr(seg,"drop",False))
            return start, fps, drop
        if isinstance(seg, Sequence):
            # search components for Timecode
            try:
                for c in seg.components:
                    if isinstance(c, Timecode):
                        start = int(getattr(c,"start",0) or 0)
                        fps = float(getattr(c,"fps",25) or 25)
                        drop = bool(getattr(c,"drop",False))
                        return start, fps, drop
            except Exception:
                pass
    return 0, 25.0, False

def descriptor_length(mob):
    if "EssenceDescription" not in mob.keys():
        return 0
    try:
        ed = mob["EssenceDescription"]
        seq = list(ed.value) if hasattr(ed.value,"__iter__") else [ed.value]
        for d in seq:
            if hasattr(d,"keys") and "Length" in d.keys():
                try:
                    return int(unwrap(d["Length"]))
                except Exception:
                    pass
    except Exception:
        pass
    return 0
