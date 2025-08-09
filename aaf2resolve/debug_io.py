import os
from typing import Dict
from .models import SequenceInfo
from .utils import json_dump_safe

def write_debug_jsons(out_fcpxml_path: str, seq: SequenceInfo, extractor) -> Dict[str, str]:
    base = os.path.splitext(out_fcpxml_path)[0]
    seq_dbg_path   = base + "_sequence_debug.json"
    ev_dbg_path    = base + "_events_debug.json"
    fx_idx_path    = base + "_effects_index.json"
    trace_txt_path = base + "_trace.txt"

    seq_dbg = {
        "sequence_name": seq.name,
        "fps_fraction": {"numerator": seq.fps.numerator, "denominator": seq.fps.denominator},
        "fps_float": float(seq.fps),
        "start_tc_frames": seq.start_tc_f,
        "dimensions": {"width": seq.width, "height": seq.height},
        "slots_scan": extractor.slot_scan,
        "traversal_trace_first_200": extractor.traversal_trace[:200],
        "events_count": len(seq.events),
    }
    json_dump_safe(seq_dbg_path, seq_dbg)

    events_dump = []
    for i, ev in enumerate(seq.events):
        events_dump.append({
            "index": i,
            "name": ev.name,
            "rec_in_frames": ev.rec_in_f,
            "rec_out_frames": ev.rec_out_f,
            "duration_frames": ev.duration_f,
            "source_path": ev.source_path,
            "source_name": ev.source_name,
            "tape_id": ev.tape_id,
            "disk_label": ev.disk_label,
            "width": ev.width, "height": ev.height,
            "effect_name": ev.effect_name,
            "effect_convertible": ev.effect_convertible,
            "filler_fx_file": ev.filler_fx_file,
            "keyframes": [
                {"time_frames": k.time_frames, "pos_x": k.pos_x, "pos_y": k.pos_y,
                 "scale_x": k.scale_x, "scale_y": k.scale_y, "rotation": k.rotation}
                for k in ev.keyframes
            ]
        })
    json_dump_safe(ev_dbg_path, events_dump)

    json_dump_safe(fx_idx_path, extractor.effects_index)

    with open(trace_txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(extractor.traversal_trace))

    return {
        "sequence_debug": seq_dbg_path,
        "events_debug": ev_dbg_path,
        "effects_index": fx_idx_path,
        "trace_txt": trace_txt_path,
    }
