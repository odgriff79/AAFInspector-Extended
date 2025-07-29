#!/usr/bin/env python3
"""
Local Desktop GUI for the AAF to DaVinci Resolve FCPXML Converter.
Emulates the functionality of the Streamlit web app for easy local testing on Windows.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import traceback
import threading

# --- Import your existing conversion logic ---
# Make sure these files are in the same directory as this script
try:
    from unified_aaf_parser import parse_aaf_unified
    from resolve_xml_generator_v7 import ResolveXMLGeneratorV7
except ImportError as e:
    messagebox.showerror(
        "Dependency Error",
        f"Could not import required scripts. Make sure 'unified_aaf_parser.py' and "
        f"'resolve_xml_generator_v7.py' are in the same folder as this app.\n\nError: {e}"
    )
    exit()

class ConverterApp:
    """A simple desktop GUI for the AAF to FCPXML conversion process."""

    def __init__(self, root):
        """Initialize the application window and widgets."""
        self.root = root
        self.root.title("AAF to FCPXML Converter (Local Test App)")
        self.root.geometry("800x600")

        self.input_aaf_path = None

        # --- Main Frame ---
        main_frame = tk.Frame(root, padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- File Selection Section ---
        file_frame = tk.Frame(main_frame)
        file_frame.pack(fill=tk.X, pady=(0, 10))

        self.select_button = tk.Button(file_frame, text="1. Select AAF File", command=self.select_aaf_file, font=("Helvetica", 10, "bold"))
        self.select_button.pack(side=tk.LEFT, padx=(0, 10))

        self.file_label = tk.Label(file_frame, text="No file selected", fg="gray", anchor="w")
        self.file_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # --- Conversion Button ---
        self.convert_button = tk.Button(main_frame, text="2. Convert to FCPXML", command=self.start_conversion_thread, state=tk.DISABLED, bg="#4CAF50", fg="white", font=("Helvetica", 12, "bold"), relief=tk.RAISED)
        self.convert_button.pack(fill=tk.X, pady=10, ipady=5)

        # --- Status/Log Display ---
        log_label = tk.Label(main_frame, text="Conversion Log & Results:", font=("Helvetica", 10, "bold"), anchor="w")
        log_label.pack(fill=tk.X)

        self.log_area = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=20, state=tk.DISABLED, bg="#f0f0f0")
        self.log_area.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        self.log_message("Welcome! Please select an AAF file to begin.")

    def log_message(self, message):
        """Appends a message to the log area."""
        self.log_area.config(state=tk.NORMAL)
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)  # Auto-scroll to the bottom
        self.log_area.config(state=tk.DISABLED)
        self.root.update_idletasks()

    def select_aaf_file(self):
        """Opens a file dialog to select an AAF file."""
        path = filedialog.askopenfilename(
            title="Select an AAF file",
            filetypes=[("AAF Files", "*.aaf")]
        )
        if path:
            self.input_aaf_path = path
            filename = os.path.basename(path)
            self.file_label.config(text=filename, fg="black")
            self.convert_button.config(state=tk.NORMAL)
            self.log_message(f"Selected AAF: {filename}")
            self.log_message("Ready to convert. Click the 'Convert' button.")

    def start_conversion_thread(self):
        """Starts the conversion in a separate thread to keep the GUI responsive."""
        self.convert_button.config(state=tk.DISABLED, text="Converting...")
        self.log_area.config(state=tk.NORMAL)
        self.log_area.delete('1.0', tk.END) # Clear previous logs
        self.log_area.config(state=tk.DISABLED)
        
        # Run the conversion in a new thread
        conversion_thread = threading.Thread(target=self.run_conversion)
        conversion_thread.daemon = True # Allows app to exit even if thread is running
        conversion_thread.start()

    def run_conversion(self):
        """The main conversion logic that runs in a separate thread."""
        try:
            if not self.input_aaf_path:
                messagebox.showerror("Error", "No input AAF file selected.")
                return

            # --- Prompt for output file location ---
            output_path = filedialog.asksaveasfilename(
                title="Save FCPXML as...",
                defaultextension=".fcpxml",
                initialfile=f"{os.path.splitext(os.path.basename(self.input_aaf_path))[0]}.fcpxml",
                filetypes=[("Final Cut Pro XML", "*.fcpxml")]
            )

            if not output_path:
                self.log_message("Conversion cancelled by user.")
                self.convert_button.config(state=tk.NORMAL, text="2. Convert to FCPXML")
                return

            self.log_message("--- Starting Conversion ---")
            
            # --- Step 1: Parse the AAF file ---
            self.log_message("Step 1/3: Parsing AAF file...")
            aaf_data = parse_aaf_unified(self.input_aaf_path)
            clips_found = len(aaf_data.get('clips', []))
            self.log_message(f"   ✅ AAF parsing complete. Found {clips_found} clips.")

            # --- Step 2: Generate the FCPXML content ---
            self.log_message("Step 2/3: Generating FCPXML...")
            generator = ResolveXMLGeneratorV7()
            xml_content = generator.generate_xml(aaf_data)
            self.log_message("   ✅ FCPXML generation complete.")

            # --- Step 3: Write the output file ---
            self.log_message(f"Step 3/3: Writing output to file...")
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(xml_content)
            self.log_message(f"   ✅ Successfully saved: {os.path.basename(output_path)}")

            # --- Display Final Statistics ---
            self.log_message("\n--- ✨ Conversion Successful! ✨ ---")
            clips_with_keyframes = sum(1 for c in aaf_data.get('clips', []) if c.get('has_keyframes'))
            total_keyframes = sum(len(v) for c in aaf_data.get('clips', []) for v in c.get('keyframe_data', {}).values() if isinstance(v, list))
            
            stats = (
                f"  - Clips Found: {clips_found}\n"
                f"  - Clips with Animation: {clips_with_keyframes}\n"
                f"  - Total Keyframes Processed: {total_keyframes}\n"
                f"  - Filler/Gap Effects: {len(aaf_data.get('filler_effects', []))}\n"
                f"  - Sequence Name: {aaf_data.get('composition_info', {}).get('name', 'N/A')}"
            )
            self.log_message("Final Statistics:\n" + stats)
            
            messagebox.showinfo("Success", f"Conversion complete!\nFCPXML saved to:\n{output_path}")

        except Exception as e:
            error_message = f"❌ An error occurred during conversion:\n{e}"
            self.log_message("\n--- 🚨 ERROR 🚨 ---\n" + error_message)
            self.log_message("\n--- Error Details ---\n" + traceback.format_exc())
            messagebox.showerror("Conversion Failed", error_message)
        finally:
            # Re-enable the button regardless of outcome
            self.convert_button.config(state=tk.NORMAL, text="2. Convert to FCPXML")

if __name__ == "__main__":
    # Ensure all necessary Python libraries are installed
    try:
        import tkinter
    except ImportError:
        print("ERROR: tkinter is not installed. Please install it to run the GUI.")
        print("On Debian/Ubuntu: sudo apt-get install python3-tk")
        print("On Windows/macOS, it should be included with Python.")
        exit()

    root = tk.Tk()
    app = ConverterApp(root)
    root.mainloop()
