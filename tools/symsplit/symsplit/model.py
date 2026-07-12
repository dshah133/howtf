"""Data model shared across the analyzer.

These are plain dataclasses -- deliberately close to the raw ELF facts so the
verdict logic in ``analyze.py`` reads as a direct transcription of the
predicate in the README, not as magic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


# ---- verdict / severity / confidence vocab --------------------------------

class Verdict:
    # Route A (interposition capture): duplicate strong symbol + a
    # self-binding DSO (-Bsymbolic-functions) in a SHARED scope, so one
    # external reader lands on a different copy than the DSO's own calls.
    SPLIT = "SPLIT"
    # Route B (scope partition): the same strong default-vis symbol defined
    # in >=2 modules loaded into SEPARATE local (RTLD_LOCAL) namespaces, each
    # resolving to its own copy. No self-binding needed. See README.
    SCOPE_PARTITION = "SCOPE-PARTITION"
    WEAK_PATTERN = "WEAK-PATTERN"         # global+weak intentional override
    VERSIONED_BENIGN = "VERSIONED-BENIGN"
    HIDDEN_BENIGN = "HIDDEN-BENIGN"
    NOT_DYNAMIC_BENIGN = "NOT-DYNAMIC-BENIGN"
    ALLOWLISTED = "ALLOWLISTED"
    NO_SPLIT = "NO-SPLIT"                 # dup exists but unifies (e.g. config A)

    # Both routes are real split-state bugs (two live copies of one strong
    # symbol's state that a running process can observe diverging). Every
    # other verdict is benign/informational. Used for the CLI exit code and
    # JSON summary counts.
    SPLIT_VERDICTS = frozenset({SPLIT, SCOPE_PARTITION})


class Severity:
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    NONE = "-"


class SelfBind:
    CONFIRMED = "confirmed"                       # DF_SYMBOLIC (-Bsymbolic)
    PROBABLE = "self-bound-or-unreferenced"       # -Bsymbolic-functions signature
    NOT_SELF_BOUND = "interposable"               # retains a self JUMP_SLOT/GLOB_DAT
    NA = "n/a"


# ---- per-module ELF facts --------------------------------------------------

@dataclass
class SymDef:
    """One defined symbol in one module's symbol table."""
    name: str
    bind: str          # GLOBAL | WEAK | LOCAL
    visibility: str    # DEFAULT | HIDDEN | INTERNAL | PROTECTED
    type: str          # FUNC | OBJECT | ...
    size: int
    version: Optional[str]   # DEFAULT defined-version node name, or None
    in_dynsym: bool
    in_symtab: bool
    # ALL version-def node names this module defines for this symbol name
    # (a module can carry more than one version of the same base name for
    # ABI-compat reasons: foo@OLDV plus foo@@NEWV). Empty if unversioned.
    # This is what a "disjoint version-def set" comparison is computed over
    # -- NOT just the single default `version` string -- so two colliding
    # definitions are only cleared as VERSIONED-BENIGN when *none* of their
    # version nodes overlap.
    all_versions: FrozenSet[str] = field(default_factory=frozenset)


@dataclass
class ModuleFacts:
    path: str
    name: str                      # basename / soname used for display
    soname: Optional[str]
    is_exe: bool                   # ET_EXEC / ET_DYN main executable (PIE)
    is_dlopened: bool = False      # supplied via --module, RTLD_LOCAL by default
    rtld_global: bool = False      # promoted to global scope

    # Local-namespace group id for a dlopened (RTLD_LOCAL) module. Modules
    # sharing a group are modeled as sharing one dlopen scope (e.g. a
    # consumer .so and its own privately-bundled dependency); modules with
    # different groups (including the default: each dlopened module gets its
    # own singleton group unless told otherwise) are modeled as isolated
    # from each other. Caller-supplied via --module-group; see closure.py.
    # None for the executable and ordinarily-linked (DT_NEEDED) DSOs, which
    # default to the single shared/global scope.
    group: Optional[str] = None

    needed: List[str] = field(default_factory=list)
    rpath: List[str] = field(default_factory=list)
    runpath: List[str] = field(default_factory=list)
    df_symbolic: bool = False

    # defined symbols, keyed by name (last def wins for display; dups within one
    # module are irrelevant to the cross-module split question)
    defs: Dict[str, SymDef] = field(default_factory=dict)
    # undefined references (global/weak) this module needs resolved
    undefs: Set[str] = field(default_factory=set)

    # names of this module's OWN defined global-default symbols that still have
    # an interposable relocation (JUMP_SLOT / GLOB_DAT) pointing at them. A
    # non-empty set proves the module did NOT globally self-bind.
    self_interposable: Set[str] = field(default_factory=set)
    # names that have a COPY relocation in this module (copy-reloc unification)
    copy_relocs: Set[str] = field(default_factory=set)

    has_init: bool = False         # DT_INIT / DT_INIT_ARRAY present (may write)

    # this module's exported (.dynsym, defined) symbol names, and the union
    # of all version-def node names it declares -- used only for the
    # same-library clustering fingerprint (report --by-library), not for the
    # per-symbol verdict predicate.
    exported_symbols: FrozenSet[str] = field(default_factory=frozenset)
    version_defs: FrozenSet[str] = field(default_factory=frozenset)

    def selfbind_status(self) -> str:
        if self.df_symbolic:
            return SelfBind.CONFIRMED
        if self.self_interposable:
            return SelfBind.NOT_SELF_BOUND
        return SelfBind.PROBABLE


# ---- findings --------------------------------------------------------------

@dataclass
class Copy:
    module: str
    symtabs: str        # ".dynsym", ".symtab", or ".dynsym+.symtab"
    bind: str
    visibility: str
    type: str
    version: Optional[str]
    size: int
    self_bind: str      # SelfBind.* for the module hosting this copy
    scope_group: Optional[str] = None   # effective scope group (Route B display)


@dataclass
class Finding:
    symbol: str
    demangled: Optional[str]
    type: str
    copies: List[Copy]
    self_bind_label: str
    predicted: Dict[str, str]     # referencing module -> resolved module
    verdict: str
    severity: str
    why: str
    minor: bool = False   # shadow-derived (hidden/symtab) benign; hidden unless --all

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "demangled": self.demangled,
            "type": self.type,
            "copies": [c.__dict__ for c in self.copies],
            "self_binding": self.self_bind_label,
            "predicted_binding": self.predicted,
            "verdict": self.verdict,
            "severity": self.severity,
            "why": self.why,
        }
