#!/usr/bin/env python3
"""
simpleFCPXML.py

A Tkinter GUI that:
  1. Prompts for a CSV containing:
       - Timeline Summary lines (Timeline Name, Timeline Edit Rate, Timeline Start, Timeline Length)
       - A blank line, then Event data with headers
  2. Extracts:
       - timeline_name
       - fps and tc_format
       - timeline_start_frames
       - timeline_length_frames
  3. Prompts where to save a new FCPXML file.
  4. Writes only the format definitions (r0, r2, r4, plus our FPS library) into <resources>,
     then injects <event>, <project>, and <sequence> driven from the CSV.
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import csv
import io
import re

# Globals
timeline_name = ""
fps = 25
tc_format = "NDF"
timeline_start_frames = 0
timeline_length_frames = 0
events = []

# HEADER with format definitions and opening library
HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.13">
    <resources>
        <format height="1080" id="r0"  frameDuration="1/25s"      name="FFVideoFormat1080p25"       width="1920"/>
        <format height="3120" id="r2"                             name="FFVideoFormatRateUndefined"  width="4208"/>
        <format height="2160" id="r4"  frameDuration="1001/30000s" name="FFVideoFormat3840x2160p2997" width="3840"/>

        <!-- 1080p common rates -->
        <format height="1080" id="r6"  frameDuration="1001/24000s" name="FFVideoFormat1080p2398"      width="1920"/>
        <format height="1080" id="r8"  frameDuration="1/24s"        name="FFVideoFormat1080p24"        width="1920"/>
        <format height="1080" id="r10" frameDuration="1/30s"        name="FFVideoFormat1080p30"        width="1920"/>
        <format height="1080" id="r12" frameDuration="1/50s"        name="FFVideoFormat1080p50"        width="1920"/>
        <format height="1080" id="r14" frameDuration="1001/60000s"  name="FFVideoFormat1080p5994"     width="1920"/>
        <format height="1080" id="r16" frameDuration="1/60s"        name="FFVideoFormat1080p60"        width="1920"/>

        <!-- 4K UHD common rates -->
        <format height="2160" id="r18" frameDuration="1001/24000s" name="FFVideoFormat3840x2160p2398" width="3840"/>
        <format height="2160" id="r20" frameDuration="1/24s"       name="FFVideoFormat3840x2160p24"    width="3840"/>
        <format height="2160" id="r22" frameDuration="1/30s"       name="FFVideoFormat3840x2160p30"    width="3840"/>
        <format height="2160" id="r24" frameDuration="1/50s"       name="FFVideoFormat3840x2160p50"    width="3840"/>
        <format height="2160" id="r26" frameDuration="1001/60000s" name="FFVideoFormat3840x2160p5994"  width="3840"/>
        <format height="2160" id="r28" frameDuration="1/60s"       name="FFVideoFormat3840x2160p60"    width="3840"/>

        <!-- (Optional) high-speed / slow-mo -->
        <format height="1080" id="r30" frameDuration="1/48s"       name="FFVideoFormat1080p48"        width="1920"/>
        <format height="1080" id="r32" frameDuration="1/120s"      name="FFVideoFormat1080p120"       width="1920"/>
        <format height="1080" id="r34" frameDuration="1/240s"      name="FFVideoFormat1080p240"       width="1920"/>
    </resources>
    <library>
"""

def parse_timeline_summary(lines):
    global timeline_name, fps, tc_format, timeline_start_frames, timeline_length_frames
    for ln in lines:
        if ln.startswith("Timeline Name,"):
            timeline_name = ln.split(",",1)[1].strip()
        elif ln.startswith("Timeline Edit Rate,"):
            parts = ln.split(",",1)[1].strip().split(" ")
            fps = float(parts[0])
            tc_format = parts[1].strip("()")
        elif ln.startswith("Timeline Start,"):
            h, m, s, f = map(int, ln.split(",",1)[1].strip().split(":"))
            timeline_start_frames = ((h*3600 + m*60 + s) * int(fps)) + f
        elif ln.startswith("Timeline Length,"):
            m_frames = re.search(r"\((\d+)\s+frames", ln)
            if m_frames:
                timeline_length_frames = int(m_frames.group(1))
            else:
                h, m, s, f = map(int, ln.split(",",1)[1].strip().split(":"))
                timeline_length_frames = ((h*3600 + m*60 + s) * int(fps)) + f

def load_csv():
    path = filedialog.askopenfilename(
        filetypes=[("CSV files", "*.csv")],
        title="Select CSV"
    )
    if not path:
        return
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    summary_end = next(i for i, ln in enumerate(lines) if ln.strip() == "")
    parse_timeline_summary(lines[:summary_end])
    data = "".join(lines[summary_end+1:])
    reader = csv.DictReader(io.StringIO(data))
    events.clear()
    events.extend(reader)
    messagebox.showinfo("CSV Loaded", f"Timeline: {timeline_name}\nEvents: {len(events)}")

def create_fcpxml():
    if not timeline_name:
        messagebox.showwarning("No CSV", "Load CSV first.")
        return
    path = filedialog.asksaveasfilename(
        defaultextension=".fcpxml",
        filetypes=[("FCPXML","*.fcpxml")],
        title="Save FCPXML"
    )
    if not path:
        return
    start_attr = f"{timeline_start_frames}/{int(fps)}s"
    duration_attr = f"{timeline_length_frames}/{int(fps)}s"
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER)
        f.write(f'        <event name="{timeline_name}">\n')
        f.write(f'            <project name="{timeline_name}">\n')
        f.write(
            f'                <sequence tcFormat="{tc_format}" '
            f'tcStart="{start_attr}" format="r0" '
            f'duration="{duration_attr}">\n'
        )
        # TODO: write <spine> here using events list...
        f.write("                </sequence>\n")
        f.write("            </project>\n")
        f.write("        </event>\n")
        f.write("    </library>\n")
        f.write("</fcpxml>\n")
    messagebox.showinfo("Success", f"FCPXML created at:\n{path}")

def main():
    root = tk.Tk()
    root.title("FCPXML Creator")
    tk.Button(root, text="Load CSV", command=load_csv, width=25, height=2).pack(pady=(20,10))
    tk.Button(root, text="Create FCPXML", command=create_fcpxml, width=25, height=2).pack(pady=(0,20))
    root.mainloop()

if __name__ == "__main__":
    main()
