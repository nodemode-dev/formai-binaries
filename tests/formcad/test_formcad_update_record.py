import hashlib
import json
import shutil
import struct
from pathlib import Path

import pytest

TOOL = "formcad/tools/update_record.py"
PREFLIGHT = "formcad/tools/preflight.py"
LINUX = "x86_64-unknown-linux-gnu"
WINDOWS = "x86_64-pc-windows-msvc"
MAC = "aarch64-apple-darwin"
KERNEL_DOCS = ["LGPL-2.1.txt", "OCCT-EXCEPTION.txt", "THIRD-PARTY-NOTICES.txt"]
MESHER_DOCS = ["LGPL-2.1.txt", "NETGEN-LICENSE.txt", "THIRD-PARTY-NOTICES.txt"]
TOP_KEYS = {"schema", "program", "release", "licence", "documents", "source", "targets", "licence_gates"}
TARGET_KEYS = {
    "protocol",
    "archive",
    "folder",
    "programs",
    "library_dir",
    "libraries_found_by",
    "minimum_os",
    "toolchain",
    "verification",
    "system_libraries_needed",
    "files",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fake_elf(needed):
    strtab = b"\0"
    offsets = []
    for name in needed:
        offsets.append(len(strtab))
        strtab += name.encode() + b"\0"
    str_off = 64
    dyn_off = str_off + len(strtab)
    dyn_off += (-dyn_off) % 8
    dynamic = b"".join(struct.pack("<qQ", 1, off) for off in offsets) + struct.pack("<qQ", 0, 0)
    sh_off = dyn_off + len(dynamic)
    sh_off += (-sh_off) % 8
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[6] = 1
    struct.pack_into("<Q", header, 0x28, sh_off)
    struct.pack_into("<HH", header, 0x3A, 64, 3)
    body = bytearray(header) + strtab
    body += b"\0" * (dyn_off - len(body))
    body += dynamic
    body += b"\0" * (sh_off - len(body))
    null = bytes(64)
    sh_str = struct.pack("<IIQQQQIIQQ", 0, 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0)
    sh_dyn = struct.pack("<IIQQQQIIQQ", 0, 6, 0, 0, dyn_off, len(dynamic), 1, 0, 8, 16)
    return bytes(body) + null + sh_str + sh_dyn


def program_for(kind, triple):
    return ("formcad-kernel" if kind == "kernel" else "formcad-mesher") + (".exe" if "windows" in triple else "")


def make_stage(root, repo_root, kind="kernel", triple=LINUX, program_bytes=b"fake program\n"):
    root.mkdir(parents=True)
    (root / program_for(kind, triple)).write_bytes(program_bytes)
    for name in KERNEL_DOCS if kind == "kernel" else MESHER_DOCS:
        licence = repo_root / "formcad" / "licences" / name
        if licence.is_file():
            shutil.copyfile(licence, root / name)
        else:
            (root / name).write_text("third party notices of the test build\n", encoding="utf-8")
    lib = root / "_internal"
    (lib / "sub").mkdir(parents=True)
    (lib / "libocct.so.8").write_bytes(b"library bytes " * 50)
    (lib / "sub" / "module.pyd").write_bytes(b"")
    (root / "README.md").write_text("readme of the test build\n", encoding="utf-8")
    return root


def arguments(stage, record, kind="kernel", triple=LINUX, release="8.0.1", extra=()):
    return ["--kind", kind, "--record", str(record), "--triple", triple, "--stage", str(stage), "--release", release, *extra]


def on_disk(stage):
    return {p.relative_to(stage).as_posix(): (digest(p), p.stat().st_size) for p in Path(stage).rglob("*") if p.is_file()}


@pytest.fixture
def update(run_tool, repo_root):
    def run(*args):
        return run_tool(repo_root / TOOL, *args)

    return run


@pytest.fixture
def module(load_module):
    return load_module(TOOL)


@pytest.fixture
def kernel_run(tmp_path, repo_root, update):
    stage = make_stage(tmp_path / "stage", repo_root)
    record = tmp_path / "records" / "occt-licence.json"
    record.parent.mkdir()
    result = update(*arguments(stage, record))
    assert result.returncode == 0, result.stderr
    return stage, record, json.loads(record.read_text(encoding="utf-8"))


def test_new_record_has_the_schema_keys_kind_and_release(kernel_run):
    stage, record, data = kernel_run
    assert set(data) == TOP_KEYS
    assert data["schema"] == 2
    assert data["program"] == "formcad-kernel"
    assert data["release"] == "8.0.1"
    assert data["licence"] == "LGPL-2.1-only WITH OCCT-exception-1.0"
    assert list(data["targets"]) == [LINUX]


def test_target_entry_holds_the_schema_fields(tmp_path, repo_root, update):
    stage = make_stage(tmp_path / "stage", repo_root)
    record = tmp_path / "occt-licence.json"
    extra = ["--minimum-os", "glibc 2.35", "--toolchain", "ubuntu-22.04, PyInstaller", "--verification", "hello handshake"]
    result = update(*arguments(stage, record, extra=extra))
    assert result.returncode == 0, result.stderr
    target = json.loads(record.read_text(encoding="utf-8"))["targets"][LINUX]
    assert set(target) == TARGET_KEYS
    assert target["protocol"] == 2
    assert target["archive"] == f"occt-lgpl-8.0.1-{LINUX}.tar.gz"
    assert target["folder"] == f"occt-lgpl-8.0.1-{LINUX}"
    assert target["programs"] == ["formcad-kernel"]
    assert target["library_dir"] == "_internal"
    assert target["libraries_found_by"] == "PyInstaller bootloader: the library folder beside the program"
    assert target["minimum_os"] == "glibc 2.35"
    assert target["toolchain"] == "ubuntu-22.04, PyInstaller"
    assert target["verification"] == "hello handshake"


def test_every_build_file_is_listed_once_with_its_real_hash_and_size(kernel_run):
    stage, record, data = kernel_run
    files = data["targets"][LINUX]["files"]
    paths = [entry["path"] for entry in files]
    assert len(paths) == len(set(paths))
    assert {entry["path"]: (entry["sha256"], entry["bytes"]) for entry in files} == on_disk(stage)
    assert all(set(entry) == {"path", "sha256", "bytes"} for entry in files)


def test_program_comes_first_and_the_rest_are_sorted(kernel_run):
    stage, record, data = kernel_run
    paths = [entry["path"] for entry in data["targets"][LINUX]["files"]]
    assert paths[0] == "formcad-kernel"
    assert paths[1:] == sorted(paths[1:])


def test_paths_are_relative_posix_paths(kernel_run):
    stage, record, data = kernel_run
    paths = [entry["path"] for entry in data["targets"][LINUX]["files"]]
    assert "_internal/sub/module.pyd" in paths
    assert "_internal/libocct.so.8" in paths
    for path in paths:
        assert "\\" not in path
        assert not path.startswith("/")
        assert ".." not in path.split("/")


def test_empty_file_is_recorded_with_zero_bytes(kernel_run):
    stage, record, data = kernel_run
    entry = next(e for e in data["targets"][LINUX]["files"] if e["path"] == "_internal/sub/module.pyd")
    assert entry["bytes"] == 0
    assert entry["sha256"] == hashlib.sha256(b"").hexdigest()


def test_documents_map_each_notice_to_its_hash(kernel_run):
    stage, record, data = kernel_run
    assert data["documents"] == {name: digest(stage / name) for name in KERNEL_DOCS}


def test_source_holds_upstream_asset_and_lock(tmp_path, repo_root, update, module):
    stage = make_stage(tmp_path / "stage", repo_root)
    lock = tmp_path / "build" / "requirements.lock"
    lock.parent.mkdir()
    lock.write_text("build123d==0.9\n", encoding="utf-8")
    record = tmp_path / "occt-licence.json"
    result = update(*arguments(stage, record, extra=["--lock", str(lock)]))
    assert result.returncode == 0, result.stderr
    source = json.loads(record.read_text(encoding="utf-8"))["source"]
    assert source["upstream"] == module.UPSTREAM["kernel"]
    assert source["asset"] == "occt-source-8.0.1.tar.gz"
    assert source["lock"] == {"path": "requirements.lock", "sha256": digest(lock)}


def test_licence_gates_are_the_kernel_gates(kernel_run, module):
    stage, record, data = kernel_run
    assert data["licence_gates"] == module.GATES["kernel"]


def test_mesher_record_uses_the_mesher_values(tmp_path, repo_root, update, module):
    stage = make_stage(tmp_path / "stage", repo_root, kind="mesher")
    record = tmp_path / "mesher-licence.json"
    result = update(*arguments(stage, record, kind="mesher", release="6.2.2608"))
    assert result.returncode == 0, result.stderr
    data = json.loads(record.read_text(encoding="utf-8"))
    target = data["targets"][LINUX]
    assert data["program"] == "formcad-mesher"
    assert data["licence"] == "LGPL-2.1-only"
    assert sorted(data["documents"]) == sorted(MESHER_DOCS)
    assert data["source"]["upstream"] == module.UPSTREAM["mesher"]
    assert data["source"]["asset"] == "mesher-source-6.2.2608.tar.gz"
    assert data["licence_gates"] == module.GATES["mesher"]
    assert target["protocol"] == 1
    assert target["archive"] == f"mesher-lgpl-6.2.2608-{LINUX}.tar.gz"
    assert target["programs"] == ["formcad-mesher"]
    assert target["files"][0]["path"] == "formcad-mesher"


def test_windows_triple_records_the_exe_program(tmp_path, repo_root, update):
    stage = make_stage(tmp_path / "stage", repo_root, triple=WINDOWS, program_bytes=fake_elf(["libc.so.6"]))
    record = tmp_path / "occt-licence.json"
    result = update(*arguments(stage, record, triple=WINDOWS))
    assert result.returncode == 0, result.stderr
    target = json.loads(record.read_text(encoding="utf-8"))["targets"][WINDOWS]
    assert target["programs"] == ["formcad-kernel.exe"]
    assert target["files"][0]["path"] == "formcad-kernel.exe"
    assert target["system_libraries_needed"] == []


def test_linux_system_libraries_are_the_needed_names_not_in_the_build(tmp_path, repo_root, update):
    stage = make_stage(tmp_path / "stage", repo_root, program_bytes=fake_elf(["libc.so.6", "libocct.so.8", "libm.so.6"]))
    (stage / "_internal" / "libocct.so.8").write_bytes(fake_elf(["libc.so.6", "libpthread.so.0"]))
    record = tmp_path / "occt-licence.json"
    result = update(*arguments(stage, record))
    assert result.returncode == 0, result.stderr
    target = json.loads(record.read_text(encoding="utf-8"))["targets"][LINUX]
    assert target["system_libraries_needed"] == ["libc.so.6", "libm.so.6", "libpthread.so.0"]


def test_symbolic_link_carries_link_and_the_target_hash(tmp_path, repo_root, update):
    stage = make_stage(tmp_path / "stage", repo_root)
    (stage / "_internal" / "libocct.so").symlink_to("libocct.so.8")
    record = tmp_path / "occt-licence.json"
    result = update(*arguments(stage, record))
    assert result.returncode == 0, result.stderr
    files = json.loads(record.read_text(encoding="utf-8"))["targets"][LINUX]["files"]
    entry = next(e for e in files if e["path"] == "_internal/libocct.so")
    real = stage / "_internal" / "libocct.so.8"
    assert entry == {"path": "_internal/libocct.so", "sha256": digest(real), "bytes": real.stat().st_size, "link": "libocct.so.8"}
    assert all("link" not in e for e in files if e["path"] != "_internal/libocct.so")


def test_second_triple_is_added_beside_the_first_and_leaves_it_untouched(kernel_run, tmp_path, repo_root, update):
    stage, record, first = kernel_run
    windows_stage = make_stage(tmp_path / "windows", repo_root, triple=WINDOWS, program_bytes=b"windows program\n")
    result = update(*arguments(windows_stage, record, triple=WINDOWS))
    assert result.returncode == 0, result.stderr
    data = json.loads(record.read_text(encoding="utf-8"))
    assert list(data["targets"]) == sorted([LINUX, WINDOWS])
    assert data["targets"][LINUX] == first["targets"][LINUX]
    assert {e["path"]: (e["sha256"], e["bytes"]) for e in data["targets"][WINDOWS]["files"]} == on_disk(windows_stage)


def test_targets_are_kept_in_sorted_order(tmp_path, repo_root, update):
    record = tmp_path / "occt-licence.json"
    for triple in (WINDOWS, LINUX, MAC):
        stage = make_stage(tmp_path / triple, repo_root, triple=triple)
        result = update(*arguments(stage, record, triple=triple))
        assert result.returncode == 0, result.stderr
    assert list(json.loads(record.read_text(encoding="utf-8"))["targets"]) == sorted([WINDOWS, LINUX, MAC])


def test_same_triple_again_replaces_its_entry(kernel_run, update):
    stage, record, first = kernel_run
    (stage / "_internal" / "libocct.so.8").write_bytes(b"rebuilt library")
    (stage / "_internal" / "sub" / "module.pyd").unlink()
    result = update(*arguments(stage, record, release="8.0.1-2"))
    assert result.returncode == 0, result.stderr
    data = json.loads(record.read_text(encoding="utf-8"))
    assert list(data["targets"]) == [LINUX]
    files = data["targets"][LINUX]["files"]
    assert {e["path"]: (e["sha256"], e["bytes"]) for e in files} == on_disk(stage)
    assert "_internal/sub/module.pyd" not in [e["path"] for e in files]
    assert data["release"] == "8.0.1-2"
    assert data["targets"][LINUX]["archive"] == f"occt-lgpl-8.0.1-2-{LINUX}.tar.gz"


def test_running_twice_with_the_same_input_changes_nothing(kernel_run, update):
    stage, record, first = kernel_run
    before = record.read_bytes()
    result = update(*arguments(stage, record))
    assert result.returncode == 0, result.stderr
    assert record.read_bytes() == before


def test_written_json_is_stable_and_ends_with_a_newline(kernel_run):
    stage, record, data = kernel_run
    text = record.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert not text.endswith("\n\n")
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"


def test_existing_pinned_upstream_and_lock_are_kept(tmp_path, repo_root, update):
    stage = make_stage(tmp_path / "stage", repo_root)
    record = tmp_path / "occt-licence.json"
    pinned = [{"name": "OpenCascade Technology", "version": "8.0.1", "licence": "LGPL", "url": "https://x/a.tar.gz", "sha256": "ab" * 32}]
    lock = {"path": "requirements.lock", "sha256": "cd" * 32}
    record.write_text(json.dumps({"source": {"upstream": pinned, "lock": lock}}) + "\n", encoding="utf-8")
    result = update(*arguments(stage, record))
    assert result.returncode == 0, result.stderr
    source = json.loads(record.read_text(encoding="utf-8"))["source"]
    assert source["upstream"] == pinned
    assert source["lock"] == lock


@pytest.mark.parametrize("release", ["", "8.0.1/x", "../8.0.1", "-8.0.1", "8.0 1", "8.0.1;x", "8.0.1_2", "8" * 42])
def test_bad_release_name_fails_and_keeps_the_record(kernel_run, update, release):
    stage, record, data = kernel_run
    before = record.read_bytes()
    result = update(*arguments(stage, record, release=release))
    assert result.returncode != 0
    assert "release" in result.stderr
    assert record.read_bytes() == before


@pytest.mark.parametrize("option,value", [("--kind", "slicer"), ("--triple", "riscv64-unknown-linux-gnu")])
def test_unknown_kind_or_triple_fails_and_keeps_the_record(kernel_run, update, option, value):
    stage, record, data = kernel_run
    before = record.read_bytes()
    args = arguments(stage, record)
    args[args.index(option) + 1] = value
    result = update(*args)
    assert result.returncode != 0
    assert "invalid choice" in result.stderr
    assert value in result.stderr
    assert record.read_bytes() == before


def test_missing_build_folder_fails_and_keeps_the_record(kernel_run, tmp_path, update):
    stage, record, data = kernel_run
    before = record.read_bytes()
    result = update(*arguments(tmp_path / "no-such-stage", record))
    assert result.returncode != 0
    assert "formcad-kernel is not in" in result.stderr
    assert record.read_bytes() == before


def test_empty_build_folder_fails_and_keeps_the_record(kernel_run, tmp_path, update):
    stage, record, data = kernel_run
    before = record.read_bytes()
    empty = tmp_path / "empty"
    empty.mkdir()
    result = update(*arguments(empty, record))
    assert result.returncode != 0
    assert "formcad-kernel is not in" in result.stderr
    assert record.read_bytes() == before


def test_failure_on_a_new_record_creates_no_file(tmp_path, update):
    empty = tmp_path / "empty"
    empty.mkdir()
    record = tmp_path / "occt-licence.json"
    result = update(*arguments(empty, record))
    assert result.returncode != 0
    assert not record.exists()


def test_missing_notice_file_fails_and_keeps_the_record(kernel_run, update):
    stage, record, data = kernel_run
    before = record.read_bytes()
    (stage / "THIRD-PARTY-NOTICES.txt").unlink()
    result = update(*arguments(stage, record))
    assert result.returncode != 0
    assert "THIRD-PARTY-NOTICES.txt" in result.stderr
    assert record.read_bytes() == before


def test_two_library_folders_need_library_dir(kernel_run, update):
    stage, record, data = kernel_run
    before = record.read_bytes()
    (stage / "extra").mkdir()
    (stage / "extra" / "data.bin").write_bytes(b"x")
    result = update(*arguments(stage, record))
    assert result.returncode != 0
    assert "expected one library folder" in result.stderr
    assert record.read_bytes() == before
    result = update(*arguments(stage, record, extra=["--library-dir", "_internal"]))
    assert result.returncode == 0, result.stderr
    target = json.loads(record.read_text(encoding="utf-8"))["targets"][LINUX]
    assert target["library_dir"] == "_internal"
    assert "extra/data.bin" in [e["path"] for e in target["files"]]


def test_module_constants_cover_both_kinds(module):
    import fcbin

    assert set(module.UPSTREAM) == set(fcbin.KINDS) == set(module.GATES)
    for items in module.UPSTREAM.values():
        assert items
        for item in items:
            assert set(item) == {"name", "version", "licence", "url", "sha256"}
            assert item["url"].startswith("https://")
    assert module.UPSTREAM["kernel"][0]["version"] == "8.0.1"
    assert module.UPSTREAM["mesher"][0]["version"] == "6.2.2608"
    for gate in module.GATES.values():
        assert set(gate) == {"forbidden_names", "needs_review", "note"}
        assert gate["forbidden_names"] and gate["needs_review"]


def test_result_passes_preflight(tmp_path, repo_root, update, run_tool):
    records = tmp_path / "records"
    records.mkdir()
    originals = {}
    for name in ("occt-licence.json", "mesher-licence.json"):
        source = repo_root / "formcad" / "records" / name
        originals[name] = source.read_bytes()
        shutil.copyfile(source, records / name)
    stage = make_stage(tmp_path / "stage", repo_root, program_bytes=fake_elf(["libc.so.6"]))
    result = update(*arguments(stage, records / "occt-licence.json"))
    assert result.returncode == 0, result.stderr
    mesher_stage = make_stage(tmp_path / "mesher", repo_root, kind="mesher", triple=WINDOWS)
    result = update(*arguments(mesher_stage, records / "mesher-licence.json", kind="mesher", triple=WINDOWS, release="6.2.2608"))
    assert result.returncode == 0, result.stderr
    check = run_tool(repo_root / PREFLIGHT, "--records", records, "--published")
    assert check.returncode == 0, check.stdout + check.stderr
    assert "preflight passed" in check.stdout
    for name, content in originals.items():
        assert (repo_root / "formcad" / "records" / name).read_bytes() == content


def test_missing_triple_fails_with_a_usage_message_and_keeps_the_record(kernel_run, update):
    stage, record, _ = kernel_run
    before = record.read_bytes()
    result = update("--kind", "kernel", "--record", str(record), "--stage", str(stage), "--release", "8.0.2")
    assert result.returncode == 2
    assert result.stderr.startswith("usage:")
    assert "--triple" in result.stderr
    assert "required" in result.stderr
    assert result.stdout == ""
    assert record.read_bytes() == before


def test_missing_triple_creates_no_record(tmp_path, repo_root, update):
    stage = make_stage(tmp_path / "stage", repo_root)
    record = tmp_path / "occt-licence.json"
    result = update("--kind", "kernel", "--record", str(record), "--stage", str(stage), "--release", "8.0.1")
    assert result.returncode != 0
    assert not record.exists()
