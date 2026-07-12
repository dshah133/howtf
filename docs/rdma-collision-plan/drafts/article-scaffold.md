# "Split-state linking" — article master scaffold

> Canonical version = the ~4k-word blog post. LWN = a ~2k mechanism-forward compression (YOUR prose only). CACM/Queue = ~6k, inverted (failure class first, incident as case study). Authorship tags: **[DEEP]** needs your firsthand braindump; **[DRAFT]** I can fully draft from mechanism/repro/facts; **[MIX]** drafted skeleton + your color. Provenance rule for every version: the soft-RoCE reproducer is the narrative spine; the production incident is told at one level of abstraction. (You chose to name Meta — keep that to your bio/plain statement; still no internal codename for the accelerator library beyond "an in-house accelerator collective-communication library.")

## Working titles
- Blog (howtf-question form): **"howtf can a device be both present and not found?"**
- LWN register: "When a strong symbol is defined on both sides of the boundary" / "Split-state linking"
- CACM: "Split-State Linking: A Silent Failure Class at the Static/Dynamic Boundary"

Name to lock (Deep's call): the failure class — candidate **"split-state linking"** (ties to the repro; cleaner than "symbol capture," which has prior interposition use).

---

## Section map

### 1. Cold open — the impossible symptom  **[DEEP]**
Beat skeleton (what it must do, no prose to keep):
- Put the reader inside the moment of "that can't happen" BEFORE any ELF word appears.
- The artifact: a training job that dies at startup with the accelerator collective reporting the device missing — while the device is demonstrably present (it's in the enumeration, the driver's loaded).
- The paradox that makes it uncanny: same source commit, some binaries fine, some not.
Question prompts (your recall → your prose): What did the job literally print? Where did you first see it (Slack, a dashboard, a failed run)? What made you sure the device was actually there?

### 2. The investigation — dead ends that teach  **[DEEP]**
Beat skeleton:
- 2–3 curated wrong turns, each ruling out a suspect the reader would also suspect (firmware? driver/kernel? a flaky host? enumeration order? image drift?).
- The observation that broke it open: the working-vs-broken pattern tracked the *binary*, not the machine or the source.
Question prompts: What got blamed first, and how was each ruled out? Roughly how long did each dead end last? Who noticed the binary correlation? How many people were pulled in, how urgent?

### 3. What was actually happening — the mechanism  **[DRAFT]**
(Fully draftable; below is the real draft, edit into voice.)

> Two things were true at once, and they should not have been. The library's constructor had run — it had enumerated the devices and written them into its table. And the discovery call, moments later, found that table empty. Both were reading a global named the same thing. They were not reading the same global.
>
> A dynamically linked program is assembled from modules — the executable and the shared libraries it loads — and the loader resolves each name to exactly one definition per lookup. But "one definition per lookup" is not "one definition." If a strong, non-weak C symbol is defined in two modules, both definitions exist in the image; which one a given reference binds to depends on where that reference is and how its module was built. C has no one-definition rule to forbid this, and the linker raises no error, because the two definitions never enter the same link: one is compiled into the executable, the other lives in a shared library, and a shared library's definition never collides at link time with the executable's.
>
> Here the device table — and the functions that filled and read it — existed in two places: statically inside the application binary, and inside the shared collective library. The shared library had been built to bind its own internal references to its own definitions (a common hardening choice; more on the exact flag below). So its constructor wrote *its* copy of the table. But the code that performed device discovery resolved the same name, under the default rules, to the executable's copy — which nothing had filled. The program was split into two halves that agreed on every symbol name and disagreed on every symbol's contents.

Figure: the two-copy split (sections → adapt fig. from the plan doc; entity colors: exe copy vs .so copy, constructor writes → .so copy, discovery reads → exe copy).

### 4. Proving it — the reproducer and the gating matrix  **[DRAFT]**
- The soft-RoCE reproducer: two rxe devices, a shared "verbs" library and a statically-linked copy, a dynamic collective; runnable in minutes.
- The four-configuration matrix, address-proven (constructor's write address vs discovery's read address):
  - **A (default):** same address — NO split. (Concede the referee's point: the DSO's own constructor is itself interposed onto the executable's copy.)
  - **B (`-Bsymbolic-functions`):** different addresses — SPLIT, 0 devices. The canonical trigger.
  - **C (protected visibility):** equivalent trigger — SPLIT.
  - **D data-global, global-in-both:** copy relocation gives everyone the exe's copy — NO split. *The "obvious" version is the one the linker saves you from.*
  - **D' data-global, static-in-DSO:** SPLIT (different copies).
- Nondeterminism: two binaries from byte-identical source, differing only in whether the redundant static copy is on the link line — one finds the device, one doesn't.
Fact pack: `demo/rdma-symbol-collision/local/split-state/` `make matrix`; artifacts `01_matrix.txt` (address proof), `02_ld_debug_bindings.txt`; EC2 mirror `ec2/split-state/artifacts/02_matrix.txt`.

### 5. Why nothing warned — "the failure that doesn't fail"  **[DRAFT]**
- Every component behaved exactly to spec. gABI: the link editor forbids multiple `STB_GLOBAL` definitions only among objects that *enter the link*; a shared library's definition doesn't. GNU ld manual: archives are searched once, left to right, members pulled lazily. lld's own docs: two links can "both succeed but they have selected different objects from different archives that both define the same symbols."
- C has no ODR machinery (no weak/COMDAT for these). No diagnostic fires by default; the opt-in ones each miss part of this class (`--warn-backrefs` = order-dependence; gold `--detect-odr-violations` = C++/weak/DWARF).
- The thesis line: *duplicate C symbols across the static/dynamic boundary can silently split a process into two disjoint copies of the same library's state — a failure class that produces wrong answers instead of crashes, that no linker diagnostic detects, and that the ML ecosystem is already patching around without naming it.*

### 6. Fixes: the folklore one that fails, and the one that works  **[MIX]**
- Honest negatives (verified): `-fvisibility=hidden` on the DSO does NOT fix it (hides the wrong copy); a `local: *` version script does NOT fix it. These are the folklore answers, and they're wrong for this shape.
- What works (verified): a single canonical copy; `-Wl,--exclude-libs,ALL` so the executable stops exporting its static copy; `objcopy --redefine-sym` namespacing.
- The convergence: Meta's public `torchcomms/rename_symbols.sh` prefixes every `nccl*` symbol "to avoid conflicting with OSS nccl* bundled with PyTorch" — the ecosystem shipped the rename fix without naming the disease.
- [DEEP color]: which fix shipped in prod, and was it the right one or the expedient one under deadline?

### 7. Can we detect it? — the scanner + the survey  **[DRAFT — scanner half ready, survey half after Phase 2]**
Scanner (BUILT + validated, `tools/symsplit/`):
- `symsplit` is a binding *simulator*, not a duplicate lister. It reads an executable + its resolved DT_NEEDED closure and models ld.so lookup to answer "will two modules resolve this name to *different* definitions," flagging SPLIT only then.
- The credibility hook (real numbers, weave verbatim): a sweep of **788 stock binaries** found **468 with cross-boundary duplicate symbols and zero splits** — `nm | uniq -d` would have screamed 468 times; symsplit correctly cleared all of them (bash's own `getenv`/`putenv` duplicating libc's = interposable → NO-SPLIT; weak overrides → WEAK-PATTERN; versioned → VERSIONED-BENIGN; hidden → HIDDEN-BENIGN; malloc shims → ALLOWLISTED).
- The technical device worth explaining (novel + true): self-binding is a *library-level* property detected without disassembly — a DSO that keeps an interposable `JUMP_SLOT`/`GLOB_DAT` relocation to one of its own exported symbols is NOT self-binding (it can be interposed); a DSO with none is probably `-Bsymbolic-functions`. This is what lets symsplit separate config B (split) from config A (no split) when the *reader* symbol is byte-identical in both.
- Ground truth: flags exactly config B of the four-config matrix, passes A/D2/hidden clean. Honest limits stated: self-binding confidence label (`self-bound-or-unreferenced`), dlopen-scope modeling, and that config B's severity is MEDIUM because the split shows on the reader symbol while the diverging state is a file-local table.
Survey → the TWO-ROUTE TAXONOMY (this is the strong-accept upgrade; numbers TBD from Phase 2). There are two routes to the same disease — two live copies of one library's state in one process:
- **Route A — interposition capture** (the incident's shape): duplicate + a self-binding `.so` (`-Bsymbolic-functions`) + an interposing module in a *shared* scope. Predicted ≈ 0 in public wheels — the trigger lives in monorepo static-link land (Buck/Bazel + `-Bsymbolic` hardening) that you *cannot scan from PyPI*. The public evidence it exists there is Meta's `rename_symbols.sh`. Stated as an explicit limitation, this is also *why the class went undiagnosed*.
- **Route B — scope partition**: `RTLD_LOCAL` + vendored duplication *alone* — no self-binding, no interposition. Module X binds copy 1, module Y binds copy 2, each runs its own state. **Confirmed live** in the top wheels: import faiss + scikit-learn + torch and count the mapped libgomp copies in `/proc/self/maps` — there are three. The ecosystem already knows this as the "multiple OpenMP runtimes" problem and ships a runtime kill-switch (Intel's `KMP_DUPLICATE_LIB_OK`) — a *named, loud* instance of the class that nobody connects to Route A.
- The "taxes table": the workarounds the ecosystem already pays without naming the disease — `KMP_DUPLICATE_LIB_OK`, auditwheel soname-hashing, `rename_symbols.sh`, and conda's single-copy-per-env model as the design that avoids Route B entirely.
- Report the full ladder (Tier 0 raw dupes ≫ Tier 2 predicted splits) with the honest caveat that Tier 0 ignores scope. `symsplit` detects both routes (SPLIT = Route A; SCOPE-PARTITION = Route B). The gap between "preconditions everywhere" and "the incident's trigger absent from public wheels, but its cousin live in every ML process" IS the finding.

> Correction locked in (Fable checkpoint): "versioned → benign" holds only when the two definitions carry *disjoint* version sets. Same-library duplicate copies share version nodes (`GOMP_4.0`), so versioning does NOT protect them — those stay Route-B hazards. The scanner's `VERSIONED-BENIGN` verdict was narrowed accordingly.

### 8. What should change  **[DRAFT]**
- The RFC pointer: `--warn-interposition` was proposed in 2021 and never built; MaskRay said it's easy but blocked on the ignore-list; the scanner is that ignore-list. Opt-in, allowlist-first.
- A build-engineer checklist: when you statically link, when a dep is `-Bsymbolic`, scan for cross-boundary strong-symbol dupes; prefer single-canonical-copy or namespacing.
- Close on a line echoing the cold open (device present, not found).

---

## Fact pack (verified citations — weave, don't re-derive)
- ELF gABI symtab rules: https://www.sco.com/developers/gabi/latest/ch4.symtab.html
- GNU ld manual (archive search, --whole-archive, -z muldefs): https://sourceware.org/binutils/docs/ld/Options.html
- lld --warn-backrefs (the "both succeed / different objects" quote): https://lld.llvm.org/ELF/warn_backrefs.html
- MaskRay symbol processing: https://maskray.me/blog/2021-06-20-symbol-processing
- MaskRay -Bsymbolic / --warn-interposition origin: https://maskray.me/blog/2021-05-16-elf-interposition-and-bsymbolic ; gcc@ thread: https://gcc.gnu.org/pipermail/gcc/2021-May/236063.html
- MaskRay ignore-list note (2023): https://maskray.me/blog/2023-10-31-dso-undef-and-non-exported-definition
- gold --detect-odr-violations scope: https://maskray.me/blog/2022-11-13-odr-violation-detection
- torchcomms rename_symbols.sh (ecosystem convergence): [pin commit permalink before publishing]
- trofi real-world split-state: https://trofi.github.io/posts/248-how-do-shared-library-collisions-break.html
- sqlelf (cite + differentiate): https://arxiv.org/abs/2405.03883
- Reproducer + matrix: demo/rdma-symbol-collision/ (this repo → to be published under Deep's GitHub)

## Anti-AI-tell checklist for the final prose (esp. LWN)
- Every command output is REAL, from the repro box, reproducible by the reader.
- Kill tri-colon cadence ("not just X, it's Y"), header symmetry, em-dash pile-ups, "it's worth noting."
- The [DEEP] sections carry specifics no model would invent — the actual error string, the real dead ends, wall-clock, who got paged. That is the un-fakeable signal; it must be yours.
