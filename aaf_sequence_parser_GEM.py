# AAF_Timeline_Analyzer.py
#
# An all-in-one GUI tool to load and cross-reference AAF, EDL,
# Avid Sequence Reports, and Source Metadata CSV files.
# It parses all inputs and displays a unified timeline view.

import json
import sys
import os
import re
import csv
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTreeView, QMenu, QFileDialog, QMessageBox,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt

# --- TIME UTILITY ---
class Timecode:
    """A utility class to handle timecode to frames conversion."""
    def __init__(self, fps=25.0):
        self.fps = float(fps)

    def tc_to_frames(self, tc_string):
        if not isinstance(tc_string, str):
            return 0
        parts = tc_string.split(':')
        if len(parts) != 4:
            return 0
        try:
            h, m, s, f = [int(p) for p in parts]
            total_seconds = (h * 3600) + (m * 60) + s
            total_frames = int(total_seconds * self.fps) + f
            return total_frames
        except (ValueError, TypeError):
            return 0

    def frames_to_tc(self, frames):
        if not isinstance(frames, (int, float)):
            return "00:00:00:00"
        frames = int(frames)
        f = frames % int(self.fps)
        total_seconds = frames // int(self.fps)
        s = total_seconds % 60
        m = (total_seconds // 60) % 60
        h = total_seconds // 3600
        return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

# --- PARSING LOGIC ---
def parse_edl(file_path):
    """Parses an EDL file and returns a dictionary of events."""
    events = {}
    # Regex updated to capture all four timecode fields
    event_pattern = re.compile(r"^\d+\s+([\w\.\s-]+?)\s+V\s+C\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})")
    try:
        # MODIFIED: Use 'utf-16-le' which is more specific for Windows-generated files that may lack a BOM.
        with open(file_path, 'r', encoding='utf-16-le') as f:
            for line in f:
                match = event_pattern.match(line)
                if match:
                    clip_name, src_in, src_out, rec_in, rec_out = match.groups()
                    key = clip_name.strip()
                    events[key] = {
                        'src_in_tc': src_in, 
                        'src_out_tc': src_out,
                        'rec_in_tc': rec_in,
                        'rec_out_tc': rec_out
                    }
    except Exception as e:
        raise ValueError(f"Failed to parse EDL file {os.path.basename(file_path)}: {e}")
    return events

def parse_sequence_report(file_path):
    """Parses an Avid Sequence Report for clip and effect info."""
    report_data = {'clips': {}, 'effects': []}
    in_clip_list = False
    try:
        # MODIFIED: Use 'utf-16-le' for robustness.
        with open(file_path, 'r', encoding='utf-16-le') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if "Source Clip List" in line:
                    in_clip_list = True
                    continue
                if "Tape Source Info" in line or "Imported Source Info" in line:
                    in_clip_list = False
                    continue
                
                if in_clip_list:
                    parts = line.split()
                    if len(parts) > 2 and "V1" in parts:
                        mob_id_match = re.search(r'([0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})', line)
                        if mob_id_match:
                            mob_id = mob_id_match.group(1)
                            # Assume clip name is everything before the mob id, after the track info
                            name_part = line.split(mob_id)[0]
                            clip_name = ' '.join(name_part.split()[2:]).strip()
                            report_data['clips'][clip_name] = {'mob_id': mob_id}

                if line.startswith('V1') or line.startswith('(V'):
                    parts = line.split()
                    if len(parts) >= 4:
                        start_tc = parts[1]
                        effect_name = " ".join(parts[3:])
                        report_data['effects'].append({'start_tc': start_tc, 'name': effect_name})
    except Exception as e:
        raise ValueError(f"Failed to parse Sequence Report {os.path.basename(file_path)}: {e}")
    return report_data

def parse_source_csv(file_path):
    """Parses a source metadata CSV into a dictionary."""
    metadata = {}
    try:
        # MODIFIED: Use 'utf-16-le' and specify tab delimiter
        with open(file_path, 'r', encoding='utf-16-le') as f:
            # Assumes CSV format: Clip Name<tab>Start TC<tab>FPS
            reader = csv.reader(f, delimiter='\t')
            next(reader, None) # Skip header
            for row in reader:
                if len(row) >= 3:
                    clip_name, start_tc, fps = row
                    metadata[clip_name.strip()] = {'start_tc': start_tc, 'fps': float(fps)}
    except Exception as e:
        raise ValueError(f"Failed to parse Source Metadata file {os.path.basename(file_path)}: {e}")
    return metadata

def parse_compressed_aaf_json(file_path):
    """Parses the compressed AAF JSON to find the main timeline."""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    def get_name(node): return node[0] if isinstance(node, list) and len(node) > 0 else None
    def get_class(node): return node[1] if isinstance(node, list) and len(node) > 1 else None
    def get_children(node): return node[-1] if isinstance(node, list) and len(node) > 0 and isinstance(node[-1], list) else None
    def find_child_by_name(p, name):
        c = get_children(p)
        if c:
            for child in c:
                if get_name(child) == name: return child
        return None
    
    def find_components_recursively(node):
        if get_name(node) == 'Components' and get_children(node) is not None: return node
        children = get_children(node)
        if children:
            for child in children:
                result = find_components_recursively(child)
                if result: return result
        return None

    def parse_mob(comp_mob):
        slots = find_child_by_name(comp_mob, 'Slots')
        if not slots: return None
        children = get_children(slots)
        if not children: return None
        for slot in children:
            components_node = find_components_recursively(slot)
            if components_node:
                return components_node
        return None

    root_node = data[0] if isinstance(data, list) else data
    mobs_list_node = find_child_by_name(find_child_by_name(find_child_by_name(find_child_by_name(root_node, 'Header'), 'Header'), 'Content'), 'ContentStorage')
    mobs_list = get_children(find_child_by_name(mobs_list_node, 'Mobs'))
    
    best_timeline_components = None
    max_events = -1

    for mob in mobs_list:
        if get_class(mob) == 'CompositionMob':
            components_node = parse_mob(mob)
            if components_node:
                num_events = len(get_children(components_node) or [])
                if num_events > max_events:
                    max_events = num_events
                    best_timeline_components = components_node
    
    return best_timeline_components, mobs_list

# --- GUI ---
class FileLoaderWidget(QWidget):
    """A widget with four file selection buttons."""
    def __init__(self):
        super().__init__()
        layout = QGridLayout(self)
        self.paths = {}
        
        self.widgets = {
            'aaf': ("AAF JSON (Compressed)", QPushButton("Browse..."), QLabel("Not Selected")),
            'edl': ("EDL File", QPushButton("Browse..."), QLabel("Not Selected")),
            'csv': ("Source Metadata CSV", QPushButton("Browse..."), QLabel("Not Selected")),
            'rpt': ("Sequence Report TXT", QPushButton("Browse..."), QLabel("Not Selected")),
        }

        for i, key in enumerate(self.widgets.keys()):
            label_text, button, path_label = self.widgets[key]
            layout.addWidget(QLabel(label_text), i, 0)
            layout.addWidget(button, i, 1)
            layout.addWidget(path_label, i, 2)
            button.clicked.connect(lambda checked=False, k=key: self.select_file(k))

    def select_file(self, key):
        title_map = {
            'aaf': "Select Compressed AAF JSON", 'edl': "Select EDL File",
            'csv': "Select Source CSV", 'rpt': "Select Sequence Report"
        }
        filter_map = {
            'aaf': "JSON (*.json)", 'edl': "EDL (*.edl)",
            'csv': "CSV (*.csv)", 'rpt': "Text (*.txt)"
        }
        
        path, _ = QFileDialog.getOpenFileName(self, title_map[key], "", filter_map[key])
        if path:
            self.paths[key] = path
            self.widgets[key][2].setText(os.path.basename(path))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AAF Timeline Analyzer")
        self.setGeometry(100, 100, 1200, 800)
        
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        self.setCentralWidget(main_widget)

        self.file_loader = FileLoaderWidget()
        main_layout.addWidget(self.file_loader)
        
        self.process_button = QPushButton("Analyze Timeline")
        self.process_button.clicked.connect(self.process_files)
        main_layout.addWidget(self.process_button)
        
        self.table = QTableWidget()
        main_layout.addWidget(self.table)
        
        self.create_menu()

    def create_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")
        export_action = QAction("Export to CSV...", self)
        export_action.triggered.connect(self.export_to_csv)
        file_menu.addAction(export_action)

    def process_files(self):
        paths = self.file_loader.paths
        if len(paths) < 4:
            QMessageBox.warning(self, "Missing Files", "Please select all four input files.")
            return
            
        try:
            # 1. Parse all inputs
            edl_data = parse_edl(paths['edl'])
            csv_data = parse_source_csv(paths['csv'])
            report_data = parse_sequence_report(paths['rpt'])
            timeline_components, all_mobs = parse_compressed_aaf_json(paths['aaf'])

            if not timeline_components:
                raise ValueError("Could not find a valid timeline in the AAF JSON.")

            # 2. Correlate and build final data
            self.final_data = self.correlate_data(timeline_components, edl_data, csv_data, report_data)
            
            # 3. Display in GUI
            self.display_data()

        except Exception as e:
            QMessageBox.critical(self, "Processing Error", f"An error occurred: {e}")

    def correlate_data(self, components_node, edl_data, csv_data, report_data):
        timeline = []
        tc_util = Timecode() 

        def get_name(node): return node[0] if isinstance(node, list) and len(node) > 0 else None
        def get_class(node): return node[1] if isinstance(node, list) and len(node) > 1 else None
        def get_children(node): return node[-1] if isinstance(node, list) and isinstance(node[-1], list) else None
        def get_value(node): return node[2] if isinstance(node, list) and len(node) > 2 else None
        def find_child_by_name(p, name):
            c = get_children(p)
            if c:
                for child in c:
                    if get_name(child) == name: return child
            return None

        for component in (get_children(components_node) or []):
            if get_class(component) != 'SourceClip':
                continue
            
            aaf_start_offset_node = find_child_by_name(component, 'StartTime')
            aaf_length_node = find_child_by_name(component, 'Length')
            aaf_start_offset = get_value(aaf_start_offset_node) if aaf_start_offset_node else 0
            aaf_length = get_value(aaf_length_node) if aaf_length_node else 0
            
            source_mob_ref = find_child_by_name(component, 'Source Mob Ref')
            clip_name = get_name(get_children(source_mob_ref)[0]) if source_mob_ref and get_children(source_mob_ref) else "Unknown"

            # Use a more robust way to find the matching clip name key
            found_key = None
            for key in edl_data.keys():
                if key in clip_name or clip_name in key:
                    found_key = key
                    break
            
            edl_clip = edl_data.get(found_key, {})
            csv_clip = csv_data.get(found_key, {})
            report_clip = report_data['clips'].get(found_key, {})
            
            fps = csv_clip.get('fps', 25.0)
            tc_util.fps = fps
            
            base_tc_str = csv_clip.get('start_tc', '00:00:00:00')
            base_tc_frames = tc_util.tc_to_frames(base_tc_str)
            
            absolute_start_frames = base_tc_frames + aaf_start_offset
            absolute_start_tc = tc_util.frames_to_tc(absolute_start_frames)
            
            # Correctly check for effects using timeline TC
            has_effect = "No"
            rec_in_frames = tc_util.tc_to_frames(edl_clip.get('rec_in_tc'))
            rec_out_frames = tc_util.tc_to_frames(edl_clip.get('rec_out_tc'))

            for effect in report_data['effects']:
                effect_start_frames = tc_util.tc_to_frames(effect['start_tc'])
                if rec_in_frames <= effect_start_frames < rec_out_frames:
                    if "FrameFlex" in effect['name'] or "Resize" in effect['name'] or "3DWarp" in effect['name']:
                        has_effect = "Yes"
                        break
            
            timeline.append({
                "Track": "V1",
                "Clip Name": clip_name,
                "Mob ID": report_clip.get('mob_id', 'N/A'),
                "Base Source TC": base_tc_str,
                "AAF Start Offset (frames)": aaf_start_offset,
                "Absolute Source TC": absolute_start_tc,
                "Duration (frames)": aaf_length,
                "FPS": fps,
                "Has Effect?": has_effect
            })
            
        return timeline

    def display_data(self):
        if not self.final_data:
            return
            
        headers = list(self.final_data[0].keys())
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.final_data))

        for row_idx, row_data in enumerate(self.final_data):
            for col_idx, key in enumerate(headers):
                item = QTableWidgetItem(str(row_data.get(key, "")))
                self.table.setItem(row_idx, col_idx, item)
                
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def export_to_csv(self):
        if not hasattr(self, 'final_data') or not self.final_data:
            QMessageBox.warning(self, "Export Error", "No data to export. Please analyze files first.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV Files (*.csv)")
        if not path:
            return

        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=self.final_data[0].keys())
                writer.writeheader()
                writer.writerows(self.final_data)
            QMessageBox.information(self, "Success", f"Data exported to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export data: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
