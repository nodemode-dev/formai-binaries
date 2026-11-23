import hashlib
import json
import os
import re
import shutil
import subprocess

import pytest

SCRIPTS = ["build/linux.sh", "build/windows-cross.sh"]
ARCHIVES = ["ffmpeg-7.1.5.tar.xz", "libopus-1.6.1.tar.gz", "libvpx-1.17.0.tar.gz"]
LGPL = "LGPL version 2.1 or later"

FAKE_TAR = """#!/usr/bin/env bash
echo "tar $PWD $*" >> "$FAKE_LOG"
name=$(basename "${@: -1}")
name=${name%.tar.*}
name=${name/#libopus/opus}
mkdir -p "$name"
cat > "$name/configure" <<'EOF'
#!/usr/bin/env bash
echo "License: $FAKE_LICENSE"
echo "$FAKE_CONFIG_H" > config.h
EOF
chmod +x "$name/configure"
"""

FAKE_QUIET = """#!/usr/bin/env bash
echo "$(basename "$0") $PWD $*" >> "$FAKE_LOG"
"""

FAKE_NPROC = """#!/usr/bin/env bash
echo 2
"""

QUIET_NAMES = ["make", "git", "cp", "curl", "meson", "ninja", "sha256sum", "patchelf", "ldd", "objdump"]


def read(repo_root, relative):
    return (repo_root / relative).read_text()


def configure_block(text):
    start = text.index("./configure \\\n  --prefix=\"$OUT\"")
    end = text.index("grep -q \"License:", start)
    return text[start:end]


def job_block(workflow, job):
    start = workflow.index("\n  " + job + ":\n")
    following = re.search(r"\n  [a-z]+:\n", workflow[start + 1:])
    return workflow[start:] if following is None else workflow[start:start + 1 + following.start()]


@pytest.fixture
def sandbox(tmp_path, repo_root):
    def make(script, good_hashes=True):
        root = tmp_path / "repo"
        (root / "build").mkdir(parents=True)
        (root / "dist").mkdir()
        shutil.copy(repo_root / script, root / script)
        source = {}
        for name, file in zip(["ffmpeg", "libopus", "libvpx"], ARCHIVES):
            data = ("archive " + file).encode()
            (root / "dist" / file).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest() if good_hashes else "0" * 64
            source[name] = {"url": "http://invalid.invalid/" + file, "sha256": digest}
        (root / "ffmpeg-licence.json").write_text(json.dumps({"source": source}))
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        for name in QUIET_NAMES:
            (fake_bin / name).write_text(FAKE_QUIET)
        (fake_bin / "tar").write_text(FAKE_TAR)
        (fake_bin / "nproc").write_text(FAKE_NPROC)
        for fake in fake_bin.iterdir():
            fake.chmod(0o755)
        log = tmp_path / "calls.log"
        log.write_text("")

        def run(licence=LGPL, config_h="#define CONFIG_GPL 0"):
            env = dict(os.environ)
            env.update(
                PATH=str(fake_bin) + os.pathsep + env["PATH"],
                WORK=str(tmp_path / "work"),
                FAKE_LOG=str(log),
                FAKE_LICENSE=licence,
                FAKE_CONFIG_H=config_h,
            )
            result = subprocess.run(
                ["bash", str(root / script)], env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=60
            )
            return result, log.read_text().splitlines()

        return run

    return make


def ffmpeg_was_made(calls):
    return any(line.startswith("make ") and line.split()[1].endswith("/ffmpeg-7.1.5") for line in calls)


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_passes_bash_syntax_check(repo_root, script):
    result = subprocess.run(["bash", "-n", str(repo_root / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_starts_with_bash_shebang(repo_root, script):
    assert read(repo_root, script).splitlines()[0] == "#!/usr/bin/env bash"


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_is_executable_in_git(repo_root, script):
    result = subprocess.run(["git", "ls-files", "-s", script], cwd=str(repo_root), capture_output=True, text=True)
    assert result.stdout.startswith("100755 "), result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_stops_on_first_error(repo_root, script):
    code = [line for line in read(repo_root, script).splitlines() if line and not line.startswith("#")]
    assert code[0] == "set -euo pipefail"


@pytest.mark.parametrize("script", SCRIPTS)
def test_archives_are_checked_against_recorded_sha256_before_unpacking(repo_root, script):
    text = read(repo_root, script)
    check = text.index("digest != record[name][\"sha256\"]")
    assert "sys.exit(" in text[check:text.index("\nPY\n", check)]
    assert check < text.index("tar xf")
    for archive in ARCHIVES:
        assert archive in text[:check]


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_unpacked_archive_is_a_verified_one(repo_root, script):
    text = read(repo_root, script)
    unpacked = re.findall(r"tar xf \"\$HERE/dist/([^\"]+)\"", text)
    assert len(unpacked) >= 3
    for archive in unpacked:
        position = text.index("tar xf \"$HERE/dist/" + archive)
        if archive in ARCHIVES:
            continue
        variable = re.match(r"\$([A-Z]+)\.tar", archive).group(1)
        fetched = text.index("fetch \"$" + variable + ".tar")
        assert "\"$" + variable + "_SHA256\"" in text[fetched:text.index("\n", fetched)]
        assert fetched < position


def test_linux_extra_sources_are_pinned_by_version_and_sha256(repo_root):
    text = read(repo_root, "build/linux.sh")
    assert re.search(r"^LIBDRM=libdrm-\d+\.\d+\.\d+$", text, re.M)
    assert re.search(r"^LIBVA=libva-\d+\.\d+\.\d+$", text, re.M)
    assert re.search(r"^LIBDRM_SHA256=[0-9a-f]{64}$", text, re.M)
    assert re.search(r"^LIBVA_SHA256=[0-9a-f]{64}$", text, re.M)
    assert "sha256sum -c --quiet - || { echo \"$1 is not the pinned archive\"; exit 1; }" in text


@pytest.mark.parametrize("script", SCRIPTS)
def test_nvidia_headers_are_cloned_at_the_pinned_tag(repo_root, script):
    text = read(repo_root, script)
    assert re.search(r"^NVCODEC_TAG=n12\.0\.16\.1\b", text, re.M)
    assert "--branch \"$NVCODEC_TAG\" https://github.com/FFmpeg/nv-codec-headers.git" in text
    assert "nv-codec-headers 12.0.16.1" in read(repo_root, "THIRD-PARTY-NOTICES.txt")


@pytest.mark.xfail(strict=True, reason="defect: windows-cross.sh clones the AMF headers from the default branch with no tag or commit pin")
def test_every_git_clone_names_a_pinned_tag_or_commit(repo_root):
    for script in SCRIPTS:
        clones = [line for line in read(repo_root, script).splitlines() if "git clone" in line]
        assert clones
        for line in clones:
            assert "--branch" in line, line


@pytest.mark.xfail(strict=True, reason="defect: cloned header commits are only written to a file, never compared with a pinned commit before the build")
@pytest.mark.parametrize("script", SCRIPTS)
def test_cloned_commits_are_compared_with_a_pinned_commit(repo_root, script):
    text = read(repo_root, script)
    assert re.search(r"^[A-Z_]+_COMMIT=[0-9a-f]{40}$", text, re.M)


def test_ffmpeg_version_is_the_same_everywhere(repo_root):
    versions = set()
    for script in SCRIPTS:
        found = set(re.findall(r"ffmpeg-(\d+\.\d+(?:\.\d+)?)", read(repo_root, script)))
        assert found, script
        versions |= found
    workflow_names = re.findall(r"name: ffmpeg-lgpl-([0-9.]+)-", read(repo_root, ".github/workflows/build.yml"))
    assert len(workflow_names) == 2
    versions |= set(workflow_names)
    readme = set(re.findall(r"ffmpeg-lgpl-(\d+\.\d+\.\d+)-", read(repo_root, "README.md")))
    assert readme
    versions |= readme
    record = json.loads(read(repo_root, "ffmpeg-licence.json"))
    versions |= set(re.findall(r"ffmpeg-(\d+\.\d+\.\d+)\.tar", record["source"]["ffmpeg"]["url"]))
    assert versions == {"7.1.5"}


def test_companion_source_versions_match_the_readme(repo_root):
    readme = read(repo_root, "README.md")
    for script in SCRIPTS:
        text = read(repo_root, script)
        for archive in ARCHIVES:
            assert archive in readme
            assert archive in text


@pytest.mark.parametrize("script", SCRIPTS)
def test_no_configure_line_enables_gpl_nonfree_or_version3(repo_root, script):
    text = read(repo_root, script)
    for flag in ["--enable-gpl", "--enable-nonfree", "--enable-version3"]:
        assert flag not in text
    block = configure_block(text)
    for flag in ["--disable-gpl", "--disable-nonfree", "--disable-version3", "--enable-shared", "--disable-static"]:
        assert flag in block


@pytest.mark.parametrize("script", SCRIPTS)
def test_licence_checks_come_between_configure_and_make(repo_root, script):
    text = read(repo_root, script)
    configure = text.index("./configure \\\n  --prefix=\"$OUT\"")
    licence = text.index("grep -q \"License: LGPL version 2.1 or later\" \"$WORK/configure.out\" ||", configure)
    config_h = text.index("grep -qE \"^#define CONFIG_(GPL|NONFREE) 1\" config.h &&", configure)
    make = text.index("\nmake -j\"$JOBS\"", configure)
    assert configure < licence < config_h < make


def test_linux_checks_every_encoder_it_enables(repo_root):
    text = read(repo_root, "build/linux.sh")
    block = configure_block(text)
    assert "--enable-nvenc" in block and "--enable-vaapi" in block
    checks = text[text.index("echo \"== checks\""):]
    for encoder in ["h264_nvenc", "hevc_nvenc", "h264_vaapi", "hevc_vaapi"]:
        assert encoder in checks
    assert checks.count("missing\"; exit 1; }") == 3
    assert checks.index("hevc_vaapi") < checks.index("== done")


def test_windows_checks_every_encoder_it_enables(repo_root):
    text = read(repo_root, "build/windows-cross.sh")
    block = configure_block(text)
    for flag in ["--enable-mediafoundation", "--enable-nvenc", "--enable-amf"]:
        assert flag in block
    checks = text[text.index("echo \"== checks\""):]
    assert "for encoder in h264_mf hevc_mf h264_nvenc hevc_nvenc h264_amf hevc_amf; do" in checks
    assert "missing\"; exit 1; }" in checks
    assert text.index("make install >/dev/null\n\necho \"== checks\"") > text.index("--enable-amf")


def test_linux_checks_the_glibc_floor(repo_root):
    text = read(repo_root, "build/linux.sh")
    assert "GLIBC_2.35 ] || { echo \"needs newer glibc than 2.35\"; exit 1; }" in text


def test_linux_job_runs_on_ubuntu_2204(repo_root):
    workflow = read(repo_root, ".github/workflows/build.yml")
    linux = job_block(workflow, "linux")
    assert "runs-on: ubuntu-22.04" in linux
    assert "run: build/linux.sh" in linux
    assert "run: build/windows-cross.sh" in job_block(workflow, "windows")


def test_workflow_uploads_the_folders_the_linux_script_writes(repo_root):
    text = read(repo_root, "build/linux.sh")
    linux = job_block(read(repo_root, ".github/workflows/build.yml"), "linux")
    assert "WORK=${WORK:-/tmp/lbuild}" in text
    assert "STAGE=$WORK/stage" in text
    assert "> \"$WORK/configure.out\"" in text
    assert "> \"$WORK/nv-codec-headers.commit\"" in text
    paths = re.findall(r"^\s+(/tmp/\S+)$", linux, re.M)
    assert paths == ["/tmp/lbuild/stage/", "/tmp/lbuild/configure.out", "/tmp/lbuild/nv-codec-headers.commit"]
    assert "if-no-files-found: error" in linux
    assert "name: ffmpeg-lgpl-7.1.5-x86_64-unknown-linux-gnu" in linux
    assert "TRIPLE=x86_64-unknown-linux-gnu" in text


def test_workflow_uploads_the_folders_the_windows_script_writes(repo_root):
    text = read(repo_root, "build/windows-cross.sh")
    windows = job_block(read(repo_root, ".github/workflows/build.yml"), "windows")
    assert "WORK=${WORK:-/tmp/xbuild}" in text
    assert "OUT=$WORK/install-win" in text
    assert "--prefix=\"$OUT\"" in text
    assert "> \"$WORK/amf.commit\"" in text
    assert "> \"$WORK/nv-codec-headers.commit\"" in text
    paths = re.findall(r"^\s+(/tmp/\S+)$", windows, re.M)
    assert paths == [
        "/tmp/xbuild/install-win/bin/",
        "/tmp/xbuild/configure.out",
        "/tmp/xbuild/nv-codec-headers.commit",
        "/tmp/xbuild/amf.commit",
    ]
    assert "if-no-files-found: error" in windows


def test_workflow_only_runs_when_started_by_hand(repo_root):
    workflow = read(repo_root, ".github/workflows/build.yml")
    header = workflow[workflow.index("\non:"):workflow.index("\njobs:")]
    assert header.split() == ["on:", "workflow_dispatch:"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_wrong_sha256_stops_the_run_before_anything_is_unpacked(sandbox, script):
    result, calls = sandbox(script, good_hashes=False)()
    assert result.returncode != 0
    assert "is not the recorded" in result.stderr
    assert not [line for line in calls if line.startswith("tar ")]


@pytest.mark.parametrize("script", SCRIPTS)
def test_gpl_configure_output_is_refused_before_make(sandbox, script):
    result, calls = sandbox(script)(licence="GPL version 3 or later")
    assert result.returncode == 1
    assert "configure did not report LGPL 2.1 or later" in result.stdout
    assert not ffmpeg_was_made(calls)


@pytest.mark.parametrize("script", SCRIPTS)
def test_config_header_enabling_gpl_is_refused_before_make(sandbox, script):
    result, calls = sandbox(script)(config_h="#define CONFIG_GPL 1")
    assert result.returncode == 1
    assert "config.h enables GPL or nonfree code" in result.stdout
    assert not ffmpeg_was_made(calls)


@pytest.mark.parametrize("script", SCRIPTS)
def test_lgpl_configure_output_goes_on_to_make(sandbox, script):
    result, calls = sandbox(script)()
    assert "configure did not report" not in result.stdout
    assert "config.h enables" not in result.stdout
    assert ffmpeg_was_made(calls)


def test_linux_run_fails_when_the_built_ffmpeg_lacks_an_encoder(sandbox):
    result, calls = sandbox("build/linux.sh")()
    assert result.returncode != 0
    assert "== checks" in result.stdout
    assert "== done" not in result.stdout
