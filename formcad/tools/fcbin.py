import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import tarfile
from pathlib import Path

KINDS = {
    "kernel": {
        "program": "formcad-kernel",
        "tag": "occt",
        "asset": "occt-lgpl-{release}-{triple}.tar.gz",
        "folder": "occt-lgpl-{release}-{triple}",
        "record": "occt-licence.json",
        "readme": "README-occt.md",
        "source_asset": "occt-source-{release}.tar.gz",
        "licence": "LGPL-2.1-only WITH OCCT-exception-1.0",
        "documents": ["LGPL-2.1.txt", "OCCT-EXCEPTION.txt", "THIRD-PARTY-NOTICES.txt"],
        "protocol": 2,
    },
    "mesher": {
        "program": "formcad-mesher",
        "tag": "mesher",
        "asset": "mesher-lgpl-{release}-{triple}.tar.gz",
        "folder": "mesher-lgpl-{release}-{triple}",
        "record": "mesher-licence.json",
        "readme": "README-mesher.md",
        "source_asset": "mesher-source-{release}.tar.gz",
        "licence": "LGPL-2.1-only",
        "documents": ["LGPL-2.1.txt", "NETGEN-LICENSE.txt", "THIRD-PARTY-NOTICES.txt"],
        "protocol": 1,
    },
}

TRIPLES = (
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "x86_64-pc-windows-msvc",
)

EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "315532800"))


def release_arg(value):
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.\-]{0,40}", value):
        raise argparse.ArgumentTypeError("a release number holds letters, digits, dots and hyphens")
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def program_name(kind, triple):
    return KINDS[kind]["program"] + (".exe" if "windows" in triple else "")


def walk_files(root):
    out = []
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            full = Path(folder) / name
            out.append(full.relative_to(root).as_posix())
    return sorted(out)


def file_entries(root, program):
    entries = []
    for rel in walk_files(root):
        full = Path(root) / rel
        entry = {"path": rel, "sha256": sha256_file(full), "bytes": full.stat().st_size}
        if full.is_symlink():
            entry["link"] = os.readlink(full)
        entries.append(entry)
    entries.sort(key=lambda e: (e["path"] != program, e["path"]))
    return entries


def elf_needed(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return None
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    shentsize, shnum = struct.unpack_from("<HH", data, 0x3A)
    sections = []
    for i in range(shnum):
        base = shoff + i * shentsize
        if base + 64 > len(data):
            return None
        name, kind, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from("<IIQQQQIIQQ", data, base)
        sections.append((kind, offset, size, link))
    needed = []
    for kind, offset, size, link in sections:
        if kind != 6:
            continue
        strtab = sections[link]
        for pos in range(offset, offset + size, 16):
            tag, value = struct.unpack_from("<qQ", data, pos)
            if tag == 0:
                break
            if tag == 1:
                start = strtab[1] + value
                end = data.index(b"\0", start)
                needed.append(data[start:end].decode("utf-8", "replace"))
    return needed


def macho_needed(path):
    try:
        out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = []
    for line in out.splitlines()[1:]:
        match = re.match(r"\s+(\S+)", line)
        if match:
            names.append(match.group(1))
    return names


def system_libraries(root, triple):
    present = {Path(rel).name for rel in walk_files(root)}
    wanted = set()
    for rel in walk_files(root):
        full = Path(root) / rel
        if full.is_symlink():
            continue
        if "linux" in triple:
            names = elf_needed(full)
        elif "apple" in triple:
            names = macho_needed(full) if full.suffix in ("", ".so", ".dylib") else None
        else:
            names = None
        for name in names or []:
            base = Path(name).name
            if base not in present and not name.startswith("@"):
                wanted.add(base if "linux" in triple else name)
    return sorted(wanted)


def reproducible_tar(source_dir, folder, target):
    source_dir = Path(source_dir)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        names = [folder] + [folder + "/" + rel for rel in all_paths(source_dir)]
        for arc in names:
            real = source_dir if arc == folder else source_dir / arc[len(folder) + 1:]
            info = tarfile.TarInfo(arc)
            info.mtime = EPOCH
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if real.is_symlink():
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(real)
                info.mode = 0o777
                tar.addfile(info)
            elif real.is_dir():
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.size = real.stat().st_size
                info.mode = 0o755 if os.access(real, os.X_OK) else 0o644
                with open(real, "rb") as handle:
                    tar.addfile(info, handle)
    with open(target, "wb") as out:
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0, compresslevel=6) as gz:
            gz.write(raw.getvalue())


def all_paths(root):
    out = []
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        base = Path(folder).relative_to(root)
        for name in sorted(dirs) + sorted(files):
            out.append((base / name).as_posix())
    return sorted(out)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def extract_checked(archive, destination):
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")


def verify_folder(folder, expected):
    present = {}
    for rel in walk_files(folder):
        present[rel] = Path(folder) / rel
    problems = []
    for entry in expected:
        rel = entry["path"]
        if rel not in present:
            problems.append(f"{rel} is missing")
        elif sha256_file(present[rel]) != entry["sha256"]:
            problems.append(f"{rel} does not match its recorded sha256")
        elif present[rel].stat().st_size != entry["bytes"]:
            problems.append(f"{rel} has a different size than recorded")
    listed = {e["path"] for e in expected}
    for rel in present:
        if rel not in listed:
            problems.append(f"{rel} is not in the record")
    return problems
