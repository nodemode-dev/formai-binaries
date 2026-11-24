import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile

import pytest

VERSION = "9.9.9-3"
MAC = "aarch64-apple-darwin"
LINUX = "x86_64-unknown-linux-gnu"
WINDOWS = "x86_64-pc-windows-msvc"
DOCUMENT_TEXTS = {
    "LGPL-2.1.txt": b"pretend licence text\n",
    "THIRD-PARTY-NOTICES.txt": b"pretend notices\n",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def triple_files(triple):
    return {
        f"ffmpeg-{triple}": f"program ffmpeg for {triple}\n".encode() * 3,
        f"ffprobe-{triple}": f"program ffprobe for {triple}\n".encode() * 2,
        f"lib-{triple}/libavcodec.so": bytes(range(256)) + triple.encode(),
        f"lib-{triple}/libavutil.so": b"\x00\x01util " + triple.encode(),
    }


def entries(contents):
    return [{"path": path, "sha256": sha(data), "bytes": len(data)} for path, data in contents.items()]


def write_files(folder, contents):
    for path, data in contents.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def make_record(triples, built=(MAC,)):
    return {
        "release": VERSION,
        "source": {
            "ffmpeg": {"version": "9.9.9", "url": "https://example.invalid/ffmpeg-9.9.9.tar.xz", "sha256": sha(b"ffmpeg source")},
            "libopus": {"version": "1.2.3", "url": "https://example.invalid/opus-1.2.3.tar.gz", "sha256": sha(b"opus source")},
            "obligation": "a sentence, not a download",
            "headers": {"repository": "https://example.invalid/headers", "commit": "abc"},
        },
        "targets": {
            triple: {
                "minimum_os": "macOS 11.0 (measured)" if triple == MAC else "glibc 2.35 (measured)",
                "files": entries(triple_files(triple)),
            }
            for triple in triples
        },
        "documents": {name: sha(text) for name, text in DOCUMENT_TEXTS.items()},
    }


class Site:
    def __init__(self, root, repo_root, record, built):
        self.home = root / "home"
        self.home.mkdir()
        self.script = self.home / "pack_release.py"
        shutil.copyfile(repo_root / "pack_release.py", self.script)
        for name, text in DOCUMENT_TEXTS.items():
            (self.home / name).write_bytes(text)
        self.record = record
        self.write_record()
        self.binaries = root / "binaries"
        self.binaries.mkdir()
        for triple in built:
            write_files(self.binaries, triple_files(triple))
        self.out = root / "out"

    def write_record(self):
        (self.home / "ffmpeg-licence.json").write_text(json.dumps(self.record, indent=2), encoding="utf-8")

    def files(self, triple):
        return self.record["targets"][triple]["files"]

    def archive(self, triple):
        return self.out / f"ffmpeg-lgpl-{VERSION}-{triple}.tar.gz"

    def archives(self):
        return sorted(self.out.glob("*.tar.gz")) if self.out.is_dir() else []


@pytest.fixture
def make_site(tmp_path, repo_root):
    def make(triples=(MAC,), built=(MAC,)):
        return Site(tmp_path, repo_root, make_record(triples), built)

    return make


@pytest.fixture
def module(load_module, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    return load_module("pack_release.py")


@pytest.fixture
def homed(module, make_site, monkeypatch):
    site = make_site()
    monkeypatch.setattr(module, "HERE", site.home)
    site.out.mkdir()
    return module, site


def run(run_tool, site, *extra):
    return run_tool(site.script, "--binaries", site.binaries, "--out", site.out, *extra, cwd=site.home)


def read_members(archive):
    with tarfile.open(archive) as bundle:
        infos = bundle.getmembers()
        data = {info.name: bundle.extractfile(info).read() for info in infos if info.isfile()}
    return infos, data


def test_digest_equals_hashlib_sha256_of_the_file(module, tmp_path):
    data = os.urandom(3 * (1 << 20) + 17)
    path = tmp_path / "blob.bin"
    path.write_bytes(data)
    assert module.digest(path) == sha(data)
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert module.digest(empty) == sha(b"")


def test_problems_is_empty_for_a_matching_folder(module, tmp_path):
    contents = triple_files(MAC)
    write_files(tmp_path, contents)
    assert module.problems(tmp_path, entries(contents)) == []


def test_problems_names_a_missing_file(module, tmp_path):
    contents = triple_files(MAC)
    write_files(tmp_path, contents)
    (tmp_path / f"ffprobe-{MAC}").unlink()
    assert module.problems(tmp_path, entries(contents)) == [f"missing ffprobe-{MAC}"]


def test_problems_names_a_file_with_a_different_hash(module, tmp_path):
    contents = triple_files(MAC)
    write_files(tmp_path, contents)
    changed = f"lib-{MAC}/libavutil.so"
    original = contents[changed]
    (tmp_path / changed).write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    found = module.problems(tmp_path, entries(contents))
    assert len(found) == 1
    assert changed in found[0]
    assert "does not match" in found[0]


@pytest.mark.xfail(strict=True, reason="defect: problems() never compares a file with the record's bytes field, so a wrong recorded size passes")
def test_problems_names_a_file_whose_size_differs_from_the_record(module, tmp_path):
    contents = triple_files(MAC)
    write_files(tmp_path, contents)
    listed = entries(contents)
    listed[0]["bytes"] += 1
    found = module.problems(tmp_path, listed)
    assert any(listed[0]["path"] in line for line in found)


@pytest.mark.xfail(strict=True, reason="defect: problems() ignores files in the build folder that the record does not list")
def test_problems_names_an_extra_file_in_the_library_folder(module, tmp_path):
    contents = triple_files(MAC)
    write_files(tmp_path, contents)
    (tmp_path / f"lib-{MAC}" / "libextra.so").write_bytes(b"not recorded")
    found = module.problems(tmp_path, entries(contents))
    assert any("libextra.so" in line for line in found)


def test_member_has_fixed_owner_time_and_given_mode(module):
    info = module.member("top/file", size=12, mode=0o755)
    assert (info.name, info.size, info.mode, info.mtime) == ("top/file", 12, 0o755, 0)
    assert (info.uid, info.gid, info.uname, info.gname) == (0, 0, "", "")
    assert info.isfile()
    plain = module.member("top/doc")
    assert (plain.size, plain.mode) == (0, 0o644)
    assert module.member("top", mode=0o755, folder=True).isdir()


def test_pack_holds_exactly_the_recorded_files_and_documents_in_one_named_folder(homed):
    module, site = homed
    archive = module.pack(site.binaries, MAC, site.files(MAC), VERSION, site.out)
    top = f"ffmpeg-lgpl-{VERSION}-{MAC}"
    assert archive == site.archive(MAC)
    infos, data = read_members(archive)
    expected = dict(triple_files(MAC))
    expected.update(DOCUMENT_TEXTS)
    expected["ffmpeg-licence.json"] = (site.home / "ffmpeg-licence.json").read_bytes()
    assert data == {f"{top}/{path}": content for path, content in expected.items()}
    assert sorted(info.name for info in infos if info.isdir()) == [top, f"{top}/lib-{MAC}"]
    assert len(infos) == len(expected) + 2
    for entry in site.files(MAC):
        packed = data[f"{top}/{entry['path']}"]
        assert sha(packed) == entry["sha256"]
        assert len(packed) == entry["bytes"]


def test_pack_sets_fixed_owner_modes_and_times_on_every_entry(homed):
    module, site = homed
    infos, _ = read_members(module.pack(site.binaries, MAC, site.files(MAC), VERSION, site.out))
    top = f"ffmpeg-lgpl-{VERSION}-{MAC}"
    documents = {f"{top}/{name}" for name in (*DOCUMENT_TEXTS, "ffmpeg-licence.json")}
    assert infos
    for info in infos:
        assert (info.uid, info.gid, info.uname, info.gname, info.mtime) == (0, 0, "", "", 0), info.name
        assert info.mode == (0o644 if info.name in documents else 0o755), info.name
    names = [info.name for info in infos if info.isfile()]
    assert names == sorted(names)


def test_pack_writes_no_time_or_name_into_the_gzip_header(homed):
    module, site = homed
    raw = module.pack(site.binaries, MAC, site.files(MAC), VERSION, site.out).read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    assert raw[3] & 0x08 == 0
    assert raw[4:8] == b"\x00\x00\x00\x00"
    assert gzip.decompress(raw)[:6] != b""


def test_packing_twice_with_different_file_times_gives_identical_bytes(homed, tmp_path):
    module, site = homed
    first = module.pack(site.binaries, MAC, site.files(MAC), VERSION, site.out).read_bytes()
    stamp = 1_000_000_000
    for path in sorted(site.binaries.rglob("*")):
        stamp += 86_400
        os.utime(path, (stamp, stamp))
        if path.is_file():
            path.chmod(0o600)
    other = tmp_path / "second"
    other.mkdir()
    second = module.pack(site.binaries, MAC, site.files(MAC), VERSION, other).read_bytes()
    assert sha(first) == sha(second)
    assert first == second


def test_recheck_accepts_a_freshly_packed_archive(homed):
    module, site = homed
    archive = module.pack(site.binaries, MAC, site.files(MAC), VERSION, site.out)
    assert module.recheck(archive, site.files(MAC)) == []


def rewrite(archive, change):
    top = archive.name[: -len(".tar.gz")]
    _, data = read_members(archive)
    data = change(top, data)
    with tarfile.open(archive, "w:gz") as bundle:
        for name in sorted(data):
            info = tarfile.TarInfo(name)
            info.size = len(data[name])
            bundle.addfile(info, io.BytesIO(data[name]))


def test_recheck_finds_a_tampered_file(homed):
    module, site = homed
    archive = module.pack(site.binaries, MAC, site.files(MAC), VERSION, site.out)

    def tamper(top, data):
        data[f"{top}/ffmpeg-{MAC}"] += b"!"
        return data

    rewrite(archive, tamper)
    found = module.recheck(archive, site.files(MAC))
    assert len(found) == 1
    assert f"ffmpeg-{MAC}" in found[0]
    assert "does not match" in found[0]


def test_recheck_finds_a_file_removed_from_the_archive(homed):
    module, site = homed
    archive = module.pack(site.binaries, MAC, site.files(MAC), VERSION, site.out)

    def drop(top, data):
        del data[f"{top}/lib-{MAC}/libavcodec.so"]
        return data

    rewrite(archive, drop)
    assert module.recheck(archive, site.files(MAC)) == [f"missing lib-{MAC}/libavcodec.so"]


def test_recheck_rejects_an_archive_with_two_top_folders(homed):
    module, site = homed
    archive = module.pack(site.binaries, MAC, site.files(MAC), VERSION, site.out)

    def second_root(top, data):
        data["another/file"] = b"x"
        return data

    rewrite(archive, second_root)
    found = module.recheck(archive, site.files(MAC))
    assert len(found) == 1
    assert "one folder" in found[0]


def test_notes_name_version_packed_skipped_and_sources(module, tmp_path):
    record = make_record((MAC, LINUX, WINDOWS))
    sources = [tmp_path / "ffmpeg-9.9.9.tar.xz", tmp_path / "libopus-1.2.3.tar.gz"]
    text = module.notes(record, VERSION, [MAC, LINUX], [WINDOWS], sources)
    lines = text.splitlines()
    assert VERSION in lines[0]
    assert f"| {MAC} | `ffmpeg-lgpl-{VERSION}-{MAC}.tar.gz` | macOS 11.0 |" in lines
    assert f"| {LINUX} | `ffmpeg-lgpl-{VERSION}-{LINUX}.tar.gz` | glibc 2.35 |" in lines
    skipped = [line for line in lines if f"`{WINDOWS}`" in line]
    assert len(skipped) == 1
    assert not skipped[0].startswith("|")
    assert f"ffmpeg-lgpl-{VERSION}-{WINDOWS}" not in text
    source_lines = [line for line in lines if "`ffmpeg-9.9.9.tar.xz`" in line]
    assert len(source_lines) == 1
    assert "`libopus-1.2.3.tar.gz`" in source_lines[0]
    assert "SHA256SUMS" in text
    assert text.endswith("\n")


class Reply:
    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def read(self, count=-1):
        return self.stream.read(count)

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False


def serve(module, monkeypatch, pages):
    asked = []

    def opener(url, timeout=None):
        asked.append(url)
        return Reply(pages[url])

    monkeypatch.setattr(module.urllib.request, "urlopen", opener)
    return asked


def test_fetch_sources_keeps_downloads_that_match_the_record(module, monkeypatch, tmp_path, capsys):
    record = make_record((MAC,))
    asked = serve(
        module,
        monkeypatch,
        {
            "https://example.invalid/ffmpeg-9.9.9.tar.xz": b"ffmpeg source",
            "https://example.invalid/opus-1.2.3.tar.gz": b"opus source",
        },
    )
    made = module.fetch_sources(record, tmp_path)
    assert made == [tmp_path / "ffmpeg-9.9.9.tar.xz", tmp_path / "libopus-1.2.3.tar.gz"]
    assert made[0].read_bytes() == b"ffmpeg source"
    assert made[1].read_bytes() == b"opus source"
    assert asked == ["https://example.invalid/ffmpeg-9.9.9.tar.xz", "https://example.invalid/opus-1.2.3.tar.gz"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["ffmpeg-9.9.9.tar.xz", "libopus-1.2.3.tar.gz"]
    capsys.readouterr()


def test_fetch_sources_stops_and_keeps_nothing_when_a_download_differs(module, monkeypatch, tmp_path, capsys):
    record = make_record((MAC,))
    serve(
        module,
        monkeypatch,
        {
            "https://example.invalid/ffmpeg-9.9.9.tar.xz": b"ffmpeg source",
            "https://example.invalid/opus-1.2.3.tar.gz": b"changed upstream",
        },
    )
    with pytest.raises(SystemExit) as stopped:
        module.fetch_sources(record, tmp_path)
    assert "libopus" in str(stopped.value.code)
    assert not (tmp_path / "libopus-1.2.3.tar.gz").exists()
    capsys.readouterr()


def test_command_packs_built_targets_and_exits_zero(make_site, run_tool):
    site = make_site(triples=(MAC, LINUX, WINDOWS), built=(MAC, LINUX))
    result = run(run_tool, site)
    assert result.returncode == 0, result.stderr
    assert site.archives() == [site.archive(MAC), site.archive(LINUX)]
    assert sorted(path.name for path in site.out.iterdir()) == sorted(
        [site.archive(MAC).name, site.archive(LINUX).name, "RELEASE_NOTES.md", "SHA256SUMS"]
    )
    for triple in (MAC, LINUX):
        top = f"ffmpeg-lgpl-{VERSION}-{triple}"
        _, data = read_members(site.archive(triple))
        for path, content in triple_files(triple).items():
            assert data[f"{top}/{path}"] == content
        assert site.archive(triple).name in result.stdout
    assert WINDOWS in result.stdout


def test_command_writes_sha256sums_for_every_archive(make_site, run_tool):
    site = make_site(triples=(MAC, LINUX), built=(MAC, LINUX))
    assert run(run_tool, site).returncode == 0
    lines = (site.out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert lines == [f"{sha(path.read_bytes())}  {path.name}" for path in site.archives()]
    assert len(lines) == 2


def test_command_writes_release_notes_naming_version_packed_and_skipped(make_site, run_tool):
    site = make_site(triples=(MAC, LINUX), built=(MAC,))
    assert run(run_tool, site).returncode == 0
    text = (site.out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert VERSION in text.splitlines()[0]
    assert f"`ffmpeg-lgpl-{VERSION}-{MAC}.tar.gz`" in text
    assert f"`{LINUX}`" in text
    assert f"ffmpeg-lgpl-{VERSION}-{LINUX}" not in text


def test_command_uses_the_ffmpeg_version_when_the_record_has_no_release(make_site, run_tool):
    site = make_site()
    del site.record["release"]
    site.write_record()
    assert run(run_tool, site).returncode == 0
    assert [path.name for path in site.archives()] == [f"ffmpeg-lgpl-9.9.9-{MAC}.tar.gz"]
    with tarfile.open(site.archives()[0]) as bundle:
        assert {name.split("/")[0] for name in bundle.getnames()} == {f"ffmpeg-lgpl-9.9.9-{MAC}"}


def test_command_run_twice_with_new_file_times_gives_the_same_archive(make_site, run_tool, tmp_path):
    site = make_site()
    assert run(run_tool, site).returncode == 0
    first = site.archive(MAC).read_bytes()
    sums = (site.out / "SHA256SUMS").read_text(encoding="utf-8")
    for path in site.binaries.rglob("*"):
        os.utime(path, (1_234_567_890, 1_234_567_890))
    site.out = tmp_path / "again"
    assert run(run_tool, site).returncode == 0
    assert site.archive(MAC).read_bytes() == first
    assert (site.out / "SHA256SUMS").read_text(encoding="utf-8") == sums


def test_command_stops_on_a_changed_file_and_packs_nothing(make_site, run_tool):
    site = make_site()
    (site.binaries / f"ffprobe-{MAC}").write_bytes(b"swapped")
    result = run(run_tool, site)
    assert result.returncode != 0
    assert MAC in result.stderr
    assert f"ffprobe-{MAC} does not match the record" in result.stderr
    assert site.archives() == []
    assert not (site.out / "SHA256SUMS").exists()


def test_command_stops_on_a_missing_file_and_packs_nothing(make_site, run_tool):
    site = make_site()
    (site.binaries / f"lib-{MAC}" / "libavcodec.so").unlink()
    result = run(run_tool, site)
    assert result.returncode != 0
    assert f"missing lib-{MAC}/libavcodec.so" in result.stderr
    assert site.archives() == []


@pytest.mark.xfail(strict=True, reason="defect: an unrecorded file in lib-<triple>/ is not reported and the run succeeds")
def test_command_stops_on_an_extra_file_and_packs_nothing(make_site, run_tool):
    site = make_site()
    (site.binaries / f"lib-{MAC}" / "libextra.so").write_bytes(b"not recorded")
    result = run(run_tool, site)
    assert result.returncode != 0
    assert "libextra.so" in result.stderr
    assert site.archives() == []


@pytest.mark.xfail(strict=True, reason="defect: a file whose length differs from the record's bytes field is not reported and the run succeeds")
def test_command_stops_on_a_size_that_differs_from_the_record(make_site, run_tool):
    site = make_site()
    site.record["targets"][MAC]["files"][0]["bytes"] += 5
    site.write_record()
    result = run(run_tool, site)
    assert result.returncode != 0
    assert f"ffmpeg-{MAC}" in result.stderr
    assert site.archives() == []


@pytest.mark.xfail(strict=True, reason="defect: targets packed before a later target fails stay in the output folder, so something is packed")
def test_command_leaves_no_archive_when_a_later_target_is_bad(make_site, run_tool):
    site = make_site(triples=(MAC, LINUX), built=(MAC, LINUX))
    (site.binaries / f"ffmpeg-{LINUX}").write_bytes(b"swapped")
    result = run(run_tool, site)
    assert result.returncode != 0
    assert LINUX in result.stderr
    assert site.archives() == []


def test_command_stops_when_a_licence_text_differs_from_the_record(make_site, run_tool):
    site = make_site()
    (site.home / "LGPL-2.1.txt").write_bytes(b"edited licence\n")
    result = run(run_tool, site)
    assert result.returncode != 0
    assert "LGPL-2.1.txt" in result.stderr
    assert site.archives() == []


def test_command_exits_non_zero_with_a_message_when_no_target_is_built(make_site, run_tool):
    site = make_site(triples=(MAC, LINUX), built=())
    result = run(run_tool, site)
    assert result.returncode != 0
    assert MAC in result.stdout + result.stderr
    assert LINUX in result.stdout + result.stderr
    assert site.archives() == []
