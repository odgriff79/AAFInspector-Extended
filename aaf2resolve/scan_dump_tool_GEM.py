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
        print(s.strip()) # Also print to console for real-time feedback

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
        s = safe_name(url)
        if s.lower().startswith("file://"):
            parsed = urlparse(s)
            path = unquote(parsed.path)
            # Handle Windows UNC paths correctly
            if os.name == 'nt' and path.startswith('/'):
                # urlparse might add a leading slash, e.g., /server/share -> //server/share
                return f"//{parsed.netloc}{path}"
            return parsed.netloc + path
        return s
    except:
        return safe_name(url)

# ==============================================================================
#                       HIGH-LEVEL SEQUENCE / FX TRAVERSAL
# ==============================================================================

def slot_media_kind(slot):
    try:
        segment = slot.segment
        if hasattr(segment, 'media_kind'):
            return str(segment.media_kind).lower()
        if hasattr(segment, 'data_def') and hasattr(segment.data_def, 'name'):
             return str(segment.data_def.name).lower()
    except:
        pass
    return ""

def comp_length_frames(comp, seq_fps):
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
    plugin_class, plugin_name = None, None
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
    animated, staticp = {}, {}
    try:
        params = list(getattr(op_group, "parameters", []) or [])
        for p in params:
            pname = safe_name(getattr(p, "name", "Param"))
            if bool(getattr(p, "is_varying", False)):
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
    info = {"scope_refs": False, "sourceclips": []}
    def harvest(seg):
        if seg is None: return
        cname = aaf_class_name(seg)
        if cname == "SourceClip":
            info["sourceclips"].append(seg)
        elif hasattr(seg, 'components'):
            for comp in seg.components: harvest(comp)
        elif hasattr(seg, 'input_segments'):
             for in_seg in seg.input_segments: harvest(in_seg)
    for attr in ("input_segments", "components"):
        try:
            for s in list(getattr(op_group, attr, []) or []): harvest(s)
        except: pass
    try:
        comps = list(getattr(op_group, "components", []) or [])
        for c in comps:
            if aaf_class_name(c) == "ScopeReference":
                info["scope_refs"] = True; break
    except: pass
    return info

NULL_UMID = "urn:smpte:umid:00000000.00000000.00000000.00000000.00000000.00000000.00000000.00000000"

def find_source_id(component):
    try:
        sid = None
        if hasattr(component, "source_id"):
             sid = safe_name(component.source_id)
        else:
            for p in component.properties():
                if p.name in ('SourceID','source_id'):
                    sid = safe_name(p.value); break
        if sid and sid != NULL_UMID:
            return sid
    except: pass
    return None

def is_file_backed_mob(mob):
    try:
        desc = getattr(mob, "descriptor", None)
        if not desc: return False
        if hasattr(desc, "locators") and list(desc.locators): return True
        return aaf_class_name(mob) == 'FileSourceMob'
    except: return False

def get_genuine_source_mob(mob_id, mob_map, dbg, visited=None):
    if visited is None: visited = set()
    if not mob_id or mob_id in visited: return None
    visited.add(mob_id)

    mob = mob_map.get(mob_id)
    if not mob:
        dbg.write(f"      MobID {mob_id} not found in mob_map.")
        return None

    if is_file_backed_mob(mob):
        dbg.write(f"      Reached file-backed source: {safe_name(getattr(mob, 'name', ''))}")
        return mob

    next_mob_id = None
    for slot in getattr(mob, "slots", []) or []:
        if 'picture' not in slot_media_kind(slot): continue
        seg = getattr(slot, "segment", None)
        def find_next(segm):
            if not segm: return None
            if aaf_class_name(segm) == "SourceClip": return find_source_id(segm)
            if hasattr(segm, "components"):
                for c in segm.components:
                    sid = find_next(c)
                    if sid: return sid
            if hasattr(segm, "input_segments"):
                for c in segm.input_segments:
                    sid = find_next(c)
                    if sid: return sid
            return None
        next_mob_id = find_next(seg)
        if next_mob_id:
            dbg.write(f"      Found next link in chain. Next MobID: {next_mob_id}")
            break
    
    if next_mob_id:
        final_mob = get_genuine_source_mob(next_mob_id, mob_map, dbg, visited)
        return final_mob or mob

    dbg.write(f"      End of chain. Returning current mob: {safe_name(getattr(mob, 'name',''))}")
    return mob

def get_locator_string(loc):
    for cand in ("url", "URLString", "path", "PathName"):
        if hasattr(loc, cand):
            s = safe_name(getattr(loc, cand))
            if s: return s
    try:
        for p in loc.properties():
            if p.name in ("URLString", "url", "PathName", "path"):
                s = safe_name(p.value)
                if s: return s
    except: pass
    return ""

def extract_mob_locators_tape_disk(mob, dbg: DebugLog):
    locs, tape, disk = [], "", ""
    try:
        desc = getattr(mob, "descriptor", None)
        if desc and hasattr(desc, "locators"):
            for loc in list(desc.locators) or []:
                raw = get_locator_string(loc)
                if raw:
                    dec = decode_locator_url_to_path(raw)
                    if dec and dec not in locs:
                        locs.append(dec)
                        dbg.write(f"        [locator] {dec}")
    except: pass
    try:
        for c in getattr(mob, "user_comments", []) or []:
            if "tape" in safe_name(getattr(c, "name", "")).lower() and not tape:
                tape = safe_name(getattr(c, "value", ""))
    except: pass
    try:
        if hasattr(mob, "attributes"):
            for a in mob.attributes:
                nm = safe_name(getattr(a, "name", "")).upper()
                if "DISK" in nm or "DISKLABEL" in nm or "_IMPORTDISKLAB" in nm:
                    val = safe_name(getattr(a, "value", ""))
                    if val and not disk: disk = val
    except: pass
    return locs, tape, disk

def get_sourceclip_start(comp):
    for cand in ("start_time", "startTime", "StartTime", "start"):
        if hasattr(comp, cand):
            try: return int(getattr(comp, cand) or 0)
            except: pass
    try:
        for p in comp.properties():
            if p.name in ("StartTime", "start_time", "start"):
                return int(p.value or 0)
    except: pass
    return 0

# ==============================================================================
#                         SUPER EDL CSV (sequence-focused)
# ==============================================================================

def build_mob_map(aaf_file, dbg: DebugLog):
    """Build a dictionary of all mobs in the file, keyed by MobID."""
    mob_map = {}
    mob_types = [
        ("CompositionMobs", aaf_file.content.compositionmobs()),
        ("MasterMobs", aaf_file.content.mastermobs()),
        ("SourceMobs", aaf_file.content.sourcemobs()),
    ]
    dbg.write("Building mob map...")
    for name, mob_iterator in mob_types:
        count = 0
        for mob in mob_iterator:
            try:
                mob_id = str(mob.mob_id)
                mob_map[mob_id] = mob
                count += 1
            except Exception as e:
                dbg.write(f"  WARNING: Could not get MobID for a mob in {name}: {e}")
        dbg.write(f"  Found {count} mobs in {name}")
    dbg.write(f"Total mobs in map: {len(mob_map)}")
    return mob_map

def super_edl_from_sequence(seq_mob, mob_map, out_csv_path, dbg: DebugLog):
    dbg.write(f"\n--- Starting Super EDL for Sequence: {safe_name(seq_mob.name)} ---")
    name = safe_name(getattr(seq_mob, "name", "(unnamed)"))
    slots = list(seq_mob.slots)
    start_tc, fps, drop = extract_timecode_from_slots(slots, dbg)
    fpsF = Fraction(getattr(fps, "numerator", 25), getattr(fps, "denominator", 1)) if not isinstance(fps, Fraction) else fps
    seq_rate = float(fpsF)

    events = []
    unique_sources = set()
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
                    keyframe_details_lines.append(f"    Time: {kp['time']} -> Value: {kp['value']}")
        if stat:
            if keyframe_details_lines: keyframe_details_lines.append("")
            keyframe_details_lines.append('--- Static Parameters ---')
            for pname, val in stat.items():
                keyframe_details_lines.append(f"  - {pname}: {val}")
        
        fx_by_rec_in.setdefault(rec_in, []).append({
            "name": eff_name,
            "keyframe_details": "\n".join(keyframe_details_lines) if keyframe_details_lines else "No parameters found."
        })
        dbg.write(f"    Recorded FX '{eff_name}' at record frame {rec_in}")

    def visit(node, rec, track_id_counter):
        cname = aaf_class_name(node)
        ln = comp_length_frames(node, fpsF)
        dbg.write(f"  [visit] Track={track_id_counter}, Type={cname}, Rec_In={rec}, Len={ln}")

        if cname == "Sequence":
            comps = list(node.components)
            cur = rec
            for comp in comps:
                cl = comp_length_frames(comp, fpsF)
                visit(comp, cur, track_id_counter)
                cur += cl
            return

        if cname == "SourceClip":
            source_mob_id = find_source_id(node)
            genuine_mob = get_genuine_source_mob(source_mob_id, mob_map, dbg)
            
            locs, tape, disk = ([], "", "")
            orig_length = 0
            
            if source_mob_id:
                unique_sources.add(source_mob_id)

            if genuine_mob:
                locs, tape, disk = extract_mob_locators_tape_disk(genuine_mob, dbg)
                descriptor = getattr(genuine_mob, 'descriptor', None)
                if descriptor:
                    orig_length = getattr(descriptor, 'length', 0)
            else:
                dbg.write(f"    WARNING: Could not resolve genuine source for MobID: {source_mob_id}")

            off = get_sourceclip_start(node)
            
            url_path = locs[0] if locs else ""
            src_fname = os.path.basename(url_path) if url_path else "N/A"
            src_dir = os.path.dirname(url_path) if url_path else "N/A"
            
            source_clip_edit_rate_frac = getattr(node, 'edit_rate', fpsF)
            source_clip_edit_rate_val = float(source_clip_edit_rate_frac)
            source_clip_edit_rate_str = fraction_to_str(source_clip_edit_rate_frac)

            events.append({
                "Event": len(events) + 1, "Event Name": src_fname, "Clip Name": src_fname,
                "Source File Name": src_fname, "Source File Path": src_dir, "DiskLabel": disk, "TapeID": tape,
                "SourceMobID": source_mob_id, "TrackID": track_id_counter, "Source Clip EditRate": source_clip_edit_rate_str,
                "Timeline Start TC": frames_to_tc(rec, seq_rate, drop),
                "Source Clip start time code": frames_to_tc(off, source_clip_edit_rate_val),
                "Source Clip offset": off, "StartTime": frames_to_tc(rec, seq_rate, drop),
                "End Time": frames_to_tc(rec + ln, seq_rate, drop), "Event Length": ln,
                "Source Clip start (frames)": off, "Source Clip offset (frames)": off, "StartTime (frames)": rec,
                "Orig Source Clip length": orig_length,
                "Effect Name": "; ".join([fx["name"] for fx in fx_by_rec_in.get(rec, [])]),
                "Keyframe Details": "\n\n".join([fx["keyframe_details"] for fx in fx_by_rec_in.get(rec, [])]),
            })
            return

        if cname == "OperationGroup":
            inputs = gather_inputs(node)
            is_fx_on_filler = not inputs['sourceclips'] and not inputs['scope_refs']
            
            record_fx(rec, node)

            if is_fx_on_filler:
                effect_name = extract_effect_name(node)
                events.append({
                    "Event": len(events) + 1, "Event Name": f"{effect_name} on Filler", "Clip Name": "FILLER",
                    "Source File Name": "FILLER", "Source File Path": "", "DiskLabel": "", "TapeID": "",
                    "SourceMobID": "FX_ON_FILLER", "TrackID": track_id_counter, "Source Clip EditRate": f"{int(seq_rate)}/1",
                    "Timeline Start TC": frames_to_tc(rec, seq_rate, drop), "Source Clip start time code": "00:00:00:00",
                    "Source Clip offset": 0, "StartTime": frames_to_tc(rec, seq_rate, drop),
                    "End Time": frames_to_tc(rec + ln, seq_rate, drop), "Event Length": ln,
                    "Source Clip start (frames)": 0, "Source Clip offset (frames)": 0, "StartTime (frames)": rec,
                    "Orig Source Clip length": ln,
                    "Effect Name": "; ".join([fx["name"] for fx in fx_by_rec_in.get(rec, [])]),
                    "Keyframe Details": "\n\n".join([fx["keyframe_details"] for fx in fx_by_rec_in.get(rec, [])]),
                })
            else:
                for attr in ("components", "input_segments"):
                    for ch in list(getattr(node, attr, []) or []):
                        visit(ch, rec, track_id_counter)
            return

        if hasattr(node, 'components'):
            cur = rec
            for comp in node.components:
                cl = comp_length_frames(comp, fpsF)
                visit(comp, cur, track_id_counter)
                cur += cl

    track_id_counter = 1
    for slot in slots:
        if "picture" in slot_media_kind(slot):
            dbg.write(f"\nProcessing Track {track_id_counter} (SlotID: {slot.slot_id})...")
            seg = getattr(slot, "segment", None)
            visit(seg, start_tc, track_id_counter)
        track_id_counter += 1

    with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        total_len = sum(e.get("Event Length", 0) for e in events)
        
        w.writerow(["Timeline Name", name])
        w.writerow(["Timeline Edit Rate", f"{seq_rate} {'(DF)' if drop else '(NDF)'}"])
        w.writerow(["Timeline Start", frames_to_tc(start_tc, seq_rate, drop)])
        w.writerow(["Timeline Length", f"{frames_to_tc(total_len, seq_rate, drop)} ({total_len} frames)"])
        w.writerow(["Total number of events found", len(events)])
        w.writerow(["Total number of unique sources", len(unique_sources)])
        w.writerow([])
        
        if events:
            hdr = ["Event","Event Name","Clip Name","Source File Name","Source File Path", "DiskLabel","TapeID","SourceMobID","TrackID","Source Clip EditRate", "Timeline Start TC","Source Clip start time code","Source Clip offset","StartTime","End Time","Event Length","Source Clip start (frames)", "Source Clip offset (frames)","StartTime (frames)","Orig Source Clip length", "Effect Name", "Keyframe Details"]
            w.writerow(hdr)
            for e in sorted(events, key=lambda x: x['StartTime (frames)']):
                w.writerow([e.get(h, "") for h in hdr])
    dbg.write(f"--- Finished Super EDL for Sequence: {name} ---")

# ==============================================================================
#                                  GUI
# ==============================================================================

class App:
    def __init__(self, root):
        self.root = root
        root.title("AAF Scan and Dump Tool")
        self.aaf_path = tk.StringVar()
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, height=18, width=120, font=("Consolas", 10))
        frm = tk.Frame(root)
        frm.pack(padx=10, pady=10, fill="x")
        tk.Label(frm, text="AAF file:").grid(row=0, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.aaf_path, width=80).grid(row=0, column=1, padx=6)
        tk.Button(frm, text="Browse", command=self.browse).grid(row=0, column=2)
        tk.Button(frm, text="Generate Report", command=self.run).grid(row=1, column=1, pady=8, sticky="e")
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
            
            with aaf2.open(path, 'r') as f:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                base_name = os.path.splitext(os.path.basename(path))[0]
                
                dbg_super_edl = DebugLog(os.path.join(os.path.dirname(path), f"{base_name}_super_edl_fx_{timestamp}.debug.txt"))
                
                mob_map = build_mob_map(f, dbg_super_edl)
                
                toplevel_comps = [mob for mob in f.content.toplevel() if aaf_class_name(mob) == 'CompositionMob']
                self._echo(f"Found {len(toplevel_comps)} Top-Level Composition Mob(s) to process.")

                for mob in toplevel_comps:
                    name = safe_name(getattr(mob, "name", "Sequence"))
                    out_csv = os.path.join(os.path.dirname(path), f"{name}_super_edl_fx_{timestamp}.csv")
                    self._echo(f"Writing Super EDL CSV: {out_csv}")
                    super_edl_from_sequence(mob, mob_map, out_csv, dbg_super_edl)
                
                dbg_super_edl.flush()

            self._echo("Done.")
            messagebox.showinfo("Done", "Scan complete. Check the folder for the CSV report and debug log.")
        except Exception as e:
            self._echo(f"ERROR: {e}\n{traceback.format_exc()}")
            messagebox.showerror("Error", f"{e}\n\nSee console/log for details.")

    def _echo(self, s): 
        self.log.insert("end", s + "\n"); self.log.see("end"); self.root.update_idletasks()

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
