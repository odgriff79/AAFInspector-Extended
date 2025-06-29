"""
AAFInspector-Extended-Batch.py

Final version of the GUI converter. This script requires the user to manually
locate their local 'aaf2' library folder via the UI to ensure it can be found.
"""
from __future__ import (
    unicode_literals,
    absolute_import,
    print_function,
    division,
)

import sys
import os
import json
import datetime
import uuid
import traceback
import importlib

from PySide6 import QtCore, QtWidgets, QtGui

# We will attempt to import aaf2 later, after the user provides the path.

# Tracks to be excluded from the JSON export. Case-insensitive.
EXCLUDED_TRACK_NAMES = {f'a{i}' for i in range(1, 9)} | {'data track'}

class Worker(QtCore.QObject):
    """
    Worker object for running the conversion in a separate thread.
    """
    progress = QtCore.Signal(str)
    finished = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(self, file_list, output_dir, aaf2_module, mob_module):
        super().__init__()
        self.file_list = file_list
        self.output_dir = output_dir
        # Pass the imported modules to the worker
        self.aaf2 = aaf2_module
        self.mob = mob_module

    def run(self):
        """Main processing loop for the worker."""
        try:
            total_files = len(self.file_list)
            for i, aaf_path in enumerate(self.file_list):
                if not self.is_running:
                    self.progress.emit("Process cancelled.")
                    break
                self.progress.emit(f"Processing file {i+1} of {total_files}: {os.path.basename(aaf_path)}")
                self._process_file(aaf_path)
            if self.is_running:
                self.progress.emit("\nBatch conversion complete.")
        except Exception as e:
            detailed_error = f"An unexpected error occurred: {e}\n\n{traceback.format_exc()}"
            self.error.emit(detailed_error)
        finally:
            self.finished.emit()

    def stop(self):
        self.is_running = False

    def _process_file(self, aaf_path):
        """Opens, converts, and saves a single AAF file."""
        base_name = os.path.splitext(os.path.basename(aaf_path))[0]
        json_path = os.path.join(self.output_dir, f"{base_name}.json")

        try:
            with self.aaf2.open(aaf_path, 'r') as f:
                content_node = self.build_node(f.header, "Header")
                final_json = { "name": "Root", "class": "Root", "children": [content_node] }

            with open(json_path, 'w', encoding='utf-8') as out_file:
                json.dump(final_json, out_file, indent=4, ensure_ascii=False)
            self.progress.emit(f"  -> Successfully saved to {os.path.basename(json_path)}")
        except Exception as e:
            self.progress.emit(f"  -> ERROR converting {os.path.basename(aaf_path)}: {e}\n{traceback.format_exc()}")

    def build_node(self, item, name):
        """
        Recursive function to build a dictionary that matches the
        name/class/children or name/class/value schema.
        """
        class_name = item.__class__.__name__
        node = {"name": name, "class": class_name}

        if isinstance(item, self.aaf2.core.AAFObject):
            children = []
            is_comp_mob = isinstance(item, self.mob.CompositionMob)
            for prop in item.properties():
                prop_name = prop.name
                if is_comp_mob and prop_name == "Slots":
                    for slot in prop.value:
                        if getattr(slot, 'name', '').lower() not in EXCLUDED_TRACK_NAMES:
                            children.append(self.build_node(slot, slot.name))
                else:
                    children.append(self.build_node(prop, prop_name))
            if children:
                node['children'] = children
        elif isinstance(item, (self.aaf2.properties.StrongRefVectorProperty, self.aaf2.properties.StrongRefSetProperty)):
            children = []
            if item.value:
                for child_item in item.value:
                    child_name = getattr(child_item, 'name', child_item.__class__.__name__)
                    children.append(self.build_node(child_item, child_name))
            if children:
                node['children'] = children
        elif isinstance(item, self.aaf2.properties.StrongRefProperty):
            child_item = item.value
            if child_item:
                child_name = getattr(child_item, 'name', child_item.__class__.__name__)
                node['children'] = [self.build_node(child_item, child_name)]
        elif isinstance(item, self.aaf2.properties.Property):
            node['value'] = self._serialize_json_value(item.value)
        else:
            node['value'] = self._serialize_json_value(item)
        return node

    def _serialize_json_value(self, value):
        """Converts a Python value into a JSON-serializable format."""
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, bytes):
            return repr(value)
        if isinstance(value, dict):
            return {str(k): self._serialize_json_value(v) for k, v in value.items()}
        return str(value)


class MainWindow(QtWidgets.QMainWindow):
    """The main application window."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AAF to JSON Batch Converter")
        self.setMinimumSize(700, 500)

        # Module placeholders
        self.aaf2_module = None
        self.mob_module = None
        
        self.input_paths = []
        self.output_dir = ""
        self.thread = None
        self.worker = None

        main_widget = QtWidgets.QWidget()
        self.setCentralWidget(main_widget)
        self.layout = QtWidgets.QVBoxLayout(main_widget)

        # --- Library Locator UI ---
        self.lib_group = QtWidgets.QGroupBox("Step 1: Locate AAF Library")
        lib_layout = QtWidgets.QHBoxLayout(self.lib_group)
        self.lib_label = QtWidgets.QLabel("The 'aaf2' library was not found. Please locate it.")
        self.lib_label.setStyleSheet("color: red;")
        self.locate_lib_btn = QtWidgets.QPushButton("Locate...")
        lib_layout.addWidget(self.lib_label)
        lib_layout.addWidget(self.locate_lib_btn)
        self.layout.addWidget(self.lib_group)

        # --- Main App UI (initially disabled) ---
        self.input_group = QtWidgets.QGroupBox("Step 2: Select Input")
        input_layout = QtWidgets.QVBoxLayout(self.input_group)
        self.input_label = QtWidgets.QLabel("No file or folder selected.")
        self.input_label.setWordWrap(True)
        btn_layout = QtWidgets.QHBoxLayout()
        self.select_file_btn = QtWidgets.QPushButton("Select AAF File...")
        self.select_folder_btn = QtWidgets.QPushButton("Select Folder...")
        btn_layout.addWidget(self.select_file_btn)
        btn_layout.addWidget(self.select_folder_btn)
        input_layout.addWidget(self.input_label)
        input_layout.addLayout(btn_layout)
        self.layout.addWidget(self.input_group)

        self.output_group = QtWidgets.QGroupBox("Step 3: Select Output Directory")
        output_layout = QtWidgets.QHBoxLayout(self.output_group)
        self.output_label = QtWidgets.QLabel("No output directory set.")
        self.output_label.setWordWrap(True)
        self.select_output_btn = QtWidgets.QPushButton("Set Directory...")
        output_layout.addWidget(self.output_label)
        output_layout.addWidget(self.select_output_btn)
        self.layout.addWidget(self.output_group)

        self.start_group = QtWidgets.QGroupBox("Step 4: Start Conversion")
        start_layout = QtWidgets.QHBoxLayout(self.start_group)
        self.start_btn = QtWidgets.QPushButton("Start Conversion")
        self.start_btn.setStyleSheet("font-weight: bold;")
        start_layout.addWidget(self.start_btn)
        self.layout.addWidget(self.start_group)

        log_group = QtWidgets.QGroupBox("Log")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.log_widget = QtWidgets.QTextEdit()
        self.log_widget.setReadOnly(True)
        log_layout.addWidget(log_group)
        self.layout.addWidget(log_group)

        # --- Connections ---
        self.locate_lib_btn.clicked.connect(self.locate_library)
        self.select_file_btn.clicked.connect(self.select_file)
        self.select_folder_btn.clicked.connect(self.select_folder)
        self.select_output_btn.clicked.connect(self.select_output)
        self.start_btn.clicked.connect(self.start_conversion)
        
        self.toggle_main_ui(False) # Start with main UI disabled
        self.update_start_button_state()

    def locate_library(self):
        """Opens a dialog to find the folder containing the 'aaf2' library."""
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Folder Containing Your 'aaf2' Library"
        )
        if not path:
            return

        sys.path.insert(0, path)
        try:
            # Dynamically import the library
            self.aaf2_module = importlib.import_module("aaf2")
            self.mob_module = importlib.import_module("aaf2.mob")
            
            self.lib_label.setText(f"Library found at: {path}")
            self.lib_label.setStyleSheet("color: green;")
            self.locate_lib_btn.setEnabled(False)
            self.toggle_main_ui(True) # Enable the rest of the UI
        except ImportError:
            QtWidgets.QMessageBox.critical(
                self, "Library Not Found",
                f"The 'aaf2' library was not found in the selected directory:\n\n{path}\n\nPlease select the correct parent folder."
            )
            # Remove the bad path to avoid issues
            sys.path.pop(0)

    def toggle_main_ui(self, enabled):
        """Enables or disables the main application controls."""
        self.input_group.setEnabled(enabled)
        self.output_group.setEnabled(enabled)
        self.start_group.setEnabled(enabled)

    def select_file(self):
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select AAF File", "", "AAF Files (*.aaf)")
        if file_path:
            self.input_paths = [file_path]
            self.input_label.setText(f"Selected File: {file_path}")
            self.log_widget.clear()
        self.update_start_button_state()

    def select_folder(self):
        folder_path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder Containing AAFs")
        if folder_path:
            self.input_paths = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if f.lower().endswith(".aaf")]
            self.input_label.setText(f"Selected Folder: {folder_path} ({len(self.input_paths)} AAFs found)")
            self.log_widget.clear()
            if not self.input_paths:
                self.log("Warning: No .aaf files found in the selected directory.")
        self.update_start_button_state()

    def select_output(self):
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if dir_path:
            self.output_dir = dir_path
            self.output_label.setText(f"Output Directory: {dir_path}")
        self.update_start_button_state()

    def update_start_button_state(self):
        enabled = bool(self.input_paths and self.output_dir and self.aaf2_module)
        self.start_btn.setEnabled(enabled)

    def log(self, message):
        self.log_widget.append(message)

    def show_error_message(self, message):
        QtWidgets.QMessageBox.critical(self, "Error", message)

    def start_conversion(self):
        self.log_widget.clear()
        self.log("Starting batch conversion...")
        self.set_ui_enabled(False)
        self.thread = QtCore.QThread()
        self.worker = Worker(self.input_paths, self.output_dir, self.aaf2_module, self.mob_module)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.progress.connect(self.log)
        self.worker.error.connect(self.show_error_message)
        self.thread.finished.connect(lambda: self.set_ui_enabled(True))
        self.thread.start()

    def set_ui_enabled(self, enabled):
        # We only re-enable the parts that should be active after the lib is found
        if self.aaf2_module:
            self.toggle_main_ui(enabled)
            self.start_btn.setEnabled(enabled)
        if enabled:
            self.start_btn.setText("Start Conversion")
        else:
            # Keep locate button active if lib not found
            if not self.aaf2_module:
                self.locate_lib_btn.setEnabled(True)
            self.start_btn.setText("Processing...")

    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            self.log("Stopping process...")
            self.worker.stop()
            self.thread.quit()
            self.thread.wait()
        event.accept()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())