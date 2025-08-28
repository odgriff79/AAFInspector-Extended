from __future__ import (
    unicode_literals,
    absolute_import,
    print_function,
    division,
)
import sys
import os
import json
import datetime
import uuid

from PySide6 import QtCore
from PySide6 import QtWidgets
from PySide6 import QtGui

try:
    import aaf2
except ImportError:
    print("Error: aaf2 library not found.")
    print("Please ensure the local 'aaf2' folder is in the same directory as this script.")
    sys.exit(1)


class InputDialog(QtWidgets.QDialog):
    def __init__(self, default_options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AAFInspector")
        self.setMinimumWidth(400)

        self.filePath = ""
        self.options = default_options.copy()

        mainLayout = QtWidgets.QVBoxLayout(self)
        fileLayout = QtWidgets.QHBoxLayout()
        optionsLayout = QtWidgets.QVBoxLayout()

        self.fileLabel = QtWidgets.QLabel("Select AAF:")
        self.filePathLineEdit = QtWidgets.QLineEdit()
        self.filePathLineEdit.setPlaceholderText("Path to AAF file...")
        self.browseButton = QtWidgets.QPushButton("Browse...")
        self.browseButton.clicked.connect(self.browseForFile)

        fileLayout.addWidget(self.fileLabel)
        fileLayout.addWidget(self.filePathLineEdit)
        fileLayout.addWidget(self.browseButton)

        optionsGroup = QtWidgets.QGroupBox("Display Options")
        self.optionCheckboxes = {}

        option_labels = {
            'toplevel': "Top-Level Composition Mobs",
            'compmobs': "Composition Mobs",
            'mastermobs': "Master Mobs",
            'sourcemobs': "Source Mobs",
            'dictionary': "Dictionary",
            'metadict': "MetaDictionary",
            'root': "Root",
        }

        for key in default_options:
            if key not in option_labels:
                 option_labels[key] = f"Show {key.capitalize()}"

        for key in default_options.keys():
            label = option_labels.get(key, f"Show {key.capitalize()}")
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.setChecked(self.options.get(key, False))
            self.optionCheckboxes[key] = checkbox
            optionsLayout.addWidget(checkbox)

        optionsGroup.setLayout(optionsLayout)

        self.buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        mainLayout.addLayout(fileLayout)
        mainLayout.addWidget(optionsGroup)
        mainLayout.addWidget(self.buttonBox)

    @QtCore.Slot()
    def browseForFile(self):
        start_dir = os.path.dirname(self.filePathLineEdit.text()) if self.filePathLineEdit.text() else ""
        filePath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select AAF File", start_dir, "AAF Files (*.aaf);;All Files (*)"
        )
        if filePath:
            self.filePathLineEdit.setText(filePath)

    def accept(self):
        selectedPath = self.filePathLineEdit.text().strip()
        if not selectedPath:
            QtWidgets.QMessageBox.warning(self, "Input Required", "Please select or enter an AAF file path.")
            return
        if not os.path.exists(selectedPath):
             QtWidgets.QMessageBox.warning(self, "File Not Found", f"The file '{selectedPath}' does not exist or is not accessible.")
             return
        self.filePath = selectedPath
        for key, checkbox in self.optionCheckboxes.items():
            if key in self.options:
                 self.options[key] = checkbox.isChecked()
        super().accept()

    def getResults(self):
        return self.filePath, self.options

class TreeItem(object):
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
            if row < len(self.references):
                key = self.references[row]
                item = self.item.get(key)
                t = TreeItem(item, self, row)
            else: return None
        elif isinstance(self.item, aaf2.properties.StrongRefVectorProperty):
            if 0 <= row < len(self.item):
                item = self.item.get(row)
                t = TreeItem(item, self, row)
            else: return None
        else:
            return None
        self.children[row] = t
        return t

    def childNumber(self):
        return self.index

    def parent(self):
        return self.parentItem

    def extend(self, items):
        for i in items:
            index = self.children_count
            t = TreeItem(i, self, index)
            self.children[index] = t
            self.children_count += 1

    def name(self):
        item = self.item
        if isinstance(item, DummyItem):
             return item.name
        if hasattr(item, 'name'):
            name = item.name
            if name:
                return name
        if isinstance(item, aaf2.properties.Property):
             if hasattr(item, 'propertydef') and hasattr(item.propertydef, 'name'):
                 return item.propertydef.name
        return self.class_name()

    def class_name(self):
        item = self.item
        if isinstance(item, DummyItem):
             return item.class_name
        if isinstance(item, aaf2.core.AAFObject):
            return getattr(getattr(item, 'classdef', None), 'name', 'UnknownAAFObject')
        if hasattr(item, "class_name"):
            return item.class_name
        return getattr(item, '__class__', type(None)).__name__

    def setup(self):
        if self.loaded:
            return
        item = self.item
        if isinstance(item, DummyItem):
             self.extend([item.item])
             self.properties['Name'] = self.name()
             self.properties['Class'] = self.class_name()
             self.loaded = True
             return
        if isinstance(item, list):
            self.extend(item)
        if isinstance(item, aaf2.core.AAFObject):
            try:
                props = sorted(list(item.properties()), key=lambda p: getattr(p, 'name', ''))
                self.extend(props)
            except Exception as e:
                 print(f"Error accessing properties for {self.name()}: {e}")
        elif isinstance(item, aaf2.properties.StrongRefProperty):
            if item.value:
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
                try:
                    keys = list(item.references.keys())
                    try:
                        self.references = sorted(keys)
                    except TypeError:
                        self.references = keys
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
                v_raw = item.value
                if isinstance(v_raw, (str, bytes)) and len(v_raw) > 100:
                    v = repr(v_raw[:100]) + "... (truncated)"
                elif isinstance(v_raw, (dict, list, tuple)) and len(str(v_raw)) > 100:
                     v = str(type(v_raw)) + " ... (truncated)"
                else:
                     v = str(v_raw)
            except Exception as e:
                v = f"<Error accessing value: {type(e).__name__}>"
            self.properties['Value'] = v
        if hasattr(item, 'mob') and hasattr(item, 'slot'):
             try:
                 mob = item.mob
                 if mob:
                     self.extend([DummyItem("Source Mob Ref", mob)])
             except Exception as e:
                  print(f"Error accessing mob for {self.name()}: {e}")
             try:
                 slot = item.slot
                 if slot:
                     self.extend([DummyItem("Source Slot Ref", slot)])
             except Exception as e:
                  print(f"Error accessing slot for {self.name()}: {e}")

        self.properties['Name'] = self.name()
        self.properties['Class'] = self.class_name()
        self.loaded = True

class DummyItem:
     def __init__(self, name, target_item):
         self._name = name
         self.item = target_item
     @property
     def name(self):
         return self._name
     @property
     def class_name(self):
         target = self.item
         if isinstance(target, aaf2.core.AAFObject):
             return getattr(getattr(target, 'classdef', None), 'name', 'UnknownAAFObject')
         if hasattr(target, "class_name"):
             return target.class_name
         return getattr(target, '__class__', type(None)).__name__
     def properties(self):
         return [self.item]

class AAFModel(QtCore.QAbstractItemModel):
    def __init__(self, root, parent=None):
        super(AAFModel, self).__init__(parent)
        self.rootItem = TreeItem(root, parent=None, index=0)
        self.headers = ['Name', 'Value', 'Class']

    def headerData(self, section, orientation, role):
        if orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.headers):
                return self.headers[section]
        elif orientation == QtCore.Qt.Orientation.Horizontal and role == QtCore.Qt.ItemDataRole.ToolTipRole:
             if 0 <= section < len(self.headers):
                 return f"Column: {self.headers[section]}"
        return None

    def columnCount(self, parent=QtCore.QModelIndex()):
        return len(self.headers)

    def rowCount(self, parent=QtCore.QModelIndex()):
        parentItem = self.getItem(parent)
        return parentItem.childCount() if parentItem else 0

    def data(self, index, role):
        if not index.isValid():
            return None
        item = self.getItem(index)
        if not item:
             return None
        if role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.ToolTipRole):
            item.setup()
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            header_key = self.headers[index.column()]
            return str(item.properties.get(header_key, ''))
        elif role == QtCore.Qt.ItemDataRole.ToolTipRole:
             header_key = self.headers[index.column()]
             if header_key in ('Name', 'Class'):
                  try:
                       return repr(item.item)
                  except Exception:
                       return item.name()
             elif header_key == 'Value':
                  raw_value_str = item.properties.get('Value', '')
                  if raw_value_str.endswith("... (truncated)"):
                        try:
                            original_value = getattr(item.item, 'value', None) if isinstance(item.item, aaf2.properties.Property) else None
                            return str(original_value) if original_value is not None else raw_value_str
                        except Exception:
                             return raw_value_str
                  return raw_value_str
        return None

    def parent(self, index):
        if not index.isValid():
            return QtCore.QModelIndex()
        childItem = self.getItem(index)
        if not childItem:
            return QtCore.QModelIndex()
        parentItem = childItem.parent()
        if parentItem is None or parentItem == self.rootItem:
            return QtCore.QModelIndex()
        return self.createIndex(parentItem.childNumber(), 0, parentItem)

    def index(self, row, column, parent=QtCore.QModelIndex()):
         if not self.hasIndex(row, column, parent):
             return QtCore.QModelIndex()
         parentItem = self.getItem(parent)
         if not parentItem:
              return QtCore.QModelIndex()
         childItem = parentItem.child(row)
         if childItem:
             return self.createIndex(row, column, childItem)
         else:
             return QtCore.QModelIndex()

    def getItem(self, index):
        if index.isValid():
            item = index.internalPointer()
            if isinstance(item, TreeItem):
                return item
        return self.rootItem

class SearchDialog(QtWidgets.QDialog):
    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self.setWindowTitle("Find")
        self.setMinimumWidth(350)
        
        layout = QtWidgets.QVBoxLayout(self)
        form_layout = QtWidgets.QHBoxLayout()
        
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Search for (case-insensitive)...")
        
        self.find_next_button = QtWidgets.QPushButton("Find Next")
        self.find_prev_button = QtWidgets.QPushButton("Find Previous")
        
        form_layout.addWidget(self.search_input)
        form_layout.addWidget(self.find_next_button)
        form_layout.addWidget(self.find_prev_button)
        
        layout.addLayout(form_layout)
        
        self.search_input.returnPressed.connect(self.find_next)
        self.find_next_button.clicked.connect(self.find_next)
        self.find_prev_button.clicked.connect(self.find_previous)
        
    def find_next(self):
        search_term = self.search_input.text()
        if search_term:
            self.parent_window.find_next(search_term)

    def find_previous(self):
        search_term = self.search_input.text()
        if search_term:
            self.parent_window.find_previous(search_term)

    def closeEvent(self, event):
        self.hide()
        event.ignore()


class Window(QtWidgets.QTreeView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.resize(800, 700)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.showContextMenu)
        
        self.current_file_path = None
        self.current_options = {}
        self.aaf_file = None
        self.fs_watcher = None
        
        self.search_dialog = None
        self.search_results = []
        self.current_search_index = -1
        self.last_search_term = ""

    @QtCore.Slot(QtCore.QPoint)
    def showContextMenu(self, point):
        menu = QtWidgets.QMenu(self)
        model_is_loaded = self.model() is not None

        changeAction = QtGui.QAction("Change AAF/Options...", self)
        changeAction.triggered.connect(self.showOptionsDialog)
        changeAction.setEnabled(bool(self.current_file_path))
        menu.addAction(changeAction)
        menu.addSeparator()

        findAction = QtGui.QAction("Find...", self)
        findAction.setShortcut(QtGui.QKeySequence.StandardKey.Find)
        findAction.triggered.connect(self.open_search_dialog)
        findAction.setEnabled(model_is_loaded)
        menu.addAction(findAction)

        exportJsonAction = QtGui.QAction("Export to JSON...", self)
        exportJsonAction.triggered.connect(self.exportToJson)
        exportJsonAction.setEnabled(model_is_loaded)
        menu.addAction(exportJsonAction)
        
        menu.addSeparator()
        
        expandAllAction = QtGui.QAction("Expand All", self)
        expandAllAction.triggered.connect(self.expand_all_recursive)
        expandAllAction.setEnabled(model_is_loaded)
        menu.addAction(expandAllAction)
        
        collapseAllAction = QtGui.QAction("Collapse All", self)
        collapseAllAction.triggered.connect(self.collapseAll)
        collapseAllAction.setEnabled(model_is_loaded)
        menu.addAction(collapseAllAction)
        
        globalPos = self.mapToGlobal(point)
        menu.exec(globalPos)

    @QtCore.Slot()
    def showOptionsDialog(self):
        if not self.current_options or not self.current_file_path:
             print("No current file/options available to modify.")
             return

        dialog = InputDialog(self.current_options, self)
        dialog.filePathLineEdit.setText(self.current_file_path)

        dialogResult = dialog.exec()

        if dialogResult == QtWidgets.QDialog.DialogCode.Accepted:
            new_file_path, new_options = dialog.getResults()
            print(f"Re-loading with new settings: {new_file_path}, {new_options}")
            self.loadAafFile(new_file_path, new_options)
        else:
            print("Options dialog cancelled.")

    @QtCore.Slot()
    def open_search_dialog(self):
        if self.search_dialog is None:
            self.search_dialog = SearchDialog(self)
        self.search_dialog.show()
        self.search_dialog.activateWindow()
        self.search_dialog.raise_()

    def _perform_search(self, search_term):
        print(f"Performing new search for: '{search_term}'")
        self.last_search_term = search_term
        self.search_results.clear()
        self.current_search_index = -1
        model = self.model()
        if not model:
            return
        self._search_recursive(QtCore.QModelIndex(), search_term.lower())
        if not self.search_results:
            QtWidgets.QMessageBox.information(self, "Search", f"Term '{search_term}' not found.")
        else:
            print(f"Found {len(self.search_results)} match(es).")

    def _search_recursive(self, parent_index, search_term):
        model = self.model()
        for r in range(model.rowCount(parent_index)):
            for c in range(model.columnCount(parent_index)):
                index = model.index(r, c, parent_index)
                text = str(model.data(index, QtCore.Qt.ItemDataRole.DisplayRole)).lower()
                if search_term in text:
                    self.search_results.append(model.index(r, 0, parent_index))
                    break
            index = model.index(r, 0, parent_index)
            if model.hasChildren(index):
                self._search_recursive(index, search_term)

    def _navigate_results(self):
        if not self.search_results: return
        index = self.search_results[self.current_search_index]
        parent = index.parent()
        while parent.isValid():
            self.expand(parent)
            parent = parent.parent()
        self.scrollTo(index, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)
        self.setCurrentIndex(index)

    def find_next(self, search_term):
        if not search_term: return
        if search_term.lower() != self.last_search_term:
            self._perform_search(search_term)
            if not self.search_results: return
        if not self.search_results: return
        self.current_search_index += 1
        if self.current_search_index >= len(self.search_results):
            self.current_search_index = 0
        self._navigate_results()

    def find_previous(self, search_term):
        if not search_term: return
        if search_term.lower() != self.last_search_term:
            self._perform_search(search_term)
            if not self.search_results: return
        if not self.search_results: return
        self.current_search_index -= 1
        if self.current_search_index < 0:
            self.current_search_index = len(self.search_results) - 1
        self._navigate_results()

    @QtCore.Slot()
    def exportToJson(self):
        model = self.model()
        if not model or not model.rootItem:
            QtWidgets.QMessageBox.warning(self, "Export Error", "No data to export.")
            return
        default_path = os.path.splitext(self.current_file_path)[0] + ".json" if self.current_file_path else "export.json"
        filePath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export AAF to JSON", default_path, "JSON Files (*.json);;All Files (*)"
        )
        if not filePath: return
        try:
            print("Starting JSON export...")
            json_data = self._convert_node_to_dict(model.rootItem)
            print(f"Writing JSON data to: {filePath}")
            with open(filePath, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=4, ensure_ascii=False)
            QtWidgets.QMessageBox.information(self, "Export Successful", f"Data successfully exported to:\n{filePath}")
            print("JSON export finished successfully.")
        except Exception as e:
            print(f"Error during JSON export: {e}")
            QtWidgets.QMessageBox.critical(self, "Export Failed", f"An error occurred while exporting to JSON.\n\nError: {e}")

    def _convert_node_to_dict(self, tree_item):
        tree_item.setup()
        name = tree_item.name()
        class_name = tree_item.class_name()
        data = {"name": name, "class": class_name}
        if "Value" in tree_item.properties:
            raw_value = (tree_item.item.value if isinstance(tree_item.item, aaf2.properties.Property)
                         else tree_item.properties.get("Value"))
            data["value"] = self._serialize_json_value(raw_value)
        children = []
        for i in range(tree_item.childCount()):
            child = tree_item.child(i)
            if not child: continue
            child_dict = self._convert_node_to_dict(child)
            if child_dict: children.append(child_dict)
        if children:
            data["children"] = children
        return data

    def _serialize_json_value(self, value):
        if isinstance(value, (str, int, float, bool, type(None))): return value
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)): return value.isoformat()
        if isinstance(value, uuid.UUID): return str(value)
        if isinstance(value, bytes): return repr(value)
        if isinstance(value, dict): return {str(k): self._serialize_json_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)): return [self._serialize_json_value(v) for v in value]
        return str(value)

    @QtCore.Slot()
    def expand_all_recursive(self):
        """Custom method to recursively expand all items in a lazy-loading model."""
        print("Expanding all items...")
        self.setUpdatesEnabled(False)
        
        items_to_expand = [QtCore.QModelIndex()]
        
        head = 0
        while head < len(items_to_expand):
            parent_index = items_to_expand[head]
            head += 1
            
            model = self.model()
            if not model: continue
            
            for r in range(model.rowCount(parent_index)):
                child_index = model.index(r, 0, parent_index)
                if model.hasChildren(child_index):
                    items_to_expand.append(child_index)

        for index in reversed(items_to_expand):
            if index.isValid():
                self.expand(index)

        self.setUpdatesEnabled(True)
        print("Finished expanding.")

    def loadAafFile(self, file_path, options):
        if not file_path or not options:
             self.setModel(None); self.setWindowTitle("AAFInspector"); self.current_file_path = None; return
        if self.aaf_file and self.current_file_path != file_path:
            try: self.aaf_file.close()
            except Exception as e: print(f"Error closing previous file: {e}")
            self.aaf_file = None
        self.current_file_path = file_path
        self.current_options = options.copy()
        try:
            if not self.aaf_file: self.aaf_file = aaf2.open(file_path, 'r')
            f = self.aaf_file
            
            root_items = []
            option_map = {
                'toplevel': lambda f: list(f.content.toplevel()),
                'compmobs': lambda f: list(f.content.compositionmobs()),
                'mastermobs': lambda f: list(f.content.mastermobs()),
                'sourcemobs': lambda f: list(f.content.sourcemobs()),
                'dictionary': lambda f: f.dictionary,
                'metadict': lambda f: f.metadict,
                'root': lambda f: f.root,
            }

            for key in ['toplevel', 'compmobs', 'mastermobs', 'sourcemobs', 'dictionary', 'metadict', 'root']:
                if self.current_options.get(key):
                     try:
                          print(f"Adding data from option: {key}")
                          data = option_map[key](f)
                          if isinstance(data, list):
                              root_items.extend(data)
                          else:
                              root_items.append(data)
                     except Exception as e:
                          print(f"Error getting root data for option {key}: {e}")
                          QtWidgets.QMessageBox.warning(self, "Data Error", f"Failed to retrieve data for option '{key}'.\nError: {e}")
            
            if root_items:
                model = AAFModel(root_items)
                self.setModel(model)
                self.expandToDepth(0)
            else:
                QtWidgets.QMessageBox.warning(self, "No Data", "No display options selected or no data found for selection.")
                self.setModel(None)

            self.setWindowTitle(f"{os.path.basename(file_path)} - AAFInspector")
            self.resizeColumnToContents(0)
            self.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
            self.resizeColumnToContents(2)
            self.header().setStretchLastSection(False)
            self.setupFileWatcher(file_path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error Loading File", f"Could not process AAF file:\n{file_path}\n\nError: {str(e)}")
            self.setModel(None)
            if self.aaf_file:
                try: self.aaf_file.close()
                except Exception: pass
            self.aaf_file = None; self.current_file_path = None; self.setupFileWatcher(None)

    @QtCore.Slot(str)
    def fileChangedHandler(self, path):
        if path == self.current_file_path:
            reply = QtWidgets.QMessageBox.question(self, "File Changed",
                                                   f"The file '{os.path.basename(path)}' has been modified.\nDo you want to reload it?",
                                                   QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                                                   QtWidgets.QMessageBox.StandardButton.Yes)
            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                self.loadAafFile(self.current_file_path, self.current_options)
            else: self.setupFileWatcher(self.current_file_path)

    def setupFileWatcher(self, file_path):
        if not self.fs_watcher:
             self.fs_watcher = QtCore.QFileSystemWatcher(self)
             try: self.fs_watcher.fileChanged.connect(self.fileChangedHandler)
             except (TypeError, RuntimeError) as e: print(f"Error connecting file watcher signal: {e}")
        current_paths = self.fs_watcher.files()
        if current_paths: self.fs_watcher.removePaths(current_paths)
        if file_path and os.path.exists(file_path): self.fs_watcher.addPath(file_path)

    def closeEvent(self, event):
        if self.aaf_file:
            try: self.aaf_file.close()
            except Exception as e: print(f"Error closing AAF file on exit: {e}")
        super().closeEvent(event)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("AAFInspector")
    default_options = {
        'toplevel': True, 'compmobs': False, 'mastermobs': False,
        'sourcemobs': False, 'dictionary': False, 'metadict': False, 'root': False,
    }
    initial_dialog = InputDialog(default_options, parent=None)
    dialogResult = initial_dialog.exec()
    if dialogResult == QtWidgets.QDialog.DialogCode.Accepted:
        selected_file_path, selected_options = initial_dialog.getResults()
        window = Window(parent=None)
        window.loadAafFile(selected_file_path, selected_options)
        window.show()
        sys.exit(app.exec())
    else:
        print("Operation cancelled by user at startup.")
        sys.exit(0)