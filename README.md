# AAFInspector
Small GUI utility for viewing the internal structure of Advanced Authoring Files (AAFs).

This code is basically an exact copy of [this script](https://github.com/markreidvfx/pyaaf2/blob/main/examples/qt_aafmodel.py) from the pyaaf2 repository by markreidvfx, with some differences:

![](https://github.com/user-attachments/assets/ded12e1b-d6a9-4f2f-9bb6-9e40c7315385)![](https://github.com/user-attachments/assets/3757b934-cd82-428f-afb8-71d143fed652)




- Code has been refactored from PySide2 to PySide6, ensuring compatibility with the latest Python versions (as at 2025).
- Additional dialog has been added to allow you to select an AAF file and set viewing preferences within the GUI, rather than in the `if __name__ == '__main__'` block.
- A right-click context menu has been implemented allowing you to collapse/expand all fields and go back to the file selection dialog.

