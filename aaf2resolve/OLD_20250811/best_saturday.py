#!/usr/bin/env python3
# AAF_Source_Originals_V3_Corrected.py
# v3: Implements robust timeline traversal inspired by the proven JSON-parsing logic.
# Output per row: Name | MobID | Path
# Requires: pyaaf2==1.4.0

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import aaf2
from urllib.parse import urlparse, unquote

# -------------------- logging --------------------

def debug(msg: str):
    """Prints a message to the console and the GUI log widget if available."""
    print(f"[DEBUG] {msg}")
    if hasattr(debug, "log_widget") and debug.log_widget:
        debug.log_widget.insert(tk.END, f"[DEBUG] {msg}\n")
        debug.log_widget.see(tk.END)

# -------------------- mob map --------------------

def build_mob_map(aaf_file):
    """Creates a dictionary mapping MobIDs to Mob objects for quick lookup."""
    mob_map = {}
    for mob in aaf_file.content.mobs:
        mob_map[mob.mob_id] = mob
        name = getattr(mob, 'name', None)
        if name:
            debug(f"Mob added to map: {name}")
    return mob_map

# -------------------- locator helpers --------------------

def _url_to_path_winaware(url: str) -> str:
    """Convert URLs like file:///C:/... to normalized paths."""
    if not url: return ""
    p = unquote(urlparse(url).path)
    if p.startswith("/") and len(p) >= 3 and p[2] == ":":
        p = p.lstrip("/")
    return p.replace("\\", "/")

def extract_locator_path(mob):
    """Best-effort to extract a real file path from a mob's descriptor."""
    if not hasattr(mob, 'descriptor') or not mob.descriptor: return "No descriptor"
    if not hasattr(mob.descriptor, 'locators') or not mob.descriptor.locators: return "No locators"
    
    for loc in mob.descriptor.locators:
        if hasattr(loc, 'path') and loc.path:
            path = _url_to_path_winaware(str(loc.path))
            if path:
                debug(f"Locator Path found for '{getattr(mob, 'name', 'Unnamed')}': {path}")
                return path
    return "No valid path in locators"

# -------------------- source resolution --------------------

def _sourceclip_target_mobid(sc):
    """Robustly get the referenced mob id from a SourceClip."""
    mob_id = getattr(sc, 'source_mob_id', None)
    if mob_id: return mob_id
    source_mob = getattr(sc, 'source_mob', None)
    if source_mob: return getattr(source_mob, 'mob_id', None)
    return None

def resolve_genuine_source(mob_id, mob_map):
    """Walks the SourceClip chain to the definitive original source mob."""
    visited = set()
    current_mob = mob_map.get(mob_id)
    final_mob = current_mob

    while current_mob and current_mob.mob_id not in visited:
        visited.add(current_mob.mob_id)
        final_mob = current_mob
        next_id = None
        
        if hasattr(current_mob, 'slots'):
            for slot in current_mob.slots:
                if slot.media_kind == 'picture':
                    seg = slot.segment
                    if isinstance(seg, aaf2.components.SourceClip):
                        nid = _sourceclip_target_mobid(seg)
                        if nid:
                            next_id = nid
                            break
        
        if next_id:
            next_mob = mob_map.get(next_id)
            if next_mob: current_mob = next_mob
            else: break
        else: break
            
    if final_mob: debug(f"Resolved to: {getattr(final_mob, 'name', 'Unnamed')}")
    return final_mob

# -------------------- timeline traversal (NEW LOGIC) --------------------

def find_clips_in_component(component, found_clips):
    """
    NEW: Recursively explores a component, inspired by the JSON script's logic,
    to find all nested SourceClips.
    """
    # If it's a Sequence or similar container, iterate through its sub-components
    if hasattr(component, 'components'):
        for comp in component.components:
            find_clips_in_component(comp, found_clips)
            
    # If we find a SourceClip, get its MobID and add it to our list
    elif isinstance(component, aaf2.components.SourceClip):
        mob_id = _sourceclip_target_mobid(component)
        if mob_id:
            found_clips.append({"MobID": mob_id})

def extract_original_sources(top_mob, mob_map):
    """
    NEW: This function now correctly initiates the search. It loops through the
    main timeline's tracks and uses the recursive helper function to find all clips.
    """
    all_clips_on_timeline = []
    
    # Iterate through the main timeline's tracks (slots)
    if not hasattr(top_mob, 'slots'): return []
    debug(f"Searching for video tracks in '{getattr(top_mob, 'name', 'Unnamed')}'...")
    for slot in top_mob.slots:
        # We only care about video tracks
        if slot.media_kind == 'picture':
            debug(f"Found video track (Slot {slot.slot_id}). Traversing segment...")
            # Start the recursive search on the segment of this track
            find_clips_in_component(slot.segment, all_clips_on_timeline)

    debug(f"Found {len(all_clips_on_timeline)} video clips on the timeline.")
    
    # Now, resolve and de-duplicate the genuine sources
    out = []
    resolved_source_ids = set()
    for clip_info in all_clips_on_timeline:
        initial_mob_id = clip_info.get("MobID")
        if not initial_mob_id: continue

        resolved_mob = resolve_genuine_source(initial_mob_id, mob_map)

        if resolved_mob and resolved_mob.mob_id not in resolved_source_ids:
            resolved_source_ids.add(resolved_mob.mob_id)
            name = getattr(resolved_mob, "name", "Unnamed")
            mobid_str = str(resolved_mob.mob_id)
            path = extract_locator_path(resolved_mob)
            out.append(f"{name} | {mobid_str} | {path}")
            
    return out

# -------------------- GUI --------------------

def browse_file():
    path = filedialog.askopenfilename(title="Open AAF", filetypes=[("AAF files", "*.aaf"), ("All files", "*.*")])
    if not path: return
        
    result_box.delete(0, tk.END)
    debug.log_widget.delete(1.0, tk.END)
    debug(f"Selected file: {path}")
    
    try:
        with aaf2.open(path, 'r') as f:
            debug("AAF file opened.")
            mob_map = build_mob_map(f)
            
            # Find top-level CompositionMobs
            top_mobs = [m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob)]
            if not top_mobs:
                messagebox.showerror("Error", "No Composition Mobs found in this AAF file.")
                return

            top_mob = top_mobs[-1] # Assume the last one is the main sequence
            debug(f"Using top-level CompositionMob: {getattr(top_mob, 'name', 'Unnamed')}")

            rows = extract_original_sources(top_mob, mob_map)
            
            if not rows:
                result_box.insert(tk.END, "[No unique original sources found]")
            else:
                for line in sorted(rows):
                    result_box.insert(tk.END, line)
                debug("Processing finished successfully.")

    except Exception as e:
        messagebox.showerror("Error", f"An error occurred:\n{e}")
        debug(f"FATAL EXCEPTION: {e}")

# --- Tkinter UI Setup ---
root = tk.Tk()
root.title("AAF Genuine Source Finder (v3)")
main_frame = tk.Frame(root, padx=10, pady=10)
main_frame.pack(fill=tk.BOTH, expand=True)
tk.Button(main_frame, text="Select AAF File", command=browse_file, font=("Helvetica", 10, "bold")).pack(pady=5)
result_box = tk.Listbox(main_frame, width=140, height=20, font=("Courier", 10))
result_box.pack(fill=tk.BOTH, expand=True, pady=5)
debug.log_widget = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=12, font=("Courier", 9))
debug.log_widget.pack(fill=tk.BOTH, expand=True, pady=5)
root.mainloop()