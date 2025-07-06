import re
import csv
import json
import os
import tkinter as tk
from tkinter import filedialog, scrolledtext

def safe_read_text(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(filepath, "r", encoding="utf-16") as f:
            return f.read()

def parse_sequence_report(seq_text):
    in_clip_list = False
    entries = []
    lines = seq_text.splitlines()
    for line in lines:
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

def parse_edl(edl_text):
    events = []
    lines = edl_text.splitlines()
    for line in lines:
        line = line.strip()
        if re.match(r"^\d{6}\s+", line):
            parts = line.split()
            if len(parts) >= 8:
                clip_name = parts[1]
                rec_in = parts[6]
                rec_out = parts[7]
                events.append({
                    "Clip": clip_name,
                    "RecIn": rec_in,
                    "RecOut": rec_out
                })
    return events

def parse_csv(csv_path):
    entries = []
    with open(csv_path, "r", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile, delimiter='\t')
        for row in reader:
            entries.append(row)
    return entries

def extract_sourceclips_compressed_recursive(node, slot_name=None, results=None, depth=0, log_file=None, counters=None):
    if results is None:
        results = []
    if counters is None:
        counters = {"visited":0}

    if not isinstance(node, list) or len(node) < 2:
        return results

    name = node[0]
    class_name = node[1]
    value = node[2] if len(node) > 2 else None
    children = node[3] if len(node) > 3 else []

    counters["visited"] +=1

    indent = "  " * depth
    if log_file:
        log_file.write(f"{indent}Node: {name} | Class: {class_name}\n")

    # If this is a TimelineMobSlot, track slot name
    if class_name == "ClassDefinition":
        for c in children:
            if isinstance(c, list) and c[0] == "SlotName":
                slot_name = c[2]

    # If this is a SourceClip
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
        extract_sourceclips_compressed_recursive(
            c, slot_name=slot_name, results=results, depth=depth+1,
            log_file=log_file, counters=counters
        )

    return results

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF SourceClip Recursive Extractor (Optimized)")
        self.root.geometry("1000x700")

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="Select EDL File", command=self.load_edl).pack(pady=5)
        tk.Button(root, text="Select Sequence Report", command=self.load_rpt).pack(pady=5)
        tk.Button(root, text="Select Source Metadata CSV", command=self.load_csv).pack(pady=5)
        tk.Button(root, text="Process", command=self.process).pack(pady=10)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=130, height=35)
        self.log.pack()

        self.paths = {"json": None, "edl": None, "rpt": None, "csv": None}
        self.json_data = None
        self.json_file_dir = None

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select Compressed JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            with open(path, "r", encoding="utf-8") as jf:
                self.json_data = json.load(jf)
            self.paths["json"] = path
            self.json_file_dir = os.path.dirname(path)
            self.log_msg(f"✅ Loaded JSON: {path}")

            if isinstance(self.json_data, list):
                self.log_msg(f"🔍 JSON Root Type: list")
                self.log_msg(f"First Entry: {str(self.json_data[0])[:300]}...")
            else:
                self.log_msg(f"⚠️ Unexpected JSON format: {type(self.json_data)}")

    def load_edl(self):
        path = filedialog.askopenfilename(title="Select EDL File", filetypes=[("EDL Files", "*.edl"), ("Text Files", "*.txt")])
        if path:
            self.paths["edl"] = path
            self.log_msg(f"✅ Selected EDL: {path}")

    def load_rpt(self):
        path = filedialog.askopenfilename(title="Select Sequence Report", filetypes=[("Text Files", "*.txt")])
        if path:
            self.paths["rpt"] = path
            self.log_msg(f"✅ Selected Sequence Report: {path}")

    def load_csv(self):
        path = filedialog.askopenfilename(title="Select Source Metadata CSV", filetypes=[("CSV Files", "*.csv"), ("Text Files", "*.txt")])
        if path:
            self.paths["csv"] = path
            self.log_msg(f"✅ Selected CSV: {path}")

    def process(self):
        if not all(self.paths.values()):
            self.log_msg("❌ Please select all input files first.")
            return

        self.log.delete(1.0, tk.END)

        edl_text = safe_read_text(self.paths["edl"])
        edl_events = parse_edl(edl_text)
        self.log_msg(f"✅ Parsed {len(edl_events)} EDL events.")

        rpt_text = safe_read_text(self.paths["rpt"])
        seq_entries = parse_sequence_report(rpt_text)
        self.log_msg(f"✅ Parsed {len(seq_entries)} sequence report clips.")

        csv_entries = parse_csv(self.paths["csv"])
        self.log_msg(f"✅ Parsed {len(csv_entries)} CSV metadata entries.")

        self.log_msg("🔍 Recursing JSON to find SourceClip entries...")

        debug_path = os.path.join(self.json_file_dir, "recursive_debug_log.txt")
        counters = {"visited":0}
        with open(debug_path, "w", encoding="utf-8") as logf:
            json_clips = extract_sourceclips_compressed_recursive(
                self.json_data,
                log_file=logf,
                counters=counters
            )

        self.log_msg(f"✅ Traversed {counters['visited']} nodes (full log in {debug_path})")
        self.log_msg(f"✅ Found {len(json_clips)} SourceClip entries.")

        dump_path = os.path.join(self.json_file_dir, "extracted_sourceclips_dump.txt")
        with open(dump_path, "w", encoding="utf-8") as f:
            for j in json_clips:
                f.write(f"Slot: {j['Slot']}\n")
                f.write(f"MobID: {j['MobID']}\n")
                f.write(f"StartFrame: {j['StartFrame']}\n")
                f.write(f"Length: {j['Length']}\n")
                f.write("----\n")

        self.log_msg(f"✅ Dumped all SourceClip entries to:\n{dump_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
