# symsplit — a split-state linking diagnostic

`symsplit` is a **binding simulator** for the *split-state* linking failure:
when two live copies of one strong C/C++ symbol exist across the
static/dynamic boundary, and a process resolves the same name to **different
definitions**. A load-time constructor populates one copy's private state;
other code reads the other, empty, copy. No linker error. No crash. Just a
wrong answer — "device not found" while the devices were demonstrably
registered (into the *other* copy).

`symsplit` reads an executable and its resolved dependency closure, models the
dynamic linker's symbol lookup, and reports whether any duplicate symbol would
actually **split** at run time — via either of two distinct routes (see
below).

- Python 3.8+, `pyelftools` only. Apache-2.0.
- ELF targets (Linux, any arch — x86-64, aarch64, …). It parses ELF, so it
  runs fine as an analysis host on macOS too, pointed at Linux binaries.

---

## Two routes to the same disease

"Two live copies of one library's state" happens two structurally different
ways, and `symsplit` tells them apart with two distinct verdicts:

- **Route A — interposition capture (`SPLIT`).** A duplicate **strong,
  default-visibility** symbol, where the defining DSO **self-binds** its own
  calls (confirmed `-Bsymbolic`, or the `-Bsymbolic-functions` signature),
  and an **interposing module in a shared scope** resolves the name the
  ordinary way and lands on the *other* copy. Both copies are reachable from
  one shared/global namespace; self-binding is what makes them diverge
  anyway.
- **Route B — scope partition (`SCOPE-PARTITION`).** `RTLD_LOCAL` +
  **vendored duplication of the same library**. Two (or more) modules are
  loaded into **different local scopes**, each defining its own copy of the
  symbol — no self-binding, no interposition needed. Each namespace's own
  consumers simply resolve the name to whatever copy lives in *their*
  namespace. This is the common real-world shape: the same runtime library
  (`libgomp`, `libstdc++`, …) bundled separately inside several Python
  wheels, each `dlopen`'d `RTLD_LOCAL` by the loader — e.g. `libgomp`
  bundled independently across `faiss` / `scikit-learn` / `torch` wheels.

Both are genuine bugs — two live copies of one strong symbol's state,
resolved differently by different readers. Everything else `symsplit`
reports is a benign/informational explanation for a duplicate that does
*not* split.

---

## Why this is not `nm | sort | uniq -d`

`nm | sort | uniq -d` answers **"does a duplicate symbol exist?"** That is
necessary but nowhere near sufficient, and on any real system it fires
constantly on things that are completely fine. In a sweep of 788 stock
binaries, 468 had duplicate symbols across their closures — and **zero** were
splits.

A duplicate is benign in every one of these cases, none of which `uniq -d` can
tell apart from a real bug:

| Situation | Why it is NOT a split | symsplit verdict |
|---|---|---|
| `bash` defines its own `getenv`, libc also defines `getenv` | libc's own uses are **interposable**, so everything unifies on `bash`'s copy | `NO-SPLIT` |
| a library ships a **weak** default you override | global+weak is the *intended* override idiom | `WEAK-PATTERN` |
| two libs export `foo@@V1` and `foo@@V2` under **disjoint** version-def sets | versioned references disambiguate them | `VERSIONED-BENIGN` |
| two copies of *the same library* both export `foo@@V1` under the **same** version node | versioning does not disambiguate identical version nodes — still a hazard | *(stays in the pool → Route A/B)* |
| a private copy compiled `-fvisibility=hidden` | not exported → cannot interpose | `HIDDEN-BENIGN` |
| a copy that lives only in the exe's `.symtab` | not dynamic → cannot interpose | `NOT-DYNAMIC-BENIGN` |
| a custom `malloc` / sanitizer runtime | interposition is the whole point | `ALLOWLISTED` |
| the actual bug: DSO self-binds its writes, an interposing module in a shared scope reads the exe's copy | the two copies genuinely diverge (Route A) | **`SPLIT`** |
| the actual bug: the same library, vendored twice, loaded into two `RTLD_LOCAL` namespaces | each namespace answers from its own copy (Route B) | **`SCOPE-PARTITION`** |

The verdict is what `symsplit` contributes. It flags **`SPLIT`** or
**`SCOPE-PARTITION`** only when a duplicate would actually make two modules
in one process image resolve the same name to different definitions — and it
says *why*, per copy, with an honest confidence label on the heuristics that
cannot be fully proven from ELF alone.

---

## The model

Per `(symbol, module)` `symsplit` records the ELF facts that actually decide
the outcome: binding (global/weak/local), visibility, **which symbol table**
the copy lives in (only `.dynsym` copies can interpose; a `.symtab`-only copy
in the exe cannot), symbol versioning (the **full set** of version-def nodes
a module defines for that name, not just the default one), type/size, and the
module's relocations.

It then simulates `ld.so`: the executable is first in the global scope,
followed by `DT_NEEDED` breadth-first (honoring `RPATH`/`RUNPATH`/`$ORIGIN`
and `--ld-library-path`); `dlopen`-style `--module`s are modeled `RTLD_LOCAL`
by default, each in its **own isolated local-scope group** unless
`--module-group` says two of them share one dlopen namespace (see "dlopen
scope modeling" below).

A duplicate first has to clear the benign checks (weak override, hidden
visibility, disjoint version-def sets, symtab-only, allowlist) before it is
classified into a route:

**Route A — `SPLIT`** fires only when **all** hold:

1. a duplicate exists, both copies `STB_GLOBAL` / `STV_DEFAULT`, not
   disambiguated by disjoint versioning, not allowlisted;
2. the interposing copy is dynamically visible in a scope the referencing
   modules search (i.e. at least one copy lives in the shared/global scope);
3. the **defining DSO self-binds its own copy** — *confirmed* (`DF_SYMBOLIC`
   from `-Bsymbolic`) or *probable* (the `-Bsymbolic-functions` signature: the
   DSO retains **no** `JUMP_SLOT`/`GLOB_DAT` relocation naming any of its own
   exported symbols, so its internal/constructor calls were bound at link
   time);
4. **at least one other module's reference resolves to the other copy.**

The self-binding test is deliberately a *library-level* signal, because the
split is emergent across the export set: in the reproducer the DSO's
constructor **writes** via `vx_register_device` (self-bound to the DSO copy)
while a collective **reads** via `vx_get_device_list` (bound to the exe copy).
Neither symbol splits alone; the library does.

**Route B — `SCOPE-PARTITION`** fires when, after the same benign checks,
**none** of the colliding copies is reachable from the shared/global scope
at all, and the definers span **two or more isolated local (`RTLD_LOCAL`)
namespaces**. No self-binding is required — the isolation itself is the
mechanism: each namespace's own consumers resolve the name to whatever copy
lives in *their* namespace, so the copies can never unify. (If one copy IS
reachable from the shared/global scope, it dominates ordinary resolution for
everyone and the duplicate falls back to the Route A predicate instead.)

### Honest confidence

The `-Bsymbolic-functions` case sets **no** dynamic flag, so it cannot be
*proven* from ELF — a DSO with no self-`JUMP_SLOT` might simply never call its
own exports. `symsplit` labels this `self-bound-or-unreferenced` and prints
that label in the output. When a DSO *retains* an interposable self-reference
(as in config A / D2) that is positive proof it did **not** self-bind, and the
copy is cleared as `interposable`.

Real `dlopen` scope (`RTLD_LOCAL` vs `RTLD_GLOBAL`, and which modules share a
`dlmopen`-style namespace) is a **runtime property, not present in the
ELF**. `symsplit` can only model what the caller tells it — see "dlopen
scope modeling" below. This is the analogous honesty gap for Route B that
`self-bound-or-unreferenced` is for Route A.

---

## dlopen scope modeling (Route B input)

Because real dlopen scope isn't recoverable from the ELF, the caller supplies
it:

- **`--module ext.so`** (repeatable): compose a `dlopen`-style extension into
  the image. **Default assumption:** each `--module` is `RTLD_LOCAL` and gets
  its **own isolated local-scope group**, which extends to whatever
  `DT_NEEDED` dependencies *that module* privately pulls in (a module's own
  dependency closure shares its dlopen scope, matching real `dlopen()`
  behavior). Two separate `--module` invocations are modeled as two separate,
  mutually isolated namespaces unless told otherwise.
- **`--module-group NAME:mod1,mod2,...`** (repeatable): declare that specific
  `--module`s (matched by basename or soname) share **one** dlopen
  namespace — e.g. a consumer extension and its own privately-bundled
  dependency that were actually loaded by the same `dlopen()` call. Grouped
  modules can interpose each other; they are no longer isolated for the
  Route B check.
- **`--rtld-global`**: model **all** `--module`s as `RTLD_GLOBAL` (promoted
  into the shared scope) instead of `RTLD_LOCAL`.
- **`--assume-rtld-local`**: also isolate *ordinarily-linked* (`DT_NEEDED`)
  DSOs into their own private scope group each, for callers who know — from
  outside the ELF — that a plain-looking closure is actually assembled from
  separately `dlopen`'d `RTLD_LOCAL` pieces (or who simply want a
  conservative "what if these were isolated" sweep). Off by default: an
  ordinary `DT_NEEDED` closure is modeled as one shared/global scope, matching
  normal dynamic linking.

`dlmopen` namespaces, per-`dlopen` `RTLD_*` flag combinations beyond
local/global, and load-order-dependent effects are still not modeled — see
Limitations.

---

## Usage

```
symsplit <executable> [--ld-library-path DIR:DIR] [--module ext.so ...]
                      [--module-group NAME:mod1,mod2,...] [--rtld-global]
                      [--assume-rtld-local] [--allowlist FILE]
                      [--json] [--all] [--by-library]
```

- Walks the closure and prints a human table (and per-copy detail); `--json`
  emits the machine record.
- **Exit code is nonzero iff any `SPLIT` or `SCOPE-PARTITION`** — usable as a
  CI gate.
- `--module` composes `dlopen`-style extensions (e.g. a Python process's C
  extension `.so`s) into the image; `--module-group` / `--rtld-global` /
  `--assume-rtld-local` control the scope model (see above).
- `--all` also shows benign `NO-SPLIT` / shadow findings.
- `--by-library` clusters duplicate-defining modules by same-library
  fingerprint (soname prefix before any auditwheel-style `-<hash>` suffix,
  overlapping exported-symbol sets, matching version-def sets) and reports
  **"N copies of library L (M shared symbols)"** as one finding instead of
  one row per colliding symbol — the mode to reach for a directory/wheel
  sweep where the same vendored library shows up dozens of times, so the
  report stays readable instead of looking like Tier-0 fearmongering.

Example — the reproducer's Route A splitting configuration:

```
$ symsplit app_B --ld-library-path .
...
VERDICT          SEV    SYMBOL               TYPE   WHY
SPLIT            MEDIUM vx_get_device_list   FUNC   libverbs_shared.so is probably self-binding
   (no JUMP_SLOT/GLOB_DAT to any own export = -Bsymbolic-functions signature); its own
   copy answers its constructor calls, while libcollective.so's reference to
   vx_get_device_list resolves to app_B's copy -> two live copies diverge (split state)
      copy: app_B              .dynsym+.symtab GLOBAL DEFAULT size=192 sb=self-bound-or-unreferenced group=__GLOBAL__
      copy: libverbs_shared.so .dynsym+.symtab GLOBAL DEFAULT size=192 sb=self-bound-or-unreferenced group=__GLOBAL__
      ref:  libcollective.so   -> app_B
$ echo $?
2
```

Example — a Route B scope partition (two vendored copies of one library,
each its own `--module`):

```
$ symsplit app --module libgomp-a34b3233.so.1 --module libgomp-d22c30c5.so.1
...
VERDICT          SEV    SYMBOL                TYPE   WHY
SCOPE-PARTITION  MEDIUM omp_get_num_threads   FUNC   no copy of omp_get_num_threads is reachable from a
   shared/global scope; the definers split across 2 isolated local (RTLD_LOCAL) namespaces --
   solo:.../libgomp-a34b3233.so.1 (libgomp-a34b3233.so.1); solo:.../libgomp-d22c30c5.so.1
   (libgomp-d22c30c5.so.1) -- so each namespace's own consumers resolve omp_get_num_threads to
   THEIR OWN copy -> two live copies diverge (split state); no self-binding or interposition needed
$ echo $?
2
```

Same case with `--by-library`, collapsed into one cluster instead of one row
per colliding symbol:

```
$ symsplit app --module libgomp-a34b3233.so.1 --module libgomp-d22c30c5.so.1 --by-library
...
CLUSTER  library=libgomp.so.1 copies=2 shared_symbols=137 sev=MEDIUM  SCOPE-PARTITION=137
  modules: libgomp-a34b3233.so.1, libgomp-d22c30c5.so.1
  symbols (137 total): GOMP_barrier, GOMP_parallel, omp_get_num_threads, omp_set_num_threads, ...
$ echo $?
2
```

---

## The allowlist

`symsplit/data/allowlist.txt` lists symbols that are duplicated *by design* —
the allocator (`malloc`/`free`/…), `operator new`/`delete`, `__cxa_*`,
jemalloc/tcmalloc, sanitizer runtimes, common pthread shims. A dup that would
otherwise be `SPLIT` or `SCOPE-PARTITION` but matches the allowlist is
reported `ALLOWLISTED` instead, on either route. The file is the policy —
edit it; every entry carries a rationale.

---

## Tests & reproducibility

The fixtures are **real ELF binaries**, so the tests need a Linux/ELF
toolchain. A container harness is provided:

```
cd tools/symsplit
make test      # builds the container, runs the fixtures + pytest
make sweep     # system-binary sweep inside the container
```

On a non-ELF host the pytest suite skips the ELF tests with a clear message
(the allowlist unit test still runs). See `Dockerfile` for the exact
environment (Debian, gcc, `pyelftools==0.31`, `pytest==8.3.4`).

The suite covers:

1. **Ground truth** (`test_ground_truth.py`) — the four-configuration gating
   matrix from `demo/rdma-symbol-collision`. Flags **exactly** config B
   (`SPLIT`, Route A).
2. **Benign fixtures** (`test_benign.py`) — weak / versioned / hidden /
   allowlisted / symtab-only, plus a same-version-node regression
   (`test_versioned_same_node_not_cleared`) proving two vendored copies under
   an identical version node are **not** cleared as `VERSIONED-BENIGN`.
   Each returns its correct verdict.
3. **Route B / clustering** (`test_scope_partition.py`) — two vendored
   copies of one library, composed via `--module`, isolated by default →
   `SCOPE-PARTITION`; `--module-group` unifies them back into one scope;
   `--assume-rtld-local` turns an ordinarily-linked pair into a
   scope-partition finding; `--by-library` collapses the per-symbol rows
   into one cluster line.
4. **System sweep** — see `NOTES-system-sweep.md`.

---

## Limitations (read these)

- **Self-binding confidence (Route A).** `-Bsymbolic-functions` leaves no ELF
  flag. `symsplit` infers it from the absence of self-`JUMP_SLOT`/`GLOB_DAT`
  and labels it `self-bound-or-unreferenced`. This is a *library-level*
  inference: it assumes `-Bsymbolic-functions` binds all internal calls
  uniformly (true for the linker flag; a per-symbol version-script that
  self-binds *some* exports but keeps a `JUMP_SLOT` for others could be
  misread as `interposable`). No disassembly is performed.
- **dlopen scope modeling is caller-supplied (Route B).** `RTLD_LOCAL` vs
  `RTLD_GLOBAL`, and which modules share one dlopen/`dlmopen` namespace, is a
  **runtime property that is not present in the ELF**. `symsplit` models
  exactly what `--module` / `--module-group` / `--rtld-global` /
  `--assume-rtld-local` are told, per the default assumptions documented
  above (an ordinary `DT_NEEDED` closure is one shared/global scope; each
  `--module` is its own isolated `RTLD_LOCAL` group unless grouped). Real
  programs can pass arbitrary `RTLD_*` flag combinations per `dlopen`, load
  order matters at the margins, and `dlmopen` namespaces beyond "shared" /
  "isolated singleton" / "explicit group" are not modeled.
- **Copy relocations.** On the platforms tested, a global data object exported
  by both exe and DSO is unified by ordinary interposition (the DSO's
  `GLOB_DAT` binds to the exe copy), which `symsplit` reads as `interposable`
  → `NO-SPLIT`. Classic `R_*_COPY` copy relocations are also recorded; either
  way the copies unify.
- **Reader/writer across symbols.** A Route A `SPLIT` is reported on the
  reader symbol; the self-bound writer symbol (no external reader) is
  reported `NO-SPLIT` with a note. The shared *state* behind them (a
  file-local `static` table) is not itself a symbol and is not directly
  visible — the finding names the exported functions that touch it.
- **Same-library clustering is a fingerprint, not proof of identity.**
  `--by-library` groups modules by soname prefix (hash-suffix stripped) +
  matching version-def sets + overlapping exported-symbol sets. Two
  genuinely different libraries that happen to share a soname-prefix naming
  convention and a similar export surface could theoretically cluster
  together; the per-symbol mode (default, no `--by-library`) is always
  available to check any individual finding directly.
- **Unresolved `DT_NEEDED`.** If a dependency can't be located the analysis is
  partial and a warning is printed; pass `--ld-library-path`.
