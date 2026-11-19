# formCAD binaries

Binary releases of the two LGPL worker programs that FormCAD downloads on first use. Git holds text only; every binary lives on a GitHub Release of `nodemode-dev/formai-binaries`, a repository shared by the form apps (it was formcut-binaries and is being renamed). This folder, `formcad/`, holds formCAD's files and nothing else in the repository depends on it. `release/formCAD-binaries/` in the FormCAD repository mirrors this folder; it is prepared there and pushed here by the owner.

| Program | Contains | Tag | Asset per platform |
| --- | --- | --- | --- |
| `formcad-kernel` | OpenCascade Technology 8.0.1 (LGPL-2.1 with the Open CASCADE exception), build123d, rhino3dm | `occt-<release>` | `occt-lgpl-<release>-<triple>.tar.gz` |
| `formcad-mesher` | Netgen 6.2.2608 and the OpenCascade build in netgen-occt (LGPL-2.1) | `mesher-<release>` | `mesher-lgpl-<release>-<triple>.tar.gz` |

Triples: `aarch64-apple-darwin`, `x86_64-apple-darwin`, `x86_64-unknown-linux-gnu`, `x86_64-pc-windows-msvc`. A published tag is never replaced; a rebuild gets a new number such as `8.0.1-2`.

The tags `occt-<release>` and `mesher-<release>` are formCAD's. The ffmpeg builds of formcut use `ffmpeg-<release>`, so the three prefixes never collide and a release of one app never touches another's.

## Layout

The paths below are relative to `formcad/`.

| Path | What it is |
| --- | --- |
| `records/` | `occt-licence.json`, `mesher-licence.json` (schema 2, empty until a release is recorded) and `SCHEMA.md` |
| `licences/` | `LGPL-2.1.txt`, `OCCT-EXCEPTION.txt`, `NETGEN-LICENSE.txt`, copied unedited from the upstream files |
| `templates/` | the per-archive `THIRD-PARTY-NOTICES` and `README` texts |
| `LGPL-OFFER.txt`, `SOURCES.md` | the written offer of source, and where each source lives |
| `tools/` | `stage_notices.py`, `update_record.py`, `pack_release.py`, `merge_records.py`, `preflight.py` (standard library only, Python 3.12) |
| `../.github/workflows/formcad-preflight.yml` | record checks, including equality with the copies inside the app; it runs only when `formcad/**` or the workflow itself changes, and it checks out `nodemode-dev/formCAD` to compare, which needs read access to that repository |

The build workflow lives in the FormCAD repository (`.github/workflows/binaries.yml`) because it freezes the programs from FormCAD's own source. It builds one archive per triple on native runners (ubuntu-22.04 for a glibc 2.35 floor), signs, records, merges the records, writes `SHA256SUMS`, the source asset and `RELEASE_NOTES.md`, and creates a draft release in `nodemode-dev/formai-binaries`.

## An archive

```
occt-lgpl-<release>-<triple>/
  formcad-kernel[.exe]      the program, first in the record
  _internal/                the frozen Python runtime and the shared libraries, replaceable
  LGPL-2.1.txt  OCCT-EXCEPTION.txt  THIRD-PARTY-NOTICES.txt  LGPL-OFFER.txt  README.md
```

The schema 2 record is published as a release asset beside the archives, not inside them, because the app refuses any file that is not in its shipped record and a file cannot hold its own hash. A release carries: the archives, `occt-licence.json`, `requirements.lock`, `occt-source-<release>.tar.gz`, `SHA256SUMS`, `RELEASE_NOTES.md`, and the CycloneDX file of the program.

## Local check

`release/dry_run` in the FormCAD repository builds the archive, the record and the checksums from `kernel/dist` without signing or fetching anything. See `docs/RELEASE.md` there.

## Check from the repository root

```
python3 formcad/tools/preflight.py --records formcad/records
python3 formcad/tools/preflight.py --records formcad/records --app-data path/to/formCAD/backend/app/data
```

The tools find the licences, templates and records beside themselves, so they work from any directory.

## Licence

The files of this folder are Apache-2.0 (`LICENSE` in this folder, which carries its own copyright line and differs from the `LICENSE` at the repository root). The programs in the releases carry their own licences, listed in `THIRD-PARTY-NOTICES.txt` inside each archive.
