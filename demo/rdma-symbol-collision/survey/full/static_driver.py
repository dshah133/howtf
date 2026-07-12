"""Static per-wheel (and static multi-wheel) analysis.

Extracts one or more wheels, collects every .so (compiled extensions +
auditwheel-vendored .libs/ deps), and runs symsplit modeling python3 dlopening
that whole set. This is the STATIC view:

  * one wheel  -> its own load group (extensions + its .libs): catches
    bundled duplicate copies inside a single distribution.
  * many wheels -> a purely static "union image". This deliberately OVER-models
    (it drops RTLD_LOCAL isolation between wheels); we report it only as an
    upper-bound Tier ladder to contrast against the realistic /proc/maps co-load
    numbers from coload_driver.py.

Usage: python static_driver.py <unit> <out_dir> --wheels a.whl b.whl
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import zipfile

sys.path.insert(0, "/work/demo/rdma-symbol-collision/survey/full")
from survey_lib import analyze_unit                 # noqa: E402

SCRATCH = "/scratch"
PY = "/usr/local/bin/python3"


def is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def extract(whl: str) -> str:
    base = os.path.basename(whl).split("-")[0]
    dest = os.path.join(SCRATCH, "extracted", base)
    if not os.path.isdir(dest):
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(whl) as z:
            z.extractall(dest)
    return dest


def find_sos(root: str) -> list:
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            base = os.path.basename(p)
            if (base.endswith(".so") or ".so." in base) and is_elf(p):
                out.append(p)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("unit")
    ap.add_argument("out_dir")
    ap.add_argument("--wheels", nargs="+", required=True)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    wheels = []
    for w in args.wheels:
        wheels += sorted(glob.glob(w))
    dirs = [extract(w) for w in wheels]
    sos = []
    for d in dirs:
        sos += find_sos(d)
    sos = sorted(set(sos))
    # include the interpreter's own lib dir so libpython (CPython ABI) resolves
    ldlp = sorted({os.path.dirname(s) for s in sos} | {"/usr/local/lib"})

    result = analyze_unit(unit=args.unit, exe=PY, modules=sos,
                          ld_library_path=ldlp, rtld_global=False)
    rec = {
        "unit": args.unit,
        "wheels": [os.path.basename(w) for w in wheels],
        "n_sos": len(sos),
        "so_names": [os.path.basename(s) for s in sos],
        "analysis": result.to_dict(),
    }
    json.dump(rec, open(os.path.join(args.out_dir, args.unit + ".json"), "w"), indent=2)
    a = result.to_dict()
    print("STATIC %s | sos=%d modules_in_image=%d tier0=%d tier1=%d tier2=%d %s"
          % (args.unit, len(sos), a["n_modules"], a["tier0_count"],
             a["tier1_count"], a["tier2_count"], a["verdict_counts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
