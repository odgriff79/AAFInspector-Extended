#!/usr/bin/env python3
"""
simpleFCPXML.py

A Tkinter GUI that:
  1. Prompts for a CSV containing:
       - Timeline Summary lines (Timeline Name, Timeline Edit Rate, Timeline Start, Timeline Length)
       - A blank line, then Event data with headers including "Source Clip start time code", "Source Clip start (frames)", and "Timeline Start TC"
  2. Parses and normalizes:
       - timeline_name
       - fps and tc_format
       - timeline_start_frames and timeline_start_seconds
       - timeline_length_frames
       - events list
  3. Prompts where to save a new FCPXML file.
  4. Writes:
       - Fixed <format> definitions for 25fps, undefined-stills, and 4K 29.97fps
       - The first-event <asset> inside <resources>, pulling start from "Source Clip start time code" and hardcoding duration to 1 hour
       - Closes </resources>, opens <library>
       - Writes <event>, <project>, <sequence> (with tcStart from timeline start), and <spine> with:
           • <gap> using start & offset from timeline_start_seconds
           • <asset-clip> with offset and duration from CSV
       - Closes all tags

"""
import tkinter as tk
from tkinter import filedialog, messagebox
import csv, io, re

# Globals
timeline_name = ""
fps = 25
tc_format = "NDF"
timeline_start_frames = 0
# seconds component for gap start/offset
timeline_start_seconds = 0
timeline_length_frames = 0
events = []


def tc_to_frames(tc: str) -> int:
    """Convert HH:MM:SS:FF to total frames at current fps."""
    h, m, s, f = map(int, tc.split(':'))
    return (h*3600 + m*60 + s) * int(fps) + f


def parse_timeline_summary(lines):
    global timeline_name, fps, tc_format, timeline_start_frames, timeline_start_seconds, timeline_length_frames
    for ln in lines:
        if ln.startswith("Timeline Name,"):
            timeline_name = ln.split(',',1)[1].strip()
        elif ln.startswith("Timeline Edit Rate,"):
            parts = ln.split(',',1)[1].strip().split(' ')
            fps = float(parts[0]); tc_format = parts[1].strip('()')
        elif ln.startswith("Timeline Start,"):
            tc = ln.split(',',1)[1].strip()
            timeline_start_frames = tc_to_frames(tc)
            timeline_start_seconds = timeline_start_frames // int(fps)
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

    idx = next(i for i, ln in enumerate(lines) if ln.strip() == '')
    parse_timeline_summary(lines[:idx])
    events = list(csv.DictReader(io.StringIO(''.join(lines[idx+1:]))))

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

    fe = events[0]
    # Asset start from Source Clip start time code
    src_tc = fe['Source Clip start time code']
    asset_start_frames = tc_to_frames(src_tc)
    # Hard-coded asset duration (1 hour)
    asset_duration_frames = int(fps * 3600)  # Convert to integer frames
    # Clip in-point from Source Clip start (frames)
    clip_start_frames = int(fe['Source Clip start (frames)'])
    # Compute offset: Timeline Start TC minus Timeline Start
    event_tc = fe['Timeline Start TC']
    event_frames = tc_to_frames(event_tc)
    offset_frames = event_frames - timeline_start_frames
    # Clip duration in the sequence (Event Length)
    dur_frames = int(fe['Event Length'])
    clip_name = fe['Clip Name']
    src_path  = fe['Source File Path']

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
        f.write('        <format height="1080" id="r0" frameDuration="1/25s" name="FFVideoFormat1080p25" width="1920"/>\n')
        f.write('        <format height="3120" id="r2" frameDuration="1/25s" name="FFVideoFormatRateUndefined" width="4208"/>\n')
        f.write('        <format height="2160" id="r4" frameDuration="1001/30000s" name="FFVideoFormat3840x2160p2997" width="3840"/>\n')
        # First-asset in resources
        f.write(f'        <asset start=\"{asset_start_frames}/{int(fps)}s\" id=\"r1\" duration=\"{asset_duration_frames}/{int(fps)}s\" format="r0" name="{clip_name}" hasVideo="1">\n')
        f.write(f'            <media-rep src="file://localhost{src_path}" kind="original-media"/>\n')
        f.write('        </asset>\n')
        f.write('    </resources>\n')
        f.write('    <library>\n')

        # Event / project / sequence / spine
        f.write(f'        <event name="{timeline_name}">\n')
        f.write(f'            <project name="{timeline_name}">\n')
        f.write(f'                <sequence tcFormat="{tc_format}" tcStart="{timeline_start_seconds}/1s" format="r0" duration="{timeline_length_frames}/{int(fps)}s">\n')
        f.write('                    <spine>\n')

        # Gap using actual sequence start in seconds
        f.write(f'                        <gap start="{timeline_start_seconds}/1s" offset="{timeline_start_seconds}/1s" duration="{offset_frames}/{int(fps)}s" name="Gap"/>\n')

        # Asset-clip with offset
        f.write(f'                        <asset-clip start="{clip_start_frames}/{int(fps)}s" tcFormat="{tc_format}" ref="r1" offset="{offset_frames}/{int(fps)}s" duration="{dur_frames}/{int(fps)}s" format="r0" name="{clip_name}" enabled="1">\n')
        f.write('                            <adjust-transform scale="1 1" anchor="0 0" position="0 0"/>\n')
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
