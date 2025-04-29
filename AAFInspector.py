# ... (Keep all previous imports: __future__, sys, os, PySide6, aaf2) ...
from __future__ import (
    unicode_literals,
    absolute_import,
    print_function,
    division,
)
import sys
import os
from PySide6 import QtCore
from PySide6 import QtWidgets
from PySide6 import QtGui # Added for QAction if needed, good practice

# Try importing aaf2, handle potential ImportError
try:
    import aaf2
except ImportError:
    print("Error: aaf2 library not found.")
    print("Please install it using: pip install aaf2")
    sys.exit(1)


# --- Input Dialog Class (Unchanged from previous version) ---
class InputDialog(QtWidgets.QDialog):
    def __init__(self, default_options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AAFInspector")
        self.setMinimumWidth(400)

        self.filePath = ""
        # Make a copy to avoid modifying the original dict passed in
        self.options = default_options.copy()

        # --- Layouts ---
        mainLayout = QtWidgets.QVBoxLayout(self)
        fileLayout = QtWidgets.QHBoxLayout()
        optionsLayout = QtWidgets.QVBoxLayout()
        # buttonLayout = QtWidgets.QHBoxLayout() # Not needed with QDialogButtonBox

        # --- File Selection Widgets ---
        self.fileLabel = QtWidgets.QLabel("Select AAF:")
        self.filePathLineEdit = QtWidgets.QLineEdit()
        self.filePathLineEdit.setPlaceholderText("Path to AAF file...")
        self.browseButton = QtWidgets.QPushButton("Browse...")
        self.browseButton.clicked.connect(self.browseForFile)

        fileLayout.addWidget(self.fileLabel)
        fileLayout.addWidget(self.filePathLineEdit)
        fileLayout.addWidget(self.browseButton)

        # --- Options Widgets (Checkboxes) ---
        optionsGroup = QtWidgets.QGroupBox("Display Options")
        self.optionCheckboxes = {} # Dictionary to hold checkboxes

        # Define options with user-friendly labels (Incorporating your change)
        option_labels = {
            'toplevel': "Top-Level Composition Mobs", # Your label change
            'compmobs': "Composition Mobs",
            'mastermobs': "Master Mobs",
            'sourcemobs': "Source Mobs",
            'dictionary': "Dictionary",
            'metadict': "MetaDictionary",
            'root': "Root", # Your label change
        }

        # Ensure all keys from default_options are present (in case defaults change later)
        for key in default_options:
            if key not in option_labels:
                 option_labels[key] = f"Show {key.capitalize()}" # Auto-generate fallback label

        # Create checkboxes based on keys in default_options (respecting its order)
        # Or use sorted(default_options.keys()) for alphabetical order
        for key in default_options.keys(): # Iterate using the order from the passed dict
            label = option_labels.get(key, f"Show {key.capitalize()}") # Get label or generate
            checkbox = QtWidgets.QCheckBox(label)
            # Set initial state from the potentially modified copy of options
            checkbox.setChecked(self.options.get(key, False))
            self.optionCheckboxes[key] = checkbox
            optionsLayout.addWidget(checkbox)

        optionsGroup.setLayout(optionsLayout)

        # --- Standard Dialog Buttons ---
        self.buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        # --- Assemble Main Layout ---
        mainLayout.addLayout(fileLayout)
        mainLayout.addWidget(optionsGroup)
        mainLayout.addWidget(self.buttonBox)

    @QtCore.Slot()
    def browseForFile(self):
        """Opens a file dialog to select an AAF file."""
        # Suggest the directory of the currently selected file, if any
        start_dir = os.path.dirname(self.filePathLineEdit.text()) if self.filePathLineEdit.text() else ""
        filePath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select AAF File",
            start_dir,
            "AAF Files (*.aaf);;All Files (*)"
        )
        if filePath:
            self.filePathLineEdit.setText(filePath)

    def accept(self):
        """Override accept to validate input and store results."""
        selectedPath = self.filePathLineEdit.text().strip()
        if not selectedPath:
            QtWidgets.QMessageBox.warning(self, "Input Required", "Please select or enter an AAF file path.")
            return # Don't close the dialog
        if not os.path.exists(selectedPath):
             # Allow non-existent paths in case of network drives that might appear later?
             # Or keep strict check:
             QtWidgets.QMessageBox.warning(self, "File Not Found", f"The file '{selectedPath}' does not exist or is not accessible.")
             return # Don't close the dialog
        # Optional: Relax the .aaf check if users might have non-standard extensions
        # if not selectedPath.lower().endswith(".aaf"):
        #      reply = QtWidgets.QMessageBox.question(self, "Confirm File Type", ...) # As before

        self.filePath = selectedPath
        # Update options based on checkbox states before accepting
        for key, checkbox in self.optionCheckboxes.items():
            # Ensure the key exists in self.options before assignment
            if key in self.options:
                 self.options[key] = checkbox.isChecked()
            else:
                 print(f"Warning: Checkbox key '{key}' not found in internal options dict during accept.")


        super().accept() # Call the original accept method

    def getResults(self):
        """Returns the selected file path and options dictionary."""
        return self.filePath, self.options

# --- TreeItem Class (Unchanged) ---
class TreeItem(object):
    # ... (No changes needed in TreeItem) ...
    def __init__(self, item, parent=None, index=0):
        self.parentItem = parent
        self.item = item
        self.children = {}
        self.children_count = 0
        self.properties = {}
        self.loaded = False
        self.index = index
        self.references = []

    def columnCount(self):
        return 1

    def childCount(self):
        self.setup()
        return self.children_count

    def child(self, row):
        self.setup()
        if row in self.children:
            return self.children[row]

        if isinstance(self.item, aaf2.properties.StrongRefSetProperty):
            if row < len(self.references): # Bounds check
                key = self.references[row]
                item = self.item.get(key)
                t = TreeItem(item, self, row)
            else: return None # Invalid row index

        elif isinstance(self.item, aaf2.properties.StrongRefVectorProperty):
            # Check bounds for vector access
            if 0 <= row < len(self.item):
                item = self.item.get(row)
                t = TreeItem(item, self, row)
            else: return None # Invalid row index
        else:
            return None
        self.children[row] = t
        return t

    def childNumber(self):
        # Return the stored index
        return self.index

    def parent(self):
        # No setup needed here, parentItem is set in __init__
        return self.parentItem

    def extend(self, items):
        for i in items:
            index = self.children_count
            t = TreeItem(i, self, index)
            self.children[index] = t
            self.children_count += 1

    def name(self):
        item = self.item
        # Check for DummyItem first
        if isinstance(item, DummyItem):
             return item.name # Use the name defined in DummyItem

        if hasattr(item, 'name'):
            name = item.name
            if name:
                return name
        # Fallback for properties to use their definition name
        if isinstance(item, aaf2.properties.Property):
             if hasattr(item, 'propertydef') and hasattr(item.propertydef, 'name'):
                 return item.propertydef.name
        return self.class_name() # Fallback further to class name

    def class_name(self):
        item = self.item
         # Check for DummyItem first
        if isinstance(item, DummyItem):
             return item.class_name # Use the class_name defined in DummyItem


        if isinstance(item, aaf2.core.AAFObject):
            # Handle potential errors if classdef is missing (unlikely but safe)
            return getattr(getattr(item, 'classdef', None), 'class_name', 'UnknownAAFObject')
        if hasattr(item, "class_name"):
            return item.class_name
        # Ensure we always return a string
        return getattr(item, '__class__', type(None)).__name__

    def setup(self):
        if self.loaded:
            return

        item = self.item
        # Handle DummyItem - it acts as a container for its target
        if isinstance(item, DummyItem):
             self.extend([item.item]) # Add the actual target as the child
             self.properties['Name'] = self.name()
             self.properties['Class'] = self.class_name()
             self.loaded = True
             return # Nothing more to do for DummyItem container itself


        if isinstance(item, list):
            self.extend(item)

        if isinstance(item, aaf2.core.AAFObject):
            try:
                # Sort properties alphabetically by name for consistency
                props = sorted(list(item.properties()), key=lambda p: getattr(p, 'name', ''))
                self.extend(props)
            except Exception as e:
                 print(f"Error accessing properties for {self.name()}: {e}") # Handle potential errors


        elif isinstance(item, aaf2.properties.StrongRefProperty):
            if item.value: # Handle cases where the ref might be null
                self.extend([item.value])

        elif isinstance(item, aaf2.properties.StrongRefVectorProperty):
             try:
                 self.children_count = len(item)
             except Exception as e:
                  print(f"Error getting length of StrongRefVectorProperty {self.name()}: {e}")
                  self.children_count = 0


        elif isinstance(item, aaf2.properties.StrongRefSetProperty):
            try:
                self.children_count = len(item)
                # Sort references for consistent display (assuming they are comparable)
                try:
                    # Ensure keys are hashable and sortable if possible
                    keys = list(item.references.keys())
                    # Attempt to sort, fallback if keys are not comparable
                    try:
                        self.references = sorted(keys)
                    except TypeError:
                        self.references = keys # Keep original order if sorting fails
                except Exception as e:
                    print(f"Error processing references for {self.name()}: {e}")
                    self.references = []
                    self.children_count = 0
            except Exception as e:
                  print(f"Error getting length/references of StrongRefSetProperty {self.name()}: {e}")
                  self.children_count = 0
                  self.references = []


        elif isinstance(item, (aaf2.properties.Property)):
            try:
                # Limit long string values for better display
                v_raw = item.value
                if isinstance(v_raw, (str, bytes)) and len(v_raw) > 100:
                    # Use repr for clarity on type and potential non-printable chars
                    v = repr(v_raw[:100]) + "... (truncated)"
                elif isinstance(v_raw, (dict, list, tuple)) and len(str(v_raw)) > 100:
                     v = str(type(v_raw)) + " ... (truncated)" # Show type for collections
                else:
                     v = str(v_raw)
            except Exception as e: # Catch potential errors during value access/str conversion
                v = f"<Error accessing value: {type(e).__name__}>"
            self.properties['Value'] = v

        # Add slot and mob references as children for convenience
        if isinstance(item, aaf2.components.SourceClip):
             try:
                 mob = item.mob
                 if mob:
                     # Use DummyItem for clearer representation
                     self.extend([DummyItem("Source Mob Ref", mob)])
             except Exception as e:
                  print(f"Error accessing SourceClip.mob for {self.name()}: {e}")

             try:
                 slot = item.slot
                 if slot:
                     # Use DummyItem
                     self.extend([DummyItem("Source Slot Ref", slot)])
             except Exception as e:
                  print(f"Error accessing SourceClip.slot for {self.name()}: {e}")


        # Set properties after potentially extending children
        self.properties['Name'] = self.name()
        self.properties['Class'] = self.class_name()

        self.loaded = True

# --- DummyItem Class (Unchanged) ---
class DummyItem:
     # ... (No changes needed in DummyItem) ...
     def __init__(self, name, target_item):
         self._name = name
         self.item = target_item # The actual item this dummy points to

     @property
     def name(self):
         return self._name

     @property
     def class_name(self):
        # Delegate class name lookup to the actual target item
         target = self.item
         if isinstance(target, aaf2.core.AAFObject):
            # Handle potential errors if classdef is missing
             return getattr(getattr(target, 'classdef', None), 'class_name', 'UnknownAAFObject')
         if hasattr(target, "class_name"):
             return target.class_name
         # Ensure we always return a string
         return getattr(target, '__class__', type(None)).__name__

     def properties(self): # Make it behave somewhat like an AAFObject for the tree
         # Return the target item directly for the model to process
         return [self.item]


# --- AAFModel Class (Unchanged) ---
class AAFModel(QtCore.QAbstractItemModel):
    # ... (No changes needed in AAFModel) ...
    def __init__(self, root, parent=None):
        super(AAFModel, self).__init__(parent)
        # Wrap the root if it's a list (e.g., from toplevel(), etc.)
        # Create a root TreeItem that holds the list or single object
        self.rootItem = TreeItem(root, parent=None, index=0) # List/object passed to TreeItem constructor


        self.headers = ['Name', 'Value', 'Class']

    def headerData(self, section, orientation, role):
        if orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.headers):
                return self.headers[section]
        # Add tooltips for headers too
        elif orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.ToolTipRole:
             if 0 <= section < len(self.headers):
                 return f"Column: {self.headers[section]}"
        return None

    def columnCount(self, parent=QtCore.QModelIndex()): # Default parent is root
        # The number of columns is fixed based on headers
        return len(self.headers)

    def rowCount(self, parent=QtCore.QModelIndex()):
        parentItem = self.getItem(parent)
        # Ensure parentItem is valid before calling childCount
        return parentItem.childCount() if parentItem else 0

    def data(self, index, role):
        if not index.isValid():
            return None

        item = self.getItem(index)
        if not item: # Safety check
             return None

        # Ensure data is loaded before accessing properties, but only if needed for display roles
        if role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.ToolTipRole):
            item.setup() # Load data if not already loaded

        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            header_key = self.headers[index.column()]
            # Directly access properties dict, handle DummyItem within TreeItem's setup/properties
            return str(item.properties.get(header_key, ''))

        elif role == QtCore.Qt.ItemDataRole.ToolTipRole:
             header_key = self.headers[index.column()]
             # Provide tooltip for Name and Class columns showing the item's internal representation
             if header_key in ('Name', 'Class'):
                  try:
                       return repr(item.item)
                  except Exception:
                       return item.name() # Fallback tooltip
             # Provide full value as tooltip for Value column if it was truncated
             elif header_key == 'Value':
                  raw_value_str = item.properties.get('Value', '')
                  # Check if the display value indicates truncation
                  if raw_value_str.endswith("... (truncated)"):
                        try:
                           # Try to get the original full value string representation
                            original_value = getattr(item.item, 'value', None) if isinstance(item.item, aaf2.properties.Property) else None
                            return str(original_value) if original_value is not None else raw_value_str
                        except Exception:
                             return raw_value_str # Fallback to truncated string if error
                  # return None # No special tooltip if not truncated (or return the value itself?)
                  # Let's return the value itself for consistency
                  return raw_value_str

        return None # Default return for unhandled roles


    def parent(self, index):
        if not index.isValid():
            return QtCore.QModelIndex()

        childItem = self.getItem(index)
        if not childItem:
            return QtCore.QModelIndex()

        parentItem = childItem.parent()

        if parentItem is None or parentItem == self.rootItem:
            # This index belongs to a top-level item, its parent is the invisible root
            return QtCore.QModelIndex()

        # We need the row of parentItem within *its* parent (grandParentItem)
        # Use the index stored in TreeItem
        return self.createIndex(parentItem.childNumber(), 0, parentItem)


    def index(self, row, column, parent=QtCore.QModelIndex()):
         if not self.hasIndex(row, column, parent):
             return QtCore.QModelIndex()

         parentItem = self.getItem(parent)
         if not parentItem: # Should not happen if hasIndex passed, but safety first
              return QtCore.QModelIndex()


         childItem = parentItem.child(row) # child() handles loading if needed

         if childItem:
             # Create the index using the row, column, and the childItem pointer
             return self.createIndex(row, column, childItem)
         else:
             # This might happen if child data isn't loaded yet, index out of bounds, or error in child()
             # print(f"Warning: Could not get childItem for row {row} in parent {parentItem.name()}")
             return QtCore.QModelIndex()


    def getItem(self, index):
        if index.isValid():
            item = index.internalPointer()
            # Check if the internal pointer is a valid TreeItem instance
            if isinstance(item, TreeItem):
                return item
        # If index is invalid or pointer is not a TreeItem, return the root
        return self.rootItem


# --- Main Window Class (MODIFIED for Context Menu) ---
class Window(QtWidgets.QTreeView):
    # Add import for QMenu, QAction if not already covered by QtWidgets/QtGui
    # from PySide6.QtWidgets import QMenu (usually covered)
    # from PySide6.QtGui import QAction

    def __init__(self, parent=None):
        super().__init__(parent)
        self.resize(800, 700)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        # Enable context menu policy
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.showContextMenu) # Connect signal

        self.current_file_path = None
        self.current_options = {}
        self.aaf_file = None
        self.fs_watcher = None

    # --- Context Menu Implementation ---
    @QtCore.Slot(QtCore.QPoint)
    def showContextMenu(self, point):
        """Shows the context menu at the requested point."""
        # Create the menu only when requested
        menu = QtWidgets.QMenu(self)

        # Action to re-open the options dialog
        changeAction = QtGui.QAction("Change AAF/Options...", self) # Use QtGui.QAction
        changeAction.triggered.connect(self.showOptionsDialog)
        # Disable the action if no file is currently loaded
        changeAction.setEnabled(bool(self.current_file_path))
        menu.addAction(changeAction)

        # --- Add more actions as needed ---
        # Example: Expand All / Collapse All
        menu.addSeparator()
        expandAllAction = QtGui.QAction("Expand All", self)
        expandAllAction.triggered.connect(self.expandAll)
        expandAllAction.setEnabled(self.model() is not None) # Enable only if model exists
        menu.addAction(expandAllAction)

        collapseAllAction = QtGui.QAction("Collapse All", self)
        collapseAllAction.triggered.connect(self.collapseAll)
        collapseAllAction.setEnabled(self.model() is not None) # Enable only if model exists
        menu.addAction(collapseAllAction)

        # --- Execute the menu ---
        # Map the local point to global coordinates for the menu position
        globalPos = self.mapToGlobal(point)
        menu.exec(globalPos)

    @QtCore.Slot()
    def showOptionsDialog(self):
        """Creates and shows the InputDialog, pre-filled with current settings."""
        if not self.current_options or not self.current_file_path:
             # Should not happen if action is disabled correctly, but safety check
             print("No current file/options available to modify.")
             # Optionally show the dialog fresh?
             # dialog = InputDialog({}, self) # Pass empty dict for defaults?
             return

        # Create dialog with current options and self as parent
        dialog = InputDialog(self.current_options, self)
        # Pre-fill the file path line edit
        dialog.filePathLineEdit.setText(self.current_file_path)

        # Execute the dialog modally
        dialogResult = dialog.exec()

        if dialogResult == QtWidgets.QDialog.DialogCode.Accepted:
            # Get the potentially new file path and options
            new_file_path, new_options = dialog.getResults()
            print(f"Re-loading with new settings: {new_file_path}, {new_options}")
            # Reload the view using the existing loadAafFile method
            self.loadAafFile(new_file_path, new_options)
        else:
            print("Options dialog cancelled.")
    # --- End Context Menu Implementation ---

    def loadAafFile(self, file_path, options):
        """Loads or reloads the AAF file with given options."""
        if not file_path or not options:
             print("Error: Missing file path or options for loading.")
             # Clear the view if inputs are invalid
             self.setModel(None)
             self.setWindowTitle("AAF Viewer")
             self.current_file_path = None
             self.current_options = {}
             if self.aaf_file:
                  try: self.aaf_file.close()
                  except Exception: pass
                  self.aaf_file = None
             self.setupFileWatcher(None) # Stop watching
             return


        # Close previous file if open *before* updating current path/options
        if self.aaf_file and self.current_file_path != file_path: # Only close if file path changes
            try:
                print(f"Closing previous file: {self.current_file_path}")
                self.aaf_file.close()
            except Exception as e:
                print(f"Error closing previous file: {e}")
            self.aaf_file = None # Ensure it's reset


        # Update current state *before* trying to open
        self.current_file_path = file_path
        # Make sure to store a copy of options, not a reference if mutable
        self.current_options = options.copy()
        print(f"Attempting to load AAF: {file_path}")
        print(f"With options: {options}")


        try:
             # Re-open file only if it's not already open or path changed
            if not self.aaf_file: # Check if None (was closed or never opened)
                self.aaf_file = aaf2.open(file_path, 'r') # Open in read mode
                print(f"Successfully opened: {file_path}")

            # --- Determine root object (Simplified logic) ---
            f = self.aaf_file # Use the opened file object
            root_data = None
            option_map = { # Map option keys to methods/attributes
                'root': lambda f: f.root,
                'metadict': lambda f: f.metadict,
                'dictionary': lambda f: f.dictionary,
                'sourcemobs': lambda f: list(f.content.sourcemobs()),
                'mastermobs': lambda f: list(f.content.mastermobs()),
                'compmobs': lambda f: list(f.content.compositionmobs()),
                'toplevel': lambda f: list(f.content.toplevel()),
            }
            # Find the first selected option in a preferred order (e.g., specific to general)
            found_option = False
            for key in ['root', 'metadict', 'dictionary', 'sourcemobs', 'mastermobs', 'compmobs', 'toplevel']:
                if self.current_options.get(key):
                     try:
                          root_data = option_map[key](f)
                          print(f"Using root data from option: {key}")
                          found_option = True
                          break # Use the first selected option found in this order
                     except Exception as e:
                          print(f"Error getting root data for option {key}: {e}")
                          QtWidgets.QMessageBox.warning(self, "Data Error", f"Failed to retrieve data for option '{key}'.\nError: {e}")
                          # Fallback or clear view? Let's clear for now.
                          root_data = None
                          found_option = True # Mark as handled, even with error
                          break


            if not found_option:
                 # Fallback if somehow no relevant option is selected (should have defaults)
                 print("Warning: No specific view option selected, defaulting to ContentStorage.")
                 try:
                      root_data = f.content
                 except Exception as e:
                      print(f"Error accessing default f.content: {e}")
                      root_data = None


            # --- Set Model ---
            if root_data is not None:
                model = AAFModel(root_data)
                self.setModel(model)
                print("Model set successfully.")
                # Expand/collapse state can be reset or preserved here if needed
                self.expandToDepth(0) # Start collapsed after reload
            else:
                QtWidgets.QMessageBox.warning(self, "No Data", "Could not retrieve valid data to display based on selected options.")
                self.setModel(None) # Clear the view if no root data found/generated
                print("Setting model to None as root_data is None.")


            self.setWindowTitle(f"{os.path.basename(file_path)} - AAF Viewer")

            # Adjust column widths after model is potentially set or cleared
            self.resizeColumnToContents(0) # Name
            self.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch) # Value stretch
            self.resizeColumnToContents(2) # Class
            self.header().setStretchLastSection(False)


            # Setup or update file watcher for the potentially new path
            self.setupFileWatcher(file_path)


        except FileNotFoundError:
             QtWidgets.QMessageBox.critical(self, "Error", f"File not found:\n{file_path}")
             self.setWindowTitle("AAF Viewer - File Not Found")
             self.setModel(None)
             self.aaf_file = None # Ensure file handle is cleared
             self.current_file_path = None # Clear current path on failure
             self.setupFileWatcher(None) # Stop watching
             return # Stop further processing
        except Exception as e:
            # Catch other potential errors during aaf2.open or data access
            print(f"An unexpected error occurred during loading: {e}")
            QtWidgets.QMessageBox.critical(self, "Error Loading File", f"Could not process AAF file:\n{file_path}\n\nError: {str(e)}")
            self.setWindowTitle(f"AAF Viewer - Error loading {os.path.basename(file_path)}")
            self.setModel(None)
            if self.aaf_file: # Try to close if opened partially before error
                try: self.aaf_file.close()
                except Exception: pass
            self.aaf_file = None
            self.current_file_path = None # Clear current path on failure
            self.setupFileWatcher(None) # Stop watching
            return # Stop further processing


    @QtCore.Slot(str)
    def fileChangedHandler(self, path):
        """Handles the signal from QFileSystemWatcher."""
        # Check if the changed path is the one we are currently displaying
        if path == self.current_file_path:
            print(f"Detected change in: {path}")
            # Make the reload prompt non-blocking? Maybe just reload automatically?
            # For now, keep the prompt:
            reply = QtWidgets.QMessageBox.question(self, "File Changed",
                                                   f"The file '{os.path.basename(path)}' has been modified outside the application.\nDo you want to reload it?",
                                                   QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                                                   QtWidgets.QMessageBox.StandardButton.Yes)
            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                print("Reloading file due to external change...")
                # Reload with the same options
                self.loadAafFile(self.current_file_path, self.current_options)
            else:
                print("User chose not to reload.")
                # Re-add the path to the watcher explicitly, as it might be needed on some OSes
                self.setupFileWatcher(self.current_file_path) # Re-establish watch

        else:
            # This case might occur if the watcher is watching multiple files or
            # if the current_file_path was cleared due to an error but watcher wasn't updated.
            print(f"Ignoring change signal for path not currently loaded: {path}")
            # Optionally remove the path from watcher if it's unexpected
            if self.fs_watcher and path in self.fs_watcher.files():
                 self.fs_watcher.removePaths([path])


    def setupFileWatcher(self, file_path):
        """Sets up or resets the file system watcher for a single path."""
        # Ensure watcher exists
        if not self.fs_watcher:
             self.fs_watcher = QtCore.QFileSystemWatcher(self)
             # Connect signal only once when watcher is created
             try:
                  # Use a lambda to ignore the path argument if not needed,
                  # or connect directly if handler uses it.
                  # self.fs_watcher.fileChanged.connect(lambda p: self.fileChangedHandler(p))
                  self.fs_watcher.fileChanged.connect(self.fileChangedHandler)
             except (TypeError, RuntimeError) as e:
                  print(f"Error connecting file watcher signal initially: {e}")

        # Remove any currently watched paths
        current_paths = self.fs_watcher.files()
        if current_paths:
            self.fs_watcher.removePaths(current_paths)
            # print(f"Removed paths from watcher: {current_paths}")

        # Add the new path if it's valid
        if file_path and os.path.exists(file_path): # Check existence before adding
            if self.fs_watcher.addPath(file_path):
                 print(f"Now watching path: {file_path}")
            else:
                 print(f"Warning: Failed to add path to watcher: {file_path}")
        # elif file_path: # Log if path doesn't exist
        #      print(f"Info: Cannot watch file path (does not exist): {file_path}")



    def closeEvent(self, event):
        """Ensure the AAF file is closed when the window closes."""
        print("Close event triggered for main window.")
        if self.aaf_file:
            try:
                self.aaf_file.close()
                print(f"Closed AAF file on exit: {self.current_file_path}")
            except Exception as e:
                print(f"Error closing AAF file on exit: {e}")
        # Clean up watcher? QFileSystemWatcher is parented, should be auto-deleted.
        super().closeEvent(event) # Proceed with closing


# --- Main Execution Block (Unchanged from previous version) ---
if __name__ == "__main__":

    app = QtWidgets.QApplication(sys.argv)
    # Set application info for better integration (optional)
    app.setApplicationName("AAFInspector")
    

    # --- Default options for the initial dialog ---
    default_options = {
        'toplevel': False,
        'compmobs': True, # Often the most useful starting point
        'mastermobs': False,
        'sourcemobs': False,
        'dictionary': False,
        'metadict': False,
        'root': False,
    }

    # --- Show Initial Input Dialog ---
    # Pass defaults and None for parent (it's the first window)
    initial_dialog = InputDialog(default_options, parent=None)
    dialogResult = initial_dialog.exec()

    if dialogResult == QtWidgets.QDialog.DialogCode.Accepted:
        # Get results from the initial dialog
        selected_file_path, selected_options = initial_dialog.getResults()

        # --- Create and Show Main Window ---
        # Create window with no parent
        window = Window(parent=None)
        window.loadAafFile(selected_file_path, selected_options) # Load initial data
        window.show() # Show the main window

        # Start the application event loop only if dialog was accepted
        sys.exit(app.exec())
    else:
        # User cancelled the initial dialog
        print("Operation cancelled by user at startup.")
        sys.exit(0) # Exit gracefully