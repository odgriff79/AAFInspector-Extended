#!/usr/bin/env python3
"""
simpleFCPXML.py

A Tkinter GUI that:
  1. Prompts for a CSV containing:
       - Timeline Summary lines (Timeline Name, Timeline Edit Rate, Timeline Start, Timeline Length)
       - A blank line, then Event data with headers.
  2. Parses and normalizes timeline and event data.
  3. Prompts where to save a new FCPXML file.
  4. Writes a valid FCPXML for the first event with corrected timing.

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
    # Ensure we have 4 components, padding with 0 if missing
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

    # Find the first blank line to separate summary from events
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

    # Use the first event for now as requested
    fe = events[0]
    
    # --- Corrected Logic ---

    # 1. Get asset start from the beginning of the source file
    asset_start_tc = fe.get('Source Clip start time code', '00:00:00:00')
    asset_start_frames = tc_to_frames(asset_start_tc)
    
    # 2. Get the event's absolute start on the timeline
    event_start_tc = fe.get('Timeline Start TC', '00:00:00:00')
    event_start_frames = tc_to_frames(event_start_tc)

    # 3. Get the clip's in-point from the source media
    # As requested, using 'StartTime (frames)' which corresponds to the 'StartTime' TC
    clip_start_in_source_frames = int(fe.get('StartTime (frames)', 0))

    # 4. Get the clip's duration on the timeline
    clip_duration_frames = int(fe.get('Event Length', 0))

    # 5. Calculate the duration of the gap before this clip
    gap_duration_frames = event_start_frames - timeline_start_frames

    # --- Other Data ---
    clip_name = fe.get('Clip Name', 'Untitled')
    src_path = fe.get('Source File Path', '')
    asset_duration_frames = int(fps * 3600)  # Hard-coded asset duration (1 hour)

    save_path = filedialog.asksaveasfilename(
        defaultextension='.fcpxml',
        filetypes=[('FCPXML','*.fcpxml')],
        title='Save FCPXML'
    )
    if not save_path:
        return

    with open(save_path, 'w', encoding='utf-8') as f:
        # Header + resources
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE fcpxml>\n')
        f.write('<fcpxml version="1.13">\n')
        f.write('    <resources>\n')
        f.write(f'        <format id="r0" name="FFVideoFormat1080p25" frameDuration="{1}/{int(fps)}s" width="1920" height="1080"/>\n')
        # Asset in resources
        f.write(f'        <asset id="r1" name="{clip_name}" start="{asset_start_frames}/{int(fps)}s" duration="{asset_duration_frames}/{int(fps)}s" hasVideo="1" format="r0">\n')
        f.write(f'            <media-rep kind="original-media" src="file://localhost{src_path}"/>\n')
        f.write('        </asset>\n')
        f.write('    </resources>\n')
        f.write('    <library>\n')

        # Event / project / sequence / spine
        f.write(f'        <event name="{timeline_name}">\n')
        f.write(f'            <project name="{timeline_name}">\n')
        # Sequence tcStart is the absolute start of the timeline in frames
        f.write(f'                <sequence format="r0" tcStart="{timeline_start_frames}/{int(fps)}s" duration="{timeline_length_frames}/{int(fps)}s" tcFormat="{tc_format}">\n')
        f.write('                    <spine>\n')

        # Gap before the clip
        # offset and start are the timeline's start, duration is the calculated gap
        f.write(f'                        <gap name="Gap" offset="{timeline_start_frames}/{int(fps)}s" start="{timeline_start_frames}/{int(fps)}s" duration="{gap_duration_frames}/{int(fps)}s"/>\n')

        # Asset-clip with corrected timing
        f.write(f'                        <asset-clip name="{clip_name}" ref="r1" offset="{event_start_frames}/{int(fps)}s" duration="{clip_duration_frames}/{int(fps)}s" start="{clip_start_in_source_frames}/{int(fps)}s" tcFormat="{tc_format}" enabled="1">\n')
        f.write('                            <adjust-transform position="0 0" scale="1 1" anchor="0 0"/>\n')
        f.write('                        </asset-clip>\n')

        # Close tags
        f.write('                    </spine>\n')
        f.write('                </sequence>\n')
        f.write('            </project>\n')
        f.write('        </event>\n')
        f.write('    </library>\n')
        f.write('</fcpxml>\n')

    messagebox.showinfo('Done', f'FCPXML written to:\n{save_path}')

if __name__ == '__main__':
    root = tk.Tk()
    root.title('simpleFCPXML Creator')
    tk.Button(root, text='Load CSV', command=load_csv, width=25, height=2).pack(pady=10)
    tk.Button(root, text='Create XML', command=create_fcpxml, width=25, height=2).pack(pady=10)
    root.mainloop()