"""ROUTE B — scope-partition confirmation by direct /proc/maps copy-counting.

Route B is the split-state disease reached WITHOUT interposition or self-binding:
RTLD_LOCAL isolation + auditwheel vendoring means two wheels each map their OWN
private copy of a shared runtime (libgomp, libgfortran, libquadmath, ...). The
ground-truth confirmation is not a symsplit verdict (its VERSIONED-BENIGN filter
over-clears same-library dupes) but the *mapped copy count*: how many distinct
files of one library are actually resident in a single process.

For a captured maps set (or a live import), we:
  1. cluster every mapped .so by LIBRARY IDENTITY = soname with the auditwheel
     content-hash stripped and the .so version suffix dropped
     (libgomp-a34b3233.so.1 -> "libgomp");
  2. count distinct mapped FILES per identity, with source wheel + DT_SONAME +
     content sha256 (are they byte-identical builds or genuinely different?);
  3. flag every identity with >=2 mapped copies -> a live Route-B instance.

Usage: python routeB_copycount.py <maps.json> [<maps.json> ...]
   or:  python routeB_copycount.py --live <mod1> <mod2> ...   (import + capture)
"""
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, "/work/tools/symsplit")
from elftools.elf.elffile import ELFFile          # noqa: E402
from elftools.elf.dynamic import DynamicSection    # noqa: E402

HASH_RE = re.compile(r"-[0-9a-f]{6,}")

# runtimes worth calling out (shared mutable state / thread pools / allocators)
RUNTIME_HINTS = ("libgomp", "libomp", "libiomp", "libgfortran", "libquadmath",
                 "libopenblas", "libblas", "liblapack", "libmkl", "libstdc++",
                 "libgcc", "libarrow", "libtbb", "libcrypto", "libssl")


def identity(basename: str) -> str:
    # drop everything from the first '.so' on, then strip auditwheel hashes
    stem = basename.split(".so")[0]
    stem = HASH_RE.sub("", stem)
    return stem


def soname_of(path: str) -> str:
    try:
        with open(path, "rb") as f:
            elf = ELFFile(f)
            dyn = elf.get_section_by_name(".dynamic")
            if isinstance(dyn, DynamicSection):
                for t in dyn.iter_tags():
                    if t.entry.d_tag == "DT_SONAME":
                        return t.soname
    except Exception:
        pass
    return "-"


def sha(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except OSError:
        return "?"


def wheel_of(path: str) -> str:
    m = re.search(r"site-packages/([^/]+)", path)
    return m.group(1) if m else ("system" if path.startswith(("/usr", "/lib")) else "?")


def capture_live(mods):
    code = ("import importlib,os,json,sys\n"
            "for m in %r:\n"
            "    try: importlib.import_module(m)\n"
            "    except Exception as e: sys.stderr.write('import-fail %%s: %%s\\n'%%(m,e))\n"
            "sos=set()\n"
            "for ln in open('/proc/self/maps'):\n"
            "    p=ln.split()[-1]\n"
            "    if p.startswith('/') and ('.so' in os.path.basename(p)) and os.path.isfile(p):\n"
            "        sos.add(os.path.realpath(p))\n"
            "json.dump({'sos':sorted(sos)}, open('/tmp/live_maps.json','w'))\n"
            % mods)
    subprocess.run(["/scratch/venvs/mega/bin/python", "-c", code], check=False)
    return json.load(open("/tmp/live_maps.json"))


def analyze(sos, label):
    clusters = {}
    for p in sos:
        base = os.path.basename(p)
        key = identity(base)
        clusters.setdefault(key, []).append(p)
    out = {"label": label, "n_sos": len(sos), "libraries": {}}
    multi = {}
    for key, paths in sorted(clusters.items()):
        uniq = sorted(set(paths))
        copies = [{"path": p, "wheel": wheel_of(p), "soname": soname_of(p),
                   "sha12": sha(p), "size": os.path.getsize(p) if os.path.isfile(p) else 0}
                  for p in uniq]
        rec = {"n_copies": len(uniq), "copies": copies}
        out["libraries"][key] = rec
        if len(uniq) >= 2:
            multi[key] = rec
    out["multi_copy_libraries"] = multi
    return out, multi


def main():
    args = sys.argv[1:]
    results = []
    if args and args[0] == "--live":
        mods = args[1:]
        data = capture_live(mods)
        res, multi = analyze(data["sos"], "live:" + ",".join(mods))
        results.append(res)
    else:
        for mp in args:
            data = json.load(open(mp))
            res, multi = analyze(data["sos"], os.path.basename(mp))
            results.append(res)

    for res in results:
        print("\n=== %s  (%d mapped .so) ===" % (res["label"], res["n_sos"]))
        mc = res["multi_copy_libraries"]
        if not mc:
            print("  no multi-copy runtime libraries")
        for key, rec in sorted(mc.items(), key=lambda kv: -kv[1]["n_copies"]):
            hot = "  <-- RUNTIME" if any(h in key for h in RUNTIME_HINTS) else ""
            shas = {c["sha12"] for c in rec["copies"]}
            ident = "IDENTICAL-build" if len(shas) == 1 else "%d-distinct-builds" % len(shas)
            print("  %-22s x%d  [%s]%s" % (key, rec["n_copies"], ident, hot))
            for c in rec["copies"]:
                print("       %-9s soname=%-30s sha=%s  %s"
                      % (c["wheel"], c["soname"], c["sha12"], c["path"]))

    outp = "/work/demo/rdma-symbol-collision/survey/full/results/routeB_copycount.json"
    # merge into a list file
    existing = []
    if os.path.isfile(outp):
        try:
            existing = json.load(open(outp))
        except Exception:
            existing = []
    labels = {r["label"] for r in results}
    existing = [e for e in existing if e["label"] not in labels] + results
    json.dump(existing, open(outp, "w"), indent=2)
    print("\n[written %s : %d unit(s)]" % (outp, len(existing)))


if __name__ == "__main__":
    main()
