import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

TOOL = "formcad/tools/pack_release.py"
TRIPLE = "x86_64-unknown-linux-gnu"
OFFER = "Written offer: the source is on the release page for three years.\n"

KINDS = {
    "kernel": {
        "program": "formcad-kernel",
        "tag": "occt",
        "prefix": "occt-lgpl",
        "record": "occt-licence.json",
        "source": "occt-source",
        "licence": "LGPL-2.1-only WITH OCCT-exception-1.0",
        "documents": ["LGPL-2.1.txt", "OCCT-EXCEPTION.txt", "THIRD-PARTY-NOTICES.txt"],
    },
    "mesher": {
        "program": "formcad-mesher",
        "tag": "mesher",
        "prefix": "mesher-lgpl",
        "record": "mesher-licence.json",
        "source": "mesher-source",
        "licence": "LGPL-2.1-only",
        "documents": ["LGPL-2.1.txt", "NETGEN-LICENSE.txt", "THIRD-PARTY-NOTICES.txt"],
    },
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Build:
    def __init__(self, base, kind="kernel", release="9.9.9-2"):
        self.kind = kind
        self.release = release
        self.spec = KINDS[kind]
        self.base = base
        self.stage = base / "stage"
        self.folder_name = f"{self.spec['prefix']}-{release}-{TRIPLE}"
        self.archive_name = self.folder_name + ".tar.gz"
        self.folder = self.stage / self.folder_name
        self.out = base / "out"
        self.record = base / "repo" / "records" / self.spec["record"]
        self.lock = base / "requirements.lock"
        self.contents = {
            self.spec["program"]: b"#!/bin/sh\necho fake program\n",
            "_internal/libfake.so": b"\x7fNOTELF fake library bytes",
            "LGPL-OFFER.txt": OFFER.encode(),
            "README.md": b"# fake build\n",
        }
        for name in self.spec["documents"]:
            self.contents[name] = f"notice text of {name}\n".encode()
        for rel, data in self.contents.items():
            path = self.folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.folder / self.spec["program"]).chmod(0o755)
        self.record.parent.mkdir(parents=True)
        (self.record.parent.parent / "SOURCES.md").write_text("# Sources\n", encoding="utf-8")
        (self.record.parent.parent / "LGPL-OFFER.txt").write_text(OFFER, encoding="utf-8")
        self.lock.write_text("fakepkg==1.0 --hash=sha256:00\n", encoding="utf-8")
        self.write_record(self.make_record())

    def make_record(self):
        program = self.spec["program"]
        order = [program] + sorted(k for k in self.contents if k != program)
        files = [{"path": rel, "sha256": sha(self.contents[rel]), "bytes": len(self.contents[rel])} for rel in order]
        return {
            "schema": 2,
            "program": program,
            "release": self.release,
            "licence": self.spec["licence"],
            "documents": {name: sha(self.contents[name]) for name in self.spec["documents"]},
            "source": {
                "upstream": [
                    {
                        "name": "FakeCascade Technology",
                        "version": "1.2.3",
                        "licence": "LGPL-2.1-only",
                        "url": "https://example.invalid/fake/archive/refs/tags/V1_2_3.tar.gz",
                        "sha256": None,
                    },
                    {
                        "name": "fakewheel",
                        "version": "4.5",
                        "licence": "Apache-2.0",
                        "url": "https://example.invalid/project/fakewheel/4.5/",
                        "sha256": None,
                    },
                ],
                "asset": f"{self.spec['source']}-{self.release}.tar.gz",
                "lock": {"path": "requirements.lock", "sha256": sha(self.lock.read_bytes())},
            },
            "targets": {
                TRIPLE: {
                    "protocol": 2,
                    "archive": self.archive_name,
                    "folder": self.folder_name,
                    "programs": [program],
                    "library_dir": "_internal",
                    "minimum_os": "glibc 2.35",
                    "files": files,
                }
            },
            "licence_gates": {"forbidden_names": [], "needs_review": []},
        }

    def write_record(self, data, path=None):
        target = path or self.record
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return target

    def args(self, command, *extra):
        base = [command, "--kind", self.kind, "--record", self.record, "--out", self.out]
        if command == "pack":
            base += ["--stage-root", self.stage]
        return base + list(extra)


@pytest.fixture
def build(tmp_path):
    return Build(tmp_path)


@pytest.fixture
def packed(build, run_tool):
    result = run_tool(TOOL, *build.args("pack", "--lock", build.lock))
    assert result.returncode == 0, result.stderr
    return build


def archive_files(path):
    found = {}
    with tarfile.open(path) as bundle:
        for member in bundle.getmembers():
            if member.isfile():
                found[member.name] = bundle.extractfile(member).read()
    return found


def read_sums(out):
    sums = {}
    for line in (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    return sums


def test_pack_names_the_archive_as_the_readme_says(packed):
    assert packed.archive_name == f"occt-lgpl-{packed.release}-{TRIPLE}.tar.gz"
    assert (packed.out / packed.archive_name).is_file()
    archives = sorted(p.name for p in packed.out.glob("*.tar.gz"))
    assert archives == [packed.archive_name]


def test_mesher_pack_uses_the_mesher_names(tmp_path, run_tool):
    build = Build(tmp_path, kind="mesher", release="6.2.2608")
    result = run_tool(TOOL, *build.args("pack"))
    assert result.returncode == 0, result.stderr
    assert (build.out / f"mesher-lgpl-6.2.2608-{TRIPLE}.tar.gz").is_file()
    assert (build.out / "mesher-licence.json").is_file()
    notes = (build.out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert "# formcad-mesher 6.2.2608" in notes
    assert "`mesher-6.2.2608`" in notes


def test_archive_holds_exactly_the_build_files(packed):
    found = archive_files(packed.out / packed.archive_name)
    expected = {f"{packed.folder_name}/{rel}": data for rel, data in packed.contents.items()}
    assert found == expected


def test_archive_has_one_top_folder_and_fixed_metadata(packed):
    with tarfile.open(packed.out / packed.archive_name) as bundle:
        members = bundle.getmembers()
    assert members[0].name == packed.folder_name and members[0].isdir()
    assert all(m.name == packed.folder_name or m.name.startswith(packed.folder_name + "/") for m in members)
    assert {m.uid for m in members} == {0} and {m.gid for m in members} == {0}
    assert len({m.mtime for m in members}) == 1
    modes = {m.name.rsplit("/", 1)[-1]: m.mode for m in members if m.isfile()}
    assert modes["formcad-kernel"] == 0o755
    assert modes["libfake.so"] == 0o644


def test_published_record_pins_every_archived_file_by_hash_and_size(packed):
    published = json.loads((packed.out / "occt-licence.json").read_text(encoding="utf-8"))
    assert published["schema"] == 2
    assert published["program"] == "formcad-kernel"
    assert published["release"] == packed.release
    target = published["targets"][TRIPLE]
    assert target["archive"] == packed.archive_name
    assert target["folder"] == packed.folder_name
    found = archive_files(packed.out / packed.archive_name)
    rows = target["files"]
    assert rows[0]["path"] == "formcad-kernel"
    assert {row["path"] for row in rows} == {name.split("/", 1)[1] for name in found}
    for row in rows:
        assert set(row) == {"path", "sha256", "bytes"}
        data = found[f"{packed.folder_name}/{row['path']}"]
        assert row["sha256"] == hashlib.sha256(data).hexdigest()
        assert row["bytes"] == len(data)
    for name, digest in published["documents"].items():
        assert digest == hashlib.sha256(found[f"{packed.folder_name}/{name}"]).hexdigest()


def test_published_record_is_a_byte_copy_and_is_not_inside_the_archive(packed):
    assert (packed.out / "occt-licence.json").read_bytes() == packed.record.read_bytes()
    names = archive_files(packed.out / packed.archive_name)
    assert not any(name.endswith("occt-licence.json") for name in names)


def test_packing_twice_gives_identical_bytes(build, run_tool, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for out in (first, second):
        result = run_tool(
            TOOL, "pack", "--kind", "kernel", "--record", build.record, "--out", out, "--stage-root", build.stage
        )
        assert result.returncode == 0, result.stderr
    for name in (build.archive_name, "SHA256SUMS", "RELEASE_NOTES.md", "occt-licence.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_checksum_file_lists_every_asset_with_its_hash(packed):
    sums = read_sums(packed.out)
    assert sorted(sums) == sorted([packed.archive_name, "occt-licence.json", "requirements.lock"])
    for name, digest in sums.items():
        assert digest == hashlib.sha256((packed.out / name).read_bytes()).hexdigest()
    assert (packed.out / "requirements.lock").read_bytes() == packed.lock.read_bytes()


def test_release_notes_name_kind_release_assets_and_offer(packed):
    notes = (packed.out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert notes.splitlines()[0] == f"# formcad-kernel {packed.release}"
    assert f"`occt-{packed.release}`" in notes
    assert "LGPL-2.1-only WITH OCCT-exception-1.0" in notes
    for name in (packed.archive_name, "occt-licence.json", "requirements.lock"):
        assert f"- `{name}`" in notes
    assert f"`occt-source-{packed.release}.tar.gz`" in notes
    assert "LGPL-OFFER.txt" in notes
    assert "THIRD-PARTY-NOTICES.txt" in notes
    assert "three years" in notes
    assert "never replaced" in notes
    assert f"- `{TRIPLE}`: {len(packed.contents)} files, minimum OS glibc 2.35" in notes


@pytest.mark.xfail(strict=True, reason="defect: RELEASE_NOTES.md lists the assets by name only, without their SHA-256")
def test_release_notes_give_each_asset_hash(packed):
    notes = (packed.out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    for name, digest in read_sums(packed.out).items():
        assert digest in notes, name


def test_app_record_is_the_flat_record_the_app_keeps(build, run_tool, tmp_path):
    target = tmp_path / "app" / "kernel-licence.json"
    target.parent.mkdir()
    result = run_tool(TOOL, "app-record", "--kind", "kernel", "--record", build.record, "--out", target)
    assert result.returncode == 0, result.stderr
    flat = json.loads(target.read_text(encoding="utf-8"))
    assert flat["schema"] == 1
    assert flat["program"] == "formcad-kernel"
    assert flat["release"] == build.release
    assert flat["licence"] == build.spec["licence"]
    assert flat["source"] == "https://github.com/nodemode-dev/formai-binaries"
    assert flat["url"].endswith("/releases/download/occt-{release}/occt-lgpl-{release}-{triple}.tar.gz")
    assert flat["folder"] == "occt-lgpl-{release}-{triple}"
    assert flat["triples"] == [
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-unknown-linux-gnu",
        "x86_64-pc-windows-msvc",
    ]
    rows = flat["files"]
    assert len(rows) == len(build.contents)
    assert rows[0]["path"] == "formcad-kernel"
    for row in rows:
        assert set(row) == {"triple", "path", "sha256"}
        assert row["triple"] == TRIPLE
        assert row["sha256"] == hashlib.sha256(build.contents[row["path"]]).hexdigest()
    assert str(len(rows)) in result.stdout


def test_check_passes_on_what_pack_made(packed, run_tool):
    result = run_tool(TOOL, *packed.args("check"))
    assert result.returncode == 0, result.stderr
    assert "check passed" in result.stdout
    assert "3 checksums" in result.stdout
    assert not any((packed.out / ".check").glob("*"))


def test_check_fails_and_names_an_asset_after_one_byte_changes(packed, run_tool):
    asset = packed.out / "requirements.lock"
    data = bytearray(asset.read_bytes())
    data[0] ^= 1
    asset.write_bytes(bytes(data))
    result = run_tool(TOOL, *packed.args("check"))
    assert result.returncode != 0
    assert "check failed" in result.stderr
    assert "requirements.lock" in result.stderr
    assert "check passed" not in result.stdout


def test_check_fails_and_names_the_archive_after_one_byte_changes(packed, run_tool):
    asset = packed.out / packed.archive_name
    data = bytearray(asset.read_bytes())
    data[len(data) // 2] ^= 0x40
    asset.write_bytes(bytes(data))
    result = run_tool(TOOL, *packed.args("check"))
    assert result.returncode != 0
    assert "check failed" in result.stderr
    assert packed.archive_name in result.stderr


def test_check_exits_non_zero_and_names_a_missing_asset(packed, run_tool):
    (packed.out / packed.archive_name).unlink()
    result = run_tool(TOOL, *packed.args("check"))
    assert result.returncode != 0
    assert packed.archive_name in result.stderr
    assert "check passed" not in result.stdout


@pytest.mark.xfail(strict=True, reason="defect: check on a missing asset dies with a FileNotFoundError traceback instead of a check failed message")
def test_check_reports_a_missing_asset_without_a_traceback(packed, run_tool):
    (packed.out / packed.archive_name).unlink()
    result = run_tool(TOOL, *packed.args("check"))
    assert result.returncode != 0
    assert "check failed" in result.stderr
    assert "Traceback" not in result.stderr


def test_check_fails_when_the_record_names_a_file_the_archive_lacks(packed, run_tool, tmp_path):
    data = json.loads(packed.record.read_text(encoding="utf-8"))
    data["targets"][TRIPLE]["files"].append({"path": "_internal/libghost.so", "sha256": "0" * 64, "bytes": 3})
    other = packed.write_record(data, tmp_path / "other-record.json")
    result = run_tool(TOOL, "check", "--kind", "kernel", "--record", other, "--out", packed.out)
    assert result.returncode != 0
    assert "check failed" in result.stderr
    assert "_internal/libghost.so is missing" in result.stderr
    assert TRIPLE in result.stderr


def test_check_fails_when_the_archive_holds_a_file_the_record_lacks(packed, run_tool, tmp_path):
    data = json.loads(packed.record.read_text(encoding="utf-8"))
    rows = data["targets"][TRIPLE]["files"]
    data["targets"][TRIPLE]["files"] = [row for row in rows if row["path"] != "_internal/libfake.so"]
    other = packed.write_record(data, tmp_path / "other-record.json")
    result = run_tool(TOOL, "check", "--kind", "kernel", "--record", other, "--out", packed.out)
    assert result.returncode != 0
    assert "_internal/libfake.so is not in the record" in result.stderr


def test_wrong_kind_is_refused_with_a_message(build, run_tool):
    result = run_tool(
        TOOL, "pack", "--kind", "ffmpeg", "--record", build.record, "--out", build.out, "--stage-root", build.stage
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "ffmpeg" in result.stderr
    assert not build.out.exists()


def test_unknown_subcommand_and_missing_options_are_refused(build, run_tool):
    unknown = run_tool(TOOL, "publish", "--kind", "kernel", "--record", build.record, "--out", build.out)
    assert unknown.returncode == 2
    assert "invalid choice" in unknown.stderr
    bare = run_tool(TOOL, "pack", "--kind", "kernel", "--record", build.record, "--out", build.out)
    assert bare.returncode == 2
    assert "--stage-root" in bare.stderr


def test_missing_staged_folder_packs_nothing_and_says_so(build, run_tool, tmp_path):
    empty = tmp_path / "no-builds-here"
    empty.mkdir()
    result = run_tool(
        TOOL, "pack", "--kind", "kernel", "--record", build.record, "--out", build.out, "--stage-root", empty
    )
    assert result.returncode != 0
    assert "nothing was packed" in result.stderr
    assert f"skip {TRIPLE}" in result.stdout
    assert build.folder_name in result.stdout
    assert not (build.out / "SHA256SUMS").exists()


def test_pack_stops_when_a_staged_file_differs_from_the_record(build, run_tool):
    (build.folder / "_internal" / "libfake.so").write_bytes(b"tampered")
    result = run_tool(TOOL, *build.args("pack"))
    assert result.returncode != 0
    assert "staged folder does not match the record" in result.stderr
    assert "_internal/libfake.so" in result.stderr
    assert not (build.out / build.archive_name).exists()


def test_pack_stops_when_a_stray_file_sits_in_the_staged_folder(build, run_tool):
    (build.folder / "_internal" / "stray.bin").write_bytes(b"x")
    result = run_tool(TOOL, *build.args("pack"))
    assert result.returncode != 0
    assert "_internal/stray.bin is not in the record" in result.stderr


def test_pack_stops_when_a_notice_changed(build, run_tool):
    (build.folder / "LGPL-2.1.txt").write_bytes(b"edited licence text\n")
    result = run_tool(TOOL, *build.args("pack"))
    assert result.returncode != 0
    assert "notice LGPL-2.1.txt changed" in result.stderr
    assert TRIPLE in result.stderr


def test_finalize_rewrites_the_same_checksums_and_notes(packed, run_tool):
    sums = (packed.out / "SHA256SUMS").read_bytes()
    notes = (packed.out / "RELEASE_NOTES.md").read_bytes()
    (packed.out / "SHA256SUMS").unlink()
    (packed.out / "RELEASE_NOTES.md").unlink()
    result = run_tool(TOOL, *packed.args("finalize", "--lock", packed.lock))
    assert result.returncode == 0, result.stderr
    assert (packed.out / "SHA256SUMS").read_bytes() == sums
    assert (packed.out / "RELEASE_NOTES.md").read_bytes() == notes


def test_finalize_names_the_archives_that_are_missing(build, run_tool):
    build.out.mkdir()
    result = run_tool(TOOL, *build.args("finalize"))
    assert result.returncode != 0
    assert "missing" in result.stderr
    assert build.archive_name in result.stderr
    assert not (build.out / "SHA256SUMS").exists()


def fake_upstream(served):
    def urlopen(url, timeout=None):
        served.append(url)
        return io.BytesIO(b"fake upstream source archive bytes")

    return urlopen


def test_source_asset_is_built_from_patched_downloads_and_pins_the_record(build, load_module, monkeypatch, tmp_path):
    module = load_module(TOOL)
    served = []
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_upstream(served))
    worker = tmp_path / "worker.py"
    worker.write_text("print('worker')\n", encoding="utf-8")
    argv = ["pack_release.py"] + [str(a) for a in build.args("pack", "--lock", build.lock, "--fetch-sources", "--worker-src", worker)]
    monkeypatch.setattr(module.sys, "argv", argv)
    module.main()
    assert served == ["https://example.invalid/fake/archive/refs/tags/V1_2_3.tar.gz"]
    name = f"occt-source-{build.release}"
    found = archive_files(build.out / f"{name}.tar.gz")
    assert found == {
        f"{name}/FakeCascade-1.2.3.tar.gz": b"fake upstream source archive bytes",
        f"{name}/requirements.lock": build.lock.read_bytes(),
        f"{name}/SOURCES.md": b"# Sources\n",
        f"{name}/LGPL-OFFER.txt": OFFER.encode(),
        f"{name}/worker/worker.py": b"print('worker')\n",
    }
    pinned = json.loads(build.record.read_text(encoding="utf-8"))["source"]["upstream"]
    assert pinned[0]["sha256"] == hashlib.sha256(b"fake upstream source archive bytes").hexdigest()
    assert pinned[1]["sha256"] is None
    assert (build.out / "occt-licence.json").read_bytes() == build.record.read_bytes()
    sums = read_sums(build.out)
    assert f"{name}.tar.gz" in sums
    assert sums[f"{name}.tar.gz"] == hashlib.sha256((build.out / f"{name}.tar.gz").read_bytes()).hexdigest()
    assert f"- `{name}.tar.gz`" in (build.out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert not (build.out / ".source").exists()


def test_source_asset_stops_when_upstream_differs_from_the_pin(build, load_module, monkeypatch):
    data = json.loads(build.record.read_text(encoding="utf-8"))
    data["source"]["upstream"][0]["sha256"] = "1" * 64
    build.write_record(data)
    module = load_module(TOOL)
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_upstream([]))
    monkeypatch.setattr(module.sys, "argv", ["pack_release.py"] + [str(a) for a in build.args("pack", "--fetch-sources")])
    with pytest.raises(SystemExit) as stopped:
        module.main()
    message = str(stopped.value.code)
    assert "FakeCascade Technology" in message
    assert "does not match the pin" in message
    assert "1" * 64 in message
    assert not (build.out / "SHA256SUMS").exists()


def test_release_notes_function_lists_assets_sorted_and_unrecorded_minimum_os(load_module):
    module = load_module(TOOL)
    record = {
        "release": "1.0",
        "licence": "LGPL-2.1-only",
        "targets": {"x86_64-pc-windows-msvc": {"files": [{}, {}]}},
    }
    notes = module.release_notes(module.fcbin.KINDS["mesher"], record, ["b.tar.gz", "a.json"])
    assert notes.index("- `a.json`") < notes.index("- `b.tar.gz`")
    assert "Tag `mesher-1.0`. Licence: LGPL-2.1-only." in notes
    assert "`mesher-source-1.0.tar.gz`" in notes
    assert "- `x86_64-pc-windows-msvc`: 2 files, minimum OS not recorded" in notes
    assert notes.endswith("\n")


def test_real_records_are_not_changed_by_these_tests(repo_root, build, run_tool, tmp_path):
    records = sorted((repo_root / "formcad" / "records").glob("*.json"))
    before = {p.name: p.read_bytes() for p in records}
    result = run_tool(TOOL, *build.args("pack"), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert {p.name: p.read_bytes() for p in records} == before
    assert sorted(before) == ["mesher-licence.json", "occt-licence.json"]
