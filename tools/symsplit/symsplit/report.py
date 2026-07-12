"""Human table + machine JSON rendering, with optional c++filt demangling."""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import List

from .model import Finding, Verdict


def demangle(names: List[str]) -> dict:
    """Best-effort demangle via c++filt; returns {} if unavailable."""
    cxxfilt = shutil.which("c++filt")
    if not cxxfilt or not names:
        return {}
    try:
        out = subprocess.run(
            [cxxfilt], input="\n".join(names), capture_output=True,
            text=True, timeout=10,
        ).stdout.splitlines()
    except Exception:
        return {}
    m = {}
    for raw, dem in zip(names, out):
        if dem and dem != raw:
            m[raw] = dem
    return m


def enrich_demangled(findings: List[Finding]) -> None:
    dm = demangle([f.symbol for f in findings])
    for f in findings:
        f.demangled = dm.get(f.symbol)


def to_json(findings: List[Finding], modules, exit_split: bool) -> str:
    return json.dumps({
        "modules": [
            {"name": m.name, "path": m.path, "is_exe": m.is_exe,
             "is_dlopened": m.is_dlopened,
             "self_binding": m.selfbind_status()}
            for m in modules
        ],
        "findings": [f.to_dict() for f in findings],
        "summary": {
            "split": sum(1 for f in findings if f.verdict == Verdict.SPLIT),
            "total_findings": len(findings),
            "has_split": exit_split,
        },
    }, indent=2)


_SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "-": 2}


def to_table(findings: List[Finding], modules, show_all: bool) -> str:
    lines = []
    lines.append("modules in image (global-scope order):")
    for m in modules:
        tag = "exe" if m.is_exe else ("dlopen" if m.is_dlopened else "dso")
        lines.append("  [%s] %-28s self-binding=%s" % (tag, m.name, m.selfbind_status()))
    lines.append("")

    shown = [f for f in findings
             if show_all or (f.verdict != Verdict.NO_SPLIT and not f.minor)]
    if not shown:
        lines.append("no duplicate symbols of interest.")
        return "\n".join(lines)

    lines.append("%-9s %-6s %-26s %-6s %s" %
                 ("VERDICT", "SEV", "SYMBOL", "TYPE", "WHY"))
    lines.append("-" * 100)
    for f in shown:
        sym = f.symbol
        if f.demangled:
            sym = f.demangled[:24]
        lines.append("%-9s %-6s %-26s %-6s %s" %
                     (f.verdict, f.severity, sym[:26], f.type, f.why))
        for c in f.copies:
            lines.append("            copy: %-26s %-14s %-9s %-9s ver=%s size=%d sb=%s"
                         % (c.module, c.symtabs, c.bind, c.visibility,
                            c.version, c.size, c.self_bind))
        if f.predicted:
            for r, w in f.predicted.items():
                lines.append("            ref:  %-26s -> %s" % (r, w))
    return "\n".join(lines)
