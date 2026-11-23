import hashlib
import json

import pytest

TOOL = "formcad/tools/preflight.py"
TRIPLE = "x86_64-unknown-linux-gnu"
KINDS = {
    "kernel": {
        "program": "formcad-kernel",
        "tag": "occt",
        "record": "occt-licence.json",
        "app": "kernel-licence.json",
        "release": "8.0.1",
        "licence": "LGPL-2.1-only WITH OCCT-exception-1.0",
        "documents": ["LGPL-2.1.txt", "OCCT-EXCEPTION.txt", "THIRD-PARTY-NOTICES.txt"],
        "protocol": 2,
    },
    "mesher": {
        "program": "formcad-mesher",
        "tag": "mesher",
        "record": "mesher-licence.json",
        "app": "mesher-licence.json",
        "release": "6.2.2608",
        "licence": "LGPL-2.1-only",
        "documents": ["LGPL-2.1.txt", "NETGEN-LICENSE.txt", "THIRD-PARTY-NOTICES.txt"],
        "protocol": 1,
    },
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def notice_bytes(repo_root, name):
    path = repo_root / "formcad" / "licences" / name
    return path.read_bytes() if path.is_file() else b"third party notices\n"


def make_record(repo_root, kind, release=None):
    info = KINDS[kind]
    release = info["release"] if release is None else release
    program = info["program"]
    documents = {name: digest(notice_bytes(repo_root, name)) for name in info["documents"]}
    files = [{"path": program, "sha256": digest(program.encode()), "bytes": len(program)}]
    for name in info["documents"]:
        files.append({"path": name, "sha256": documents[name], "bytes": len(notice_bytes(repo_root, name))})
    files.append({"path": "_internal/libexample.so", "sha256": digest(b"lib"), "bytes": 3})
    target = {
        "protocol": info["protocol"],
        "archive": f"{info['tag']}-lgpl-{release}-{TRIPLE}.tar.gz",
        "folder": f"{info['tag']}-lgpl-{release}-{TRIPLE}",
        "programs": [program],
        "library_dir": "_internal",
        "libraries_found_by": "rpath",
        "minimum_os": "glibc 2.35",
        "toolchain": "gcc 11",
        "verification": "hello handshake",
        "system_libraries_needed": ["libc.so.6"],
        "files": files,
    }
    return {
        "schema": 2,
        "program": program,
        "release": release,
        "licence": info["licence"],
        "documents": documents,
        "source": {
            "upstream": [
                {
                    "name": "Upstream",
                    "version": "1.0",
                    "licence": "LGPL-2.1-only",
                    "url": "https://example.org/upstream-1.0.tar.gz",
                    "sha256": digest(b"upstream"),
                }
            ],
            "asset": f"{info['tag']}-source-{release}.tar.gz",
            "lock": {"path": "requirements.lock", "sha256": digest(b"lock")},
        },
        "targets": {TRIPLE: target},
        "licence_gates": {"forbidden_names": ["libgpl*"], "needs_review": []},
    }


def app_copy(record):
    return {
        "schema": 1,
        "program": record["program"],
        "release": record["release"],
        "files": [
            {"triple": triple, "path": entry["path"], "sha256": entry["sha256"]}
            for triple, target in record["targets"].items()
            for entry in target["files"]
        ],
    }


@pytest.fixture
def records(repo_root):
    return {kind: make_record(repo_root, kind) for kind in KINDS}


@pytest.fixture
def check(tmp_path, repo_root, run_tool):
    def run(records, app=None, extra=()):
        folder = tmp_path / "records"
        folder.mkdir(exist_ok=True)
        for kind, record in records.items():
            path = folder / KINDS[kind]["record"]
            if record is None:
                continue
            path.write_text(record if isinstance(record, str) else json.dumps(record, indent=2), encoding="utf-8")
        args = ["--records", folder]
        if app is not None:
            data = tmp_path / "app-data"
            data.mkdir(exist_ok=True)
            for kind, copy in app.items():
                if copy is not None:
                    (data / KINDS[kind]["app"]).write_text(json.dumps(copy, indent=2), encoding="utf-8")
            args += ["--app-data", data]
        return run_tool(TOOL, *args, *extra, cwd=repo_root)

    return run


def rejected(result, name=None):
    assert result.returncode != 0
    assert "preflight passed" not in result.stdout
    if name is not None:
        assert name in result.stdout


def test_real_records_pass_the_workflow_command(repo_root, run_tool):
    result = run_tool(TOOL, "--records", "formcad/records", cwd=repo_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preflight passed" in result.stdout


def test_small_well_formed_records_pass(check, records):
    result = check(records)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preflight passed" in result.stdout


def test_empty_skeleton_passes_but_not_when_published(check, records):
    for record in records.values():
        record["release"] = ""
        record["targets"] = {}
    assert check(records).returncode == 0
    rejected(check(records, extra=["--published"]), "mesher-licence.json")


@pytest.mark.parametrize("key", ["schema", "program", "release", "licence", "documents", "source", "targets"])
def test_missing_required_field_is_reported(check, records, key):
    del records["mesher"][key]
    result = check(records)
    rejected(result, "mesher-licence.json")
    assert key in result.stdout


def test_program_of_the_other_kind_is_reported(check, records):
    records["mesher"]["program"] = "formcad-kernel"
    rejected(check(records), "mesher-licence.json")


@pytest.mark.xfail(strict=True, reason="defect: preflight never checks the release against the kind, so a mesher record with release occt-8.0.1 passes")
def test_release_name_of_the_other_kind_is_reported(check, records, repo_root):
    records["mesher"] = make_record(repo_root, "mesher", release="occt-8.0.1")
    rejected(check(records), "mesher-licence.json")


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64])
def test_file_sha256_that_is_not_64_lower_case_hex_digits_is_reported(check, records, value):
    records["mesher"]["targets"][TRIPLE]["files"][-1]["sha256"] = value
    rejected(check(records), "mesher-licence.json")


@pytest.mark.xfail(strict=True, reason="defect: a documents sha256 is only compared when the notice is in formcad/licences, so a malformed THIRD-PARTY-NOTICES.txt digest passes")
def test_document_sha256_that_is_not_lower_case_hex_is_reported(check, records):
    records["mesher"]["documents"]["THIRD-PARTY-NOTICES.txt"] = "Z" * 64
    rejected(check(records), "mesher-licence.json")


@pytest.mark.parametrize(
    "value",
    [-1, pytest.param(1.5, marks=pytest.mark.xfail(strict=True, reason="defect: bytes is only checked for being negative, so 1.5 passes"))],
)
def test_size_that_is_not_a_whole_number_is_reported(check, records, value):
    records["mesher"]["targets"][TRIPLE]["files"][-1]["bytes"] = value
    rejected(check(records), "mesher-licence.json")


def test_size_written_as_text_fails(check, records):
    records["mesher"]["targets"][TRIPLE]["files"][-1]["bytes"] = "3"
    rejected(check(records))


@pytest.mark.xfail(strict=True, reason="defect: preflight does not look for a file path listed twice")
def test_duplicate_file_path_is_reported(check, records):
    files = records["mesher"]["targets"][TRIPLE]["files"]
    files.append(dict(files[-1]))
    rejected(check(records), "mesher-licence.json")


@pytest.mark.xfail(strict=True, reason="defect: preflight accepts an absolute file path or one that climbs out with ..")
@pytest.mark.parametrize("path", ["/usr/lib/libexample.so", "_internal/../../libexample.so"])
def test_absolute_or_parent_path_is_reported(check, records, path):
    records["mesher"]["targets"][TRIPLE]["files"][-1]["path"] = path
    rejected(check(records), "mesher-licence.json")


def test_invalid_json_fails(check, records):
    records["mesher"] = '{"schema": 2, "program": '
    rejected(check(records))


def test_missing_record_file_fails(check, records):
    records["mesher"] = None
    rejected(check(records))


def test_app_copy_equal_to_the_record_passes(check, records):
    result = check(records, app={kind: app_copy(record) for kind, record in records.items()})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preflight passed" in result.stdout


def test_app_copy_with_another_hash_fails(check, records):
    app = {kind: app_copy(record) for kind, record in records.items()}
    app["mesher"]["files"][-1]["sha256"] = digest(b"other")
    rejected(check(records, app=app), "mesher-licence.json")


def test_app_copy_without_a_file_fails(check, records):
    app = {kind: app_copy(record) for kind, record in records.items()}
    del app["kernel"]["files"][-1]
    rejected(check(records, app=app), "kernel-licence.json")


def test_app_copy_of_another_release_fails(check, records):
    app = {kind: app_copy(record) for kind, record in records.items()}
    app["mesher"]["release"] = "6.2.2609"
    result = check(records, app=app)
    rejected(result, "mesher-licence.json")
    assert "6.2.2609" in result.stdout


def test_missing_app_copy_is_reported(check, records, tmp_path):
    app = {"kernel": app_copy(records["kernel"]), "mesher": None}
    result = check(records, app=app)
    rejected(result)
    assert f"{tmp_path / 'app-data' / 'mesher-licence.json'} is missing" in result.stdout


def unfill(record):
    record["release"] = ""
    record["targets"] = {}


def test_published_passes_when_every_record_is_filled(check, records):
    result = check(records, extra=["--published"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preflight passed" in result.stdout


@pytest.mark.parametrize("kind", ["kernel", "mesher"])
def test_published_fails_for_an_unfilled_record_and_names_it(check, records, kind):
    unfill(records[kind])
    result = check(records, extra=["--published"])
    rejected(result, f"{KINDS[kind]['record']}: not filled in")
    other = "mesher" if kind == "kernel" else "kernel"
    assert KINDS[other]["record"] not in result.stdout


def test_published_fails_when_only_the_targets_are_missing(check, records):
    records["kernel"]["targets"] = {}
    rejected(check(records, extra=["--published"]), "occt-licence.json: not filled in")


def test_published_fails_when_only_the_release_is_empty(check, records):
    records["mesher"]["release"] = ""
    rejected(check(records, extra=["--published"]), "mesher-licence.json: not filled in")


def test_unfilled_record_passes_without_published(check, records):
    unfill(records["kernel"])
    result = check(records)
    assert result.returncode == 0, result.stdout + result.stderr


def test_published_with_a_kind_ignores_an_unfilled_record_of_the_other_kind(check, records):
    unfill(records["kernel"])
    result = check(records, extra=["--published", "--kind", "mesher"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preflight passed" in result.stdout


def test_published_with_a_kind_ignores_an_unfilled_mesher_record_for_kernel(check, records):
    unfill(records["mesher"])
    result = check(records, extra=["--published", "--kind", "kernel"])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["kernel", "mesher"])
def test_published_with_a_kind_still_requires_that_kinds_record(check, records, kind):
    unfill(records[kind])
    result = check(records, extra=["--published", "--kind", kind])
    rejected(result, f"{KINDS[kind]['record']}: not filled in")


def test_published_with_a_kind_reports_only_that_kinds_record(check, records):
    unfill(records["kernel"])
    unfill(records["mesher"])
    result = check(records, extra=["--published", "--kind", "kernel"])
    rejected(result, "occt-licence.json: not filled in")
    assert "mesher-licence.json" not in result.stdout


def test_kind_without_published_changes_nothing(check, records):
    unfill(records["kernel"])
    result = check(records, extra=["--kind", "mesher"])
    assert result.returncode == 0, result.stdout + result.stderr
