import json
import argparse
import os

def load_exclusion_list(filepath):
    """
    Load a text file of names to exclude (one per line).
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"WARNING: Could not load exclusion list: {e}")
        return set()

def filter_taggedvalues(tagged_values, excluded_names):
    """
    Remove TaggedValues whose name matches the exclusion list.
    """
    filtered = []
    for tv in tagged_values:
        name = tv.get("name") or ""
        # Also check children if needed
        children = tv.get("children", [])
        child_name = ""
        for c in children:
            if c.get("name") == "Name":
                child_name = c.get("value", "")
        target_name = name or child_name
        if target_name in excluded_names:
            continue
        filtered.append(tv)
    return filtered

def filter_essence_descriptor(descriptor):
    """
    Remove unneeded fields from essence_descriptor.
    """
    if not descriptor:
        return {}
    keys_to_keep = ["edit_rate"]
    return {k: v for k, v in descriptor.items() if k in keys_to_keep}

def process_json(data, excluded_taggedvalues):
    """
    Walk the JSON data recursively and filter as needed.
    """
    # Remove orphaned SourceMobs
    referenced_ids = set()
    if "composition_mob" in data:
        for slot in data["composition_mob"].get("slots", []):
            segment = slot.get("segment", {})
            collect_referenced_mob_ids(segment, referenced_ids)

    if "source_mobs" in data:
        data["source_mobs"] = [
            sm for sm in data["source_mobs"]
            if sm.get("mob_id") in referenced_ids
        ]

    # Filter slots (remove audio/data)
    if "composition_mob" in data:
        slots = data["composition_mob"].get("slots", [])
        data["composition_mob"]["slots"] = [
            s for s in slots
            if s.get("data_definition") == "Picture" or s.get("data_definition") == "Timecode"
        ]

    # Filter essence descriptors
    if "source_mobs" in data:
        for sm in data["source_mobs"]:
            sm["essence_descriptor"] = filter_essence_descriptor(sm.get("essence_descriptor", {}))

    # Filter TaggedValues
    if "composition_mob" in data:
        tags = data["composition_mob"].get("TaggedValues", [])
        data["composition_mob"]["TaggedValues"] = filter_taggedvalues(tags, excluded_taggedvalues)

    if "source_mobs" in data:
        for sm in data["source_mobs"]:
            tags = sm.get("TaggedValues", [])
            sm["TaggedValues"] = filter_taggedvalues(tags, excluded_taggedvalues)

    return data

def collect_referenced_mob_ids(segment, mob_ids_set):
    """
    Recursively collect mob_ids referenced by SourceClips.
    """
    if not segment:
        return
    if segment.get("class_name") == "SourceClip":
        sid = segment.get("source_id")
        if sid:
            mob_ids_set.add(sid)
    # Recursively look in input_segments or components
    for key in ["input_segments", "components"]:
        nested = segment.get(key, [])
        for s in nested:
            collect_referenced_mob_ids(s, mob_ids_set)

def main():
    parser = argparse.ArgumentParser(description="Trim AAF JSON")
    parser.add_argument("--input", required=True, help="Input JSON file")
    parser.add_argument("--output", required=True, help="Output JSON file")
    parser.add_argument("--exclude", default="exclude_taggedvalues.txt", help="File listing TaggedValues to remove")
    args = parser.parse_args()

    # Load exclusion list
    excluded_taggedvalues = load_exclusion_list(args.exclude)
    print(f"Loaded {len(excluded_taggedvalues)} exclusion tags.")

    # Load JSON
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Process
    data = process_json(data, excluded_taggedvalues)

    # Save
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Saved cleaned JSON to {args.output}")

if __name__ == "__main__":
    main()
