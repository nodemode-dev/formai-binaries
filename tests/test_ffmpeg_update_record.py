import datetime
import hashlib
import json
import os
import shutil
import sys
import types

import pytest

LINUX = "x86_64-unknown-linux-gnu"
WINDOWS = "x86_64-pc-windows-msvc"
CONFIGURE = "--prefix=/tmp/fake/install --enable-shared --disable-static --disable-gpl --disable-nonfree"
DOCUMENTS = ("LGPL-2.1.txt", "THIRD-PARTY-NOTICES.txt")


def stage(root, triple, configure=CONFIGURE):
    windows = "windows" in triple
    suffix = ".exe" if windows else ""
    folder = root / "staged"
    lib = folder / f"lib-{triple}"
    lib.mkdir(parents=True)
    if windows:
        libraries = {
            "avcodec-61.dll": "DLL Name: avutil-59.dll\nDLL Name: KERNEL32.dll\n",
            "avutil-59.dll": "DLL Name: bcrypt.dll\n",
        }
        program = "DLL Name: AVCODEC-61.dll\nDLL Name: msvcrt.dll\n"
    else:
        libraries = {
            "libavcodec.so.61": "NEEDED libavutil.so.59\nNEEDED libm.so.6\n",
            "libavutil.so.59": "NEEDED libc.so.6\n",
        }
        program = "NEEDED libavcodec.so.61\nNEEDED libz.so.1\n"
    for name, needed in libraries.items():
        body = f"library {name}\x00"
        if "avutil" in name:
            body += configure + "\x00"
        (lib / name).write_text(body + needed, encoding="utf-8")
    for name in ("ffmpeg", "ffprobe"):
        (folder / f"{name}-{triple}{suffix}").write_text(f"program {name}\x00{program}", encoding="utf-8")
    for name in DOCUMENTS:
        (folder / name).write_text(f"text of {name}\n", encoding="utf-8")
    return folder


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def tool(tmp_path, repo_root):
    folder = tmp_path / "tool"
    folder.mkdir()
    shutil.copy2(repo_root / "update_record.py", folder / "update_record.py")
    shutil.copy2(repo_root / "ffmpeg-licence.json", folder / "ffmpeg-licence.json")
    return folder


@pytest.fixture
def tool_env(tmp_path):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name in ("objdump", "x86_64-w64-mingw32-objdump"):
        script = bin_dir / name
        script.write_text('#!/bin/sh\n[ "$1" = "-p" ] || exit 9\ncat "$2"\n', encoding="utf-8")
        script.chmod(0o755)
    return {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}


@pytest.fixture
def record_run(run_tool, tool, tool_env):
    def run(folder, triple, *extra, release="7.1.5-2"):
        return run_tool(
            tool / "update_record.py",
            "--binaries", folder, "--triple", triple, "--release", release, "--toolchain", "gcc 11.4.0 test",
            *extra,
            cwd=tool.parent,
            env=tool_env,
        )

    return run


def load_record(tool):
    return json.loads((tool / "ffmpeg-licence.json").read_text(encoding="utf-8"))


def test_digest_is_the_sha256_of_the_file(load_module, tmp_path):
    module = load_module("update_record.py")
    path = tmp_path / "blob"
    path.write_bytes(b"\x00\x01binary\xffdata")
    assert module.digest(path) == hashlib.sha256(b"\x00\x01binary\xffdata").hexdigest()


def test_shipped_lists_programs_then_sorted_libraries(load_module, tmp_path):
    module = load_module("update_record.py")
    folder = stage(tmp_path, LINUX)
    (folder / f"lib-{LINUX}" / "pkgconfig").mkdir()
    names = [path.relative_to(folder).as_posix() for path in module.shipped(folder, LINUX)]
    assert names == [
        f"ffmpeg-{LINUX}",
        f"ffprobe-{LINUX}",
        f"lib-{LINUX}/libavcodec.so.61",
        f"lib-{LINUX}/libavutil.so.59",
    ]


def test_shipped_uses_exe_names_for_windows(load_module, tmp_path):
    module = load_module("update_record.py")
    folder = stage(tmp_path, WINDOWS)
    names = [path.name for path in module.shipped(folder, WINDOWS)]
    assert names[:2] == [f"ffmpeg-{WINDOWS}.exe", f"ffprobe-{WINDOWS}.exe"]
    assert names[2:] == ["avcodec-61.dll", "avutil-59.dll"]


def test_shipped_ignores_files_of_other_triples(load_module, tmp_path):
    module = load_module("update_record.py")
    folder = stage(tmp_path, LINUX)
    other = "aarch64-apple-darwin"
    (folder / f"ffmpeg-{other}").write_text("other", encoding="utf-8")
    (folder / f"lib-{other}").mkdir()
    (folder / f"lib-{other}" / "libavutil.59.dylib").write_text("other", encoding="utf-8")
    names = [path.name for path in module.shipped(folder, LINUX)]
    assert all(other not in name for name in names)
    assert "libavutil.59.dylib" not in names
    assert len(names) == 4


def test_shipped_names_the_missing_programs(load_module, tmp_path):
    module = load_module("update_record.py")
    folder = stage(tmp_path, LINUX)
    (folder / f"ffprobe-{LINUX}").unlink()
    with pytest.raises(SystemExit) as raised:
        module.shipped(folder, LINUX)
    assert str(raised.value) == f"not staged: ffprobe-{LINUX}"


def test_configure_line_is_read_from_avutil_up_to_the_nul(load_module, tmp_path):
    module = load_module("update_record.py")
    codec = tmp_path / "libavcodec.so.61"
    codec.write_bytes(b"\x00--prefix=/wrong/place --enable-gpl\x00")
    avutil = tmp_path / "libavutil.so.59"
    avutil.write_bytes(b"\x7fELF\x00junk\x00" + CONFIGURE.encode() + b"\x00tail --prefix=/later\x00")
    assert module.configure_line([codec, avutil]) == CONFIGURE


def test_configure_line_missing_fails_naming_the_library(load_module, tmp_path):
    module = load_module("update_record.py")
    avutil = tmp_path / "libavutil.so.59"
    avutil.write_bytes(b"\x7fELF no configuration here\x00")
    with pytest.raises(SystemExit) as raised:
        module.configure_line([avutil])
    assert str(raised.value) == "no configure line in libavutil.so.59"


def test_imports_runs_objdump_and_leaves_bundled_libraries_out(load_module, tmp_path, monkeypatch):
    module = load_module("update_record.py")
    folder = stage(tmp_path, LINUX)
    paths = module.shipped(folder, LINUX)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return types.SimpleNamespace(stdout=open(command[2], encoding="utf-8").read())

    monkeypatch.setattr(module, "subprocess", types.SimpleNamespace(run=fake_run))
    assert module.imports(paths, LINUX) == ["libc.so.6", "libm.so.6", "libz.so.1"]
    assert [command[:2] for command in calls] == [["objdump", "-p"]] * 4
    assert [command[2] for command in calls] == [str(path) for path in paths]


def test_imports_for_windows_uses_mingw_objdump_and_ignores_case_of_bundled(load_module, tmp_path, monkeypatch):
    module = load_module("update_record.py")
    folder = stage(tmp_path, WINDOWS)
    paths = module.shipped(folder, WINDOWS)
    tools = set()

    def fake_run(command, **kwargs):
        tools.add(command[0])
        return types.SimpleNamespace(stdout=open(command[2], encoding="utf-8").read())

    monkeypatch.setattr(module, "subprocess", types.SimpleNamespace(run=fake_run))
    assert module.imports(paths, WINDOWS) == ["bcrypt.dll", "KERNEL32.dll", "msvcrt.dll"]
    assert tools == {"x86_64-w64-mingw32-objdump"}


@pytest.mark.xfail(strict=True, reason="defect: a Windows DLL imported under two spellings (KERNEL32.dll, kernel32.dll) is listed twice")
def test_imports_lists_a_windows_dll_once_whatever_its_case(load_module, tmp_path, monkeypatch):
    module = load_module("update_record.py")
    folder = stage(tmp_path, WINDOWS)
    (folder / f"lib-{WINDOWS}" / "avutil-59.dll").write_text("x\x00" + CONFIGURE + "\x00DLL Name: kernel32.dll\n", encoding="utf-8")
    paths = module.shipped(folder, WINDOWS)
    monkeypatch.setattr(
        module, "subprocess",
        types.SimpleNamespace(run=lambda command, **kwargs: types.SimpleNamespace(stdout=open(command[2], encoding="utf-8").read())),
    )
    found = module.imports(paths, WINDOWS)
    assert [name.lower() for name in found].count("kernel32.dll") == 1


def test_main_records_exactly_the_shipped_files_with_real_hashes_and_sizes(record_run, tool, tmp_path):
    folder = stage(tmp_path, LINUX)
    proc = record_run(folder, LINUX)
    assert proc.returncode == 0, proc.stderr
    expected_paths = [
        folder / f"ffmpeg-{LINUX}",
        folder / f"ffprobe-{LINUX}",
        folder / f"lib-{LINUX}" / "libavcodec.so.61",
        folder / f"lib-{LINUX}" / "libavutil.so.59",
    ]
    expected = [
        {"path": path.relative_to(folder).as_posix(), "sha256": sha(path), "bytes": len(path.read_bytes())}
        for path in expected_paths
    ]
    assert load_record(tool)["targets"][LINUX]["files"] == expected


def test_main_with_a_relative_binaries_folder_records_relative_paths(run_tool, tool, tool_env, tmp_path):
    stage(tmp_path, LINUX)
    proc = run_tool(
        tool / "update_record.py", "--binaries", "staged", "--triple", LINUX, "--release", "7.1.5",
        "--toolchain", "gcc", cwd=tmp_path, env=tool_env,
    )
    assert proc.returncode == 0, proc.stderr
    paths = [entry["path"] for entry in load_record(tool)["targets"][LINUX]["files"]]
    assert paths == [f"ffmpeg-{LINUX}", f"ffprobe-{LINUX}", f"lib-{LINUX}/libavcodec.so.61", f"lib-{LINUX}/libavutil.so.59"]


def test_main_takes_the_configure_line_from_the_built_library(record_run, tool, tmp_path):
    configure = "--prefix=/opt/built/here --enable-shared --disable-gpl --extra-cflags=-O2"
    folder = stage(tmp_path, LINUX, configure=configure)
    proc = record_run(folder, LINUX)
    assert proc.returncode == 0, proc.stderr
    assert load_record(tool)["targets"][LINUX]["configure"] == configure


@pytest.mark.parametrize("release", ["7.1.5", "7.1.5-2"])
def test_main_writes_the_release_name(record_run, tool, tmp_path, release):
    folder = stage(tmp_path, LINUX)
    proc = record_run(folder, LINUX, release=release)
    assert proc.returncode == 0, proc.stderr
    assert load_record(tool)["release"] == release
    assert proc.stdout.strip() == f"recorded {LINUX}: 4 files, 3 system imports, release {release}"


def test_main_writes_system_imports_toolchain_checks_and_status(record_run, tool, tmp_path):
    folder = stage(tmp_path, LINUX)
    proc = record_run(folder, LINUX, "--check", "ran ffmpeg -version", "--check", "every library loads from the bundle")
    assert proc.returncode == 0, proc.stderr
    target = load_record(tool)["targets"][LINUX]
    assert target["system_libraries_needed"] == ["libc.so.6", "libm.so.6", "libz.so.1"]
    assert target["toolchain"] == "gcc 11.4.0 test"
    assert target["verification"] == {
        "method": "executed",
        "checks": ["ran ffmpeg -version", "every library loads from the bundle"],
    }
    assert target["status"] == "shipped"


def test_main_for_windows_records_exe_files_and_inspected_method(record_run, tool, tmp_path):
    folder = stage(tmp_path, WINDOWS)
    proc = record_run(folder, WINDOWS)
    assert proc.returncode == 0, proc.stderr
    target = load_record(tool)["targets"][WINDOWS]
    assert [entry["path"] for entry in target["files"]] == [
        f"ffmpeg-{WINDOWS}.exe",
        f"ffprobe-{WINDOWS}.exe",
        f"lib-{WINDOWS}/avcodec-61.dll",
        f"lib-{WINDOWS}/avutil-59.dll",
    ]
    assert target["system_libraries_needed"] == ["bcrypt.dll", "KERNEL32.dll", "msvcrt.dll"]
    assert target["verification"]["method"] == "inspected"
    assert target["configure"] == CONFIGURE


def test_main_records_the_hashes_of_the_staged_documents(record_run, tool, tmp_path):
    folder = stage(tmp_path, LINUX)
    proc = record_run(folder, LINUX)
    assert proc.returncode == 0, proc.stderr
    assert load_record(tool)["documents"] == {name: sha(folder / name) for name in DOCUMENTS}


def test_main_leaves_other_triples_untouched(record_run, tool, tmp_path):
    before = load_record(tool)
    folder = stage(tmp_path, LINUX)
    proc = record_run(folder, LINUX)
    assert proc.returncode == 0, proc.stderr
    after = load_record(tool)
    assert set(after["targets"]) == set(before["targets"])
    for triple, target in before["targets"].items():
        if triple != LINUX:
            assert after["targets"][triple] == target
    for key in ("record", "schema", "replaces", "licence", "source"):
        assert after[key] == before[key]


def test_second_run_changes_nothing_but_the_timestamp(record_run, tool, tmp_path):
    folder = stage(tmp_path, LINUX)
    assert record_run(folder, LINUX).returncode == 0
    first = load_record(tool)
    assert record_run(folder, LINUX).returncode == 0
    second = load_record(tool)
    first.pop("written_utc")
    second.pop("written_utc")
    assert second == first


@pytest.mark.xfail(strict=True, reason="defect: written_utc is restamped on every run, so a second run of the same build still rewrites the record")
def test_second_run_later_leaves_the_record_byte_identical(load_module, tool, tool_env, tmp_path, monkeypatch):
    module = load_module("update_record.py")
    monkeypatch.setattr(module, "RECORD", tool / "ffmpeg-licence.json")
    monkeypatch.setenv("PATH", tool_env["PATH"])
    folder = stage(tmp_path, LINUX)
    times = iter([
        datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 1, 1, 10, 5, 0, tzinfo=datetime.timezone.utc),
    ])

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return next(times)

    monkeypatch.setattr(module, "datetime", types.SimpleNamespace(datetime=Clock, timezone=datetime.timezone))
    monkeypatch.setattr(sys, "argv", ["update_record.py", "--binaries", str(folder), "--triple", LINUX, "--release", "7.1.5-2", "--toolchain", "gcc"])
    assert module.main() == 0
    first = (tool / "ffmpeg-licence.json").read_bytes()
    assert module.main() == 0
    assert (tool / "ffmpeg-licence.json").read_bytes() == first


def test_missing_binaries_folder_fails_with_a_message_and_keeps_the_record(record_run, tool, tmp_path):
    before = (tool / "ffmpeg-licence.json").read_bytes()
    proc = record_run(tmp_path / "nowhere", LINUX)
    assert proc.returncode == 1
    assert proc.stderr.strip() == f"not staged: ffmpeg-{LINUX}, ffprobe-{LINUX}"
    assert (tool / "ffmpeg-licence.json").read_bytes() == before


def test_empty_binaries_folder_fails_with_a_message_and_keeps_the_record(record_run, tool, tmp_path):
    before = (tool / "ffmpeg-licence.json").read_bytes()
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = record_run(empty, WINDOWS)
    assert proc.returncode == 1
    assert proc.stderr.strip() == f"not staged: ffmpeg-{WINDOWS}.exe, ffprobe-{WINDOWS}.exe"
    assert (tool / "ffmpeg-licence.json").read_bytes() == before


def test_unknown_triple_fails_with_a_message_and_keeps_the_record(record_run, tool, tmp_path):
    before = (tool / "ffmpeg-licence.json").read_bytes()
    folder = stage(tmp_path, "riscv64-unknown-linux-gnu")
    proc = record_run(folder, "riscv64-unknown-linux-gnu")
    assert proc.returncode == 1
    assert proc.stderr.strip() == "riscv64-unknown-linux-gnu is not a target in the record"
    assert (tool / "ffmpeg-licence.json").read_bytes() == before


@pytest.mark.parametrize("state", ["empty", "missing"])
def test_broken_library_folder_fails_and_keeps_the_record(record_run, tool, tmp_path, state):
    before = (tool / "ffmpeg-licence.json").read_bytes()
    folder = stage(tmp_path, LINUX)
    shutil.rmtree(folder / f"lib-{LINUX}")
    if state == "empty":
        (folder / f"lib-{LINUX}").mkdir()
    proc = record_run(folder, LINUX)
    assert proc.returncode != 0
    assert (tool / "ffmpeg-licence.json").read_bytes() == before


@pytest.mark.xfail(strict=True, reason="defect: an empty or missing lib-<triple> folder ends in a Python traceback (StopIteration, FileNotFoundError) instead of a message")
@pytest.mark.parametrize("state", ["empty", "missing"])
def test_broken_library_folder_fails_with_a_message(record_run, tool, tmp_path, state):
    folder = stage(tmp_path, LINUX)
    shutil.rmtree(folder / f"lib-{LINUX}")
    if state == "empty":
        (folder / f"lib-{LINUX}").mkdir()
    proc = record_run(folder, LINUX)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert f"lib-{LINUX}" in proc.stderr


def test_missing_documents_fail_and_keep_the_record(record_run, tool, tmp_path):
    before = (tool / "ffmpeg-licence.json").read_bytes()
    folder = stage(tmp_path, LINUX)
    (folder / "THIRD-PARTY-NOTICES.txt").unlink()
    proc = record_run(folder, LINUX)
    assert proc.returncode != 0
    assert (tool / "ffmpeg-licence.json").read_bytes() == before


def test_running_the_copy_leaves_the_repository_record_alone(record_run, repo_root, tool, tmp_path):
    before = sha(repo_root / "ffmpeg-licence.json")
    folder = stage(tmp_path, LINUX)
    assert record_run(folder, LINUX).returncode == 0
    assert sha(repo_root / "ffmpeg-licence.json") == before
    assert sha(tool / "ffmpeg-licence.json") != before
