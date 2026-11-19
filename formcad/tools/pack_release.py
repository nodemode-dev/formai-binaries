#!/usr/bin/env python3
import argparse
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fcbin

REPO = "https://github.com/nodemode-dev/formai-binaries"


def pack(args):
    kind = fcbin.KINDS[args.kind]
    record = fcbin.load(args.record)
    release = record["release"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stage_root = Path(args.stage_root)
    work = Path(args.work) if args.work else out / ".verify"
    made = []
    for triple, target in record["targets"].items():
        folder = stage_root / target["folder"]
        if not folder.is_dir():
            print(f"skip {triple}: no staged folder {folder}")
            continue
        for name, want in record["documents"].items():
            if fcbin.sha256_file(folder / name) != want:
                sys.exit(f"{triple}: notice {name} changed since the record was written")
        problems = fcbin.verify_folder(folder, target["files"])
        if problems:
            sys.exit(f"{triple}: staged folder does not match the record: {problems[:3]}")
        archive = out / target["archive"]
        fcbin.reproducible_tar(folder, target["folder"], archive)
        check = work / triple
        fcbin.extract_checked(archive, check)
        problems = fcbin.verify_folder(check / target["folder"], target["files"])
        shutil.rmtree(check)
        if problems:
            sys.exit(f"{triple}: re-extracted archive does not match the record: {problems[:3]}")
        print(f"packed {archive.name} ({archive.stat().st_size} bytes), re-extracted and verified {len(target['files'])} files")
        made.append(archive)
    if not made:
        sys.exit("nothing was packed")
    finish(args, record, out, made)


def finalize(args):
    kind = fcbin.KINDS[args.kind]
    record = fcbin.load(args.record)
    out = Path(args.out)
    made = [out / t["archive"] for t in record["targets"].values() if (out / t["archive"]).is_file()]
    missing = [t["archive"] for t in record["targets"].values() if not (out / t["archive"]).is_file()]
    if missing:
        sys.exit(f"archives named in the record are missing: {missing}")
    finish(args, record, out, made)


def finish(args, record, out, made):
    kind = fcbin.KINDS[args.kind]
    if args.fetch_sources:
        made.append(build_source_asset(args, record, out))
    record_copy = out / kind["record"]
    if Path(args.record).resolve() != record_copy.resolve():
        shutil.copyfile(args.record, record_copy)
    made.append(record_copy)
    lock = Path(args.lock) if args.lock else None
    if lock and lock.is_file():
        if lock.resolve() != (out / "requirements.lock").resolve():
            shutil.copyfile(lock, out / "requirements.lock")
        made.append(out / "requirements.lock")
    sums = "".join(f"{fcbin.sha256_file(p)}  {p.name}\n" for p in sorted(made, key=lambda p: p.name))
    (out / "SHA256SUMS").write_text(sums, encoding="utf-8")
    notes = release_notes(kind, record, [p.name for p in made])
    (out / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    print(f"wrote SHA256SUMS ({len(made)} entries) and RELEASE_NOTES.md in {out}")


def build_source_asset(args, record, out):
    kind = fcbin.KINDS[args.kind]
    release = record["release"]
    name = kind["source_asset"].format(release=release).removesuffix(".tar.gz")
    tree = out / ".source" / name
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    for item in record["source"]["upstream"]:
        url = item["url"]
        if not url.endswith(".tar.gz"):
            continue
        target = tree / url.rsplit("/", 1)[-1] if "/archive/" not in url else tree / f"{item['name'].split()[0]}-{item['version']}.tar.gz"
        with urllib.request.urlopen(url, timeout=120) as response, open(target, "wb") as handle:
            shutil.copyfileobj(response, handle)
        digest = fcbin.sha256_file(target)
        if item.get("sha256") and item["sha256"] != digest:
            sys.exit(f"{item['name']}: upstream sha256 {digest} does not match the pin {item['sha256']}")
        item["sha256"] = digest
    fcbin.dump(args.record, record)
    if args.lock:
        shutil.copyfile(args.lock, tree / "requirements.lock")
    here = Path(args.records_dir or Path(args.record).parent).parent
    for extra in ("SOURCES.md", "LGPL-OFFER.txt"):
        if (here / extra).is_file():
            shutil.copyfile(here / extra, tree / extra)
    for src in args.worker_src or []:
        src = Path(src)
        destination = tree / "worker" / src.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, destination)
    archive = out / f"{name}.tar.gz"
    fcbin.reproducible_tar(tree, name, archive)
    shutil.rmtree(out / ".source")
    return archive


def release_notes(kind, record, assets):
    tag = f"{kind['tag']}-{record['release']}"
    lines = [
        f"# {kind['program']} {record['release']}",
        "",
        f"Tag `{tag}`. Licence: {record['licence']}.",
        "",
        "## Assets",
        "",
    ]
    lines += [f"- `{a}`" for a in sorted(assets)]
    lines += [
        "",
        "## Verify",
        "",
        "Check `SHA256SUMS` with `sha256sum -c SHA256SUMS`. FormCAD checks every file of an archive against its shipped record before it places anything.",
        "",
        "## Source and written offer",
        "",
        f"The source asset `{kind['source_asset'].format(release=record['release'])}` and `requirements.lock` are on this same release page. The written offer is in `LGPL-OFFER.txt` and in `THIRD-PARTY-NOTICES.txt` inside every archive. This release and its source asset stay available for at least three years after the last distribution of this build. A published tag is never replaced.",
        "",
        "## Platforms",
        "",
    ]
    for triple, target in record["targets"].items():
        lines.append(f"- `{triple}`: {len(target['files'])} files, minimum OS {target.get('minimum_os') or 'not recorded'}")
    return "\n".join(lines) + "\n"


def app_record(args):
    kind = fcbin.KINDS[args.kind]
    record = fcbin.load(args.record)
    flat = {
        "schema": 1,
        "program": kind["program"],
        "release": record["release"],
        "licence": record["licence"],
        "source": REPO,
        "url": f"{REPO}/releases/download/{kind['tag']}-{{release}}/{kind['asset']}",
        "folder": kind["folder"],
        "triples": list(fcbin.TRIPLES),
        "files": [],
    }
    for triple, target in record["targets"].items():
        for entry in target["files"]:
            flat["files"].append({"triple": triple, "path": entry["path"], "sha256": entry["sha256"]})
    fcbin.dump(args.out, flat)
    print(f"wrote {args.out} with {len(flat['files'])} file rows")


def check(args):
    record = fcbin.load(args.record)
    out = Path(args.out)
    sums = {}
    for line in (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    bad = []
    for name, digest in sums.items():
        if fcbin.sha256_file(out / name) != digest:
            bad.append(name)
    for triple, target in record["targets"].items():
        archive = out / target["archive"]
        if not archive.is_file():
            continue
        work = out / ".check" / triple
        fcbin.extract_checked(archive, work)
        bad += [f"{triple}: {p}" for p in fcbin.verify_folder(work / target["folder"], target["files"])]
        shutil.rmtree(work)
    if bad:
        sys.exit("check failed: " + "; ".join(bad[:5]))
    print(f"check passed: {len(sums)} checksums and every archive match the record")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pack", "finalize", "app-record", "check"):
        p = sub.add_parser(name)
        p.add_argument("--kind", choices=list(fcbin.KINDS), required=True)
        p.add_argument("--record", required=True)
        p.add_argument("--out", required=True)
        if name in ("pack", "finalize"):
            if name == "pack":
                p.add_argument("--stage-root", required=True)
                p.add_argument("--work")
            p.add_argument("--lock")
            p.add_argument("--records-dir")
            p.add_argument("--fetch-sources", action="store_true")
            p.add_argument("--worker-src", nargs="*")
    args = ap.parse_args()
    {"pack": pack, "finalize": finalize, "app-record": app_record, "check": check}[args.cmd](args)


if __name__ == "__main__":
    main()
