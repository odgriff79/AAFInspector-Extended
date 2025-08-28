#!/usr/bin/env python3
import tkinter as tk
from tkinter import filedialog, ttk, messagebox, scrolledtext
import csv, re, json, ast, os
import xml.etree.ElementTree as ET
import xml.parsers.expat
from xml.dom import minidom
from pathlib import Path

def tc_to_frames(tc, fps=25):
    try:
        h, m, s, f = map(int, tc.replace(';',':').split(':'))
        return ((h*3600 + m*60 + s) * fps) + f
    except:
        return 0

def frames_to_fcpxml_time(frames, rate):
    return f"{int(frames)}/{int(round(rate))}s"

def sanitize(text):
    if not isinstance(text, str):
        return ''
    clean = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    clean = re.sub(r"[<>&\"'\\\\]", '', clean)
    clean = re.sub(r'[^\x20-\x7E]', '', clean)
    return clean.strip()

def generate_full_fcpxml(summary, events, output_path, logger=print):
    fps_str = summary.get('Timeline Edit Rate') or summary.get('Edit Rate') or '25.0'
    m = re.match(r'([0-9\.]+)', fps_str)
    fps = float(m.group(1)) if m else 25.0
    start_tc = summary.get('Timeline Start TC') or summary.get('Start TC') or '00:00:00:00'
    seq_start = tc_to_frames(start_tc, fps)
    seq_name = sanitize(summary.get('Timeline Name') or 'Sequence')
    logger(f"Timeline start {start_tc} → frame {seq_start}, FPS={fps}")

    fcpxml = ET.Element('fcpxml', version='1.9')
    res = ET.SubElement(fcpxml, 'resources')
    ET.SubElement(res, 'format',
                  id='r0', name=f'FFVideoFormat{int(fps)}p',
                  width='1920', height='1080',
                  frameDuration=f'1/{int(fps)}s')

    asset_map = {}
    for ev in events:
        src = ev.get('Source File Path') or ev.get('SourcePath') or ''
        if src and src not in asset_map:
            aid = f"r{len(asset_map)+1}"
            asset_map[src] = aid
            asset = ET.SubElement(res, 'asset',
                                  id=aid,
                                  name=sanitize(Path(src).name),
                                  hasVideo='1', format='r0')
            try:
                p = Path(src)
                uri = p.as_uri().replace(' ', '%20').replace('&', '%26')
            except ValueError:
                uri = 'file://' + src.replace('\\', '/').replace(' ', '%20').replace('&', '%26')
            ET.SubElement(asset, 'media-rep', kind='original-media', src=uri)
            logger(f"  Asset {aid}: {uri}")

    lib = ET.SubElement(fcpxml, 'library')
    evt = ET.SubElement(ET.SubElement(lib, 'event', name=seq_name), 'project', name=seq_name)
    total_dur = sum(int(ev.get('Event Length') or ev.get('EventLength') or 0) for ev in events)
    seq = ET.SubElement(evt, 'sequence',
                        format='r0',
                        duration=frames_to_fcpxml_time(total_dur, fps),
                        tcStart=frames_to_fcpxml_time(seq_start, fps),
                        tcFormat='NDF')
    spine = ET.SubElement(seq, 'spine')

    current = seq_start
    for idx, ev in enumerate(events, start=1):
        in_tc = ev.get('Timeline Start TC') or ev.get('TimelineStartTC') or start_tc
        abs_frame = tc_to_frames(in_tc, fps)
        rel = abs_frame - seq_start
        dur = int(ev.get('Event Length') or ev.get('EventLength') or 0)
        logger(f"Event {idx}: abs={abs_frame}, rel={rel}, dur={dur}")

        if rel > (current - seq_start):
            gap_len = rel - (current - seq_start)
            ET.SubElement(spine, 'gap',
                          duration=frames_to_fcpxml_time(gap_len, fps),
                          name=sanitize(ev.get('Clip Name') or ''))
            logger(f"  Gap {gap_len} frames named '{ev.get('Clip Name')}'")

        src = ev.get('Source File Path') or ev.get('SourcePath') or ''
        ref = asset_map.get(src)
        if ref:
            clip = ET.SubElement(spine, 'asset-clip',
                                 ref=ref,
                                 name=sanitize(ev.get('Clip Name') or ''),
                                 start=frames_to_fcpxml_time(int(ev.get('Source Clip start (frames)') or 0), fps),
                                 duration=frames_to_fcpxml_time(dur, fps))
            logger(f"  Clip ref={ref}")
        else:
            clip = ET.SubElement(spine, 'gap',
                                 duration=frames_to_fcpxml_time(dur, fps),
                                 name=sanitize(ev.get('Clip Name') or ''))
            logger(f"  Fallback gap named '{ev.get('Clip Name')}'")

        raw_kf = ev.get('FCPXML_Converted_Keyframes') or ev.get('FCPXMLConvertedKeyframes') or ''
        kflist = []
        if raw_kf:
            try:
                kflist = json.loads(raw_kf)
            except json.JSONDecodeError:
                try:
                    kflist = ast.literal_eval(raw_kf)
                    logger("  Parsed keyframes via literal_eval")
                except Exception as e:
                    logger(f"  Keyframe parse error: {e}")
        if kflist:
            px = ET.SubElement(clip, 'param', name='ScaleX')
            py = ET.SubElement(clip, 'param', name='ScaleY')
            for k in kflist:
                t = k.get('FCPXML_Time')
                sx = k.get('FCPXML_Scale_X')
                sy = k.get('FCPXML_Scale_Y')
                ET.SubElement(px, 'keyframe', time=t, value=str(sx), interpolation='linear')
                ET.SubElement(py, 'keyframe', time=t, value=str(sy), interpolation='linear')
                logger(f"    KF@{t}: ScaleX={sx}, ScaleY={sy}")

        current = abs_frame + dur

    xml_raw = ET.tostring(fcpxml, encoding='unicode')
    dbg_file = Path.home() / 'fcpxml_debug_raw.xml'
    dbg_file.write_text(xml_raw, encoding='utf-8')
    logger(f"Raw XML dumped to {dbg_file}")

    try:
        pretty = minidom.parseString(xml_raw).toprettyxml(indent='  ')
    except xml.parsers.expat.ExpatError as err:
        ln, col = err.lineno, err.offset
        logger(f"Parse error at {ln}:{col}: {err}")
        line = xml_raw.splitlines()[ln-1]
        ctx = line[max(0, col-40):col+40]
        logger(f"Context: {ctx}")
        raise
    Path(output_path).write_text(pretty, encoding='utf-8')
    logger(f"Written FCPXML to {output_path}")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('X_CON CSV → FCPXML')
        self.geometry('700x500')
        self.events = []
        self.summary = {}

        top = ttk.Frame(self)
        top.pack(fill='x', padx=10, pady=5)
        ttk.Button(top, text='Load CSV', command=self.load_csv).pack(side='left')
        self.exp = ttk.Button(top, text='Export XML', command=self.export, state='disabled')
        self.exp.pack(side='left', padx=5)
        self.lbl = ttk.Label(top, text='No CSV loaded')
        self.lbl.pack(side='left', padx=10)

        self.log = scrolledtext.ScrolledText(self, font=('Courier New',10))
        self.log.pack(fill='both', expand=True, padx=10, pady=5)

    def logmsg(self, msg):
        self.log.insert('end', msg + '\n')
        self.log.see('end')

    def load_csv(self):
        file_path = filedialog.askopenfilename(filetypes=[('CSV','*.csv')])
        if not file_path:
            return
        self.lbl.config(text=file_path)
        with open(file_path, newline='', encoding='utf-8') as f:
            self.events = list(csv.DictReader(f))
        if self.events:
            self.summary = self.events[0]
            self.logmsg(f"Loaded {len(self.events)} events. Keys: {list(self.summary.keys())}")
            self.exp.config(state='normal')
        else:
            self.logmsg('No rows found in CSV.')


    def export(self):
        if not self.events:
            messagebox.showwarning('No Data','Load a CSV first')
            return
        out = filedialog.asksaveasfilename(defaultextension='.fcpxml', filetypes=[('FCPXML','*.fcpxml')])
        if out:
            try:
                generate_full_fcpxml(self.summary, self.events, out, logger=self.logmsg)
                messagebox.showinfo('Success', f'FCPXML saved to:\n{out}')
            except Exception as e:
                self.logmsg(f"Generation error: {e}")
                messagebox.showerror('Error', str(e))

if __name__=='__main__':
    App().mainloop()