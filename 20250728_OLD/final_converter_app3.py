import sys
import pandas as pd
import xml.etree.ElementTree as ET
from xml.dom import minidom
import re
import traceback
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QFileDialog, QTextEdit, QTableWidget,

    QTableWidgetItem, QLabel, QHeaderView
)
from PySide6.QtCore import Qt

# ==============================================================================
#  1. BACKEND LOGIC (The Converter)
# ==============================================================================

def parse_keyframes(effect_name, keyframe_details_str):
    """
    Parses the keyframe details string from the CSV to generate structured
    effect and parameter data for XMEML.
    """
    effects_list = []
    param_map = {
        'Master_S_X_Position': 'X_Position',
        'Master_S_Y_Position': 'Y_Position',
        'Master_S_Scale_X': 'Scale_X',
        'Master_S_Scale_Y': 'Scale_Y',
    }
    
    effect_clean_name = effect_name.split(':')[-1].strip() if ':' in effect_name else effect_name.strip()
    
    current_effect = {
        "name": effect_clean_name,
        "id": "Motion", # Standard ID for transform effects
        "params": []
    }

    if pd.isna(keyframe_details_str):
        effects_list.append(current_effect)
        return effects_list

    pairs = re.findall(r'([\w_]+)=([\d\.\-]+)', keyframe_details_str)
    
    parameter_values = {}
    for key, value in pairs:
        if key in param_map:
            parameter_values[param_map[key]] = value

    for param_name, param_value in parameter_values.items():
        param_block = {
            "name": param_name,
            "keyframes": [{"when": "0", "value": param_value}]
        }
        current_effect["params"].append(param_block)

    effects_list.append(current_effect)
    return effects_list

def create_xmeml_with_keyframes(df):
    """
    Generates an XMEML v5 string from the user's DataFrame, including keyframes.
    Takes a pandas DataFrame as input.
    """
    xmeml = ET.Element('xmeml', version="5")
    sequence = ET.SubElement(xmeml, 'sequence')
    
    sequence_rate_val = df['Source Clip EditRate'].iloc[0]
    sequence_ntsc = "FALSE" if sequence_rate_val == 25 else "TRUE"

    ET.SubElement(sequence, 'name').text = "Sequence_with_Keyframes"
    ET.SubElement(sequence, 'duration').text = str(int(df['StartTime (frames)'].max() + df['Event Length'].max()))
    
    rate = ET.SubElement(sequence, 'rate')
    ET.SubElement(rate, 'timebase').text = str(int(sequence_rate_val))
    ET.SubElement(rate, 'ntsc').text = sequence_ntsc
    
    media = ET.SubElement(sequence, 'media')
    video = ET.SubElement(media, 'video')
    track = ET.SubElement(video, 'track')

    for _, row in df.iterrows():
        if pd.isna(row['StartTime (frames)']) or pd.isna(row['Event Length']):
            continue

        start_frame = int(row['StartTime (frames)'])
        duration = int(row['Event Length'])
        in_frame = int(row['Source Clip start (frames)'])
        
        clipitem = ET.SubElement(track, 'clipitem', id=f"clip_{row['Event']}")
        ET.SubElement(clipitem, 'name').text = str(row['Clip Name'])
        ET.SubElement(clipitem, 'start').text = str(start_frame)
        ET.SubElement(clipitem, 'end').text = str(start_frame + duration)
        ET.SubElement(clipitem, 'in').text = str(in_frame)
        ET.SubElement(clipitem, 'out').text = str(in_frame + duration)
        
        file_el = ET.SubElement(clipitem, 'file', id=f"file_{row['Event']}")
        ET.SubElement(file_el, 'pathurl').text = f"file://localhost{row['Source File Path']}"
        ET.SubElement(file_el, 'name').text = str(row['Source File Name'])
        
        file_rate = ET.SubElement(file_el, 'rate')
        ET.SubElement(file_rate, 'timebase').text = str(int(row['Source Clip EditRate']))
        ET.SubElement(file_rate, 'ntsc').text = "FALSE" if row['Source Clip EditRate'] in [25, 50] else "TRUE"
        
        file_media = ET.SubElement(file_el, 'media')
        ET.SubElement(file_media, 'video')

        if pd.notna(row['Effect Name']):
            effects_data = parse_keyframes(row['Effect Name'], row['Keyframe Details'])
            for effect_info in effects_data:
                filter_el = ET.SubElement(clipitem, 'filter')
                effect_el = ET.SubElement(filter_el, 'effect')
                ET.SubElement(effect_el, 'name').text = effect_info.get("name")
                ET.SubElement(effect_el, 'effectid').text = effect_info.get("id")
                for param_info in effect_info.get("params", []):
                    param_el = ET.SubElement(effect_el, 'parameter')
                    ET.SubElement(param_el, 'parameterid').text = param_info.get("name")
                    ET.SubElement(param_el, 'name').text = param_info.get("name")
                    for keyframe_info in param_info.get("keyframes", []):
                        keyframe_el = ET.SubElement(param_el, 'keyframe')
                        ET.SubElement(keyframe_el, 'when').text = str(keyframe_info.get("when"))
                        ET.SubElement(keyframe_el, 'value').text = str(keyframe_info.get("value"))

    rough_string = ET.tostring(xmeml, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")

# ==============================================================================
#  2. FRONTEND LOGIC (The GUI)
# ==============================================================================

class XMLExporterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AAF CSV to XMEML v5 Converter (with Keyframes)")
        self.setGeometry(100, 100, 1000, 800)
        self.dataframe = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- I/O Panel ---
        io_layout = QHBoxLayout()
        self.load_csv_btn = QPushButton("1. Load Enriched CSV...")
        self.csv_path_le = QLineEdit("Click 'Load' to select your CSV file...")
        self.csv_path_le.setReadOnly(True)
        self.set_xml_btn = QPushButton("2. Set Output XML Path...")
        self.xml_path_le = QLineEdit("Click 'Set Output' to choose save location...")
        self.xml_path_le.setReadOnly(True)
        
        io_group = QWidget()
        io_group_layout = QVBoxLayout(io_group)
        
        h1_layout = QHBoxLayout()
        h1_layout.addWidget(self.load_csv_btn)
        h1_layout.addWidget(self.csv_path_le)
        
        h2_layout = QHBoxLayout()
        h2_layout.addWidget(self.set_xml_btn)
        h2_layout.addWidget(self.xml_path_le)
        
        io_group_layout.addLayout(h1_layout)
        io_group_layout.addLayout(h2_layout)
        main_layout.addWidget(io_group)

        # --- Data Preview Table ---
        main_layout.addWidget(QLabel("CSV Data Preview:"))
        self.preview_table = QTableWidget()
        main_layout.addWidget(self.preview_table)
        
        # --- Action Button ---
        self.generate_btn = QPushButton("3. Generate XMEML File")
        self.generate_btn.setEnabled(False)
        self.generate_btn.setStyleSheet("font-size: 16px; padding: 10px;")
        main_layout.addWidget(self.generate_btn)

        # --- Log Console ---
        main_layout.addWidget(QLabel("Log & Debug Console:"))
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        main_layout.addWidget(self.log_console)

        # --- Connect Signals ---
        self.load_csv_btn.clicked.connect(self.load_csv)
        self.set_xml_btn.clicked.connect(self.set_output_xml)
        self.generate_btn.clicked.connect(self.run_generation)

        self.log("Application started. Please load a CSV file.")

    def log(self, message):
        self.log_console.append(message)
        print(message)

    def check_paths_and_data(self):
        if self.csv_path_le.text() and self.xml_path_le.text() and self.dataframe is not None:
            self.generate_btn.setEnabled(True)
            self.log("✅ Inputs ready. You can now generate the XMEML file.")

    def load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV File", "", "CSV Files (*.csv)")
        if path:
            self.log(f"Loading CSV from: {path}")
            try:
                self.dataframe = pd.read_csv(path, skiprows=8)
                self.csv_path_le.setText(path)
                
                # Populate preview table
                self.preview_table.setRowCount(self.dataframe.shape[0])
                self.preview_table.setColumnCount(self.dataframe.shape[1])
                self.preview_table.setHorizontalHeaderLabels(self.dataframe.columns)
                
                for i in range(self.dataframe.shape[0]):
                    for j in range(self.dataframe.shape[1]):
                        self.preview_table.setItem(i, j, QTableWidgetItem(str(self.dataframe.iat[i, j])))
                
                self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                self.log(f"✅ Successfully loaded and displayed {len(self.dataframe)} rows from CSV.")
                self.check_paths_and_data()

            except Exception as e:
                self.log(f"❌ ERROR: Failed to read or parse CSV file.\n{traceback.format_exc()}")
                self.dataframe = None

    def set_output_xml(self):
        path, _ = QFileDialog.getSaveFileName(self, "Set Output XML File", "", "XML Files (*.xml)")
        if path:
            self.xml_path_le.setText(path)
            self.log(f"Output XML will be saved to: {path}")
            self.check_paths_and_data()

    def run_generation(self):
        self.log("\n🚀 Starting XMEML generation...")
        try:
            # Pass the already-loaded DataFrame to the backend function
            xmeml_content = create_xmeml_with_keyframes(self.dataframe)
            
            output_path = self.xml_path_le.text()
            with open(output_path, "w", encoding='utf-8') as f:
                f.write(xmeml_content)
            
            self.log(f"🎉 SUCCESS: XMEML file with keyframes generated at:\n{output_path}")
            
        except Exception as e:
            self.log(f"❌ FATAL ERROR during generation:\n{traceback.format_exc()}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = XMLExporterApp()
    window.show()
    sys.exit(app.exec())