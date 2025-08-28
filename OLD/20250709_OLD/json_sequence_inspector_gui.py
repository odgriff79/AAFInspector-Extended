import json
import tkinter as tk
from tkinter import filedialog, scrolledtext

def safe_read_json(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def extract_composition_mobs(json_data):
    """Find all CompositionMobs (timelines) in the AAF JSON."""
    mobs = []
    if isinstance(json_data, dict):
        for mob in json_data.get("Mobs", []):
            if mob.get("ClassName") == "CompositionMob":
                mobs.append(mob)
    return mobs

def parse_timeline_slots(mob):
    """Extract slots and timeline events from a CompositionMob."""
    timeline = []
    slots = mob.get("Slots", [])
    for slot in slots:
        seg = slot.get("Segment", {})
        # TimelineMobSlot may have a Sequence segment
        if seg.get("ClassName") == "Sequence":
            components = seg.get("Components", [])
            for comp in components:
                if comp.get("ClassName") == "SourceClip":
                    ref = comp.get("SourceID")
                    start = comp.get("StartTime")
                    length = comp.get("Length")
                    timeline.append({
                        "SourceID": ref,
                        "StartTime": start,
                        "Length": length
                    })
        elif seg.get("ClassName") == "SourceClip":
            # Direct SourceClip
            ref = seg.get("SourceID")
            start = seg.get("StartTime")
            length = seg.get("Length")
            timeline.append({
                "SourceID": ref,
                "StartTime": start,
                "Length": length
            })
    return timeline

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF JSON Sequence Inspector")
        self.root.geometry("900x700")

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="List CompositionMobs", command=self.list_mobs).pack(pady=5)
        tk.Button(root, text="Parse Selected Timeline", command=self.parse_selected).pack(pady=5)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=120, height=35)
        self.log.pack()

        self.paths = {"json": None}
        self.mobs = []
        self.selected_mob = None

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select Compressed JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            self.paths["json"] = path
            self.log_msg(f"✅ Loaded JSON: {path}")
            self.json_data = safe_read_json(path)
            self.log_msg("✅ JSON loaded successfully.")

    def list_mobs(self):
        if not self.paths["json"]:
            self.log_msg("❌ Please load JSON first.")
            return

        self.mobs = extract_composition_mobs(self.json_data)
        self.log_msg(f"✅ Found {len(self.mobs)} CompositionMobs (timelines):")

        for idx, mob in enumerate(self.mobs):
            name = mob.get("Name", "(unnamed)")
            self.log_msg(f"  [{idx}] {name}")

        self.log_msg("\n👉 To select a timeline, enter the index in the field below and click 'Parse Selected Timeline'.")
        if not hasattr(self, 'entry'):
            frame = tk.Frame(self.root)
            frame.pack(pady=5)
            tk.Label(frame, text="Timeline Index:").pack(side=tk.LEFT)
            self.entry = tk.Entry(frame, width=5)
            self.entry.pack(side=tk.LEFT)

    def parse_selected(self):
        if not self.mobs:
            self.log_msg("❌ No CompositionMobs loaded.")
            return

        index_str = self.entry.get()
        if not index_str.isdigit():
            self.log_msg("❌ Please enter a valid index number.")
            return

        idx = int(index_str)
        if idx < 0 or idx >= len(self.mobs):
            self.log_msg("❌ Index out of range.")
            return

        mob = self.mobs[idx]
        self.selected_mob = mob
        name = mob.get("Name", "(unnamed)")
        self.log_msg(f"\n✅ Parsing timeline: {name}\n")

        timeline = parse_timeline_slots(mob)
        self.log_msg(f"✅ Found {len(timeline)} edit events:")

        for t in timeline:
            self.log_msg(
                f"- SourceID: {t['SourceID']}, StartFrame: {t['StartTime']}, Length: {t['Length']}"
            )

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
