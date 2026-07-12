# CACM Practice outline — "Split-State Linking: A Silent Failure Class at the Static/Dynamic Boundary"

> Detailed section-by-section outline for the ~6,000-word CACM/Queue Practice version. Structure is INVERTED relative to the blog: the failure class and taxonomy lead; the Meta incident is the motivating case study; scanner design, survey method/results, and practitioner guidance form the body; the upstream RFC is the outlook. The published `symsplit` + reproducer repository is the paper's artifact. Word budgets are targets, not limits. Every claim maps to a repo artifact or fact-pack citation (noted per section).

**Anonymization/provenance rules carried over:** Meta, Buck, link groups, NCCL/NCCLX, MTIA, torchcomms all nameable; the in-house accelerator collective-communication library stays unnamed ("an in-house accelerator collective-communication library"). The reproducer is the evidentiary spine; the incident appears at one level of abstraction. Incident texture must come from Deep (reuse the blog's filled §1–§2 once he has written them; CACM needs less texture than the blog — symptom, elimination logic, binary correlation suffice).

---

## Abstract (~150 words)

- One live process, two copies of one library's state, references silently partitioned between them: define **split-state linking**.
- Wrong-answer failure class (not crashes); undetectable by any default or opt-in toolchain diagnostic; two structurally distinct routes (interposition capture; scope partition).
- Contributions: (1) a named, reproducible characterization with a six-configuration gating matrix; (2) `symsplit`, a binding simulator with a zero-false-positive record on 788 system binaries; (3) an ecosystem survey showing the class live in stock Python ML wheels; (4) a path to an opt-in linker diagnostic, unblocking a proposal stalled since 2021.

## 1. Introduction: the failure that doesn't fail (~500 words)

- Open with the paradox in class terms, not incident terms: a program whose initialization verifiably ran and whose queries verifiably return empty, both correct, both against "the same" global.
- Thesis sentence (from scaffold §5): duplicate strong C symbols across the static/dynamic boundary can silently split a process into two disjoint copies of the same library's state — a failure class that produces wrong answers instead of crashes, that no linker diagnostic detects, and that the ML ecosystem is already patching around without naming it.
- Why now: monorepo static linking (Buck/Bazel) plus vendored-wheel dynamic loading have made both routes common; the trigger flag leaves no forensic trace; the community's mitigations exist but are unlabeled and uncoordinated.
- Roadmap paragraph.

## 2. The failure class and its two routes (~800 words)

- 2.1 Background compressed for a professional audience (one paragraph each, no tutorials):
  - ELF symbol resolution: per-lookup uniqueness ≠ per-image uniqueness; executable-first global scope; interposition as a feature (LD_PRELOAD, allocators). [gABI, MaskRay symbol-processing]
  - Why no error: gABI's multiple-definition rule applies only within one link; C has no ODR/COMDAT; lld docs' "both succeed…different objects" posture. [lld --warn-backrefs doc]
- 2.2 **Route A — interposition capture.** Ingredients: duplicate strong default-visibility symbol; self-binding DSO (`-Bsymbolic`/`-Bsymbolic-functions`/protected visibility); interposing module in a shared scope. Constructor writes the DSO copy; everyone else reads the executable's. Key property: `-Bsymbolic-functions` sets no `DF_SYMBOLIC`, so the trigger is invisible in the shipped artifact.
- 2.3 **Route B — scope partition.** `RTLD_LOCAL` + vendored duplication; no self-binding or interposition required; each namespace's consumers bind their own copy. Note this is the default shape of every Python extension ecosystem.
- 2.4 The taxonomy table: route × ingredients × who ships it × existing (unlabeled) mitigation. (Figure 1: two-copy split diagram; Figure 2: route taxonomy.)
- 2.5 Related failures distinguished: archive-order misdirection (`--warn-backrefs` territory), C++ ODR (gold's detector), COMMON-symbol interposition and copy relocation as *split-avoiding* mechanisms.

## 3. Case study: present, and not found (~700 words)

- Setting (one paragraph): Meta; Buck-composed application training binaries; PyTorch statically linked for hermeticity and startup; link groups forced by the 2 GiB relocation barrier; some binaries carry MTIA support via an in-house RDMA-based collective library; NCCL/NCCLX coexists in-process.
- Symptom: same torch commit, some binaries report the MTIA device "not found" at startup while the device enumerates and NCCL sees it. Elimination logic: hardware/driver vouched for by a working in-process consumer; source vouched for by the shared commit; failures track binaries, hence the link.
- Resolution (de-hedged to the reproduced mechanism): constructors ran; for some symbols resolution landed on the statically linked copy; constructor filled one copy of device state, discovery read the other. Route A, in production.
- ⟦Deep: 2–3 sentences of firsthand texture — first dead end, duration, who spotted the binary correlation — imported from the blog's finished §1–§2, plus which fix shipped.⟧
- Why the case matters for the survey design (foreshadow §5): the trigger lives in a corporate monorepo you cannot scan from outside; public-channel scanning alone would (and does) miss Route A entirely.

## 4. Reproducing and gating the failure (~800 words)

- Reproducer design: soft-RoCE (`rdma_rxe`), two virtual RDMA devices, a "verbs" library present as static + shared copies, dynamic collective performing discovery; address identity (table@ printed by writer and reader) as the split oracle. Pinned toolchain (gcc 13.3.0, binutils 2.42, Ubuntu 24.04, kernel 6.17-aws); reproduced aarch64 + x86_64; re-validated on a clean instance from scripts alone. [artifacts 00–02]
- The six-configuration matrix (Table 1 = the blog's table: A default/no-split; B `-Bsymbolic-functions`/SPLIT; C protected/SPLIT; C′ hidden/dropped-then-SPLIT; D1 data static-in-DSO/SPLIT; D2 data global-both/no-split via copy relocation).
- Analysis beats: config A as the falsifiable concession (duplicates alone benign); D2 as the toolchain rescuing the "obvious" bug shape; nondeterminism run (byte-identical source, link line decides 2 devices vs 0) mapping directly onto the case study's per-binary signature. [05_nondeterminism.txt]
- The fix ladder as experiment, not advice (Table 2): verified negatives (`-fvisibility=hidden`, `local:*` version script — hide the wrong copy; `--as-needed` interaction) and verified positives (single copy; `--exclude-libs,ALL`; `objcopy --redefine-sym`). Ecosystem convergence: torchcomms `rename_symbols.sh`. [06_fix_ladder.txt; torchcomms permalink]

## 5. Detection: a binding simulator, not a duplicate lister (~1,100 words — the paper's technical core)

- 5.1 Why `nm | sort | uniq -d` fails as a detector: 788 stock system binaries, 468 with cross-boundary duplicates, zero splits; worked benign examples (bash `getenv` unified by libc's retained interposable reference; 2,956 weak-alias pairs; disjoint version sets; hidden/symtab-only copies; allocator allowlist). [NOTES-system-sweep.md]
- 5.2 `symsplit` model: per-(symbol, module) facts (binding, visibility, defining symbol table, full version-def sets, relocations); ld.so scope simulation (executable-first BFS over DT_NEEDED, RPATH/RUNPATH; `--module` = isolated RTLD_LOCAL group; `--module-group`/`--rtld-global` as caller-supplied scope truth).
- 5.3 The self-binding inference (novel, worth a figure): library-level, relocation-based — a DSO retaining an interposable JUMP_SLOT/GLOB_DAT to any own export provably did not self-bind; absence of all self-references = the `-Bsymbolic-functions` signature, labeled `self-bound-or-unreferenced`. Why library-level: the split is emergent across the export set (writer symbol self-bound, reader symbol interposed — neither splits alone).
- 5.4 Verdict taxonomy: SPLIT (Route A predicate, 4 conjuncts) / SCOPE-PARTITION (Route B predicate) / NO-SPLIT / WEAK-PATTERN / VERSIONED-BENIGN (narrowed: only disjoint version sets clear; identical version nodes across vendored copies stay hazards) / HIDDEN- and NOT-DYNAMIC-BENIGN / ALLOWLISTED.
- 5.5 Validation: flags exactly config B on the ground-truth matrix, clears A/D2/hidden; zero false positives on the 788-binary sweep; honest-limits paragraph (self-binding confidence label; dlopen scope as input, not inference; copy-relocation handling; reader-symbol reporting).

## 6. Survey: how prevalent, by which route (~900 words)

- 6.1 Method: manylinux ML wheels (torch, faiss, scikit-learn, numpy, scipy, onnxruntime, +12 more); three tiers — Tier 0 nm-level precondition counts (reported only as context: 8,181 cross-wheel duplicate strong symbols), Tier 1/2 `symsplit` over realistic co-load images, Tier 3 runtime `LD_DEBUG=bindings` under import and real compute workloads, plus `/proc/self/maps` copy-counting. [survey/full/*]
- 6.2 Route B results: two distinct builds each of libgomp/libgfortran/libquadmath mapped in the faiss+sklearn+torch process; numpy+scipy → two libgfortran + two libquadmath; workload trace: 206 duplicated compute symbols (dominantly OpenBLAS kernels: faiss's embedded copy vs numpy's libopenblas) bound to two definitions simultaneously. Ecosystem's partial awareness: "multiple OpenMP runtimes," `KMP_DUPLICATE_LIB_OK`. [routeB_copycount.json; tier3_workload.json]
- 6.3 Route A results (the honest negative that is itself a finding): DF_SYMBOLIC 0/366; zero predicted SPLITs across 8 co-load units; the trigger lives in unscannable monorepo builds — explaining the class's long invisibility; public evidence of its reality = torchcomms renaming. Scope promotion: `import torch`'s `ctypes.CDLL(..., RTLD_GLOBAL)` flips faiss's OpenMP bindings with import order (LD_DEBUG probe, alone-vs-torch-first). [selfbind_hunt.json; ladder.md; routeA_scope_promotion.json]
- 6.4 The "scattered tax" synthesis (Table 3): KMP_DUPLICATE_LIB_OK / auditwheel soname hashing (enables coexistence) / rename_symbols.sh / conda single-copy policy — four mitigations, one unnamed disease.

## 7. Guidance for practitioners (~500 words)

- Build-side checklist: inventory `-Bsymbolic*`/`-fno-semantic-interposition`/protected-visibility deps; scan any mixed static/dynamic image where a strong C symbol crosses the boundary; prefer single canonical copy; namespace with `--redefine-sym` when two copies are intentional; `--exclude-libs,ALL` on executables that must carry static duplicates; treat `-Bsymbolic` as a split trigger, not a fix.
- Runtime-side: know your dlopen scopes; treat `RTLD_GLOBAL` promotions (torch-style) as scope-graph edits; `KMP_DUPLICATE_LIB_OK` silences a detector, it does not fix the state duplication.
- CI integration: `symsplit` exit code as a gate; `--by-library` clustering for wheel sweeps.

## 8. Outlook: the diagnostic the toolchain almost had (~350 words)

- `--warn-interposition` (gcc@, May 2021): proposed, scoped, never built; MaskRay's documented blocker — "in the absence of an ignore list mechanism, this extension will not be useful."
- Position: the scanner is the ignore-list mechanism, with the false-positive record as evidence; proposal shape for an opt-in lld/ld flag (warn when the output dynamically exports a strong default-visibility symbol also defined in a DT_NEEDED DSO, escalate on self-binding), reusing the allowlist and FP taxonomy.
- Fallback stance: if this is not the linker's job, the standalone tool suffices — the goal is diagnosability of a silent class.
- Close by echoing the case study: "device not found" can mean the device is present in the copy of the world you didn't ask.

## Artifact statement (~100 words)

- Repository: reproducer (six-config matrix + soft-RoCE variant, containerized, pinned toolchain), `symsplit` (Apache-2.0, pyelftools-only, test suite with real-ELF fixtures + ground-truth matrix + sweep harness), survey scripts + captured JSON results. All quoted outputs regenerable via `make matrix` / `make test` / `make sweep` / survey drivers.

## Figures/tables plan

1. Fig 1 — the two-copy split (writer→DSO copy, readers→exe copy).
2. Fig 2 — route taxonomy (Route A vs Route B ingredient graphs).
3. Table 1 — six-config gating matrix.
4. Table 2 — fix ladder (verified negatives + positives).
5. Fig 3 — self-binding inference from relocations.
6. Table 3 — the ecosystem's scattered-tax mitigations.

## Notes for the CACM revision pass

- CACM register: no "howtf," measured first person plural or neutral voice; the blog's coinage paragraph compresses to a definition.
- The 8,181 Tier-0 number appears ONLY with its caveat (nm-level, scope-blind) — it is context for why the simulator exists, never a headline.
- Do not reuse the blog's or LWN's sentences; CACM wants original prose (and Deep should draft §3's texture himself, as with LWN).
