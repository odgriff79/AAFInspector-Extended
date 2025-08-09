import os, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import APP_NAME
from .utils import log, aaf_class_name
from .extractor import AAFInMemoryExtractor
from .fcpxml_builder import FCPXMLBuilder
from .debug_io import write_debug_jsons

class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        self.aaf_path = tk.StringVar()
        self.out_path = tk.StringVar()
        self.placeholder_ok = tk.StringVar()
        self.placeholder_bad = tk.StringVar()

        frm = ttk.Frame(root, padding=10); frm.pack(fill="both", expand=True)
        def row(label, var, btn_text, cmd):
            r = ttk.Frame(frm); r.pack(fill="x", pady=4)
            ttk.Label(r, text=label, width=28).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(r, text=btn_text, command=cmd).pack(side="left")
            return r

        row("AAF file:", self.aaf_path, "Browse", self.browse_aaf)
        row("Output FCPXML:", self.out_path, "Save As", self.browse_out)
        row("Placeholder (convertible OK):", self.placeholder_ok, "Browse", self.browse_ok)
        row("Placeholder (other/non-conv):", self.placeholder_bad, "Browse", self.browse_bad)

        self.logbox = tk.Text(frm, height=20, width=112); self.logbox.pack(fill="both", expand=True, pady=6)
        run_row = ttk.Frame(frm); run_row.pack(fill="x")
        ttk.Button(run_row, text="Build FCPXML + Debug Logs", command=self.run).pack(side="left")
        ttk.Button(run_row, text="Open Log", command=self.open_log).pack(side="left", padx=6)

    def browse_aaf(self):
        p = filedialog.askopenfilename(title="Select AAF", filetypes=[("AAF files","*.aaf"),("All files","*.*")])
        if p: self.aaf_path.set(p)

    def browse_out(self):
        p = filedialog.asksaveasfilename(title="Save FCPXML", defaultextension=".fcpxml", filetypes=[("FCPXML","*.fcpxml")])
        if p: self.out_path.set(p)

    def browse_ok(self):
        p = filedialog.askopenfilename(title="Placeholder OK PNG", filetypes=[("PNG","*.png"),("All files","*.*")])
        if p: self.placeholder_ok.set(p)

    def browse_bad(self):
        p = filedialog.askopenfilename(title="Placeholder Other PNG", filetypes=[("PNG","*.png"),("All files","*.*")])
        if p: self.placeholder_bad.set(p)

    def _echo(self, s: str):
        log(s)
        try:
            self.logbox.insert("end", s + "\n")
            self.logbox.see("end")
            self.root.update_idletasks()
        except:
            pass

    def open_log(self):
        try:
            import aaf2resolve.utils as u
            os.startfile(u.LOG_PATH)
        except Exception as e:
            messagebox.showinfo("Log", str(e))

    def run(self):
        try:
            self.logbox.delete("1.0","end")
            aaf = self.aaf_path.get().strip()
            out = self.out_path.get().strip()
            okp = self.placeholder_ok.get().strip() or None
            badp = self.placeholder_bad.get().strip() or None

            if not aaf or not os.path.exists(aaf):
                messagebox.showerror("Error", "Select a valid AAF file.")
                return
            if not out:
                messagebox.showerror("Error", "Choose an output .fcpxml path.")
                return

            self._echo(f"Opening AAF: {aaf}")
            with AAFInMemoryExtractor(aaf) as ex:
                comps = ex.get_top_level_sequences()
                if not comps:
                    raise RuntimeError("No Top-Level Composition Mobs found")
                self._echo(f"Top-Level comps found: {len(comps)}")
                for idx, m in enumerate(comps):
                    self._echo(f"  [{idx}] name={getattr(m, 'name', '(unnamed)')} class={aaf_class_name(m)}")
                comp = comps[0]
                self._echo(f"Sequence: {getattr(comp, 'name','(unnamed)')}  class={aaf_class_name(comp)}")
                seq = ex.extract_sequence(comp)

                base = os.path.splitext(out)[0]
                ex.write_slots_tree(comp, base)
                self._echo(f"  - slots_tree: {base}_slots_tree.json")

            self._echo(f"Events: {len(seq.events)}  FPS={float(seq.fps)}  Size={seq.width}x{seq.height}")
            for i, ev in enumerate(seq.events[:25]):
                self._echo(f"  [{i}] {ev.name} recIn={ev.rec_in_f} recOut={ev.rec_out_f} durF={ev.duration_f} "
                           f"eff={ev.effect_name} src={ev.source_name or ev.filler_fx_file or 'N/A'}")

            dbg_paths = write_debug_jsons(out, seq, ex)
            self._echo("Wrote debug files:")
            for k, p in dbg_paths.items():
                self._echo(f"  - {k}: {p}")

            self._echo("Building FCPXML...")
            builder = FCPXMLBuilder(seq, okp, badp)
            tree = builder.build()
            xml_text = builder.serialize(tree)

            with open(out, "w", encoding="utf-8") as f:
                f.write(xml_text)
            self._echo(f"OK: wrote {out}")

        except Exception as e:
            tb = traceback.format_exc()
            self._echo(f"ERROR: {e}\n{tb}")
            messagebox.showerror("Error", f"{e}\n\nSee log for details.")
