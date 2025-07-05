import json
from PySide6 import QtWidgets, QtCore

TARGET_CLASS = "CompositionMob"

def recursive_find_mobs(node, found):
    """Recursively find all CompositionMobs"""
    if isinstance(node, list):
        if len(node) >= 4:
            maybe_name = node[0]
            maybe_class = node[1]
            maybe_props = node[3]
            if isinstance(maybe_name, str) and maybe_class == TARGET_CLASS and isinstance(maybe_props, list):
                mob_id = "(no MobID)"
                slots = []
                for p in maybe_props:
                    if p[0] == "MobID":
                        mob_id = p[2]
                    if p[0] == "Slots":
                        slots = p[3]
                found.append({
                    "name": maybe_name,
                    "class": maybe_class,
                    "mob_id": mob_id,
                    "slots": slots
                })
        for item in node:
            recursive_find_mobs(item, found)
    elif isinstance(node, dict):
        for v in node.values():
            recursive_find_mobs(v, found)

class AAFCompositionBrowser(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AAF CompositionMob Browser")
        self.resize(1000, 700)

        layout = QtWidgets.QVBoxLayout(self)

        self.load_button = QtWidgets.QPushButton("Load JSON")
        self.load_button.clicked.connect(self.load_json)
        layout.addWidget(self.load_button)

        self.mob_combo = QtWidgets.QComboBox()
        layout.addWidget(self.mob_combo)

        self.inspect_button = QtWidgets.QPushButton("Show Selected Mob Details")
        self.inspect_button.clicked.connect(self.show_mob_details)
        layout.addWidget(self.inspect_button)

        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)

        self.mobs = []
        self.json_data = None

    def load_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Compressed JSON", "", "JSON Files (*.json)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            self.json_data = json.load(f)
        self.text.appendPlainText(f"✅ Loaded JSON: {path}\nScanning recursively for CompositionMobs...")
        self.mobs = []
        recursive_find_mobs(self.json_data, self.mobs)
        self.text.appendPlainText(f"✅ Found {len(self.mobs)} CompositionMobs.\n")
        self.mob_combo.clear()
        for mob in self.mobs:
            label = f"{mob['name']} ({mob['mob_id']})"
            self.mob_combo.addItem(label)

    def show_mob_details(self):
        idx = self.mob_combo.currentIndex()
        if idx < 0 or idx >= len(self.mobs):
            self.text.appendPlainText("❌ No mob selected.")
            return
        mob = self.mobs[idx]
        self.text.appendPlainText(f"\n=== Mob Details ===")
        self.text.appendPlainText(f"Name: {mob['name']}")
        self.text.appendPlainText(f"Class: {mob['class']}")
        self.text.appendPlainText(f"MobID: {mob['mob_id']}")
        self.text.appendPlainText(f"Slots: {len(mob['slots'])}\n")
        for sidx, slot in enumerate(mob['slots']):
            seg_class = "(unknown)"
            if isinstance(slot, list):
                for sub in slot[3]:
                    if sub[0] == "Segment":
                        if isinstance(sub[3], list) and len(sub[3]) > 1:
                            seg_class = sub[3][1]
            self.text.appendPlainText(f"  Slot {sidx}: Segment Class = {seg_class}")

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    win = AAFCompositionBrowser()
    win.show()
    app.exec()
