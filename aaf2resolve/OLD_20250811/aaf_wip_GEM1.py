#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AAF Inspector + In-Memory SuperEDL (no JSON files)
- Opens an AAF using pyaaf2 (1.4.0 recommended)
- Builds an in-memory nested dict (name/class/value/children)
- (Optionally) compresses that dict to a compact list format
- Traverses the live AAF to produce SuperEDL-style events & FX
- Presents summary in GUI and can export enriched CSV

Tested on Windows with PySide6 + pyaaf2==1.4.0
"""

import os
import re
import sys
import csv
import json
import math
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Iterable

# ---- GUI (PySide6 preferred) ----
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    # Fallback to PyQt5 if needed
    from PyQt5 import QtCore, QtGui, QtWidgets  # type: ignore

# ---- AAF ----
try:
    import aaf2  # pyaaf2==1.4.0
except Exception as e:
    raise SystemExit("pyaaf2==1.4.0 is required. pip install pyaaf2==1.4.0") from e


# =============================================================================
# UTILITIES: Timecode, safe strings, URL decode, etc.
# =============================================================================

def frames_to_tc(frame_count: Optional[int], fps: float, drop: bool = False) -> str:
    """
    Convert integer frames to HH:MM:SS:FF
    """
    if frame_count is None or fps is None or fps <= 0:
        return "N/A"
    try:
        fc = int(frame_count)
        fps_int = round(float(fps))
        if fps_int <= 0:
            return "N/A"

        # drop-frame handling: only valid at 29.97 (30*1000/1001) and 59.94
        # For display purposes here we treat fps as integer and ignore the exact drop-frame counting rules,
        # but we keep the colon vs semicolon distinction.
        hh = fc // (3600 * fps_int)
        rem = fc % (3600 * fps_int)
        mm = rem // (60 * fps_int)
        rem = rem % (60 * fps_int)
        ss = rem // fps_int
        ff = rem % fps_int
        sep = ';' if drop else ':'
        return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"
    except Exception:
        return "N/A"


def unquote_url_to_path(url: str) -> Tuple[str, str]:
    """
    Decode AAF Locator URLString into (filename, directory)
    """
    try:
        p = urllib.parse.urlparse(url)
        path = urllib.parse.unquote(p.path or "")
        if path.startswith("/") and sys.platform.startswith("win"):
            # Might be a file:///C:/... form
            if len(path) >= 3 and path[2] == ":":
                path = path[1:]
        fname = os.path.basename(path) or "Unknown"
        dname = os.path.dirname(path) or ""
        return fname, dname
    except Exception:
        return "Unknown", ""


def tidy_effect_name(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"_v\d+$", "", s, flags=re.IGNORECASE)
    s = s.replace("_", " ")
    return s


# =============================================================================
# IN-MEMORY TREE BUILDER (name/class/value/children) + COMPRESSOR
# =============================================================================

def _node_dict(name: str, cls: str, value: Any = None, children: Optional[List[dict]] = None) -> dict:
    return {
        "name": name,
        "class": cls,
        "value": value,
        "children": children or []
    }


def aaf_to_nested_dict(aaf: aaf2.AAFFile) -> dict:
    """
    Builds a top-level dictionary of interesting AAF parts (mobs + dictionary).
    This is intentionally compact (not a full lossless dump) but keeps the shape
    needed for downstream experiments or display.
    """
    root = _node_dict("AAF Root", "Root", value=None, children=[])

    # Dictionary (names and defs can be large; keep high-level)
    try:
        d_node = _node_dict("Dictionary", "Dictionary")
        d_node["children"].append(_node_dict("ClassDefs", "Bucket", value=len(aaf.dictionary.classdefs)))
        d_node["children"].append(_node_dict("DataDefs", "Bucket", value=len(aaf.dictionary.datadefs)))
        root["children"].append(d_node)
    except Exception:
        pass

    # Composition, Master, Source mobs
    comp_parent = _node_dict("Composition Mobs", "MobList")
    master_parent = _node_dict("Master Mobs", "MobList")
    source_parent = _node_dict("Source Mobs", "MobList")

    for mob in aaf.content.mobs():
        try:
            nm = getattr(mob, "name", None) or "Unnamed"
            mobid = str(getattr(mob, "mob_id", ""))
            kind = mob.class_name

            node = _node_dict(nm, kind, value=mobid, children=[])
            # Slots summary
            s_parent = _node_dict("Slots", "Slots", value=len(list(mob.slots())), children=[])
            node["children"].append(s_parent)

            if mob.class_name == "CompositionMob":
                comp_parent["children"].append(node)
            elif mob.class_name == "MasterMob":
                master_parent["children"].append(node)
            elif mob.class_name == "SourceMob":
                source_parent["children"].append(node)
            else:
                # leave it out to keep tidy
                pass
        except Exception:
            continue

    root["children"] += [comp_parent, master_parent, source_parent]
    return root


def compress_node_to_list(node: Any) -> Any:
    """
    Compress nested dict (name/class/value/children) to compact list:
    [name, class, (value?), (children?)]
    """
    if not isinstance(node, dict):
        return node
    name = node.get("name")
    cls = node.get("class")
    val = node.get("value")
    children = node.get("children", []) or []

    out = [name, cls]
    if val is not None or children:
        out.append(val)
    if children:
        out.append([compress_node_to_list(c) for c in children])
    return out


# =============================================================================
# SUPEREDL-LIKE EXTRACTOR (directly on aaf2 live objects)
# =============================================================================

@dataclass
class EventRow:
    Event: int
    EventName: str
    ClipName: str
    SourceFileName: str
    SourceFilePath: str
    DiskLabel: str
    TapeID: str
    SourceMobID: str
    TrackID: str
    SourceClipEditRate: float
    TimelineStartTC: str
    SourceClipStartTC: str
    SourceClipOffset: str
    StartTime: str
    EndTime: str
    EventLength: int
    SourceClipStartFrames: int
    SourceClipOffsetFrames: int
    StartTimeFrames: int
    EffectName: str
    KeyframeDetails: str
    OrigSourceClipLength: int


@dataclass
class Summary:
    timeline_name: str
    edit_rate: float
    is_drop: bool
    start_tc_frames: int
    total_length_frames: int
    total_events: int
    unique_sources: int


def _mob_label(mob) -> str:
    try:
        return mob.name or "Unnamed"
    except Exception:
        return "Unnamed"


def _read_tapeid_and_disklabel_from_mob(mob) -> Tuple[Optional[str], Optional[str]]:
    """
    Crawl common locations for TapeID and DiskLabel.
    """
    tape = None
    disk = None
    try:
        u = getattr(mob, "user_comments", None)
        if u:
            for k, v in u.items():
                if isinstance(k, str) and isinstance(v, str):
                    if tape is None and re.search(r"tape\s*id", k, re.I):
                        tape = v
                    if disk is None and re.search(r"disk\s*label|_importdisklab", k, re.I):
                        disk = v
    except Exception:
        pass

    # fallbacks: look for tagged values on mob attribute lists if present
    try:
        attr = getattr(mob, "attributes", None)
        if attr:
            for k, v in attr.items():
                if isinstance(k, str):
                    if disk is None and re.search(r"_importdisklab", k, re.I):
                        if isinstance(v, (str, int, float)):
                            disk = str(v)
    except Exception:
        pass

    return tape, disk


def _find_locator_url_from_descriptor(mob) -> Optional[str]:
    """
    If the mob has an ImportDescriptor with Locator -> URLString, return that URLString.
    """
    try:
        desc = getattr(mob, "descriptor", None)
        if desc is None:
            return None
        # Many descriptors may expose 'locators' list with URLString
        locs = getattr(desc, "locators", None)
        if locs:
            for loc in locs:
                try:
                    url = getattr(loc, "url", None) or getattr(loc, "URLString", None)
                    if url:
                        return str(url)
                except Exception:
                    continue
        # Some AAFs store as single locator named 'Locator'
        loc = getattr(desc, "Locator", None)
        if loc:
            url = getattr(loc, "url", None) or getattr(loc, "URLString", None)
            if url:
                return str(url)
    except Exception:
        pass
    return None


def _resolve_source_chain_to_import_mob(mob_map: Dict[str, Any], start_mob_id: str) -> Tuple[Optional[Any], int]:
    """
    Follow SourceID/UMID chain until a mob with an ImportDescriptor/Locator is found.
    Returns (import_mob, hop_count). If not found, returns (final_mob_seen, hops).
    """
    hops = 0
    current = mob_map.get(start_mob_id)
    last = current
    visited = set()
    while current and (id(current) not in visited):
        visited.add(id(current))
        # If descriptor has locator → it's our anchor
        url = _find_locator_url_from_descriptor(current)
        if url:
            return current, hops
        # Climb via slots->segment->SourceClip->SourceID (Master/Source)
        try:
            next_mob_id = None
            for slot in current.slots():
                seg = slot.segment
                source_id = None
                if hasattr(seg, "source_id"):
                    source_id = str(seg.source_id) if seg.source_id else None
                # Some sequences nest SourceClips
                if not source_id and hasattr(seg, "components"):
                    for comp in seg.components:
                        if comp.class_name == "SourceClip":
                            sid = getattr(comp, "source_id", None)
                            if sid:
                                source_id = str(sid)
                                break
                if source_id:
                    next_mob_id = source_id
                    break
            if next_mob_id and next_mob_id in mob_map:
                last = mob_map[next_mob_id]
                current = last
                hops += 1
                continue
            else:
                break
        except Exception:
            break
    return last, hops


def _build_mob_map(aaf: aaf2.AAFFile) -> Dict[str, Any]:
    mp: Dict[str, Any] = {}
    for m in aaf.content.mobs():
        try:
            mp[str(m.mob_id)] = m
        except Exception:
            pass
    return mp


def _find_top_level_sequence(aaf: aaf2.AAFFile) -> Tuple[Optional[Any], Optional[Any], float, bool]:
    """
    Heuristic: return (comp_mob, picture_slot_segment, edit_rate, is_drop)
    Prefers a CompositionMob with a picture track that looks like the main sequence.
    """
    best_mob = None
    best_seg = None
    best_rate = 25.0
    is_drop = False

    for mob in aaf.content.mobs():
        try:
            if mob.class_name != "CompositionMob":
                continue
            # pick picture slot (often DataDefinition = Picture)
            for slot in mob.slots():
                try:
                    dd = getattr(slot, "data_def", None)
                    if dd and str(dd).lower().find("picture") >= 0:
                        seg = slot.segment
                        rate = float(getattr(slot, "edit_rate", 25.0) or 25.0)
                        best_mob, best_seg, best_rate = mob, seg, rate
                        is_drop = False
                        # Could refine to prefer name ending in ".Exported.01"
                        return best_mob, best_seg, best_rate, is_drop
                except Exception:
                    continue
        except Exception:
            continue
    return best_mob, best_seg, best_rate, is_drop


def _timecode_start_from_source_mob(mob) -> Tuple[int, float, bool]:
    """
    Read genuine camera/source Start (frames), edit rate and drop:
    - search for a Timecode segment in source mob slots
    """
    try:
        for slot in mob.slots():
            seg = slot.segment
            # direct Timecode
            if seg.class_name == "Timecode":
                start_frames = int(getattr(seg, "start", 0) or 0)
                rate = float(getattr(slot, "edit_rate", 25.0) or 25.0)
                drop = bool(getattr(seg, "drop", False))
                return start_frames, rate, drop
            # possibly nested sequence containing Timecode
            if hasattr(seg, "components"):
                for comp in seg.components:
                    if comp.class_name == "Timecode":
                        start_frames = int(getattr(comp, "start", 0) or 0)
                        rate = float(getattr(slot, "edit_rate", 25.0) or 25.0)
                        drop = bool(getattr(comp, "drop", False))
                        return start_frames, rate, drop
    except Exception:
        pass
    return 0, 25.0, False


def _collect_events_from_sequence(seg, mob_map: Dict[str, Any], timeline_rate: float) -> List[Dict[str, Any]]:
    """
    Traverse the CompositionMob's picture slot segment (Sequence or OperationGroup)
    and collect SourceClip events (and FX on Filler).
    """
    events: List[Dict[str, Any]] = []

    def walk(node, t0_frames: int):
        cname = getattr(node, "class_name", "")
        if cname == "SourceClip":
            length = int(getattr(node, "length", 0) or 0)
            off = int(getattr(node, "start_time", 0) or 0)  # source offset frames
            sid = getattr(node, "source_id", None)
            smob = mob_map.get(str(sid)) if sid else None
            ev = {
                "kind": "source",
                "length": length,
                "source_offset": off,
                "mob_id": str(sid) if sid else "",
                "source_mob": smob,
                "timeline_start": t0_frames
            }
            events.append(ev)
            return t0_frames + length

        if cname == "OperationGroup":
            # FX on filler or on clip; collect presence
            length = int(getattr(node, "length", 0) or 0)
            # If this op has an input Segment that is Filler, we produce a synthetic event for FX-on-filler
            is_fx_on_filler = False
            try:
                for i in range(0, 2):
                    try:
                        ip = node.input_segments[i]
                        if ip and ip.class_name == "Filler":
                            is_fx_on_filler = True
                            break
                    except Exception:
                        pass
            except Exception:
                pass
            if is_fx_on_filler:
                events.append({
                    "kind": "fx_filler",
                    "length": length,
                    "timeline_start": t0_frames,
                    "fx_name": tidy_effect_name(getattr(node, "operation", node.class_name) or "Effect")
                })
                return t0_frames + length

            # Otherwise, traverse its input for source events
            try:
                for i in range(0, 2):
                    try:
                        ip = node.input_segments[i]
                        if ip:
                            t0_frames = walk(ip, t0_frames)
                    except Exception:
                        pass
            except Exception:
                pass
            return t0_frames

        if hasattr(node, "components"):
            # Sequence etc.
            cur = t0_frames
            for comp in node.components:
                cur = walk(comp, cur)
            return cur

        # Fallback: unknown node
        length = int(getattr(node, "length", 0) or 0)
        return t0_frames + length

    walk(seg, 0)
    return events


def extract_superedl(aaf: aaf2.AAFFile) -> Tuple[Summary, List[EventRow]]:
    """
    Main extraction: find top sequence, produce enriched rows and summary.
    """
    mob_map = _build_mob_map(aaf)
    comp_mob, seq_seg, timeline_rate, is_drop = _find_top_level_sequence(aaf)
    if not comp_mob or not seq_seg:
        # Empty fallback
        s = Summary("N/A", 25.0, False, 0, 0, 0, 0)
        return s, []

    events = _collect_events_from_sequence(seq_seg, mob_map, timeline_rate)

    enriched: List[EventRow] = []
    total_length = 0
    uniq_sources = set()

    for idx, ev in enumerate(events, start=1):
        total_length += ev.get("length", 0)
        tstart = int(ev.get("timeline_start", 0))
        if ev["kind"] == "fx_filler":
            fxname = tidy_effect_name(ev.get("fx_name") or "Effect")
            placeholder = re.sub(r"[^0-9a-z]+", "_", fxname.lower()) + "_placeholder.png"
            enriched.append(EventRow(
                Event=idx,
                EventName=f"{fxname} on Filler",
                ClipName=placeholder,
                SourceFileName=placeholder,
                SourceFilePath=os.path.join("placeholders", placeholder),
                DiskLabel="N/A",
                TapeID="N/A",
                SourceMobID="FX_ON_FILLER",
                TrackID="VFX",
                SourceClipEditRate=timeline_rate,
                TimelineStartTC=frames_to_tc(tstart, timeline_rate, is_drop),
                SourceClipStartTC="01:00:00:00",
                SourceClipOffset="00:00:00:00",
                StartTime="01:00:00:00",
                EndTime="01:00:00:00",
                EventLength=int(ev.get("length", 0)),
                SourceClipStartFrames=0,
                SourceClipOffsetFrames=0,
                StartTimeFrames=0,
                EffectName=fxname,
                KeyframeDetails="No effect data found.",
                OrigSourceClipLength=int(ev.get("length", 0))
            ))
            continue

        # source event
        smob = ev.get("source_mob")
        smob_id = ev.get("mob_id") or ""
        uniq_sources.add(smob_id)

        # resolve to import mob (true source) via chain
        final_mob, hops = _resolve_source_chain_to_import_mob(mob_map, smob_id)
        tape, disk = _read_tapeid_and_disklabel_from_mob(smob) if smob else (None, None)
        tape2, disk2 = _read_tapeid_and_disklabel_from_mob(final_mob) if final_mob else (None, None)
        tape = tape or tape2 or "N/A"
        disk = disk or disk2 or "N/A"

        url = _find_locator_url_from_descriptor(final_mob) if final_mob else None
        fname, dname = ("Unknown", "")
        if url:
            fname, dname = unquote_url_to_path(url)

        gstart, srate, sdrop = _timecode_start_from_source_mob(final_mob) if final_mob else (0, timeline_rate, False)

        off = int(ev.get("source_offset", 0))
        length = int(ev.get("length", 0))
        start_frames = gstart + off
        end_frames = start_frames + length

        enriched.append(EventRow(
            Event=idx,
            EventName=fname,
            ClipName=fname,
            SourceFileName=fname,
            SourceFilePath=dname.replace("\\", "/"),
            DiskLabel=disk,
            TapeID=tape,
            SourceMobID=smob_id,
            TrackID="V",  # could be refined per-slot
            SourceClipEditRate=srate,
            TimelineStartTC=frames_to_tc(tstart, timeline_rate, is_drop),
            SourceClipStartTC=frames_to_tc(gstart, srate, sdrop),
            SourceClipOffset=frames_to_tc(off, srate, sdrop),
            StartTime=frames_to_tc(start_frames, srate, sdrop),
            EndTime=frames_to_tc(end_frames, srate, sdrop),
            EventLength=length,
            SourceClipStartFrames=gstart,
            SourceClipOffsetFrames=off,
            StartTimeFrames=start_frames,
            EffectName="N/A",
            KeyframeDetails="No effect data found.",
            OrigSourceClipLength=length  # can be refined from descriptor if needed
        ))

    s = Summary(
        timeline_name=_mob_label(comp_mob),
        edit_rate=timeline_rate,
        is_drop=is_drop,
        start_tc_frames=0,  # If your comp_mob has an explicit start offset, compute here
        total_length_frames=total_length,
        total_events=len(enriched),
        unique_sources=len({r.SourceMobID for r in enriched if r.SourceMobID not in ("FX_ON_FILLER", "")})
    )
    return s, enriched


# =============================================================================
# GUI
# =============================================================================

class AAFInspectorWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AAF Inspector + In-Memory SuperEDL")
        self.resize(1100, 720)

        self.aaf_path: Optional[str] = None
        self.aaf_file: Optional[aaf2.AAFFile] = None

        self.in_memory_data: Optional[dict] = None
        self.compressed_data: Optional[list] = None

        # UI
        self._build_ui()

    # ---- UI Layout ----
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        v = QtWidgets.QVBoxLayout(central)

        # Top bar
        h = QtWidgets.QHBoxLayout()
        self.btn_open = QtWidgets.QPushButton("Open AAF…")
        self.btn_build_tree = QtWidgets.QPushButton("Build In-Memory Tree")
        self.btn_build_tree.setEnabled(False)
        self.btn_compress = QtWidgets.QPushButton("Compress Tree")
        self.btn_compress.setEnabled(False)
        self.btn_run = QtWidgets.QPushButton("Run SuperEDL (In-Memory)")
        self.btn_run.setEnabled(False)

        h.addWidget(self.btn_open)
        h.addWidget(self.btn_build_tree)
        h.addWidget(self.btn_compress)
        h.addWidget(self.btn_run)
        h.addStretch(1)

        v.addLayout(h)

        # Info
        self.lbl_info = QtWidgets.QLabel("No file loaded.")
        self.lbl_info.setStyleSheet("font-weight: 600;")
        v.addWidget(self.lbl_info)

        # Tree/Text Views
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.tree_view = QtWidgets.QTreeWidget()
        self.tree_view.setHeaderLabels(["Name", "Class", "Value"])
        splitter.addWidget(self.tree_view)

        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        splitter.addWidget(self.txt_log)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        v.addWidget(splitter)

        # Bottom
        h2 = QtWidgets.QHBoxLayout()
        self.btn_save_csv = QtWidgets.QPushButton("Export CSV…")
        self.btn_save_csv.setEnabled(False)
        h2.addWidget(self.btn_save_csv)
        h2.addStretch(1)
        v.addLayout(h2)

        # Signals
        self.btn_open.clicked.connect(self._on_open_aaf)
        self.btn_build_tree.clicked.connect(self._on_build_tree)
        self.btn_compress.clicked.connect(self._on_compress_tree)
        self.btn_run.clicked.connect(self._on_run_superedl)
        self.btn_save_csv.clicked.connect(self._on_save_csv)

    # ---- Actions ----

    def _log(self, msg: str):
        self.txt_log.appendPlainText(msg)

    def _on_open_aaf(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open AAF", "", "AAF files (*.aaf)")
        if not p:
            return
        try:
            if self.aaf_file:
                self.aaf_file.close()
                self.aaf_file = None
            self.aaf_file = aaf2.open(p)
            self.aaf_path = p
            self.lbl_info.setText(f"Opened: {os.path.basename(p)}")
            self._log(f"[INFO] AAF opened: {p}")
            self.btn_build_tree.setEnabled(True)
            self.btn_run.setEnabled(True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to open AAF:\n{e}")

    def _populate_tree_widget(self, node: dict, parent_item: Optional[QtWidgets.QTreeWidgetItem] = None):
        name = str(node.get("name"))
        cls = str(node.get("class"))
        val = "" if node.get("value") is None else str(node.get("value"))
        item = QtWidgets.QTreeWidgetItem([name, cls, val])
        if parent_item is None:
            self.tree_view.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        for ch in node.get("children", []):
            self._populate_tree_widget(ch, item)

    def _on_build_tree(self):
        if not self.aaf_file:
            QtWidgets.QMessageBox.warning(self, "No AAF", "Open an AAF first.")
            return
        try:
            self.tree_view.clear()
            self.in_memory_data = aaf_to_nested_dict(self.aaf_file)
            self._populate_tree_widget(self.in_memory_data)
            self._log("[OK] In-memory tree built.")
            self.btn_compress.setEnabled(True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to build tree:\n{e}")

    def _on_compress_tree(self):
        if not self.in_memory_data:
            QtWidgets.QMessageBox.warning(self, "No Tree", "Build the in-memory tree first.")
            return
        try:
            self.compressed_data = compress_node_to_list(self.in_memory_data)
            self._log("[OK] Compressed list produced (in memory).")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Compression failed:\n{e}")

    def _on_run_superedl(self):
        if not self.aaf_file:
            QtWidgets.QMessageBox.warning(self, "No AAF", "Open an AAF first.")
            return
        try:
            summary, rows = extract_superedl(self.aaf_file)
            self._last_summary = summary
            self._last_rows = rows
            self.btn_save_csv.setEnabled(bool(rows))

            # Show summary dialog
            msg = [
                f"Timeline Name: {summary.timeline_name}",
                f"Timeline Edit Rate: {summary.edit_rate} {'(DF)' if summary.is_drop else '(NDF)'}",
                f"Timeline Start: {frames_to_tc(summary.start_tc_frames, summary.edit_rate, summary.is_drop)}",
                f"Timeline Length: {frames_to_tc(summary.total_length_frames, summary.edit_rate, summary.is_drop)} ({summary.total_length_frames} frames)",
                f"Total number of EDL events found: {summary.total_events}",
                f"Total number of unique sources: {summary.unique_sources}",
            ]
            QtWidgets.QMessageBox.information(self, "SuperEDL Summary", "\n".join(msg))

            self._log("[OK] SuperEDL extraction complete.")
            self._log("---- Summary ----")
            for line in msg:
                self._log(line)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "SuperEDL Error", str(e))

    def _on_save_csv(self):
        if not getattr(self, "_last_rows", None):
            QtWidgets.QMessageBox.information(self, "No Data", "Run SuperEDL first.")
            return

        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV files (*.csv)")
        if not p:
            return

        try:
            rows: List[EventRow] = self._last_rows
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                # Summary block
                s: Summary = self._last_summary
                w.writerow(["Timeline Summary"])
                w.writerow(["Timeline Name", s.timeline_name])
                w.writerow(["Timeline Edit Rate", f"{s.edit_rate} {'(DF)' if s.is_drop else '(NDF)'}"])
                w.writerow(["Timeline Start", frames_to_tc(s.start_tc_frames, s.edit_rate, s.is_drop)])
                w.writerow(["Timeline Length", frames_to_tc(s.total_length_frames, s.edit_rate, s.is_drop)])
                w.writerow(["Total number of EDL events found", s.total_events])
                w.writerow(["Total number of unique sources", s.unique_sources])
                w.writerow([])

                # Enriched rows
                hdr = [
                    "Event","Event Name","Clip Name","Source File Name","Source File Path","DiskLabel","TapeID",
                    "SourceMobID","TrackID","Source Clip EditRate","Timeline Start TC",
                    "Source Clip start time code","Source Clip offset","StartTime","End Time",
                    "Event Length","Source Clip start (frames)","Source Clip offset (frames)","StartTime (frames)",
                    "Effect Name","Keyframe Details","Orig Source Clip length"
                ]
                w.writerow(hdr)
                for r in rows:
                    w.writerow([
                        r.Event, r.EventName, r.ClipName, r.SourceFileName, r.SourceFilePath, r.DiskLabel, r.TapeID,
                        r.SourceMobID, r.TrackID, r.SourceClipEditRate, r.TimelineStartTC,
                        r.SourceClipStartTC, r.SourceClipOffset, r.StartTime, r.EndTime,
                        r.EventLength, r.SourceClipStartFrames, r.SourceClipOffsetFrames, r.StartTimeFrames,
                        r.EffectName, r.KeyframeDetails, r.OrigSourceClipLength
                    ])
            QtWidgets.QMessageBox.information(self, "Saved", f"CSV written:\n{p}")
            self._log(f"[OK] CSV saved: {p}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Write Error", str(e))


# =============================================================================
# MAIN
# =============================================================================

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = AAFInspectorWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
