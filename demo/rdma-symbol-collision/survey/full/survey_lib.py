"""Shared analysis core for the split-state ecosystem survey.

Wraps symsplit's binding simulator and computes the honest metric ladder for a
single co-load unit (an executable + a set of dlopen'd module .so's):

  Tier 0  raw duplicate STRONG (GLOBAL) DEFAULT-visibility symbols: >=2
          interposable copies across distinct modules in the image.
  Tier 1  Tier-0 symbols where >=1 copy lives in a self-binding (non-exe) DSO
          (self_bind in {confirmed, self-bound-or-unreferenced}).
  Tier 2  symsplit verdict == SPLIT under the modeled dlopen scope.
  Tier 3  runtime-confirmed (computed elsewhere via LD_DEBUG).

Everything is derived from symsplit's own Finding/Copy objects so the ladder is
a strict refinement of the tool's verdict, never a re-implementation.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, "/work/tools/symsplit")

from symsplit.allowlist import Allowlist            # noqa: E402
from symsplit.analyze import Analyzer               # noqa: E402
from symsplit.closure import resolve_closure        # noqa: E402
from symsplit.model import SelfBind, Verdict        # noqa: E402

SELFBOUND = {SelfBind.CONFIRMED, SelfBind.PROBABLE}


def _is_strong_default(c) -> bool:
    return (c.bind == "GLOBAL"
            and c.visibility in ("DEFAULT", "PROTECTED")
            and c.symtabs.startswith(".dynsym"))


@dataclass
class UnitResult:
    unit: str
    exe: str
    n_modules: int
    module_names: List[str]
    # tier symbol lists (names)
    tier0: List[str] = field(default_factory=list)
    tier1: List[str] = field(default_factory=list)
    tier2: List[dict] = field(default_factory=list)   # SPLIT findings (rich)
    verdict_counts: Dict[str, int] = field(default_factory=dict)
    # every dangerous-family symbol that reaches tier0, keyed by family
    families_tier0: Dict[str, List[str]] = field(default_factory=dict)
    families_tier2: Dict[str, List[str]] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "unit": self.unit,
            "exe": self.exe,
            "n_modules": self.n_modules,
            "module_names": self.module_names,
            "tier0_count": len(self.tier0),
            "tier1_count": len(self.tier1),
            "tier2_count": len(self.tier2),
            "tier0_symbols": self.tier0,
            "tier1_symbols": self.tier1,
            "tier2_splits": self.tier2,
            "verdict_counts": self.verdict_counts,
            "families_tier0": self.families_tier0,
            "families_tier2": self.families_tier2,
            "missing_needed": self.missing,
        }


# dangerous families from the pilot: match by symbol-name prefix/pattern.
_OMP_PREFIXES = ("GOMP_", "omp_", "GOACC_", "gomp", "kmp_", "__kmp", "_kmp",
                 "__omp", "ompc_")
_BLAS_PREFIXES = ("cblas_", "LAPACKE_", "lapack_", "openblas_", "goto_",
                  "blas_", "cblas", "catlas_")


def _family(name: str) -> Optional[str]:
    n = name
    if n.startswith(_OMP_PREFIXES) or n == "ompt_start_tool":
        return "OpenMP"
    if n.startswith(_BLAS_PREFIXES) or "gemm" in n.lower() or "gemv" in n.lower():
        return "BLAS/LAPACK"
    if n.startswith(("_gfortran", "GFORTRAN")):
        return "Fortran-RT"
    if n.startswith(("__cxa_", "_ZSt", "_ZNSt", "_ZN9__gnu_cxx", "_ZNKSt",
                     "__gnu_cxx")):
        return "libstdc++"
    if n.startswith(("PyInit_", "_Py", "Py")):
        return "CPython-ABI"
    return None


def analyze_unit(unit: str, exe: str, modules: List[str],
                 ld_library_path: List[str], rtld_global: bool = False,
                 allowlist: Optional[str] = None) -> UnitResult:
    closure = resolve_closure(exe, ld_library_path=ld_library_path,
                              extra_modules=modules, rtld_global=rtld_global)
    findings = Analyzer(closure, Allowlist.load(allowlist)).run()

    res = UnitResult(
        unit=unit, exe=os.path.basename(exe),
        n_modules=len(closure.modules),
        module_names=[m.name for m in closure.modules],
        missing=sorted(set(closure.missing)),
    )
    for f in findings:
        res.verdict_counts[f.verdict] = res.verdict_counts.get(f.verdict, 0) + 1
        # a finding reaching _classify has >=2 interposable copies; Tier 0 needs
        # >=2 that are STRONG + DEFAULT specifically.
        strong = [c for c in f.copies if _is_strong_default(c)]
        strong_mods = {c.module for c in strong}
        is_tier0 = len(strong_mods) >= 2
        if not is_tier0:
            continue
        res.tier0.append(f.symbol)
        fam = _family(f.symbol)
        if fam:
            res.families_tier0.setdefault(fam, []).append(f.symbol)
        # Tier 1: >=1 strong copy in a self-binding non-exe DSO
        selfbound = any(c.self_bind in SELFBOUND for c in strong)
        if selfbound:
            res.tier1.append(f.symbol)
        # Tier 2: symsplit SPLIT verdict
        if f.verdict == Verdict.SPLIT:
            entry = {
                "symbol": f.symbol,
                "demangled": f.demangled,
                "severity": f.severity,
                "why": f.why,
                "copies": [{"module": c.module, "bind": c.bind,
                            "visibility": c.visibility, "type": c.type,
                            "size": c.size, "self_bind": c.self_bind,
                            "symtabs": c.symtabs, "version": c.version}
                           for c in f.copies],
                "predicted_binding": f.predicted,
                "family": fam,
            }
            res.tier2.append(entry)
            if fam:
                res.families_tier2.setdefault(fam, []).append(f.symbol)
    return res
