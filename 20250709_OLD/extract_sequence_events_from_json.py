import os
import re
import json
import csv
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def extract_sequence_events(node, slot_name=None, edit_rate=25, timeline_offset=0, results=None, depth=0, counters=None, log_file=None):
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

    if class_name == "ClassDefinition":
        for c in children:
            if isinstance(c, list) and c[0] == "SlotName":
                slot_name = c[2]

    if name == "Sequence":
        for c in children:
            if isinstance(c, list) and c[0] == "Components":
                for comp in c[3]:
                    extract_sequence_events(comp, slot_name, edit_rate, timeline_offset, results, depth+1, counters, log_file)

    if name == "SourceClip":
        mobid = ""
        source_start = 0
        length = 0
        for c in children:
            if isinstance(c, list):
                if c[0] == "SourceID":
                    mobid = c[2]
                elif c[0] in ("Start", "StartTime"):
                    source_start = c[2]
                elif c[0] == "Length":
                    length = c[2]
        results.append({
            "Slot": slot_name,
            "MobID": mobid,
            "TimelineStartFrame": timeline_offset,
            "SourceStartFrame": source_start,
            "Length": length
        })
        if log_file:
            log_file.write(f"{indent}🎯 Extracted SourceClip: MobID={mobid}, SourceStart={source_start}, TimelineStart={timeline_offset}, Length={length}\n")

        timeline_offset += length
        return results

    if name == "Filler":
        for c in children:
            if isinstance(c, list) and c[0] == "Length":
                length = c[2]
                timeline_offset += length
        return results

    if "Segment" in name or "OperationGroup" in name:
        for c in children:
            extract_sequence_events(c, slot_name, edit_rate, timeline_offset, results, depth+1, counters, log_file)
        return results

    for c in children:
        extract_sequence_events(c, slot_name, edit_rate, timeline_offset, results, depth+1, counters, log_file)
    return results

def parse_tab_metadata(file_path):
    entries = []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            entries.append(row)
    return entries

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Sequence Extractor with Metadata Matching")
        self.root.geometry("1100x700")

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="Select Avid Bin Metadata (Tab-Delimited)", command=self.load_metadata).pack(pady=5)
        tk.Button(root, text="Generate Enriched Sequence CSV", command=self.process).pack(pady=10)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=140, height=35)
        self.log.pack()

        self.paths = {"json": None, "meta": None}
        self.json_data = None
        self.json_dir = None
        self.metadata = []

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select Compressed JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            with open(path, "r", encoding="utf-8") as jf:
                self.json_data = json.load(jf)
            self.paths["json"] = path
            self.json_dir = os.path.dirname(path)
            self.log_msg(f"✅ Loaded JSON:\n{path}")

    def load_metadata(self):
        path = filedialog.askopenfilename(title="Select Tab-Delimited Metadata", filetypes=[("TSV Files", "*.txt"), ("TSV Files", "*.tsv")])
        if path:
            self.metadata = parse_tab_metadata(path)
            self.paths["meta"] = path
            self.log_msg(f"✅ Loaded metadata with {len(self.metadata)} entries:\n{path}")

    def process(self):
        if not self.json_data or not self.metadata:
            self.log_msg("❌ Please select both the JSON and Metadata files first.")
            return

        self.log.delete(1.0, tk.END)
        self.log_msg("🔍 Extracting sequence events...")
        counters = {"visited":0}
        debug_path = os.path.join(self.json_dir, "sequence_debug_log.txt")
        with open(debug_path, "w", encoding="utf-8") as logf:
            events = extract_sequence_events(
                self.json_data,
                edit_rate=25,
                counters=counters,
                log_file=logf
            )
        self.log_msg(f"✅ Traversed {counters['visited']} nodes (log saved to {debug_path})")
        self.log_msg(f"✅ Extracted {len(events)} timeline events.")

        enriched_rows = []
        for e in events:
            match = None
            status = "Unresolved"

            # First pass: Source File match
            for m in self.metadata:
                if e["MobID"] and m.get("Source File","") and e["MobID"].split('.')[-1].split('-')[0] in m.get("Source File",""):
                    match = m
                    status = "Matched by Source File"
                    break

            # Second pass: Disk Label match
            if not match:
                for m in self.metadata:
                    if m.get("Disk Label","") and m["Disk Label"] in e["Slot"]:
                        match = m
                        status = "Matched by Disk Label"
                        break

            # Third pass: Name match
            if not match:
                for m in self.metadata:
                    if m.get("Name","") and m["Name"] in e["Slot"]:
                        match = m
                        status = "Matched by Name"
                        break

            enriched_rows.append({
                "Slot": e["Slot"],
                "MobID": e["MobID"],
                "TimelineStartFrame": e["TimelineStartFrame"],
                "SourceStartFrame": e["SourceStartFrame"],
                "Length": e["Length"],
                "Matched Name": match["Name"] if match else "",
                "Source File": match["Source File"] if match else "",
                "Source Path": match["Source Path"] if match else "",
                "Disk Label": match["Disk Label"] if match else "",
                "TapeID": match.get("TapeID","") if match else "",
                "Match Status": status
            })

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(self.json_dir, f"sequence_events_report_{timestamp}.csv")

        with open(out_path, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = [
                "Slot","MobID","TimelineStartFrame","SourceStartFrame","Length",
                "Matched Name","Source File","Source Path","Disk Label","TapeID","Match Status"
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in enriched_rows:
                writer.writerow(row)

        self.log_msg(f"✅ Enriched CSV generated:\n{out_path}")
        messagebox.showinfo("Success", f"CSV saved:\n{out_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
