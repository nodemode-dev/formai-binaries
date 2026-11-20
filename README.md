# formai-binaries

The programs the Form apps depend on and do not carry in their installers,
published as release archives for every app of the family to download and
verify when it needs them. Today it holds two sets:

- **ffmpeg**, the LGPL builds the media apps use, one archive per platform.
  The rest of this page describes them.
- **FormCAD's 3D workers**, the two LGPL programs behind its geometry kernel
  and its mesher. They have their own folder and page: [`formcad/`](formcad/README.md).

A dependency another Form app needs later gets its own folder and its own
release tags here in the same way.

## ffmpeg

An app does not ship ffmpeg inside its installer: on first run, when
no ffmpeg is found, it downloads the archive for its machine from a release
here and checks every file against the SHA-256 recorded in
[`ffmpeg-licence.json`](ffmpeg-licence.json), a copy of which each app carries. If
one file differs, nothing is installed.

This repository was called `formcut-binaries` before, because FormCut was the
first app to use it. The old address, `https://github.com/nodemode-dev/formcut-binaries`,
keeps working, and builds made before the rename download from it, so release
tags, file names and archive layout have not changed and never will for a
published release. The apps that use these builds today are FormCut, FormDAM,
FormEncoder, FormLit, FormTools, FormVFX, FormVis and FormWav.

This repository holds text only. The binaries live on the
[releases](https://github.com/nodemode-dev/formai-binaries/releases) page, never
in the git history.

## What a release holds

A release is tagged `ffmpeg-<release>`: the FFmpeg version, as in `ffmpeg-7.1.5`, or that version with a rebuild number when the same FFmpeg is built again, as in `ffmpeg-7.1.5-2`, so a published release is never replaced. The record's `release` field holds the name, and `update_record.py` writes it with each build's files. A release carries:

| Asset | What it is |
| --- | --- |
| `ffmpeg-lgpl-7.1.5-aarch64-apple-darwin.tar.gz` | macOS, Apple silicon |
| `ffmpeg-lgpl-7.1.5-x86_64-apple-darwin.tar.gz` | macOS, Intel |
| `ffmpeg-lgpl-7.1.5-x86_64-unknown-linux-gnu.tar.gz` | Linux, x86_64 |
| `ffmpeg-lgpl-7.1.5-x86_64-pc-windows-msvc.tar.gz` | Windows, x86_64 |
| `ffmpeg-7.1.5.tar.xz`, `libopus-1.6.1.tar.gz`, `libvpx-1.17.0.tar.gz` | the source these were built from |
| `SHA256SUMS` | the SHA-256 of every asset |

Each archive holds one folder of the same name:

```
ffmpeg-lgpl-7.1.5-<triple>/
  ffmpeg-<triple>          (ffmpeg-<triple>.exe on Windows)
  ffprobe-<triple>
  lib-<triple>/            the seven FFmpeg libraries, found through a relative path
  LGPL-2.1.txt
  THIRD-PARTY-NOTICES.txt
  ffmpeg-licence.json
```

An app builds the download address as
`https://github.com/nodemode-dev/formai-binaries/releases/download/ffmpeg-<version>/ffmpeg-lgpl-<version>-<triple>.tar.gz`,
and tries the same path under `nodemode-dev/formcut-binaries` if that fails (builds
made before the rename try only that one), so the tag and the file names must be
exactly these. `FORMCUT_FFMPEG_RELEASE` points an app at a mirror holding the
same files, and is then the only source. On disk the apps keep the install folder
`formcut-binaries/ffmpeg-<release>`, so a build that is already installed is found.

## Other apps

This repository is being renamed formai-binaries, so that every form app pulls
its binaries from one place. formCAD's worker releases, tagged `occt-<release>`
and `mesher-<release>`, and their records live under `formcad/`; see
[`formcad/README.md`](formcad/README.md). They never touch the `ffmpeg-<release>`
releases above.

## Licences

ffmpeg is built with `--disable-gpl --disable-nonfree`, so it is
**LGPL-2.1-or-later** ([`LGPL-2.1.txt`](LGPL-2.1.txt)); libopus and libvpx,
built into libavcodec, are **BSD-3-Clause**
([`THIRD-PARTY-NOTICES.txt`](THIRD-PARTY-NOTICES.txt)). The libraries are shared
and found through a relative path, so they can be replaced, as the LGPL
requires. Each app runs ffmpeg as a separate program, which is why an app
that is Apache-2.0 stays so. The exact source is attached to each release, and the
record names every upstream download, its SHA-256 and the configure line of
each target.

The scripts and documents of this repository are Apache-2.0 ([`LICENSE`](LICENSE)).

## Publishing a build

1. Put the builds in one folder the way FormCut's `src-tauri/binaries` holds
   them: `ffmpeg-<triple>`, `ffprobe-<triple>` and `lib-<triple>/` per target.
2. Copy FormCut's `src-tauri/binaries/ffmpeg-licence.json` here if it changed.
   It must be the record the apps ship, since that is what the fetch checks.
3. Pack and check:

   ```
   python pack_release.py --binaries path/to/binaries --sources
   ```

   Every recorded file must match its SHA-256 or nothing is packed. The
   archives are reproducible, so packing the same files again gives the same
   hashes. The results go to `dist/`, which git ignores.
4. Publish, as a release rather than a draft, since the apps download
   anonymously:

   ```
   gh release create ffmpeg-7.1.5 dist/* --title "ffmpeg 7.1.5 (LGPL) for the Form apps" --notes-file dist/RELEASE_NOTES.md
   ```

   or on github.com: Releases, Draft a new release, tag `ffmpeg-7.1.5`, attach
   everything in `dist/`, Publish.

Never replace an asset under a tag that has been published. The apps pin every
file by hash, so a changed build goes out as a new record in the apps and a new
tag here.

## Checking a download

```
sha256sum -c SHA256SUMS              # Linux
shasum -a 256 -c SHA256SUMS          # macOS
```
