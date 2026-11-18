# formcad-kernel {release} ({triple})

The geometry worker of FormCAD: a frozen Python program with OpenCascade Technology (LGPL-2.1 with the Open CASCADE exception). FormCAD downloads this archive on first use, checks every file against the record it ships, and installs it into its per-user data folder. It never sits inside the signed application.

- `formcad-kernel` starts the worker. It speaks JSON lines on standard input and output.
- The library folder next to it holds the OpenCascade shared libraries and the Python runtime. They are separate files and you may replace them with your own build.
- `THIRD-PARTY-NOTICES.txt` has the licences and the written offer of source. The source is on the release page `occt-{release}` of https://github.com/nodemode-dev/formai-binaries together with `requirements.lock`.
- Check this archive with `SHA256SUMS` from the release page.
