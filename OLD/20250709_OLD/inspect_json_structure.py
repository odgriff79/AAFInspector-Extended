import json
from tkinter import filedialog
from collections import Counter

def inspect_json_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f"{path}.{k}" if path else k
            print(new_path)
            inspect_json_keys(v, new_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            inspect_json_keys(item, path + f"[{i}]")

if __name__ == "__main__":
    path = filedialog.askopenfilename(title="Select JSON")
    if not path:
        print("No file selected.")
        exit()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print("✅ Top-level keys:")
    if isinstance(data, dict):
        for k in data.keys():
            print("-", k)
    elif isinstance(data, list):
        print("Top-level list")

    print("\n✅ Recursing keys (first 100):")
    counter = Counter()
    def count_keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                counter[k] += 1
                count_keys(v)
        elif isinstance(o, list):
            for i in o:
                count_keys(i)
    count_keys(data)
    for k, v in counter.most_common(100):
        print(f"{k}: {v}")
