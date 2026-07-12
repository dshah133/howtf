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
actually **split** at run time.

- Python 3.8+, `pyelftools` only. Apache-2.0.
- ELF targets (Linux, any arch — x86-64, aarch64, …). It parses ELF, so it
  runs fine as an analysis host on macOS too, pointed at Linux binaries.

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
| two libs export `foo@@V1` and `foo@@V2` | **versioned** references disambiguate them | `VERSIONED-BENIGN` |
| a private copy compiled `-fvisibility=hidden` | not exported → cannot interpose | `HIDDEN-BENIGN` |
| a copy that lives only in the exe's `.symtab` | not dynamic → cannot interpose | `NOT-DYNAMIC-BENIGN` |
| a custom `malloc` / sanitizer runtime | interposition is the whole point | `ALLOWLISTED` |
| the actual bug: DSO self-binds its writes, another module reads the exe's copy | the two copies genuinely diverge | **`SPLIT`** |

The verdict is what `symsplit` contributes. It flags **`SPLIT`** only when a
duplicate would make two modules in one process image resolve the same name to
different definitions — and it says *why*, per copy, with an honest confidence
label on the one heuristic that cannot be proven from ELF alone.

---

## The model (what makes a SPLIT)

Per `(symbol, module)` `symsplit` records the ELF facts that actually decide
the outcome: binding (global/weak/local), visibility, **which symbol table**
the copy lives in (only `.dynsym` copies can interpose; a `.symtab`-only copy
in the exe cannot), symbol versioning, type/size, and the module's
relocations.

It then simulates `ld.so`: the executable is first in the global scope,
followed by `DT_NEEDED` breadth-first (honoring `RPATH`/`RUNPATH`/`$ORIGIN`
and `--ld-library-path`); `dlopen`-style `--module`s are modeled `RTLD_LOCAL`
by default.

It flags **`SPLIT`** only when **all** hold:

1. a duplicate exists, both copies `STB_GLOBAL` / `STV_DEFAULT`, unversioned
   (or same version), not allowlisted;
2. the interposing copy is dynamically visible in a scope the referencing
   modules search;
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

### Honest confidence

The `-Bsymbolic-functions` case sets **no** dynamic flag, so it cannot be
*proven* from ELF — a DSO with no self-`JUMP_SLOT` might simply never call its
own exports. `symsplit` labels this `self-bound-or-unreferenced` and prints
that label in the output. When a DSO *retains* an interposable self-reference
(as in config A / D2) that is positive proof it did **not** self-bind, and the
copy is cleared as `interposable`.

---

## Usage

```
symsplit <executable> [--ld-library-path DIR:DIR] [--module ext.so ...]
                      [--rtld-global] [--allowlist FILE] [--json] [--all]
```

- Walks the closure and prints a human table (and per-copy detail); `--json`
  emits the machine record.
- **Exit code is nonzero iff any `SPLIT`** — usable as a CI gate.
- `--module` composes `dlopen`-style extensions (e.g. a Python process's C
  extension `.so`s) into the image.
- `--all` also shows benign `NO-SPLIT` / shadow findings.

Example — the reproducer's splitting configuration:

```
$ symsplit app_B --ld-library-path .
...
VERDICT   SEV    SYMBOL               TYPE   WHY
SPLIT     MEDIUM vx_get_device_list   FUNC   libverbs_shared.so is probably self-binding
   (no JUMP_SLOT/GLOB_DAT to any own export = -Bsymbolic-functions signature); its own
   copy answers its constructor calls, while libcollective.so's reference to
   vx_get_device_list resolves to app_B's copy -> two live copies diverge (split state)
      copy: app_B              .dynsym+.symtab GLOBAL DEFAULT size=192 sb=self-bound-or-unreferenced
      copy: libverbs_shared.so .dynsym+.symtab GLOBAL DEFAULT size=192 sb=self-bound-or-unreferenced
      ref:  libcollective.so   -> app_B
$ echo $?
2
```

---

## The allowlist

`symsplit/data/allowlist.txt` lists symbols that are duplicated *by design* —
the allocator (`malloc`/`free`/…), `operator new`/`delete`, `__cxa_*`,
jemalloc/tcmalloc, sanitizer runtimes, common pthread shims. A dup that would
otherwise be `SPLIT` but matches the allowlist is reported `ALLOWLISTED`. The
file is the policy — edit it; every entry carries a rationale.

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
   matrix from `demo/rdma-symbol-collision`. Flags **exactly** config B.
2. **Benign fixtures** (`test_benign.py`) — weak / versioned / hidden /
   allowlisted / symtab-only. Each returns its correct benign verdict.
3. **System sweep** — see `NOTES-system-sweep.md`.

---

## Limitations (read these)

- **Self-binding confidence.** `-Bsymbolic-functions` leaves no ELF flag.
  `symsplit` infers it from the absence of self-`JUMP_SLOT`/`GLOB_DAT` and
  labels it `self-bound-or-unreferenced`. This is a *library-level* inference:
  it assumes `-Bsymbolic-functions` binds all internal calls uniformly (true
  for the linker flag; a per-symbol version-script that self-binds *some*
  exports but keeps a `JUMP_SLOT` for others could be misread as
  `interposable`). No disassembly is performed.
- **dlopen scope modeling.** `--module`s are modeled `RTLD_LOCAL` (own group)
  or `RTLD_GLOBAL` (`--rtld-global`). Real programs can pass arbitrary
  `RTLD_*` flags per `dlopen`, load order matters, and `dlmopen` namespaces
  are not modeled. The tool assumes one global namespace.
- **Copy relocations.** On the platforms tested, a global data object exported
  by both exe and DSO is unified by ordinary interposition (the DSO's
  `GLOB_DAT` binds to the exe copy), which `symsplit` reads as `interposable`
  → `NO-SPLIT`. Classic `R_*_COPY` copy relocations are also recorded; either
  way the copies unify.
- **Reader/writer across symbols.** The `SPLIT` is reported on the reader
  symbol; the self-bound writer symbol (no external reader) is reported
  `NO-SPLIT` with a note. The shared *state* behind them (a file-local
  `static` table) is not itself a symbol and is not directly visible — the
  finding names the exported functions that touch it.
- **Unresolved `DT_NEEDED`.** If a dependency can't be located the analysis is
  partial and a warning is printed; pass `--ld-library-path`.
