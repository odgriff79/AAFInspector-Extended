#!/usr/bin/env python3
# export_now.py — zero-config wrapper
import sys, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
# exporter file name — must match the script you saved earlier
EXPORTER = HERE / "csv_to_fcpxml_correct_TCs_FX_EDL_TIME_MODE_CROP_UNITS_v2.py"

def pick_csv():
    m = HERE / "mem1.csv"
    if m.exists(): return m
    csvs = sorted(HERE.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if csvs: return csvs[0]
    raise SystemExit("No CSV found. Put mem1.csv (or any .csv) next to this script.")

def main():
    if not EXPORTER.exists():
        raise SystemExit(f"Exporter not found: {EXPORTER}\n"
                         f"Place csv_to_fcpxml_correct_TCs_FX_EDL_TIME_MODE_CROP_UNITS.py beside this file.")

    csv_path = pick_csv()
    stem = csv_path.stem
    xml_out = HERE / f"{stem}_REL13.fcpxml"
    edl_out = HERE / f"{stem}_REL15_markers.edl"

    cmd = [
        sys.executable, str(EXPORTER),
        "-i", str(csv_path),
        "-x", str(xml_out),
        "-e", str(edl_out),
        "--width", "1920", "--height", "1080",
        "--time-mode", "abs",
        "--pos-units", "pixels",
    ]
    print("[1/1] Exporting…")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit(f"Export failed with code {r.returncode}.")
    print("✅ Done")
    print(f"   FCPXML: {xml_out}")
    print(f"   EDL   : {edl_out}")

if __name__ == "__main__":
    main()
