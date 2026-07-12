"""symsplit command-line entry point."""
from __future__ import annotations

import argparse
import sys
from typing import List

from . import __version__
from .allowlist import Allowlist
from .analyze import Analyzer
from .closure import resolve_closure
from .model import Verdict
from .report import enrich_demangled, to_json, to_table


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="symsplit",
        description="Split-state linking scanner: flags when two live copies "
                    "of one strong symbol resolve to DIFFERENT definitions "
                    "across the static/dynamic boundary. Not nm|uniq -d.",
    )
    p.add_argument("executable", help="path to the ELF executable to analyze")
    p.add_argument("--ld-library-path", default="",
                   help="colon-separated search dirs (like LD_LIBRARY_PATH)")
    p.add_argument("--module", action="append", default=[],
                   help="extra dlopen-style module (.so). Repeatable. "
                        "RTLD_LOCAL by default.")
    p.add_argument("--rtld-global", action="store_true",
                   help="model --module loads as RTLD_GLOBAL")
    p.add_argument("--allowlist", default=None,
                   help="path to an alternate interposer allowlist")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--all", action="store_true",
                   help="also show benign NO-SPLIT dups in the table")
    p.add_argument("--version", action="version",
                   version="symsplit " + __version__)
    return p


def run(argv: List[str]) -> int:
    args = build_parser().parse_args(argv)
    ldlp = [d for d in args.ld_library_path.split(":") if d]
    allow = Allowlist.load(args.allowlist)

    closure = resolve_closure(
        args.executable, ld_library_path=ldlp,
        extra_modules=args.module, rtld_global=args.rtld_global,
    )
    findings = Analyzer(closure, allow).run()
    enrich_demangled(findings)

    has_split = any(f.verdict == Verdict.SPLIT for f in findings)
    if args.json:
        print(to_json(findings, closure.modules, has_split))
    else:
        print(to_table(findings, closure.modules, args.all))
        if closure.missing:
            print("\nWARNING: unresolved DT_NEEDED (analysis may be partial): "
                  + ", ".join(sorted(set(closure.missing))), file=sys.stderr)
    return 2 if has_split else 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
