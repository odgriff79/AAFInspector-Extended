from typing import Dict, List
from .models import Event

def avid_to_resolve_transform(ev: Event) -> List[Dict[str, float]]:
    # Normalize to percent or factor depending on typical Avid values.
    w = max(1, ev.width or 1920)
    h = max(1, ev.height or 1080)
    out = []
    seen = set()
    for k in ev.keyframes:
        t = max(0, min(ev.duration_f-1, k.time_frames))
        if t in seen: 
            continue
        seen.add(t)
        sx = k.scale_x if k.scale_x <= 3.0 else (k.scale_x / 100.0)
        sy = k.scale_y if k.scale_y <= 3.0 else (k.scale_y / 100.0)
        out.append({
            "timeFrames": t,
            "x": (k.pos_x / w),
            "y": -(k.pos_y / h),
            "scaleX": sx,
            "scaleY": sy,
            "rotation": k.rotation or 0.0,
        })
    if not out:
        out = [
            {"timeFrames": 0, "x": 0.0, "y": 0.0, "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0},
            {"timeFrames": max(0, ev.duration_f-1), "x": 0.0, "y": 0.0, "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0},
        ]
    return sorted(out, key=lambda d: d["timeFrames"])
