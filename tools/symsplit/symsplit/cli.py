"""symsplit command-line entry point."""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List

from . import __version__
from .allowlist import Allowlist
from .analyze import Analyzer
from .closure import resolve_closure
from .model import Verdict
from .report import (
    enrich_demangled,
    to_json,
    to_json_by_library,
    to_table,
    to_table_by_library,
)


def _parse_module_groups(specs: List[str]) -> Dict[str, str]:
    """Parse repeated --module-group NAME:mod1,mod2,... into a flat
    {module-basename-or-soname: group-name} map."""
    out: Dict[str, str] = {}
    for spec in specs:
        if ":" not in spec:
            raise SystemExit(
                "--module-group must be NAME:mod1,mod2,... (got %r)" % spec)
        name, mods = spec.split(":", 1)
        name = name.strip()
        for mod in mods.split(","):
            mod = mod.strip()
            if mod:
                out[mod] = name
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="symsplit",
        description="Split-state linking scanner: flags when two live copies "
                    "of one strong symbol resolve to DIFFERENT definitions "
                    "across the static/dynamic boundary -- Route A (SPLIT, "
                    "interposition capture) or Route B (SCOPE-PARTITION, "
                    "scope partition). Not nm|uniq -d.",
    )
    p.add_argument("executable", help="path to the ELF executable to analyze")
    p.add_argument("--ld-library-path", default="",
                   help="colon-separated search dirs (like LD_LIBRARY_PATH)")
    p.add_argument("--module", action="append", default=[],
                   help="extra dlopen-style module (.so). Repeatable. "
                        "RTLD_LOCAL by default; each gets its own isolated "
                        "local-scope group unless assigned one with "
                        "--module-group.")
    p.add_argument("--module-group", action="append", default=[],
                   metavar="NAME:mod1,mod2,...",
                   help="declare that these --module modules (matched by "
                        "basename or soname) share ONE dlopen namespace "
                        "(e.g. a consumer .so and its own privately-bundled "
                        "dependency). Repeatable. Modules not named in any "
                        "--module-group default to their own singleton "
                        "group (own local scope) -- see README 'dlopen "
                        "scope modeling'.")
    p.add_argument("--rtld-global", action="store_true",
                   help="model --module loads as RTLD_GLOBAL")
    p.add_argument("--assume-rtld-local", action="store_true",
                   help="also isolate ordinarily-linked (DT_NEEDED) DSOs "
                        "into their own private scope group each, for "
                        "callers who know -- from outside the ELF -- that a "
                        "plain-looking closure is actually assembled from "
                        "separately dlopen'd RTLD_LOCAL pieces. Enables "
                        "Route B (SCOPE-PARTITION) detection on modules "
                        "that would otherwise default to the shared global "
                        "scope assumption.")
    p.add_argument("--allowlist", default=None,
                   help="path to an alternate interposer allowlist")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--all", action="store_true",
                   help="also show benign NO-SPLIT dups in the table")
    p.add_argument("--by-library", action="store_true",
                   help="cluster duplicate-defining modules by same-library "
                        "fingerprint (soname prefix + version-defs + "
                        "overlapping exports) and report 'N copies of "
                        "library L (M shared symbols)' as one finding "
                        "instead of one row per colliding symbol.")
    p.add_argument("--version", action="version",
                   version="symsplit " + __version__)
    return p


def run(argv: List[str]) -> int:
    args = build_parser().parse_args(argv)
    ldlp = [d for d in args.ld_library_path.split(":") if d]
    allow = Allowlist.load(args.allowlist)
    module_groups = _parse_module_groups(args.module_group)

    closure = resolve_closure(
        args.executable, ld_library_path=ldlp,
        extra_modules=args.module, rtld_global=args.rtld_global,
        module_groups=module_groups,
    )
    findings = Analyzer(closure, allow,
                        assume_rtld_local=args.assume_rtld_local).run()
    enrich_demangled(findings)

    has_split = any(f.verdict in Verdict.SPLIT_VERDICTS for f in findings)
    if args.json:
        if args.by_library:
            print(to_json_by_library(findings, closure.modules, has_split))
        else:
            print(to_json(findings, closure.modules, has_split))
    else:
        if args.by_library:
            print(to_table_by_library(findings, closure.modules, args.all))
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
