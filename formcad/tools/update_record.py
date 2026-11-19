#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fcbin

UPSTREAM = {
    "kernel": [
        {
            "name": "OpenCascade Technology",
            "version": "8.0.1",
            "licence": "LGPL-2.1-only WITH OCCT-exception-1.0",
            "url": "https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V8_0_1.tar.gz",
            "sha256": None,
        },
        {
            "name": "cadquery-ocp-novtk",
            "version": "8.0.1.1.0",
            "licence": "Apache-2.0",
            "url": "https://pypi.org/project/cadquery-ocp-novtk/8.0.1.1.0/",
            "sha256": None,
        },
    ],
    "mesher": [
        {
            "name": "Netgen",
            "version": "6.2.2608",
            "licence": "LGPL-2.1-only",
            "url": "https://github.com/NGSolve/netgen/archive/refs/tags/v6.2.2608.tar.gz",
            "sha256": None,
        },
        {
            "name": "OpenCascade Technology (netgen-occt build)",
            "version": "7.8.1",
            "licence": "LGPL-2.1-only WITH OCCT-exception-1.0",
            "url": "https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V7_8_1.tar.gz",
            "sha256": None,
        },
    ],
}

GATES = {
    "kernel": {
        "forbidden_names": ["libx264*", "libx265*", "libavcodec*", "libgpl*"],
        "needs_review": ["libfreeimage*", "libraw*", "libjasper*", "libquadmath*", "libgfortran*", "libgomp*"],
        "note": "review entries are bundled by the OpenCascade wheel or by numpy, scipy and scikit-learn wheels; each needs its licence text and source location in THIRD-PARTY-NOTICES.txt before a release is published",
    },
    "mesher": {
        "forbidden_names": ["libx264*", "libgpl*"],
        "needs_review": ["libGLU*", "libXmu*", "libXt*", "ng_mpich*", "ng_openmpi*", "libnggui*", "libtogl*"],
        "note": "Netgen's wheel carries its GUI and MPI pieces; the mesher never uses them and the freeze script excludes the GUI modules",
    },
}


def main():
    ap = argparse.ArgumentParser(description="record a staged build for one platform triple")
    ap.add_argument("--kind", choices=list(fcbin.KINDS), required=True)
    ap.add_argument("--record", required=True)
    ap.add_argument("--triple", choices=fcbin.TRIPLES, required=True)
    ap.add_argument("--stage", required=True, help="the archive folder, already holding the program, its library folder and the notices")
    ap.add_argument("--release", required=True, type=fcbin.release_arg)
    ap.add_argument("--lock", help="requirements.lock of the build")
    ap.add_argument("--library-dir", default=None)
    ap.add_argument("--minimum-os", default=None)
    ap.add_argument("--toolchain", default="")
    ap.add_argument("--verification", default="")
    args = ap.parse_args()

    kind = fcbin.KINDS[args.kind]
    stage = Path(args.stage)
    program = fcbin.program_name(args.kind, args.triple)
    if not (stage / program).is_file():
        sys.exit(f"{program} is not in {stage}")
    documents = {}
    for name in kind["documents"]:
        if not (stage / name).is_file():
            sys.exit(f"notice file {name} is not in {stage}")
        documents[name] = fcbin.sha256_file(stage / name)

    library_dir = args.library_dir
    if library_dir is None:
        found = [p.name for p in sorted(stage.iterdir()) if p.is_dir()]
        if len(found) != 1:
            sys.exit(f"expected one library folder beside the program, found {found}")
        library_dir = found[0]

    path = Path(args.record)
    record = fcbin.load(path) if path.is_file() else {}
    record.update(
        {
            "schema": 2,
            "program": kind["program"],
            "release": args.release,
            "licence": kind["licence"],
            "documents": documents,
        }
    )
    source = record.get("source") or {}
    source["upstream"] = source.get("upstream") or UPSTREAM[args.kind]
    source["asset"] = kind["source_asset"].format(release=args.release)
    if args.lock:
        source["lock"] = {"path": Path(args.lock).name, "sha256": fcbin.sha256_file(args.lock)}
    record["source"] = source

    targets = record.get("targets") or {}
    entries = fcbin.file_entries(stage, program)
    needed = fcbin.system_libraries(stage, args.triple)
    targets[args.triple] = {
        "protocol": kind["protocol"],
        "archive": kind["asset"].format(release=args.release, triple=args.triple),
        "folder": kind["folder"].format(release=args.release, triple=args.triple),
        "programs": [program],
        "library_dir": library_dir,
        "libraries_found_by": "PyInstaller bootloader: the library folder beside the program",
        "minimum_os": args.minimum_os,
        "toolchain": args.toolchain,
        "verification": args.verification,
        "system_libraries_needed": needed,
        "files": entries,
    }
    record["targets"] = dict(sorted(targets.items()))
    record["licence_gates"] = GATES[args.kind]
    fcbin.dump(path, record)
    print(f"recorded {len(entries)} files for {args.triple} in {path}")


if __name__ == "__main__":
    main()
