import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext
from datetime import datetime

def frames_to_tc(frame, rate=25):
    if frame is None: return "UNKNOWN"
    fps = float(rate)
    h = int(frame // (3600 * fps))
    m = int((frame % (3600 * fps)) // (60 * fps))
    s = int((frame % (60 * fps)) // fps)
    f = int(frame % fps)
    return f"{h:02}:{m:02}:{s:02}:{f:02}"

def get_property(item, key, default=None):
    if not isinstance(item, list): return default
    for c in item[3] if len(item) > 3 else []:
        if isinstance(c, list) and c[0] == key:
            return c[3] if isinstance(c[3], list) else c[2]
    return default

def find_mobs(data):
    if not isinstance(data, list): return []
    result = []
    if any(isinstance(c, list) and len(c) > 0 and c[0] == "MobID" for c in data[3] if isinstance(c, list)):
        result.append(data)
    for item in data[3] if len(data) > 3 else []:
        if isinstance(item, list) and len(item) > 3:
            result += find_mobs(item)
    return result

def find_sequence_mob_and_tc(mobs):
    for mob in mobs:
        slots = get_property(mob, "Slots", [])
        for slot in slots:
            seg = get_property(slot, "Segment")
            if seg and isinstance(seg, list):
                for comp in seg[3] if len(seg) > 3 else []:
                    if isinstance(comp, list) and comp[0] == "Sequence":
                        return mob, find_start_tc(mob)
    return None, 0

def find_start_tc(mob):
    slots = get_property(mob, "Slots", [])
    for slot in slots:
        seg = get_property(slot, "Segment")
        if seg and isinstance(seg, list):
            for comp in seg[3] if len(seg) > 3 else []:
                if isinstance(comp, list) and comp[0] == "Timecode":
                    for field in comp[3]:
                        if isinstance(field, list) and field[0] == "Start":
                            return int(field[2])
    return 0

def get_mob_by_id(mobs, mobid):
    for mob in mobs:
        found_id = get_property(mob, "MobID")
        if found_id == mobid:
            return mob
    return None

def extract_timecode_from_source_mob(mob):
    slots = get_property(mob, "Slots", [])
    for slot in slots:
        seg = get_property(slot, "Segment")
        if seg and isinstance(seg, list):
            for comp in seg[3] if len(seg) > 3 else []:
                if isinstance(comp, list) and comp[0] == "Timecode":
                    for field in comp[3]:
                        if isinstance(field, list) and field[0] == "Start":
                            return int(field[2])
    return None

def recursive_search(node, timeline_offset=0, edit_rate=25, results=None, dedupe=None):
    if results is None: results = []
    if dedupe is None: dedupe = set()
    if not isinstance(node, list) or len(node) < 4: return results
    if node[0] == "SourceClip":
        fields = {c[0]: c[2] for c in node[3] if isinstance(c, list) and len(c) > 2}
        source_id = fields.get("SourceID")
        mobslot_id = fields.get("SourceMobSlotID")
        offset = int(fields.get("StartTime", 0))
        length = next((int(c[2]) for c in node[3] if isinstance(c, list) and c[0] == "Length"), 0)
        key = (source_id, mobslot_id, offset)
        if key not in dedupe:
            results.append({
                "SourceMobID": source_id,
                "TrackID": mobslot_id,
                "SourceOffsetFrames": offset,
                "EventLengthFrames": length,
                "TimelineOffsetFrames": timeline_offset,
            })
            dedupe.add(key)
    for c in node[3]:
        if isinstance(c, list):
            recursive_search(c, timeline_offset, edit_rate, results, dedupe)
    return results

def extract_metadata(mob_node):
    meta = {
        "Name": get_property(mob_node, "Name", "UNKNOWN"),
        "MobID": get_property(mob_node, "MobID", "UNKNOWN"),
        "DiskLabel": "UNKNOWN",
        "TapeID": "UNKNOWN",
        "EditRate": 25,
        "URL": "UNKNOWN",
        "SourceClipStartFrames": 0
    }
    slots = get_property(mob_node, "Slots", [])
    for slot in slots:
        seg = get_property(slot, "Segment")
        if seg and isinstance(seg, list):
            if seg[0] == "Timecode":
                for c in seg[3]:
                    if isinstance(c, list) and c[0] == "Start":
                        try:
                            meta["SourceClipStartFrames"] = int(c[2])
                        except:
                            pass
            if seg[0] == "SourceClip":
                for c in seg[3]:
                    if isinstance(c, list) and c[0] == "Length":
                        try:
                            meta["SourceClipLengthFrames"] = int(c[2])
                        except:
                            pass
    try:
        meta["SourceClipStartFrames"] = extract_timecode_from_source_mob(mob_node)
    except:
        pass
    for child in mob_node[3]:
        if isinstance(child, list) and child[0] == "MobAttributeList":
            for attr in child[3]:
                if isinstance(attr, list):
                    key = get_property(attr, "Name", "")
                    val = get_property(attr, "Value", "")
                    if isinstance(key, str) and "disklabel" in key.lower():
                        meta["DiskLabel"] = val
        if isinstance(child, list) and child[0] == "UserComments":
            for comment in child[3]:
                if isinstance(comment, list):
                    key = get_property(comment, "Name", "")
                    val = get_property(comment, "Value", "")
                    if isinstance(key, str) and "tape" in key.lower():
                        meta["TapeID"] = val
    locators = get_property(mob_node, "EssenceDescription", [])
    if isinstance(locators, list):
        for edesc in locators:
            locs = get_property(edesc, "Locator", [])
            if isinstance(locs, list):
                for loc in locs:
                    url = get_property(loc, "URLString")
                    if url:
                        meta["URL"] = url
    return meta

class SuperEDLGui:
    def __init__(self, master):
        self.master = master
        master.title("Super EDL Extractor")
        master.geometry("1000x700")

        self.load_button = tk.Button(master, text="Load AAF JSON", command=self.load_json)
        self.load_button.pack(pady=5)

        self.text_area = scrolledtext.ScrolledText(master, wrap=tk.WORD, width=140, height=40)
        self.text_area.pack()

    def log_msg(self, msg):
        self.text_area.insert(tk.END, msg + "\n")
        self.text_area.see(tk.END)

    def load_json(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if not filepath: return
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        mobs = find_mobs(data)
        self.log_msg(f"🔍 Mobs indexed: {len(mobs)}")

        seq, start_tc = find_sequence_mob_and_tc(mobs)
        if not seq:
            self.log_msg("❌ No sequence mob found.")
            return
        self.log_msg(f"🎬 Sequence TC Start: {start_tc} frames")

        edit_rate = 25  # Optional: extract from CompositionMob if present
        events = recursive_search(seq, timeline_offset=start_tc, edit_rate=edit_rate)
        self.log_msg(f"🎞️ Events found: {len(events)}")
        unique_sources = len(set(e["SourceMobID"] for e in events))
        self.log_msg(f"📦 Unique source clips: {unique_sources}")

        self.log_msg("📋 Listing events...\n")
        for i, e in enumerate(events, 1):
            mob = get_mob_by_id(mobs, e["SourceMobID"])
            md = extract_metadata(mob) if mob else {}
            source_start = md.get("SourceClipStartFrames", 0)
            in_frame = source_start + e["SourceOffsetFrames"]
            out_frame = in_frame + e["EventLengthFrames"]

            timeline_in = e["TimelineOffsetFrames"]
            timeline_out = timeline_in + e["EventLengthFrames"]

            self.text_area.insert(tk.END, f"Event {i:03} | {md.get('Name')} | Track {e['TrackID']} | Timeline {frames_to_tc(timeline_in)} → {frames_to_tc(timeline_out)} | Source {frames_to_tc(in_frame)} → {frames_to_tc(out_frame)} | File: {md.get('URL', 'N/A')}\n")

if __name__ == "__main__":
    root = tk.Tk()
    app = SuperEDLGui(root)
    root.mainloop()
