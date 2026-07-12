"""Co-load driver: build a REAL process image and run symsplit on it.

For a co-load unit (a set of pip packages + the modules to import):
  1. create a venv, pip install the packages (record resolved versions),
  2. import the modules and capture the REAL loaded .so set from /proc/self/maps,
  3. feed that captured set to symsplit's --module mode (exe = the venv python),
  4. compute the Tier 0/1/2 ladder.

The dlopen'd extensions (everything under site-packages) are modeled RTLD_LOCAL
(symsplit's default for --module); libc / libpython / system libstdc++ enter the
image through the python executable's own DT_NEEDED closure. ld_library_path is
seeded with every directory that actually held a loaded .so, so vendored
(.libs) deps resolve to the exact bundled copies that were mapped at runtime.

Usage: python coload_driver.py <unit_name> <out_dir> --pkgs a b --imports x y
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, "/work/demo/rdma-symbol-collision/survey/full")
from survey_lib import analyze_unit                 # noqa: E402

SCRATCH = "/scratch"
HERE = "/work/demo/rdma-symbol-collision/survey/full"


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("unit")
    ap.add_argument("out_dir")
    ap.add_argument("--pkgs", nargs="+", required=True)
    ap.add_argument("--imports", nargs="+", required=True)
    ap.add_argument("--reuse-venv", default=None,
                    help="path to an existing venv to reuse (skip install)")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    venv = args.reuse_venv or os.path.join(SCRATCH, "venvs", args.unit)
    vpy = os.path.join(venv, "bin", "python")
    rec = {"unit": args.unit, "pkgs_requested": args.pkgs,
           "imports_requested": args.imports}

    if not args.reuse_venv:
        r = sh([sys.executable, "-m", "venv", venv])
        if r.returncode != 0:
            rec["error"] = "venv create failed: " + r.stderr[-500:]
            json.dump(rec, open(os.path.join(args.out_dir, args.unit + ".json"), "w"), indent=2)
            print("VENV-FAIL", r.stderr[-300:]); return 1
        r = sh([vpy, "-m", "pip", "install", "-q", "--no-input"] + args.pkgs,
               env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
        rec["pip_install_rc"] = r.returncode
        if r.returncode != 0:
            rec["pip_error"] = (r.stdout + r.stderr)[-1500:]
            json.dump(rec, open(os.path.join(args.out_dir, args.unit + ".json"), "w"), indent=2)
            print("PIP-FAIL", (r.stdout + r.stderr)[-400:]); return 1

    # resolved versions
    fr = sh([vpy, "-m", "pip", "freeze"])
    rec["pip_freeze"] = [ln for ln in fr.stdout.splitlines() if ln.strip()]

    # capture the real loaded .so set
    maps_out = os.path.join(args.out_dir, args.unit + ".maps.json")
    cap = sh([vpy, os.path.join(HERE, "capture_maps.py"), maps_out] + args.imports)
    rec["capture_stdout"] = cap.stdout.strip()
    rec["capture_stderr"] = cap.stderr.strip()[-800:]
    if not os.path.isfile(maps_out):
        rec["error"] = "capture failed"
        json.dump(rec, open(os.path.join(args.out_dir, args.unit + ".json"), "w"), indent=2)
        print("CAPTURE-FAIL", cap.stderr[-400:]); return 1
    maps = json.load(open(maps_out))
    rec["imported"] = maps["imported"]
    rec["import_failed"] = maps["failed"]
    rec["py_exe"] = maps["py_exe"]

    sos = maps["sos"]
    site_marker = "/site-packages/"
    site_sos = [s for s in sos if site_marker in s]
    sys_sos = [s for s in sos if site_marker not in s]
    rec["n_loaded_sos"] = len(sos)
    rec["n_site_sos"] = len(site_sos)
    rec["n_sys_sos"] = len(sys_sos)
    rec["site_sos"] = [os.path.basename(s) for s in site_sos]
    rec["sys_sos"] = [os.path.basename(s) for s in sys_sos]

    # ld_library_path = every dir that actually held a loaded .so
    ldlp = sorted({os.path.dirname(s) for s in sos})

    # symsplit: exe = venv python (real interpreter), modules = the dlopen'd
    # site-packages extensions + their bundled libs.
    result = analyze_unit(
        unit=args.unit, exe=maps["py_exe"], modules=site_sos,
        ld_library_path=ldlp, rtld_global=False,
    )
    rec["analysis"] = result.to_dict()
    rec["ld_library_path"] = ldlp

    json.dump(rec, open(os.path.join(args.out_dir, args.unit + ".json"), "w"), indent=2)
    a = result.to_dict()
    print("UNIT %s | modules_in_image=%d tier0=%d tier1=%d tier2=%d verdicts=%s"
          % (args.unit, a["n_modules"], a["tier0_count"], a["tier1_count"],
             a["tier2_count"], a["verdict_counts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
