import sys
import pandas as pd
import xml.etree.ElementTree as ET
from xml.dom import minidom
import traceback
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLineEdit, QFileDialog, QTextEdit, QLabel
)

# ==============================================================================
#  1. BACKEND LOGIC
# ==============================================================================

COLUMN_MAPPING = {
    "clip_name": "Clip Name",
    "source_file_name": "Source File Name",
    "source_file_path": "Source File Path",
    "edit_rate": "Source Clip EditRate",
    "timeline_start_tc": "Timeline Start TC",
    "source_start_tc": "Source Clip start time code",
    "event_length": "Event Length",
    "source_start_frames": "Source Clip start (frames)",
    "in_point": "Source Clip offset (frames)",
    "start_time_frames": "StartTime (frames)"
}

def clean_text(input_data):
    if pd.isna(input_data): return ""
    return str(input_data).replace('\x00', '')

def create_xmeml_final(df, logger):
    C = COLUMN_MAPPING
    xmeml = ET.Element('xmeml', version="5")
    sequence = ET.SubElement(xmeml, 'sequence')

    # ✅ THE FINAL FIX: Precisely filter out only what's not needed, keeping placeholders.
    original_row_count = len(df)
    unwanted_names = ['filler', 'slug', 'black']
    filler_conditions = df[C['clip_name']].str.lower().isin(unwanted_names)
    df_processed_clips = df[~filler_conditions].copy()
    
    # Log how many placeholders we are keeping
    ph_count = df_processed_clips[C['clip_name']].str.contains('placeholder', case=False, na=False).sum()
    logger(f"INFO: Including {ph_count} placeholder graphics.")

    omitted_count = original_row_count - len(df_processed_clips)
    if omitted_count > 0:
        logger(f"INFO: Kept {len(df_processed_clips)} total clips and omitted {omitted_count} filler/slug/black rows.")

    if df_processed_clips.empty:
        ET.SubElement(sequence, 'name').text = "Empty Sequence (No valid clips found)"
        return ET.tostring(xmeml, encoding='unicode')

    # Remove duplicates, keeping the last instance (important for re-used graphics)
    df_processed_clips = df_processed_clips.sort_values(by=C['start_time_frames'])
    df_processed_clips = df_processed_clips.drop_duplicates(subset=[C['clip_name']], keep='last')
    
    # Convert data types
    numeric_cols = [C['edit_rate'], C['start_time_frames'], C['source_start_frames'], C['event_length'], C['in_point']]
    for col_name in numeric_cols:
        df_processed_clips[col_name] = pd.to_numeric(df_processed_clips[col_name], errors='coerce').fillna(0)

    # Correctly determine the first clip and its timecode from the filtered data
    first_clip_row = df_processed_clips.loc[df_processed_clips[C['start_time_frames']].idxmin()]
    sequence_start_tc = clean_text(first_clip_row[C['timeline_start_tc']])
    sequence_start_frame = int(first_clip_row[C['start_time_frames']])
    sequence_rate_val = first_clip_row[C['edit_rate']]
    
    ET.SubElement(sequence, 'name').text = "Definitive_Resolve_Sequence"
    last_clip = df_processed_clips.loc[df_processed_clips[C['start_time_frames']].idxmax()]
    sequence_duration = (last_clip[C['start_time_frames']] - sequence_start_frame) + last_clip[C['event_length']]
    ET.SubElement(sequence, 'duration').text = str(int(sequence_duration))

    rate_seq = ET.SubElement(sequence, 'rate')
    ET.SubElement(rate_seq, 'timebase').text = str(int(sequence_rate_val))
    ET.SubElement(rate_seq, 'ntsc').text = "FALSE"

    timecode_seq = ET.SubElement(sequence, 'timecode')
    ET.SubElement(timecode_seq, 'string').text = sequence_start_tc
    ET.SubElement(timecode_seq, 'frame').text = str(sequence_start_frame)
    ET.SubElement(timecode_seq, 'displayformat').text = "NDF"

    media = ET.SubElement(sequence, 'media')
    video = ET.SubElement(media, 'video')
    track = ET.SubElement(video, 'track')

    for i, row in df_processed_clips.iterrows():
        start_frame_relative = row[C['start_time_frames']] - sequence_start_frame
        duration = row[C['event_length']]
        in_frame = row[C['in_point']]

        clipitem = ET.SubElement(track, 'clipitem', id=f"clipitem-{i+1}")
        ET.SubElement(clipitem, 'name').text = clean_text(row[C['clip_name']])
        
        rate_ci = ET.SubElement(clipitem, 'rate')
        ET.SubElement(rate_ci, 'timebase').text = str(int(row[C['edit_rate']]))
        ET.SubElement(rate_ci, 'ntsc').text = "FALSE"

        ET.SubElement(clipitem, 'start').text = str(int(start_frame_relative))
        ET.SubElement(clipitem, 'end').text = str(int(start_frame_relative + duration))
        ET.SubElement(clipitem, 'enabled').text = "TRUE"
        ET.SubElement(clipitem, 'in').text = str(int(in_frame))
        ET.SubElement(clipitem, 'out').text = str(int(in_frame + duration))
        
        file_el = ET.SubElement(clipitem, 'file', id=f"file-{i+1}")
        ET.SubElement(file_el, 'name').text = "Slug"
        ET.SubElement(file_el, 'mediaSource').text = "Slug"
        reel = ET.SubElement(file_el, 'reel')
        ET.SubElement(reel, 'name').text = clean_text(row[C['source_file_name']])
        
        clean_path = clean_text(row[C['source_file_path']]).replace('\\', '/')
        ET.SubElement(file_el, 'pathurl').text = f"file://{clean_path}"
        
        rate_file = ET.SubElement(file_el, 'rate')
        ET.SubElement(rate_file, 'timebase').text = str(int(row[C['edit_rate']]))
        ET.SubElement(rate_file, 'ntsc').text = "FALSE"
        
        timecode_file = ET.SubElement(file_el, 'timecode')
        ET.SubElement(timecode_file, 'string').text = clean_text(row[C['source_start_tc']])
        ET.SubElement(timecode_file, 'frame').text = str(int(row[C['source_start_frames']]))
        ET.SubElement(timecode_file, 'displayformat').text = "NDF"
        rate_tc_file = ET.SubElement(timecode_file, 'rate')
        ET.SubElement(rate_tc_file, 'timebase').text = str(int(row[C['edit_rate']]))
        ET.SubElement(rate_tc_file, 'ntsc').text = "FALSE"
        
        media_file = ET.SubElement(file_el, 'media')
        video_file = ET.SubElement(media_file, 'video')
        
        sample_chars = ET.SubElement(video_file, 'samplecharacteristics')
        ET.SubElement(sample_chars, 'width').text = "1920"
        ET.SubElement(sample_chars, 'height').text = "1080"
        
    rough_string = ET.tostring(xmeml, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")

# ==============================================================================
#  GUI
# ==============================================================================
class XMLExporterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Definitive Converter (Final)")
        self.setGeometry(100, 100, 800, 700)
        self.dataframe = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.load_csv_btn = QPushButton("1. Load CSV File...")
        self.csv_path_le = QLineEdit("Please select your source CSV...")
        self.set_xml_btn = QPushButton("2. Set Output XML Path...")
        self.xml_path_le = QLineEdit("Please select a save location...")
        self.generate_btn = QPushButton("3. Generate Final XMEML")
        self.log_console = QTextEdit()
        
        self.generate_btn.setEnabled(False)
        self.generate_btn.setStyleSheet("font-weight: bold; color: green;")
        
        main_layout.addWidget(self.load_csv_btn)
        main_layout.addWidget(self.csv_path_le)
        main_layout.addWidget(self.set_xml_btn)
        main_layout.addWidget(self.xml_path_le)
        main_layout.addWidget(self.generate_btn)
        main_layout.addWidget(QLabel("Log:"))
        main_layout.addWidget(self.log_console)

        self.load_csv_btn.clicked.connect(self.load_csv)
        self.set_xml_btn.clicked.connect(self.set_output_xml)
        self.generate_btn.clicked.connect(self.run_generation)

    def log(self, message): self.log_console.append(message)
    
    def check_ready(self):
        if self.csv_path_le.text() and self.xml_path_le.text() and self.dataframe is not None:
            self.generate_btn.setEnabled(True)
            self.log("Ready to generate.")

    def load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV Files (*.csv)")
        if path:
            try:
                # Intelligent loading logic
                temp_df = pd.read_csv(path, low_memory=False)
                required_cols = set(COLUMN_MAPPING.values())
                
                if not required_cols.issubset(temp_df.columns):
                    self.log("Header not found on first line, trying with skiprows=8...")
                    temp_df = pd.read_csv(path, skiprows=8, low_memory=False)

                self.dataframe = temp_df
                self.csv_path_le.setText(path)
                self.log(f"Loaded {len(self.dataframe)} total rows.")
                self.check_ready()
            except Exception as e:
                self.log(f"ERROR reading CSV: {e}")

    def set_output_xml(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save XML", "", "XML Files (*.xml)")
        if path:
            self.xml_path_le.setText(path)
            self.log(f"Output path set.")
            self.check_ready()

    def run_generation(self):
        self.log("\nStarting generation...")
        try:
            xmeml_content = create_xmeml_final(self.dataframe.copy(), self.log)
            with open(self.xml_path_le.text(), "w", encoding='utf-8') as f:
                f.write(xmeml_content)
            self.log(f"SUCCESS: File saved to {self.xml_path_le.text()}")
        except Exception as e:
            self.log(f"FATAL ERROR: {traceback.format_exc()}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = XMLExporterApp()
    window.show()
    sys.exit(app.exec())