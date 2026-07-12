# Linux Plumbers 2026 — Toolchains MC — proposal (DRAFT for Deep to rewrite/submit)

> Status: draft. Not submitted. Submit under Deep's own account before the CFP deadline (July 24, 2026). Rewrite the prose in your own voice; this is scaffolding + the facts.

**Working title:** Split-state linking: a strong C symbol defined on both sides of the static/dynamic boundary, and whether the toolchain should say so

**Track:** Toolchains microconference (linker/ELF). Format: 30-min problem-discussion slot.

**Proposal (the pitch, ~250 words — rewrite in your voice):**

Static ELF linking has a silent failure mode that produces wrong answers rather than crashes, and no linker diagnostic fires for it by default. When a strong, default-visibility, non-weak C symbol is defined on *both* sides of the static/dynamic boundary — once in the executable (or a static archive linked into it) and once in a shared library the process also loads — and that shared library was built to bind its own references internally (`-Bsymbolic-functions`, or `-fno-semantic-interposition`, or protected visibility), the process ends up with two live copies of that symbol's state. The DSO's constructor populates one copy; code elsewhere in the process reads the other. First-definition-wins across the boundary, C has no ODR enforcement, and the result is a component that behaves exactly to spec while the composition silently lies.

We hit this in production in a large ML training system: an accelerator's collective-communication library reported "device not found" for a device that was demonstrably present, on some application binaries but not others built from the identical source commit. Root cause was this split.

This session brings: (1) a hardware-reproduced, four-configuration gating matrix (soft-RoCE) that pins exactly when the split occurs and — importantly — when it does not (a default build does not split; a plain data global does not split, because copy relocation saves you; the function + self-binding case does); (2) a standalone binding-simulator scanner that models `.dynsym`-vs-`.symtab` visibility, ld.so scope order, symbol versioning, and per-symbol self-binding to predict split state, with a curated false-positive allowlist; and (3) prevalence data from a survey of the manylinux ML-wheel ecosystem.

The question for the room: `--warn-interposition` was proposed on the gcc list in 2021 and never built; the extension has been described as easy but blocked on false-positive volume and the absence of an ignore-list mechanism. If the scanner *is* that ignore-list mechanism, should an opt-in diagnostic live in lld/ld — and what would its default-off, allowlist-first shape be?

**Why this fits Toolchains:** it is a linker-semantics discussion with a reproducer and data, aimed squarely at the lld/binutils maintainers, and it revives a proposal they already floated.

**Prior discussion to cite:** the 2021 gcc@ `--warn-interposition` idea; MaskRay's 2023 note that the general check is easy but needs an ignore list; gold's `--detect-odr-violations` (C++/weak/DWARF-scoped — does not cover the C strong case); lld `--warn-backrefs` (order-dependence, not cross-boundary duplication).

**Speaker:** Deep Shah — [bio: ex-Meta training infra, ex-VMware VMM, Linux kernel PTP]. (Deep: confirm attendance — Prague, Oct 5–7, talks recorded.)
