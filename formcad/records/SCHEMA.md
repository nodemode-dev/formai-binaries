# Record layout (schema 2)

One record per program: `occt-licence.json` for `formcad-kernel`, `mesher-licence.json` for `formcad-mesher`. The files here are empty skeletons; `tools/update_record.py` fills one `targets` entry per platform triple from a staged build, and `tools/pack_release.py` verifies every pin before it writes an archive.

| Key | Meaning |
| --- | --- |
| `schema` | `2` |
| `program` | `formcad-kernel` or `formcad-mesher` |
| `release` | the release number, which is also the tag suffix (`occt-8.0.1`, `occt-8.0.1-2`, `mesher-6.2.2608`) |
| `licence` | SPDX expression of the program's LGPL core |
| `documents` | notice file name to its sha256 (`LGPL-2.1.txt`, `OCCT-EXCEPTION.txt` or `NETGEN-LICENSE.txt`, `THIRD-PARTY-NOTICES.txt`) |
| `source.upstream` | list of `{name, version, licence, url, sha256}`; the sha256 is filled and pinned by `pack --fetch-sources` and a later mismatch stops the pack |
| `source.asset` | name of the source asset on the same release page |
| `source.lock` | `{path, sha256}` of `requirements.lock` |
| `targets[triple]` | one entry per triple below |
| `licence_gates` | name patterns that must not appear (`forbidden_names`) and patterns that need a person's review (`needs_review`) |

Triples: `aarch64-apple-darwin`, `x86_64-apple-darwin`, `x86_64-unknown-linux-gnu`, `x86_64-pc-windows-msvc`.

A `targets[triple]` entry holds `protocol` (engine to worker wire version, 2 for the kernel and 1 for the mesher at this release), `archive` and `folder` (names), `programs` (the executable first), `library_dir` (the folder beside the program that holds the shared libraries), `libraries_found_by`, `minimum_os`, `toolchain`, `verification` (what was run on the built files), `system_libraries_needed` (shared libraries the files import that are not in the archive; Linux and macOS only) and `files`, a list of `{path, sha256, bytes}` with the program first and a `link` field on symbolic links (the hash is of the file the link points to).

## The record inside the app

The app ships a flat schema 1 record (`backend/app/data/kernel-licence.json`, `mesher-licence.json`) with `files: [{triple, path, sha256}]`, which `pack_release.py app-record` derives from this one. It checks that the extracted folder holds exactly the listed files. For that reason the schema 2 record is published as a release asset next to the archives and is not placed inside them.
