import json
import sys
import os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt

class JsonCompressorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JSON Compressor")
        self.setMinimumWidth(400)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        self.label = QLabel("Select a readable JSON file to compress.")
        self.layout.addWidget(self.label)

        self.btn_browse = QPushButton("Select Input JSON File...")
        self.btn_browse.clicked.connect(self.select_file)
        self.layout.addWidget(self.btn_browse)

        self.file_path_label = QLabel("No file selected.")
        self.file_path_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.file_path_label)

        self.btn_compress = QPushButton("Compress File")
        self.btn_compress.clicked.connect(self.compress_file)
        self.btn_compress.setEnabled(False) # Disabled until a file is selected
        self.layout.addWidget(self.btn_compress)
        
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.status_label)

        self.input_file_path = None

    def select_file(self):
        """Opens a file dialog to select the input JSON file."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select JSON File", "", "JSON Files (*.json)")
        if file_path:
            self.input_file_path = file_path
            self.file_path_label.setText(os.path.basename(file_path))
            self.btn_compress.setEnabled(True)
            self.status_label.setText("")

    def compress_file(self):
        """The main logic for reading, converting, and saving the compressed file."""
        if not self.input_file_path:
            QMessageBox.warning(self, "Warning", "Please select a file first.")
            return

        # 1. Read the full JSON file
        try:
            with open(self.input_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            QMessageBox.critical(self, "Error", f"Input file not found:\n{self.input_file_path}")
            return
        except json.JSONDecodeError:
            QMessageBox.critical(self, "Error", "The selected file is not a valid JSON file.")
            return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while reading the file:\n{e}")
            return

        # 2. Convert the data structure to the compact list format
        compressed_data = self.convert_node_to_list(data)

        # 3. Define the output path
        directory, filename = os.path.split(self.input_file_path)
        name, ext = os.path.splitext(filename)
        output_filename = f"{name}_comp{ext}"
        output_path = os.path.join(directory, output_filename)

        # 4. Write the new compressed file
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(compressed_data, f, separators=(',', ':'))
            self.status_label.setText(f"Success! Saved as:\n{output_filename}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while writing the file:\n{e}")

    def convert_node_to_list(self, node):
        """
        Recursively converts a dictionary node from the full format
        to the compact list format.
        """
        if not isinstance(node, dict):
            return node

        name = node.get("name")
        class_name = node.get("class")
        value = node.get("value")

        node_list = [name, class_name]

        children = []
        if "children" in node:
            children = [self.convert_node_to_list(child) for child in node["children"]]

        if value is not None or children:
            node_list.append(value)
        if children:
            node_list.append(children)
            
        return node_list

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = JsonCompressorApp()
    window.show()
    sys.exit(app.exec())
