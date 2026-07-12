"""Human table + machine JSON rendering, with optional c++filt demangling."""
from __future__ import annotations

import collections
import json
import shutil
import subprocess
from typing import List

from .cluster import multi_copy_clusters
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
            "scope_partition": sum(1 for f in findings
                                   if f.verdict == Verdict.SCOPE_PARTITION),
            "total_findings": len(findings),
            "has_split": exit_split,
        },
    }, indent=2)


def to_json_by_library(findings: List[Finding], modules, exit_split: bool) -> str:
    clusters, leftover = _cluster_findings(findings, modules)
    return json.dumps({
        "modules": [
            {"name": m.name, "path": m.path, "is_exe": m.is_exe,
             "is_dlopened": m.is_dlopened,
             "self_binding": m.selfbind_status()}
            for m in modules
        ],
        "clusters": [
            {
                "library": c["prefix"],
                "copies": len(c["modules"]),
                "modules": [m.name for m in c["modules"]],
                "shared_symbols": sorted(c["cluster"].shared_symbols),
                "findings": [f.to_dict() for f in c["findings"]],
                "verdicts": dict(collections.Counter(f.verdict for f in c["findings"])),
            }
            for c in clusters
        ],
        "findings": [f.to_dict() for f in leftover],
        "summary": {
            "split": sum(1 for f in findings if f.verdict == Verdict.SPLIT),
            "scope_partition": sum(1 for f in findings
                                   if f.verdict == Verdict.SCOPE_PARTITION),
            "total_findings": len(findings),
            "has_split": exit_split,
        },
    }, indent=2)


_SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "-": 2}


def _module_line(m) -> str:
    tag = "exe" if m.is_exe else ("dlopen" if m.is_dlopened else "dso")
    grp = (" group=%s" % m.group) if m.is_dlopened and not m.rtld_global else ""
    return "  [%s] %-28s self-binding=%s%s" % (
        tag, m.name, m.selfbind_status(), grp)


def to_table(findings: List[Finding], modules, show_all: bool) -> str:
    lines = []
    lines.append("modules in image (global-scope order):")
    for m in modules:
        lines.append(_module_line(m))
    lines.append("")

    shown = [f for f in findings
             if show_all or (f.verdict != Verdict.NO_SPLIT and not f.minor)]
    if not shown:
        lines.append("no duplicate symbols of interest.")
        return "\n".join(lines)

    lines.append("%-16s %-6s %-26s %-6s %s" %
                 ("VERDICT", "SEV", "SYMBOL", "TYPE", "WHY"))
    lines.append("-" * 100)
    for f in shown:
        sym = f.symbol
        if f.demangled:
            sym = f.demangled[:24]
        lines.append("%-16s %-6s %-26s %-6s %s" %
                     (f.verdict, f.severity, sym[:26], f.type, f.why))
        for c in f.copies:
            lines.append("            copy: %-26s %-14s %-9s %-9s ver=%s size=%d sb=%s group=%s"
                         % (c.module, c.symtabs, c.bind, c.visibility,
                            c.version, c.size, c.self_bind, c.scope_group))
        if f.predicted:
            for r, w in f.predicted.items():
                lines.append("            ref:  %-26s -> %s" % (r, w))
    return "\n".join(lines)


# -- clustered (--by-library) rendering --------------------------------------

def _cluster_findings(findings: List[Finding], modules):
    """Bucket findings into same-library clusters (all of a finding's copy
    modules fall inside one multi-copy library cluster) plus a leftover list
    for everything else (cross-library collisions, exe-involving findings,
    single-copy shadow findings, etc -- anything that isn't cleanly "N copies
    of one vendored library")."""
    lib_clusters = multi_copy_clusters(modules)
    name_to_cluster = {}
    for idx, c in enumerate(lib_clusters):
        for m in c.modules:
            name_to_cluster[m.name] = idx

    out = [{"prefix": c.prefix, "modules": c.modules, "cluster": c, "findings": []}
           for c in lib_clusters]
    leftover: List[Finding] = []
    for f in findings:
        copy_names = {c.module for c in f.copies}
        cluster_ids = {name_to_cluster[n] for n in copy_names if n in name_to_cluster}
        if len(cluster_ids) == 1:
            idx = next(iter(cluster_ids))
            cluster_names = {m.name for m in out[idx]["modules"]}
            if copy_names <= cluster_names:
                out[idx]["findings"].append(f)
                continue
        leftover.append(f)
    return out, leftover


def to_table_by_library(findings: List[Finding], modules, show_all: bool) -> str:
    lines = []
    lines.append("modules in image (global-scope order):")
    for m in modules:
        lines.append(_module_line(m))
    lines.append("")

    clusters, leftover = _cluster_findings(findings, modules)

    def _visible(fs):
        return [f for f in fs if show_all or (f.verdict != Verdict.NO_SPLIT and not f.minor)]

    any_shown = False
    lines.append("clustered by same-library fingerprint (soname prefix + "
                 "version-defs + overlapping exports):")
    lines.append("-" * 100)
    for c in clusters:
        fs = _visible(c["findings"])
        if not fs:
            continue
        any_shown = True
        counts = collections.Counter(f.verdict for f in fs)
        worst = min(fs, key=lambda f: _SEV_ORDER.get(f.severity, 9)).severity
        verdict_str = ", ".join("%s=%d" % (v, n) for v, n in
                                sorted(counts.items(), key=lambda kv: -kv[1]))
        lines.append(
            "CLUSTER  library=%s copies=%d shared_symbols=%d sev=%s  %s"
            % (c["prefix"], len(c["modules"]), len(c["cluster"].shared_symbols),
               worst, verdict_str))
        lines.append("  modules: " + ", ".join(sorted(m.name for m in c["modules"])))
        examples = sorted({f.symbol for f in fs})
        shown_examples = examples[:8]
        more = "" if len(examples) <= 8 else " (+%d more)" % (len(examples) - 8)
        lines.append("  symbols (%d total): %s%s"
                     % (len(examples), ", ".join(shown_examples), more))
        lines.append("")

    left_shown = _visible(leftover)
    if left_shown:
        any_shown = True
        lines.append("per-symbol findings outside any multi-copy library cluster:")
        lines.append("%-16s %-6s %-26s %-6s %s" %
                     ("VERDICT", "SEV", "SYMBOL", "TYPE", "WHY"))
        lines.append("-" * 100)
        for f in left_shown:
            sym = f.symbol
            if f.demangled:
                sym = f.demangled[:24]
            lines.append("%-16s %-6s %-26s %-6s %s" %
                         (f.verdict, f.severity, sym[:26], f.type, f.why))

    if not any_shown:
        lines.append("no duplicate symbols of interest.")
    return "\n".join(lines)
