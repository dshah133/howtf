# LWN pitch email (DRAFT — Deep sends from his own address, in his own words)

> Status: draft. A pitch, not the article. Send to lwn@lwn.net (or the address on lwn.net/op/AuthorGuide.lwn). LWN wants a short pitch first, then writes on editor interest. LWN bans LLM-drafted prose — the pitch and the eventual article must be your own composition; treat this as talking points to rephrase, not text to paste.

**Subject:** Article pitch: the silent linker failure class behind a training-cluster "device not found"

**Body (~250 words — rewrite in your voice):**

Hi —

I'd like to pitch a technical article on a static-linking failure mode that produces wrong answers instead of crashes, and that no linker diagnostic catches by default.

The short version: a strong, default-visibility C symbol defined on both sides of the static/dynamic boundary — once in the executable, once in a shared library the process also loads — gives you two live copies of that symbol's state. If the shared library was built to self-bind (`-Bsymbolic-functions`, common as a hardening flag), its constructor populates one copy while the rest of the program reads the other. First-definition-wins, C has no ODR, and nothing warns you.

I hit this in production on a large ML training system: an accelerator's collective-communication library reported "device not found" for a device that was demonstrably present — on some binaries but not others built from the identical source commit. The bug was a split between two copies of a device table.

What I think makes this worth an article for your readers: (1) a hardware reproducer (soft-RoCE) with a four-configuration matrix that pins exactly when the split does and does not happen — including the cases where it *doesn't* (a default build; a plain data global, which copy relocation saves); (2) the honest fix result — `-fvisibility=hidden` does *not* fix it, only symbol renaming does, which converges with what the ML ecosystem already ships (Meta's public torchcomms rename script); and (3) a standalone scanner that predicts the split, tied to the 2021 `--warn-interposition` idea that was floated and never built.

I can target ~2,000 words. Background on me: [ex-Meta training infra, ex-VMware VMM, Linux kernel PTP]. Happy to send an outline.

Thanks,
Deep
