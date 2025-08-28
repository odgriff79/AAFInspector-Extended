#!/usr/bin/env python3
"""
simpleFCPXML.py

A Tkinter GUI that:
  1. Prompts for a CSV containing:
       - Timeline Summary lines (Timeline Name, Timeline Edit Rate, Timeline Start, Timeline Length)
       - A blank line, then Event data with headers including "Timeline Start TC"
  2. Parses and normalizes:
       - timeline_name
       - fps and tc_format
       - timeline_start_frames
       - timeline_length_frames
       - events list
  3. Prompts where to save a new FCPXML file.
  4. Writes:
       - The <resources> block with all <format> definitions and the first-event <asset> inside it
       - Closes </resources>, opens <library>
       - Writes <event>, <project>, <sequence> (with tcStart in frames), and <spine> without leading gaps
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
timeline_length_frames = 0
events = []

# Full format definitions library
FORMAT_BLOCK = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.13">
    <resources>
        <format height="1080" id="r0" frameDuration="1/25s" name="FFVideoFormat1080p25" width="1920"/>
        <format height="3120" id="r2" frameDuration="1/25s" name="FFVideoFormatRateUndefined" width="4208"/>
        <format height="2160" id="r4" frameDuration="1001/30000s" name="FFVideoFormat3840x2160p2997" width="3840"/>
        <format height="1080" id="r6" frameDuration="1001/24000s" name="FFVideoFormat1080p2398" width="1920"/>
        <format height="1080" id="r8" frameDuration="1/24s" name="FFVideoFormat1080p24" width="1920"/>
        <format height="1080" id="r10" frameDuration="1/30s" name="FFVideoFormat1080p30" width="1920"/>
        <format height="1080" id="r12" frameDuration="1/50s" name="FFVideoFormat1080p50" width="1920"/>
        <format height="1080" id="r14" frameDuration="1001/60000s" name="FFVideoFormat1080p5994" width="1920"/>
        <format height="1080" id="r16" frameDuration="1/60s" name="FFVideoFormat1080p60" width="1920"/>
        <format height="2160" id="r18" frameDuration="1001/24000s" name="FFVideoFormat3840x2160p2398" width="3840"/>
        <format height="2160" id="r20" frameDuration="1/24s" name="FFVideoFormat3840x2160p24" width="3840"/>
        <format height="2160" id="r22" frameDuration="1/30s" name="FFVideoFormat3840x2160p30" width="3840"/>
        <format height="2160" id="r24" frameDuration="1/50s" name="FFVideoFormat3840x2160p50" width="3840"/>
        <format height="2160" id="r26" frameDuration="1001/60000s" name="FFVideoFormat3840x2160p5994" width="3840"/>
        <format height="2160" id="r28" frameDuration="1/60s" name="FFVideoFormat3840x2160p60" width="3840"/>
        <format height="1080" id="r30" frameDuration="1/48s" name="FFVideoFormat1080p48" width="1920"/>
        <format height="1080" id="r32" frameDuration="1/120s" name="FFVideoFormat1080p120" width="1920"/>
        <format height="1080" id="r34" frameDuration="1/240s" name="FFVideoFormat1080p240" width="1920"/>
"""

RESOURCES_CLOSE_LIBRARY = """
    </resources>
    <library>
"""

def tc_to_frames(tc):
    h, m, s, f = map(int, tc.split(':'))
    return (h * 3600 + m * 60 + s) * int(fps) + f

def parse_timeline_summary(lines):
    global timeline_name, fps, tc_format, timeline_start_frames, timeline_length_frames
    for ln in lines:
        if ln.startswith("Timeline Name,"):
            timeline_name = ln.split(',', 1)[1].strip()
        elif ln.startswith("Timeline Edit Rate,"):
            parts = ln.split(',', 1)[1].strip().split(' ')
            fps = float(parts[0])
            tc_format = parts[1].strip('()')
        elif ln.startswith("Timeline Start,"):
            tc = ln.split(',', 1)[1].strip()
            timeline_start_frames = tc_to_frames(tc)
        elif ln.startswith("Timeline Length,"):
            m = re.search(r"\((\d+)\s+frames", ln)
            if m:
                timeline_length_frames = int(m.group(1))

def load_csv():
    global events
    path = filedialog.askopenfilename(filetypes=[('CSV files', '*.csv')], title='Select CSV')
    if not path:
        return
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    idx = next(i for i, ln in enumerate(lines) if ln.strip() == '')
    parse_timeline_summary(lines[:idx])
    events = list(csv.DictReader(io.StringIO(''.join(lines[idx + 1:]))))
    messagebox.showinfo('CSV Loaded',
        f'Timeline: {timeline_name}\n'
        f'Edit Rate: {fps} ({tc_format})\n'
        f'Start Frames: {timeline_start_frames}\n'
        f'Length Frames: {timeline_length_frames}\n'
        f'Events: {len(events)}')

def create_fcpxml():
    if not events:
        messagebox.showwarning('No CSV', 'Load CSV first.')
        return
    fe = events[0]
    clip_tc = fe['Timeline Start TC']
    clip_frames = tc_to_frames(clip_tc)
    dur_frames = int(fe['Event Length'])
    media_start = int(fe['Source Clip start (frames)'])
    clip_name = fe['Clip Name']
    src_path = fe['Source File Path']

    save_path = filedialog.asksaveasfilename(defaultextension='.fcpxml', filetypes=[('FCPXML', '*.fcpxml')])
    if not save_path:
        return
    with open(save_path, 'w', encoding='utf-8') as f:
        f.write(FORMAT_BLOCK)
        f.write(f'        <asset start="{clip_frames}/{int(fps)}s" id="r1" duration="{dur_frames}/{int(fps)}s" format="r0" name="{clip_name}" hasVideo="1">\n')
        f.write(f'            <media-rep src="file://localhost{src_path}" kind="original-media"/>\n')
        f.write('        </asset>\n')
        f.write(RESOURCES_CLOSE_LIBRARY)
        f.write(f'        <event name="{timeline_name}">\n')
        f.write(f'            <project name="{timeline_name}">\n')
        f.write(f'                <sequence tcFormat="{tc_format}" tcStart="{timeline_start_frames}/{int(fps)}s" format="r0" duration="{timeline_length_frames}/{int(fps)}s">\n')
        f.write('                    <spine>\n')
        f.write(f'                        <asset-clip start="{media_start}/{int(fps)}s" tcFormat="{tc_format}" ref="r1" offset="{clip_frames - timeline_start_frames}/{int(fps)}s" duration="{dur_frames}/{int(fps)}s" format="r0" name="{clip_name}" enabled="1">\n')
        f.write('                            <adjust-transform scale="1 1" anchor="0 0" position="0 0"/>\n')
        f.write('                        </asset-clip>\n')
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
