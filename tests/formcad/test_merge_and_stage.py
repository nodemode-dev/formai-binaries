import copy
import hashlib
import json
import re

import pytest

MERGE = "formcad/tools/merge_records.py"
STAGE = "formcad/tools/stage_notices.py"

LINUX = "x86_64-unknown-linux-gnu"
WINDOWS = "x86_64-pc-windows-msvc"
ARM_MAC = "aarch64-apple-darwin"
INTEL_MAC = "x86_64-apple-darwin"

STAGED_NAMES = {
    "kernel": {"LGPL-2.1.txt", "OCCT-EXCEPTION.txt", "THIRD-PARTY-NOTICES.txt", "LGPL-OFFER.txt", "README.md"},
    "mesher": {"LGPL-2.1.txt", "NETGEN-LICENSE.txt", "THIRD-PARTY-NOTICES.txt", "LGPL-OFFER.txt", "README.md"},
}
COPIED = {
    "kernel": {"LGPL-2.1.txt": "licences/LGPL-2.1.txt", "OCCT-EXCEPTION.txt": "licences/OCCT-EXCEPTION.txt", "LGPL-OFFER.txt": "LGPL-OFFER.txt"},
    "mesher": {"LGPL-2.1.txt": "licences/LGPL-2.1.txt", "NETGEN-LICENSE.txt": "licences/NETGEN-LICENSE.txt", "LGPL-OFFER.txt": "LGPL-OFFER.txt"},
}
FILLED = {
    "kernel": {"README.md": "templates/README-occt.md", "THIRD-PARTY-NOTICES.txt": "templates/THIRD-PARTY-NOTICES-kernel.txt"},
    "mesher": {"README.md": "templates/README-mesher.md", "THIRD-PARTY-NOTICES.txt": "templates/THIRD-PARTY-NOTICES-mesher.txt"},
}
SHIPPED = {"kernel": "occt-licence.json", "mesher": "mesher-licence.json"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target_entry(triple, release="8.0.1"):
    program = "formcad-kernel.exe" if "windows" in triple else "formcad-kernel"
    return {
        "protocol": 2,
        "archive": f"occt-lgpl-{release}-{triple}.tar.gz",
        "folder": f"occt-lgpl-{release}-{triple}",
        "programs": [program],
        "library_dir": "_internal",
        "libraries_found_by": "PyInstaller bootloader",
        "minimum_os": "any",
        "toolchain": f"runner for {triple}",
        "verification": "hello handshake",
        "system_libraries_needed": [f"lib-{triple}.so"],
        "files": [
            {"path": program, "sha256": hashlib.sha256(triple.encode()).hexdigest(), "bytes": 10 + len(triple)},
            {"path": "_internal/café.txt", "sha256": "0" * 64, "bytes": 0},
            {"path": "_internal/link.so", "sha256": "1" * 64, "bytes": 3, "link": "real.so"},
        ],
    }


def make_record(triples, release="8.0.1", program="formcad-kernel"):
    return {
        "schema": 2,
        "program": program,
        "release": release,
        "licence": "LGPL-2.1-only WITH OCCT-exception-1.0",
        "documents": {"LGPL-2.1.txt": "a" * 64, "OCCT-EXCEPTION.txt": "b" * 64, "THIRD-PARTY-NOTICES.txt": "c" * 64},
        "source": {
            "upstream": [{"name": "OpenCascade Technology", "version": "8.0.1", "licence": "LGPL-2.1-only", "url": "https://example.invalid/occt.tar.gz", "sha256": None}],
            "asset": f"occt-source-{release}.tar.gz",
            "lock": {"path": "requirements.lock", "sha256": "d" * 64},
        },
        "targets": {triple: target_entry(triple, release) for triple in triples},
        "licence_gates": {"forbidden_names": ["libgpl3"], "needs_review": ["libquadmath"]},
    }


def write_record(path, record):
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def two_records(tmp_path):
    first = make_record([LINUX])
    second = make_record([WINDOWS])
    return first, second, write_record(tmp_path / "linux.json", first), write_record(tmp_path / "windows.json", second)


def stage(run_tool, folder, kind, release="9.1.0-3", triple=LINUX):
    folder.mkdir(parents=True, exist_ok=True)
    return run_tool(STAGE, "--kind", kind, "--stage", folder, "--release", release, "--triple", triple)


def snapshot(folder):
    return {p.name: sha(p) for p in sorted(folder.iterdir())}


def test_merge_two_triples_holds_both_entries_unchanged(run_tool, tmp_path):
    first, second, a, b = two_records(tmp_path)
    out = tmp_path / "merged.json"
    result = run_tool(MERGE, "--out", out, a, b)
    assert result.returncode == 0, result.stderr
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert set(merged["targets"]) == {LINUX, WINDOWS}
    assert merged["targets"][LINUX] == first["targets"][LINUX]
    assert merged["targets"][WINDOWS] == second["targets"][WINDOWS]
    assert "café.txt" in out.read_text(encoding="utf-8")


def test_merge_keeps_the_shared_fields_once(run_tool, tmp_path):
    first, _, a, b = two_records(tmp_path)
    out = tmp_path / "merged.json"
    assert run_tool(MERGE, "--out", out, a, b).returncode == 0
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert list(merged) == list(first)
    for key in first:
        if key != "targets":
            assert merged[key] == first[key]
    text = out.read_text(encoding="utf-8")
    for key in ("schema", "program", "release", "documents", "licence_gates", "upstream", "lock"):
        assert text.count(f'"{key}":') == 1


def test_merge_in_either_order_gives_the_same_bytes(run_tool, tmp_path):
    _, _, a, b = two_records(tmp_path)
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    assert run_tool(MERGE, "--out", one, a, b).returncode == 0
    assert run_tool(MERGE, "--out", two, b, a).returncode == 0
    assert one.read_bytes() == two.read_bytes()


def test_merge_of_four_records_lists_every_triple_in_sorted_order(run_tool, tmp_path):
    paths = [write_record(tmp_path / f"{t}.json", make_record([t])) for t in (WINDOWS, LINUX, INTEL_MAC, ARM_MAC)]
    out = tmp_path / "merged.json"
    result = run_tool(MERGE, "--out", out, *paths)
    assert result.returncode == 0, result.stderr
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert list(merged["targets"]) == sorted([WINDOWS, LINUX, INTEL_MAC, ARM_MAC])
    for t in merged["targets"]:
        assert merged["targets"][t] == target_entry(t)
        assert t in result.stdout


@pytest.mark.parametrize("key,value", [("release", "8.0.1-2"), ("program", "formcad-mesher")])
def test_merge_refuses_records_of_another_release_or_kind(run_tool, tmp_path, key, value):
    first = make_record([LINUX])
    other = make_record([WINDOWS])
    other[key] = value
    a = write_record(tmp_path / "a.json", first)
    b = write_record(tmp_path / "b.json", other)
    out = tmp_path / "merged.json"
    result = run_tool(MERGE, "--out", out, a, b)
    assert result.returncode != 0
    assert key in result.stderr and "b.json" in result.stderr
    assert "Traceback" not in result.stderr
    assert not out.exists()


def test_merge_refuses_records_built_from_another_lock(run_tool, tmp_path):
    other = make_record([WINDOWS])
    other["source"]["lock"]["sha256"] = "e" * 64
    a = write_record(tmp_path / "a.json", make_record([LINUX]))
    b = write_record(tmp_path / "b.json", other)
    out = tmp_path / "merged.json"
    result = run_tool(MERGE, "--out", out, a, b)
    assert result.returncode != 0
    assert "lock" in result.stderr
    assert not out.exists()


def test_merge_refuses_records_with_other_document_hashes(run_tool, tmp_path):
    other = make_record([WINDOWS])
    other["documents"]["THIRD-PARTY-NOTICES.txt"] = "f" * 64
    a = write_record(tmp_path / "a.json", make_record([LINUX]))
    b = write_record(tmp_path / "b.json", other)
    out = tmp_path / "merged.json"
    result = run_tool(MERGE, "--out", out, a, b)
    assert result.returncode != 0
    assert "documents" in result.stderr
    assert not out.exists()


def test_merge_refuses_a_triple_present_in_both_inputs(run_tool, tmp_path):
    a = write_record(tmp_path / "a.json", make_record([LINUX, WINDOWS]))
    b = write_record(tmp_path / "b.json", make_record([LINUX]))
    out = tmp_path / "merged.json"
    result = run_tool(MERGE, "--out", out, a, b)
    assert result.returncode != 0
    assert LINUX in result.stderr and "twice" in result.stderr
    assert not out.exists()


def test_merge_of_one_input_gives_itself(run_tool, tmp_path):
    source = write_record(tmp_path / "only.json", make_record([ARM_MAC, LINUX]))
    out = tmp_path / "merged.json"
    assert run_tool(MERGE, "--out", out, source).returncode == 0
    assert out.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("name", sorted(SHIPPED.values()))
def test_merge_of_a_shipped_record_alone_gives_its_bytes(run_tool, repo_root, tmp_path, name):
    source = repo_root / "formcad" / "records" / name
    out = tmp_path / name
    assert run_tool(MERGE, "--out", out, source).returncode == 0
    assert sha(out) == sha(source)


def test_merge_with_a_missing_file_fails_and_names_it(run_tool, tmp_path):
    a = write_record(tmp_path / "a.json", make_record([LINUX]))
    out = tmp_path / "merged.json"
    result = run_tool(MERGE, "--out", out, a, tmp_path / "absent.json")
    assert result.returncode != 0
    assert "absent.json" in result.stderr
    assert not out.exists()


def test_merge_with_invalid_json_fails_and_writes_nothing(run_tool, tmp_path):
    a = write_record(tmp_path / "a.json", make_record([LINUX]))
    bad = tmp_path / "broken.json"
    bad.write_text('{"schema": 2,', encoding="utf-8")
    out = tmp_path / "merged.json"
    result = run_tool(MERGE, "--out", out, a, bad)
    assert result.returncode != 0
    assert result.stderr.strip()
    assert not out.exists()


@pytest.mark.xfail(strict=True, reason="defect: invalid JSON ends in a JSONDecodeError traceback that does not name the file")
def test_merge_with_invalid_json_names_the_broken_file(run_tool, tmp_path):
    a = write_record(tmp_path / "a.json", make_record([LINUX]))
    bad = tmp_path / "broken.json"
    bad.write_text('{"schema": 2,', encoding="utf-8")
    result = run_tool(MERGE, "--out", tmp_path / "merged.json", a, bad)
    assert result.returncode != 0
    assert "broken.json" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.xfail(strict=True, reason="defect: source.asset is not compared, the first record wins and the result depends on argument order")
def test_merge_with_another_source_asset_is_refused_or_order_free(run_tool, tmp_path):
    other = make_record([WINDOWS])
    other["source"]["asset"] = "occt-source-8.0.1-2.tar.gz"
    a = write_record(tmp_path / "a.json", make_record([LINUX]))
    b = write_record(tmp_path / "b.json", other)
    one = run_tool(MERGE, "--out", tmp_path / "one.json", a, b)
    two = run_tool(MERGE, "--out", tmp_path / "two.json", b, a)
    if one.returncode != 0 and two.returncode != 0:
        return
    assert one.returncode == 0 and two.returncode == 0
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()


@pytest.mark.parametrize("kind", ["kernel", "mesher"])
def test_stage_puts_exactly_the_listed_files(run_tool, tmp_path, kind):
    folder = tmp_path / "stage"
    result = stage(run_tool, folder, kind)
    assert result.returncode == 0, result.stderr
    assert {p.name for p in folder.iterdir()} == STAGED_NAMES[kind]
    assert all(p.is_file() and not p.is_symlink() for p in folder.iterdir())


@pytest.mark.parametrize("kind", ["kernel", "mesher"])
def test_stage_copies_licence_texts_byte_identical(run_tool, repo_root, tmp_path, kind):
    folder = tmp_path / "stage"
    assert stage(run_tool, folder, kind).returncode == 0
    for name, source in COPIED[kind].items():
        assert sha(folder / name) == sha(repo_root / "formcad" / source)


@pytest.mark.parametrize("kind", ["kernel", "mesher"])
def test_stage_fills_templates_and_keeps_the_rest_byte_identical(run_tool, repo_root, tmp_path, kind):
    folder = tmp_path / "stage"
    assert stage(run_tool, folder, kind, release="9.1.0-3", triple=WINDOWS).returncode == 0
    for name, source in FILLED[kind].items():
        text = (repo_root / "formcad" / source).read_bytes().decode("utf-8")
        assert "{release}" in text
        expected = text.replace("{release}", "9.1.0-3").replace("{triple}", WINDOWS).encode("utf-8")
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("kind", ["kernel", "mesher"])
def test_stage_leaves_no_placeholder_unfilled(run_tool, tmp_path, kind):
    folder = tmp_path / "stage"
    assert stage(run_tool, folder, kind, release="7.7.7", triple=INTEL_MAC).returncode == 0
    for path in folder.iterdir():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\{[A-Za-z_]+\}", text), path.name
    readme = (folder / "README.md").read_text(encoding="utf-8")
    assert "7.7.7" in readme and INTEL_MAC in readme
    assert "7.7.7" in (folder / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["kernel", "mesher"])
def test_stage_reproduces_the_notice_hashes_of_the_shipped_record(run_tool, repo_root, tmp_path, kind):
    record = json.loads((repo_root / "formcad" / "records" / SHIPPED[kind]).read_text(encoding="utf-8"))
    assert record["targets"]
    for triple, target in record["targets"].items():
        folder = tmp_path / triple
        assert stage(run_tool, folder, kind, release=record["release"], triple=triple).returncode == 0
        top = {f["path"]: f for f in target["files"] if "/" not in f["path"] and f["path"] not in target["programs"]}
        assert set(top) == {p.name for p in folder.iterdir()}
        for name, entry in top.items():
            assert sha(folder / name) == entry["sha256"], (triple, name)
            assert (folder / name).stat().st_size == entry["bytes"]
        for name, digest in record["documents"].items():
            assert sha(folder / name) == digest


def test_stage_twice_changes_nothing(run_tool, tmp_path):
    folder = tmp_path / "stage"
    assert stage(run_tool, folder, "kernel").returncode == 0
    first = snapshot(folder)
    assert stage(run_tool, folder, "kernel").returncode == 0
    assert snapshot(folder) == first


def test_stage_unknown_kind_exits_non_zero_with_a_message(run_tool, tmp_path):
    folder = tmp_path / "stage"
    result = stage(run_tool, folder, "ffmpeg")
    assert result.returncode != 0
    assert "ffmpeg" in result.stderr and "kind" in result.stderr
    assert list(folder.iterdir()) == []


def test_stage_missing_folder_exits_non_zero_with_a_message(run_tool, tmp_path):
    folder = tmp_path / "absent"
    result = run_tool(STAGE, "--kind", "mesher", "--stage", folder, "--release", "6.2.2608", "--triple", LINUX)
    assert result.returncode != 0
    assert str(folder) in result.stderr
    assert not folder.exists()


def test_stage_refuses_a_bad_release_or_triple(run_tool, tmp_path):
    folder = tmp_path / "stage"
    bad_release = stage(run_tool, folder, "kernel", release="../8.0.1")
    assert bad_release.returncode != 0 and "release" in bad_release.stderr
    bad_triple = stage(run_tool, folder, "kernel", triple="riscv64-unknown-linux-gnu")
    assert bad_triple.returncode != 0 and "riscv64-unknown-linux-gnu" in bad_triple.stderr
    assert list(folder.iterdir()) == []
