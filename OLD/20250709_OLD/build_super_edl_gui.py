import json
import csv
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from datetime import datetime

def frames_to_tc(frames, fps):
    hours = frames // (3600 * fps)
    minutes = (frames % (3600 * fps)) // (60 * fps)
    seconds = (frames % (60 * fps)) // fps
    frames_remain = frames % fps
    return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}:{int(frames_remain):02}"

def recursive_search(node, timeline_cursor, edit_rate, mob_map, events, counters, log_file, dedupe_set):
    counters["visited"] += 1

    if isinstance(node, list):
        for item in node:
            recursive_search(item, timeline_cursor, edit_rate, mob_map, events, counters, log_file, dedupe_set)

    elif isinstance(node, dict):
        classdef = node.get("ClassDefinition", "").lower()

        # Handle TimelineMobSlot to adjust edit rate
        if classdef == "timelinemobslot":
            edit_rate = float(node.get("EditRate", edit_rate))
            segment = node.get("Segment")
            if segment:
                recursive_search(segment, timeline_cursor, edit_rate, mob_map, events, counters, log_file, dedupe_set)

        # Recurse into Sequences
        elif classdef == "sequence":
            components = node.get("Components")
            if components:
                for comp in components:
                    recursive_search(comp, timeline_cursor, edit_rate, mob_map, events, counters, log_file, dedupe_set)

        # SourceClip detection
        elif classdef == "sourceclip":
            mobid = node.get("SourceID", "")
            length = int(node.get("Length", 0))
            start_frame = int(node.get("StartTime", 0))
            slot_id = int(node.get("SourceMobSlotID", 0))

            timeline_in_frame = timeline_cursor
            timeline_out_frame = timeline_cursor + length

            timeline_in_tc = frames_to_tc(timeline_in_frame, edit_rate)
            timeline_out_tc = frames_to_tc(timeline_out_frame, edit_rate)
            source_in_tc = frames_to_tc(start_frame, edit_rate)
            source_out_tc = frames_to_tc(start_frame + length, edit_rate)

            mob_info = mob_map.get(mobid, {})
            disklabel = mob_info.get("DiskLabel", "")
            tapeid = mob_info.get("TapeID", "")
            url = mob_info.get("URL", "")
            sourcefile = mob_info.get("SourceFile", "")
            timecode_start = mob_info.get("TimecodeStart", 0)
            fps = mob_info.get("FPS", edit_rate)

            source_absolute_tc_in = frames_to_tc(timecode_start + start_frame, fps)
            source_absolute_tc_out = frames_to_tc(timecode_start + start_frame + length, fps)

            dedupe_key = (mobid, timeline_in_frame, timeline_out_frame)
            if dedupe_key in dedupe_set:
                return
            dedupe_set.add(dedupe_key)

            events.append({
                "Timeline In Frame": timeline_in_frame,
                "Timeline Out Frame": timeline_out_frame,
                "Timeline In TC": timeline_in_tc,
                "Timeline Out TC": timeline_out_tc,
                "Source Start Frame": start_frame,
                "Source In TC": source_in_tc,
                "Source Out TC": source_out_tc,
                "Source Absolute In TC": source_absolute_tc_in,
                "Source Absolute Out TC": source_absolute_tc_out,
                "Length": length,
                "Edit Rate": edit_rate,
                "DiskLabel": disklabel,
                "TapeID": tapeid,
                "SourceFile": sourcefile,
                "URL": url,
                "MobID": mobid
            })

            log_file.write(
                f"✅ Event: MobID={mobid} TimelineIn={timeline_in_tc}-{timeline_out_tc} "
                f"SourceIn={source_in_tc} AbsIn={source_absolute_tc_in} Frames={length}\n"
            )

        # Always recurse all children
        for v in node.values():
            recursive_search(v, timeline_cursor, edit_rate, mob_map, events, counters, log_file, dedupe_set)

def build_super_edl(json_data, output_prefix, log_widget):
    counters = {"visited": 0}
    events = []
    mob_map = {}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = f"{output_prefix}_super_edl_{timestamp}.csv"
    txt_path = f"{output_prefix}_super_edl_{timestamp}.txt"
    log_path = f"{output_prefix}_super_edl_debug_{timestamp}.log"

    # Build MobID map
    def index_mobs(node):
        if isinstance(node, list):
            for item in node:
                index_mobs(item)
        elif isinstance(node, dict):
            if "MobID" in node:
                mobid = node.get("MobID")
                mob_map[mobid] = {
                    "DiskLabel": node.get("DiskLabel", ""),
                    "TapeID": node.get("TapeID", ""),
                    "URL": node.get("URL", ""),
                    "SourceFile": node.get("SourceFile", ""),
                    "TimecodeStart": int(node.get("TimecodeStart", 0)),
                    "FPS": float(node.get("FPS", 25))
                }
            for v in node.values():
                index_mobs(v)

    index_mobs(json_data)

    log_widget.insert(tk.END, f"✅ Mobs indexed: {len(mob_map)}\n")

    with open(log_path, "w", encoding="utf-8") as logf:
        recursive_search(
            json_data,
            timeline_cursor=0,
            edit_rate=25,
            mob_map=mob_map,
            events=events,
            counters=counters,
            log_file=logf,
            dedupe_set=set()
        )

    log_widget.insert(tk.END, f"✅ Traversed {counters['visited']} nodes\n")
    log_widget.insert(tk.END, f"✅ Events found: {len(events)}\n")

    if events:
        with open(csv_path, "w", newline="", encoding="utf-8") as f_csv:
            fieldnames = list(events[0].keys())
            writer = csv.DictWriter(f_csv, fieldnames=fieldnames)
            writer.writeheader()
            for e in events:
                writer.writerow(e)

        with open(txt_path, "w", encoding="utf-8") as f_txt:
            f_txt.write("Super EDL\n\n")
            for idx, e in enumerate(events, 1):
                f_txt.write(f"Event {idx}\n")
                f_txt.write(f"Timeline In: \"{e['Timeline In TC']}\" | Out: \"{e['Timeline Out TC']}\"\n")
                f_txt.write(f"Source In: \"{e['Source In TC']}\" | Out: \"{e['Source Out TC']}\"\n")
                f_txt.write(f"Source Absolute In: \"{e['Source Absolute In TC']}\" | Out: \"{e['Source Absolute Out TC']}\"\n")
                f_txt.write(f"Length: {e['Length']} frames\n")
                f_txt.write(f"Edit Rate: {e['Edit Rate']}\n")
                f_txt.write(f"DiskLabel: {e['DiskLabel']} | TapeID: {e['TapeID']}\n")
                f_txt.write(f"SourceFile: {e['SourceFile']}\n")
                f_txt.write(f"URL: {e['URL']}\n")
                f_txt.write(f"MobID: {e['MobID']}\n\n")

        log_widget.insert(tk.END, f"✅ CSV saved: {csv_path}\n")
        log_widget.insert(tk.END, f"✅ TXT saved: {txt_path}\n")
    else:
        log_widget.insert(tk.END, "⚠️ No events found.\n")

class SuperEDLApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Super EDL Extractor")

        self.json_data = None
        self.output_prefix = ""

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack()
        tk.Button(root, text="Generate Super EDL", command=self.run).pack()

        self.log = scrolledtext.ScrolledText(root, width=100, height=30)
        self.log.pack()

    def load_json(self):
        path = filedialog.askopenfilename(title="Select Compressed JSON")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            self.json_data = json.load(f)
        self.output_prefix = path.rsplit(".", 1)[0]
        self.log.insert(tk.END, f"✅ Loaded JSON: {path}\n")

    def run(self):
        if not self.json_data:
            messagebox.showerror("Error", "Please load JSON first.")
            return
        build_super_edl(self.json_data, self.output_prefix, self.log)

if __name__ == "__main__":
    tk.Tk().after(0, lambda: SuperEDLApp(tk.Tk()))
    tk.mainloop()
