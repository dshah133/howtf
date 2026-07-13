---
title: "howtf can a device be both present and not found?"
description: "A recurring production SEV, a device that was present and not found, and the 1970s linker rule that split one library's state into two — Part 2 of Linking & Loading."
date: 2026-07-13
series:
  name: "Linking & Loading"
  part: 2
tags: [linker, elf, rdma, static-linking]
featured: true
draft: false
---

> **Linking & Loading, Part 2.** Part 1 — [*howtf does `./app` reach `main()`?*](/blog/ELF-Linking-101/) — traced the machinery: ELF, `ld.so`, symbol resolution, the PLT/GOT, relocations, interposition. This one is about what that machinery does to you when the same symbol exists twice.

---

## 1. Present, and not found

The failure that starts this story showed up the way these things do: in the logs and on the dashboards, as training jobs started crashing at startup. The line that mattered was the RDMA stack's classic no-device error — the moral equivalent of `ibv_get_device_list()` coming back empty:

```text
No IB devices found
```

Paste that string into a search engine and every hit is hardware troubleshooting. Check the cable. Check the firmware. Check that the driver is loaded. Which is exactly the rabbit hole it aims you down, because everything about that error says *the machine*, and nothing about it says *the binary*.

The device was not missing. It showed up in enumeration. The driver was loaded. On the same host, in the same kind of process, NCCL could see the very RDMA devices the failing library claimed didn't exist.

Some context, because the shape of the build matters later. This was at Meta. The binaries were application training binaries composed by Buck, with PyTorch built in-house and statically linked — hermetic builds and fast startup are worth a great deal at that scale. "Composed by Buck" is doing real work in that sentence, and the public tooling documents the backbone of what it means. A Python training program pulls in an enormous amount of native code — torch and everything under it — and not all of it can be statically compiled into one executable, so Buck's [omnibus](https://buck.build/javadoc/com/facebook/buck/cxx/Omnibus.html) strategy does the next-best merge: statically link most of the native code "into a single giant shared library" — a `libomnibus.so` — leaving only the extensions Python imports directly as separate .so's. You pay the full static-link cost once, at build time; at runtime the binary `dlopen`s roughly one big library instead of hundreds. Meta had written down the hazard of merging back in [2018](https://engineering.fb.com/2018/01/23/android/android-native-library-merging/), in the Android sibling of this exact machinery: native-library merging "works great, as long as there are no common symbols between the libraries being merged."

Then the giant blob meets a hard limit. On x86-64, a PC-relative reference reaches ±2 GiB — `R_X86_64_PC32` spans [-2³¹, 2³¹) — and a merged native library at training scale eventually outgrows it. The link fails with `relocation truncated to fit`, and because [GCC and Clang don't implement `-mcmodel=large` for position-independent code](https://maskray.me/blog/2023-05-14-relocation-overflow-and-code-models), the documented way out is exactly the move MaskRay's relocation-overflow survey prescribes: "partition the large monolithic executable into the main executable and a few shared objects." In buck2, that partitioning is [link groups](https://github.com/facebook/buck2/blob/main/prelude/linking/link_groups_explained.md): a `link_group_map` carves the binary's native dependency graph into multiple shared libraries, each under the limit, with per-group control over what links statically and what dynamically. (None of this machinery is gentle around torch — the public tracker has [buck2 #62](https://github.com/facebook/buck2/issues/62), libomnibus turning a symbol undefined that libtorch_cpu, libc10, and libtorch_python all keep weak.) For this story, the composition matters for one reason: it sweeps the binary's native dependencies into the artifact itself. Among them, in these binaries, was the RDMA user-space stack — libibverbs and the mlx5 provider, the code that enumerates RDMA devices — bundled into the binary's own image at build time rather than taken from the host it lands on. Hold that thought. Torch was one ingredient; the final artifact was each application's own training binary. And some of those binaries, depending on what they trained on, carried support for MTIA, Meta's own accelerator — provided by an in-house collective-communication library that discovers its devices through RDMA the same way NCCL does.

So: two collective libraries in one process. NCCL/NCCLX for the GPUs, the in-house library for MTIA. Both walk the same device list at startup.

The GPU path worked everywhere. The MTIA path worked in most binaries and failed in others — the same `No IB devices found`, at startup, every time. All of them built from the same torch commit. The device was present by every check anyone could run, and absent according to the one piece of code whose opinion mattered.

That's the howtf. Same source. Same fleet. Same device, verifiably there. Whether a binary could see it depended on the binary.

## 2. The investigation

The first guess, always, is hardware. That is what the error says, that is where on-call muscle memory goes, and that is where this one went. We checked the hardware: no issues, everything looked fine. We ran the basic IP and connectivity tests: passed. The device was cabled, enumerated, and reachable.

Then came the observation that snapped the frame. Other binaries ran fine on the same device. Not other hosts — the same device, on the same machine, found and used happily by different binaries. Some binaries could see it, some could not, and each binary was consistent about which. That's the "wait, what is happening?" moment, and it's the hinge of this whole story: the instant the bug stops being a hardware bug and starts being a linker bug, even though nobody is saying the word "linker" yet.

Two more facts sharpened it. NCCL, running inside the same processes, saw the same RDMA device list and worked — so the kernel was serving the device list correctly to a process that then reported it empty, and the hardware and driver stack were vouched for by a second, working consumer inside the same address space. (File away one detail about that consumer, because it turns out to be the whole story: NCCL doesn't link the verbs library at all — it [dlopens the system `libibverbs` at runtime](https://github.com/NVIDIA/nccl/blob/master/src/misc/ibvsymbols.cc).) And every one of these binaries came from one torch commit, so whatever was different, it wasn't the code anyone had written. Working versus broken didn't track hosts and didn't track source. It tracked *binaries*. The difference had to live in the one step that distinguishes two binaries built from identical code: the link.

I should be honest about the stakes, because they explain the depth of the eventual dig. This was a SEV — and a recurrence of an earlier SEV that had been mitigated without ever being fully root-caused. The failure had been here before, been made to go away, and come back. Running it to ground this time meant descending through layers that don't usually share a whiteboard: how shared libraries are loaded for a binary, how Python links native extensions, and how the RDMA user-space drivers initialize. The root cause, once it surfaced, fit in a sentence: a symbol collision, from double inclusion of the same shared library.

Because once we looked inside those binaries, the constructors *had* run. A verbs stack — libibverbs, the mlx5 provider — initialized and registered its devices; the in-house library's setup ran on top of it. Discovery still came back empty. The state that initialization filled and the state discovery read had the same symbol names, and were not the same memory.

## 3. What was actually happening

Two things were true at once, and they should not have been. Initialization had run: the verbs stack had walked the device list and written the results into its tables. And the discovery call, a moment later, found those tables empty. Both were reaching that state through the same symbol names. They were not reaching the same state.

In [Part 1](/blog/ELF-Linking-101/) we traced how a dynamically linked program comes to life: `ld.so` maps the executable and its libraries, builds the lookup scope, and resolves every reference to exactly one definition — *per lookup*. What Part 1 never had to confront is that "one definition per lookup" is not "one definition." If a strong, non-weak C symbol is defined in two modules, both definitions exist in the process image, and which one a reference binds to depends on where that reference lives and how its module was built. No error fires, because the two definitions never enter the same link: one is compiled into the executable, the other into a shared library, and a shared library's definitions do not collide at link time with the executable's. C has no one-definition rule to make the duplication illegal, either.

Who wins when both copies exist? The executable — this is Part 1's lookup-order rule doing exactly what it promised. The global scope is searched executable-first, so every dynamically resolved reference to the duplicated name lands on the executable's copy. That's interposition, the same feature that lets `LD_PRELOAD` swap in a debugging allocator. Crucially, in a default build it applies to the shared library's *own* references too: the library calls its own functions through the same lookup (the PLT indirection from Part 1), gets the executable's copy like everyone else, and the process stays consistent on one winner. Wasteful, but coherent.

The failure needs one more ingredient: a library that opts out of being interposed. Build a shared library with `-Bsymbolic-functions` (or protected visibility) and its internal references are bound to its own definitions at link time, skipping the runtime lookup. Now the two copies stop agreeing. The library's constructor runs against the library's copy; every other module's references still resolve through the global scope to the executable's copy.

That is the double inclusion the root cause named — and now the two copies have names. The verbs stack existed twice in those processes because it arrived by two roads. Copy one was bundled: Buck's composition had linked libibverbs and the mlx5 provider into the binary's own image — the "hold that thought" from section 1. Copy two was the system's: the same stack is also reached at runtime through `dlopen`. That is how NCCL gets its verbs, deliberately [loading the system `libibverbs`](https://github.com/NVIDIA/nccl/blob/master/src/misc/ibvsymbols.cc) instead of linking it, and it is how the verbs library itself finds its hardware providers, [dlopening the driver](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/dynamic_driver.c) named in its config. Two instances of one library in one address space, each with its own device tables. Initialization filled one instance's tables; the in-house library's discovery, resolving the same symbol names, read the other instance's, which nothing had filled. NCCL never noticed, because NCCL was consistent: it asked the copy it had dlopened, the same copy top to bottom. The process was split into two halves that agreed on every symbol name and disagreed on every symbol's contents. (The dlopen road is also a preview — it is exactly the runtime-scope machinery that returns in section 7 as Route B.)

That is the whole disease, and it deserves a name, because it has none: **split-state linking** — two live copies of one library's state in a single process, with references silently partitioned between them.

<figure class="frame diagram">
  <span class="frame-title">fig. 1 — the two-copy split: the constructor filled one copy, discovery read the other</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 350" role="img" aria-label="Diagram: the application binary holds a static copy of the device table which stays empty, while the shared verbs library built with -Bsymbolic-functions holds its own copy which the constructor fills with two devices; the collective library's discovery call binds across the interposition boundary to the empty executable copy">
      <defs>
        <marker id="p2f1w" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ldr)"/>
        </marker>
        <marker id="p2f1r" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--sec)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <!-- executable side -->
        <rect x="28" y="40" width="306" height="128" fill="var(--sec)" opacity="0.07"/>
        <rect x="28" y="40" width="306" height="128" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="44" y="62" fill="var(--sec)">the application binary</text>
        <text x="44" y="78" font-size="10" fill="var(--muted)">verbs stack statically linked in</text>
        <rect x="46" y="92" width="270" height="60" fill="none" stroke="var(--muted)" stroke-dasharray="3 3"/>
        <text x="58" y="115" fill="var(--sec)">vx_devices[] — copy A</text>
        <text x="58" y="137" font-size="10" fill="var(--muted)">0 devices — nobody wrote here</text>
        <!-- shared library side -->
        <rect x="386" y="40" width="306" height="128" fill="var(--seg)" opacity="0.07"/>
        <rect x="386" y="40" width="306" height="128" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="402" y="62" fill="var(--seg)">libverbs_shared.so</text>
        <text x="402" y="78" font-size="10" fill="var(--muted)">built -Bsymbolic-functions: self-binding</text>
        <rect x="404" y="92" width="270" height="60" fill="none" stroke="var(--muted)" stroke-dasharray="3 3"/>
        <text x="416" y="115" fill="var(--seg)">vx_devices[] — copy B</text>
        <text x="416" y="137" font-size="10" fill="var(--ldr)">rxe_train, rxe_store — 2 devices ✓</text>
        <!-- reader -->
        <rect x="60" y="222" width="230" height="52" fill="var(--sec)" opacity="0.14"/>
        <rect x="60" y="222" width="230" height="52" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="76" y="243" fill="var(--sec)">libcollective.so</text>
        <text x="76" y="260" font-size="10" fill="var(--muted)">vx_get_device_list()</text>
        <!-- writer -->
        <rect x="430" y="222" width="230" height="52" fill="var(--ldr)" opacity="0.14"/>
        <rect x="430" y="222" width="230" height="52" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="446" y="243" fill="var(--ldr)">the verbs constructor</text>
        <text x="446" y="260" font-size="10" fill="var(--muted)">runs at load, registers devices</text>
      </g>
      <g stroke-width="1.5" fill="none">
        <path d="M 175 218 L 175 158" stroke="var(--sec)" marker-end="url(#p2f1r)"/>
        <path d="M 545 218 L 545 158" stroke="var(--ldr)" marker-end="url(#p2f1w)"/>
      </g>
      <g font-family="var(--font-display)" font-size="10">
        <text x="187" y="192" fill="var(--sec)">reads copy A — empty</text>
        <text x="557" y="192" fill="var(--ldr)">writes copy B</text>
      </g>
      <line x1="360" y1="34" x2="360" y2="296" stroke="var(--accent)" stroke-width="1.4" stroke-dasharray="5 5"/>
      <text x="360" y="330" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">the interposition boundary — discovery bound left, the constructor ran right</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>the exe's copy (read)</span>
      <span><span class="k" style="background:var(--seg)"></span>the .so's copy</span>
      <span><span class="k" style="background:var(--ldr)"></span>constructor writes</span>
    </p>
  </div>
</figure>

## 4. Proving it

A claim like that is easy to state and easy to doubt, so here is a reproducer you can run in minutes. It uses soft-RoCE (`rdma_rxe`), the kernel's software RDMA provider, so no special hardware is needed: two virtual RDMA devices, a small "verbs" library present in two copies (one static in the executable, one shared), and a collective that performs discovery. `make matrix` builds the same scenario six ways and prints, for each, the address of the table the constructor wrote and the address of the table discovery read. Same address, no split. Different address, split. The proof is a comparison of two hexadecimal numbers, not a matter of interpretation.

Here is the splitting configuration's actual output:

```shellsession title="make matrix — config B, the splitting build"
[constructor in copy=SHARED] registering rxe_train, rxe_store
[register -> copy=SHARED table@0xffff90340028] now holds 2 device(s)
[get_list <- copy=STATIC table@0xaaaad8f00018] this copy holds 0 device(s)
collective: discovered 0 device(s)   *** DEVICE NOT FOUND -- but the
constructor DID register devices, into the OTHER copy ***
```

The constructor registered both devices. Discovery found zero. Different addresses, different copies. And the matrix pins down exactly when this happens — and, just as important, when it doesn't:

| config | what changed | result |
|---|---|---|
| **A** | default build, no special flags | **no split** — same address; the shared library's own constructor is interposed onto the executable's copy, so everyone agrees |
| **B** | shared lib built `-Bsymbolic-functions` | **SPLIT** — constructor writes the .so copy, discovery reads the exe copy; "device not found" |
| **C** | protected visibility on the lib's internals | **SPLIT** — an equivalent self-binding trigger |
| **C′** | hidden visibility | the DSO is dropped by `--as-needed`, so the constructor never runs at all (a different failure); force it to load and the split reappears |
| **D1** | the colliding thing is a *data* table, static in the .so, global in the exe | **SPLIT** |
| **D2** | data table, global on both sides | **no split** — copy relocation quietly unifies everyone onto the executable's copy |

<figure class="frame diagram">
  <span class="frame-title">fig. 2 — the gate: a split needs all three conditions at once</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 330" role="img" aria-label="Truth-table diagram of the six reproducer configurations: only the rows where a duplicate copy, a self-binding shared library, and an interposing executable are all present produce a split; the default build and the copy-relocated data build do not">
      <text x="360" y="26" text-anchor="middle" font-family="var(--font-display)" font-size="12" fill="var(--text)">SPLIT = duplicate copy AND self-binding .so AND interposing exe</text>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)" text-anchor="middle">
        <text x="300" y="56">duplicate</text>
        <text x="390" y="56">self-binds</text>
        <text x="480" y="56">interposes</text>
        <text x="600" y="56">result</text>
      </g>
      <line x1="30" y1="66" x2="690" y2="66" stroke="var(--border)"/>
      <g font-family="var(--font-mono)" font-size="11" fill="var(--text)">
        <text x="36" y="92">A — default build</text>
        <text x="36" y="130">B — -Bsymbolic-functions</text>
        <text x="36" y="168">C — protected visibility</text>
        <text x="36" y="206">C′ — hidden visibility</text>
        <text x="36" y="244">D1 — data: static in .so</text>
        <text x="36" y="282">D2 — data: global on both</text>
      </g>
      <!-- condition cells: filled amber = condition present, hollow = absent -->
      <g>
        <!-- A: yes / no / yes -->
        <rect x="294" y="82" width="12" height="12" fill="var(--sec)"/>
        <rect x="384" y="82" width="12" height="12" fill="none" stroke="var(--muted)"/>
        <rect x="474" y="82" width="12" height="12" fill="var(--sec)"/>
        <!-- B: yes / yes / yes -->
        <rect x="294" y="120" width="12" height="12" fill="var(--sec)"/>
        <rect x="384" y="120" width="12" height="12" fill="var(--sec)"/>
        <rect x="474" y="120" width="12" height="12" fill="var(--sec)"/>
        <!-- C: yes / yes / yes -->
        <rect x="294" y="158" width="12" height="12" fill="var(--sec)"/>
        <rect x="384" y="158" width="12" height="12" fill="var(--sec)"/>
        <rect x="474" y="158" width="12" height="12" fill="var(--sec)"/>
        <!-- C': yes / yes / n.a. -->
        <rect x="294" y="196" width="12" height="12" fill="var(--sec)"/>
        <rect x="384" y="196" width="12" height="12" fill="var(--sec)"/>
        <text x="480" y="206" text-anchor="middle" font-family="var(--font-mono)" font-size="11" fill="var(--muted)">—</text>
        <!-- D1: yes / yes / yes -->
        <rect x="294" y="234" width="12" height="12" fill="var(--sec)"/>
        <rect x="384" y="234" width="12" height="12" fill="var(--sec)"/>
        <rect x="474" y="234" width="12" height="12" fill="var(--sec)"/>
        <!-- D2: yes / no / yes -->
        <rect x="294" y="272" width="12" height="12" fill="var(--sec)"/>
        <rect x="384" y="272" width="12" height="12" fill="none" stroke="var(--muted)"/>
        <rect x="474" y="272" width="12" height="12" fill="var(--sec)"/>
      </g>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="552" y="92" fill="var(--ldr)">no split</text>
        <rect x="545" y="116" width="60" height="20" fill="var(--accent)" opacity="0.18"/>
        <rect x="545" y="116" width="60" height="20" fill="none" stroke="var(--accent)"/>
        <text x="575" y="130" text-anchor="middle" fill="var(--accent)">SPLIT</text>
        <rect x="545" y="154" width="60" height="20" fill="var(--accent)" opacity="0.18"/>
        <rect x="545" y="154" width="60" height="20" fill="none" stroke="var(--accent)"/>
        <text x="575" y="168" text-anchor="middle" fill="var(--accent)">SPLIT</text>
        <text x="552" y="206" font-size="10" fill="var(--muted)">.so never loads*</text>
        <rect x="545" y="230" width="60" height="20" fill="var(--accent)" opacity="0.18"/>
        <rect x="545" y="230" width="60" height="20" fill="none" stroke="var(--accent)"/>
        <text x="575" y="244" text-anchor="middle" fill="var(--accent)">SPLIT</text>
        <text x="552" y="282" fill="var(--ldr)">no split†</text>
      </g>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)">
        <text x="36" y="308">* C′ — --as-needed drops the unreferenced .so; the constructor never runs (a different failure)</text>
        <text x="36" y="322">† D2 — copy relocation unifies every reference onto the executable's copy</text>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>condition present</span>
      <span><span class="k" style="background:var(--accent)"></span>split — two live copies</span>
      <span><span class="k" style="background:var(--ldr)"></span>no split</span>
    </p>
  </div>
</figure>

Two rows are worth pausing on. Config **A** concedes the obvious objection: a default build does not split. Duplicate copies alone are not the bug; you need the self-binding trigger on top. And that trigger is routinely applied — `-Bsymbolic-functions` is a standard startup-performance and hardening flag, it's in plenty of build templates — with one nasty property: unlike full `-Bsymbolic`, which sets a `DF_SYMBOLIC` flag in the output, `-Bsymbolic-functions` leaves *no trace in the binary at all*. The linker simply resolves the internal calls and moves on. You cannot grep a .so for it after the fact. Config **D2** is the trap turned inside out: the "obvious" version of this bug, a duplicated plain data global, is exactly the one the toolchain saves you from, because copy relocation unifies the copies. The dangerous shape is a *function* (or state reached through one) in a *self-binding* library.

The reproducer also demonstrates the part that made the production incident so disorienting. Build the application twice from byte-identical source, once with the redundant static copy on the link line and once without:

```shellsession title="same source, two link lines"
### app_with_static    (redundant static copy linked):
  collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
### app_without_static (single copy):
  collective: discovered 2 device(s)
```

Same source, opposite behavior, decided entirely by link composition. In a Buck-style build where link groups and library composition vary per application, that is precisely "some binaries fine, some not, same commit."

Toolchain, for the record: gcc 13.3.0, binutils 2.42, Ubuntu 24.04, kernel 6.17-aws for the soft-RoCE variant; reproduced on both aarch64 and x86_64, and re-validated on a clean EC2 instance from the scripts alone, fresh RDMA GUIDs and all. The repo is at [the reproducer repo]. <!-- TODO(Deep): publish repo under your GitHub, then set URL -->

## 5. Why nothing warned

The uncomfortable part is that every component behaved exactly to spec. The ELF gABI forbids multiple `STB_GLOBAL` definitions only among the objects that *enter a link* — and a shared library's definition never enters the executable's link. The GNU ld manual describes archive members being pulled lazily, left to right, once. lld's documentation states the situation without alarm: two links can "both succeed but they have selected different objects from different archives that both define the same symbols." C has no one-definition rule and no COMDAT machinery for these symbols; nothing even checks that the two definitions are the same code.

So no diagnostic fires by default, and the opt-in diagnostics that exist each miss this class. `--warn-backrefs` catches order-dependent archive resolution, not cross-boundary duplication. gold's `--detect-odr-violations` is scoped to C++ mangled names and weak definitions, and needs debug info. `-z muldefs` governs duplicates *within* a link, and this pair never shares one.

People have run into this before, of course — Sergei Trofimovich wrote up a shared-library collision breaking real programs and landed on the same verdict, that the toolchain does not help much here. What has been missing is the recognition that these one-off war stories are a single failure class with a describable trigger.

A failure that produces a crash gets a stack trace. A failure that produces a wrong answer gets silence.

## 6. Fixes: the folklore one that fails, and the ones that work

The instinct, once you know two copies of a symbol are colliding, is to reach for visibility: rebuild the shared library with `-fvisibility=hidden`, or slap a `local: *` version script on it, and the duplicate should stop being exported. It does not fix this. Verified against the reproducer, both leave the split fully in place:

```shellsession title="the fix ladder — naive rungs"
NAIVE FIXES THAT DO NOT WORK:
  nofix-visibility     :   collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
  nofix-version-script :   collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
```

They hide the wrong copy. Visibility controls what the shared library *exports*; it does nothing about the executable's copy, which is the one discovery was binding to all along. (Hiding the library's symbols can also get it dropped by `--as-needed` entirely, trading a split for a constructor that never runs.)

What works, verified, is making the two copies stop being the same symbol — or stop being two:

```shellsession title="the fix ladder — rungs that hold"
FIXES THAT WORK:
  fix-drop-duplicate :   collective: discovered 2 device(s)
  fix-exclude-libs   :   collective: discovered 2 device(s)
  fix-prefix-rename  :   collective: discovered 2 device(s)
```

Keep a single canonical copy. Or link the executable with `-Wl,--exclude-libs,ALL` so it stops dynamically exporting its static copy. Or rename one side's symbols with `objcopy --redefine-sym`. That last one is not hypothetical. Meta's public [torchcomms repository](https://github.com/meta-pytorch/torchcomms) ships a [`rename_symbols.sh`](https://github.com/meta-pytorch/torchcomms/blob/e01f9bf0b44b37e35425c2250e040fca328557af/rename_symbols.sh) that prefixes every `nccl*` symbol, with a comment saying it exists to avoid conflicting with the OSS `nccl*` bundled with PyTorch. The ecosystem shipped the rename fix years before the disease had a name.

In the incident, both rungs got used, in the order SEV pressure dictates. The immediate mitigation was to make the in-house collective library opt-in: binaries that didn't need MTIA stopped pulling it in, and the double inclusion simply stopped happening in the common path — the bug defused by removing one of the two copies from most processes, not by fixing the collision. The principled fix came after: statically link libibverbs and libmlx5, so there is always exactly one copy of the ibv and mlx5 symbols in the image — the bundled-versus-system split collapsed to a single canonical copy. That's the first rung above, the single canonical copy — shipped in production before the reproducer existed to validate it.

## 7. How common is this, really?

One of the layers the SEV dig descended through was Python native linking — how the interpreter `dlopen`s extension modules and the libraries bundled alongside them. That detour turns out not to be a detour at all. If the trigger is a linker flag that leaves no trace in the binary, you can't answer "how common is this?" by grepping; you have to model the binding. And modeling it shows the same double inclusion that took down a training binary sitting quietly in ordinary Python ML processes — because split-state linking arrives by *two* routes, not one.

**Route A — interposition capture** is the incident's shape: a duplicate strong symbol, a self-binding library, and an interposing module sharing one symbol scope. **Route B — scope partition** needs neither self-binding nor interposition. If two modules are loaded into separate local scopes — `RTLD_LOCAL`, the default for every `dlopen`, which is how Python loads extension modules — and each carries its own vendored copy of a library, then each side binds its own copy and runs its own state. Same disease, two live copies of one library's state, reached without any special flag at all.

To measure both, I built `symsplit`, a binding *simulator* rather than a duplicate lister. The distinction is the whole tool. `nm | sort | uniq -d` answers "does a duplicate exist," and on any real system it screams constantly about things that are fine: in a sweep of 788 stock system binaries, 468 had duplicate symbols somewhere in their closures, and not one was a split. bash defines its own `getenv` over libc's — benign, because libc keeps an interposable reference to the name and unifies onto bash's copy. Thousands of weak libc aliases exist to be overridden. Versioned symbols with disjoint version sets can't collide. `symsplit` models what `ld.so` actually does — `.dynsym` versus `.symtab` visibility, scope order, symbol versioning, and per-library self-binding inferred from relocations (a library that retains an interposable `JUMP_SLOT` or `GLOB_DAT` reference to one of its own exports demonstrably did *not* self-bind; one with none probably did) — and it flags a split only when two modules in one image would genuinely resolve the same name to different definitions. Against the reproducer matrix it flags exactly the splitting configuration and clears the rest. Against those 788 system binaries: zero false positives. When it does fire, it says why:

```text title="symsplit — verdict on the splitting config"
VERDICT  SEV     SYMBOL               WHY
SPLIT    MEDIUM  vx_get_device_list   libverbs_shared.so is probably
  self-binding (no JUMP_SLOT/GLOB_DAT to any own export =
  -Bsymbolic-functions signature); its own copy answers its constructor
  calls, while libcollective.so's reference resolves to app_B's copy
  -> two live copies diverge (split state)
```

It is honest about its own limits, too. `-Bsymbolic-functions` can't be proven from the ELF (a library with no self-references *looks* self-bound), so that inference carries a confidence label in the output. And dlopen scope is a runtime property the ELF doesn't record, so Route B modeling takes the scope layout as input rather than pretending to know it.

Pointed at the manylinux ML-wheel ecosystem, the picture that comes back is specific:

**Route B is live in stock wheels.** Import faiss, scikit-learn, and torch into one Python process and `/proc/self/maps` shows two distinct builds each of libgomp, libgfortran, and libquadmath — two OpenMP runtimes, two Fortran runtimes, each with its own global state, resident in one process. numpy plus scipy alone maps two libgfortran and two libquadmath. And this is not merely structural: trace an actual compute workload (numpy matmul, torch matmul, a faiss index search) under `LD_DEBUG=bindings` and 206 duplicated compute symbols bind to two different definitions at once in the same process — almost all of them OpenBLAS kernels, faiss's statically embedded copy answering faiss's calls while numpy's libopenblas answers numpy's. The ecosystem half-knows this. It's the "multiple OpenMP runtimes" problem, and Intel ships a runtime kill-switch for it, `KMP_DUPLICATE_LIB_OK`, silencing an error whose own text warns the duplication "can cause incorrect results."

Step back and the symmetry with the incident's setting is almost too neat. Python's appetite for native libraries creates the same pressure in both worlds; the two ecosystems just push it in opposite directions. The monorepo *merges* — omnibus, link groups, one canonical copy per image — and breaks the day the merge contains common symbols, exactly as Meta's 2018 write-up warned. The wheel ecosystem *vendors* — auditwheel grafts a private copy of every native dependency into each wheel — and breaks, Route B style, the day two of those private copies co-load and each runs its own state. Same pressure, opposite mitigations, one disease.

**Route A's exact trigger is absent from public wheels — which is itself the finding.** `DF_SYMBOLIC` is set on zero of the 366 libraries examined, and `symsplit` predicts zero Route A splits across all eight co-load configurations tested. The trigger lives where the incident lived: in monorepo static-link builds — Buck, Bazel, symbolic-binding hardening — that you cannot download from PyPI. That inaccessibility is a good part of why the class went undiagnosed for so long. But the ingredient that *promotes* Route A is one line away in software everyone runs: `import torch` executes `ctypes.CDLL("libtorch_global_deps.so", RTLD_GLOBAL)`, lifting torch's OpenMP into the global scope. An `LD_DEBUG` probe shows the consequence directly: import faiss alone and its extension module's OpenMP references bind faiss's bundled libgomp; import torch first and every one of those traced references rebinds to torch's copy instead. Which copy of a runtime your library gets is decided by Python import order.

The honest shape of the result: the preconditions are everywhere, the full Route A alignment is rare in public and lives behind corporate build systems, Route B is quietly resident in essentially every large ML process — the training binary's disease, one `import` away — and nothing warns at any tier. The ecosystem survives by paying a scattered tax — `KMP_DUPLICATE_LIB_OK`, auditwheel's content-hashed sonames (which *enable* coexisting copies rather than prevent them), torchcomms' `rename_symbols.sh`, conda's one-copy-per-environment discipline — four patches for one disease, none of them labeled with what they treat.

## 8. What should change

The diagnostic nobody built already has a name in the record. A `--warn-interposition` warning was floated on the GCC mailing list in May 2021 and never implemented in ld or lld. The reason it stalled is documented too: Fangrui Song (MaskRay), lld's maintainer, scoping the equivalent check, noted that the mechanics are easy but that "in the absence of an ignore list mechanism, this extension will not be useful" — interposition is a load-bearing ELF feature, and the base rate of benign duplication is enormous.

That missing ignore-list mechanism is exactly what `symsplit` is. The allowlist for intentional interposers (allocators, sanitizers), the weak/versioned/hidden/symtab-only filtering, the self-binding inference — demonstrated against real binaries with a zero-false-positive record on a 788-binary sweep. The tool stands alone today; the question worth putting to the linker maintainers, and I intend to, is whether an opt-in, allowlist-first version of the check belongs in lld or ld proper.

Until then, the checklist for anyone shipping large statically-or-mixed-linked binaries. If a dependency is built `-Bsymbolic` or `-Bsymbolic-functions`, and a strong C symbol it defines also exists anywhere else in your image, you have a latent split-state hazard that no default tool will flag. Scan for it. Prefer one canonical copy, or make the copies different symbols outright. And file the lesson somewhere it will be found at 2 a.m.: `No IB devices found` can mean the devices are right there — enumerated, registered, waiting — in the copy of the world you didn't ask.

---

*Reproducer, scanner, and survey artifacts: [the reproducer repo]. Everything quoted above — the address matrix, the fix ladder, the sweep, the wheel survey — is a captured artifact in the repo, rerunnable from scripts.*

<!-- TODO(Deep): publish repo under your GitHub, then set the two "[the reproducer repo]" URLs (here and in section 4) -->

