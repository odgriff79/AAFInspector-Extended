import os
import json
import csv
import urllib.parse
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def frames_to_tc(frame_count, fps):
    h = frame_count // (3600 * int(fps))
    m = (frame_count % (3600 * int(fps))) // (60 * int(fps))
    s = (frame_count % (60 * int(fps))) // int(fps)
    f = frame_count % int(fps)
    return f"{h:02}:{m:02}:{s:02}:{f:02}"

def create_mob_map(node, mob_map=None):
    if mob_map is None:
        mob_map = {}
    if not isinstance(node, list) or len(node) < 2:
        return mob_map
    children = node[3] if len(node) > 3 else []
    if any(isinstance(c, list) and c[0] == "MobID" for c in children):
        mobid = next((c[2] for c in children if isinstance(c, list) and c[0] == "MobID"), None)
        if mobid:
            mob_map[mobid] = node
    for c in children:
        create_mob_map(c, mob_map)
    return mob_map

def extract_metadata(mob_node):
    metadata = {"DiskLabel": "", "TapeID": "", "URLString": "", "TimecodeStart": None, "TimecodeFPS": None}
    def recurse(n):
        if not isinstance(n, list):
            return
        name = n[0] if len(n) >= 1 else ""
        children = n[3] if len(n) > 3 else []
        if name == "URLString" and len(n) > 2:
            metadata["URLString"] = n[2]
        if isinstance(n, list) and len(n) >= 2:
            cname = n[1]
            if cname == "ClassDefinition":
                attr_name = ""
                attr_value = ""
                for c in children:
                    if isinstance(c, list):
                        if c[0] == "Name" and len(c) > 2:
                            attr_name = c[2]
                        if c[0] == "Value" and len(c) > 2:
                            attr_value = c[2]
                if attr_name == "_IMPORTDISKLAB":
                    metadata["DiskLabel"] = attr_value
                if attr_name == "TapeID":
                    metadata["TapeID"] = attr_value
        if name == "Timecode":
            tc_start = None
            fps = None
            for c in n:
                if isinstance(c, list):
                    if c[0] == "Start":
                        tc_start = int(c[2])
                    if c[0] == "FPS":
                        fps = float(c[2])
            if tc_start is not None and fps is not None:
                metadata["TimecodeStart"] = tc_start
                metadata["TimecodeFPS"] = fps
        for c in children:
            recurse(c)
    recurse(mob_node)
    return metadata

def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, depth=0, counters=None, log_file=None, dedupe_set=None):
    if results is None:
        results = []
    if counters is None:
        counters = {"visited":0}
    if dedupe_set is None:
        dedupe_set = set()
    if not isinstance(node, list) or len(node) < 2:
        return results
    name = node[0]
    children = node[3] if len(node) > 3 else []
    counters["visited"] += 1
    indent = "  " * depth
    if log_file:
        log_file.write(f"{indent}Node: {name}\n")
    for c in children:
        if isinstance(c, list):
            if c[0] == "EditRate":
                try:
                    edit_rate = float(c[2])
                except:
                    pass
            elif c[0] == "FPS":
                try:
                    edit_rate = float(c[2])
                except:
                    pass
    if name == "Sequence":
        for c in children:
            if isinstance(c, list) and c[0] == "Components":
                cursor = timeline_offset
                for comp in c[3]:
                    recursive_search(comp, cursor, edit_rate, results, depth+1, counters, log_file, dedupe_set)
                    # Increment cursor for sequential placement
                    if comp[0] == "SourceClip":
                        l = next((int(x[2]) for x in comp[3] if x[0] == "Length"), 0)
                        cursor += l
    if name == "SourceClip":
        mobid = ""
        source_start = 0
        length = 0
        for c in children:
            if isinstance(c, list):
                if c[0] == "SourceID":
                    mobid = c[2]
                elif c[0] in ("Start", "StartTime"):
                    source_start = int(c[2])
                elif c[0] == "Length":
                    length = int(c[2])
        dedupe_key = (mobid, timeline_offset, source_start)
        if dedupe_key not in dedupe_set:
            dedupe_set.add(dedupe_key)
            results.append({
                "MobID": mobid,
                "TimelineStartFrame": timeline_offset,
                "SourceStartFrame": source_start,
                "Length": length,
                "EditRate": edit_rate
            })
            if log_file:
                log_file.write(f"{indent}🎯 Unique SourceClip: MobID={mobid}, Timeline={timeline_offset}, Length={length}, EditRate={edit_rate}\n")
        return results
    for c in children:
        recursive_search(c, timeline_offset, edit_rate, results, depth+1, counters, log_file, dedupe_set)
    return results

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Super EDL Generator v3")
        self.root.geometry("1200x750")
        tk.Button(root, text="Select JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="Generate Super EDL", command=self.process).pack(pady=5)
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=140, height=40)
        self.log.pack()
        self.json_data = None
        self.json_path = None

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select Compressed JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            with open(path, "r", encoding="utf-8") as f:
                self.json_data = json.load(f)
            self.json_path = path
            self.log_msg(f"✅ Loaded JSON:\n{path}")

    def process(self):
        if not self.json_data:
            self.log_msg("❌ Please load JSON first.")
            return
        self.log.delete(1.0, tk.END)
        self.log_msg("✅ Building MobID map...")
        mob_map = create_mob_map(self.json_data)
        self.log_msg(f"✅ Mobs indexed: {len(mob_map)}")
        debug_path = os.path.join(os.path.dirname(self.json_path), "superedl_debug_log.txt")
        events = []
        counters = {"visited":0}
        with open(debug_path, "w", encoding="utf-8") as logf:
            recursive_search(self.json_data, counters=counters, log_file=logf)
        self.log_msg(f"✅ Traversed {counters['visited']} nodes (log saved to {debug_path})")
        self.log_msg("✅ Extracting timeline events...")
        events = []
        recursive_search(self.json_data, 0, 25, events, dedupe_set=set())
        self.log_msg(f"✅ Events found (unique): {len(events)}")
        enriched = []
        for idx, e in enumerate(events, 1):
            fps = e["EditRate"]
            mobid = e["MobID"]
            mob = mob_map.get(mobid)
            md = extract_metadata(mob) if mob else {}
            abs_frame = md.get("TimecodeStart", 0) + e["SourceStartFrame"] if md.get("TimecodeStart") is not None else None
            abs_tc = frames_to_tc(abs_frame, md.get("TimecodeFPS")) if abs_frame else "UNKNOWN"
            source_file = ""
            if md.get("URLString"):
                parsed = urllib.parse.urlparse(md["URLString"])
                source_file = os.path.basename(parsed.path)
            enriched.append({
                "Event": idx,
                "MobID": mobid,
                "DiskLabel": md.get("DiskLabel", ""),
                "TapeID": md.get("TapeID", ""),
                "URLString": md.get("URLString", ""),
                "SourceFile": source_file,
                "EditRate": fps,
                "TimelineInTC": frames_to_tc(e["TimelineStartFrame"], fps),
                "TimelineOutTC": frames_to_tc(e["TimelineStartFrame"] + e["Length"], fps),
                "SourceStartFrame": e["SourceStartFrame"],
                "SourceOffsetTC": frames_to_tc(e["SourceStartFrame"], fps),
                "SourceAbsoluteTC": abs_tc,
                "Length": e["Length"]
            })
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.dirname(self.json_path)
        out_csv = os.path.join(out_dir, f"super_edl_{ts}.csv")
        out_txt = os.path.join(out_dir, f"super_edl_{ts}.txt")
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(enriched[0].keys()))
            writer.writeheader()
            for r in enriched:
                writer.writerow(r)
        with open(out_txt, "w", encoding="utf-8") as f:
            for r in enriched:
                f.write(f"{r['Event']:03} {r['DiskLabel']} {r['TimelineInTC']}-{r['TimelineOutTC']} Source:{r['SourceOffsetTC']} AbsTC:{r['SourceAbsoluteTC']} File:{r['SourceFile']} MobID:{r['MobID']}\n")
        self.log_msg(f"✅ CSV saved: {out_csv}")
        self.log_msg(f"✅ Super EDL TXT saved: {out_txt}")
        messagebox.showinfo("Done", "Super EDL generated successfully.")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
