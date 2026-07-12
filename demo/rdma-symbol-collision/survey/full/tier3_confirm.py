"""Tier 3: runtime confirmation of a predicted split via LD_DEBUG.

For a venv + import set + a target symbol, run the import under
LD_DEBUG=bindings,symbols and parse the loader's own binding decisions. glibc
prints, for every resolution:

    <pid>:  binding file <REF> [ns] to <DEF> [ns]: normal symbol `<sym>' [ver]

A runtime-confirmed split = the SAME symbol name is bound, from different
referencing objects, to DIFFERENT defining objects. We report, per target
symbol, the set of (ref -> def) bindings actually taken.

Usage: python tier3_confirm.py <venv_python> <out.json> --imports x y --symbols s1 s2
"""
import argparse
import json
import os
import re
import subprocess
import sys

BIND_RE = re.compile(
    r"binding file\s+(?P<ref>\S+)\s+\[\d+\]\s+to\s+(?P<def>\S+)\s+\[\d+\]:"
    r"\s+normal symbol\s+`(?P<sym>[^']+)'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("venv_python")
    ap.add_argument("out")
    ap.add_argument("--imports", nargs="+", required=True)
    ap.add_argument("--symbols", nargs="+", default=[])
    args = ap.parse_args()

    code = "import importlib\n" + "".join(
        "importlib.import_module(%r)\n" % m for m in args.imports)
    env = {**os.environ, "LD_DEBUG": "bindings,symbols",
           "LD_DEBUG_OUTPUT": "/tmp/lddbg"}
    # LD_DEBUG_OUTPUT appends .<pid>; capture stderr too as a fallback.
    r = subprocess.run([args.venv_python, "-c", code],
                       capture_output=True, text=True, env=env)
    blob = r.stderr
    for fn in os.listdir("/tmp"):
        if fn.startswith("lddbg."):
            try:
                blob += open(os.path.join("/tmp", fn)).read()
            except OSError:
                pass

    want = set(args.symbols)
    per_sym = {}                         # sym -> {def_module -> set(ref_modules)}
    for line in blob.splitlines():
        m = BIND_RE.search(line)
        if not m:
            continue
        sym = m.group("sym")
        if want and sym not in want:
            continue
        d = os.path.basename(m.group("def"))
        ref = os.path.basename(m.group("ref"))
        per_sym.setdefault(sym, {}).setdefault(d, set()).add(ref)

    result = {}
    for sym, defs in per_sym.items():
        result[sym] = {
            "distinct_defs": sorted(defs),
            "split_confirmed": len(defs) >= 2,
            "bindings": {d: sorted(refs) for d, refs in defs.items()},
        }
    out = {
        "imports": args.imports,
        "import_rc": r.returncode,
        "symbols_probed": args.symbols,
        "n_binding_lines": sum(len(v) for v in per_sym.values()),
        "per_symbol": result,
        "any_split_confirmed": any(v["split_confirmed"] for v in result.values()),
    }
    json.dump(out, open(args.out, "w"), indent=2)
    # cleanup
    for fn in os.listdir("/tmp"):
        if fn.startswith("lddbg."):
            try:
                os.remove(os.path.join("/tmp", fn))
            except OSError:
                pass
    print("tier3 imports_rc=%d symbols=%d any_split_confirmed=%s"
          % (r.returncode, len(result), out["any_split_confirmed"]))
    for sym, v in result.items():
        print("  %s: defs=%s split=%s" % (sym, v["distinct_defs"], v["split_confirmed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
