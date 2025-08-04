#!/usr/bin/env python3
"""
Resolve FX FCPXML Generator
Generates DaVinci Resolve-compatible FCPXML from enriched SuperEDL JSON
Includes AVX and DVE effect keyframes and placeholder support
"""

import json
from jinja2 import Environment, FileSystemLoader
from fractions import Fraction
import os
import sys

def parse_fraction(frac_str):
    if isinstance(frac_str, (int, float)):
        return float(frac_str)
    try:
        return float(Fraction(frac_str))
    except Exception:
        return 0.0

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"❌ JSON root must be a list of event dicts, got {type(data).__name__}")
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"❌ JSON item at index {i} is not a dict: {type(item).__name__}")
    return data

def generate_fcpxml(data, output_path, template_path="template_fcpxml.j2"):
    env = Environment(
        loader=FileSystemLoader(searchpath=os.path.dirname(template_path)),
        trim_blocks=True,
        lstrip_blocks=True
    )
    template = env.get_template(os.path.basename(template_path))

    # Filter and convert events
    for i, item in enumerate(data):
        try:
            item["StartTimeFrames"] = int(item.get("StartTime (frames)", 0))
            item["EventLength"] = int(item.get("Event Length", 0))
            item["SourceTC"] = item.get("Timeline Start TC", "00:00:00:00")
            item["ClipName"] = item.get("Clip Name", f"Unnamed_{i}")
            item["Effect"] = item.get("Effect Name", "")
            item["Keyframes"] = item.get("FCPXML_Converted_Keyframes", {})
        except Exception as e:
            raise RuntimeError(f"❌ Failed to parse event #{i}: {e}")

    fcpxml_str = template.render(events=data)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(fcpxml_str)
    p
