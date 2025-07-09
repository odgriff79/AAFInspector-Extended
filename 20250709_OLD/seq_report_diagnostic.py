import re

def safe_read_text(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(filepath, "r", encoding="utf-16") as f:
            return f.read()

def parse_sequence_report(seq_text):
    in_clip_list = False
    entries = []
    lines = seq_text.splitlines()

    for line in lines:
        raw = line.rstrip()

        if "Source Clip List:" in raw:
            in_clip_list = True
            continue
        if "########## Tape Source Info:" in raw and in_clip_list:
            in_clip_list = False

        if not in_clip_list:
            continue

        if not raw.strip() or "clips found" in raw or "____" in raw:
            continue

        # Split by 2+ spaces
        parts = re.split(r"\s{2,}", raw.strip())
        if len(parts) < 4:
            continue

        # Last part is always the MobID
        mob_id = parts[-1]
        # Third part is always the Clip Name
        clip_name = parts[2]

        entries.append({"Clip": clip_name, "MobID": mob_id})

    print("\n✅ FINAL PARSED CLIPS:")
    for e in entries:
        print(f"Clip: {e['Clip']}\n  MobID: {e['MobID']}\n")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parse_report_singleline.py <sequence_report.txt>")
        sys.exit(1)

    text = safe_read_text(sys.argv[1])
    parse_sequence_report(text)
