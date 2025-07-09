import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def frames_to_tc(frames, fps=25.0):
    if frames is None or fps is None or fps <= 0:
        return "N/A"
    frames = int(frames)
    fps = float(fps)
    h = frames // int(fps * 3600)
    m = (frames % int(fps * 3600)) // int(fps * 60)
    s = (frames % int(fps * 60)) // int(fps)
    f = frames % int(fps)
    return f"{h:02}:{m:02}:{s:02}:{f:02}"

def create_mob_map(node, mob_map=None):
    if mob_map is None:
        mob_map = {}
    if isinstance(node, list) and len(node) >= 4:
        tag, children = node[0], node[3]
        if tag == "CompositionMob" or tag.endswith("Mob"):
            mobid_node = next((c for c in children if isinstance(c, list) and c[0] == "MobID"), None)
            if mobid_node:
                mobid = mobid_node[2]
                mob_map[mobid] = node
        for child in children:
            create_mob_map(child, mob_map)
    return mob_map

def extract_start_time(mob_node):
    start_frames = []
    def recurse(n):
        if not isinstance(n, list):
            return
        if n[0] in ("Start", "StartTime") and len(n) > 2:
            try:
                val = int(n[2])
                start_frames.append(val)
            except:
                pass
        for child in (n[3] if len(n) > 3 else []):
            recurse(child)
    recurse(mob_node)
    return max(start_frames) if start_frames else None

def extract_url(mob_node):
    def recurse(n):
        if not isinstance(n, list):
            return None
        if n[0] == "URLString" and len(n) > 2:
            return n[2]
        for child in (n[3] if len(n) > 3 else []):
            result = recurse(child)
            if result:
                return result
        return None
    return recurse(mob_node)

def get_main_sequence(json_data):
    if not json_data or json_data[0] != "list" or len(json_data) < 4:
        return None, 0
    for mob in json_data[3]:
        if not isinstance(mob, list) or len(mob) < 4:
            continue
        slotlist = next((x for x in mob[3] if x[0] == "Slots"), None)
        if not slotlist:
            continue
        for slot in slotlist[3]:
            segment = next((x for x in slot[3] if x[0] == "Segment"), None)
            if segment and segment[3] and segment[3][0][0] == "Sequence":
                # Try to extract TC start frame
                sequence_node = segment[3][0]
                components = next((x for x in sequence_node[3] if x[0] == "Components"), None)
                if components:
                    for comp in components[3]:
                        if comp[0] == "Timecode":
                            tc_start = next((x for x in comp[3] if x[0] == "Start"), None)
                            try:
                                return mob, int(tc_start[2])
                            except:
                                return mob, 0
                return mob, 0
    return None, 0

def extract_sourceclips(mob_node, timeline_offset=0, edit_rate=25.0, results=None):
    if results is None:
        results = []
    if not isinstance(mob_node, list) or len(mob_node) < 4:
        return results
    children = mob_node[3]
    rate_node = next((c for c in children if c[0] in ("EditRate", "FPS")), None)
    if rate_node:
        try:
            edit_rate = float(rate_node[2])
        except:
            pass
    if mob_node[0] == "Sequence":
        components = next((x for x in children if x[0] == "Components"), None)
        if components:
            for comp in components[3]:
                length = next((x for x in comp[3] if x[0] == "Length"), None)
                lval = int(length[2]) if length else 0
                extract_sourceclips(comp, timeline_offset, edit_rate, results)
                timeline_offset += lval
    elif mob_node[0] == "SourceClip":
        mobid = next((x[2] for x in children if x[0] == "SourceID"), None)
        start = next((x for x in children if x[0] in ("Start", "StartTime")), None)
        offset = int(start[2]) if start else 0
        length = next((x for x in children if x[0] == "Length"), None)
        dur = int(length[2]) if length else 0
        results.append({
            "MobID": mobid,
            "TimelineStart": timeline_offset,
            "SourceOffset": offset,
            "Length": dur,
            "EditRate": edit_rate
        })
    else:
        for c in children:
            extract_sourceclips(c, timeline_offset, edit_rate, results)
    return results

class SuperEDLApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Super EDL GUI")
        self.root.geometry("1300x800")
        tk.Button(root, text="Load AAF JSON", command=self.load_json).pack()
        tk.Button(root, text="Generate EDL", command=self.run).pack()
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.log.pack(expand=True, fill=tk.BOTH)
        self.json_data = None
        self.json_path = None

    def log_msg(self, txt): self.log.insert(tk.END, txt + "\n"); self.log.see(tk.END)

    def load_json(self):
        f = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not f: return
        self.json_data = json.load(open(f, "r", encoding="utf-8"))
        self.json_path = f
        self.log.delete(1.0, tk.END)
        self.log_msg(f"✅ Loaded: {f}")

    def run(self):
        if not self.json_data:
            messagebox.showerror("No JSON", "Load AAF JSON first.")
            return

        self.log_msg("1. Building Mob map...")
        mob_map = create_mob_map(self.json_data)
        self.log_msg(f"   - Mobs indexed: {len(mob_map)}")

        self.log_msg("2. Finding main sequence...")
        sequence_mob, seq_start_tc = get_main_sequence(self.json_data)
        if not sequence_mob:
            self.log_msg("❌ No sequence found.")
            return
        self.log_msg(f"   - Sequence found. Master start TC: {frames_to_tc(seq_start_tc)}")

        self.log_msg("3. Extracting timeline events...")
        events = extract_sourceclips(sequence_mob, timeline_offset=seq_start_tc)

        output_rows = []
        for i, e in enumerate(events, 1):
            mobid = e["MobID"]
            mob = mob_map.get(mobid)
            if not mob:
                self.log_msg(f"⚠️ Missing Mob for ID: {mobid}")
                continue

            fps = e["EditRate"]
            start_tc = extract_start_time(mob)
            if start_tc is None:
                self.log_msg(f"⚠️ Defaulting source start TC to 0 for MobID {mobid}")
                start_tc = 0

            url = extract_url(mob)
            name = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path)) if url else "Unknown"

            row = {
                "Event": i,
                "ClipName": name,
                "TimelineInTC": frames_to_tc(e["TimelineStart"], fps),
                "SourceClipStartTC": frames_to_tc(start_tc, fps),
                "OffsetFrames": e["SourceOffset"],
                "OffsetTC": frames_to_tc(e["SourceOffset"], fps),
                "SourceIn": frames_to_tc(start_tc + e["SourceOffset"], fps),
                "EditRate": fps
            }
            output_rows.append(row)

        out_csv = os.path.join(os.path.dirname(self.json_path), f"super_edl_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            writer.writeheader()
            writer.writerows(output_rows)

        self.log_msg(f"✅ CSV written to: {out_csv}")
        self.log_msg("--- Timeline Summary ---")
        self.log_msg(f"Timeline Start: {frames_to_tc(seq_start_tc)}")
        self.log_msg(f"Total Events: {len(output_rows)}\n")
        for row in output_rows:
            self.log_msg(f"🎞 Event #{row['Event']}")
            for k, v in row.items():
                if k != "Event": self.log_msg(f"- {k}: {v}")
            self.log_msg("")

if __name__ == "__main__":
    root = tk.Tk()
    app = SuperEDLApp(root)
    root.mainloop()
