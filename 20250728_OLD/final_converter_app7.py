#!/usr/bin/env python3
import sys, csv, re
from xml.etree import ElementTree as ET
from xml.dom import minidom
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QFileDialog, QTextEdit, QLabel
)

# Helpers

def clean_text(input_data):
    if input_data is None:
        return ""
    return str(input_data).replace('\x00', '')

def tc_to_frames(tc_str, fps):
    try:
        h, m, s, fr = map(int, tc_str.split(':'))
        return ((h * 3600 + m * 60 + s) * fps) + fr
    except Exception:
        return 0

# Build XMEML

def build_xmeml(header_text, events):
    # Parse header
    nm_match = re.search(r'^Timeline Name\s*,\s*(.+)', header_text, re.M)
    seq_name = nm_match.group(1).strip() if nm_match else "Untitled Sequence"
    fps_match = re.search(r'^Timeline Edit Rate\s*,\s*(\d+\.?\d*)', header_text, re.M)
    fps = int(float(fps_match.group(1))) if fps_match else 25
    tc_match = re.search(r'^Timeline Start\s*,\s*(\d{2}:\d{2}:\d{2}:\d{2})', header_text, re.M)
    seq_tc = tc_match.group(1) if tc_match else "00:00:00:00"
    seq_start = tc_to_frames(seq_tc, fps)
    dur_match = re.search(r'\((\d+) frames\)', header_text, re.M)
    seq_duration = int(dur_match.group(1)) if dur_match else 0

    x = ET.Element('xmeml', version="5")
    s = ET.SubElement(x, 'sequence')
    ET.SubElement(s, 'name').text = f"{clean_text(seq_name)}"
    ET.SubElement(s, 'duration').text = str(seq_duration)
    rate = ET.SubElement(s, 'rate')
    ET.SubElement(rate, 'timebase').text = str(fps)
    ET.SubElement(rate, 'ntsc').text = 'FALSE'
    ET.SubElement(s, 'in').text = '-1'
    ET.SubElement(s, 'out').text = '-1'
    tc = ET.SubElement(s, 'timecode')
    ET.SubElement(tc, 'string').text = seq_tc
    ET.SubElement(tc, 'frame').text = str(seq_start)
    ET.SubElement(tc, 'displayformat').text = 'NDF'
    tc_rate = ET.SubElement(tc, 'rate')
    ET.SubElement(tc_rate, 'timebase').text = str(fps)
    ET.SubElement(tc_rate, 'ntsc').text = 'FALSE'
    media = ET.SubElement(s, 'media')
    video = ET.SubElement(media, 'video')
    track = ET.SubElement(video, 'track')

    cid = 0
    for ev in events:
        name = clean_text(ev.get('Source File Name', ''))
        if not name or name.lower() in ('filler', 'slug'):
            continue
        try:
            evt_tc = ev.get('Timeline Start TC', '00:00:00:00')
            start_frame = tc_to_frames(evt_tc, fps) - seq_start
            ln = int(ev.get('Event Length', 0))
            in_f = int(ev.get('Source Clip offset (frames)', 0))
        except:
            continue
        cid += 1
        item_index = cid - 1
        ci = ET.SubElement(track, 'clipitem', id=f"{name} {item_index}")
        ET.SubElement(ci, 'name').text = name
        ET.SubElement(ci, 'duration').text = str(ln)
        r2 = ET.SubElement(ci, 'rate')
        ET.SubElement(r2, 'timebase').text = str(fps)
        ET.SubElement(r2, 'ntsc').text = 'FALSE'
        ET.SubElement(ci, 'start').text = str(start_frame)
        ET.SubElement(ci, 'end').text = str(start_frame + ln)
        ET.SubElement(ci, 'enabled').text = 'TRUE'
        ET.SubElement(ci, 'in').text = str(in_f)
        ET.SubElement(ci, 'out').text = str(in_f + ln)

        # Build file element with correct order and structure
        fe = ET.SubElement(ci, 'file', id=f"{name} {item_index} 2")
        # duration
        ET.SubElement(fe, 'duration').text = str(ln)
        # rate
        fr = ET.SubElement(fe, 'rate')
        ET.SubElement(fr, 'timebase').text = str(fps)
        ET.SubElement(fr, 'ntsc').text = 'FALSE'
        # name uses source file name
        ET.SubElement(fe, 'name').text = name
        # pathurl
        path = clean_text(ev.get('Source File Path', '')).replace('\\', '/')
        ET.SubElement(fe, 'pathurl').text = 'file://' + path
        # timecode with reel
        tcf = ET.SubElement(fe, 'timecode')
        ET.SubElement(tcf, 'string').text = '00:00:00:00'
        ET.SubElement(tcf, 'displayformat').text = 'NDF'
        tcr = ET.SubElement(tcf, 'rate')
        ET.SubElement(tcr, 'timebase').text = str(fps)
        ET.SubElement(tcr, 'ntsc').text = 'FALSE'
        reel = ET.SubElement(tcf, 'reel')
        dl = clean_text(ev.get('DiskLabel', ''))
        tp = clean_text(ev.get('TapeID', ''))
        reel_name = name
        if dl and len(dl) > len(tp):
            reel_name = dl
        elif tp:
            reel_name = tp
        ET.SubElement(reel, 'name').text = reel_name
        # media block
        media_el = ET.SubElement(fe, 'media')
        video_el = ET.SubElement(media_el, 'video')
        ET.SubElement(video_el, 'duration').text = str(ln)
        sc = ET.SubElement(video_el, 'samplecharacteristics')
        ET.SubElement(sc, 'width').text = '1920'
        ET.SubElement(sc, 'height').text = '1080'

        ET.SubElement(ci, 'compositemode').text = 'normal'
        ET.SubElement(ci, 'mediaSource').text = name
        ET.SubElement(ci, 'comments')

    # Tail identical to previous version
    ET.SubElement(track, 'enabled').text = 'TRUE'
    ET.SubElement(track, 'locked').text = 'FALSE'
    fmt = ET.SubElement(video, 'format')
    sc = ET.SubElement(fmt, 'samplecharacteristics')
    ET.SubElement(sc, 'width').text = '1920'
    ET.SubElement(sc, 'height').text = '1080'
    ET.SubElement(sc, 'pixelaspectratio').text = 'square'
    r_f = ET.SubElement(sc, 'rate')
    ET.SubElement(r_f, 'timebase').text = str(fps)
    ET.SubElement(r_f, 'ntsc').text = 'FALSE'
    codec = ET.SubElement(sc, 'codec')
    app = ET.SubElement(codec, 'appspecificdata')
    ET.SubElement(app, 'appname').text = 'Final Cut Pro'
    ET.SubElement(app, 'appmanufacturer').text = 'Apple Inc.'
    data = ET.SubElement(app, 'data')
    ET.SubElement(data, 'qtcodec')
    audio = ET.SubElement(media, 'audio')
    atr = ET.SubElement(audio, 'track')
    ET.SubElement(atr, 'enabled').text = 'TRUE'
    ET.SubElement(atr, 'locked').text = 'FALSE'

    # prepend XML declaration + DOCTYPE
    rough = ET.tostring(x, 'utf-8')
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    header = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
    return header + pretty

class ConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Super Resolve Converter")
        self.csv_path = self.out_path = self.header_text = ""
        self.events = []
        self.init_ui()

    def init_ui(self):
        w = QWidget(); self.setCentralWidget(w)
        v = QVBoxLayout(w)
        h1 = QHBoxLayout(); self.csv_le = QLineEdit(); self.csv_le.setPlaceholderText("Select enriched CSV…")
        b1 = QPushButton("Load CSV"); b1.clicked.connect(self.load_csv)
        h1.addWidget(self.csv_le); h1.addWidget(b1); v.addLayout(h1)
        h2 = QHBoxLayout(); self.xml_le = QLineEdit(); self.xml_le.setPlaceholderText("Select output XML…")
        b2 = QPushButton("Set Output"); b2.clicked.connect(self.set_output)
        h2.addWidget(self.xml_le); h2.addWidget(b2); v.addLayout(h2)
        self.gen_btn = QPushButton("Generate XMEML"); self.gen_btn.clicked.connect(self.generate_xml)
        self.gen_btn.setEnabled(False); v.addWidget(self.gen_btn)
        v.addWidget(QLabel("Log:")); self.log = QTextEdit(); self.log.setReadOnly(True); v.addWidget(self.log)

    def append_log(self, msg):
        self.log.append(msg)

    def load_csv(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV Files (*.csv)")
        if not fn: return
        self.csv_path = fn; self.csv_le.setText(fn)
        try:
            with open(fn, encoding='utf-8', errors='replace') as f: txt = f.read()
            head, body = (txt.split('Event,', 1) + ["", ""])[:2]
            self.header_text = head
            lines = ['Event,' + body.splitlines()[0]] + body.splitlines()[1:]
            flds = ['Event', 'Event Name', 'Clip Name', 'Source File Name', 'Source File Path',
                   'DiskLabel', 'TapeID', 'SourceMobID', 'TrackID', 'Source Clip EditRate',
                   'Timeline Start TC', 'Source Clip start time code', 'Source Clip offset',
                   'StartTime', 'End Time', 'Event Length', 'Source Clip start (frames)',
                   'Source Clip offset (frames)', 'StartTime (frames)', 'Effect Name', 'Keyframe Details']
            rdr = csv.DictReader(lines, fieldnames=flds)
            self.events = [r for r in rdr if r.get('Event') != 'Event']
            self.append_log(f"Loaded {len(self.events)} events.")
            self.check_ready()
        except Exception as e:
            self.append_log(f"Failed to load CSV: {e}")

    def set_output(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save XML", "", "XML Files (*.xml)")
        if not fn: return
        self.out_path = fn; self.xml_le.setText(fn); self.append_log(f"Output set: {fn}"); self.check_ready()

    def check_ready(self):
        self.gen_btn.setEnabled(bool(self.csv_path and self.out_path and self.events))

    def generate_xml(self):
        if not self.events:
            self.append_log("No events to process")
            return
        try:
            xm = build_xmeml(self.header_text, self.events)
            with open(self.out_path, 'w', encoding='utf-8') as f:
                f.write(xm)
            self.append_log(f"SUCCESS: XML written to {self.out_path}")
        except Exception as e:
            self.append_log(f"Error generating XML: {e}")
            import traceback; self.append_log(traceback.format_exc())

if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = ConverterApp()
    win.show()
    sys.exit(app.exec())
