#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI launcher (v2) — pick the exporter .py explicitly, then run it.

Works with the merged exporter that exposes:
  - build_model_from_csv(csv_path: Path) -> (header: dict, models: list)
  - write_fcpxml_from_model(header, models, out_path: Path) -> None
  - write_marker_edl_from_model(header, models, out_path: Path, max_kf_per_event: Optional[int]) -> None

Usage:
  python launch_fcpxml_and_markers_gui_v2.py
"""

from __future__ import annotations
import os
import sys
import traceback
import importlib.util
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional

# --------------------------------
# Dynamic import by file path
# --------------------------------
def import_module_from_path(py_path: Path):
    py_path = py_path.resolve()
    if not py_path.exists():
        raise FileNotFoundError(f"Exporter file not found:\n{py_path}")
    # Build a unique module name based on file path
    mod_name = f"exporter_{abs(hash(str(py_path)))}"
    spec = importlib.util.spec_from_file_location(mod_name, str(py_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec for:\n{py_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    sys.modules[mod_name] = mod
    return mod

REQUIRED_FUNCS = (
    "build_model_from_csv",
    "write_fcpxml_from_model",
    "write_marker_edl_from_model",
)

# --------------------------------
# GUI
# --------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CSV → FCPXML (REL13) + Marker EDL (REL15)")
        self.geometry("820x560")
        self.minsize(760, 540)

        # Vars
        self.exporter_path = tk.StringVar()
        self.csv_path = tk.StringVar()
        self.xml_path = tk.StringVar()
        self.edl_path = tk.StringVar()
        self.max_kf = tk.StringVar()
        self.open_folder = tk.BooleanVar(value=True)

        # Build UI
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, **pad)

        row = 0
        # Exporter picker
        ttk.Label(frm, text="Exporter script (.py):").grid(row=row, column=0, sticky="e")
        ttk.Entry(frm, textvariable=self.exporter_path, width=80).grid(row=row, column=1, sticky="we")
        ttk.Button(frm, text="Browse…", command=self.pick_exporter).grid(row=row, column=2, sticky="w")
        row += 1

        # CSV picker
        ttk.Label(frm, text="SuperEDL CSV:").grid(row=row, column=0, sticky="e")
        ttk.Entry(frm, textvariable=self.csv_path, width=80).grid(row=row, column=1, sticky="we")
        ttk.Button(frm, text="Browse…", command=self.pick_csv).grid(row=row, column=2, sticky="w")
        row += 1

        # XML out
        ttk.Label(frm, text="FCPXML output:").grid(row=row, column=0, sticky="e")
        ttk.Entry(frm, textvariable=self.xml_path, width=80).grid(row=row, column=1, sticky="we")
        ttk.Button(frm, text="Save as…", command=self.pick_xml).grid(row=row, column=2, sticky="w")
        row += 1

        # EDL out
        ttk.Label(frm, text="Marker EDL output:").grid(row=row, column=0, sticky="e")
        ttk.Entry(frm, textvariable=self.edl_path, width=80).grid(row=row, column=1, sticky="we")
        ttk.Button(frm, text="Save as…", command=self.pick_edl).grid(row=row, column=2, sticky="w")
        row += 1

        # Options
        opts = ttk.Frame(frm)
        opts.grid(row=row, column=0, columnspan=3, sticky="we")
        ttk.Label(opts, text="Max keyframe times per event (optional):").pack(side="left")
        ttk.Entry(opts, textvariable=self.max_kf, width=8).pack(side="left", padx=(6, 10))
        ttk.Checkbutton(opts, text="Open output folder when done", variable=self.open_folder).pack(side="left")
        row += 1

        # Buttons
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="we", pady=(2, 8))
        ttk.Button(btns, text="Run", command=self.run).pack(side="left", padx=5)
        ttk.Button(btns, text="Open output folder", command=self.open_outputs_folder).pack(side="left", padx=5)
        ttk.Button(btns, text="Quit", command=self.destroy).pack(side="right", padx=5)
        row += 1

        # Log
        ttk.Label(frm, text="Log:").grid(row=row, column=0, sticky="ne")
        self.txt = tk.Text(frm, height=18, wrap="word")
        self.txt.grid(row=row, column=1, columnspan=2, sticky="nsew")
        frm.rowconfigure(row, weight=1)
        frm.columnconfigure(1, weight=1)

        self.log("Pick the exporter .py, then your CSV. Outputs auto-fill.")

    # -------------- helpers --------------
    def log(self, msg: str):
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.update_idletasks()

    def pick_exporter(self):
        path = filedialog.askopenfilename(
            title="Select exporter .py",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")]
        )
        if path:
            self.exporter_path.set(path)

    def pick_csv(self):
        path = filedialog.askopenfilename(
            title="Select SuperEDL CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self.csv_path.set(path)
            self.autofill_outputs(Path(path))

    def pick_xml(self):
        path = filedialog.asksaveasfilename(
            title="Save FCPXML as…",
            defaultextension=".fcpxml",
            filetypes=[("FCPXML", "*.fcpxml")]
        )
        if path:
            self.xml_path.set(path)

    def pick_edl(self):
        path = filedialog.asksaveasfilename(
            title="Save Marker EDL as…",
            defaultextension=".edl",
            filetypes=[("EDL", "*.edl"), ("Text", "*.txt")]
        )
        if path:
            self.edl_path.set(path)

    def autofill_outputs(self, csv_path: Path):
        base = csv_path.with_suffix("")
        xml_default = base.parent / f"{base.name}_REL13.fcpxml"
        edl_default = base.parent / f"{base.name}_REL15_markers.edl"
        if not self.xml_path.get():
            self.xml_path.set(str(xml_default))
        if not self.edl_path.get():
            self.edl_path.set(str(edl_default))

    def open_outputs_folder(self):
        target: Optional[Path] = None
        if self.xml_path.get():
            target = Path(self.xml_path.get()).resolve().parent
        elif self.edl_path.get():
            target = Path(self.edl_path.get()).resolve().parent
        elif self.csv_path.get():
            target = Path(self.csv_path.get()).resolve().parent
        if target and target.exists():
            if os.name == "nt":
                os.startfile(str(target))  # Windows
            elif sys.platform == "darwin":
                os.system(f'open "{target}"')
            else:
                os.system(f'xdg-open "{target}"')
        else:
            messagebox.showinfo("Open folder", "Nothing to open yet.")

    # -------------- run --------------
    def run(self):
        self.txt.delete("1.0", "end")
        exp_path = self.exporter_path.get().strip()
        csv_path = self.csv_path.get().strip()
        xml_path = self.xml_path.get().strip()
        edl_path = self.edl_path.get().strip()

        if not exp_path:
            messagebox.showerror("Missing exporter", "Please choose the exporter .py file.")
            return
        if not csv_path:
            messagebox.showerror("Missing CSV", "Please choose a SuperEDL CSV.")
            return

        try:
            csv_p = Path(csv_path).resolve(strict=True)
        except Exception:
            messagebox.showerror("CSV not found", f"Cannot find:\n{csv_path}")
            return

        xml_p = Path(xml_path).resolve() if xml_path else None
        edl_p = Path(edl_path).resolve() if edl_path else None
        if not xml_p or not edl_p:
            self.autofill_outputs(Path(csv_path))
            xml_p = Path(self.xml_path.get()).resolve()
            edl_p = Path(self.edl_path.get()).resolve()

        xml_p.parent.mkdir(parents=True, exist_ok=True)
        edl_p.parent.mkdir(parents=True, exist_ok=True)

        # optional max keyframes
        max_kf = None
        if self.max_kf.get().strip():
            try:
                max_kf = int(self.max_kf.get().strip())
                if max_kf < 1:
                    raise ValueError
            except Exception:
                messagebox.showerror("Invalid number", "Max keyframes must be a positive integer.")
                return

        # Import exporter
        try:
            self.log("[1/3] Importing exporter module from file…")
            exporter = import_module_from_path(Path(exp_path))
        except Exception as e:
            self.log("ERROR importing exporter:")
            self.log(str(e))
            self.log(traceback.format_exc())
            messagebox.showerror("Import error", f"{e}\n\nSee log for details.")
            return

        # Check required functions
        missing = [fn for fn in REQUIRED_FUNCS if not hasattr(exporter, fn)]
        if missing:
            msg = ("The selected exporter is missing required functions:\n  - " +
                   "\n  - ".join(missing) +
                   "\n\nExpected:\n"
                   "  build_model_from_csv(csv_path: Path)\n"
                   "  write_fcpxml_from_model(header, models, out_path: Path)\n"
                   "  write_marker_edl_from_model(header, models, out_path: Path, max_kf_per_event: Optional[int])")
            self.log(msg)
            messagebox.showerror("Wrong exporter", msg)
            return

        # Run export
        try:
            self.log("[2/3] Building model from CSV…")
            header, models = exporter.build_model_from_csv(Path(csv_p))

            self.log("[3/3] Writing FCPXML (REL13)…")
            exporter.write_fcpxml_from_model(header, models, Path(xml_p))

            self.log("Writing Marker EDL (REL15)…")
            exporter.write_marker_edl_from_model(header, models, Path(edl_p), max_kf_per_event=max_kf)

            self.log("✅ Done.")
            self.log(f"FCPXML: {xml_p}")
            self.log(f"Marker EDL: {edl_p}")

            if self.open_folder.get():
                self.open_outputs_folder()

            messagebox.showinfo("Success", "Export finished.")
        except Exception as e:
            self.log("❌ Export failed.")
            self.log(str(e))
            self.log(traceback.format_exc())
            messagebox.showerror("Export failed", f"{e}\n\nSee the log for details.")


if __name__ == "__main__":
    # crisp UI on HiDPI (Windows)
    try:
        from ctypes import windll  # type: ignore
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = App()
    app.mainloop()
