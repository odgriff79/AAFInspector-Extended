#!/usr/bin/env python3
# AAF_Source_Originals_V1.py
# v1-simple: identify definitive original sources only.
# Output: "Name | MobID | Path"
# Requires: pyaaf2==1.4.0

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import aaf2
from urllib.parse import urlparse, unquote

# -------------------- logging --------------------

def debug(msg: str):
    print(f"[DEBUG] {msg}")
    if hasattr(debug, "log_widget") and debug.log_widget:
        debug.log_widget.insert(tk.END, f"[DEBUG] {msg}\n")
        debug.log_widget.see(tk.END)

# -------------------- mob map --------------------

def build_mob_map(aaf_file):
    mob_map = {}
    for mob in aaf_file.content.mobs:
        mob_map[mob.mob_id] = mob
        debug(f"Mob added: {getattr(mob,'name','')} (MobID: {mob.mob_id})")
    return mob_map

# -------------------- locator helpers (robust, windows-aware) --------------------

def _first_url_like_from_taggedvalues(mob):
    """Fallback if descriptor.locators are missing—look for URL-ish TaggedValues."""
    for attr_name in ("user_comments", "attributes"):
        try:
            for tv in getattr(mob, attr_name, []) or []:
                name = (getattr(tv, "name", "") or "").lower()
                val  = (getattr(tv, "value", "") or "")
                if not val:
                    continue
                if "url" in name or "urlstring" in name or name.endswith("url"):
                    return str(val)
        except Exception:
            pass
    return ""

def _url_to_path_winaware(url: str) -> str:
    """Convert URLs like file:///C:/... or file:/Volumes/... to normalized paths."""
    if not url:
        return ""
    u = urlparse(url)
    p = unquote(u.path or "")
    # Windows: file:///C:/path -> C:/path
    if u.scheme in ("file", "") and p.startswith("/") and len(p) >= 3 and p[2] == ":":
        p = p.lstrip("/")
    return p.replace("\\", "/")

def extract_locator_path(mob):
    """Best-effort to extract a real file path from a mob."""
    # 1) descriptor -> locators
    try:
        desc = getattr(mob, "descriptor", None) or getattr(mob, "essence_descriptor", None)
        locs = (getattr(desc, "locators", []) or []) if desc else []
        for loc in locs:
            for attr in ("url", "URLString", "path"):
                if hasattr(loc, attr):
                    v = getattr(loc, attr)
                    if v:
                        path = _url_to_path_winaware(str(v))
                        if path:
                            debug(f"Locator URL found: {path}")
                            return path
    except Exception:
        pass
    # 2) fallback: URL-like tagged values
    url_fallback = _first_url_like_from_taggedvalues(mob)
    if url_fallback:
        path = _url_to_path_winaware(url_fallback)
        if path:
            debug(f"Locator (TaggedValue) found: {path}")
            return path
    # 3) nothing found
    return "No locator"

# -------------------- source chain helpers --------------------

def _sourceclip_target_mobid(sc):
    """Robustly get the referenced mob id from a SourceClip (handles pyaaf2 variants)."""
    v = getattr(sc, "source_id", None)
    if v:
        return v
    sm = getattr(sc, "source_mob", None)
    if sm is not None:
        mid = getattr(sm, "mob_id", None)
        if mid:
            return mid
    v = getattr(sc, "mob_id", None)  # rare fallback
    return v

def _next_sourceclip_id_in_segment(seg):
    """
    Find the next SourceClip target inside a segment:
      - direct SourceClip
      - Sequence components
      - effect/operation 'inputs'
    Return referenced mob id or None.
    """
    try:
        # Direct SourceClip
        if isinstance(seg, aaf2.components.SourceClip):
            return _sourceclip_target_mobid(seg)

        # Sequence: scan components
        if isinstance(seg, aaf2.components.Sequence):
            for comp in seg.components:
                # SourceClip as component
                if isinstance(comp, aaf2.components.SourceClip):
                    return _sourceclip_target_mobid(comp)
                # Components may have inputs (e.g., OperationGroup)
                if hasattr(comp, "inputs"):
                    for inp in comp.inputs or []:
                        if isinstance(inp, aaf2.components.SourceClip):
                            return _sourceclip_target_mobid(inp)

        # Non-sequence effect with inputs
        if hasattr(seg, "inputs"):
            for inp in seg.inputs or []:
                if isinstance(inp, aaf2.components.SourceClip):
                    return _sourceclip_target_mobid(inp)
    except Exception:
        pass
    return None

def _next_sourceclip_id_in_mob(mob):
    """Scan all slots of a mob to find the next SourceClip target mob id."""
    try:
        for slot in getattr(mob, "slots", []) or []:
            seg = slot.segment
            nid = _next_sourceclip_id_in_segment(seg)
            if nid:
                return nid
    except Exception:
        pass
    return None

# -------------------- definitive resolver (follow to leaf) --------------------

def resolve_genuine_source(mob_id, mob_map):
    """
    Follow the SourceClip chain all the way to the definitive leaf mob.
    Do NOT stop early just because an intermediate mob has (or lacks) a locator.
    Return the final reachable mob (leaf). If the chain breaks, return the last valid mob seen.
    """
    if not mob_id:
        return None

    visited = set()
    current_id = mob_id
    last_valid_mob = None
    depth = 0

    while current_id and current_id not in visited and depth < 32:
        visited.add(current_id)
        mob = mob_map.get(current_id)
        if not mob:
            break
        last_valid_mob = mob  # remember the last valid mob we reached

        # walk to next
        next_id = _next_sourceclip_id_in_mob(mob)
        if not next_id:
            # no deeper SourceClip → this is the leaf
            break

        current_id = next_id
        depth += 1

    return last_valid_mob

# -------------------- minimal timeline traversal --------------------

def recursive_timeline_traverse(seg, results=None):
    """
    Collect SourceClip occurrences (minimal info).
    Returns list of dicts with only {'MobID'} for each occurrence.
    """
    if results is None:
        results = []
    try:
        if isinstance(seg, aaf2.components.Sequence):
            for comp in seg.components:
                recursive_timeline_traverse(comp, results)
        elif isinstance(seg, aaf2.components.SourceClip):
            results.append({"MobID": _sourceclip_target_mobid(seg)})
        else:
            if hasattr(seg, "inputs"):
                for inp in seg.inputs or []:
                    recursive_timeline_traverse(inp, results)
    except Exception as e:
        debug(f"recursive_timeline_traverse error: {e}")
    return results

# -------------------- extraction (v1: originals only) --------------------

def extract_original_sources(top_mob, mob_map):
    """
    v1 output: list of strings "Name | MobID | Path"
    No durations, no TC, no extra metadata.
    """
    out = []
    if not getattr(top_mob, "slots", None):
        return out

    seen_leaf_ids = set()  # de-dup on the definitive leaf mob

    for slot in top_mob.slots:
        seg = slot.segment
        events = recursive_timeline_traverse(seg)
        for e in events:
            start_id = e.get("MobID")
            if not start_id:
                continue
            leaf = resolve_genuine_source(start_id, mob_map)
            if not leaf:
                continue
            leaf_id = getattr(leaf, "mob_id", None)
            if leaf_id in seen_leaf_ids:
                continue
            seen_leaf_ids.add(leaf_id)

            name = getattr(leaf, "name", "Unnamed")
            mobid = str(leaf_id or "N/A")
            path = extract_locator_path(leaf)
            out.append(f"{name} | {mobid} | {path}")

    return out

# -------------------- GUI --------------------

def browse_file():
    path = filedialog.askopenfilename(title="Open AAF", filetypes=[("AAF files", "*.aaf"), ("All files", "*.*")])
    if not path:
        return
    result_box.delete(0, tk.END)
    debug.log_widget.delete(1.0, tk.END)
    debug(f"Selected file: {path}")
    try:
        with aaf2.open(path) as f:
            debug("AAF file opened.")
            mob_map = build_mob_map(f)

            # pick top-level CompositionMob (v1)
            from aaf2.mobs import CompositionMob
            comps = [m for m in f.content.mobs if isinstance(m, CompositionMob)]
            if not comps:
                debug("No CompositionMob found.")
                messagebox.showerror("Error", "No CompositionMob found in AAF.")
                return

            top_mob = comps[0]
            debug(f"Using top-level CompositionMob: {getattr(top_mob, 'name', 'Unnamed')}")

            rows = extract_original_sources(top_mob, mob_map)
            if not rows:
                result_box.insert(tk.END, "[No sources found]")
            for line in rows:
                result_box.insert(tk.END, line)
                debug(line)

    except Exception as e:
        messagebox.showerror("Error", f"Failed to parse AAF:\n{e}")
        debug(f"Exception: {e}")

# Tk UI
root = tk.Tk()
root.title("AAF Originals — v1 simple (leaf resolver)")
tk.Button(root, text="Select AAF File", command=browse_file).pack(pady=10)
result_box = tk.Listbox(root, width=140, height=20)
result_box.pack(padx=10, pady=10)
debug.log_widget = scrolledtext.ScrolledText(root, wrap=tk.WORD, height=10, font=("Courier", 9))
debug.log_widget.pack(padx=10, pady=5, fill=tk.BOTH, expand=False)
root.mainloop()
