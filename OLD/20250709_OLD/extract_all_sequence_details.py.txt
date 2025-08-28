import os
import json
import csv
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def recursive_search(node, slot_name=None, timeline_offset=0, edit_rate=25, results=None, depth=0, counters=None, log_file=None):
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

    # Extract EditRate if available
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
                for comp in c[3]:
                    recursive_search(comp, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)

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

    if "Segment" in name or "OperationGroup" in name:
        for c in children:
            recursive_search(c, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)
        return results

    for c in children:
        recursive_search(c, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)
    return results

def find_mob_metadata(node, target_mobid):
    """
    Recursively search the entire JSON tree to find:
      - DiskLabel
      - TapeID
      - URLString
    for the given MobID.
    """
    metadata = {"DiskLabel": "", "TapeID": "", "URLString": ""}
    if not isinstance(node, list) or len(node) < 2:
        return metadata
    children = node[3] if len(node) > 3 else []

    # Look for MobID match
    found_mobid = False
    if node[0] == "MobID" and node[2] == target_mobid:
        found_mobid = True

    if found_mobid:
        # Once found, search children recursively for metadata
        def recurse_metadata(subnode):
            if not isinstance(subnode, list) or len(subnode) < 2:
                return
            subchildren = subnode[3] if len(subnode) > 3 else []
            # DiskLabel
            if subnode[0] == "Name" and "_IMPORTDISKLAB" in subnode[2]:
                for sc in subchildren:
                    if sc[0] == "Value":
                        metadata["DiskLabel"] = sc[2]
            # TapeID
            if subnode[0] == "Name" and "TapeID" in subnode[2]:
                for sc in subchildren:
                    if sc[0] == "Value":
                        metadata["TapeID"] = sc[2]
            # URLString
            if subnode[0] == "URLString":
                metadata["URLString"] = subnode[2]
            for sc in subchildren:
                recurse_metadata(sc)
        recurse_metadata(node)
    else:
        for c in children:
            child_metadata = find_mob_metadata(c, target_mobid)
            # If any field is populated, return immediately
            if any(child_metadata.values()):
                return child_metadata
    return metadata

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Sequence Extractor with Full Metadata")
        self.root.geometry("1100x700")

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="Extract All Sequence Details", command=self.process).pack(pady=10)

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
            events = recursive_search(
                self.json_data,
                counters=counters,
                log_file=logf
            )
        self.log_msg(f"✅ Traversed {counters['visited']} nodes (log saved to {debug_path})")
        self.log_msg(f"✅ Found {len(events)} SourceClip events.")

        enriched = []
        for e in events:
            metadata = find_mob_metadata(self.json_data, e["MobID"])
            enriched.append({
                "Slot": e["Slot"],
                "MobID": e["MobID"],
                "TimelineStartFrame": e["TimelineStartFrame"],
                "SourceStartFrame": e["SourceStartFrame"],
                "Length": e["Length"],
                "EditRate": e["EditRate"],
                "DiskLabel": metadata["DiskLabel"],
                "TapeID": metadata["TapeID"],
                "URLString": metadata["URLString"]
            })

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(self.json_dir, f"sequence_details_{timestamp}.csv")
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
