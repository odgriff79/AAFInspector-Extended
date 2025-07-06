import os
import re
import json
import csv
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def extract_sourceclips(node, slot_name=None, results=None, depth=0, counters=None, log_file=None):
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

    if name == "SourceClip":
        mobid = ""
        start = 0
        length = 0
        for c in children:
            if isinstance(c, list):
                if c[0] == "SourceID":
                    mobid = c[2]
                elif c[0] in ("Start", "StartTime"):
                    start = c[2]
                elif c[0] == "Length":
                    length = c[2]
        results.append({
            "Slot": slot_name,
            "MobID": mobid,
            "StartFrame": start,
            "Length": length
        })
        if log_file:
            log_file.write(f"{indent}🎯 Extracted SourceClip: MobID={mobid}, Start={start}, Length={length}\n")

    for c in children:
        extract_sourceclips(c, slot_name, results, depth+1, counters, log_file)
    return results

def parse_sequence_report(seq_text):
    entries = []
    in_clip_list = False
    for line in seq_text.splitlines():
        raw = line.rstrip()
        if "Source Clip List:" in raw:
            in_clip_list = True
            continue
        if "########## Tape Source Info:" in raw and in_clip_list:
            in_clip_list = False
        if not in_clip_list:
            continue
        if not raw.strip() or "clips found" in raw or "____" in raw:
            continue
        parts = re.split(r"\s{2,}", raw.strip())
        if len(parts) < 4:
            continue
        mob_id = parts[-1]
        clip_name = parts[2]
        entries.append({"Clip": clip_name, "MobID": mob_id})
    return entries

def parse_csv(csv_path):
    entries = []
    with open(csv_path, "r", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile, delimiter='\t')
        for row in reader:
            entries.append(row)
    return entries

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Unified AAF Cross-Reference Tool (Fixed)")
        self.root.geometry("1000x700")

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="Select Sequence Report", command=self.load_rpt).pack(pady=5)
        tk.Button(root, text="Select Metadata CSV", command=self.load_csv).pack(pady=5)
        tk.Button(root, text="Generate Cross-Referenced CSV", command=self.process).pack(pady=10)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=130, height=35)
        self.log.pack()

        self.paths = {"json": None, "rpt": None, "csv": None}
        self.json_data = None
        self.json_dir = None

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

    def load_rpt(self):
        path = filedialog.askopenfilename(title="Select Sequence Report", filetypes=[("Text Files", "*.txt")])
        if path:
            self.paths["rpt"] = path
            self.log_msg(f"✅ Loaded Sequence Report:\n{path}")

    def load_csv(self):
        path = filedialog.askopenfilename(title="Select Metadata CSV", filetypes=[("CSV Files", "*.csv"), ("Text Files", "*.txt")])
        if path:
            self.paths["csv"] = path
            self.log_msg(f"✅ Loaded Metadata CSV:\n{path}")

    def read_text_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="utf-16") as f:
                return f.read()

    def process(self):
        if not all(self.paths.values()):
            self.log_msg("❌ Please select all input files first.")
            return

        self.log.delete(1.0, tk.END)
        self.log_msg("🔍 Extracting SourceClip entries...")
        counters = {"visited":0}
        debug_path = os.path.join(self.json_dir, "recursive_debug_log.txt")
        with open(debug_path, "w", encoding="utf-8") as logf:
            sourceclips = extract_sourceclips(
                self.json_data,
                counters=counters,
                log_file=logf
            )
        self.log_msg(f"✅ Traversed {counters['visited']} nodes (log saved to {debug_path})")
        self.log_msg(f"✅ Found {len(sourceclips)} SourceClip entries.")

        filtered = [
            e for e in sourceclips
            if e["MobID"]
            and not e["MobID"].endswith("00000000.00000000.00000000.00000000")
            and not e["MobID"].startswith("00000000")
        ]
        self.log_msg(f"✅ Filtered down to {len(filtered)} entries with valid MobIDs.")

        seq_text = self.read_text_file(self.paths["rpt"])
        seq_entries = parse_sequence_report(seq_text)
        seq_map = {s["MobID"]: s["Clip"] for s in seq_entries}
        self.log_msg(f"✅ Loaded {len(seq_entries)} sequence report entries.")

        csv_entries = parse_csv(self.paths["csv"])
        self.log_msg(f"✅ Loaded {len(csv_entries)} metadata entries.")

        output_rows = []
        for e in filtered:
            mobid = e["MobID"]
            clip_name = seq_map.get(mobid, "")
            csv_match = next(
                (c for c in csv_entries if mobid in c.get("MobID","")),
                None
            )
            disk_label = csv_match["Disk Label"] if csv_match else ""
            source_path = csv_match["Source Path"] if csv_match else ""
            output_rows.append({
                "Slot": e["Slot"],
                "MobID": mobid,
                "ClipName": clip_name,
                "StartFrame": e["StartFrame"],
                "Length": e["Length"],
                "DiskLabel": disk_label,
                "SourcePath": source_path
            })

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(self.json_dir, f"cross_referenced_report_{timestamp}.csv")

        with open(out_path, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["Slot", "MobID", "ClipName", "StartFrame", "Length", "DiskLabel", "SourcePath"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in output_rows:
                writer.writerow(row)

        self.log_msg(f"✅ Cross-referenced CSV generated:\n{out_path}")
        messagebox.showinfo("Success", f"CSV saved:\n{out_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
