import os
import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from datetime import datetime

def summarize_controlpoints(block):
    summary = {"keyframes": 0, "times": [], "edit_hint": None}
    
    def collect(cp):
        time = None
        val = None
        if not isinstance(cp, list):
            return
        for prop_block in cp:
            if isinstance(prop_block, list) and len(prop_block) >= 1 and prop_block[0] == "ControlPointPointProperties":
                for subprop in prop_block[3] if len(prop_block) > 3 else []:
                    if isinstance(subprop, list) and len(subprop) >= 4:
                        field = subprop[0]
                        val_field = [x for x in subprop[3] if x[0] == "Value"]
                        if val_field:
                            val_str = val_field[0][2]
                            if field == "Time":
                                time = val_str
                            elif field == "Value":
                                val = val_str
        if time is not None:
            summary["times"].append(time)
            summary["keyframes"] += 1

    def recurse(node):
        if isinstance(node, list):
            for item in node:
                if isinstance(item, list):
                    label = item[0] if len(item) > 0 else ""
                    body = item[3] if len(item) > 3 else []
                    if label == "ControlPoint":
                        collect(body)
                    elif label == "EditHint":
                        hint = item[2]
                        summary["edit_hint"] = hint
                    recurse(body)
    recurse(block)

    return summary

def scan_dve_keyframes(root_node):
    results = []
    
    def walk(node):
        if isinstance(node, list):
            for item in node:
                if isinstance(item, list) and len(item) >= 1:
                    label = item[0]
                    body = item[3] if len(item) > 3 else []
                    if isinstance(label, str) and label.startswith("DVE_"):
                        summary = summarize_controlpoints(item)
                        if summary["keyframes"] > 0:
                            line = f"{label:<20} ▪ Keyframes: {summary['keyframes']:<3} ▪ Time Range: {summary['times'][0]} → {summary['times'][-1]}" if summary["times"] else f"{label:<20} ▪ Keyframes: {summary['keyframes']}"
                            if summary["edit_hint"]:
                                line += f" ▪ EditHint: {summary['edit_hint']}"
                            results.append(line)
                    walk(body)
    walk(root_node)
    return results

class DVEKeyframeSummaryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DVE Keyframe Summary Scanner")
        self.root.geometry("880x600")
        
        tk.Button(root, text="Load JSON File", command=self.load_json).pack(pady=10)
        self.label = tk.Label(root, text="No file loaded", fg="gray")
        self.label.pack()
        
        self.scan_button = tk.Button(root, text="Summarize Keyframes", command=self.run_summary, state=tk.DISABLED)
        self.scan_button.pack(pady=5)
        
        self.output = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Courier New", 10))
        self.output.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)

    def load_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.json_data = json.load(f)
            self.file_path = path
            self.label.config(text=f"Loaded: {os.path.basename(path)}", fg="black")
            self.output.insert(tk.END, "✅ File loaded.\n")
            self.scan_button.config(state=tk.NORMAL)
        except Exception as e:
            self.label.config(text="Failed to load file", fg="red")
            messagebox.showerror("Error", f"Could not parse JSON:\n{e}")

    def run_summary(self):
        self.output.delete(1.0, tk.END)
        self.output.insert(tk.END, "🔎 Scanning for animated DVE parameters...\n")
        summary = scan_dve_keyframes(self.json_data)
        if summary:
            self.output.insert(tk.END, "\n".join(summary) + "\n")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(os.path.dirname(self.file_path), f"dve_keyframe_summary_{timestamp}.txt")
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(summary))
                self.output.insert(tk.END, f"\n✅ Summary saved to:\n{out_path}\n")
            except Exception as e:
                self.output.insert(tk.END, f"\n❌ Failed to save:\n{e}\n")
        else:
            self.output.insert(tk.END, "⚠️ No animated DVE parameters found.\n")

if __name__ == "__main__":
    root = tk.Tk()
    app = DVEKeyframeSummaryApp(root)
    root.mainloop()
