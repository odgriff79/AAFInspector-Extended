import json
import os
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

# --- Timecode and Parsing Functions ---
def frames_to_tc(frame_count, fps=25.0, is_drop_frame=False):
    if frame_count is None or fps is None or fps <= 0:
        return "N/A"
    try:
        separator = ";" if is_drop_frame else ":"
        fc = int(frame_count)
        int_fps = round(float(fps))
        if int_fps <= 0:
            return "N/A"
        h = fc // (3600 * int_fps)
        m = (fc % (3600 * int_fps)) // (60 * int_fps)
        s = (fc % (60 * int_fps)) // int_fps
        f = fc % int_fps
        return f"{h:02}:{m:02}:{s:02}{separator}{f:02}"
    except (ValueError, TypeError):
        return "N/A"

# --- Find main sequence Mob and start-timecode ---
def find_main_sequence_mob_and_start_tc(root_node):
    if not isinstance(root_node, list) or root_node[0] != "list" or len(root_node) < 4:
        return None, 0, 25.0, False
    for mob in root_node[3]:
        if not (isinstance(mob, list) and len(mob) > 3):
            continue
        slots_node = next((c for c in mob[3] if isinstance(c, list) and c[0] == "Slots"), None)
        if not (slots_node and len(slots_node) > 3):
            continue
        is_sequence = any(
            isinstance(s, list) and len(s) > 3 and
            (seg := next((c for c in s[3] if c[0] == "Segment"), None)) and
            isinstance(seg, list) and len(seg) > 3 and
            isinstance(seg[3], list) and seg[3] and seg[3][0][0] == "Sequence"
            for s in slots_node[3]
        )
        if not is_sequence:
            continue
        start_tc, is_drop, edit_rate = 0, False, 25.0
        for s in slots_node[3]:
            rate_node = next((c for c in s[3] if isinstance(c, list) and c[0] == "EditRate"), None)
            if rate_node and len(rate_node) > 2:
                try:
                    rs = str(rate_node[2])
                    if "/" in rs:
                        n, d = map(float, rs.split("/"))
                        if d:
                            edit_rate = n / d
                    else:
                        edit_rate = float(rs)
                except:
                    pass
            seg_tm = next((c for c in s[3] if c[0] == "Segment"), None)
            if seg_tm and isinstance(seg_tm, list) and len(seg_tm) > 3 and isinstance(seg_tm[3], list) and seg_tm[3] and seg_tm[3][0][0] == "Timecode":
                tc_node = seg_tm[3][0]
                start_node = next((c for c in tc_node[3] if c[0] == "Start"), None)
                drop_node = next((c for c in tc_node[3] if c[0] == "Drop"), None)
                if drop_node and len(drop_node) > 2:
                    is_drop = bool(drop_node[2])
                if start_node and len(start_node) > 2:
                    try:
                        start_tc = int(start_node[2])
                    except:
                        pass
        return mob, start_tc, edit_rate, is_drop
    return None, 0, 25.0, False

# --- Find timeline effects (OperationGroup) ---
def find_timeline_effects(node, timeline_offset=0, results_list=None):
    """
    Recursively search a Sequence for OperationGroup effects.
    Records any OperationGroup that has plugin metadata, or is a MatteKey,
    or contains key-specific parameters (e.g., FG_KEY_OPACITY).
    """
    if results_list is None:
        results_list = []
    if not isinstance(node, list) or len(node) < 2:
        return results_list
    name = node[0]
    children = node[3] if len(node) > 3 else []

    if name == 'Sequence':
        comps = next((c for c in children if c[0] == 'Components'), None)
        if comps and len(comps) > 3:
            for comp in comps[3]:
                find_timeline_effects(comp, timeline_offset, results_list)
                ln = next((c for c in comp[3] if c[0] == 'Length'), None)
                if ln and len(ln) > 2:
                    try:
                        timeline_offset += int(ln[2])
                    except:
                        pass

    elif name == 'OperationGroup':
        record = False
        # A: plugin metadata
        attrs = next((c for c in node[3] if c[0] == 'ComponentAttributeList'), None)
        if attrs and len(attrs) > 3:
            plugin_keys = [a[0] for a in attrs[3] if isinstance(a, list)]
            if '_EFFECT_PLUGIN_NAME' in plugin_keys or '_EFFECT_PLUGIN_CLASS' in plugin_keys:
                record = True
        # B: MatteKey fallback
        if not record:
            op_def = next((c for c in node[3] if c[0] == 'Operation'), None)
            if op_def and len(op_def) > 2 and isinstance(op_def[2], str) and 'MatteKey' in op_def[2]:
                record = True
        # C: key-specific parameter fallback
        if not record:
            params = next((c for c in node[3] if c[0] == 'Parameters'), None)
            if params and len(params) > 3:
                for p in params[3]:
                    pname = next((x[2] for x in p[3] if x[0] == 'Name'), p[0])
                    if 'KEY' in pname.upper():
                        record = True
                        break
        if record:
            results_list.append({'node': node, 'start_frame': timeline_offset})
        # Do not recurse into found effects to avoid double-counting or picking up helpers
        return results_list

    else:
        for child in children:
            find_timeline_effects(child, timeline_offset, results_list)

    return results_list

# --- Extract effect details (name, length, keyframes) ---
def extract_effect_details(node):
    all_attrs = {}
    def collect(n):
        if not isinstance(n, list): return
        if n[0] == 'ComponentAttributeList' and len(n) > 3:
            for a in n[3]:
                if isinstance(a, list):
                    v = next((x for x in a[3] if x[0]=='Value'), None)
                    if v and len(v)>2:
                        all_attrs[a[0]] = v[2]
        for c in (n[3] if len(n)>3 else []):
            collect(c)
    collect(node)
    plugin_name  = all_attrs.get('_EFFECT_PLUGIN_NAME')
    plugin_class = all_attrs.get('_EFFECT_PLUGIN_CLASS')
    if plugin_class and plugin_name:
        effect_name = f"{plugin_class} : {plugin_name}"
    elif plugin_name:
        effect_name = plugin_name
    else:
        # Fallback to Operation definition, extracting clean name
        op = next((c for c in node[3] if c[0]=='Operation'), None)
        if op and len(op)>2 and isinstance(op[2], str):
            raw = op[2]
            name_part = raw.split(" ")[1] if " " in raw else raw
            effect_name = name_part.replace('_v2','').replace('_2','').replace('_',' ').strip()
        else:
            effect_name = 'Unknown Effect'

    # Length
    length = 0
    ln = next((c for c in node[3] if c[0]=='Length'), None)
    if ln and len(ln)>2:
        try: length = int(ln[2])
        except: pass
    animated = {}
    pn = next((c for c in node[3] if c[0]=='Parameters'), None)
    if pn and len(pn)>3:
        for p in pn[3]:
            pname = next((x[2] for x in p[3] if x[0]=='Name'), p[0])
            plist = next((x for x in p[3] if x[0]=='PointList'), None)
            kfs = []
            if plist and len(plist)>3:
                for cp in plist[3]:
                    if isinstance(cp, list) and cp[0]=='ControlPoint':
                        t = next((x[2] for x in cp[3] if x[0]=='Time'), '0')
                        v = next((x[2] for x in cp[3] if x[0]=='Value'), 'N/A')
                        kfs.append({'time': t, 'value': v})
            if kfs:
                animated[pname] = kfs
    return {'effect_name': effect_name, 'length': length, 'animated_params': animated}

class EffectAnalyzerApp:
    def __init__(self, root):
        root.title("AAF Detailed Effect Analyzer")
        root.geometry("800x600")
        tk.Button(root, text="Load AAF JSON File", command=self.load_and_process).pack(pady=10)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New",10))
        self.log.pack(expand=True, fill=tk.BOTH, padx=10, pady=5)
    def log_msg(self, msg, indent=0): self.log.insert(tk.END, "  "*indent+msg+"\n"); self.log.see(tk.END)
    def load_and_process(self):
        path = filedialog.askopenfilename(title="Select AAF JSON", filetypes=[("JSON Files","*.json")])
        if not path: return
        self.log.delete(1.0, tk.END); self.log_msg(f"✅ Loaded JSON file: {os.path.basename(path)}\n")
        try: data = json.load(open(path,'r',encoding='utf-8'))
        except Exception as e: messagebox.showerror("Error",f"Failed to load JSON:\n{e}"); return
        self.log_msg("--- Detailed Effect Report ---\n")
        mob, start, er, drop = find_main_sequence_mob_and_start_tc(data)
        if not mob: self.log_msg("Could not find main sequence."); return
        effects = find_timeline_effects(mob, timeline_offset=start)
        if not effects: self.log_msg("No effects found."); return
        out=[]
        for i,e in enumerate(effects,1):
            d=extract_effect_details(e['node']); sf,ln=e['start_frame'],d['length']
            self.log_msg("----------------------------------------")
            self.log_msg(f"EVENT #{i}: {d['effect_name']}")
            self.log_msg(f"  Timeline Start: {frames_to_tc(sf,er,drop)} ({sf}f)")
            self.log_msg(f"  Length: {ln} frames")
            out.append(f"--- EVENT #{i}: {d['effect_name']} ---")
            if not d['animated_params']:
                self.log_msg("  - No animated parameters."); out.append("No animated parameters.\n")
            else:
                for pname,kfs in d['animated_params'].items():
                    self.log_msg(f"  - Parameter: {pname} ({len(kfs)} keyframes)"); out.append(f"Parameter: {pname}")
                    for kp in kfs:
                        try:
                            t = float(kp['time'])
                            off = int(t * (ln - 1)) if ln > 1 else 0
                            af = sf + off
                            line = f"Keyframe at {frames_to_tc(af,er,drop)} ({af}f) -> Value: {kp['value']}"
                        except:
                            line = f"Keyframe at Time: {kp['time']} -> Value: {kp['value']}"
                        self.log_msg(line, indent=2)
                        out.append("    " + line)
            self.log_msg(""); out.append("")
        dump=f"effect_parameters_dump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(dump,'w',encoding='utf-8') as f: f.write("\n".join(out))
        self.log_msg(f"✅ Dump saved to: {dump}")
        messagebox.showinfo("Done","Analysis complete.")

if __name__ == '__main__':
    root=tk.Tk(); app=EffectAnalyzerApp(root); root.mainloop()
