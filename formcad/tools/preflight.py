#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fcbin

HERE = Path(__file__).resolve().parent.parent
HEX = re.compile(r"^[0-9a-f]{64}$")


def check_record(kind_name, path, problems):
    kind = fcbin.KINDS[kind_name]
    record = fcbin.load(path)
    for key in ("schema", "program", "release", "licence", "documents", "source", "targets"):
        if key not in record:
            problems.append(f"{path.name}: missing {key}")
    if record.get("schema") != 2 or record.get("program") != kind["program"]:
        problems.append(f"{path.name}: wrong schema or program")
    for name, digest in record.get("documents", {}).items():
        licence = HERE / "licences" / name
        if licence.is_file() and fcbin.sha256_file(licence) != digest:
            problems.append(f"{path.name}: document {name} differs from licences/{name}")
    for triple, target in record.get("targets", {}).items():
        if triple not in fcbin.TRIPLES:
            problems.append(f"{path.name}: unknown triple {triple}")
        program = fcbin.program_name(kind_name, triple)
        files = target.get("files", [])
        if not files or files[0]["path"] != program:
            problems.append(f"{path.name}/{triple}: the program must be the first file")
        for entry in files:
            if not HEX.match(entry["sha256"]) or entry["bytes"] < 0:
                problems.append(f"{path.name}/{triple}: bad entry {entry['path']}")
        for key in ("protocol", "archive", "folder", "library_dir", "system_libraries_needed"):
            if key not in target:
                problems.append(f"{path.name}/{triple}: missing {key}")
        names = {e["path"] for e in files}
        for doc in record.get("documents", {}):
            if doc not in names:
                problems.append(f"{path.name}/{triple}: notice {doc} is not in the file list")
    for item in record.get("source", {}).get("upstream", []):
        if record.get("targets") and item.get("url", "").endswith(".tar.gz") and not item.get("sha256"):
            problems.append(f"{path.name}: upstream {item['name']} has no pinned sha256")
    return record


def check_app_record(kind_name, record, app_path, problems):
    if not app_path.is_file():
        problems.append(f"{app_path} is missing")
        return
    app = fcbin.load(app_path)
    if app.get("release") != record.get("release"):
        problems.append(f"{app_path.name}: release {app.get('release')} differs from {record.get('release')}")
    want = sorted((t, e["path"], e["sha256"]) for t, target in record["targets"].items() for e in target["files"])
    have = sorted((e["triple"], e["path"], e["sha256"]) for e in app.get("files", []))
    if want != have:
        problems.append(f"{app_path.name}: the app record does not list the same files as the release record")


def main():
    ap = argparse.ArgumentParser(description="check the formCAD records of formai-binaries, and their copies inside the app")
    ap.add_argument("--records", default=str(HERE / "records"))
    ap.add_argument("--app-data", help="backend/app/data of the FormCAD repository")
    ap.add_argument("--published", action="store_true", help="records must be filled in (release set, targets present)")
    ap.add_argument("--kind", choices=list(fcbin.KINDS), help="with --published, only this program's record must be filled in")
    args = ap.parse_args()
    problems = []
    for kind_name, kind in fcbin.KINDS.items():
        path = Path(args.records) / kind["record"]
        record = check_record(kind_name, path, problems)
        if args.published and args.kind in (None, kind_name) and not (record.get("release") and record.get("targets")):
            problems.append(f"{path.name}: not filled in")
        if args.app_data and record.get("targets"):
            check_app_record(kind_name, record, Path(args.app_data) / f"{'kernel' if kind_name == 'kernel' else 'mesher'}-licence.json", problems)
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print("preflight passed")


if __name__ == "__main__":
    main()
