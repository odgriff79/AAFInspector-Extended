#!/usr/bin/env python3
"""
simpleFCPXML.py

A Tkinter GUI that:
  - Parses an AAF-style CSV report with keyframe data.
  - Generates a structurally correct FCPXML that precisely matches the DaVinci Resolve schema.
"""
# --- AVID to FCPXML/RESOLVE CONVERSION SCHEMA ---
#
# 1. POSITION (X, Y)
#    - Resolve_X = Avid_X / 1920
#    - Resolve_Y = Avid_Y / 1080
#    - For DVE-type effects, Y-axis is inverted: Resolve_Y = (Avid_Y / 1080) * -1
#
# 2. SCALE (Zoom)
#    - AFX Scale: Resolve_Zoom = Avid_Scale / 100.0
#    - Pan & Zoom 'Zoom Factor': Resolve_Zoom = Avid_Zoom_Factor
#    - DVE Scale: Resolve_Zoom_X = Avid_Scale / 100.0, Resolve_Zoom_Y = (Avid_Scale / 100.0) * -1
#
# 3. CROP & OPACITY
#    - Direct 1-to-1 mapping.
#
# 4. ROTATION
#    - Resolve_Rotation = Avid_Rotation * -1
#
# 5. TIMING
#    - tcStart, offset, start, and keyframe time attributes use absolute seconds: int(frames / fps)
#    - duration attributes use fractional seconds: frames / fps_rate
#
# --- End of Schema ---

import tkinter as tk
from tkinter import filedialog, messagebox
import csv, io, re
from urllib.parse import quote

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
    text = text.replace('\x00', '')
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

def parse_keyframe_details(kf_string, clip_start_frames):
    """Parses the Keyframe Details string into a structured dictionary."""
    if not kf_string or "No animated parameters" in kf_string:
        if '-> Value:' not in kf_string:
            return {}

    parsed_params = {}
    current_param = None

    for line in kf_string.strip().split('\n'):
        param_match = re.search(r'Parameter:\s*(.+?)(?:\s*\(|\s*->)', line)
        if param_match:
            current_param = param_match.group(1).strip()
            if current_param not in parsed_params:
                parsed_params[current_param] = []
        
        kf_match = re.search(r'Keyframe at .*?\((\d+)f\)\s*->\s*Value:\s*(.*)', line)
        if kf_match and current_param:
            frame, value_str = kf_match.groups()
            frame = int(frame)
            value_str = value_str.strip()
            try:
                value = float(value_str.split('/')[0]) / float(value_str.split('/')[1]) if '/' in value_str else float(value_str)
                relative_frame = frame - clip_start_frames
                parsed_params[current_param].append({'time': relative_frame, 'value': value})
            except (ValueError, ZeroDivisionError):
                continue
        
        static_kf_match = re.search(r'->\s*Value:\s*(.*)', line)
        if static_kf_match and not kf_match and current_param:
            value_str = static_kf_match.group(1).strip()
            try:
                value = float(value_str.split('/')[0]) / float(value_str.split('/')[1]) if '/' in value_str else float(value_str)
                parsed_params[current_param].append({'time': 0, 'value': value})
            except (ValueError, ZeroDivisionError):
                continue
    
    for param in parsed_params:
        parsed_params[param].sort(key=lambda x: x['time'])
        
    return parsed_params


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
        
        asset_counter = asset_id_start_index
        for i, event in enumerate(events_to_process):
            src_path = sanitize_string(event.get('Source File Path', '')).strip()
            if not src_path:
                continue
            
            asset_id = f"r{asset_counter}"
            asset_map[i] = asset_id
            
            filename_to_check = get_filename_for_check(event)
            is_still = filename_to_check.lower().endswith(still_image_extensions)
            
            format_id = "r2" if is_still else "r0"
            start_str = f"{tc_to_frames(event.get('Source Clip start time code', '00:00:00:00'))}/{int(fps)}s"
            duration_str = f"{int(event.get('Orig Source Clip length', 0))}/{int(fps)}s"
            if is_still: start_str, duration_str = "0/1s", "0/1s"
            
            clip_name = sanitize_string(event.get('Clip Name', 'Untitled Asset')).strip()
            normalized_path = quote(src_path.replace('\\', '/'))
            
            audio_attrs = 'hasAudio="1" audioChannels="1" audioSources="1"' if not is_still else ''
            
            f.write(f'        <asset id="{asset_id}" name="{clip_name}" start="{start_str}" duration="{duration_str}" hasVideo="1" {audio_attrs} format="{format_id}">\n')
            f.write(f'            <media-rep kind="original-media" src="file://localhost{normalized_path}"/>\n')
            f.write('        </asset>\n')
            asset_counter += 1

        f.write('    </resources>\n')
        f.write('    <library>\n')
        f.write(f'        <event name="{timeline_name}">\n')
        f.write(f'            <project name="{timeline_name}">\n')
        f.write(f'                <sequence tcFormat="{tc_format}" tcStart="{int(timeline_start_frames/fps)}/1s" format="r0" duration="{timeline_length_frames}/{int(fps)}s">\n')
        f.write('                    <spine>\n')

        last_clip_end_frames = timeline_start_frames
        
        for i, event in enumerate(events_to_process):
            if i not in asset_map:
                continue

            event_start_frames = tc_to_frames(event.get('Timeline Start TC', '00:00:00:00'))
            
            gap_duration_frames = event_start_frames - last_clip_end_frames
            if gap_duration_frames > 0:
                f.write(f'                        <gap start="{int(last_clip_end_frames/fps)}/1s" offset="{int(last_clip_end_frames/fps)}/1s" duration="{gap_duration_frames}/{int(fps)}s" name="Gap"/>\n')

            clip_duration_frames = int(event.get('Event Length', 0))
            clip_start_in_source_frames = int(event.get('StartTime (frames)', 0))
            clip_name = sanitize_string(event.get('Clip Name', 'Untitled')).strip()
            asset_ref_id = asset_map[i]
            reel_name = sanitize_string(max(event.get('DiskLabel', ''), event.get('TapeID', ''), event.get('Source File Name', ''), key=len)).strip()

            filename_to_check = get_filename_for_check(event)
            is_still = filename_to_check.lower().endswith(still_image_extensions)
            
            clip_offset = event_start_frames
            
            tag = "clip"
            base_attrs = f'offset="{int(clip_offset/fps)}/1s" duration="{clip_duration_frames}/{int(fps)}s" name="{clip_name}" start="{int(clip_start_in_source_frames/fps)}/1s" enabled="1" tcFormat="{tc_format}" format="{ "r2" if is_still else "r0" }"'
            
            f.write(f'                        <{tag} {base_attrs}>\n')

            keyframes = parse_keyframe_details(event.get('Keyframe Details', ''), event_start_frames)
            
            transform_kfs, crop_kfs, opacity_kfs = {}, {}, {}
            if keyframes:
                for param, kfs in keyframes.items():
                    p_upper = param.upper()
                    if 'POS_X' in p_upper or 'X' == p_upper: transform_kfs.setdefault('position_x', []).extend(kfs)
                    elif 'POS_Y' in p_upper or 'Y' == p_upper: transform_kfs.setdefault('position_y', []).extend(kfs)
                    elif 'SCALE_X' in p_upper or 'SCALE_Y' in p_upper or 'ZOOM FACTOR' in p_upper: transform_kfs.setdefault('scale', []).extend(kfs)
                    elif 'ROTATION' in p_upper or 'ROT_Z' in p_upper: transform_kfs.setdefault('rotation', []).extend(kfs)
                    elif 'CROP_LEFT' in p_upper: crop_kfs.setdefault('left', []).extend(kfs)
                    elif 'CROP_RIGHT' in p_upper: crop_kfs.setdefault('right', []).extend(kfs)
                    elif 'CROP_TOP' in p_upper: crop_kfs.setdefault('top', []).extend(kfs)
                    elif 'CROP_BOTTOM' in p_upper: crop_kfs.setdefault('bottom', []).extend(kfs)
                    elif 'OPACITY' in p_upper: opacity_kfs.setdefault('opacity', []).extend(kfs)

            has_transform = any(k in transform_kfs for k in ['position_x', 'position_y', 'scale', 'rotation'])
            
            if has_transform:
                is_dve = 'DVE_' in event.get('Effect Name', '')
                is_pan_zoom = 'PAN & ZOOM' in event.get('Effect Name', '').upper()
                
                pos_x_kfs = transform_kfs.get('position_x', [])
                pos_y_kfs = transform_kfs.get('position_y', [])
                scale_kfs = transform_kfs.get('scale', [])
                rot_kfs = transform_kfs.get('rotation', [])

                first_pos_x = (pos_x_kfs[0]['value']) if pos_x_kfs else 0
                first_pos_y = (pos_y_kfs[0]['value']) if pos_y_kfs else 0
                first_scale_raw = scale_kfs[0]['value'] if scale_kfs else 1.0
                first_scale = first_scale_raw if is_pan_zoom else first_scale_raw / 100.0
                first_rot = (rot_kfs[0]['value'] * -1) if rot_kfs else 0
                
                pos_y_init = first_pos_y * -1 if is_dve else first_pos_y
                scale_y_init = first_scale * -1 if is_dve else first_scale
                
                f.write(f'                            <adjust-transform position="{first_pos_x} {pos_y_init}" scale="{first_scale} {scale_y_init}" rotation="{first_rot}">\n')

                if pos_x_kfs or pos_y_kfs:
                    all_pos_frames = sorted(list(set(k['time'] for k in pos_x_kfs + pos_y_kfs)))
                    f.write(f'                                <param name="position" value="{first_pos_x} {pos_y_init}">\n                                    <keyframeAnimation>\n')
                    for frame in all_pos_frames:
                        x = next((k['value'] for k in pos_x_kfs if k['time'] == frame), first_pos_x)
                        y = next((k['value'] for k in pos_y_kfs if k['time'] == frame), first_pos_y)
                        y_inv = y * -1 if is_dve else y
                        f.write(f'                                        <keyframe time="{int(frame/fps)}/1s" value="{x} {y_inv}" curve="linear"/>\n')
                    f.write('                                    </keyframeAnimation>\n                                </param>\n')

                if scale_kfs:
                    f.write(f'                                <param name="scale" value="{first_scale} {scale_y_init}">\n                                    <keyframeAnimation>\n')
                    for kf in scale_kfs:
                        val = kf['value'] if is_pan_zoom else kf['value'] / 100.0
                        y_val = val * -1 if is_dve else val
                        f.write(f'                                        <keyframe time="{int(kf["time"]/fps)}/1s" value="{val} {y_val}" curve="linear"/>\n')
                    f.write('                                    </keyframeAnimation>\n                                </param>\n')
                
                if rot_kfs:
                    f.write(f'                                <param name="rotation" value="{first_rot}">\n                                    <keyframeAnimation>\n')
                    for kf in rot_kfs:
                        f.write(f'                                        <keyframe time="{int(kf["time"]/fps)}/1s" value="{kf["value"] * -1}" curve="linear"/>\n')
                    f.write('                                    </keyframeAnimation>\n                                </param>\n')
                f.write('                            </adjust-transform>\n')
            
            else:
                 f.write('                            <adjust-transform position="0 0" scale="1 1" anchor="0 0"/>\n')

            if crop_kfs:
                left = crop_kfs.get('left', [{'value':0}])[0]['value']
                right = crop_kfs.get('right', [{'value':0}])[0]['value']
                top = crop_kfs.get('top', [{'value':0}])[0]['value']
                bottom = crop_kfs.get('bottom', [{'value':0}])[0]['value']
                f.write(f'                            <filter-video name="Crop">\n                                <adjust-crop mode="trim">\n                                    <trim-rect left="{left}" top="{top}" right="{right}" bottom="{bottom}">\n')
                for param_name in ['left', 'right', 'top', 'bottom']:
                    kfs_list = sorted(crop_kfs.get(param_name, []), key=lambda k: k['time'])
                    initial_val = kfs_list[0]['value'] if kfs_list else 0
                    f.write(f'                                        <param name="{param_name}" value="{initial_val}">\n')
                    if kfs_list:
                        f.write('                                            <keyframeAnimation>\n')
                        for kf in kfs_list:
                            f.write(f'                                                <keyframe time="{int(kf["time"]/fps)}/1s" value="{kf["value"]}" curve="linear"/>\n')
                        f.write('                                            </keyframeAnimation>\n')
                    f.write('                                        </param>\n')
                f.write('                                    </trim-rect>\n                                </adjust-crop>\n                            </filter-video>\n')
            
            if opacity_kfs:
                op_kfs_list = sorted(opacity_kfs.get('opacity', []), key=lambda k: k['time'])
                initial_opacity = op_kfs_list[0]['value'] if op_kfs_list else 100
                f.write('                            <filter-video name="Opacity">\n')
                f.write(f'                                <param name="opacity" value="{initial_opacity}">\n')
                if op_kfs_list:
                    f.write('                                    <keyframeAnimation>\n')
                    for kf in op_kfs_list:
                        f.write(f'                                        <keyframe time="{int(kf["time"]/fps)}/1s" value="{kf["value"]}" curve="linear"/>\n')
                    f.write('                                    </keyframeAnimation>\n')
                f.write('                                </param>\n')
                f.write('                            </filter-video>\n')

            f.write(f'                            <video ref="{asset_ref_id}" offset="{int(clip_start_in_source_frames/fps)}/1s" duration="{clip_duration_frames}/{int(fps)}s" start="{int(clip_start_in_source_frames/fps)}/1s"/>\n')

            f.write('                            <metadata>\n')
            f.write(f'                                <md value="{reel_name}" key="com.apple.proapps.studio.reel"/>\n')
            f.write('                            </metadata>\n')
            
            f.write(f'                        </{tag}>\n')

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