import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

def load_json_file():
    file_path = filedialog.askopenfilename(
        title="Select AAF JSON File",
        filetypes=[("JSON Files", "*.json")]
    )
    if not file_path:
        return
    json_path_var.set(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        global aaf_data
        aaf_data = data
        populate_sequence_dropdown(data)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to load JSON:\n{e}")

def get_mobs(data):
    return data if isinstance(data, list) else data.get("mobs", [])

def populate_sequence_dropdown(data):
    sequence_names = [
        mob.get("name") for mob in get_mobs(data)
        if mob.get("class") == "CompositionMob"
    ]
    sequence_dropdown["values"] = sequence_names
    if sequence_names:
        sequence_dropdown.current(0)

def extract_sequence():
    if not aaf_data:
        messagebox.showwarning("Warning", "No JSON data loaded.")
        return

    target_sequence = sequence_var.get()
    if not target_sequence:
        messagebox.showwarning("Warning", "Please select a sequence.")
        return

    mob_index = {}
    for mob in get_mobs(aaf_data):
        mob_id = mob.get("mobID") or mob.get("mob_id")
        if mob_id:
            mob_index[mob_id] = mob

    sequence_mob = next(
        (mob for mob in get_mobs(aaf_data)
         if mob.get("class") == "CompositionMob" and mob.get("name") == target_sequence),
        None
    )

    if not sequence_mob:
        messagebox.showerror("Error", f"Sequence '{target_sequence}' not found.")
        return

    summary = []
    for slot in sequence_mob.get("slots", []):
        track_name = slot.get("name", f"Track_{slot.get('slotID')}")
        segment = slot.get("segment")
        if not segment:
            continue

        if segment.get("class") == "Sequence":
            for component in segment.get("components", []):
                if component.get("class") == "SourceClip":
                    source_mob_id = component.get("sourceMobID")
                    start_time = component.get("startTime")
                    length = component.get("length")
                    edit_rate = component.get("editRate", [25, 1])
                    fps = edit_rate[0] / edit_rate[1] if edit_rate[1] != 0 else 25

                    source_mob = mob_index.get(source_mob_id)
                    source_name = source_mob.get("name") if source_mob else "UNKNOWN"
                    file_path = "N/A"

                    if source_mob:
                        descriptors = source_mob.get("descriptors") or []
                        for desc in descriptors:
                            locators = desc.get("locators", [])
                            for loc in locators:
                                if "URLString" in loc:
                                    file_path = loc["URLString"].replace("file://localhost/", "")
                                    break

                    summary.append({
                        "track": track_name,
                        "source_clip": source_name,
                        "source_mob_id": source_mob_id,
                        "start_frame": start_time,
                        "length": length,
                        "record_start_tc": int(start_time / fps),
                        "record_end_tc": int((start_time + length) / fps),
                        "file_path": file_path
                    })

    if not summary:
        messagebox.showinfo("Info", "No SourceClips found in the selected sequence.")
        return

    output_path = Path(f"{target_sequence}_summary.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    messagebox.showinfo("Success", f"Summary saved to:\n{output_path.resolve()}")

# === GUI SETUP ===
aaf_data = None

root = tk.Tk()
root.title("AAF Sequence Extractor")

frame = ttk.Frame(root, padding=10)
frame.grid(row=0, column=0, sticky="nsew")

json_path_var = tk.StringVar()
sequence_var = tk.StringVar()

ttk.Label(frame, text="AAF JSON File:").grid(row=0, column=0, sticky="w")
ttk.Entry(frame, textvariable=json_path_var, width=60).grid(row=0, column=1, padx=5)
ttk.Button(frame, text="Browse", command=load_json_file).grid(row=0, column=2)

ttk.Label(frame, text="Select Sequence:").grid(row=1, column=0, sticky="w", pady=10)
sequence_dropdown = ttk.Combobox(frame, textvariable=sequence_var, width=57)
sequence_dropdown.grid(row=1, column=1, padx=5, columnspan=2)

ttk.Button(frame, text="Extract Sequence", command=extract_sequence).grid(row=2, column=1, pady=15)

root.mainloop()
