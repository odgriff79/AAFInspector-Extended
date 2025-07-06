import os
import json
import csv
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def recursive_search_sourceclips(node, slot_name=None, timeline_offset=0, edit_rate=25, results=None, depth=0, counters=None, log_file=None):
    if results is None:
        results = []
    if counters is None:
        counters = {"visited":0}

    if not isinstance(node, list) or len(node) < 2:
        return results

    name = node[0]
    class_name = node[1]
    children = node[3] if len(node) > 3 else []

    counters["visited"] += 1
    indent = "  " * depth
    if log_file:
        log_file.write(f"{indent}Node: {name} | Class: {class_name}\n")

    # If this node declares EditRate or FPS, update local edit_rate
    for c in children:
        if isinstance(c, list):
            if c[0] == "EditRate":
                edit_rate = c[2]
            elif c[0] == "FPS":
                edit_rate = c[2]

    if name == "Sequence":
        for c in children:
            if isinstance(c, list) and c[0] == "Components":
                for comp in c[3]:
                    recursive_search_sourceclips(comp, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)

    if name == "SourceClip":
        mobid = ""
        source_start = 0
        length = 0
        for c in children:
            if isinstance(c, list):
                if c[0] == "SourceID":
                    mobid = c[2]
                elif c[0] in ("Start","StartTime"):
                    source_start = c[2]
                elif c[0] == "Length":
                    length = c[2]
        results.append({
            "Slot": slot_name,
            "MobID": mobid,
            "TimelineStartFrame": timeline_offset,
            "SourceStartFrame": source_start,
            "Length": length,
            "EditRate": edit_rate
        })
        if log_file:
            log_file.write(f"{indent}🎯 SourceClip: MobID={mobid}, Start={source_start}, TimelineStart={timeline_offset}, Length={length}, EditRate={edit_rate}\n")

        timeline_offset += length
        return results

    if name == "Filler":
        for c in children:
            if isinstance(c, list) and c[0]=="Length":
                length = c[2]
                timeline_offset += length
        return results

    if "Segment" in name or "OperationGroup" in name:
        for c in children:
            recursive_search_sourceclips(c, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)
        return results

    for c in children:
        recursive_search_sourceclips(c, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)
    return results

def find_source_mob_by_mobid(node, target_mobid):
    if not isinstance(node, list) or len(node) < 2:
        return None
    name = node[0]
    children = node[3] if len(node) > 3 else []

    if name == "MobID" and node[2] == target_mobid:
        return node
    for c in children:
        result = find_source_mob_by_mobid(c, target_mobid)
        if result:
            return result
    return None

def extract_disklabel_tapeid_url(node):
    disklabel = ""
    tapeid = ""
    url = ""
    if not isinstance(node, list) or len(node) < 2:
        return disklabel, tapeid, url
    children = node[3] if len(node) > 3 else []
    for c in children:
        if isinstance(c, list):
            if c[0]=="Name" and "_IMPORTDISKLAB" in c[2]:
                if len(children)>=2 and isinstance(children[1], list) and children[1][0]=="Value":
                    disklabel = children[1][2]
            if c[0]=="Name" and "TapeID" in c[2]:
                if len(children)>=2 and isinstance(children[1], list) and children[1][0]=="Value":
                    tapeid = children[1][2]
            if c[0]=="URLString":
                url = c[2]
            child_disklabel, child_tapeid, child_url = extract_disklabel_tapeid_url(c)
            disklabel = disklabel or child_disklabel
            tapeid = tapeid or child_tapeid
            url = url or child_url
    return disklabel, tapeid, url

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Sequence Extractor (JSON Only, EditRate)")
        self.root.geometry("1100x700")

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="Extract Sequence Data", command=self.process).pack(pady=10)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=140, height=35)
        self.log.pack()

        self.paths = {"json": None}
        self.json_data = None
        self.json_dir = None

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select Compressed JSON", filetypes=[("JSON Files","*.json")])
        if path:
            with open(path, "r", encoding="utf-8") as jf:
                self.json_data = json.load(jf)
            self.paths["json"] = path
            self.json_dir = os.path.dirname(path)
            self.log_msg(f"✅ Loaded JSON:\n{path}")

    def process(self):
        if not self.json_data:
            self.log_msg("❌ Please load the JSON file first.")
            return

        self.log.delete(1.0, tk.END)
        self.log_msg("🔍 Extracting timeline events...")
        counters = {"visited":0}
        debug_path = os.path.join(self.json_dir, "sequence_debug_log.txt")
        with open(debug_path, "w", encoding="utf-8") as logf:
            events = recursive_search_sourceclips(
                self.json_data,
                counters=counters,
                log_file=logf
            )
        self.log_msg(f"✅ Traversed {counters['visited']} nodes (log saved to {debug_path})")
        self.log_msg(f"✅ Found {len(events)} timeline events.")

        enriched = []
        for e in events:
            disklabel, tapeid, url = "", "", ""
            if e["MobID"]:
                mobnode = find_source_mob_by_mobid(self.json_data, e["MobID"])
                if mobnode:
                    parent = mobnode
                    while parent:
                        disklabel, tapeid, url = extract_disklabel_tapeid_url(parent)
                        if disklabel or tapeid or url:
                            break
                        parent = parent[:-1]
            enriched.append({
                "Slot": e["Slot"],
                "MobID": e["MobID"],
                "TimelineStartFrame": e["TimelineStartFrame"],
                "SourceStartFrame": e["SourceStartFrame"],
                "Length": e["Length"],
                "EditRate": e["EditRate"],
                "DiskLabel": disklabel,
                "TapeID": tapeid,
                "URLString": url
            })

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(self.json_dir, f"sequence_events_jsononly_{timestamp}.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = [
                "Slot","MobID","TimelineStartFrame","SourceStartFrame","Length","EditRate","DiskLabel","TapeID","URLString"
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in enriched:
                writer.writerow(row)

        self.log_msg(f"✅ CSV generated:\n{out_path}")
        messagebox.showinfo("Success", f"CSV saved:\n{out_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
