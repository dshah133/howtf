# Split-state linking: when a strong symbol is defined on both sides of the boundary

**DRAFT — Deep must rewrite every sentence in his own voice before submitting; LWN prohibits machine-drafted prose. This is a structural/factual scaffold only. Every number and command output below traces to a repo artifact; the citation list is at the end. Do not submit any part of this text verbatim.**

---

## The symptom

At Meta, application training binaries built by Buck from a single PyTorch commit split into two populations: most started normally, but some died at startup with the collective-communication library for MTIA (Meta's in-house accelerator) reporting that the device was not found. The device was present. It enumerated; NCCL, in the same process image, saw the same RDMA device list and worked. Whether a binary could find the device turned out to be a property of the binary — specifically of its link line — and the mechanism behind it is a failure class that no linker diagnostic detects, that produces wrong answers rather than crashes, and that the ML ecosystem is already patching around under several unrelated names.

The binaries statically link PyTorch (hermetic builds, faster startup), carved into link groups by the 2 GiB relocation barrier. Some also load an in-house RDMA-based collective library for MTIA. The colliding ingredient: symbols backing that library's device state existed both statically inside the executable and in a dynamically linked library. The library's constructor populated one copy; device discovery read the other.

## The mechanism, briefly

Readers here know the pieces; the failure is their composition. The ELF gABI forbids duplicate `STB_GLOBAL` definitions only among objects entering one link, and a `DT_NEEDED` DSO's definitions never enter the executable's link, so a strong C symbol defined in both places produces two live copies with no diagnostic. At run time the executable is first in the global lookup scope, so every dynamically resolved reference — including, in a default build, the DSO's references to its own functions — binds to the executable's copy: ordinary interposition, and the process stays coherent on one winner.

The split needs one more ingredient: a DSO that self-binds. `-Bsymbolic-functions` (or protected visibility) resolves the DSO's internal calls at link time, so its constructor now writes *its* copy of the state while every other module still reads the executable's copy. Two copies of one library's state, live in one process, with the reference graph silently partitioned between them — call it split-state linking. Note that `-Bsymbolic-functions`, unlike full `-Bsymbolic` with its `DF_SYMBOLIC` flag, leaves no trace in the output binary.

## The reproducer

The claim is checkable in minutes with soft-RoCE (`rdma_rxe`): two virtual RDMA devices, a "verbs" library present as both a static copy in the executable and a shared object, and a collective that performs discovery. The harness builds the scenario six ways and prints the address of the table the constructor wrote next to the address discovery read (gcc 13.3.0, binutils 2.42, Ubuntu 24.04; reproduced on aarch64 and x86_64, and re-validated on a clean instance from the scripts alone).

| config | variation | result |
|---|---|---|
| A | default flags | no split (DSO's constructor is itself interposed onto the exe copy) |
| B | DSO built `-Bsymbolic-functions` | **SPLIT** — constructor writes DSO copy, discovery reads exe copy, "device not found" |
| C | protected visibility | **SPLIT** |
| C′ | hidden visibility | DSO dropped by `--as-needed` (constructor never runs); forced load → SPLIT |
| D1 | data table: static in DSO, global in exe | **SPLIT** |
| D2 | data table: global in both | no split (copy relocation unifies onto the exe copy) |

The splitting run, verbatim:

```
[register -> copy=SHARED table@0xffff90340028] now holds 2 device(s)
[get_list <- copy=STATIC table@0xaaaad8f00018] this copy holds 0 device(s)
collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
```

Config A is the concession that duplicates alone are benign; config D2 shows the toolchain rescuing exactly the "obvious" version of the bug (a duplicated data global) while the function-shaped version slips through. Building the executable twice from byte-identical source, with and without the redundant static copy on the link line, yields one binary that finds two devices and one that finds zero — the production incident's "same commit, different binaries" signature.

## Why no diagnostic fires

Everything behaved to spec, which is the problem. lld's documentation describes two links that "both succeed but they have selected different objects from different archives that both define the same symbols" without alarm. The opt-in diagnostics each miss the class: `--warn-backrefs` is about archive order; gold's `--detect-odr-violations` wants C++ mangled names, weak definitions, and DWARF; `-z muldefs` governs intra-link duplicates only. C has no ODR and no COMDAT for these symbols, so nothing verifies the two definitions even match.

## Fixes: one folklore failure, three verified successes

The folklore fix — `-fvisibility=hidden` on the DSO, or a `local: *` version script — verifiably does not work: it hides the wrong copy (the executable's copy is the one others bind to), and can additionally get the DSO dropped by `--as-needed` so its constructor never runs. What works, each verified against the reproducer: keep a single canonical copy; link the executable `-Wl,--exclude-libs,ALL` so the static copy stops being dynamically exported; or rename one side with `objcopy --redefine-sym`. The last is already shipping practice: Meta's public torchcomms repository carries a `rename_symbols.sh` prefixing every `nccl*` symbol to avoid conflicting with the `nccl*` bundled in PyTorch.

## Two routes, and what a survey found

Split-state has a second route that needs no self-binding at all. **Route A** (above) is interposition capture within a shared scope. **Route B** is scope partition: two modules dlopen'd `RTLD_LOCAL` — Python's default for extension modules — each vendoring its own copy of a runtime library, each binding its own copy. Same two-copies-of-one-state disease, no special flag.

Measuring either honestly requires a binding simulator, not a duplicate lister: a sweep of 788 stock system binaries found 468 with cross-boundary duplicate symbols and zero actual splits (bash's own `getenv` over libc's, weak libc aliases, disjoint symbol versions — all benign for concrete, ELF-visible reasons). `symsplit`, built for this survey, models ld.so lookup — `.dynsym`/`.symtab` visibility, scope order, versioning, and per-DSO self-binding inferred from relocations (a DSO retaining an interposable `JUMP_SLOT`/`GLOB_DAT` to one of its own exports demonstrably did not self-bind) — and flags only genuinely divergent resolution. It flags exactly config B of the matrix, clears the rest, and produced zero false positives on the 788-binary sweep.

Against manylinux ML wheels:

- **Route B is live.** Importing faiss, scikit-learn, and torch into one process maps two distinct builds each of libgomp, libgfortran, and libquadmath (`/proc/self/maps`); numpy plus scipy maps two libgfortran and two libquadmath. Under a real compute workload traced with `LD_DEBUG=bindings`, 206 duplicated compute symbols bound to two different definitions at once — nearly all OpenBLAS kernels, faiss's statically embedded copy answering faiss while numpy's libopenblas answered numpy. The ecosystem knows a fragment of this as the "multiple OpenMP runtimes" problem and ships a kill-switch, Intel's `KMP_DUPLICATE_LIB_OK`, whose suppressed error warns of incorrect results.
- **Route A's trigger is absent from public wheels** — `DF_SYMBOLIC` on zero of 366 libraries examined, zero predicted splits across eight co-load configurations — because it lives in monorepo static-link builds that cannot be scanned from PyPI, which is part of why the class went undiagnosed. But its promoting ingredient ships everywhere: `import torch` runs `ctypes.CDLL("libtorch_global_deps.so", RTLD_GLOBAL)`, lifting torch's OpenMP into the global scope; an `LD_DEBUG` probe shows faiss's OpenMP references binding faiss's bundled libgomp when faiss is imported alone and rebinding to torch's copy when torch is imported first. Which copy a library gets is decided by import order.

The ecosystem's existing mitigations — `KMP_DUPLICATE_LIB_OK`, auditwheel's content-hashed sonames (which let copies coexist rather than collide), torchcomms' symbol renaming, conda's one-copy-per-environment rule — are four unlabeled patches for the same disease.

## Toward a diagnostic

`--warn-interposition` was floated on the GCC list in May 2021 and never implemented. Fangrui Song, scoping the equivalent lld check, identified the blocker: "in the absence of an ignore list mechanism, this extension will not be useful." The scanner's allowlist, benign-pattern filtering, and self-binding inference are that mechanism, with a zero-false-positive record to argue the point. The open question for linker maintainers is whether an opt-in, allowlist-first form of the check (a working name: warn when the output dynamically exports a strong default-visibility symbol also defined strong and default in a `DT_NEEDED` DSO, escalating when that DSO self-binds) belongs in lld or BFD ld — and if the answer is that this is not the linker's job, the standalone scanner already exists.

---

## Fact/citation appendix (for Deep's rewrite)

- Reproducer + six-config address-proof matrix: `demo/rdma-symbol-collision/local/split-state/` (`artifacts/01_matrix.txt`, `05_nondeterminism.txt`, `06_fix_ladder.txt`); real soft-RoCE variant in `ec2/split-state/artifacts/`.
- Scanner + 788-binary sweep: `tools/symsplit/` (`NOTES-system-sweep.md`: 788 scanned, 468 with duplicate findings, 0 SPLIT).
- Survey: `demo/rdma-symbol-collision/survey/full/results/` — `routeB_copycount.json` (two libgomp/libgfortran/libquadmath builds mapped), `tier3_workload.json` (206 split-confirmed symbols under workload), `routeA_scope_promotion.json` (import-order rebinding), `selfbind_hunt.json` (DF_SYMBOLIC 0/366), `ladder.md` (0 predicted SPLITs across 8 co-load units).
- lld `--warn-backrefs` doc quote: https://lld.llvm.org/ELF/warn_backrefs.html
- gold ODR-detection scope: https://maskray.me/blog/2022-11-13-odr-violation-detection
- `--warn-interposition` (gcc@, May 2021): https://gcc.gnu.org/pipermail/gcc/2021-May/236063.html via https://maskray.me/blog/2021-05-16-elf-interposition-and-bsymbolic
- MaskRay ignore-list quote (2023): https://maskray.me/blog/2023-10-31-dso-undef-and-non-exported-definition
- torchcomms `rename_symbols.sh`: [pin commit permalink]
- trofi, "How do shared library collisions break?": https://trofi.github.io/posts/248-how-do-shared-library-collisions-break.html
