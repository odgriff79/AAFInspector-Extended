import os
import json
import csv
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def recursive_search(node, slot_name=None, timeline_offset=0, edit_rate=25, results=None, depth=0, counters=None, log_file=None):
    if results is None:
        results = []
    if counters is None:
        counters = {"visited":0}

    if not isinstance(node, list) or len(node) < 2:
        return results

    name = node[0]
    class_name = node[1]
    children = node[3] if len(node) > 3 else []

    counters["visited"] += 1
    indent = "  " * depth
    if log_file:
        log_file.write(f"{indent}Node: {name} | Class: {class_name}\n")

    # Extract EditRate if available
    for c in children:
        if isinstance(c, list):
            if c[0] == "EditRate":
                try:
                    edit_rate = float(c[2])
                except:
                    pass
            elif c[0] == "FPS":
                try:
                    edit_rate = float(c[2])
                except:
                    pass

    if name == "Sequence":
        for c in children:
            if isinstance(c, list) and c[0] == "Components":
                for comp in c[3]:
                    recursive_search(comp, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)

    if name == "SourceClip":
        mobid = ""
        source_start = 0
        length = 0
        for c in children:
            if isinstance(c, list):
                if c[0] == "SourceID":
                    mobid = c[2]
                elif c[0] in ("Start", "StartTime"):
                    source_start = int(c[2])
                elif c[0] == "Length":
                    length = int(c[2])
        results.append({
            "Slot": slot_name,
            "MobID": mobid,
            "TimelineStartFrame": timeline_offset,
            "SourceStartFrame": source_start,
            "Length": length,
            "EditRate": edit_rate
        })
        if log_file:
            log_file.write(f"{indent}🎯 SourceClip: MobID={mobid}, Start={source_start}, TimelineStart={timeline_offset}, Length={length}, EditRate={edit_rate}\n")

        timeline_offset += length
        return results

    if "Segment" in name or "OperationGroup" in name:
        for c in children:
            recursive_search(c, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)
        return results

    for c in children:
        recursive_search(c, slot_name, timeline_offset, edit_rate, results, depth+1, counters, log_file)
    return results

def create_mob_map(node, mob_map=None):
    """
    Recursively traverses the entire JSON tree and creates a dictionary
    mapping each MobID to its corresponding Mob object.
    """
    if mob_map is None:
        mob_map = {}
    if not isinstance(node, list) or len(node) < 2:
        return mob_map

    children = node[3] if len(node) > 3 else []
    
    # A Mob object is identified by having a MobID as a direct child.
    is_mob_object = False
    mob_id = None
    for child in children:
        if isinstance(child, list) and child[0] == 'MobID':
            is_mob_object = True
            mob_id = child[2]
            break
            
    if is_mob_object and mob_id:
        mob_map[mob_id] = node

    # Continue recursion through all children
    for child in children:
        create_mob_map(child, mob_map)
        
    return mob_map

def extract_metadata_from_mob(mob_node):
    """
    Recursively searches within a single Mob object for metadata
    like URLString, DiskLabel, and TapeID.
    """
    metadata = {"DiskLabel": "", "TapeID": "", "URLString": ""}
    
    def search_in_node(node):
        if not isinstance(node, list) or len(node) < 2:
            return
            
        name = node[0]
        class_name = node[1]
        children = node[3] if len(node) > 3 else []

        # Case 1: Find URLString property directly
        if name == "URLString" and len(node) > 2:
            metadata["URLString"] = node[2]

        # Case 2: Find attribute objects (Key-Value pairs in a ClassDefinition)
        if class_name == "ClassDefinition":
            attr_name_node = None
            attr_value_node = None
            for child in children:
                if isinstance(child, list):
                    if child[0] == 'Name':
                        attr_name_node = child
                    elif child[0] == 'Value':
                        attr_value_node = child
            
            if attr_name_node and attr_value_node and len(attr_name_node) > 2:
                name_val = attr_name_node[2]
                value_val = attr_value_node[2] if len(attr_value_node) > 2 else ""
                if name_val == "_IMPORTDISKLAB":
                    metadata["DiskLabel"] = value_val
                elif name_val == "TapeID":
                    metadata["TapeID"] = value_val
        
        # Recurse into all children
        for child in children:
            search_in_node(child)
    
    search_in_node(mob_node)
    return metadata

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("AAF Sequence Extractor with Full Metadata")
        self.root.geometry("1100x700")

        tk.Button(root, text="Select Compressed JSON", command=self.load_json).pack(pady=5)
        tk.Button(root, text="Extract All Sequence Details", command=self.process).pack(pady=10)

        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=140, height=35)
        self.log.pack()

        self.paths = {"json": None}
        self.json_data = None
        self.json_dir = None

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def load_json(self):
        path = filedialog.askopenfilename(title="Select Compressed JSON", filetypes=[("JSON Files","*.json")])
        if path:
            with open(path, "r", encoding="utf-8") as jf:
                self.json_data = json.load(jf)
            self.paths["json"] = path
            self.json_dir = os.path.dirname(path)
            self.log_msg(f"✅ Loaded JSON:\n{path}")

    def process(self):
        if not self.json_data:
            self.log_msg("❌ Please load the JSON file first.")
            return

        self.log.delete(1.0, tk.END)
        
        # Stage 1: Create the MobID-to-Mob map for efficient lookup
        self.log_msg("🗺️ Building MobID map for faster metadata lookup...")
        mob_map = create_mob_map(self.json_data)
        self.log_msg(f"✅ Found {len(mob_map)} total Mobs in the file.")

        # Stage 2: Extract timeline events (SourceClips)
        self.log_msg("🔍 Extracting timeline events...")
        counters = {"visited":0}
        debug_path = os.path.join(self.json_dir, "sequence_debug_log.txt")
        with open(debug_path, "w", encoding="utf-8") as logf:
            events = recursive_search(
                self.json_data,
                counters=counters,
                log_file=logf
            )
        self.log_msg(f"✅ Traversed {counters['visited']} nodes (log saved to {debug_path})")
        self.log_msg(f"✅ Found {len(events)} SourceClip events.")

        # Stage 3: Enrich events with metadata using the map
        self.log_msg("📝 Enriching events with DiskLabel, TapeID, and URLString...")
        enriched = []
        found_count = 0
        for e in events:
            mob_node = mob_map.get(e["MobID"])
            metadata = {}
            if mob_node:
                metadata = extract_metadata_from_mob(mob_node)
                if any(metadata.values()):
                    found_count += 1

            enriched.append({
                "Slot": e["Slot"],
                "MobID": e["MobID"],
                "TimelineStartFrame": e["TimelineStartFrame"],
                "SourceStartFrame": e["SourceStartFrame"],
                "Length": e["Length"],
                "EditRate": e["EditRate"],
                "DiskLabel": metadata.get("DiskLabel", ""),
                "TapeID": metadata.get("TapeID", ""),
                "URLString": metadata.get("URLString", "")
            })
        self.log_msg(f"✅ Found metadata for {found_count} of {len(events)} clips.")

        # Stage 4: Write to CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(self.json_dir, f"sequence_details_{timestamp}.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = [
                "Slot","MobID","TimelineStartFrame","SourceStartFrame","Length","EditRate","DiskLabel","TapeID","URLString"
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in enriched:
                writer.writerow(row)

        self.log_msg(f"✅ CSV generated:\n{out_path}")
        messagebox.showinfo("Success", f"CSV saved:\n{out_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()