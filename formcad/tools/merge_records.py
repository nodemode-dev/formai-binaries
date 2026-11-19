#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fcbin


def main():
    ap = argparse.ArgumentParser(description="merge the per-triple records of the build jobs into one record")
    ap.add_argument("--out", required=True)
    ap.add_argument("records", nargs="+")
    args = ap.parse_args()
    merged = None
    for path in args.records:
        record = fcbin.load(path)
        if merged is None:
            merged = record
            continue
        for key in ("schema", "program", "release", "licence", "documents"):
            if record.get(key) != merged.get(key):
                sys.exit(f"{path}: {key} differs from the first record")
        if record["source"].get("lock") != merged["source"].get("lock"):
            sys.exit(f"{path}: the lock differs from the first record, every platform must build from the same lock")
        for triple, target in record["targets"].items():
            if triple in merged["targets"]:
                sys.exit(f"{path}: {triple} appears twice")
            merged["targets"][triple] = target
    merged["targets"] = dict(sorted(merged["targets"].items()))
    fcbin.dump(args.out, merged)
    print(f"merged {len(args.records)} records into {args.out}: {', '.join(merged['targets'])}")


if __name__ == "__main__":
    main()
