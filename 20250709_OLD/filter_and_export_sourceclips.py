import csv
import os
import tkinter as tk
from tkinter import filedialog, scrolledtext

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("SourceClip Filter and CSV Export")
        self.root.geometry("800x500")

        tk.Button(root, text="Select Dump File", command=self.load_dump).pack(pady=10)
        tk.Button(root, text="Generate Clean CSV", command=self.process).pack(pady=5)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=100, height=25)
        self.log.pack()

        self.dump_path = None

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_dump(self):
        path = filedialog.askopenfilename(
            title="Select extracted_sourceclips_dump.txt",
            filetypes=[("Text Files", "*.txt")]
        )
        if path:
            self.dump_path = path
            self.log_msg(f"✅ Selected dump file:\n{path}")

    def process(self):
        if not self.dump_path:
            self.log_msg("❌ Please select a dump file first.")
            return

        entries = []
        current = {}

        with open(self.dump_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("Slot:"):
                    current["Slot"] = line.split(":",1)[1].strip()
                elif line.startswith("MobID:"):
                    current["MobID"] = line.split(":",1)[1].strip()
                elif line.startswith("StartFrame:"):
                    current["StartFrame"] = line.split(":",1)[1].strip()
                elif line.startswith("Length:"):
                    current["Length"] = line.split(":",1)[1].strip()
                elif line.strip() == "----":
                    if current:
                        entries.append(current)
                    current = {}

        self.log_msg(f"✅ Loaded {len(entries)} entries from dump.")

        filtered = [e for e in entries if e["MobID"] and not e["MobID"].startswith("00000000")]
        self.log_msg(f"✅ Filtered down to {len(filtered)} entries with valid MobIDs.")

        out_path = os.path.join(os.path.dirname(self.dump_path), "clean_sourceclips.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["Slot", "MobID", "StartFrame", "Length"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for e in filtered:
                writer.writerow(e)

        self.log_msg(f"✅ Clean CSV report generated:\n{out_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
