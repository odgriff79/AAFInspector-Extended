import json
import csv
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from datetime import datetime

def frames_to_tc(frames, fps):
    hours = frames // (3600 * fps)
    minutes = (frames // (60 * fps)) % 60
    seconds = (frames // fps) % 60
    frame = frames % fps
    return f"{hours:02}:{minutes:02}:{seconds:02}:{frame:02}"

def build_super_edl(json_data, output_prefix, log_callback):
    def log(msg):
        print(msg)
        log_callback(msg + "\n")

    # Build MobID lookup
    mobs = {}
    def recurse_mobs(node):
        if isinstance(node, dict):
            mobid = node.get("MobID")
            if mobid:
                mobs[mobid] = node
            for v in node.values():
                recurse_mobs(v)
        elif isinstance(node, list):
            for item in node:
                recurse_mobs(item)

    recurse_mobs(json_data)
    log(f"✅ Mobs indexed: {len(mobs)}")

    # Find timeline events
    events = []
    def recurse_sequence(node, timeline_cursor=0):
        if isinstance(node, dict):
            if node.get("ClassName") == "SourceClip":
                length = node.get("Length", 0)
                source_id = node.get("SourceID")
                start_time = node.get("StartTime", 0)
                source_slot = node.get("SourceMobSlotID", 0)

                mob = mobs.get(source_id)
                if mob:
                    edit_rate = None
                    disklabel = ""
                    tapeid = ""
                    url = ""
                    sourcefile = ""
                    start_tc_frames = 0

                    # Try to extract EditRate and Start TC from slots
                    slots = mob.get("Slots") or []
                    for s in slots:
                        if isinstance(s, dict):
                            if s.get("SlotID") == source_slot:
                                edit_rate = s.get("EditRate", 25)
                                segment = s.get("Segment")
                                if segment and isinstance(segment, dict):
                                    tc = segment.get("Timecode")
                                    if tc and isinstance(tc, dict):
                                        start_tc_frames = tc.get("Start", 0)
                                        edit_rate = tc.get("FPS", edit_rate)
                    # DiskLabel, TapeID, URL
                    attrs = mob.get("MobAttributeList") or []
                    for a in attrs:
                        if isinstance(a, dict):
                            n = a.get("Name", "").lower()
                            v = a.get("Value", "")
                            if "disk" in n:
                                disklabel = v
                            if "tape" in n:
                                tapeid = v
                    locators = mob.get("EssenceDescription", {}).get("Locator", [])
                    for l in locators:
                        if isinstance(l, dict):
                            url = l.get("URLString", url)

                    sourcefile = mob.get("Name", "")
                    events.append({
                        "TimelineInFrames": timeline_cursor,
                        "TimelineOutFrames": timeline_cursor + length,
                        "SourceID": source_id,
                        "SourceMobSlotID": source_slot,
                        "SourceStartTCFrames": start_tc_frames,
                        "SourceOffsetFrames": start_time,
                        "Length": length,
                        "EditRate": edit_rate,
                        "DiskLabel": disklabel,
                        "TapeID": tapeid,
                        "URL": url,
                        "SourceFile": sourcefile,
                        "MobID": source_id,
                    })

                return
            for v in node.values():
                recurse_sequence(v, timeline_cursor)
        elif isinstance(node, list):
            for item in node:
                recurse_sequence(item, timeline_cursor)

    recurse_sequence(json_data)
    log(f"✅ Events found: {len(events)}")

    if not events:
        messagebox.showwarning("No events", "No timeline events found.")
        return

    # Output files
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = f"{output_prefix}_super_edl_{timestamp}.csv"
    txt_path = f"{output_prefix}_super_edl_{timestamp}.txt"

    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile, open(txt_path, "w", encoding="utf-8") as txtfile:
        fieldnames = [
            "TimelineIn",
            "TimelineOut",
            "SourceStartTC",
            "SourceOffsetFrames",
            "SourceInTC",
            "SourceOutTC",
            "Length",
            "EditRate",
            "DiskLabel",
            "TapeID",
            "SourceFile",
            "URL",
            "MobID",
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for idx, e in enumerate(events, 1):
            fps = e["EditRate"] or 25
            timeline_in_tc = frames_to_tc(e["TimelineInFrames"], fps)
            timeline_out_tc = frames_to_tc(e["TimelineOutFrames"], fps)
            source_start_tc = frames_to_tc(e["SourceStartTCFrames"], fps)
            source_in_frames = e["SourceStartTCFrames"] + e["SourceOffsetFrames"]
            source_out_frames = source_in_frames + e["Length"]
            source_in_tc = frames_to_tc(source_in_frames, fps)
            source_out_tc = frames_to_tc(source_out_frames, fps)

            row = {
                "TimelineIn": timeline_in_tc,
                "TimelineOut": timeline_out_tc,
                "SourceStartTC": source_start_tc,
                "SourceOffsetFrames": e["SourceOffsetFrames"],
                "SourceInTC": source_in_tc,
                "SourceOutTC": source_out_tc,
                "Length": e["Length"],
                "EditRate": fps,
                "DiskLabel": e["DiskLabel"],
                "TapeID": e["TapeID"],
                "SourceFile": e["SourceFile"],
                "URL": e["URL"],
                "MobID": e["MobID"],
            }
            writer.writerow(row)

            txtfile.write(f"Event {idx}\n")
            for k, v in row.items():
                txtfile.write(f"{k}: \"{v}\"\n")
            txtfile.write("\n")

    log(f"✅ CSV saved: {csv_path}")
    log(f"✅ Super EDL TXT saved: {txt_path}")

def main():
    root = tk.Tk()
    root.title("Build Super EDL GUI")

    tk.Label(root, text="Select JSON file:").pack()
    json_entry = tk.Entry(root, width=80)
    json_entry.pack()
    def browse_json():
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if path:
            json_entry.delete(0, tk.END)
            json_entry.insert(0, path)
    tk.Button(root, text="Browse", command=browse_json).pack()

    log = scrolledtext.ScrolledText(root, width=100, height=20)
    log.pack()

    def run():
        path = json_entry.get()
        if not path:
            messagebox.showerror("Error", "Please select a JSON file.")
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        prefix = path.rsplit(".", 1)[0]
        build_super_edl(data, prefix, lambda msg: log.insert(tk.END, msg))

    tk.Button(root, text="Build Super EDL", command=run).pack()
    root.mainloop()

if __name__ == "__main__":
    main()
