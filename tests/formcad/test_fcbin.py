import argparse
import gzip
import hashlib
import io
import os
import struct
import tarfile

import pytest

UNIT = "formcad/tools/fcbin.py"


@pytest.fixture
def fcbin(load_module):
    return load_module(UNIT)


def build_elf(needed, sections=True):
    strtab = b"\0"
    offsets = []
    for name in needed:
        offsets.append(len(strtab))
        strtab += name.encode() + b"\0"
    dynamic = b"".join(struct.pack("<qQ", 1, off) for off in offsets)
    dynamic += struct.pack("<qQ", 5, 0)
    dynamic += struct.pack("<qQ", 0, 0)
    dynamic += struct.pack("<qQ", 1, 0)
    str_off = 64 + 56
    dyn_off = str_off + len(strtab)
    sh_off = dyn_off + len(dynamic)
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        ident, 3, 62, 1, 0, 64, sh_off if sections else 0, 0, 64, 56, 1, 64, 3 if sections else 0, 0,
    )
    phdr = struct.pack("<IIQQQQQQ", 2, 6, dyn_off, dyn_off, dyn_off, len(dynamic), len(dynamic), 8)
    body = header + phdr + strtab + dynamic
    if sections:
        body += struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        body += struct.pack("<IIQQQQIIQQ", 0, 3, 2, 0, str_off, len(strtab), 0, 0, 1, 0)
        body += struct.pack("<IIQQQQIIQQ", 0, 6, 3, 0, dyn_off, len(dynamic), 1, 0, 8, 16)
    return body


def fake_otool(tmp_path, monkeypatch):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    log = tmp_path / "otool.log"
    script = bindir / "otool"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$2" >> "{log}"\n'
        'echo "$2:"\n'
        'printf "\\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1345.0.0)\\n"\n'
        'printf "\\t@rpath/libTKernel.8.0.dylib (compatibility version 8.0.0, current version 8.0.1)\\n"\n'
        'printf "\\t/opt/build/lib/libshipped.dylib (compatibility version 1.0.0, current version 1.0.0)\\n"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    return log


def make_tree(root, order):
    content = {
        "formcad-kernel": b"#!/bin/sh\necho kernel\n",
        "README.md": b"read me\n",
        "_internal/libTKernel.so": b"library bytes",
        "_internal/deep/data.bin": bytes(range(256)),
    }
    root.mkdir()
    for rel in order:
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content[rel])
    (root / "formcad-kernel").chmod(0o755)
    (root / "README.md").chmod(0o644)
    (root / "_internal/libTKernel.so").chmod(0o644)
    (root / "_internal/deep/data.bin").chmod(0o644)
    return content


def write_tar(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            if data is None:
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize(
    "value",
    ["8.0.1", "8.0.1-2", "6.2.2608", "occt-8.0.1", "mesher-6.2.2608", "A", "x" * 41],
)
def test_release_names_accepted(fcbin, value):
    assert fcbin.release_arg(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "../8.0.1", "occt/8.0.1", "/tmp/occt-8.0.1", "8.0.1 ", "-8.0.1", ".8.0.1", "8.0.1\n", "8_0_1", "x" * 42],
)
def test_release_names_refused(fcbin, value):
    with pytest.raises(argparse.ArgumentTypeError):
        fcbin.release_arg(value)


def test_sha256_file_matches_hashlib_for_large_file(fcbin, tmp_path):
    data = os.urandom((1 << 20) * 2 + 17)
    target = tmp_path / "big.bin"
    target.write_bytes(data)
    assert fcbin.sha256_file(target) == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "kind, triple, expected",
    [
        ("kernel", "aarch64-apple-darwin", "formcad-kernel"),
        ("kernel", "x86_64-apple-darwin", "formcad-kernel"),
        ("kernel", "x86_64-unknown-linux-gnu", "formcad-kernel"),
        ("kernel", "x86_64-pc-windows-msvc", "formcad-kernel.exe"),
        ("mesher", "aarch64-apple-darwin", "formcad-mesher"),
        ("mesher", "x86_64-unknown-linux-gnu", "formcad-mesher"),
        ("mesher", "x86_64-pc-windows-msvc", "formcad-mesher.exe"),
    ],
)
def test_program_name_per_kind_and_triple(fcbin, kind, triple, expected):
    assert fcbin.program_name(kind, triple) == expected


def test_program_name_unknown_kind_fails(fcbin):
    with pytest.raises(KeyError):
        fcbin.program_name("ffmpeg", "x86_64-unknown-linux-gnu")


def test_walk_files_lists_every_file_once_sorted_posix(fcbin, tmp_path):
    root = tmp_path / "stage"
    make_tree(root, ["_internal/deep/data.bin", "formcad-kernel", "_internal/libTKernel.so", "README.md"])
    (root / "empty_dir").mkdir()
    found = fcbin.walk_files(root)
    assert found == sorted(
        ["README.md", "_internal/deep/data.bin", "_internal/libTKernel.so", "formcad-kernel"]
    )
    assert len(found) == len(set(found))
    assert all("\\" not in rel and not rel.startswith("/") for rel in found)


def test_walk_files_of_empty_folder(fcbin, tmp_path):
    assert fcbin.walk_files(tmp_path) == []


def test_file_entries_program_first_with_real_hash_and_size(fcbin, tmp_path):
    root = tmp_path / "stage"
    content = make_tree(root, ["README.md", "_internal/deep/data.bin", "formcad-kernel", "_internal/libTKernel.so"])
    entries = fcbin.file_entries(root, "formcad-kernel")
    paths = [e["path"] for e in entries]
    assert paths[0] == "formcad-kernel"
    assert paths[1:] == sorted(p for p in content if p != "formcad-kernel")
    assert len(paths) == len(set(paths)) == len(content)
    for entry in entries:
        data = content[entry["path"]]
        assert entry == {
            "path": entry["path"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }


def test_file_entries_link_field_and_hash_of_target(fcbin, tmp_path):
    root = tmp_path / "stage"
    root.mkdir()
    (root / "libA.so.1").write_bytes(b"real library")
    os.symlink("libA.so.1", root / "libA.so")
    entries = {e["path"]: e for e in fcbin.file_entries(root, "prog")}
    assert entries["libA.so"]["link"] == "libA.so.1"
    assert entries["libA.so"]["sha256"] == hashlib.sha256(b"real library").hexdigest()
    assert "link" not in entries["libA.so.1"]


def test_elf_needed_reads_dt_needed_names_in_order(fcbin, tmp_path):
    target = tmp_path / "prog"
    target.write_bytes(build_elf(["libTKernel.so.8.0", "libm.so.6", "libc.so.6"]))
    assert fcbin.elf_needed(target) == ["libTKernel.so.8.0", "libm.so.6", "libc.so.6"]


def test_elf_needed_without_needed_entries_is_empty(fcbin, tmp_path):
    target = tmp_path / "static"
    target.write_bytes(build_elf([]))
    assert fcbin.elf_needed(target) == []


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"MZ\x90\x00 not an elf at all, long enough to hold a header" + b"\0" * 64,
        b"#!/bin/sh\necho hello\n",
        b"\xcf\xfa\xed\xfe" + b"\0" * 100,
        b"\x7fELF\x01\x01" + b"\0" * 100,
        b"\x7fELF\x02\x02" + b"\0" * 100,
    ],
)
def test_elf_needed_gives_nothing_for_other_files(fcbin, tmp_path, data):
    target = tmp_path / "other"
    target.write_bytes(data)
    assert not fcbin.elf_needed(target)


def test_elf_needed_missing_file_gives_none(fcbin, tmp_path):
    assert fcbin.elf_needed(tmp_path / "absent") is None


@pytest.mark.xfail(strict=True, reason="defect: elf_needed raises on a file cut short after the ELF magic instead of giving nothing")
@pytest.mark.parametrize("data", [b"\x7fELF", b"\x7fELF\x02\x01\x01\x00"])
def test_elf_needed_truncated_elf_gives_nothing(fcbin, tmp_path, data):
    target = tmp_path / "cut"
    target.write_bytes(data)
    assert not fcbin.elf_needed(target)


@pytest.mark.xfail(strict=True, reason="defect: elf_needed reads section headers only, so an ELF with PT_DYNAMIC but a stripped section table reports no libraries")
def test_elf_needed_from_program_header_when_sections_stripped(fcbin, tmp_path):
    target = tmp_path / "stripped"
    target.write_bytes(build_elf(["libm.so.6"], sections=False))
    assert fcbin.elf_needed(target) == ["libm.so.6"]


def test_macho_needed_reads_names_from_otool(fcbin, tmp_path, monkeypatch):
    log = fake_otool(tmp_path, monkeypatch)
    target = tmp_path / "libX.dylib"
    target.write_bytes(b"\xcf\xfa\xed\xfe")
    assert fcbin.macho_needed(target) == [
        "/usr/lib/libSystem.B.dylib",
        "@rpath/libTKernel.8.0.dylib",
        "/opt/build/lib/libshipped.dylib",
    ]
    assert log.read_text(encoding="utf-8").splitlines() == [str(target)]


def test_macho_needed_without_otool_gives_none(fcbin, tmp_path, monkeypatch):
    empty = tmp_path / "nobin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert fcbin.macho_needed(tmp_path / "anything") is None


def test_system_libraries_linux_leaves_out_shipped(fcbin, tmp_path):
    root = tmp_path / "stage"
    (root / "_internal").mkdir(parents=True)
    (root / "formcad-kernel").write_bytes(build_elf(["libTKernel.so.8.0", "libc.so.6", "libm.so.6"]))
    (root / "_internal/libTKernel.so.8.0").write_bytes(build_elf(["libstdc++.so.6", "libc.so.6"]))
    (root / "README.md").write_bytes(b"text, not a program\n")
    os.symlink("libTKernel.so.8.0", root / "_internal/libTKernel.so")
    assert fcbin.system_libraries(root, "x86_64-unknown-linux-gnu") == [
        "libc.so.6",
        "libm.so.6",
        "libstdc++.so.6",
    ]


def test_system_libraries_apple_leaves_out_shipped_and_rpath(fcbin, tmp_path, monkeypatch):
    log = fake_otool(tmp_path, monkeypatch)
    root = tmp_path / "stage"
    (root / "_internal").mkdir(parents=True)
    (root / "formcad-kernel").write_bytes(b"\xcf\xfa\xed\xfe")
    (root / "_internal/libshipped.dylib").write_bytes(b"\xcf\xfa\xed\xfe")
    (root / "LGPL-2.1.txt").write_bytes(b"licence\n")
    assert fcbin.system_libraries(root, "aarch64-apple-darwin") == ["/usr/lib/libSystem.B.dylib"]
    asked = sorted(log.read_text(encoding="utf-8").splitlines())
    assert asked == sorted([str(root / "formcad-kernel"), str(root / "_internal/libshipped.dylib")])


def test_system_libraries_windows_is_empty(fcbin, tmp_path):
    root = tmp_path / "stage"
    root.mkdir()
    (root / "formcad-kernel.exe").write_bytes(build_elf(["libc.so.6"]))
    assert fcbin.system_libraries(root, "x86_64-pc-windows-msvc") == []


def test_all_paths_lists_folders_and_files_sorted(fcbin, tmp_path):
    root = tmp_path / "stage"
    make_tree(root, ["_internal/deep/data.bin", "README.md", "formcad-kernel", "_internal/libTKernel.so"])
    (root / "void").mkdir()
    assert fcbin.all_paths(root) == sorted(
        [
            "README.md",
            "_internal",
            "_internal/deep",
            "_internal/deep/data.bin",
            "_internal/libTKernel.so",
            "formcad-kernel",
            "void",
        ]
    )


def test_reproducible_tar_same_bytes_for_other_times_and_order(fcbin, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    make_tree(first, ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"])
    make_tree(second, ["_internal/deep/data.bin", "_internal/libTKernel.so", "README.md", "formcad-kernel"])
    for index, rel in enumerate(["formcad-kernel", "README.md", "_internal", "_internal/deep/data.bin"]):
        os.utime(first / rel, (1_000_000_000 + index, 1_000_000_000 + index))
        os.utime(second / rel, (1_700_000_000 - index * 99, 1_700_000_000 - index * 99))
    folder = "occt-lgpl-8.0.1-x86_64-unknown-linux-gnu"
    one = tmp_path / "one.tar.gz"
    two = tmp_path / "two.tar.gz"
    fcbin.reproducible_tar(first, folder, one)
    fcbin.reproducible_tar(second, folder, two)
    assert one.read_bytes() == two.read_bytes()
    assert one.read_bytes()[4:8] == b"\0\0\0\0"


def test_reproducible_tar_changes_when_content_changes(fcbin, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    order = ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"]
    make_tree(first, order)
    make_tree(second, order)
    (second / "README.md").write_bytes(b"read mE\n")
    fcbin.reproducible_tar(first, "f", tmp_path / "one.tar.gz")
    fcbin.reproducible_tar(second, "f", tmp_path / "two.tar.gz")
    assert (tmp_path / "one.tar.gz").read_bytes() != (tmp_path / "two.tar.gz").read_bytes()


def test_reproducible_tar_fixed_owner_times_modes_and_content(fcbin, tmp_path):
    root = tmp_path / "stage"
    content = make_tree(root, ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"])
    os.symlink("libTKernel.so", root / "_internal/libTK.so")
    folder = "occt-lgpl-8.0.1-aarch64-apple-darwin"
    target = tmp_path / "out.tar.gz"
    fcbin.reproducible_tar(root, folder, target)
    with tarfile.open(target) as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        assert names[0] == folder
        assert names[1:] == sorted(
            folder + "/" + rel for rel in list(content) + ["_internal", "_internal/deep", "_internal/libTK.so"]
        )
        for member in members:
            assert member.mtime == fcbin.EPOCH
            assert (member.uid, member.gid) == (0, 0)
            assert (member.uname, member.gname) == ("", "")
        by_name = {m.name[len(folder) + 1:]: m for m in members[1:]}
        assert by_name["formcad-kernel"].mode == 0o755
        assert by_name["README.md"].mode == 0o644
        assert by_name["_internal/libTKernel.so"].mode == 0o644
        assert by_name["_internal"].isdir() and by_name["_internal"].mode == 0o755
        assert by_name["_internal/libTK.so"].issym()
        assert by_name["_internal/libTK.so"].linkname == "libTKernel.so"
        for rel, data in content.items():
            assert tar.extractfile(by_name[rel]).read() == data
    with gzip.open(target) as handle:
        assert handle.read(5) != b""


def test_extract_checked_restores_archive_and_clears_old_content(fcbin, tmp_path):
    root = tmp_path / "stage"
    content = make_tree(root, ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"])
    archive = tmp_path / "a.tar.gz"
    fcbin.reproducible_tar(root, "pack", archive)
    destination = tmp_path / "out"
    destination.mkdir()
    (destination / "stale.txt").write_text("old", encoding="utf-8")
    fcbin.extract_checked(archive, destination)
    assert not (destination / "stale.txt").exists()
    assert fcbin.walk_files(destination) == sorted("pack/" + rel for rel in content)
    for rel, data in content.items():
        assert (destination / "pack" / rel).read_bytes() == data
    assert os.access(destination / "pack/formcad-kernel", os.X_OK)
    assert fcbin.verify_folder(destination / "pack", fcbin.file_entries(root, "formcad-kernel")) == []


def test_extract_checked_keeps_absolute_member_inside(fcbin, tmp_path):
    outside = tmp_path / "outside" / "evil.txt"
    archive = tmp_path / "abs.tar.gz"
    write_tar(archive, [(tarfile.TarInfo(str(outside)), b"evil")])
    destination = tmp_path / "out"
    try:
        fcbin.extract_checked(archive, destination)
    except tarfile.TarError:
        pass
    assert not outside.exists()
    assert not outside.parent.exists()


def test_extract_checked_refuses_member_climbing_out(fcbin, tmp_path):
    archive = tmp_path / "climb.tar.gz"
    write_tar(archive, [(tarfile.TarInfo("pack/../../escaped.txt"), b"evil")])
    destination = tmp_path / "deep" / "out"
    with pytest.raises(tarfile.TarError):
        fcbin.extract_checked(archive, destination)
    assert not (tmp_path / "deep" / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.parametrize("linkname", ["../../secret.txt", "/etc/passwd"])
def test_extract_checked_refuses_symlink_pointing_outside(fcbin, tmp_path, linkname):
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    info = tarfile.TarInfo("pack/link")
    info.type = tarfile.SYMTYPE
    info.linkname = linkname
    archive = tmp_path / "link.tar.gz"
    write_tar(archive, [(info, None)])
    destination = tmp_path / "out"
    with pytest.raises(tarfile.TarError):
        fcbin.extract_checked(archive, destination)
    assert not os.path.lexists(destination / "pack" / "link")


def test_extract_checked_refuses_hard_link_pointing_outside(fcbin, tmp_path):
    info = tarfile.TarInfo("pack/hard")
    info.type = tarfile.LNKTYPE
    info.linkname = "../outside.txt"
    archive = tmp_path / "hard.tar.gz"
    write_tar(archive, [(info, None)])
    destination = tmp_path / "out"
    with pytest.raises(tarfile.TarError):
        fcbin.extract_checked(archive, destination)
    assert not os.path.lexists(destination / "pack" / "hard")


def test_verify_folder_clean_folder_has_no_problems(fcbin, tmp_path):
    root = tmp_path / "stage"
    make_tree(root, ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"])
    assert fcbin.verify_folder(root, fcbin.file_entries(root, "formcad-kernel")) == []


def test_verify_folder_reports_changed_byte(fcbin, tmp_path):
    root = tmp_path / "stage"
    make_tree(root, ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"])
    expected = fcbin.file_entries(root, "formcad-kernel")
    target = root / "_internal/deep/data.bin"
    data = bytearray(target.read_bytes())
    data[100] ^= 1
    target.write_bytes(bytes(data))
    assert fcbin.verify_folder(root, expected) == ["_internal/deep/data.bin does not match its recorded sha256"]


def test_verify_folder_reports_missing_file(fcbin, tmp_path):
    root = tmp_path / "stage"
    make_tree(root, ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"])
    expected = fcbin.file_entries(root, "formcad-kernel")
    (root / "_internal/libTKernel.so").unlink()
    assert fcbin.verify_folder(root, expected) == ["_internal/libTKernel.so is missing"]


def test_verify_folder_reports_extra_file(fcbin, tmp_path):
    root = tmp_path / "stage"
    make_tree(root, ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"])
    expected = fcbin.file_entries(root, "formcad-kernel")
    (root / "_internal/added.so").write_bytes(b"not recorded")
    assert fcbin.verify_folder(root, expected) == ["_internal/added.so is not in the record"]


def test_verify_folder_reports_wrong_recorded_size(fcbin, tmp_path):
    root = tmp_path / "stage"
    make_tree(root, ["formcad-kernel", "README.md", "_internal/libTKernel.so", "_internal/deep/data.bin"])
    expected = fcbin.file_entries(root, "formcad-kernel")
    for entry in expected:
        if entry["path"] == "README.md":
            entry["bytes"] += 1
    assert fcbin.verify_folder(root, expected) == ["README.md has a different size than recorded"]


def test_dump_then_load_round_trips_with_stable_text(fcbin, tmp_path):
    data = {
        "schema": 2,
        "program": "formcad-kernel",
        "release": "8.0.1-2",
        "note": "Büro ünïcode",
        "targets": {"x86_64-unknown-linux-gnu": {"files": [{"path": "a", "sha256": "0" * 64, "bytes": 3}]}},
        "a_last_key": None,
    }
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    fcbin.dump(first, data)
    loaded = fcbin.load(first)
    assert loaded == data
    assert list(loaded) == list(data)
    fcbin.dump(second, loaded)
    raw = first.read_bytes()
    assert raw == second.read_bytes()
    text = raw.decode("utf-8")
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert "\r" not in text
    assert '\n  "schema": 2,\n' in text
    assert "Büro ünïcode" in text
