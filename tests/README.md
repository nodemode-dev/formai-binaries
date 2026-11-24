# Tests

The tests of this repository's release tools, build scripts and records.

## Run them

```bash
pip install -r tests/requirements.txt
python3 -m pytest
```

One file: `python3 -m pytest tests/formcad/test_fcbin.py`. The `tests` workflow runs the same line on every pull
request and on `main`. The tools use the Python standard library only, so pytest is the one thing to install.

## Layout

| File or folder | What it tests |
| --- | --- |
| `tests/test_ffmpeg_pack_release.py` | `pack_release.py`: the ffmpeg release archive and its checks |
| `tests/test_ffmpeg_update_record.py` | `update_record.py`: what a build writes into `ffmpeg-licence.json` |
| `tests/test_records_and_licences.py` | the three records and the licence texts as they stand |
| `tests/build/` | `build/linux.sh`, `build/windows-cross.sh` and the `build` workflow, without building anything |
| `tests/formcad/` | `formcad/tools/`: `fcbin.py`, `pack_release.py`, `preflight.py`, `update_record.py`, `merge_records.py`, `stage_notices.py` |
| `tests/conftest.py` | `repo_root`, `load_module` and `run_tool` for every test |

## What a test must do

- Test what the tool is for, from outside: a small build folder made for the test, the archive or record a person
  would expect, the edges, bad input. Hashes and sizes are worked out again in the test, never copied from the tool.
- Write only under a temporary folder. The records and the licence texts in this repository are read, never written.
- Build nothing and download nothing. A script that calls an outside program gets a small stand-in for it.
- A test that finds a real fault stays as it is, marked `@pytest.mark.xfail(strict=True, reason="defect: ...")`, so the
  suite stays green, the fault stays visible, and the mark fails the run on the day the fault is fixed and the mark
  was not removed.
