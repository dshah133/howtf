# howtf can a device be both present and not found?

> CANONICAL BLOG DRAFT (~4k target). Technical sections are drafted in the howtf.io house voice — Deep to revoice/tighten. Sections marked ⟦DEEP⟧ are scaffolds: they need your firsthand recollection (the interview) and must be written in your own words (the LWN version bans machine prose entirely; this blog version you edit into your voice). Provenance rule: the reproducer is the spine; the production incident is told at one level of abstraction. Citations are in the fact pack (article-scaffold.md); pin permalinks before publishing.

---

## ⟦DEEP — §1 Cold open⟧ *(needs your braindump; ~250 words, your voice)*

> Beat: put the reader inside "that can't happen" before any ELF word. The training job that dies at startup because the accelerator's collective-communication library reports the device missing — while the device is right there in the enumeration, driver loaded. Then the twist that makes it uncanny: same source commit, some binaries fine, some not.
>
> Your recall to write from: what the job literally printed; where you first saw it; how you knew the device was actually present. End the section on the paradox, not the explanation.

---

## ⟦DEEP — §2 The investigation⟧ *(needs your braindump; ~500 words, your voice)*

> Beat: 2–3 curated dead ends, each ruling out a suspect the reader shares (firmware? driver? a flaky host? enumeration order? image drift?), each teaching the tool that ruled it out. Then the observation that broke it open: working-vs-broken tracked the *binary*, not the machine or the source.
>
> Your recall to write from: what got blamed first and how each was ruled out; how long each dead end lasted; who noticed the binary correlation; how many people, how urgent. This section is the un-fakeable heart — the specifics are what make it yours.

---

## §3 — What was actually happening

Two things were true at once, and they should not have been. The library's constructor had run: it had walked the device list and written the results into its table. And the discovery call, a moment later, found that table empty. Both were reading a global with the same name. They were not reading the same global.

A dynamically linked program is assembled from modules — the executable and the shared libraries it loads — and the loader resolves each name to exactly one definition per lookup. But "one definition per lookup" is not "one definition." If a strong, non-weak C symbol is defined in two modules, both definitions exist in the image; which one a given reference binds to depends on where that reference lives and how its module was built. C has no one-definition rule to forbid the duplication, and the linker raises no error, because the two definitions never enter the same link: one is compiled into the executable, the other into a shared library, and a shared library's definition does not collide at link time with the executable's.

Here, the device table — and the functions that filled and read it — existed in two places: statically inside the application binary, and inside the shared collective library. The shared library had been built to bind its own internal references to its own definitions (a common hardening choice; the exact flag matters and we will name it). So its constructor wrote *its* copy of the table. The code that performed device discovery, resolving the same name under the default rules, bound to the executable's copy — which nothing had filled. The process was split into two halves that agreed on every symbol *name* and disagreed on every symbol's *contents*.

That is the whole disease, and it deserves a name, because it has none: **split-state linking** — two live copies of one library's state in a single process, with references silently partitioned between them.

*(Figure: the two-copy split — exe copy vs .so copy; constructor writes → .so copy; discovery reads → exe copy. Entity colors per DESIGN.md.)*

## §4 — Proving it

The claim above is easy to state and easy to doubt, so here is a reproducer you can run in minutes. It uses soft-RoCE (`rdma_rxe`), so no special hardware is required: two virtual RDMA devices, a "verbs" library present in two copies (one static in the executable, one shared), and a small collective that performs discovery. `make matrix` builds it four ways and prints, for each, the address the constructor wrote and the address discovery read — the split is a comparison of two hexadecimal numbers, not a matter of opinion.

The four configurations pin exactly when the split happens, and — just as importantly — when it does not:

| Config | Shared lib self-binds? | Executable interposes? | Result |
| --- | --- | --- | --- |
| **A — default** | no | yes | constructor and discovery hit the **same** address → **no split** |
| **B — `-Bsymbolic-functions`** | yes | yes | different addresses → **SPLIT**, device not found |
| **C — protected visibility** | yes | yes | equivalent trigger → **SPLIT** |
| **D — data global, defined both sides** | — | — | copy relocation gives everyone the exe's copy → **no split** |
| **D′ — data global, static-only in the .so** | — | — | separate copies → **SPLIT** |

Two of these are worth pausing on. Config **A** concedes the obvious objection: in a default build there is no split, because the shared library's own constructor is *also* subject to interposition and writes the executable's copy — everyone agrees. The split needs the library to bind its own references internally, and the common way that happens is `-Bsymbolic-functions`, a flag frequently applied for performance and hardening; it leaves *no flag in the binary*, which is why you cannot grep for it. Config **D** is the trap turned inside out: the "obvious" version of this bug, where the duplicated thing is a plain data global, does *not* split — copy relocation quietly unifies everyone on the executable's copy. The dangerous shape is specifically a *function* (or a table reached through one) in a *self-binding* library. The reproducer also shows the nondeterminism directly: two binaries built from byte-identical source, differing only in whether the redundant static copy appears on the link line, one finds the device and one does not.

## §5 — Why nothing warned

The uncomfortable part is that every component behaved exactly to spec. The ELF gABI forbids multiple `STB_GLOBAL` definitions only among the objects that *enter a link* — and a shared library's definition never enters the executable's link. The GNU `ld` manual describes archive members being pulled lazily, left to right, once. lld's own documentation states the situation without alarm: two links can "both succeed but they have selected different objects from different archives that both define the same symbols." C has no one-definition rule and no COMDAT machinery for these symbols; there is nothing to check that the two definitions are even the same. So no diagnostic fires by default, and the opt-in diagnostics that exist each miss this class: `--warn-backrefs` catches order-dependent archive resolution, not cross-boundary duplication; gold's `--detect-odr-violations` is scoped to C++ mangled names, weak/COMDAT definitions, and requires debug info. The failure that produces a crash gets a stack trace. The failure that produces a wrong answer gets silence.

## §6 — ⟦MIX — Fixes: the folklore one that fails, and the one that works⟧

The instinct, when you learn two copies of a symbol are colliding, is to reach for visibility: rebuild the shared library with `-fvisibility=hidden` and the duplicate should stop being exported. It does not fix this. Verified against the reproducer, `-fvisibility=hidden` on the shared library and a `local: *` version script both leave the split in place — they hide the *wrong* copy. Hidden visibility controls what a library *exports*; it does nothing about the executable's copy that discovery was already binding to.

What works, verified, is to stop the two copies from being the same symbol: keep a single canonical copy; or link the executable with `-Wl,--exclude-libs,ALL` so it stops exporting its static copy; or rename one library's symbols with `objcopy --redefine-sym`. That last one is not hypothetical — it is what the ecosystem already does. Meta's public `torchcomms` repository ships a `rename_symbols.sh` that prefixes every `nccl*` symbol, with a comment stating it exists "to avoid conflicting with OSS nccl\* that is bundled with PyTorch." The fix shipped years before the disease had a name.

> ⟦DEEP color, ~1–2 sentences⟧: which fix shipped in your incident, and was it the right one or the expedient one under deadline?

## §7 — How common is this, really? Two routes, and a survey

If the trigger is a hardening flag that leaves no trace in the binary, you cannot answer "how common is this?" by grepping. But you can reason about it, and then measure it — and doing so reveals that split-state linking arrives by *two* routes, not one.

**Route A — interposition capture** is the incident's shape: a duplicate, a self-binding library, and an interposing module sharing one symbol scope. **Route B — scope partition** needs neither self-binding nor interposition: if two modules are loaded into separate local scopes (`RTLD_LOCAL`, the default for Python extension modules) and each carries its own vendored copy of a library, each binds to its own copy and runs its own state. Same disease — two live copies of one library's state — reached without any special flag at all.

To measure both, we built `symsplit`, a binding *simulator* rather than a duplicate lister. `nm | sort | uniq -d` answers "does a duplicate exist"; on a sweep of 788 stock system binaries, 468 had cross-boundary duplicates and *none* were splits. `symsplit` models what the loader actually does — `.dynsym` versus `.symtab` visibility, scope order, symbol versioning, and library-level self-binding detected from relocations (a library that keeps an interposable `JUMP_SLOT`/`GLOB_DAT` reference to its own exported symbol is *not* self-binding; one with none probably is) — and reports a split only when two modules would genuinely resolve one name to different definitions. Against the four-config reproducer it flags exactly the splitting configuration and clears the other three; against those 788 system binaries it reported zero false positives.

Pointed at the manylinux ML-wheel ecosystem, the picture is sharp:

- **Route B is live in stock wheels.** Import faiss, scikit-learn, and torch into one Python process and `/proc/self/maps` shows two distinct builds each of libgomp, libgfortran, and libquadmath — two OpenMP runtimes, two Fortran runtimes, each with its own global state, resident in the same process. numpy and scipy together map two libgfortran and two libquadmath. This is not obscure: the ecosystem already knows it as the "multiple OpenMP runtimes" problem and ships a runtime kill-switch for it — Intel's `KMP_DUPLICATE_LIB_OK`, which silences an error that warns the duplication "can cause incorrect results."
- **Route A's exact trigger is absent from public wheels** — `DF_SYMBOLIC` is set on zero of 366 libraries examined — because it lives in monorepo static-link builds (Buck/Bazel with symbolic-binding hardening) that you cannot download from PyPI. That inaccessibility is part of why the class went undiagnosed. **But** its scope-promotion variant reproduces in stock wheels: `import torch` runs `ctypes.CDLL("libtorch_global_deps.so", RTLD_GLOBAL)`, promoting torch's OpenMP into the global scope, and an `LD_DEBUG` trace shows faiss's OpenMP references — which bind to faiss's own copy when faiss is imported alone — rebinding to torch's copy once torch is imported first. In the three-library process, 127 references bind torch's copy and 2 bind faiss's. The reference graph splits, live, in software everyone runs.

The honest shape of the finding is the whole point: the preconditions are everywhere, the full alignment is scope-dependent and rarer, and *nothing warns you at any tier*. The ecosystem survives not because the toolchain protects it but because it pays a scattered tax — `KMP_DUPLICATE_LIB_OK`, auditwheel's content-hashed sonames (which *enable* coexistence rather than prevent it), `rename_symbols.sh`, conda's single-copy-per-environment discipline — none of which is labeled as treating the same disease.

## §8 — What should change

The diagnosis nobody built already has a name in the record: `--warn-interposition` was proposed on the GCC list in 2021 and never implemented, and the reason it stalled is documented — the maintainer who scoped it noted the general check is easy but that, "in the absence of an ignore list mechanism, this extension will not be useful," because interposition is a load-bearing feature and the base rate of benign duplication is high. That is precisely the gap `symsplit` fills: it *is* the ignore-list mechanism — the allowlist for intentional interposers, the weak/COMDAT/versioned/visibility filtering, the self-binding awareness — demonstrated against real binaries with a zero false-positive record on a 788-binary sweep. The tool can live standalone today; the open question worth putting to the linker maintainers is whether an opt-in, allowlist-first version belongs in `lld` or `ld` as well.

Until then, the checklist for anyone shipping large statically-or-mixed-linked binaries: when you link a dependency built `-Bsymbolic`, and a strong C symbol it defines also exists elsewhere in your image, you have a latent split-state hazard that no tool will flag by default. Scan for it. Prefer a single canonical copy or explicit namespacing. And know that "the device isn't there" can mean "the device is there, and you are asking the wrong copy."

---

## Length cuts (after this canonical draft + Deep's §1/§2/§6 are final)
- **LWN ~2k:** compress §3–§6 for an ELF-literate audience (one-sentence gABI/ld refreshers, not paragraphs); keep the reproducer table, the negative fix result, and the two-route survey; §7 tightened to the torch/`RTLD_GLOBAL` hit + the taxes list. **All prose Deep's own.**
- **CACM ~6k:** invert — open with the split-state failure class and the two-route taxonomy, the incident becomes the motivating case study, scanner design + survey + guidance are the body, the RFC is the outlook. Ship the scanner repo as the artifact.
