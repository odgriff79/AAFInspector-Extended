#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, re
from datetime import datetime

# --- Environment Verification ---
try:
    resolve
except NameError:
    raise RuntimeError("❌ This script must run within Resolve via Workspace > Scripts.")

# --- Utilities ---
def clean_path(filepath):
    if not filepath:
        return None
    norm = os.path.normpath(filepath.replace("\\", "/"))
    match = re.search(r"[a-zA-Z]:/|//", norm)
    return norm[match.start():] if match else norm

def ensure_media_storage(ms):
    return ms and callable(getattr(ms, "AddItemListToMediaPool", None))

def mark_clip_as_offline(timeline, frame, label, original_path, source_in, duration):
    for item in timeline.GetItemListInTrack("video", 1):
        if item and item.GetStart() == frame:
            item.SetClipColor("Red")
            item.SetProperty("ClipName", f"OFFLINE – {label}")
            note = f"Original: {original_path}\nIn: {source_in}\nDuration: {duration}"
            item.AddMarker(0, "Red", "OFFLINE METADATA", note, 1)
            print(f"🟥 OFFLINE MARKED: {label} @ {frame}")
            return
    print(f"⚠️ Unable to tag offline clip at frame {frame}.")

# --- Main Entry Point ---
def main():
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        raise RuntimeError("❌ No project open.")

    media_pool = project.GetMediaPool()
    media_storage = resolve.GetMediaStorage()
    fusion = resolve.Fusion()

    if not ensure_media_storage(media_storage):
        raise RuntimeError("❌ MediaStorage API not available.")

    # --- File Inputs via UI ---
    json_path = fusion.AskUserForPath("Select timeline JSON file")
    if not json_path:
        raise RuntimeError("JSON file not selected.")

    placeholder_path = fusion.AskUserForPath("Select placeholder image for offline clips")
    if not (placeholder_path and os.path.exists(placeholder_path)):
        raise RuntimeError("❌ Placeholder image path is invalid or missing.")

    print(f"[INFO] Loading JSON: {os.path.basename(json_path)}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    clips = data.get("clips", [])
    effects = data.get("filler_effects", [])
    markers = data.get("markers", [])
    all_events = clips + effects
    if not all_events:
        raise RuntimeError("❌ No timeline events found in JSON.")

    offset = min(item.get("timeline_start", 0) for item in all_events)
    timeline_name = f"Build {datetime.now().strftime('%H%M%S')}"
    timeline = media_pool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        raise RuntimeError("Failed to create timeline.")
    project.SetCurrentTimeline(timeline)

    # --- Clip Placement ---
    stats = {"online": 0, "offline": 0, "markers": 0}
    for clip in clips:
        original = clip.get("source_file")
        source_path = clean_path(original)
        record_frame = clip.get("timeline_start", 0) - offset
        duration = clip.get("duration", 1)
        source_in = clip.get("source_in", 0)

        if record_frame < 0:
            continue

        label = os.path.basename(source_path or original or "Unknown")
        is_offline = not (source_path and os.path.exists(source_path))
        use_path = source_path if not is_offline else placeholder_path

        added = media_storage.AddItemListToMediaPool([use_path])
        if not added:
            print(f"[ERROR] Could not import: {label}")
            continue

        media_pool.AppendToTimeline([{
            "mediaPoolItem": added[0],
            "startFrame": 0 if is_offline else source_in,
            "endFrame": (0 if is_offline else source_in) + duration - 1,
            "trackIndex": 1,
            "recordFrame": record_frame
        }])

        if is_offline:
            stats["offline"] += 1
            mark_clip_as_offline(timeline, record_frame, label, original, source_in, duration)
        else:
            stats["online"] += 1

    # --- Marker Placement ---
    for m in markers:
        frame = m.get("frame", 0) - offset
        label = m.get("label", "Marker")
        color = m.get("color", "Red")
        if frame >= 0:
            timeline.AddMarker(frame, color, label, "", 1)
            stats["markers"] += 1

    # --- Summary ---
    print("\n=== ✅ BUILD COMPLETE ===")
    print(f"Timeline: {timeline_name}")
    print(f"🟩 Clips Online: {stats['online']}")
    print(f"🟥 Clips Offline: {stats['offline']}")
    print(f"🔖 Markers Added: {stats['markers']}")
    print("==========================")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n--- ❌ SCRIPT FAILED ---")
        print(f"Exception: {e}")
        print("=========================")
