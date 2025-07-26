#!/usr/bin/env python3
import sys, csv, re
from xml.etree import ElementTree as ET
from xml.dom import minidom
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QFileDialog, QTextEdit, QLabel
)

# ————————————————————————————————
# Helpers
# ————————————————————————————————
def clean_text(input_data):
    """Remove null characters and ensure the output is a string."""
    if input_data is None:
        return ""
    return str(input_data).replace('\x00', '')

def tc_to_frames(tc_str, fps):
    """Convert HH:MM:SS:FF timecode string to an absolute frame count."""
    try:
        h, m, s, fr = map(int, tc_str.split(':'))
        return ((h * 3600 + m * 60 + s) * fps) + fr
    except (ValueError, AttributeError):
        # Return a default value or handle the error if the TC is invalid
        return 0

# —————————————————————————————————————————————————————
# Main XML Building Logic
# —————————————————————————————————————————————————————
def build_xmeml(header_text, events):
    # --- 1. Parse Sequence Header from the first few CSV rows ---
    nm_match = re.search(r'^Timeline Name,(.+)', header_text, re.M)
    nm = nm_match.group(1).strip() if nm_match else "Untitled Sequence"

    fps_match = re.search(r'^Timeline Edit Rate,(\d+\.?\d*)', header_text, re.M)
    fps = int(float(fps_match.group(1))) if fps_match else 25 # Default to 25 if not found
    
    tc_match = re.search(r'^Timeline Start,(\d{2}:\d{2}:\d{2}:\d{2})', header_text, re.M)
    seq_start_tc = tc_match.group(1) if tc_match else "00:00:00:00"
    seq_start_frame = tc_to_frames(seq_start_tc, fps)

    dur_match = re.search(r'\((\d+) frames\)', header_text, re.M)
    seq_duration = int(dur_match.group(1)) if dur_match else 0

    # --- 2. Create XML Root and Sequence Header ---
    x = ET.Element('xmeml', version="5")
    s = ET.SubElement(x, 'sequence')
    ET.SubElement(s, 'name').text = clean_text(nm + ' (Converted)')
    ET.SubElement(s, 'duration').text = str(seq_duration)
    rate = ET.SubElement(s, 'rate')
    ET.SubElement(rate, 'timebase').text = str(fps)
    ET.SubElement(rate, 'ntsc').text = 'FALSE' # Assuming NDF as per our discussion
    ET.SubElement(s, 'in').text = '-1'
    ET.SubElement(s, 'out').text = '-1'
    tc_el = ET.SubElement(s, 'timecode')
    ET.SubElement(tc_el, 'string').text = seq_start_tc
    ET.SubElement(tc_el, 'frame').text = str(seq_start_frame)
    ET.SubElement(tc_el, 'displayformat').text = 'NDF'
    rt = ET.SubElement(tc_el, 'rate')
    ET.SubElement(rt, 'timebase').text = str(fps)
    ET.SubElement(rt, 'ntsc').text = 'FALSE'

    media = ET.SubElement(s, 'media')
    video = ET.SubElement(media, 'video')
    track = ET.SubElement(video, 'track')

    # --- 3. Process Each Event Row ---
    cid = 0
    for ev in events:
        try:
            # Use 'Timeline Start TC' for position, not 'StartTime (frames)'
            evt_tc = ev.get('Timeline Start TC', '00:00:00:00')
            sf = tc_to_frames(evt_tc, fps)
            
            name = clean_text(ev.get('Source File Name', ''))
            ip = int(ev.get('Source Clip offset (frames)', 0))
            ln = int(ev.get('Event Length', 0))
        except (ValueError, TypeError):
            continue # Skip rows with invalid numeric data
            
        if not name or name.lower() in ('filler', 'black', 'slug'):
            continue
            
        cid += 1
        # CORRECT CALCULATION for relative start on timeline
        start_on_timeline = sf - seq_start_frame
        end_on_timeline = start_on_timeline + ln

        ci = ET.SubElement(track, 'clipitem', id=f"clipitem-{cid}")
        ET.SubElement(ci, 'name').text = name
        ET.SubElement(ci, 'duration').text = str(ln)
        r2 = ET.SubElement(ci, 'rate')
        ET.SubElement(r2, 'timebase').text = str(fps)
        ET.SubElement(r2, 'ntsc').text = 'FALSE'
        ET.SubElement(ci, 'start').text = str(start_on_timeline)
        ET.SubElement(ci, 'end').text = str(end_on_timeline)
        ET.SubElement(ci, 'enabled').text = 'TRUE'
        ET.SubElement(ci, 'in').text = str(ip)
        ET.SubElement(ci, 'out').text = str(ip + ln)

        fe = ET.SubElement(ci, 'file', id=f"file-{cid}")
        ET.SubElement(fe, 'name').text = 'Slug'
        
        # 🔽🔽🔽 **UPDATED REEL NAME LOGIC** 🔽🔽🔽
        reel = ET.SubElement(fe, 'reel')
        disk_label = clean_text(ev.get('DiskLabel', ''))
        tape_id = clean_text(ev.get('TapeID', ''))
        
        reel_name_source = name # Default to Source File Name (D)
        if len(disk_label) > len(tape_id):
            reel_name_source = disk_label
        elif len(tape_id) > 0:
            reel_name_source = tape_id
        
        ET.SubElement(reel, 'name').text = reel_name_source
        # 🔼🔼🔼 **END OF UPDATE** 🔼🔼🔼

        clean_path = clean_text(ev.get('Source File Path', '')).replace('\\', '/')
        ET.SubElement(fe, 'pathurl').text = f"file://{clean_path}"
        
        rf = ET.SubElement(fe, 'rate')
        ET.SubElement(rf, 'timebase').text = str(fps)
        ET.SubElement(rf, 'ntsc').text = 'FALSE'
        tcf = ET.SubElement(fe, 'timecode')
        ET.SubElement(tcf, 'string').text = '00:00:00:00'
        ET.SubElement(tcf, 'displayformat').text = 'NDF'
        rt2 = ET.SubElement(tcf, 'rate')
        ET.SubElement(rt2, 'timebase').text = str(fps)
        ET.SubElement(rt2, 'ntsc').text = 'FALSE'
        
        ET.SubElement(ci,'compositemode').text = "normal"
        ET.SubElement(ci, 'mediaSource').text = 'Slug'


        # --- Default Filters with All Parameters ---
        filter_defs = {
            'basic': ('Basic Motion', [
                ('Scale', 'scale', '100', '0', '10000'),
                ('Center', 'center', {'horiz': '0', 'vert': '0'}),
                ('Rotation', 'rotation', '0', '-100000', '100000'),
                ('Anchor Point', 'centerOffset', {'horiz': '0', 'vert': '0'})
            ]),
            'crop': ('Crop', [
                ('left', 'left', '0', '0', '100'),
                ('right', 'right', '0', '0', '100'),
                ('top', 'top', '0', '0', '100'),
                ('bottom', 'bottom', '0', '0', '100')
            ]),
            'opacity': ('Opacity', [('opacity', 'opacity', '100', '0', '100')])
        }

        for eid, (effname, params) in filter_defs.items():
            flt = ET.SubElement(ci, 'filter')
            ET.SubElement(flt, 'enabled').text = 'TRUE'
            ET.SubElement(flt, 'start').text = '0'
            ET.SubElement(flt, 'end').text = str(ln)
            eff = ET.SubElement(flt, 'effect')
            ET.SubElement(eff, 'name').text = effname
            ET.SubElement(eff, 'effectid').text = eid
            ET.SubElement(eff, 'effecttype').text = 'motion'
            ET.SubElement(eff, 'mediatype').text = 'video'
            ET.SubElement(eff, 'effectcategory').text = 'motion'
            
            for p_info in params:
                p = ET.SubElement(eff, 'parameter')
                ET.SubElement(p, 'name').text = p_info[0]
                ET.SubElement(p, 'parameterid').text = p_info[1]
                if isinstance(p_info[2], dict):
                    val_node = ET.SubElement(p, 'value')
                    ET.SubElement(val_node, 'horiz').text = p_info[2]['horiz']
                    ET.SubElement(val_node, 'vert').text = p_info[2]['vert']
                else:
                    ET.SubElement(p, 'value').text = p_info[2]
                    ET.SubElement(p, 'valuemin').text = p_info[3]
                    ET.SubElement(p, 'valuemax').text = p_info[4]

        ET.SubElement(ci, 'comments')

    # --- 4. Append Hard-coded XML Tail ---
    ET.SubElement(track, 'enabled').text = 'TRUE'
    ET.SubElement(track, 'locked').text = 'FALSE'
    fmt = ET.SubElement(video, 'format')
    sc = ET.SubElement(fmt, 'samplecharacteristics')
    ET.SubElement(sc, 'width').text = '1920'
    ET.SubElement(sc, 'height').text = '1080'
    ET.SubElement(sc, 'pixelaspectratio').text = 'square'
    rate_f = ET.SubElement(sc, 'rate')
    ET.SubElement(rate_f, 'timebase').text = str(fps)
    ET.SubElement(rate_f, 'ntsc').text = 'FALSE'
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

    rough = ET.tostring(x, 'utf-8')
    return minidom.parseString(rough).toprettyxml(indent="  ")

# —————————————————————————————————————————————————————
# GUI Application
# —————————————————————————————————————————————————————
class ConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Super Resolve Converter")
        self.setGeometry(100,100,800,600)
        self.csv_path = ''
        self.out_path = ''
        self.header_text = ''
        self.events = []
        self.init_ui()

    def init_ui(self):
        w = QWidget(); self.setCentralWidget(w)
        v = QVBoxLayout(w)

        h1 = QHBoxLayout()
        self.csv_le = QLineEdit(); self.csv_le.setPlaceholderText("Select enriched CSV…")
        b1 = QPushButton("Load CSV"); b1.clicked.connect(self.load_csv)
        h1.addWidget(self.csv_le); h1.addWidget(b1)
        v.addLayout(h1)

        h2 = QHBoxLayout()
        self.xml_le = QLineEdit(); self.xml_le.setPlaceholderText("Select output XML…")
        b2 = QPushButton("Set Output"); b2.clicked.connect(self.set_output)
        h2.addWidget(self.xml_le); h2.addWidget(b2)
        v.addLayout(h2)

        self.gen_btn = QPushButton("Generate XMEML")
        self.gen_btn.clicked.connect(self.generate_xml)
        self.gen_btn.setEnabled(False)
        v.addWidget(self.gen_btn)

        v.addWidget(QLabel("Log:"))
        self.log = QTextEdit(); self.log.setReadOnly(True)
        v.addWidget(self.log)

    def append_log(self,msg):
        self.log.append(msg)

    def load_csv(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV Files (*.csv)")
        if not fn: return
        self.csv_path = fn; self.csv_le.setText(fn)
        try:
            with open(fn, encoding='utf-8', errors='replace') as f:
                txt = f.read()
            
            if 'Event,' in txt:
                head, body = txt.split('Event,', 1)
                self.header_text = head
                lines = ['Event,'+body.splitlines()[0]] + body.splitlines()[1:]
            else:
                self.append_log("Warning: 'Event,' separator not found.")
                self.header_text = ""
                lines = txt.splitlines()

            fieldnames = [
                'Event','Event Name','Clip Name','Source File Name','Source File Path',
                'DiskLabel','TapeID','SourceMobID','TrackID','Source Clip EditRate',
                'Timeline Start TC','Source Clip start time code','Source Clip offset',
                'StartTime','End Time','Event Length','Source Clip start (frames)',
                'Source Clip offset (frames)','StartTime (frames)','Effect Name','Keyframe Details'
            ]
            reader = csv.DictReader(lines, fieldnames=fieldnames)
            
            all_rows = list(reader)
            self.events = [row for row in all_rows if row.get('Event') != 'Event']
            
            self.append_log(f"Loaded {len(self.events)} events.")
            self.check_ready()
        except Exception as e:
            self.append_log(f"Failed to load or parse CSV: {e}")

    def set_output(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save XML", "", "XML Files (*.xml)")
        if not fn: return
        self.out_path = fn; self.xml_le.setText(fn)
        self.append_log(f"Output path set to: {fn}")
        self.check_ready()

    def check_ready(self):
        self.gen_btn.setEnabled(bool(self.csv_path and self.out_path and self.events))

    def generate_xml(self):
        if not self.events:
            self.append_log("ERROR: No events loaded to generate XML.")
            return
        try:
            xm = build_xmeml(self.header_text, self.events)
            with open(self.out_path, 'w', encoding='utf8') as f:
                f.write(xm)
            self.append_log(f"SUCCESS: Wrote XML to {self.out_path}")
        except Exception as e:
            self.append_log(f"ERROR during XML generation: {e}")
            import traceback
            self.append_log(traceback.format_exc())

if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = ConverterApp()
    win.show()
    sys.exit(app.exec())