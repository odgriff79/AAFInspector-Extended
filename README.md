# AAFInspector
Small GUI utility for viewing the internal structure of Advanced Authoring Files (AAFs). 


![](https://github.com/user-attachments/assets/ded12e1b-d6a9-4f2f-9bb6-9e40c7315385)![](https://github.com/user-attachments/assets/3757b934-cd82-428f-afb8-71d143fed652)

This code is basically an exact copy of [this script](https://github.com/markreidvfx/pyaaf2/blob/main/examples/qt_aafmodel.py) from the pyaaf2 repository by markreidvfx, with some differences:
- Code has been refactored from PySide2 to PySide6, ensuring compatibility with the latest Python versions (as at 2025).
- Additional dialog has been added to allow you to select an AAF file and set viewing preferences within the GUI, rather than in the `if __name__ == '__main__'` block.
- A right-click context menu has been implemented allowing you to collapse/expand all fields and go back to the file selection dialog.

## AAF Object Model TL;DR:

This diagram shows a high-level overview of the inner workings of the AAF file structure. Note the multiple nested squares in the diagram - these symbolise how objects are nested within the file:

![](https://github.com/user-attachments/assets/d1540d43-8a81-4e8a-87be-363e11fca9e3)

### Display Options

- **Top-Level Composition Mobs:** Equivalent to regular sequences in Avid Media Composer.
- **Composition Mobs:** Will display all CompositionMobs in the AAF. These include top-level composition mobs (sequences) and lower-level composition mobs (groups and subclips).
- **Master Mobs:** Equivalent to master clips and/or AMA-linked clips in Avid.
- **Source Mobs**: In modern digital workflows, these represent physical files, although they can also be used to represent physical sources from which essence is derived, like tape or film. For example, when you AMA link a clip, you will have 1 source mob, then when you transcode it within Avid, you will have 1 source mob per OP-Atom file. *Hint: Source timecode will often originate here.*

