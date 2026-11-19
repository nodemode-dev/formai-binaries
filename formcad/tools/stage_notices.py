#!/usr/bin/env python3
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fcbin

HERE = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description="write the notice files into a staged archive folder")
    ap.add_argument("--kind", choices=list(fcbin.KINDS), required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--release", required=True, type=fcbin.release_arg)
    ap.add_argument("--triple", choices=fcbin.TRIPLES, required=True)
    args = ap.parse_args()
    kind = fcbin.KINDS[args.kind]
    stage = Path(args.stage)
    values = {"release": args.release, "triple": args.triple}
    for name in kind["documents"]:
        if name == "THIRD-PARTY-NOTICES.txt":
            template = (HERE / "templates" / f"THIRD-PARTY-NOTICES-{args.kind}.txt").read_text(encoding="utf-8")
            (stage / name).write_text(template.format(**values), encoding="utf-8")
        else:
            shutil.copyfile(HERE / "licences" / name, stage / name)
    readme = (HERE / "templates" / kind["readme"]).read_text(encoding="utf-8")
    (stage / "README.md").write_text(readme.format(**values), encoding="utf-8")
    shutil.copyfile(HERE / "LGPL-OFFER.txt", stage / "LGPL-OFFER.txt")
    print(f"notices written into {stage}")


if __name__ == "__main__":
    main()
