"""The binding simulator and the SPLIT / SCOPE-PARTITION verdict predicates.

This is where symsplit earns the "not nm|uniq -d" claim. A duplicate symbol is
necessary but nowhere near sufficient. We model ld.so's scope-based symbol
lookup and classify every duplicate strong symbol into exactly one of two real
split-state routes, or one of several benign explanations:

  Route A -- SPLIT (interposition capture): both copies live in a scope every
  reader can see, but the DEFINING DSO self-binds its OWN calls to its own
  copy (confirmed DF_SYMBOLIC, or the -Bsymbolic-functions signature), while
  an external reader resolves the ordinary way and lands on a DIFFERENT copy.

  Route B -- SCOPE-PARTITION (scope partition): no copy is reachable from a
  shared/global scope at all -- the definers are split across >= 2 isolated
  local (RTLD_LOCAL) namespaces. No self-binding is needed for this to
  split: each namespace's own consumers simply resolve the name to whatever
  copy lives in THEIR namespace. This is the common real-world shape (e.g.
  the same vendored library -- libgomp, libstdc++, etc. -- bundled
  separately inside several wheels, each dlopen'd RTLD_LOCAL).

Both are genuine "two live copies of one strong symbol's state, resolved
differently by different readers" bugs; see Verdict.SPLIT_VERDICTS.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from .closure import Closure
from .model import (
    Copy,
    Finding,
    ModuleFacts,
    SelfBind,
    Severity,
    SymDef,
    Verdict,
)

_GLOBAL_GROUP = "__GLOBAL__"


def _global_scope(closure: Closure) -> List[ModuleFacts]:
    """Modules that live in the global symbol scope, in search order.

    dlopen'd modules are RTLD_LOCAL by default -> not providers in global
    scope (but they still reference into it). --rtld-global promotes them.
    """
    return [m for m in closure.modules
            if (not m.is_dlopened) or m.rtld_global]


def _defines(m: ModuleFacts, name: str) -> Optional[SymDef]:
    sd = m.defs.get(name)
    if sd is None or not sd.in_dynsym:
        return None
    if sd.bind not in ("GLOBAL", "WEAK"):
        return None
    if sd.visibility in ("HIDDEN", "INTERNAL"):
        return None
    return sd


def _self_binds(m: ModuleFacts, name: str) -> bool:
    """Does module m bind its OWN reference to `name` to its own copy?"""
    sd = m.defs.get(name)
    if sd is None or not sd.in_dynsym or sd.bind != "GLOBAL":
        return False
    if m.df_symbolic:
        return True                       # -Bsymbolic: everything self-binds
    # -Bsymbolic-functions signature: the module retained NO interposable
    # self-reference anywhere, so any internal use is link-time bound.
    if m.self_interposable:
        return False                      # kept an interposable self-ref
    return True                           # probable self-bind (or unreferenced)


def resolve(name: str, ref_module: ModuleFacts, scope: List[ModuleFacts],
           groups: Optional[Dict[str, List[ModuleFacts]]] = None
           ) -> Optional[ModuleFacts]:
    """Which module's definition of `name` does a reference in ref_module bind
    to? Models self-binding first, then global-scope search order, then (for
    an RTLD_LOCAL module) its own local-scope group -- itself plus any peers
    declared to share its dlopen namespace (see closure.py / --module-group).
    """
    if _self_binds(ref_module, name) and _defines(ref_module, name):
        return ref_module
    search = list(scope)
    if ref_module.is_dlopened and not ref_module.rtld_global:
        key = ref_module.group or ("solo:" + ref_module.path)
        peers = (groups or {}).get(key, [ref_module])
        for p in peers:
            if p not in search:
                search.append(p)
    for m in search:
        if _defines(m, name):
            return m
    return None


def _pairwise_disjoint(sets: Sequence[Set[str]]) -> bool:
    """True iff no two sets in the sequence share any element."""
    seen: Set[str] = set()
    for s in sets:
        if seen & s:
            return False
        seen |= s
    return True


class Analyzer:
    def __init__(self, closure: Closure, allowlist, assume_rtld_local: bool = False):
        self.closure = closure
        self.allow = allowlist
        self.assume_rtld_local = assume_rtld_local
        self.scope = _global_scope(closure)
        # local-scope groups: effective-group-key -> member modules, for
        # RTLD_LOCAL peer expansion in resolve().
        self.groups: Dict[str, List[ModuleFacts]] = {}
        for m in closure.modules:
            if m.is_dlopened and not m.rtld_global:
                key = m.group or ("solo:" + m.path)
                self.groups.setdefault(key, []).append(m)

    # -- scope modeling (Route B) --------------------------------------------

    def _effective_group(self, m: ModuleFacts) -> str:
        """The local-namespace group `m` is modeled as living in, for the
        purpose of the scope-partition predicate. Modules in the shared
        global scope (ordinary DT_NEEDED links, or --rtld-global modules) all
        share _GLOBAL_GROUP. A dlopened (RTLD_LOCAL) module is in its own
        group -- explicit via --module-group, otherwise a private singleton
        that includes whatever it privately NEEDED. --assume-rtld-local
        additionally isolates ordinarily-linked DSOs into their own private
        group each, for callers who know (from outside the ELF) that a
        plain-looking DT_NEEDED closure is actually assembled from separately
        dlopen'd RTLD_LOCAL pieces."""
        if m.is_dlopened and not m.rtld_global:
            return m.group or ("solo:" + m.path)
        if self.assume_rtld_local and not m.is_exe and not m.rtld_global:
            return "solo:" + m.path
        return _GLOBAL_GROUP

    # -- indexing ------------------------------------------------------------

    def _dup_index(self) -> Dict[str, List[Tuple[ModuleFacts, SymDef]]]:
        idx: Dict[str, List[Tuple[ModuleFacts, SymDef]]] = {}
        for m in self.closure.modules:
            for name, sd in m.defs.items():
                idx.setdefault(name, []).append((m, sd))
        return idx

    def _dyn_copies(self, entries):
        """Copies that can actually interpose: exported, global/weak, default
        (or protected) visibility."""
        return [(m, sd) for (m, sd) in entries
                if sd.in_dynsym and sd.bind in ("GLOBAL", "WEAK")
                and sd.visibility not in ("HIDDEN", "INTERNAL")]

    def _shadow_copies(self, entries):
        """Copies that EXIST in the image but cannot interpose: local binding,
        hidden/internal visibility, or living only in .symtab."""
        out = []
        for (m, sd) in entries:
            interposable = (sd.in_dynsym and sd.bind in ("GLOBAL", "WEAK")
                            and sd.visibility not in ("HIDDEN", "INTERNAL"))
            if interposable:
                continue
            # priority: a non-default-visibility or local-bound copy is HIDDEN
            # (private by design); a would-be GLOBAL/WEAK interposer that is
            # merely absent from .dynsym is NOT-DYNAMIC.
            reason = None
            if sd.visibility in ("HIDDEN", "INTERNAL"):
                reason = "hidden"
            elif sd.bind == "LOCAL":
                reason = "local"
            elif sd.in_symtab and not sd.in_dynsym:
                reason = "symtab"
            if reason:
                out.append((m, sd, reason))
        return out

    def _family_types(self, D: ModuleFacts, dup_names: set) -> set:
        types = set()
        for name in dup_names:
            sd = D.defs.get(name)
            if sd:
                types.add(sd.type)
        return types

    # -- main ----------------------------------------------------------------

    def run(self) -> List[Finding]:
        idx = self._dup_index()
        # names that are duplicated across >= 2 modules in the dynamic scope
        dup_names = set()
        for name, entries in idx.items():
            mods = {m.path for (m, sd) in self._dyn_copies(entries)}
            if len(mods) >= 2:
                dup_names.add(name)

        findings: List[Finding] = []
        for name, entries in sorted(idx.items()):
            dyn = self._dyn_copies(entries)
            dyn_mods = {m.path for (m, _) in dyn}

            if len(dyn_mods) >= 2:
                findings.append(self._classify(name, dyn, dup_names))
                continue

            # exactly one interposable copy, but another module holds a copy
            # that cannot interpose (hidden / local / symtab-only) -> the dup
            # is real in the image but benign. Label it (informational).
            shadow = self._shadow_copies(entries)
            shadow = [(m, sd, r) for (m, sd, r) in shadow
                      if not any(dm.path == m.path for (dm, _) in dyn)]
            if len(dyn_mods) >= 1 and shadow:
                findings.append(self._shadow_finding(name, dyn, shadow))

        findings.sort(key=lambda f: (f.verdict not in Verdict.SPLIT_VERDICTS, f.symbol))
        return findings

    # -- verdicts ------------------------------------------------------------

    def _mk_copies(self, dyn) -> List[Copy]:
        out = []
        for (m, sd) in dyn:
            tabs = ".dynsym" + ("+.symtab" if sd.in_symtab else "")
            out.append(Copy(
                module=m.name, symtabs=tabs, bind=sd.bind,
                visibility=sd.visibility, type=sd.type, version=sd.version,
                size=sd.size, self_bind=m.selfbind_status(),
                scope_group=self._effective_group(m),
            ))
        return out

    def _predicted(self, name) -> Dict[str, str]:
        pred = {}
        for m in self.closure.modules:
            if name in m.undefs:
                w = resolve(name, m, self.scope, self.groups)
                pred[m.name] = w.name if w else "UNRESOLVED"
        return pred

    def _shadow_finding(self, name, dyn, shadow) -> Finding:
        copies = self._mk_copies(dyn)
        reasons = set()
        for (m, sd, r) in shadow:
            reasons.add(r)
            tabs = ".symtab" if r == "symtab" else ".dynsym"
            copies.append(Copy(m.name, tabs, sd.bind, sd.visibility,
                               sd.type, sd.version, sd.size, SelfBind.NA,
                               scope_group=self._effective_group(m)))
        if "hidden" in reasons or "local" in reasons:
            verdict = Verdict.HIDDEN_BENIGN
            why = ("a second copy has non-default visibility / local binding "
                   "(%s) -> not exported for interposition, cannot split"
                   % ", ".join(sorted(reasons)))
        else:
            verdict = Verdict.NOT_DYNAMIC_BENIGN
            why = ("a second copy lives only in .symtab (not .dynsym) -> it "
                   "cannot interpose, so no dynamic split")
        return Finding(
            symbol=name, demangled=None, type=dyn[0][1].type, copies=copies,
            self_bind_label=SelfBind.NA, predicted=self._predicted(name),
            verdict=verdict, severity=Severity.NONE, why=why, minor=True,
        )

    def _classify(self, name, dyn, dup_names) -> Finding:
        copies = self._mk_copies(dyn)
        pred = self._predicted(name)
        typ = dyn[0][1].type
        binds = {sd.bind for (_, sd) in dyn}
        viss = {sd.visibility for (_, sd) in dyn}

        def F(verdict, sev, why, label=SelfBind.NA):
            return Finding(name, None, typ, copies, label, pred, verdict, sev, why)

        # 1. global + weak = intentional override idiom
        if "WEAK" in binds:
            return F(Verdict.WEAK_PATTERN, Severity.NONE,
                     "one copy is STB_WEAK -> intentional weak-override idiom, "
                     "not a same-strength collision")
        # 2. hidden visibility on a copy
        if viss & {"HIDDEN", "INTERNAL"}:
            return F(Verdict.HIDDEN_BENIGN, Severity.NONE,
                     "a copy has non-default visibility -> not exported for "
                     "interposition")
        # 3. symbol versioning -- disambiguates ONLY when every pair of
        # colliding definitions carries a DISJOINT version-def set (genuinely
        # different libraries / major versions). Two copies that happen to
        # carry the SAME version node (e.g. two vendored copies of the same
        # library, each defining foo@@V1 under an identical verdef) are NOT
        # disambiguated by that shared version -- they stay in the hazard
        # pool below for Route A / Route B classification. A copy with no
        # version at all also blocks the clearing: an unversioned reference
        # can still bind to it.
        version_sets = []
        for (_, sd) in dyn:
            if sd.all_versions:
                version_sets.append(set(sd.all_versions))
            elif sd.version:
                version_sets.append({sd.version})
            else:
                version_sets.append(set())
        if all(version_sets) and _pairwise_disjoint(version_sets):
            all_versions = sorted(set().union(*version_sets))
            return F(Verdict.VERSIONED_BENIGN, Severity.NONE,
                     "copies carry disjoint symbol version sets (%s) -> "
                     "versioned references disambiguate them"
                     % ", ".join(all_versions))

        # 4. ROUTE B -- scope partition. If NONE of the colliding copies is
        # reachable from a shared/global scope, and the definers span >= 2
        # isolated local (RTLD_LOCAL) namespaces, each namespace's own
        # consumers resolve the name to THEIR OWN copy -- no self-binding is
        # needed for this to split; the isolation itself is the mechanism.
        eff_groups: Dict[str, List[Tuple[ModuleFacts, SymDef]]] = {}
        for (m, sd) in dyn:
            eff_groups.setdefault(self._effective_group(m), []).append((m, sd))
        isolated_groups = {g: members for g, members in eff_groups.items()
                           if g != _GLOBAL_GROUP}
        if _GLOBAL_GROUP not in eff_groups and len(isolated_groups) >= 2:
            if self.allow.match(name):
                return F(Verdict.ALLOWLISTED, Severity.NONE,
                         "matches the intentional-interposer allowlist (%s) "
                         "-> duplicate is by design" % self.allow.match(name))
            sev = self._severity(name, dyn, dyn[0][0], dup_names)
            group_desc = "; ".join(
                "%s (%s)" % (g, ", ".join(sorted(mm.name for mm, _ in members)))
                for g, members in sorted(isolated_groups.items())
            )
            why = (
                "no copy of %s is reachable from a shared/global scope; the "
                "definers split across %d isolated local (RTLD_LOCAL) "
                "namespaces -- %s -- so each namespace's own consumers "
                "resolve %s to THEIR OWN copy -> two live copies diverge "
                "(split state); no self-binding or interposition needed"
                % (name, len(isolated_groups), group_desc, name)
            )
            return F(Verdict.SCOPE_PARTITION, sev, why, label=dyn[0][0].selfbind_status())

        # 5. ROUTE A -- self-binding split predicate (library-level
        # writer/reader), for copies that share a reachable scope.
        dso_copies = [(m, sd) for (m, sd) in dyn if not m.is_exe]
        split_reader = None
        winner_mod = None
        selfbound_D = None
        for (D, sdD) in dso_copies:
            if D.selfbind_status() == SelfBind.NOT_SELF_BOUND:
                continue                         # D's own use is interposable
            # D is self-binding (confirmed/probable). Does an external module
            # read THIS name from a copy other than D's?
            for R in self.closure.modules:
                if R is D or name not in R.undefs:
                    continue
                w = resolve(name, R, self.scope, self.groups)
                if w is not None and w is not D:
                    split_reader, winner_mod, selfbound_D = R, w, D
                    break
            if split_reader:
                break

        if split_reader is not None:
            D = selfbound_D
            if self.allow.match(name):
                return F(Verdict.ALLOWLISTED, Severity.NONE,
                         "matches the intentional-interposer allowlist (%s) -> "
                         "duplicate is by design" % self.allow.match(name),
                         label=D.selfbind_status())
            sev = self._severity(name, dyn, D, dup_names)
            why = (
                "%s is probably self-binding (%s); its own copy of the symbol "
                "set answers its internal/constructor calls, while %s's "
                "reference to %s resolves to %s's copy -> two live copies "
                "diverge (split state)" % (
                    D.name,
                    "DF_SYMBOLIC" if D.df_symbolic
                    else "no JUMP_SLOT/GLOB_DAT to any own export = "
                         "-Bsymbolic-functions signature",
                    split_reader.name, name, winner_mod.name,
                )
            )
            return F(Verdict.SPLIT, sev, why, label=D.selfbind_status())

        # 6. dup exists but unifies
        # allowlisted dups that never split are still benign; label them.
        if self.allow.match(name):
            return F(Verdict.ALLOWLISTED, Severity.NONE,
                     "matches the intentional-interposer allowlist (%s)"
                     % self.allow.match(name))
        selfbinders = [m.name for (m, _) in dso_copies
                       if m.selfbind_status() != SelfBind.NOT_SELF_BOUND]
        if selfbinders:
            why = ("duplicate is self-bound in %s but no OTHER module "
                   "references it to a different copy (self-bound writer / "
                   "unreferenced) -> no split" % ", ".join(selfbinders))
        else:
            why = ("every DSO copy retains an interposable self-reference "
                   "(JUMP_SLOT/GLOB_DAT) -> all references unify on the "
                   "first global-scope copy -> no split")
        label = dso_copies[0][0].selfbind_status() if dso_copies else SelfBind.NA
        return F(Verdict.NO_SPLIT, Severity.NONE, why, label=label)

    def _severity(self, name, dyn, D, dup_names) -> str:
        sizes = {sd.size for (_, sd) in dyn}
        if len(sizes) >= 2:
            return Severity.HIGH          # different library versions coexisting
        fam = self._family_types(D, dup_names)
        if "FUNC" in fam and "OBJECT" in fam:
            return Severity.HIGH          # spans code + mutable state
        if dyn[0][1].type == "OBJECT":
            return Severity.HIGH          # duplicated mutable state
        return Severity.MEDIUM            # function-only, equal sizes
