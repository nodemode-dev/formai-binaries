# Sources

Where the source of every LGPL part of a binary release lives. The pin of each upstream archive (its SHA-256) is written into the record's `source.upstream` list by `tools/pack_release.py pack --fetch-sources`, and the same archives are packed into the `*-source-<release>.tar.gz` asset on the release page, next to the binaries.

## formcad-kernel (tag `occt-<release>`)

| Part | Licence | Source | Notes |
| --- | --- | --- | --- |
| OpenCascade Technology 8.0.1 | LGPL-2.1-only with the Open CASCADE exception 1.0 | `https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V8_0_1.tar.gz` | built by the OCP project into the `cadquery-ocp-novtk` wheel; UNVERIFIED that the wheel is built from this tag without patches, check against the OCP build recipe before the first publication and record any patch |
| cadquery-ocp-novtk 8.0.1.1.0, build123d 0.13.0 | Apache-2.0 | PyPI sdist and wheel pages pinned in `requirements.lock` | not LGPL, listed for completeness |
| libquadmath (inside numpy.libs and scipy.libs) | LGPL-2.1 | GCC source of the version named in the wheel | separate shared file in `_internal` |
| libraw, libfreeimage, libjasper (inside the OpenCascade wheel libs) | LGPL-2.1 or CDDL (LibRaw), GPL-2.0 or GPL-3.0 or FIPL (FreeImage), JasPer 2.0 | upstream releases of the versions the wheel names | flagged in the record's `licence_gates.needs_review`; the owner decides before publication whether these stay, see `docs/RELEASE.md` |
| formcad-kernel worker | Apache-2.0 | `kernel/` of the FormCAD repository at the release commit, packed into the source asset under `worker/` | |

## formcad-mesher (tag `mesher-<release>`)

| Part | Licence | Source | Notes |
| --- | --- | --- | --- |
| Netgen 6.2.2608 | LGPL-2.1-only | `https://github.com/NGSolve/netgen/archive/refs/tags/v6.2.2608.tar.gz` | wheel `netgen-mesher`; the freeze script excludes the GUI modules |
| OpenCascade Technology 7.8.1 (build inside `netgen-occt`) | LGPL-2.1-only with the Open CASCADE exception 1.0 | `https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V7_8_1.tar.gz` | wheel `netgen-occt` ships `LICENSE_LGPL_21.txt` |
| formcad-mesher worker | Apache-2.0 | `mesher/` of the FormCAD repository at the release commit | |

## Written offer

`LGPL-OFFER.txt` is the written offer. It is copied into each archive as part of `THIRD-PARTY-NOTICES.txt`, into the archive's `README.md`, and into the About dialog text of the app, and every copy names the release page that hosts the binary and the source.

## Retention

The source asset of a release stays on that release for at least three years after the last distribution of the binary built from it. No published tag is replaced; a rebuild gets a new release number (`8.0.1-2`).
