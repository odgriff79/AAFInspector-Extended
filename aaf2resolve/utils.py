import os, re, json
from datetime import datetime
from fractions import Fraction
from typing import Any

from . import APP_NAME

LOG_PATH = os.path.join(os.path.expanduser("~"), "Documents", f"{APP_NAME}_log.txt")

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def safe_name(s: Any) -> str:
    return str(s) if s is not None else ""

def lower_str(x):
    try: return str(x).lower()
    except: return ""

def starts_with_any(name: str, prefixes):
    n = (name or "").upper()
    return any(n.startswith(p) for p in prefixes)

def aaf_class_name(obj) -> str:
    try:
        cn = getattr(obj, "class_name", None)
        if cn: return str(cn)
    except:
        pass
    try:
        return obj.__class__.__name__
    except:
        return "Unknown"

def sanitize_filename(name: str) -> str:
    n = re.sub(r"[^0-9A-Za-z._ -]+", "_", (name or "placeholder")).strip()
    n = re.sub(r"_+", "_", n)
    return n[:120] or "placeholder"

def norm_media_uri(path: str) -> str:
    if not path: return ""
    if path.lower().startswith("file://"): return path
    p = path.replace("\\", "/")
    if p.startswith("//localhost"): return "file://" + p[2:]
    if p.startswith("/"): return f"file://localhost{p}"
    if len(p) > 1 and p[1] == ":":
        p = "/" + p
    return f"file://localhost{p}"

def nearest_int(x: float) -> int:
    return int(round(x))

def frames_to_fractional(frames: int, fps: Fraction) -> str:
    # duration expressed as A/B s so FCPXML is happy
    A = int(frames) * int(fps.denominator)
    B = int(fps.numerator)
    return f"{A}/{B}s"

def df_flag_from_rate(fps: Fraction) -> str:
    # Simple: label as NDF. Extend if you need 30000/1001 DF.
    return "NDF"

def json_dump_safe(path: str, data: Any):
    def default(o):
        try:
            from aaf2 import rational as _rat
            if isinstance(o, _rat.AAFRational):
                return {"num": o.numerator, "den": o.denominator}
        except Exception:
            pass
        try:
            return str(o)
        except Exception:
            return "<unserializable>"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=default)
