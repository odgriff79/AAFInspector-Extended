import csv
import os
import re
import tkinter as tk
from tkinter import filedialog, scrolledtext

def parse_sourceclip_dump(text):
    entries = []
    blocks = text.strip().split("----")
    for block in blocks:
        lines = block.strip().splitlines()
        entry = {}
        for line in lines:
            if line.startswith("Slot:"):
                entry["Slot"] = line.split("Slot:")[1].strip()
            elif line.startswith("MobID:"):
                entry["MobID"] = line.split("MobID:")[1].strip()
            elif line.startswith("StartFrame:"):
                entry["StartFrame"] = int(line.split("StartFrame:")[1].strip())
            elif line.startswith("Length:"):
                entry["Length"] = int(line.split("Length:")[1].strip())
        if entry:
            entries.append(entry)
    return entries

def parse_sequence_report(seq_text):
    entries = []
    in_clip_list = False
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
        self.root.title("SourceClip Cross-Reference Tool")
        self.root.geometry("1000x700")

        tk.Button(root, text="Select Dump File", command=self.load_dump).pack(pady=5)
        tk.Button(root, text="Select Sequence Report", command=self.load_rpt).pack(pady=5)
        tk.Button(root, text="Select Metadata CSV", command=self.load_csv).pack(pady=5)
        tk.Button(root, text="Generate CSV Report", command=self.process).pack(pady=10)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=130, height=35)
        self.log.pack()

        self.paths = {"dump": None, "rpt": None, "csv": None}

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_dump(self):
        path = filedialog.askopenfilename(title="Select extracted_sourceclips_dump.txt", filetypes=[("Text Files", "*.txt")])
        if path:
            self.paths["dump"] = path
            self.log_msg(f"✅ Selected dump file:\n{path}")

    def load_rpt(self):
        path = filedialog.askopenfilename(title="Select Sequence Report", filetypes=[("Text Files", "*.txt")])
        if path:
            self.paths["rpt"] = path
            self.log_msg(f"✅ Selected Sequence Report:\n{path}")

    def load_csv(self):
        path = filedialog.askopenfilename(title="Select Metadata CSV", filetypes=[("CSV Files", "*.csv"), ("Text Files", "*.txt")])
        if path:
            self.paths["csv"] = path
            self.log_msg(f"✅ Selected Metadata CSV:\n{path}")

    def process(self):
        if not all(self.paths.values()):
            self.log_msg("❌ Please select all input files first.")
            return

        # Load dump entries
        with open(self.paths["dump"], "r", encoding="utf-8") as f:
            dump_text = f.read()
        entries = parse_sourceclip_dump(dump_text)
        self.log_msg(f"✅ Loaded {len(entries)} entries from dump.")

        # Filter out placeholders
        filtered = [
            e for e in entries
            if e["MobID"]
            and not e["MobID"].endswith("00000000.00000000.00000000.00000000")
            and not e["MobID"].startswith("00000000")
        ]
        self.log_msg(f"✅ Filtered down to {len(filtered)} entries with valid MobIDs.")

        # Load Sequence Report
        seq_text = open(self.paths["rpt"], "r", encoding="utf-8").read()
        seq_entries = parse_sequence_report(seq_text)
        seq_map = {s["MobID"]: s["Clip"] for s in seq_entries}
        self.log_msg(f"✅ Loaded {len(seq_entries)} sequence report entries.")

        # Load CSV metadata
        csv_entries = parse_csv(self.paths["csv"])
        self.log_msg(f"✅ Loaded {len(csv_entries)} CSV metadata entries.")

        # Cross-reference and build rows
        output_rows = []
        for e in filtered:
            mobid = e["MobID"]
            clip_name = seq_map.get(mobid, "")
            # Try to match metadata CSV by MobID if present (optional)
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

        # Write CSV
        out_path = os.path.join(os.path.dirname(self.paths["dump"]), "cross_referenced_report.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["Slot", "MobID", "ClipName", "StartFrame", "Length", "DiskLabel", "SourcePath"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in output_rows:
                writer.writerow(row)

        self.log_msg(f"✅ Generated cross-referenced CSV report:\n{out_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
