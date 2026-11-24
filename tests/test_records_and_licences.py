import hashlib
import json
import re
import string
import subprocess

import pytest

HEX64 = re.compile(r"[0-9a-f]{64}")
TRIPLES = {
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "x86_64-pc-windows-msvc",
}
RELEASE_FORM = {
    "ffmpeg": re.compile(r"\d+\.\d+\.\d+(-\d+)?"),
    "occt": re.compile(r"\d+\.\d+\.\d+(-\d+)?"),
    "mesher": re.compile(r"\d+\.\d+\.\d+"),
}
RECORD_FILES = {
    "ffmpeg": "ffmpeg-licence.json",
    "occt": "formcad/records/occt-licence.json",
    "mesher": "formcad/records/mesher-licence.json",
}
FORMCAD_KEYS = {"schema", "program", "release", "licence", "documents", "source", "targets", "licence_gates"}
TARGET_KEYS = {
    "protocol", "archive", "folder", "programs", "library_dir", "libraries_found_by",
    "minimum_os", "toolchain", "verification", "system_libraries_needed", "files",
}


@pytest.fixture(scope="session")
def records(repo_root):
    return {
        name: json.loads((repo_root / rel).read_text(encoding="utf-8"))
        for name, rel in RECORD_FILES.items()
    }


@pytest.fixture(scope="session")
def formcad(records):
    return {"occt": records["occt"], "mesher": records["mesher"]}


@pytest.fixture(scope="session")
def git_tags(repo_root):
    try:
        out = subprocess.run(
            ["git", "tag", "--list"], cwd=str(repo_root), capture_output=True, text=True, timeout=30
        )
    except OSError:
        pytest.skip("git is not available")
    tags = set(out.stdout.split())
    if out.returncode != 0 or not tags:
        pytest.skip("the checkout has no git tags")
    return tags


def read(repo_root, rel):
    return (repo_root / rel).read_bytes()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def all_file_lists(records):
    for name, rec in records.items():
        for triple, target in rec["targets"].items():
            yield name, triple, target["files"]


def tag_of(name, record):
    return f"{name}-{record['release']}"


def test_every_record_is_json_with_its_required_fields(records, formcad):
    assert set(records) == {"ffmpeg", "occt", "mesher"}
    for name, rec in formcad.items():
        assert FORMCAD_KEYS <= set(rec), name
        assert rec["schema"] == 2
    assert formcad["occt"]["program"] == "formcad-kernel"
    assert formcad["mesher"]["program"] == "formcad-mesher"
    ff = records["ffmpeg"]
    assert ff["schema"] == 2
    assert {"licence", "source", "targets", "documents", "release"} <= set(ff)


def test_every_record_covers_the_four_triples(records):
    for name, rec in records.items():
        assert set(rec["targets"]) == TRIPLES, name


def test_formcad_targets_hold_the_documented_keys(formcad):
    for name, rec in formcad.items():
        for triple, target in rec["targets"].items():
            assert TARGET_KEYS <= set(target), (name, triple)
            assert target["programs"] and isinstance(target["programs"], list)
            assert target["files"], (name, triple)
    assert {t["protocol"] for t in formcad["occt"]["targets"].values()} == {2}
    assert {t["protocol"] for t in formcad["mesher"]["targets"].values()} == {1}


def test_target_names_follow_release_and_triple(formcad):
    for name, rec in formcad.items():
        for triple, target in rec["targets"].items():
            prefix = "occt" if name == "occt" else "mesher"
            folder = f"{prefix}-lgpl-{rec['release']}-{triple}"
            assert target["folder"] == folder
            assert target["archive"] == folder + ".tar.gz"
            exe = target["programs"][0]
            assert exe == rec["program"] + (".exe" if "windows" in triple else "")


def test_every_sha256_is_64_lower_case_hex(records):
    seen = 0
    for name, triple, files in all_file_lists(records):
        for entry in files:
            assert HEX64.fullmatch(entry["sha256"]), (name, triple, entry["path"])
            seen += 1
    assert seen > 100
    for name, rec in records.items():
        for doc, digest in rec["documents"].items():
            assert HEX64.fullmatch(digest), (name, doc)
    for name in ("occt", "mesher"):
        assert HEX64.fullmatch(records[name]["source"]["lock"]["sha256"])


def test_every_size_is_a_whole_number_not_negative(records):
    for name, triple, files in all_file_lists(records):
        for entry in files:
            size = entry["bytes"]
            assert type(size) is int and size >= 0, (name, triple, entry["path"])


def test_file_paths_are_clean_relative_paths(records):
    for name, triple, files in all_file_lists(records):
        for entry in files:
            path = entry["path"]
            assert path and not path.startswith("/"), (name, path)
            assert not re.match(r"[A-Za-z]:", path), (name, path)
            assert "\\" not in path, (name, path)
            assert ".." not in path.split("/"), (name, path)
            assert "" not in path.split("/"), (name, path)
            if "link" in entry:
                link = entry["link"]
                assert not link.startswith("/") and "\\" not in link
                assert ".." not in link.split("/")


def test_no_file_is_listed_twice_in_one_build(records):
    for name, triple, files in all_file_lists(records):
        paths = [f["path"] for f in files]
        assert len(paths) == len(set(paths)), (name, triple)
        lowered = [p.lower() for p in paths]
        assert len(lowered) == len(set(lowered)), (name, triple)


def test_programs_are_listed_first_among_the_files(formcad, records):
    for name, rec in formcad.items():
        for triple, target in rec["targets"].items():
            assert target["files"][0]["path"] == target["programs"][0]
    for triple, target in records["ffmpeg"]["targets"].items():
        paths = [f["path"] for f in target["files"]]
        assert paths[: len(target["programs"])] == target["programs"], triple
        for path in paths[len(target["programs"]):]:
            assert path.startswith(target["library_dir"] + "/"), (triple, path)


def test_release_names_have_the_documented_form(records):
    for name, rec in records.items():
        assert RELEASE_FORM[name].fullmatch(rec["release"]), (name, rec["release"])
    assert records["mesher"]["release"] == "6.2.2608"
    assert records["occt"]["release"] == "8.0.1"
    ff = records["ffmpeg"]
    assert ff["release"].split("-")[0] == ff["source"]["ffmpeg"]["version"]


def test_each_record_release_is_a_git_tag(records, git_tags):
    for name, rec in records.items():
        assert tag_of(name, rec) in git_tags, name


def test_ffmpeg_configure_lines_hold_no_gpl_or_nonfree(records):
    for triple, target in records["ffmpeg"]["targets"].items():
        line = target["configure"]
        assert "--enable-gpl" not in line, triple
        assert "--enable-nonfree" not in line, triple
        assert "--disable-gpl" in line and "--disable-nonfree" in line, triple
        for banned in ("libx264", "libx265", "libfdk"):
            assert banned not in line, (triple, banned)
        assert target["ffmpeg"] == records["ffmpeg"]["source"]["ffmpeg"]["version"]
    assert records["ffmpeg"]["licence"]["gpl_or_nonfree_components"] == "none"


def test_formcad_gates_forbid_gpl_video_codecs(formcad):
    for name, rec in formcad.items():
        gates = rec["licence_gates"]
        assert "libx264*" in gates["forbidden_names"], name
        assert gates["needs_review"], name


def iter_sources(records):
    for name, rec in records.items():
        src = rec["source"]
        if name == "ffmpeg":
            for key, item in src.items():
                if isinstance(item, dict):
                    yield name, key, item
        else:
            for item in src["upstream"]:
                yield name, item["name"], item


def test_every_named_source_has_a_location(records):
    count = 0
    for name, key, item in iter_sources(records):
        assert item.get("url") or item.get("repository"), (name, key)
        assert item.get("version") or item.get("commit"), (name, key)
        count += 1
    assert count >= 10


@pytest.mark.xfail(
    strict=True,
    reason="defect: cadquery-ocp-novtk is named in the occt record source.upstream with no sha256 pin",
)
def test_every_named_source_has_a_hash_or_commit(records):
    for name, key, item in iter_sources(records):
        if "commit" in item:
            assert re.fullmatch(r"[0-9a-f]{40}", item["commit"]), (name, key)
        else:
            assert HEX64.fullmatch(item.get("sha256") or ""), (name, key)


def test_sources_md_names_every_upstream_url_of_the_formcad_records(repo_root, formcad):
    text = read(repo_root, "formcad/SOURCES.md").decode("utf-8")
    for name, rec in formcad.items():
        for item in rec["source"]["upstream"]:
            if item["url"].endswith(".tar.gz"):
                assert item["url"] in text, (name, item["name"])
            assert item["name"].split()[0] in text, (name, item["name"])


def test_the_two_lgpl_texts_are_the_same_licence(repo_root):
    def words(rel):
        text = read(repo_root, rel).decode("utf-8").replace("\r\n", "\n")
        return text, " ".join(text.split())

    root_text, root_words = words("LGPL-2.1.txt")
    sub_text, sub_words = words("formcad/licences/LGPL-2.1.txt")
    for text in (root_text, sub_text):
        assert "GNU LESSER GENERAL PUBLIC LICENSE" in text
        assert "Version 2.1, February 1999" in text
        assert "END OF TERMS AND CONDITIONS" in text
    body = lambda w: w[w.index("Preamble"): w.index("END OF TERMS AND CONDITIONS")]
    assert body(root_words) == body(sub_words)


def test_occt_exception_and_netgen_texts_are_present_and_not_empty(repo_root):
    exc = read(repo_root, "formcad/licences/OCCT-EXCEPTION.txt").decode("utf-8")
    assert len(exc.strip()) > 300
    assert "Open CASCADE exception" in exc and "1.0" in exc
    netgen = read(repo_root, "formcad/licences/NETGEN-LICENSE.txt").decode("utf-8")
    assert len(netgen.strip()) > 20000
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in netgen
    assert "Version 2.1" in netgen


def test_document_hashes_match_the_licence_files(repo_root, records):
    for name in ("occt", "mesher"):
        for doc, digest in records[name]["documents"].items():
            path = repo_root / "formcad" / "licences" / doc
            if path.exists():
                assert sha(path.read_bytes()) == digest, (name, doc)
    ff = records["ffmpeg"]["documents"]
    for doc, digest in ff.items():
        assert sha(read(repo_root, doc)) == digest, doc


def test_documents_named_by_each_record_are_the_documented_set(records):
    assert set(records["occt"]["documents"]) == {"LGPL-2.1.txt", "OCCT-EXCEPTION.txt", "THIRD-PARTY-NOTICES.txt"}
    assert set(records["mesher"]["documents"]) == {"LGPL-2.1.txt", "NETGEN-LICENSE.txt", "THIRD-PARTY-NOTICES.txt"}
    assert set(records["ffmpeg"]["documents"]) == {"LGPL-2.1.txt", "THIRD-PARTY-NOTICES.txt"}
    lic = records["ffmpeg"]["licence"]
    assert lic["text"] in records["ffmpeg"]["documents"]
    assert lic["third_party"] in records["ffmpeg"]["documents"]


def test_templates_hold_only_placeholders_the_stage_tool_fills(repo_root):
    folder = repo_root / "formcad" / "templates"
    names = sorted(p.name for p in folder.iterdir())
    assert names == [
        "README-mesher.md", "README-occt.md",
        "THIRD-PARTY-NOTICES-kernel.txt", "THIRD-PARTY-NOTICES-mesher.txt",
    ]
    for name in names:
        text = (folder / name).read_text(encoding="utf-8")
        fields = {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}
        assert fields, name
        assert fields <= {"release", "triple"}, (name, fields)
        assert "release" in fields, name
        text.format(release="9.9.9", triple="x86_64-unknown-linux-gnu")


def test_templates_name_the_release_page_of_their_program(repo_root):
    base = repo_root / "formcad" / "templates"
    pairs = {
        "THIRD-PARTY-NOTICES-kernel.txt": ("occt", "formcad-kernel", "OCCT-EXCEPTION.txt"),
        "THIRD-PARTY-NOTICES-mesher.txt": ("mesher", "formcad-mesher", "NETGEN-LICENSE.txt"),
    }
    for name, (tag, program, licence) in pairs.items():
        text = (base / name).read_text(encoding="utf-8")
        assert text.startswith(program + " {release}")
        assert f"releases/tag/{tag}-{{release}}" in text
        assert f"{tag}-source-{{release}}.tar.gz" in text
        assert licence in text and "LGPL-2.1.txt" in text


def test_offer_and_third_party_notices_agree_with_the_records(repo_root, records):
    offer = read(repo_root, "formcad/LGPL-OFFER.txt").decode("utf-8")
    assert "three years" in offer
    assert "THIRD-PARTY-NOTICES.txt" in offer
    notices = read(repo_root, "THIRD-PARTY-NOTICES.txt").decode("utf-8")
    for key in ("libopus", "libvpx", "libva", "libdrm"):
        item = records["ffmpeg"]["source"][key]
        assert f"{key} " in notices and item["version"] in notices, key
    assert "LGPL-2.1.txt" in notices


def test_licence_file_of_the_repository_is_apache(repo_root):
    for rel in ("LICENSE", "formcad/LICENSE"):
        text = read(repo_root, rel).decode("utf-8")
        assert "Apache License" in text and "Version 2.0, January 2004" in text, rel
    assert "Copyright" in read(repo_root, "formcad/LICENSE").decode("utf-8")[:200]


def test_readme_asset_names_match_the_ffmpeg_record(repo_root, records):
    readme = read(repo_root, "README.md").decode("utf-8")
    src = records["ffmpeg"]["source"]
    ver = src["ffmpeg"]["version"]
    assert f"ffmpeg-{ver}.tar.xz" in readme
    assert f"libopus-{src['libopus']['version']}.tar.gz" in readme
    assert f"libvpx-{src['libvpx']['version']}.tar.gz" in readme
    assert "--disable-gpl --disable-nonfree" in readme
    for triple in TRIPLES:
        assert f"ffmpeg-lgpl-{ver}-{triple}.tar.gz" in readme
    assert "SHA256SUMS" in readme


def test_formcad_readme_names_the_documented_tools_and_files(repo_root):
    readme = read(repo_root, "formcad/README.md").decode("utf-8")
    for rel in (
        "formcad/records/SCHEMA.md", "formcad/SOURCES.md", "formcad/LGPL-OFFER.txt",
        "formcad/tools/stage_notices.py", "formcad/tools/update_record.py",
        "formcad/tools/pack_release.py", "formcad/tools/merge_records.py",
        "formcad/tools/preflight.py", ".github/workflows/formcad-preflight.yml",
    ):
        assert (repo_root / rel).is_file(), rel
        assert rel.rsplit("/", 1)[1] in readme, rel
    for triple in TRIPLES:
        assert triple in readme


def test_formcad_versions_in_docs_match_the_records(repo_root, formcad):
    readme = read(repo_root, "formcad/README.md").decode("utf-8")
    sources = read(repo_root, "formcad/SOURCES.md").decode("utf-8")
    assert f"OpenCascade Technology {formcad['occt']['source']['upstream'][0]['version']}" in readme
    assert f"Netgen {formcad['mesher']['release']}" in readme
    assert f"Netgen {formcad['mesher']['release']}" in sources


@pytest.mark.xfail(
    strict=True,
    reason="defect: formcad/README.md and SCHEMA.md still call the filled records empty skeletons",
)
def test_docs_do_not_call_the_filled_records_empty(repo_root, formcad):
    assert all(t["files"] for rec in formcad.values() for t in rec["targets"].values())
    readme = read(repo_root, "formcad/README.md").decode("utf-8")
    schema = read(repo_root, "formcad/records/SCHEMA.md").decode("utf-8")
    assert "empty until a release is recorded" not in readme
    assert "empty skeletons" not in schema
