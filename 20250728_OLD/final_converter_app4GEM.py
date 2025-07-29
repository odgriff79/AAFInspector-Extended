import sys
import pandas as pd
import xml.etree.ElementTree as ET
from xml.dom import minidom
import traceback
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QFileDialog, QTextEdit, QTableWidget,
    QTableWidgetItem, QLabel, QHeaderView
)

# ==============================================================================
#  1. CONFIGURATION & BACKEND LOGIC
# ==============================================================================

COLUMN_MAPPING = {
    "clip_name": "Clip Name",
    "source_file_path": "Source File Path",
    "source_file_name": "Source File Name",
    "edit_rate": "Source Clip EditRate",
    "start_time_frames": "StartTime (frames)",
    "source_start_frames": "Source Clip start (frames)",
    "event_length": "Event Length",
    "timeline_start_tc": "Timeline Start TC",
    "converted_keyframes": "FCPXML_Converted_Keyframes" 
}

def clean_text(input_data):
    if pd.isna(input_data): return ""
    return str(input_data).replace('\x00', '')

def create_xmeml_final(df):
    C = COLUMN_MAPPING
    # ✅ FIX: Match version number from the template
    xmeml = ET.Element('xmeml', version="4")
    sequence = ET.SubElement(xmeml, 'sequence')

    sequence_rate_val = df[C["edit_rate"]].iloc[0]
    is_ntsc = "FALSE" if sequence_rate_val in [25, 50] else "TRUE"
    
    ET.SubElement(sequence, 'name').text = "Final_Resolve_Sequence_v18"
    max_start = df[C["start_time_frames"]].max()
    duration_at_max_start = df.loc[df[C["start_time_frames"]].idxmax(), C["event_length"]]
    ET.SubElement(sequence, 'duration').text = str(int(max_start + duration_at_max_start))
    
    rate_seq = ET.SubElement(sequence, 'rate')
    ET.SubElement(rate_seq, 'timebase').text = str(int(sequence_rate_val))
    ET.SubElement(rate_seq, 'ntsc').text = is_ntsc
    
    timecode = ET.SubElement(sequence, 'timecode')
    ET.SubElement(timecode, 'rate', **{'timebase': str(int(sequence_rate_val)), 'ntsc': is_ntsc})
    # Set a standard start timecode. Resolve often prefers this.
    ET.SubElement(timecode, 'string').text = "10:00:00:00"
    ET.SubElement(timecode, 'frame').text = str(900000) # 10:00:00:00 at 25fps
    ET.SubElement(timecode, 'displayformat').text = "NDF"
    
    media = ET.SubElement(sequence, 'media')
    video = ET.SubElement(media, 'video')
    
    # ✅ FIX: Create a single track that will contain all self-contained clips
    track = ET.SubElement(video, 'track')

    # ✅ FIX: Remove all bin logic. Loop once and create complete, self-contained clips.
    for i, row in df.iterrows():
        start_frame = row[C["start_time_frames"]]
        duration = row[C["event_length"]]
        in_frame = row[C["source_start_frames"]]
        
        clipitem = ET.SubElement(track, 'clipitem', id=f"clipitem-{i+1}")
        ET.SubElement(clipitem, 'name').text = clean_text(row[C['clip_name']])
        ET.SubElement(clipitem, 'start').text = str(start_frame)
        ET.SubElement(clipitem, 'end').text = str(start_frame + duration)
        ET.SubElement(clipitem, 'in').text = str(in_frame)
        ET.SubElement(clipitem, 'out').text = str(in_frame + duration)
        
        # Create a full, self-contained <file> block inside each <clipitem>
        file_el = ET.SubElement(clipitem, 'file', id=f"file-{i+1}")
        ET.SubElement(file_el, 'name').text = clean_text(row[C["source_file_name"]])
        
        clean_path = clean_text(row[C['source_file_path']]).replace('\\', '/')
        # Resolve template uses file:/// not file://localhost/
        ET.SubElement(file_el, 'pathurl').text = f"file://{clean_path}"
        
        rate_file = ET.SubElement(file_el, 'rate')
        ET.SubElement(rate_file, 'timebase').text = str(int(row[C['edit_rate']]))
        ET.SubElement(rate_file, 'ntsc').text = "FALSE" if row[C['edit_rate']] in [25, 50] else "TRUE"

        media_file = ET.SubElement(file_el, 'media')
        ET.SubElement(ET.SubElement(media_file, 'video'), 'track')
        ET.SubElement(ET.SubElement(media_file, 'audio'), 'track')
        
        # Inject keyframe data from the converted column
        keyframe_xml_string = clean_text(row.get(C["converted_keyframes"]))
        if keyframe_xml_string and keyframe_xml_string.strip().startswith('<'):
            try:
                fragment = ET.fromstring(f"<root>{keyframe_xml_string}</root>")
                for effect_element in fragment:
                    clipitem.append(effect_element)
            except ET.ParseError as e:
                print(f"Warning: Could not parse keyframe XML for clip {i+1}. Error: {e}")

    try:
        rough_string = ET.tostring(xmeml, 'utf-8')
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ")
    except Exception:
        return ET.tostring(xmeml, encoding='unicode')

# ==============================================================================
#  2. FRONTEND LOGIC (The GUI)
# ==============================================================================
class XMLExporterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AAF CSV to XMEML Converter v18 (Template Matched)")
        self.setGeometry(100, 100, 1000, 800)
        self.dataframe = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        io_group = QWidget()
        io_group_layout = QVBoxLayout(io_group)
        h1_layout = QHBoxLayout()
        self.load_csv_btn = QPushButton("1. Load Converted CSV ('test 2.csv')...")
        h1_layout.addWidget(self.load_csv_btn)
        self.csv_path_le = QLineEdit("Click 'Load' to select your CSV file...")
        h1_layout.addWidget(self.csv_path_le)
        h2_layout = QHBoxLayout()
        self.set_xml_btn = QPushButton("2. Set Output XML Path...")
        self.xml_path_le = QLineEdit("Click 'Set Output' to choose save location...")
        h2_layout.addWidget(self.set_xml_btn)
        h2_layout.addWidget(self.xml_path_le)
        io_group_layout.addLayout(h1_layout)
        io_group_layout.addLayout(h2_layout)
        main_layout.addWidget(io_group)
        
        main_layout.addWidget(QLabel("CSV Data Preview:"))
        self.preview_table = QTableWidget()
        main_layout.addWidget(self.preview_table)
        
        self.generate_btn = QPushButton("3. Generate Final Resolve XMEML")
        self.generate_btn.setEnabled(False)
        self.generate_btn.setStyleSheet("font-size: 16px; padding: 10px; font-weight: bold; color: darkgreen;")
        main_layout.addWidget(self.generate_btn)
        
        main_layout.addWidget(QLabel("Log & Debug Console:"))
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        main_layout.addWidget(self.log_console)

        self.load_csv_btn.clicked.connect(self.load_csv)
        self.set_xml_btn.clicked.connect(self.set_output_xml)
        self.generate_btn.clicked.connect(self.run_generation)

        self.log(f"Application started. Please load your 'test 2.csv' file.")

    def log(self, message):
        self.log_console.append(message)
    
    def check_paths_and_data(self):
        if self.csv_path_le.text() and self.xml_path_le.text() and self.dataframe is not None:
            self.generate_btn.setEnabled(True)
            self.log("✅ All inputs are set. Ready to generate.")

    def load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV File", "", "CSV Files (*.csv)")
        if path:
            self.log(f"Attempting to load CSV from: {path}")
            try:
                self.dataframe = pd.read_csv(path, engine='python', dtype=str).fillna('')
                self.csv_path_le.setText(path)
                
                self.preview_table.setRowCount(self.dataframe.shape[0])
                self.preview_table.setColumnCount(self.dataframe.shape[1])
                self.preview_table.setHorizontalHeaderLabels(self.dataframe.columns)
                for i in range(self.dataframe.shape[0]):
                    for j in range(self.dataframe.shape[1]):
                        self.preview_table.setItem(i, j, QTableWidgetItem(str(self.dataframe.iat[i, j])))
                
                self.preview_table.resizeColumnsToContents()
                self.log(f"✅ Successfully loaded and displayed {len(self.dataframe)} rows.")
                self.check_paths_and_data()
            except Exception as e:
                self.log(f"❌ ERROR reading CSV: {traceback.format_exc()}")

    def set_output_xml(self):
        path, _ = QFileDialog.getSaveFileName(self, "Set Output XML File", "", "XML Files (*.xml)")
        if path:
            self.xml_path_le.setText(path)
            self.log(f"Output will be saved to: {path}")
            self.check_paths_and_data()

    def run_generation(self):
        self.log("\n🚀 Starting final XMEML generation...")
        
        C = COLUMN_MAPPING
        df_columns = set(self.dataframe.columns)
        missing_columns = [col for col in C.values() if col not in df_columns]
        
        if missing_columns:
            self.log(f"❌ VALIDATION FAILED: Missing columns: {', '.join(missing_columns)}")
            return
            
        self.log("✅ All required columns found.")

        try:
            clean_df = self.dataframe.copy()
            numeric_cols = [C['edit_rate'], C['start_time_frames'], C['source_start_frames'], C['event_length']]
            for col in numeric_cols:
                clean_df[col] = pd.to_numeric(clean_df[col], errors='coerce').fillna(0)

            xmeml_content = create_xmeml_final(clean_df)
            output_path = self.xml_path_le.text()
            with open(output_path, "w", encoding='utf-8') as f:
                f.write(xmeml_content)
            self.log(f"\n🎉 SUCCESS: Final XMEML file generated at:\n{output_path}")
        except Exception as e:
            self.log(f"❌ FATAL ERROR during generation:\n{traceback.format_exc()}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = XMLExporterApp()
    window.show()
    sys.exit(app.exec())