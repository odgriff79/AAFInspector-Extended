#!/usr/bin/env python3
"""
simpleFCPXML.py

A Tkinter GUI that:
  - Parses an AAF-style CSV report.
  - Generates a structurally correct FCPXML with robust sanitization to prevent database errors.
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


def sanitize_string(text: str) -> str:
    """Removes null characters, non-printing ASCII, and non-ASCII characters."""
    if not isinstance(text, str):
        return ""
    # FIX: Explicitly remove null characters which cause SQLite errors
    text = text.replace('\x00', '')
    # Then remove other non-ASCII characters
    return text.encode('ascii', 'ignore').decode('utf-8')


def tc_to_frames(tc: str) -> int:
    """Convert HH:MM:SS:FF to total frames at current fps."""
    if not tc or tc.strip().upper() == 'N/A' or ':' not in tc:
        return 0
    parts = tc.split(':')
    while len(parts) < 4:
        parts.append('0')
    h, m, s, f = map(int, parts)
    return (h*3600 + m*60 + s) * int(fps) + f

def get_filename_for_check(event):
    """
    Safely gets, sanitizes, and strips a filename from the event,
    falling back from 'Source File Name' to 'Clip Name'.
    """
    source_name = sanitize_string(event.get('Source File Name', '')).strip()
    if source_name:
        return source_name
    
    clip_name = sanitize_string(event.get('Clip Name', '')).strip()
    return clip_name


def parse_timeline_summary(lines):
    global timeline_name, fps, tc_format, timeline_start_frames, timeline_length_frames
    for ln in lines:
        if ln.startswith("Timeline Name,"):
            timeline_name = sanitize_string(ln.split(',',1)[1].strip())
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
    path = filedialog.askopenfilename(filetypes=[('CSV files','*.csv')], title='Select CSV')
    if not path:
        return
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    try:
        idx = next(i for i, ln in enumerate(lines) if ln.strip() == '')
        parse_timeline_summary(lines[:idx])
        events = list(csv.DictReader(io.StringIO(''.join(lines[idx+1:]))))
        messagebox.showinfo('CSV Loaded', f'Timeline: {timeline_name}\nEvents: {len(events)}')
    except StopIteration:
        messagebox.showerror("CSV Format Error", "Could not find a blank line separating the summary from event data.")


def create_fcpxml():
    if not events:
        messagebox.showwarning('No CSV','Load CSV first.')
        return
    
    events_to_process = [e for e in events if e.get('Effect Name') != 'N/A']
    events_to_process.sort(key=lambda e: tc_to_frames(e.get('Timeline Start TC', '00:00:00:00')))

    save_path = filedialog.asksaveasfilename(defaultextension='.fcpxml', filetypes=[('FCPXML','*.fcpxml')], title='Save FCPXML')
    if not save_path:
        return

    with open(save_path, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE fcpxml>\n')
        f.write('<fcpxml version="1.13">\n')
        f.write('    <resources>\n')
        
        format_defs = {
            "r0": f'        <format height="1080" id="r0" frameDuration="{1}/{int(fps)}s" name="FFVideoFormat1080p25" width="1920"/>\n',
            "r2": '        <format height="2160" id="r2" name="FFVideoFormatRateUndefined" width="3840"/>\n'
        }
        for f_id in sorted(format_defs.keys()):
            f.write(format_defs[f_id])

        highest_format_num = 0
        for f_id in format_defs.keys():
            highest_format_num = max(highest_format_num, int(re.sub(r'\D', '', f_id)))
        asset_id_start_index = highest_format_num + 1

        asset_map = {} 
        still_image_extensions = ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.dpx', '.psd', '.bmp')
        
        for i, event in enumerate(events_to_process):
            asset_id = f"r{i + asset_id_start_index}"
            asset_map[i] = asset_id
            
            filename_to_check = get_filename_for_check(event)
            is_still = filename_to_check.lower().endswith(still_image_extensions)
            
            format_id = "r2" if is_still else "r0"
            start_str = "0/1s" if is_still else f"{tc_to_frames(event.get('Source Clip start time code', '00:00:00:00'))}/{int(fps)}s"
            duration_str = "0/1s" if is_still else f"{int(fps * 3600)}/{int(fps)}s"
            
            src_path = event.get('Source File Path', '')
            clip_name = sanitize_string(event.get('Clip Name', 'Untitled Asset')).strip()
            normalized_path = src_path.replace('\\', '/')
            if normalized_path.startswith('//'):
                normalized_path = '/' + normalized_path.lstrip('/')

            f.write(f'        <asset id="{asset_id}" name="{clip_name}" start="{start_str}" duration="{duration_str}" hasVideo="1" format="{format_id}">\n')
            f.write(f'            <media-rep kind="original-media" src="file://localhost{normalized_path}"/>\n')
            f.write('        </asset>\n')

        f.write('    </resources>\n')
        f.write('    <library>\n')
        f.write(f'        <event name="{timeline_name}">\n')
        f.write(f'            <project name="{timeline_name}">\n')
        f.write(f'                <sequence tcFormat="{tc_format}" tcStart="{timeline_start_frames}/{int(fps)}s" format="r0" duration="{timeline_length_frames}/{int(fps)}s">\n')
        f.write('                    <spine>\n')

        last_clip_end_frames = timeline_start_frames
        
        for i, event in enumerate(events_to_process):
            if not event.get('Source File Path'):
                continue

            event_start_frames = tc_to_frames(event.get('Timeline Start TC', '00:00:00:00'))
            
            gap_duration_frames = event_start_frames - last_clip_end_frames
            if gap_duration_frames > 0:
                f.write(f'                        <gap start="{last_clip_end_frames}/{int(fps)}s" offset="{last_clip_end_frames}/{int(fps)}s" duration="{gap_duration_frames}/{int(fps)}s" name="Gap"/>\n')

            clip_duration_frames = int(event.get('Event Length', 0))
            clip_start_in_source_frames = int(event.get('StartTime (frames)', 0))
            clip_name = sanitize_string(event.get('Clip Name', 'Untitled')).strip()
            asset_ref_id = asset_map[i]
            
            reel_name_source = max(event.get('DiskLabel', ''), event.get('TapeID', ''), event.get('Source File Name', ''), key=len)
            reel_name = sanitize_string(reel_name_source).strip()

            filename_to_check = get_filename_for_check(event)
            is_still = filename_to_check.lower().endswith(still_image_extensions)
            
            clip_offset = event_start_frames
            
            if is_still:
                f.write(f'                        <video start="{clip_start_in_source_frames}/{int(fps)}s" ref="{asset_ref_id}" offset="{clip_offset}/{int(fps)}s" duration="{clip_duration_frames}/{int(fps)}s" name="{clip_name}" enabled="1">\n')
                f.write('                            <adjust-transform position="0 0" scale="1 1" anchor="0 0"/>\n')
                f.write('                        </video>\n')
            else:
                f.write(f'                        <asset-clip start="{clip_start_in_source_frames}/{int(fps)}s" tcFormat="{tc_format}" ref="{asset_ref_id}" offset="{clip_offset}/{int(fps)}s" duration="{clip_duration_frames}/{int(fps)}s" format="r0" name="{clip_name}" enabled="1">\n')
                f.write('                            <adjust-transform position="0 0" scale="1 1" anchor="0 0"/>\n')
                f.write('                            <metadata>\n')
                f.write(f'                                <md value="{reel_name}" key="com.apple.proapps.studio.reel"/>\n')
                f.write('                            </metadata>\n')
                f.write('                        </asset-clip>\n')

            last_clip_end_frames = event_start_frames + clip_duration_frames

        f.write('                    </spine>\n')
        f.write('                </sequence>\n')
        f.write('            </project>\n')
        f.write('        </event>\n')
        f.write('    </library>\n')
        f.write('</fcpxml>\n')

    messagebox.showinfo('Done', f'FCPXML for all events written to:\n{save_path}')

if __name__ == '__main__':
    root = tk.Tk()
    root.title('simpleFCPXML Creator')
    tk.Button(root, text='Load CSV', command=load_csv, width=25, height=2).pack(pady=10)
    tk.Button(root, text='Create XML', command=create_fcpxml, width=25, height=2).pack(pady=10)
    root.mainloop()