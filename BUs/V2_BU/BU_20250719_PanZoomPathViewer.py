import json
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox


def decode_filepath(filepath_node):
    """
    Safely decodes a UTF-16LE-encoded Filepath value from an AAF JSON node.
    The value is stored as a list of integers representing bytes.
    """
    try:
        # Extract the 'Value' child list
        value_node = next(
            (c for c in (filepath_node[3] if len(filepath_node) > 3 else [])
             if c[0] == "Value" and isinstance(c[2], list)),
            None
        )
        if not value_node:
            return "Path data not found or in an unexpected format."

        # Convert the integer list into bytes
        raw_bytes = bytes(b for b in value_node[2] if isinstance(b, int))

        # Decode using UTF-16LE, ignore errors
        txt = raw_bytes.decode("utf-16-le", errors="ignore")

        # Remove any header before the first backslash, if present
        idx = txt.find('\\')
        if idx != -1:
            txt = txt[idx:]

        # Strip trailing nulls and normalize separators
        cleaned = txt.rstrip('\x00').replace('\\', '/')
        return cleaned or "(decoded to an empty string)"

    except Exception as e:
        return f"An error occurred during decoding: {e}"


def find_effects_with_filepath(node, results_list):
    """
    Deeply recursive search through any AAF JSON node tree,
    collecting all 'Filepath' definitions and decoding them.
    """
    # Recurse dict values
    if isinstance(node, dict):
        for v in node.values():
            find_effects_with_filepath(v, results_list)
        return

    # Only lists represent meaningful AAF nodes
    if not isinstance(node, list) or not node:
        return

    # If this node defines a Filepath, decode it
    if node[0] == "Filepath":
        decoded = decode_filepath(node)
        results_list.append({
            "json_block": node,
            "decoded_path": decoded
        })

    # Always recurse into children if present
    children = node[3] if len(node) > 3 and isinstance(node[3], list) else []
    for child in children:
        find_effects_with_filepath(child, results_list)


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Effect Filepath Viewer")
        self.root.geometry("1000x700")

        tk.Button(
            root,
            text="Load AAF JSON File",
            command=self.load_and_process_file
        ).pack(pady=10)

        self.log_area = scrolledtext.ScrolledText(
            root,
            wrap=tk.WORD,
            font=("Courier New", 10)
        )
        self.log_area.pack(padx=10, pady=10, expand=True, fill=tk.BOTH)

    def log_message(self, message):
        """Appends a message to the log area."""
        self.log_area.insert(tk.END, message + "\n\n")
        self.log_area.see(tk.END)

    def load_and_process_file(self):
        """Loads a JSON file and processes it for Filepath entries."""
        file_path = filedialog.askopenfilename(
            title="Select AAF JSON File",
            filetypes=[("JSON Files", "*.json")]
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.log_area.delete(1.0, tk.END)

            found_effects = []
            find_effects_with_filepath(data, found_effects)

            if not found_effects:
                self.log_message(
                    "⚠️ No effects with a 'Filepath' property were found."
                )
                return

            self.log_message(
                f"✅ Found {len(found_effects)} 'Filepath' definition(s):"
            )

            for idx, effect in enumerate(found_effects, start=1):
                raw_json_str = json.dumps(
                    effect["json_block"],
                    indent=2
                )
                decoded_path = effect["decoded_path"]

                self.log_message(f"--- Entry #{idx} ---")
                self.log_message(
                    f"Raw JSON for 'Filepath':\n{raw_json_str}"
                )
                self.log_message(
                    f"Decoded path: {decoded_path}"
                )

        except Exception as e:
            messagebox.showerror(
                "Error",
                f"An error occurred:\n{e}"
            )


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
