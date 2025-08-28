#!/usr/bin/env python3
"""
simpleFCPXML.py

A Tkinter GUI that:
  1. Prompts for a CSV containing:
       - Timeline Summary lines (Timeline Name, Timeline Edit Rate, Timeline Start, Timeline Length)
       - A blank line, then Event data with headers.
  2. Parses and normalizes timeline and event data.
  3. Prompts where to save a new FCPXML file.
  4. Writes a valid FCPXML for the first three events with corrected timing and gaps.

"""
import tkinter as tk
from tkinter import filedialog, messagebox
import csv, io, re

# Globals
timeline_name = ""
fps = 25
tc_format = "NDF"
timeline_start_frames = 0
timeline_length_frames = 0
events = []


def tc_to_frames(tc: str) -> int:
    """Convert HH:MM:SS:FF to total frames at current fps."""
    parts = tc.split(':')
    while len(parts) < 4:
        parts.append('0')
    h, m, s, f = map(int, parts)
    return (h*3600 + m*60 + s) * int(fps) + f


def parse_timeline_summary(lines):
    global timeline_name, fps, tc_format, timeline_start_frames, timeline_length_frames
    for ln in lines:
        if ln.startswith("Timeline Name,"):
            timeline_name = ln.split(',',1)[1].strip()
        elif ln.startswith("Timeline Edit Rate,"):
            parts = ln.split(',',1)[1].strip().split(' ')
            fps = float(parts[0]); tc_format = parts[1].strip('()')
        elif ln.startswith("Timeline Start,"):
            tc = ln.split(',',1)[1].strip()
            timeline_start_frames = tc_to_frames(tc)
        elif ln.startswith("Timeline Length,"):
            m = re.search(r"\((\d+)\s+frames", ln)
            if m:
                timeline_length_frames = int(m.group(1))


def load_csv():
    global events
    path = filedialog.askopenfilename(
        filetypes=[('CSV files','*.csv')],
        title='Select CSV'
    )
    if not path:
        return

    with open(path, encoding='utf-8') as f:
        lines = f.readlines()

    try:
        idx = next(i for i, ln in enumerate(lines) if ln.strip() == '')
        parse_timeline_summary(lines[:idx])
        events = list(csv.DictReader(io.StringIO(''.join(lines[idx+1:]))))
    except StopIteration:
        messagebox.showerror("CSV Format Error", "Could not find a blank line separating the timeline summary from the event data.")
        return

    messagebox.showinfo('CSV Loaded',
        f'Timeline: {timeline_name}\n'
        f'Edit Rate: {fps} ({tc_format})\n'
        f'Start Frames: {timeline_start_frames}\n'
        f'Length Frames: {timeline_length_frames}\n'
        f'Events: {len(events)}'
    )


def create_fcpxml():
    if not events:
        messagebox.showwarning('No CSV','Load CSV first.')
        return
    
    events_to_process = events[:3] # Process the first three events
    if len(events) < 3:
        messagebox.showwarning("Not Enough Events", "The CSV contains fewer than three events. Processing all available events.")
        events_to_process = events

    save_path = filedialog.asksaveasfilename(
        defaultextension='.fcpxml',
        filetypes=[('FCPXML','*.fcpxml')],
        title='Save FCPXML'
    )
    if not save_path:
        return

    # --- Resource Management ---
    # Create a unique list of assets to be included in the <resources> block
    unique_assets = {}
    asset_id_counter = 1
    for event in events_to_process:
        src_path = event.get('Source File Path', '')
        if src_path not in unique_assets:
            unique_assets[src_path] = f"r{asset_id_counter}"
            asset_id_counter += 1

    with open(save_path, 'w', encoding='utf-8') as f:
        # Header + resources
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE fcpxml>\n')
        f.write('<fcpxml version="1.13">\n')
        f.write('    <resources>\n')
        f.write(f'        <format id="r_main" name="FFVideoFormat1080p25" frameDuration="{1}/{int(fps)}s" width="1920" height="1080"/>\n')

        # Write each unique asset to the resources block
        for src_path, asset_id in unique_assets.items():
            # Find the first event that uses this asset to get its details
            asset_event = next((e for e in events_to_process if e.get('Source File Path') == src_path), None)
            if asset_event:
                asset_start_tc = asset_event.get('Source Clip start time code', '00:00:00:00')
                asset_start_frames = tc_to_frames(asset_start_tc)
                asset_duration_frames = int(fps * 3600)  # Hard-coded asset duration (1 hour)
                clip_name = asset_event.get('Clip Name', 'Untitled Asset')
                f.write(f'        <asset id="{asset_id}" name="{clip_name}" start="{asset_start_frames}/{int(fps)}s" duration="{asset_duration_frames}/{int(fps)}s" hasVideo="1" format="r_main">\n')
                f.write(f'            <media-rep kind="original-media" src="file://localhost{src_path}"/>\n')
                f.write('        </asset>\n')
        
        f.write('    </resources>\n')
        f.write('    <library>\n')
        f.write(f'        <event name="{timeline_name}">\n')
        f.write(f'            <project name="{timeline_name}">\n')
        f.write(f'                <sequence format="r_main" tcStart="{timeline_start_frames}/{int(fps)}s" duration="{timeline_length_frames}/{int(fps)}s" tcFormat="{tc_format}">\n')
        f.write('                    <spine>\n')

        # --- Spine Generation ---
        last_event_end_frames = timeline_start_frames
        
        for event in events_to_process:
            event_start_tc = event.get('Timeline Start TC', '00:00:00:00')
            event_start_frames = tc_to_frames(event_start_tc)
            
            # Calculate the gap before the current clip
            gap_duration_frames = event_start_frames - last_event_end_frames
            if gap_duration_frames > 0:
                gap_start_frames = last_event_end_frames
                f.write(f'                        <gap name="Gap" offset="{gap_start_frames}/{int(fps)}s" start="{gap_start_frames}/{int(fps)}s" duration="{gap_duration_frames}/{int(fps)}s"/>\n')

            # Get clip-specific details
            clip_duration_frames = int(event.get('Event Length', 0))
            clip_start_in_source_frames = int(event.get('StartTime (frames)', 0))
            clip_name = event.get('Clip Name', 'Untitled')
            src_path = event.get('Source File Path', '')
            asset_ref_id = unique_assets.get(src_path)

            # Write the asset-clip
            if asset_ref_id:
                f.write(f'                        <asset-clip name="{clip_name}" ref="{asset_ref_id}" offset="{event_start_frames}/{int(fps)}s" duration="{clip_duration_frames}/{int(fps)}s" start="{clip_start_in_source_frames}/{int(fps)}s" tcFormat="{tc_format}" enabled="1">\n')
                f.write('                            <adjust-transform position="0 0" scale="1 1" anchor="0 0"/>\n')
                f.write('                        </asset-clip>\n')

            # Update the end time for the next gap calculation
            last_event_end_frames = event_start_frames + clip_duration_frames

        # Close tags
        f.write('                    </spine>\n')
        f.write('                </sequence>\n')
        f.write('            </project>\n')
        f.write('        </event>\n')
        f.write('    </library>\n')
        f.write('</fcpxml>\n')

    messagebox.showinfo('Done', f'FCPXML for the first three events written to:\n{save_path}')

if __name__ == '__main__':
    root = tk.Tk()
    root.title('simpleFCPXML Creator')
    tk.Button(root, text='Load CSV', command=load_csv, width=25, height=2).pack(pady=10)
    tk.Button(root, text='Create XML', command=create_fcpxml, width=25, height=2).pack(pady=10)
    root.mainloop()