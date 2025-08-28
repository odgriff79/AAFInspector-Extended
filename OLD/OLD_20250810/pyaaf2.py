#!/usr/bin/env python3
# Lists every unique SourceClip referenced by the top-level CompositionMob.
import sys, csv
from collections import OrderedDict

try:
    import aaf2  # pip install pyaaf2==1.4.0
except Exception as e:
    sys.exit("ERROR: pyaaf2==1.4.0 required. pip install pyaaf2==1.4.0")

def walk_sourceclips(seg):
    comps = []
    try:
        comps = list(seg.components)
    except Exception:
        comps = None
    if comps:
        for c in comps:
            yield from walk_sourceclips(c)
    else:
        if getattr(seg, "class_name", None) == "SourceClip":
            yield seg

def first_media_url(mob):
    phys = mob
    try:
        if mob.class_name == "MasterMob":
            for slot in mob.slots():
                s = slot.segment
                if getattr(s, "class_name", None) == "SourceClip":
                    pmob = mob.root.content.lookup_mob(s.source_id)
                    if pmob:
                        phys = pmob
                        break
    except Exception:
        pass
    try:
        ed = phys.essence_descriptor
        if ed:
            for loc in getattr(ed, "locators", []):
                if hasattr(loc, "url"):
                    return loc.url
    except Exception:
        pass
    return None

def read_attrs(mob):
    tape_id = None
    disk_label = None
    try:
        for tv in getattr(mob, "comments", []):
            n = str(tv.get("Name","")).lower()
            if n == "tapeid":
                tape_id = tv.get("Value")
    except Exception:
        pass
    try:
        for tv in getattr(mob, "attributes", []):
            n = str(tv.get("Name","")).lower()
            if n in {"disklabel","disk label","_importdisklabel","_importdisklab"}:
                disk_label = tv.get("Value")
    except Exception:
        pass
    return tape_id, disk_label

def pick_top_comp_mob(f):
    comps = [m for m in f.content.mobs() if m.class_name == "CompositionMob"]
    if not comps:
        return None
    comps.sort(key=lambda m: (len(list(m.slots())), m.name or ""), reverse=True)
    return comps[0]

def main(aaf_path):
    with aaf2.open(aaf_path) as f:
        top = pick_top_comp_mob(f)
        if not top:
            print("No CompositionMob found.")
            return
        print(f'Top CompositionMob: "{top.name}"')

        seen = set()
        rows = []
        for slot in top.slots():
            seg = slot.segment
            for sc in walk_sourceclips(seg):
                src_mob_id = getattr(sc, "source_id", None)
                src_slot_id = getattr(sc, "source_slot_id", None)
                key = (str(src_mob_id), int(src_slot_id) if src_slot_id is not None else None)
                if key in seen:
                    continue
                seen.add(key)

                mob_name = mob_class = url = tape_id = disk_label = None
                try:
                    if src_mob_id:
                        ref = f.content.lookup_mob(src_mob_id)
                        mob_name = ref.name
                        mob_class = ref.class_name
                        url = first_media_url(ref)
                        tape_id, disk_label = read_attrs(ref)
                except Exception:
                    pass

                rows.append(OrderedDict([
                    ("record_slot_id", getattr(slot, "slot_id", None)),
                    ("record_slot_name", getattr(slot, "name", None)),
                    ("source_mob_umid", str(src_mob_id) if src_mob_id else None),
                    ("source_slot_id", int(src_slot_id) if src_slot_id is not None else None),
                    ("referenced_mob_class", mob_class),
                    ("referenced_mob_name", mob_name),
                    ("media_url", url),
                    ("tape_id", tape_id),
                    ("disk_label", disk_label),
                ]))

        if not rows:
            print("No SourceClip segments found in the top-level composition.")
            return

        out_csv = aaf_path + ".top_comp_sourceclips.csv"
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

        print(f"\nFound {len(rows)} unique SourceClip references.")
        for r in rows:
            print(f'- [{r["referenced_mob_class"] or "?"}] {r["referenced_mob_name"] or "?"} '
                  f'UMID={r["source_mob_umid"]} URL={r["media_url"] or "—"}')
        print(f'\nWrote CSV: {out_csv}')

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python list_top_comp_sources.py <file.aaf>")
    main(sys.argv[1])
