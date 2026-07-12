"""Cluster duplicate-defining modules into "N copies of library L" groups.

When the same vendored library is duplicated many times over (the common
real-world Route B shape -- libgomp bundled inside faiss/scikit-learn/torch
wheels, say), the per-symbol table explodes into thousands of rows that all
say the same thing. This module groups the modules THEMSELVES by a
same-library fingerprint, so the report can collapse "N copies of library L
(M shared symbols)" into one line instead of one row per colliding symbol.

Fingerprint = (soname prefix before any auditwheel-style `-<hash>` suffix,
matching version-def set) plus an overlapping-exported-symbol-set check
within that (prefix, version-def) bucket. Two modules cluster together only
if BOTH the coarse fingerprint matches AND their exported symbol sets
overlap heavily -- this avoids merging two unrelated libraries that happen to
share a soname prefix (e.g. "libfoo.so.1" vs "libfoo.so.2" with a materially
different export surface).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Tuple

from .model import ModuleFacts

# auditwheel / vendoring tools rename sonames like
#   libgomp-a34b3233.so.1  ->  prefix "libgomp.so.1"
#   libstdc++-6f6f2a1f.so.6 -> prefix "libstdc++.so.6"
# by inserting a hex hash right before the trailing ".so[.N...]". Strip it.
_HASH_SUFFIX_RE = re.compile(r"-[0-9a-fA-F]{6,}(?=\.so\b)")

# default overlap threshold for exported-symbol-set clustering (Jaccard).
DEFAULT_OVERLAP = 0.6


def soname_prefix(name: str) -> str:
    """Strip an auditwheel-style hash suffix from a soname/basename."""
    return _HASH_SUFFIX_RE.sub("", name)


@dataclass
class LibraryCluster:
    prefix: str
    modules: List[ModuleFacts] = field(default_factory=list)

    @property
    def shared_symbols(self) -> FrozenSet[str]:
        """Symbols exported by EVERY member -- the "M shared symbols" count."""
        if not self.modules:
            return frozenset()
        shared = set(self.modules[0].exported_symbols)
        for m in self.modules[1:]:
            shared &= m.exported_symbols
        return frozenset(shared)

    @property
    def module_names(self) -> List[str]:
        return [m.name for m in self.modules]


def _jaccard(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    if union == 0:
        return 1.0
    return len(a & b) / union


def cluster_modules(modules: List[ModuleFacts],
                    overlap: float = DEFAULT_OVERLAP) -> List[LibraryCluster]:
    """Group modules into same-library clusters by (soname-prefix,
    version-def set) plus an exported-symbol overlap check. Order-preserving;
    modules that don't share a prefix+version-def bucket with anything never
    merge. A module with no exported dynamic symbols at all (e.g. the main
    executable) never clusters."""
    buckets: Dict[Tuple[str, FrozenSet[str]], List[LibraryCluster]] = {}
    order: List[LibraryCluster] = []
    for m in modules:
        if not m.exported_symbols:
            continue
        key = (soname_prefix(m.soname or m.name), m.version_defs)
        candidates = buckets.setdefault(key, [])
        placed = None
        for c in candidates:
            rep = c.modules[0]
            if _jaccard(rep.exported_symbols, m.exported_symbols) >= overlap:
                placed = c
                break
        if placed is None:
            placed = LibraryCluster(prefix=key[0])
            candidates.append(placed)
            order.append(placed)
        placed.modules.append(m)
    return order


def multi_copy_clusters(modules: List[ModuleFacts],
                        overlap: float = DEFAULT_OVERLAP) -> List[LibraryCluster]:
    """Only clusters with >= 2 members -- i.e. a library that is genuinely
    duplicated in this image."""
    return [c for c in cluster_modules(modules, overlap) if len(c.modules) >= 2]
