import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from .utils import log, sanitize_filename
from .transform import avid_to_resolve_transform

class FCPXMLBuilder:
    def __init__(self, seq_info, events, effects_index):
        self.seq_info = seq_info
        self.events = events
        self.effects_index = effects_index
        self.root = None

    def build(self):
        log("Building FCPXML...")
        self.root = ET.Element("fcpxml", version="1.13")
        resources_el = ET.SubElement(self.root, "resources")

        # Formats
        fmt_id = "r0"
        ET.SubElement(resources_el, "format", {
            "width": str(self.seq_info.width),
            "height": str(self.seq_info.height),
            "id": fmt_id,
            "frameDuration": f"1/{int(self.seq_info.fps)}s",
            "name": f"FFVideoFormat{self.seq_info.width}x{self.seq_info.height}p{int(self.seq_info.fps)}"
        })

        # Placeholder assets for FX events
        placeholder_ids = {}
        for idx, name in self.effects_index.items():
            asset_id = f"fx_placeholder_{idx}"
            placeholder_ids[idx] = asset_id
            ET.SubElement(resources_el, "asset", {
                "id": asset_id,
                "name": name,
                "format": fmt_id,
                "hasVideo": "1"
            }).append(ET.Element("media-rep", {
                "src": f"file://localhost/PLACEHOLDERS/{sanitize_filename(name)}.png",
                "kind": "original-media"
            }))

        # Sequence
        library_el = ET.SubElement(self.root, "library")
        event_el = ET.SubElement(library_el, "event", {"name": self.seq_info.name})
        seq_el = ET.SubElement(event_el, "project", {"name": self.seq_info.name})
        seq_seq_el = ET.SubElement(seq_el, "sequence", {
            "format": fmt_id,
            "duration": f"{int(max(ev.rec_out for ev in self.events))}/25s"
        })
        spine_el = ET.SubElement(seq_seq_el, "spine")

        for ev in self.events:
            if ev.effect:
                ET.SubElement(spine_el, "asset-clip", {
                    "name": ev.effect,
                    "ref": placeholder_ids.get(ev.index, ""),
                    "start": f"{ev.rec_in}/25s",
                    "duration": f"{ev.dur_frames}/25s"
                })
            else:
                ET.SubElement(spine_el, "gap", {
                    "name": ev.kind,
                    "start": f"{ev.rec_in}/25s",
                    "duration": f"{ev.dur_frames}/25s"
                })

    def write(self, out_path):
        xml_str = ET.tostring(self.root, encoding="utf-8")
        pretty_str = minidom.parseString(xml_str).toprettyxml(indent="    ")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(pretty_str)
        log(f"OK: wrote {out_path}")
