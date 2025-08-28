#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
superedl_memory_gui_strictv2.py

GUI that:
  1) Builds TOP-LEVEL COMPOSITION tree in-memory via AAFInspector-enhanced.py
  2) Compresses via YOUR compress_json_gui.py (convert_node_to_list)
  3) Delegates CSV export to YOUR superEDLguiFX_UPDATED_v2.py (its App.process)

No duplication of superEDL logic. We monkey-patch Tk filedialog/messagebox so the
export runs headlessly to the path you choose in this Qt GUI.
"""

import os
import sys
import csv
import json
import traceback
import importlib.util

from PySide6 import QtCore, QtGui, QtWidgets

# ----------------------
# Dynamic module loader
# ----------------------
def load_module(name: str, path: str):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Module not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod

# ----------------------
# Read exported CSV back (for display only)
# ----------------------
def read_events_section(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    # find header
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Event,Event Name,Clip Name,Source File Name"):
            header_idx = i
            break
    summary_lines = []
    if header_idx is None:
        # maybe summary-only file?
        return [], [], []
    # summary block is from top until blank line before header
    j = 0
    while j < header_idx and lines[j].strip():
        summary_lines.append(lines[j].rstrip("\n"))
        j += 1
    # parse events as CSV
    import pandas as pd
    df = pd.read_csv(path, skiprows=header_idx, engine="python")
    headers = list(df.columns)
    rows = df.astype(str).values.tolist()
    return summary_lines, headers, rows

# ----------------------
# Qt GUI
# ----------------------
class GUI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SuperEDL (Strict v2 Export – In-Memory)")
        self.resize(1220, 800)

        base = os.path.dirname(os.path.abspath(__file__))
        self.path_aaf = ""
        self.path_ai = os.path.join(base, "AAFInspector-enhanced.py")
        self.path_cmp = os.path.join(base, "compress_json_gui.py")
        self.path_sed = os.path.join(base, "superEDLguiFX_UPDATED_v2.py")

        self._build_ui()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)

        # --- UPDATED CODE FOR LOGO ---
        try:
            logo_path = os.path.join(os.path.dirname(__file__), "GEMINI_v1_cropped.png")
            pixmap = QtGui.QPixmap(logo_path)
            if not pixmap.isNull():
                # Rescale the pixmap to 1/4 of its original size
                scaled_pixmap = pixmap.scaled(
                    pixmap.width() // 2,
                    pixmap.height() // 2,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation
                )
                logo_label = QtWidgets.QLabel()
                logo_label.setPixmap(scaled_pixmap)
                logo_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                v.addWidget(logo_label)
            else:
                print("Error: Could not load logo image. File not found or corrupt.")
        except Exception as e:
            print(f"Failed to add logo: {e}")
        # -----------------------------
        
        # Inputs
        grp = QtWidgets.QGroupBox("Inputs")
        grid = QtWidgets.QGridLayout(grp)

        self.edt_aaf = QtWidgets.QLineEdit(self.path_aaf)
        btn_aaf = QtWidgets.QPushButton("Browse…")
        btn_aaf.clicked.connect(lambda: self._browse(self.edt_aaf, "AAF files (*.aaf)"))

        self.edt_ai = QtWidgets.QLineEdit(self.path_ai)
        btn_ai = QtWidgets.QPushButton("Browse…")
        btn_ai.clicked.connect(lambda: self._browse(self.edt_ai, "Python files (*.py)"))

        self.edt_cmp = QtWidgets.QLineEdit(self.path_cmp)
        btn_cmp = QtWidgets.QPushButton("Browse…")
        btn_cmp.clicked.connect(lambda: self._browse(self.edt_cmp, "Python files (*.py)"))

        self.edt_sed = QtWidgets.QLineEdit(self.path_sed)
        btn_sed = QtWidgets.QPushButton("Browse…")
        btn_sed.clicked.connect(lambda: self._browse(self.edt_sed, "Python files (*.py)"))

        grid.addWidget(QtWidgets.QLabel("AAF File:"), 0, 0)
        grid.addWidget(self.edt_aaf, 0, 1)
        grid.addWidget(btn_aaf, 0, 2)

        grid.addWidget(QtWidgets.QLabel("AAFInspector-enhanced.py:"), 1, 0)
        grid.addWidget(self.edt_ai, 1, 1)
        grid.addWidget(btn_ai, 1, 2)

        grid.addWidget(QtWidgets.QLabel("compress_json_gui.py:"), 2, 0)
        grid.addWidget(self.edt_cmp, 2, 1)
        grid.addWidget(btn_cmp, 2, 2)

        grid.addWidget(QtWidgets.QLabel("superEDLguiFX_UPDATED_v2.py:"), 3, 0)
        grid.addWidget(self.edt_sed, 3, 1)
        grid.addWidget(btn_sed, 3, 2)

        v.addWidget(grp)

        # Run + Save
        h = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("Run (Strict v2 Export)")
        self.btn_run.clicked.connect(self._on_run)
        h.addWidget(self.btn_run)

        h.addStretch(1)

        self.btn_save_full = QtWidgets.QPushButton("Save Full JSON…")
        self.btn_save_full.clicked.connect(lambda: self._on_save_json("full"))
        self.btn_save_full.setEnabled(False)
        h.addWidget(self.btn_save_full)

        self.btn_save_comp = QtWidgets.QPushButton("Save Compressed JSON…")
        self.btn_save_comp.clicked.connect(lambda: self._on_save_json("comp"))
        self.btn_save_comp.setEnabled(False)
        h.addWidget(self.btn_save_comp)

        v.addLayout(h)

        # Summary
        self.txt_summary = QtWidgets.QPlainTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setPlaceholderText("Summary…")
        v.addWidget(self.txt_summary, 1)

        # Table
        self.table = QtWidgets.QTableView()
        self.model = QtGui.QStandardItemModel(self)
        self.table.setModel(self.model)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        v.addWidget(self.table, 3)

        # Log
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Log…")
        v.addWidget(self.log, 1)

        self.statusBar().showMessage("Ready")

    def _browse(self, lineedit: QtWidgets.QLineEdit, filt: str):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select file", "", filt)
        if p:
            lineedit.setText(p)

    def _log(self, msg: str):
        self.log.appendPlainText(msg)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _on_run(self):
        self.txt_summary.clear()
        self.model.clear()
        self.btn_save_full.setEnabled(False)
        self.btn_save_comp.setEnabled(False)

        aaf_path = self.edt_aaf.text().strip()
        path_ai = self.edt_ai.text().strip()
        path_cmp = self.edt_cmp.text().strip()
        path_sed = self.edt_sed.text().strip()

        if not os.path.isfile(aaf_path):
            QtWidgets.QMessageBox.warning(self, "Missing", "Select a valid AAF file.")
            return
        for p in (path_ai, path_cmp, path_sed):
            if not os.path.isfile(p):
                QtWidgets.QMessageBox.warning(self, "Missing", f"Script not found:\n{p}")
                return

        # Ask user where to save the final CSV (we pass it through to superEDL's exporter)
        out_csv, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save CSV (exported by superEDL v2)", "", "CSV Files (*.csv)"
        )
        if not out_csv:
            return

        try:
            self._log("[1/4] Loading modules…")
            mod_ai = load_module("AAFInspector_enhanced", path_ai)
            mod_cmp = load_module("compress_json_gui", path_cmp)
            mod_sed = load_module("superEDLguiFX_UPDATED_v2", path_sed)

            self._log("[2/4] Building top-level CompositionMob (in memory)…")
            options = {
                'toplevel': True,
                'compmobs': False,
                'mastermobs': False,
                'sourcemobs': False,
                'dictionary': False,
                'metadict': False,
                'root': False
            }
            Window = getattr(mod_ai, "Window")
            win = Window(parent=None)
            win.loadAafFile(aaf_path, options)
            model = win.model()
            if not model or not getattr(model, "rootItem", None):
                raise RuntimeError("AAFInspector model failed (no rootItem).")
            full_json = win._convert_node_to_dict(model.rootItem)

            self._log("[3/4] Compressing via YOUR compressor…")
            JsonCompressorApp = getattr(mod_cmp, "JsonCompressorApp")
            compressor = JsonCompressorApp()
            comp = compressor.convert_node_to_list(full_json)

            # Keep intermediates for optional saving
            self._last_full = full_json
            self._last_comp = comp
            self.btn_save_full.setEnabled(True)
            self.btn_save_comp.setEnabled(True)

            self._log("[4/4] Delegating export to superEDL v2 (no re-implementation)…")
            # We call superEDL's App.process(), but we monkey-patch filedialog to headless-save to out_csv
            import tkinter as tk
            from tkinter import filedialog, messagebox

            root = tk.Tk()
            root.withdraw()
            app = mod_sed.App(root)
            app.json_data = comp  # Inject in-memory compressed list

            # Monkey-patch dialogs/messages so no windows pop and we save where user chose
            orig_askdir = filedialog.askdirectory
            orig_asksave = filedialog.asksaveasfilename
            orig_info = messagebox.showinfo
            orig_error = messagebox.showerror

            try:
                filedialog.askdirectory = lambda title=None: os.path.dirname(out_csv)
                filedialog.asksaveasfilename = lambda **kwargs: out_csv
                messagebox.showinfo = lambda *a, **k: None
                messagebox.showerror = lambda *a, **k: None
                # Silence superEDL text log (optional)
                if hasattr(app, "log_msg"):
                    app.log_msg = lambda *_a, **_k: None
                app.process()  # <-- superEDL writes the CSV
            finally:
                filedialog.askdirectory = orig_askdir
                filedialog.asksaveasfilename = orig_asksave
                messagebox.showinfo = orig_info
                messagebox.showerror = orig_error
                root.destroy()

            # Read the CSV back for on-screen viewing
            try:
                summary_lines, headers, rows = read_events_section(out_csv)
                self._render_summary(summary_lines)
                self._render_table(headers, rows)
                self._log(f"✅ Exported by superEDL v2: {out_csv}")
                self.statusBar().showMessage("Done")
            except Exception as e:
                self._log("CSV read-back failed (file saved OK): " + str(e))
                self.statusBar().showMessage("Done (view skipped)")

        except Exception as e:
            self.statusBar().showMessage("Error")
            self._log("ERROR:\n" + "".join(traceback.format_exception(e)))
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def _render_summary(self, lines):
        self.txt_summary.setPlainText("\n".join(lines))

    def _render_table(self, headers, rows):
        self.model.setColumnCount(len(headers))
        self.model.setHorizontalHeaderLabels(headers)
        self.model.setRowCount(0)
        for r in rows:
            self.model.appendRow([QtGui.QStandardItem(str(x)) for x in r])
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _on_save_json(self, which: str):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Full JSON…" if which == "full" else "Save Compressed JSON…",
            "",
            "JSON Files (*.json)"
        )
        if not p:
            return
        try:
            if which == "full":
                data = getattr(self, "_last_full", None)
                if data is None:
                    raise RuntimeError("No in-memory full JSON yet. Run first.")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            else:
                data = getattr(self, "_last_comp", None)
                if data is None:
                    raise RuntimeError("No in-memory compressed JSON yet. Run first.")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(data, f, separators=(",", ":"))
            QtWidgets.QMessageBox.information(self, "Saved", f"JSON written:\n{p}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Write Error", str(e))


def main():
    app = QtWidgets.QApplication(sys.argv)

    # --- NEW CODE FOR STYLE SHEET ---
    style_sheet = """
        QMainWindow {
            background-color: #0d1a29; /* Dark blue from logo background */
        }
        QGroupBox {
            background-color: #1a2a3e; /* Slightly lighter dark blue */
            border: 1px solid #4a5c73;
            border-radius: 5px;
            margin-top: 1ex; /* Adjust the top margin to make space for the title */
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left; /* Position at the top left */
            padding: 0 3px;
            color: #d1d1d1; /* Light gray text */
        }
        QLabel {
            color: #d1d1d1; /* Light gray text */
        }
        QPushButton {
            background-color: #1a2a3e; /* Dark blue background */
            color: #ffffff; /* White text */
            border: 1px solid #4a5c73; /* Metallic gray border */
            border-radius: 5px;
            padding: 5px 15px;
        }
        QPushButton:hover {
            background-color: #008080; /* Teal color on hover */
        }
        QLineEdit, QPlainTextEdit, QTableView {
            background-color: #1a2a3e; /* Dark blue background */
            border: 1px solid #4a5c73; /* Metallic gray border */
            color: #ffffff; /* White text */
            selection-background-color: #ff8c00; /* Orange selection color */
        }
        QTableView QHeaderView::section {
            background-color: #0d1a29;
            color: #ffffff;
            border: 1px solid #4a5c73;
        }
        QStatusBar {
            background-color: #1a2a3e;
            color: #ffffff;
        }
    """
    app.setStyleSheet(style_sheet)
    # ----------------------------------

    w = GUI()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()