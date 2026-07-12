# RFC draft — opt-in split-state / interposition diagnostic (LLVM Discourse, linker category)

> Status: DRAFT for Deep's review and rewrite. NOT posted. Do not post to LLVM Discourse, gcc@, binutils@, or any mailing list without Deep's explicit approval of the exact text (external-comms rules). Two facts must be verified before filing (see "Verify first" at the end).

**Target:** LLVM Discourse → Community / linker (lld). Rationale: modern review culture; MaskRay (Fangrui Song, lld maintainer) has already scoped this exact extension in public and is the natural reviewer. binutils second, referencing the Discourse thread.

**Framing rule (load-bearing):** Do NOT pitch "linkers should warn on interposition by default." That walks into MaskRay's documented objection and dies. Pitch: a standalone scanner is the primary deliverable *because* the linker can't carry the false-positive machinery; an opt-in lld flag that reuses the scanner's allowlist is the upstream conversation. Quote MaskRay's own "in the absence of an ignore list mechanism, this extension will not be useful" and answer: right — the scanner is that mechanism.

---

## Title: A scanner (and maybe an opt-in flag) for split-state linking: strong C symbols defined across the static/dynamic boundary

### The problem, in one paragraph
A strong, `STB_GLOBAL`, `STV_DEFAULT`, non-weak, unversioned C symbol defined in *both* the executable (or a static archive linked into it) *and* a `DT_NEEDED` shared library produces two live copies in the process image. Under default global-scope resolution the executable's copy interposes for other modules — but if the defining DSO was built to self-bind (`-Bsymbolic`/`-Bsymbolic-functions`, `-fno-semantic-interposition`, or protected visibility), the DSO uses its *own* copy while everyone else uses the executable's. When the symbol backs mutable state (a registry, a device table, a cache), the two halves of the program diverge silently: a constructor populates one copy, lookups read the other. No `multiple definition` error fires — the DSO's definition never enters the same link as the executable's. This is a wrong-answer failure class, not a crash, and nothing in the default toolchain flags it. Reproducer: [link to repo — the four-config soft-RoCE matrix].

### Why existing diagnostics don't cover it
- **gold `--detect-odr-violations`**: collects only `_Z`-prefixed (C++ mangled) names, compares two `STB_WEAK` defs by `st_size`/`st_type`, and requires DWARF line tables. Triple-scoped away from a plain C strong symbol. (MaskRay, ODR-violation-detection, 2022.)
- **lld `--warn-backrefs`**: flags order-dependent archive resolution (a reference satisfied by an archive to its left); the docs explicitly call a duplicate-in-another-archive "redundant but benign." Not this case.
- **`--allow-multiple-definition` / `-z muldefs`**: suppress the intra-link duplicate error; they don't detect the cross-DSO case (which never errors in the first place).
- **`--no-allow-shlib-undefined`**: undefined, not doubly-defined. lld's existing narrow check here already handles the non-exported/GC'd-local case; MaskRay noted (2023) it "can be extended to default visibility to catch all link-time symbol interposition [but] I suspect there are a lot of benign violations and in the absence of an ignore list mechanism, this extension will not be useful."

### Prior proposal
`--warn-interposition` was floated on gcc@ in May 2021 (Peter Smith, via MaskRay's -Bsymbolic writeup): "Warning symbol S of type STT_FUNC is defined in executable A and shared objects B and C, using definition in A." Never implemented in ld or lld; never formally rejected. This RFC is that idea, finished, plus the ignore-list that was identified as the blocker — and extended from the STT_FUNC runtime-interposition framing to the strong-object split-state correctness case.

### The proposal
1. **Primary: a standalone scanner** (`symsplit`, Apache-2.0, pyelftools — BUILT) that models the binding, not just the duplicate: `.dynsym` vs `.symtab` visibility asymmetry, ld.so scope order incl. dlopen locality, symbol versioning, the weak-override idiom, and library-level self-binding detection (DF_SYMBOLIC, or absence of any interposable JUMP_SLOT/GLOB_DAT reloc to the DSO's own exported symbols), with a curated allowlist for intentional interposers (malloc family, operator new/delete, sanitizers, pthread shims). Validated: it flags exactly the one splitting config of a hardware-reproduced four-config ground-truth matrix and passes the other three; and in a sweep of **788 stock system binaries — 468 with cross-boundary duplicate symbols — it reported zero splits and zero unadjudicated flags.** That false-positive record is the concrete evidence that the ignore-list machinery MaskRay said was the prerequisite can, in fact, be built.
2. **Discussion: an opt-in lld flag**, working name `--warn-shadowed-dso-definition` (default off), fired at executable link time when the output dynamically exports a strong default-visibility symbol also defined strong+default in a `DT_NEEDED` DSO, optionally escalating when that DSO is `DF_SYMBOLIC`. It reuses the scanner's allowlist and FP taxonomy from day one. Cost is low: the linker already reads DSO symbol tables for undefined-symbol resolution.

### The false-positive discussion (pre-empting the objection)
Interposition is a load-bearing ELF feature, so the base rate of benign "defined in both" is high. The warning must be opt-in and allowlist-first. Benign cases the scanner already filters: weak/COMDAT (scoped out — `STB_GLOBAL` non-weak only); intentional `LD_PRELOAD`/malloc/sanitizer interposition (allowlist); vendored-copy duplication where the DSO is *not* self-binding (no split predicted); `-Bsymbolic`/`-fno-semantic-interposition` where the sides are decoupled *on purpose* (recorded, not auto-flagged); versioned symbols (distinct); hidden/protected visibility (can't interpose / self-binds).

### Fallback position (stated in the RFC itself)
If consensus is "this isn't the linker's job," the standalone scanner is the answer, and it exists today. Either outcome is fine — the goal is diagnosability of a silent class, by whatever tool.

### Motivation data (from the ecosystem survey — see repo `survey/full/`)
The class has two routes, and both are present in stock PyPI ML wheels:
- **Route B (scope partition), measured live:** co-importing faiss + scikit-learn + torch maps **two distinct builds each of libgomp, libgfortran, and libquadmath** into one process (`/proc/self/maps`); numpy + scipy maps two libgfortran + two libquadmath. Two copies of a runtime's global state, silently coexisting. The ecosystem already manages this by hand (Intel `KMP_DUPLICATE_LIB_OK`, auditwheel soname-hashing, conda single-copy-per-env) without a name for it.
- **Route A (interposition capture), reproduced:** the `-Bsymbolic` trigger is absent from public wheels (DF_SYMBOLIC = 0 / 366 libs — it lives in monorepo static-link builds), but `import torch` runs `ctypes.CDLL(libtorch_global_deps.so, RTLD_GLOBAL)`, and an `LD_DEBUG` trace shows faiss's OpenMP references rebinding from faiss's libgomp to torch's once torch is imported (127:2 in the trio). A stock-PyPI reproduction of the incident's mechanism.
The honest point for the RFC: preconditions are everywhere, the full alignment is rarer and scope-dependent, and *nothing warns you at any tier*. An opt-in, allowlist-first diagnostic is exactly the missing tool.

---

## Verified (2026-07-12)
1. **No filed request found** — a sourceware/binutils search surfaces only ld docs and general interposition discussion (incl. the COMMON-symbol interposition note where the exe's larger object wins — a *split-avoiding* case, like copy relocation for data); no filed Bugzilla issue for the cross-boundary strong-C-symbol warning. Only the 2021 gcc@ `--warn-interposition` idea + MaskRay's blog note exist. (Do one more direct search of the llvm `lld:ELF` tracker right before filing — absence isn't proof — but the position holds.)
2. **auditwheel / lintian confirmed non-overlapping** — auditwheel does symbol-*version* validation + a library whitelist (manylinux policy), not cross-boundary duplicate-symbol detection; lintian's `duplicate-files` is file-level, not symbol-level. Safe to cite as "adjacent, not overlapping."

## Sources
gcc@ 2021 `--warn-interposition` thread · MaskRay: ELF interposition and -Bsymbolic (2021), ODR-violation detection (2022), DSO undef and non-exported definition (2023) · lld `--warn-backrefs` docs · sqlelf (arXiv 2405.03883 — cite + differentiate: general SQL-over-ELF, not a purpose-built split-state detector) · adobe/orc (macOS C++ ODR scanner) · libabigail/abidiff · trofi "How do shared library collisions break?" (real-world split-state, "the toolchain does not help much").
