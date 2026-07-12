#!/usr/bin/env python3
"""System-binary sweep: run symsplit over stock binaries and tally verdicts.

Every SPLIT is printed for adjudication (see NOTES-system-sweep.md). Intended
to run in a Linux/ELF environment (the container harness).
"""
import argparse
import collections
import glob
import json
import os
import subprocess
import sys


def is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*",
                    default=["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    bins = []
    for d in args.dirs:
        for p in glob.glob(os.path.join(d, "*")):
            if os.path.isfile(p) and os.access(p, os.X_OK) and is_elf(p):
                bins.append(p)

    verd = collections.Counter()
    scanned = with_dup = splits = errors = 0
    split_rows = []
    for b in sorted(set(bins)):
        try:
            out = subprocess.run(
                [sys.executable, "-m", "symsplit", b, "--json"],
                capture_output=True, text=True, timeout=args.timeout)
            d = json.loads(out.stdout)
        except Exception:
            errors += 1
            continue
        scanned += 1
        if d["findings"]:
            with_dup += 1
        for f in d["findings"]:
            verd[f["verdict"]] += 1
            if f["verdict"] == "SPLIT":
                splits += 1
                split_rows.append((b, f["symbol"], f["severity"], f["why"]))

    print("scanned: %d | with >=1 dup: %d | errors: %d" % (scanned, with_dup, errors))
    print("verdict distribution:")
    for k, v in verd.most_common():
        print("  %-20s %d" % (k, v))
    print("SPLIT flags to adjudicate: %d" % splits)
    for b, sym, sev, why in split_rows:
        print("  [SPLIT %s] %s :: %s -- %s" % (sev, b, sym, why))
    return 1 if splits else 0


if __name__ == "__main__":
    sys.exit(main())
