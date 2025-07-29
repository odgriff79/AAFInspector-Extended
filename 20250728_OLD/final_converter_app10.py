#!/usr/bin/env python3
import sys, csv, re
from copy import deepcopy
from xml.etree import ElementTree as ET
from xml.dom import minidom
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QFileDialog, QTextEdit, QLabel
)

# --- Helpers --------------------------------------------------------

def clean_text(input_data):
    return str(input_data).replace('\x00', '') if input_data is not None else ''


def tc_to_frames(tc_str, fps):
    try:
        h, m, s, fr = map(int, tc_str.split(':'))
        return ((h * 3600 + m * 60 + s) * fps) + fr
    except:
        return 0


def frames_to_tc(frames, fps):
    h = frames // (3600 * fps)
    frames %= (3600 * fps)
    m = frames // (60 * fps)
    frames %= (60 * fps)
    s = frames // fps
    f = frames % fps
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

# --- Default Filter Templates (Disabled) ---------------------------
FILTERS_XML = '''
<root>
  <filter>
    <enabled>FALSE</enabled>
    <start>0</start>
    <end>0</end>
    <effect>
      <name>Basic Motion</name>
      <effectid>basic</effectid>
      <effecttype>motion</effecttype>
      <mediatype>video</mediatype>
      <effectcategory>motion</effectcategory>
    </effect>
  </filter>
  <filter>
    <enabled>FALSE</enabled>
    <start>0</start>
    <end>0</end>
    <effect>
      <name>Crop</name>
      <effectid>crop</effectid>
      <effecttype>motion</effecttype>
      <mediatype>video</mediatype>
      <effectcategory>motion</effectcategory>
    </effect>
  </filter>
  <filter>
    <enabled>FALSE</enabled>
    <start>0</start>
    <end>0</end>
    <effect>
      <name>Opacity</name>
      <effectid>opacity</effectid>
      <effecttype>motion</effecttype>
      <mediatype>video</mediatype>
      <effectcategory>motion</effectcategory>
    </effect>
  </filter>
</root>
'''
FILTER_TEMPLATES = ET.fromstring(FILTERS_XML).findall('filter')

# --- XMEML Builder --------------------------------------------------

def build_xmeml(header_text, events):
    # Parse header
    nm = re.search(r'^Timeline Name\s*,\s*(.+)', header_text, re.M)
    seq_name = nm.group(1).strip() if nm else 'Untitled Sequence'
    fm = re.search(r'^Timeline Edit Rate\s*,\s*(\d+\.?\d*)', header_text, re.M)
    fps = int(float(fm.group(1))) if fm else 25
    tm = re.search(r'^Timeline Start\s*,\s*(\d{2}:\d{2}:\d{2}:\d{2})', header_text, re.M)
    seq_tc = tm.group(1) if tm else '00:00:00:00'
    seq_start = tc_to_frames(seq_tc, fps)
    du = re.search(r'\((\d+) frames\)', header_text, re.M)
    seq_dur = int(du.group(1)) if du else 0

    # Root structure
    x = ET.Element('xmeml', version='5')
    s = ET.SubElement(x, 'sequence')
    ET.SubElement(s, 'name').text = clean_text(seq_name)
    ET.SubElement(s, 'duration').text = str(seq_dur)
    rate = ET.SubElement(s, 'rate')
    ET.SubElement(rate, 'timebase').text = str(fps)
    ET.SubElement(rate, 'ntsc').text = 'FALSE'
    ET.SubElement(s, 'in').text = '-1'
    ET.SubElement(s, 'out').text = '-1'
    tc = ET.SubElement(s, 'timecode')
    ET.SubElement(tc, 'string').text = seq_tc
    ET.SubElement(tc, 'frame').text = str(seq_start)
    ET.SubElement(tc, 'displayformat').text = 'NDF'
    tr = ET.SubElement(tc, 'rate')
    ET.SubElement(tr, 'timebase').text = str(fps)
    ET.SubElement(tr, 'ntsc').text = 'FALSE'

    media = ET.SubElement(s, 'media')
    video = ET.SubElement(media, 'video')
    track = ET.SubElement(video, 'track')

    cid = 0
    for ev in events:
        name = clean_text(ev.get('Source File Name',''))
        if not name or name.lower() in ('filler','slug'):
            continue
        start_f = tc_to_frames(ev.get('Timeline Start TC','00:00:00:00'), fps) - seq_start
        ln = int(ev.get('Event Length','0'))
        inf = int(ev.get('Source Clip offset (frames)','0'))

        cid += 1
        idx = cid - 1
        ci_id = f'{name} {idx}'
        ci = ET.SubElement(track, 'clipitem', id=ci_id)
        ET.SubElement(ci, 'name').text = name
        ET.SubElement(ci, 'duration').text = str(ln)
        r2 = ET.SubElement(ci, 'rate')
        ET.SubElement(r2, 'timebase').text = str(fps)
        ET.SubElement(r2, 'ntsc').text = 'FALSE'
        ET.SubElement(ci, 'start').text = str(start_f)
        ET.SubElement(ci, 'end').text = str(start_f + ln)
        ET.SubElement(ci, 'enabled').text = 'TRUE'
        ET.SubElement(ci, 'in').text = str(inf)
        ET.SubElement(ci, 'out').text = str(inf + ln)

        # File element
        fe = ET.SubElement(ci, 'file', id=f'{name} 2')
        ET.SubElement(fe, 'duration').text = str(ln)
        fr2 = ET.SubElement(fe, 'rate')
        ET.SubElement(fr2, 'timebase').text = str(fps)
        ET.SubElement(fr2, 'ntsc').text = 'FALSE'
        ET.SubElement(fe, 'name').text = name
        path = clean_text(ev.get('Source File Path','')).replace('\\','/')
        ET.SubElement(fe, 'pathurl').text = 'file://' + path

        # Timecode
        raw_tc = ev.get('Source Clip start time code','00:00:00:00')
        bf = tc_to_frames(raw_tc, fps)
        ff = bf + int(ev.get('Source Clip offset (frames)','0'))
        tcf = ET.SubElement(fe, 'timecode')
        ET.SubElement(tcf, 'string').text = frames_to_tc(ff, fps)
        ET.SubElement(tcf, 'displayformat').text = 'NDF'
        tr2 = ET.SubElement(tcf, 'rate')
        ET.SubElement(tr2, 'timebase').text = str(fps)
        ET.SubElement(tr2, 'ntsc').text = 'FALSE'
        reel = ET.SubElement(tcf, 'reel')
        ET.SubElement(reel, 'name').text = (clean_text(ev.get('DiskLabel','')) or clean_text(ev.get('TapeID','')) or name)

        # Media stub
        med = ET.SubElement(fe, 'media')
        vid = ET.SubElement(med, 'video')
        ET.SubElement(vid, 'duration').text = str(ln)
        sc = ET.SubElement(vid, 'samplecharacteristics')
        ET.SubElement(sc, 'width').text = '1920'
        ET.SubElement(sc, 'height').text = '1080'

                # Inject compositemode
        ET.SubElement(ci, 'compositemode').text = 'normal'
        # Inject filters with proper enabled, start and end values
        for filt in FILTER_TEMPLATES:
            f = deepcopy(filt)
            # enable filter and set timeline range
            en = f.find('enabled')
            if en is not None: en.text = 'TRUE'
            st = f.find('start')
            if st is not None: st.text = '0'
            ed = f.find('end')
            if ed is not None: ed.text = str(ln)
            ci.append(f)
        # Finally append mediaSource and comments
        ET.SubElement(ci, 'mediaSource').text = name
        ET.SubElement(ci, 'comments')

    # Close track, add format and audio
    ET.SubElement(track, 'enabled').text = 'TRUE'
    ET.SubElement(track, 'locked').text = 'FALSE'
    fmt = ET.SubElement(video, 'format')
    sc = ET.SubElement(fmt, 'samplecharacteristics')
    ET.SubElement(sc, 'width').text = '1920'
    ET.SubElement(sc, 'height').text = '1080'
    ET.SubElement(sc, 'pixelaspectratio').text = 'square'
    rf = ET.SubElement(sc, 'rate')
    ET.SubElement(rf, 'timebase').text = str(fps)
    ET.SubElement(rf, 'ntsc').text = 'FALSE'
    codec = ET.SubElement(sc, 'codec')
    app = ET.SubElement(codec, 'appspecificdata')
    ET.SubElement(app, 'appname').text = 'Final Cut Pro'
    ET.SubElement(app, 'appmanufacturer').text = 'Apple Inc.'
    dat = ET.SubElement(app, 'data')
    ET.SubElement(dat, 'qtcodec')

    # Prettify and strip blank lines
    rough = ET.tostring(x, 'utf-8')
    raw = minidom.parseString(rough).toprettyxml(indent='  ')
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if lines and lines[0].startswith('<?xml'):
        lines = lines[1:]
    body = '\n'.join(lines)
    header = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
    return header + body

# --- GUI Application ------------------------------------------------

class ConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Super Resolve Converter")
        self.csv_path = ''
        self.out_path = ''
        self.header_text = ''
        self.events = []
        self.init_ui()

    def init_ui(self):
        w = QWidget()
        self.setCentralWidget(w)
        layout = QVBoxLayout(w)

        # CSV loader
        h1 = QHBoxLayout()
        self.csv_le = QLineEdit()
        self.csv_le.setPlaceholderText("Select enriched CSV…")
        b1 = QPushButton("Load CSV")
        b1.clicked.connect(self.load_csv)
        h1.addWidget(self.csv_le)
        h1.addWidget(b1)
        layout.addLayout(h1)

        # Output selector
        h2 = QHBoxLayout()
        self.xml_le = QLineEdit()
        self.xml_le.setPlaceholderText("Select output XML…")
        b2 = QPushButton("Set Output")
        b2.clicked.connect(self.set_output)
        h2.addWidget(self.xml_le)
        h2.addWidget(b2)
        layout.addLayout(h2)

        # Generate button
        self.gen_btn = QPushButton("Generate XMEML")
        self.gen_btn.clicked.connect(self.generate_xml)
        self.gen_btn.setEnabled(False)
        layout.addWidget(self.gen_btn)

        # Log
        layout.addWidget(QLabel("Log:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

    def append_log(self, msg):
        self.log.append(msg)

    def load_csv(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV Files (*.csv)")
        if not fn:
            return
        self.csv_path = fn
        self.csv_le.setText(fn)
        try:
            text = open(fn, encoding='utf-8', errors='replace').read()
            header, rest = text.split('Event,', 1)
            self.header_text = header
            lines = ['Event,' + rest.splitlines()[0]] + rest.splitlines()[1:]
            flds = [
                'Event','Event Name','Clip Name','Source File Name','Source File Path',
                'DiskLabel','TapeID','SourceMobID','TrackID','Source Clip EditRate',
                'Timeline Start TC','Source Clip start time code','Source Clip offset',
                'StartTime','End Time','Event Length','Source Clip start (frames)',
                'Source Clip offset (frames)','StartTime (frames)','Effect Name','Keyframe Details'
            ]
            rdr = csv.DictReader(lines, fieldnames=flds)
            self.events = [r for r in rdr if r.get('Event') != 'Event']
            self.append_log(f"Loaded {len(self.events)} events.")
            self.check_ready()
        except Exception as e:
            self.append_log(f"Failed to load CSV: {e}")

    def set_output(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save XML", "", "XML Files (*.xml)")
        if not fn:
            return
        self.out_path = fn
        self.xml_le.setText(fn)
        self.append_log(f"Output set: {fn}")
        self.check_ready()

    def check_ready(self):
        ready = bool(self.csv_path and self.out_path and self.events)
        self.gen_btn.setEnabled(ready)

    def generate_xml(self):
        if not self.events:
            self.append_log("No events to process.")
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
