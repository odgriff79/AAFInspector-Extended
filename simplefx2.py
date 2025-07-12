import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
import os

def format_deep_structure(node, indent=0):
    lines = []
    prefix = "  " * indent

    if isinstance(node, list):
        for item in node:
            if isinstance(item, list) and len(item) >= 1:
                label = item[0] if isinstance(item[0], str) else "Item"
                lines.append(f"{prefix}{label}:")
                if len(item) > 3:
                    lines.extend(format_deep_structure(item[3], indent + 1))
                else:
                    lines.extend(format_deep_structure(item[1:], indent + 1))
            else:
                lines.append(f"{prefix}{item}")
    elif isinstance(node, dict):
        for k, v in node.items():
            lines.append(f"{prefix}{k}:")
            lines.extend(format_deep_structure(v, indent + 1))
    else:
        lines.append(f"{prefix}{node}")
    return lines

def contains_dv_reference(text):
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return "dve" in lowered or "dv" in lowered

def scan_avx_and_dve(data, dump_path):
    dump_lines = []

    def recursive(node, path="root", parent=None):
        if not isinstance(node, list) or len(node) < 4:
            return

        for child in node[3]:
            is_avx = False
            plugin_type = None
            plugin_name = None
            plugin_class = None
            plugin_mfr = None

            if isinstance(child, list) and child[0] == "ComponentAttributeList":
                for attr in child[3]:
                    if isinstance(attr, list) and len(attr) > 3:
                        name = next((c[2] for c in attr[3] if c[0] == "Name"), None)
                        value = next((c[2] for c in attr[3] if c[0] == "Value"), None)
                        if name == "_EFFECT_PLUGIN_TYPE":
                            plugin_type = value
                        elif name == "_EFFECT_PLUGIN_NAME":
                            plugin_name = value
                        elif name == "_EFFECT_PLUGIN_CLASS":
                            plugin_class = value
                        elif name == "_EFFECT_PLUGIN_MANUFACTURER_NAME":
                            plugin_mfr = value

                if plugin_type in ("AVX", "AVX2"):
                    is_avx = True

            # AVX effect block
            if is_avx:
                dump_lines.append(f"🔹 AVX Effect Detected: {plugin_name if plugin_name else '(Unknown)'}")
                dump_lines.append(f"  Path: {path}/ComponentAttributeList")
                dump_lines.append(f"  Type: {plugin_type}")
                dump_lines.append(f"  Class: {plugin_class if plugin_class else '(missing)'}")
                dump_lines.append(f"  Manufacturer: {plugin_mfr if plugin_mfr else '(missing)'}")
                if parent:
                    dump_lines.append(f"  ➤ Dumping Parent Context:")
                    dump_lines.extend(format_deep_structure(parent, indent=1))
                dump_lines.append("")

            # Generic DVE match (non-AVX)
            elif isinstance(child, list):
                candidate_block = str(child[0]).lower() if len(child) > 0 else ""
                if contains_dv_reference(candidate_block) or any(
                    contains_dv_reference(str(c)) for c in child if isinstance(c, str)
                ):
                    dump_lines.append(f"🔸 DVE-Related Block Detected")
                    dump_lines.append(f"  Path: {path}/{child[0]}")
                    dump_lines.append(f"  ➤ Dumping DVE Block:")
                    dump_lines.extend(format_deep_structure(child, indent=1))
                    dump_lines.append("")

            recursive(child, f"{path}/{child[0]}" if isinstance(child, list) else path, child)

    recursive(data)

    try:
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write("\n".join(dump_lines))
        return f"📝 Deep dump saved: {os.path.basename(dump_path)}"
    except Exception as e:
        return f"❌ Failed to save dump:\n{e}"

class AVXDeepDumpWithDVScanGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AVX/AVX2 + DVE Deep Scanner")
        self.root.geometry("980x640")

        tk.Button(root, text="Load AAF JSON File", command=self.load_file).pack(pady=10)
        self.file_label = tk.Label(root, text="No file loaded.", fg="grey")
        self.file_label.pack()

        self.scan_button = tk.Button(root, text="Scan & Dump Deep Metadata", command=self.scan_file, state=tk.DISABLED)
        self.scan_button.pack(pady=5)

        self.log = scrolledtext.ScrolledText(root, font=("Courier", 10))
        self.log.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.json_data = json.load(f)
                self.file_path = path
                self.file_label.config(text=f"Loaded: {os.path.basename(path)}", fg="black")
                self.log.delete(1.0, tk.END)
                self.log.insert(tk.END, f"✅ JSON loaded.\n")
                self.scan_button.config(state=tk.NORMAL)
            except Exception as e:
                self.file_label.config(text="Failed to load file.", fg="red")
                messagebox.showerror("Load Error", str(e))

    def scan_file(self):
        self.log.delete(1.0, tk.END)
        output_path = os.path.join(os.path.dirname(self.file_path), "AVX_deep_dump.txt")
        msg = scan_avx_and_dve(self.json_data, output_path)
        self.log.insert(tk.END, msg + "\n")

if __name__ == "__main__":
    root = tk.Tk()
    AVXDeepDumpWithDVScanGUI(root)
    root.mainloop()
