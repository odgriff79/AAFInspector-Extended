import re
import csv
import json
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

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Sequence Integrator")
        self.root.geometry("800x600")

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="Select EDL File", command=self.load_edl).pack(pady=5)
        tk.Button(root, text="Select Sequence Report", command=self.load_rpt).pack(pady=5)
        tk.Button(root, text="Select Source Metadata CSV", command=self.load_csv).pack(pady=5)
        tk.Button(root, text="Process", command=self.process).pack(pady=10)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=100, height=25)
        self.log.pack()

        self.paths = {"json": None, "edl": None, "rpt": None, "csv": None}

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select Compressed JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            self.paths["json"] = path
            self.log_msg(f"✅ Selected JSON: {path}")

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

        # Load JSON
        with open(self.paths["json"], "r", encoding="utf-8") as jf:
            json_data = json.load(jf)

        # Load EDL
        edl_text = safe_read_text(self.paths["edl"])
        edl_events = parse_edl(edl_text)
        self.log_msg(f"✅ Parsed {len(edl_events)} EDL events.")

        # Load Sequence Report
        rpt_text = safe_read_text(self.paths["rpt"])
        seq_entries = parse_sequence_report(rpt_text)
        self.log_msg(f"✅ Parsed {len(seq_entries)} sequence report clips.")

        # Load CSV metadata
        csv_entries = parse_csv(self.paths["csv"])
        self.log_msg(f"✅ Parsed {len(csv_entries)} CSV metadata entries.")

        # Combine everything
        combined = []
        for edl in edl_events:
            edl_clip_name = edl["Clip"].upper()
            matched_seq = next((s for s in seq_entries if s["Clip"].upper().startswith(edl_clip_name)), None)
            matched_csv = next((c for c in csv_entries if edl_clip_name in c.get("Source File","").upper()), None)
            combined.append({
                "Clip": edl["Clip"],
                "RecIn": edl["RecIn"],
                "RecOut": edl["RecOut"],
                "MobID": matched_seq["MobID"] if matched_seq else "",
                "DiskLabel": matched_csv.get("Disk Label") if matched_csv else "",
                "FilePath": matched_csv.get("Source Path") if matched_csv else ""
            })

        self.log_msg("\n✅ Combined Timeline:\n")
        for c in combined:
            self.log_msg(f"- {c['Clip']}: {c['RecIn']}–{c['RecOut']}, DiskLabel: {c['DiskLabel']}, Path: {c['FilePath']}, MobID: {c['MobID']}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
