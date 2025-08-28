import json
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def parse_node(node):
    if not isinstance(node, list) or len(node) < 4:
        return None
    name, node_type, value, children = node
    parsed = {"name": name, "type": node_type, "value": value, "children": []}
    for child in children:
        child_parsed = parse_node(child)
        if child_parsed:
            parsed["children"].append(child_parsed)
    return parsed

def find_child(node, name):
    for child in node.get("children", []):
        if child["name"] == name:
            return child
    return None

def get_prop(node, prop_name):
    prop = find_child(node, prop_name)
    return prop["value"] if prop else "N/A"

def extract_mobs(parsed_root):
    mobs = []
    header = find_child(parsed_root, "Header")
    if not header:
        logging.warning("No 'Header' found.")
        return mobs

    header_data = find_child(header, "Header")
    content = find_child(header_data, "Content") if header_data else None
    content_storage = find_child(content, "ContentStorage") if content else None
    mob_set = find_child(content_storage, "Mobs") if content_storage else None

    if not mob_set:
        logging.warning("No 'Mobs' found.")
        return mobs

    for mob_entry in mob_set["children"]:
        mob_type = mob_entry["name"]
        mob_data = mob_entry
        mob_name = get_prop(mob_data, "Name")
        mob_id = get_prop(mob_data, "MobID")
        slots = find_child(mob_data, "Slots")
        if not slots:
            continue

        for slot in slots["children"]:
            slot_type = slot["name"]
            slot_data = slot
            slot_id = get_prop(slot_data, "SlotID")
            track_number = get_prop(slot_data, "PhysicalTrackNumber")
            edit_rate = get_prop(slot_data, "EditRate")
            origin = get_prop(slot_data, "Origin")
            segment = find_child(slot_data, "Segment")
            if not segment or not segment["children"]:
                continue

            segment_type = segment["children"][0]["name"]
            segment_data = segment["children"][0]
            start = length = fps = source_id = "N/A"

            if segment_type == "Sequence":
                components = find_child(segment_data, "Components")
                if components:
                    for comp in components["children"]:
                        comp_type = comp["name"]
                        comp_data = comp
                        if comp_type == "Timecode":
                            start = get_prop(comp_data, "Start")
                            length = get_prop(comp_data, "Length")
                            fps = get_prop(comp_data, "FPS")
                        elif comp_type == "SourceClip":
                            start = get_prop(comp_data, "StartTime")
                            length = get_prop(comp_data, "Length")
                            source_id = get_prop(comp_data, "SourceID")
                        elif comp_type == "Filler":
                            length = get_prop(comp_data, "Length")
            elif segment_type == "Timecode":
                start = get_prop(segment_data, "Start")
                length = get_prop(segment_data, "Length")
                fps = get_prop(segment_data, "FPS")

            mobs.append({
                "Mob Name": mob_name,
                "Mob Type": mob_type,
                "Slot ID": slot_id,
                "Track #": track_number,
                "Edit Rate": edit_rate,
                "Origin": origin,
                "Segment Type": segment_type,
                "Start": start,
                "Length": length,
                "FPS": fps,
                "Source ID": source_id
            })

    return mobs

class MobViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AAF Mob Slot Viewer")
        self.geometry("1200x600")

        columns = ("Mob Name", "Mob Type", "Slot ID", "Track #", "Edit Rate", "Origin",
                   "Segment Type", "Start", "Length", "FPS", "Source ID")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=100)
        self.tree.pack(fill=tk.BOTH, expand=True)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill=tk.X)
        tk.Button(btn_frame, text="Load JSON", command=self.load_json).pack(side=tk.LEFT, padx=10, pady=5)

    def load_json(self):
        file_path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            if isinstance(raw_data, list) and len(raw_data) == 4 and isinstance(raw_data[3], list):
                root_node = raw_data[3][0]
            else:
                root_node = raw_data

            parsed = parse_node(root_node)
            mob_data = extract_mobs(parsed)
            self.tree.delete(*self.tree.get_children())
            for row in mob_data:
                self.tree.insert("", tk.END, values=tuple(row.values()))
        except Exception as e:
            logging.exception("Failed to load JSON")
            messagebox.showerror("Error", f"Failed to load JSON:\n{e}")

if __name__ == "__main__":
    app = MobViewer()
    app.mainloop()
