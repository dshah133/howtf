---
title: "howtf can a device be both present and not found?"
description: "A recurring production SEV, a device that was present and not found, and the decades-old linker rule that split one library's state into two. Part 2 of Linking & Loading."
date: 2026-07-13
series:
  name: "Linking & Loading"
  part: 2
tags: [linker, elf, rdma, static-linking]
featured: true
draft: false
---

> **Linking & Loading, Part 2.** Part 1, [*howtf does `./app` reach `main()`?*](/blog/ELF-Linking-101/), traced the machinery: ELF, `ld.so`, symbol resolution, the PLT/GOT, relocations. This one is about what that machinery does to you when the same symbol exists twice.

---

## 1. Present, and not found

The failure that starts this story showed up the way these things do: in the logs and on the dashboards, as training jobs started crashing at startup. The line that mattered was the RDMA stack's classic no-device error, the moral equivalent of `ibv_get_device_list()` coming back empty:

```text
No IB devices found
```

Paste that string into a search engine and every hit is hardware troubleshooting. Check the cable. Check the firmware. Check that the driver is loaded. Which is exactly the rabbit hole it aims you down, because everything about that error says *the machine*, and nothing about it says *the binary*.

But the machine was fine. The device was present by every check anyone could run: it showed up in enumeration, the driver was loaded. And the failing consumer was the last one anyone would suspect: NCCL, the most battle-tested RDMA consumer in the fleet, code nobody had touched, reporting `No IB devices found` at startup, every time, in some binaries and not others. All of them built from the same torch commit.

That's the howtf. Same source. Same fleet. Same device, verifiably there. Whether a binary could see it depended on the binary.

Some context, because the shape of the build matters later. This was at Meta. The binaries were application training binaries composed by Buck, with PyTorch built in-house and statically linked: hermetic builds and fast startup are worth a great deal at that scale, [the exact case Part 1 made for why hyperscalers link statically](/blog/ELF-Linking-101/#61-why-hyperscalers-link-statically).

"Composed by Buck" is doing real work in that sentence, and one fact about it decides this story, so here it is up front. Buck had two strategies for packaging a binary's native code, and the fleet was mid-migration between them. Binaries still on the older **omnibus** strategy hid the bundled verbs symbols. Binaries migrated to **link groups** published them.

The two shapes, briefly. [Omnibus](https://buck.build/javadoc/com/facebook/buck/cxx/Omnibus.html) merges most of a binary's native code (torch and everything under it) "into a single giant shared library," then hides what it merged: the blob is linked behind a version script ending in `local: *;`, localizing every symbol except the exact set the Python-facing roots need. What omnibus swallows, it hides. Meta had written down the hazard of that kind of merge back in [2018](https://engineering.fb.com/2018/01/23/android/android-native-library-merging/), in the Android sibling of this exact machinery: native-library merging "works great, as long as there are no common symbols between the libraries being merged."

But a merged blob at training scale eventually outgrows x86-64's ±2 GiB relocation reach, the same wall [Part 1 hit at the end of its static-linking detour](/blog/ELF-Linking-101/#62-the-consequence-the-2-gib-relocation-barrier). The way past it is [link groups](https://github.com/facebook/buck2/blob/main/prelude/linking/link_groups_explained.md): carve the binary's native dependency graph into several shared libraries, each under the limit. A link group is still a merge, but it lands with the opposite symbol posture. Each group is a genuine shared library, wired to the binary through `DT_NEEDED`, and its boundary symbols are *exported* into the process's dynamic symbol tables so the pieces can find each other at runtime. The full machinery (the version script, the relocation arithmetic, the linker flags, and one public torch casualty) is in [Appendix A](#appendix-a-the-buck-machinery-omnibus-link-groups-and-the-2-gib-wall).

<figure class="frame diagram">
  <span class="frame-title">fig. 1 · one bundled copy, two postures</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 410" role="img" aria-label="Diagram: the same bundled verbs stack takes two postures depending on build strategy — merged into a libomnibus blob its symbols are localized by the version script and nothing reaches the process's global scope, while carved into a link group it becomes a real shared library whose symbols are exported into the global scope">
      <defs>
        <marker id="p2f1a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/>
        </marker>
        <marker id="p2f1m" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <!-- shared origin -->
        <rect x="200" y="30" width="320" height="46" fill="var(--sec)" opacity="0.07"/>
        <rect x="200" y="30" width="320" height="46" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="360" y="49" text-anchor="middle" fill="var(--sec)">libibverbs + mlx5, bundled at build time</text>
        <text x="360" y="66" text-anchor="middle" font-size="10" fill="var(--muted)">same source, same commit</text>
        <!-- omnibus posture -->
        <rect x="28" y="122" width="306" height="128" fill="var(--seg)" opacity="0.07"/>
        <rect x="28" y="122" width="306" height="128" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="44" y="144" fill="var(--seg)">libomnibus.so — the merge</text>
        <text x="44" y="160" font-size="10" fill="var(--muted)">version script ends: local: *;</text>
        <rect x="46" y="174" width="270" height="58" fill="none" stroke="var(--muted)" stroke-dasharray="3 3"/>
        <text x="58" y="197" fill="var(--sec)">verbs copy — localized</text>
        <text x="58" y="218" font-size="10" fill="var(--muted)">hidden inside the blob</text>
        <!-- link-group posture -->
        <rect x="386" y="122" width="306" height="128" fill="var(--seg)" opacity="0.07"/>
        <rect x="386" y="122" width="306" height="128" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="402" y="144" fill="var(--seg)">a link group — the split</text>
        <text x="402" y="160" font-size="10" fill="var(--muted)">a genuine .so, wired by DT_NEEDED</text>
        <rect x="404" y="174" width="270" height="58" fill="none" stroke="var(--muted)" stroke-dasharray="3 3"/>
        <text x="416" y="197" fill="var(--sec)">verbs copy — exported</text>
        <text x="416" y="218" font-size="10" fill="var(--muted)">default visibility, boundary published</text>
        <!-- global scope -->
        <rect x="28" y="312" width="664" height="52" fill="var(--ldr)" opacity="0.07"/>
        <rect x="28" y="312" width="664" height="52" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="44" y="333" fill="var(--ldr)">the process's global dynamic scope</text>
        <text x="181" y="352" text-anchor="middle" font-size="10" fill="var(--muted)">no verbs names here</text>
        <text x="539" y="352" text-anchor="middle" font-size="10" fill="var(--accent)">verbs_register_driver_&lt;N&gt; · ibv_* — on offer</text>
      </g>
      <g stroke-width="1.5" fill="none">
        <path d="M 300 80 C 260 96, 220 102, 190 118" stroke="var(--muted)" marker-end="url(#p2f1m)"/>
        <path d="M 420 80 C 460 96, 500 102, 530 118" stroke="var(--muted)" marker-end="url(#p2f1m)"/>
        <path d="M 181 254 L 181 308" stroke="var(--muted)" stroke-dasharray="4 4" marker-end="url(#p2f1m)"/>
        <path d="M 539 254 L 539 308" stroke="var(--accent)" marker-end="url(#p2f1a)"/>
      </g>
      <g font-family="var(--font-display)" font-size="10">
        <text x="193" y="284" fill="var(--muted)">exports nothing</text>
        <text x="551" y="284" fill="var(--accent)">exports its symbols</text>
      </g>
      <text x="360" y="394" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">same copy, opposite postures: what omnibus hides, a link group publishes</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>the bundled verbs copy</span>
      <span><span class="k" style="background:var(--seg)"></span>build artifact</span>
      <span><span class="k" style="background:var(--ldr)"></span>global scope</span>
      <span><span class="k" style="background:var(--accent)"></span>exported names</span>
    </p>
  </div>
</figure>

The migration between the two ran application by application: some training binaries still composed as one libomnibus, others already carved into link groups. That matters here for two reasons. First, either composition sweeps the binary's native dependencies into the artifact itself. Among them, in these binaries, was the RDMA user-space stack: libibverbs and the mlx5 provider, the code that enumerates RDMA devices, bundled at build time rather than taken from the host the binary lands on.

Second, the *posture* of that bundled copy (localized inside an omnibus blob, or exported from a link group) depended on which side of the migration a given binary stood. Hold both thoughts; the second is where the opening riddle will find its answer.

Torch was one ingredient; the final artifact was each application's own training binary. And some of those binaries, depending on what they trained on, carried support for MTIA, Meta's own accelerator, served by an in-house collective-communication library newly enabled in the Torch backend. It was that library that pulled the verbs stack into the composition at all.

So: two collective libraries in one process. NCCL/NCCLX for the GPUs, the in-house library for MTIA. Both walk the same device list at startup. The new MTIA path was the one that worked.

## 2. The investigation

The first guess, always, is hardware. That is what the error says, that is where on-call muscle memory goes, and that is where this one went. We checked the hardware: no issues, everything looked fine. We ran the basic IP and connectivity tests: passed. The device was cabled, enumerated, and reachable, and other binaries were using it happily on the same machine, which cleared the host too.

Then came the fact that snapped the frame. The in-house library, running inside the very same processes, saw the full RDMA device list and worked. So the kernel was serving the device list correctly into the address space where NCCL reported it empty, and the hardware and driver stack were vouched for by a second, working consumer a few shared libraries away. That is the hinge of this whole story: the instant the bug stops being a hardware bug and starts being a linker bug, even though nobody is saying the word "linker" yet.

One detail about how each consumer reaches the verbs stack turns out to be the whole story. The in-house library was *linked* against the verbs copy bundled into the binary. NCCL in its stock build doesn't link the verbs library at all: it [dlopens `libibverbs` at runtime](https://github.com/NVIDIA/nccl/blob/master/src/misc/ibvsymbols.cc) and takes every entry point from that handle by versioned `dlvsym`, a handle-scoped lookup, the after-`main()` loading machinery from [Part 1's Appendix H](/blog/ELF-Linking-101/#appendix-h-runtime-loading-dlopendlsym).

And every one of these binaries came from one torch commit, so the shared code was the same everywhere. Working versus broken didn't track hosts, and it didn't track that shared source. It tracked *binaries*. That doesn't eliminate every difference (two binaries can still diverge in dependencies, configuration, environment), but it aims the suspicion at the step where all of those become bits: how each binary was composed and linked.

<figure class="frame diagram">
  <span class="frame-title">fig. 2 · the elimination ladder: everything cleared except the link</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 320" role="img" aria-label="Diagram: an elimination ladder of suspects — the hardware, the network, the host, the driver and kernel, and the shared source are each cleared by evidence, aiming suspicion at the link, the step where each binary's composition, dependencies, and flags become bits">
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)">
        <text x="36" y="34">suspect</text>
        <text x="220" y="34">evidence</text>
        <text x="600" y="34">verdict</text>
      </g>
      <line x1="30" y1="44" x2="690" y2="44" stroke="var(--border)"/>
      <g font-family="var(--font-mono)" font-size="11" fill="var(--text)">
        <text x="36" y="70">the hardware</text>
        <text x="36" y="106">the network</text>
        <text x="36" y="142">the host</text>
        <text x="36" y="178">the driver + kernel</text>
        <text x="36" y="214">the shared source</text>
      </g>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)">
        <text x="220" y="70">checked — cabled, enumerated, no issues found</text>
        <text x="220" y="106">IP and connectivity tests pass</text>
        <text x="220" y="142">other binaries use the same device, same machine</text>
        <text x="220" y="178">the in-house consumer sees every device, same process</text>
        <text x="220" y="214">every binary built from one torch commit</text>
      </g>
      <g font-family="var(--font-mono)" font-size="11" fill="var(--ldr)">
        <text x="600" y="70">✓ cleared</text>
        <text x="600" y="106">✓ cleared</text>
        <text x="600" y="142">✓ cleared</text>
        <text x="600" y="178">✓ cleared</text>
        <text x="600" y="214">✓ cleared</text>
      </g>
      <g stroke="var(--border)">
        <line x1="30" y1="82" x2="690" y2="82"/>
        <line x1="30" y1="118" x2="690" y2="118"/>
        <line x1="30" y1="154" x2="690" y2="154"/>
        <line x1="30" y1="190" x2="690" y2="190"/>
        <line x1="30" y1="226" x2="690" y2="226"/>
      </g>
      <rect x="30" y="244" width="660" height="40" fill="var(--accent)" opacity="0.1"/>
      <rect x="30" y="244" width="660" height="40" fill="none" stroke="var(--accent)" stroke-width="1.5"/>
      <text x="36" y="269" font-family="var(--font-mono)" font-size="11" fill="var(--accent)">the link</text>
      <text x="220" y="262" font-family="var(--font-display)" font-size="10" fill="var(--text)">where composition, dependencies, and flags</text>
      <text x="220" y="276" font-family="var(--font-display)" font-size="10" fill="var(--text)">become bits — the step that differed</text>
      <text x="600" y="269" font-family="var(--font-mono)" font-size="11" fill="var(--accent)">← what's left</text>
      <text x="360" y="308" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--muted)">working vs broken tracked binaries, not hosts, not source</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--ldr)"></span>suspect cleared</span>
      <span><span class="k" style="background:var(--accent)"></span>the remaining suspect</span>
    </p>
  </div>
</figure>

I should be honest about the stakes, because they explain the depth of the eventual dig. This was a SEV, and a recurrence of an earlier SEV that had been mitigated without ever being fully root-caused. The failure had been here before, been made to go away, and come back.

Running it to ground this time meant descending through layers that don't usually share a whiteboard: how shared libraries are loaded for a binary, how Python links native extensions, and how the RDMA user-space drivers initialize. The root cause, once it surfaced, fit in a sentence: a symbol collision, from double inclusion of the same shared library.

Because once we looked inside those binaries, the constructors *had* run. A verbs stack (libibverbs, the mlx5 provider) initialized and registered its devices; the in-house library discovered them and ran happily on top. NCCL's discovery, in the same process, still came back empty. The state that initialization filled and the state that NCCL's discovery read had the same symbol names, and were not the same memory.

## 3. What was actually happening

> **Four ELF rules this section leans on** (all from [Part 1](/blog/ELF-Linking-101/)). One, the global scope wins a relocation lookup: for a newly loaded object, glibc searches the main program and its startup dependencies before the object's own group. Two, `RTLD_LOCAL` is not a private namespace: it keeps a dlopened library's names out of the global scope, but does not stop that library from seeing global definitions already there. Three, a handle lookup changes the search root: `dlsym`/`dlvsym` on a handle searches that object and its own dependencies, nobody else's. Four, a pointer returned by `dlvsym` is already an address: calling through it triggers no second lookup.

Two things were true at once, and they should not have been. Initialization had run: the verbs stack had walked the device list and written the results into its tables. And NCCL's discovery call, a moment later, found the tables it was reading empty. Both sides were using the same symbol names. They were not reaching the same state.

> **Evidence status.** I no longer have the internal diffs, so the graph below is a best-fit reconstruction, and it is worth being explicit about what rests on what. *Observed* during the incident: the hardware was present, the in-house consumer saw the devices, NCCL did not, and working-versus-broken tracked the binary, not the host or the source. *Verified from public source:* NCCL dlopens verbs and takes its entry points by `dlvsym`, rdma-core keeps a per-instance driver registry, and glibc searches the global scope before a newly loaded object's own dependency group. *Reconstructed:* the bundled registration symbol was exported from a link-group DSO and captured the system provider's registration. *Demonstrated separately:* the two labs in section 4. *Unavailable:* the original failing binary's own binding trace and link map.

In [Part 1](/blog/ELF-Linking-101/#part-iii-the-loader-takes-control-user-mode) we traced how a dynamically linked program comes to life: `ld.so` maps the executable and its libraries, builds the lookup scope, and resolves every reference to exactly one definition, *per lookup*.

What Part 1 never had to confront is that "one definition per lookup" is not "one definition." If a strong, non-weak C symbol is defined in two modules, both definitions exist in the process image, and which one a reference binds to depends on where that reference lives and how its module was built. No error fires, because the two definitions never enter the same link: one is compiled into the executable, the other into a shared library, and a shared library's definitions do not collide at link time with the executable's. C does have a one-definition rule (§6.9 requires exactly one external definition per name in the whole program), but violating it is undefined behavior with no diagnostic required, and nothing in the separately-linked ELF pipeline is positioned to enforce it across that boundary.

Who wins when both copies exist? Whichever definition comes first in the global scope, and the scope is searched executable-first: this is Part 1's lookup-order rule doing exactly what it promised. So when the executable exports the name (and GNU ld puts it in the executable's dynamic symbol table as soon as a shared library on the link line references it, even without `-rdynamic`), every dynamically resolved reference to the duplicated name lands on the executable's copy. That's interposition, the same feature that lets `LD_PRELOAD` swap in a debugging allocator.

Crucially, in a default build it applies to the shared library's *own* references too: the library calls its own functions through the same lookup (the [PLT indirection from Part 1](/blog/ELF-Linking-101/#34-lazy-binding-watched-live)), gets the executable's copy like everyone else, and the process stays consistent on one winner. Wasteful, but coherent.

The process can stop agreeing on one winner in more than one way. The cleanest trigger, the one the reproducer in section 4 uses to make the split happen on demand, is a library that opts out of being interposed. Build a shared library with `-Bsymbolic-functions` (or protected visibility) and its internal references are bound to its own definitions at link time, skipping the runtime lookup. Now the two copies stop agreeing: the library's constructor runs against the library's copy, while every other module's references still resolve through the global scope to the executable's copy.

The incident reached the same split through a different door: the migration from section 1. Its double inclusion, the one the root cause named, had two copies with names and a history. **Copy A**, the bundled copy, arrived with the in-house collective library: enabling MTIA support pulled libibverbs and the mlx5 provider into the binary's composition. In a binary still composed as libomnibus, that copy was merged into the blob and *localized*: section 1's `local: *;` version script kept the verbs symbols out of the process's dynamic symbol tables entirely, and every verbs call the in-house library made resolved to that internal copy, end to end.

(rdma-core is happy to live self-contained: a static build [compiles the dlopen loader out entirely](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/dynamic_driver.c) and pulls providers in through [`ibv_static_providers()`](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/static_driver.c).)

**Copy B** was the system's: the same stack reached at runtime through `dlopen`. That is how NCCL gets its verbs, deliberately [loading `libibverbs` at runtime](https://github.com/NVIDIA/nccl/blob/master/src/misc/ibvsymbols.cc) instead of linking it, with a bare-name `dlopen("libibverbs.so.1")` that the loader's search resolved, on these hosts, to the system copy. And it is how that libibverbs in turn finds its hardware providers, [dlopening the driver](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/dynamic_driver.c) named in its config. Two instances, two consumers, zero contact: a hidden copy and a public copy have nothing to fight over. Every binary on that side of the migration worked.

Then a binary migrated to link groups, and the bundled verbs changed posture. Carved into a link group, libibverbs stopped being a localized region of a blob and became a genuine shared library whose symbols were exported, default visibility, into the process's global dynamic scope.

Precision matters here, because the naive next sentence ("and they collided with the system copy's identical names") is wrong in an instructive way. The system copy's names never entered the global scope: `dlopen` defaults to `RTLD_LOCAL`, which keeps a loaded library's names out of the global lookup.

A second precision, and it is load-bearing: why did that `dlopen` map a fresh system copy at all, with a verbs stack already resident in the image? Because the loader reuses libraries by *name*, not by contents. `dlopen("libibverbs.so.1")` first walks the objects already loaded, comparing the request against each one's names and `SONAME` ([glibc's `_dl_lookup_map`](https://github.com/bminor/glibc/blob/master/elf/dl-load.c)), and maps a new file only when nothing answers. Had the bundled copy been shipped as a standalone `libibverbs.so.1`, it would have answered: NCCL's `dlopen` would have received the bundled copy, one instance, no split, no bug. But the bundled copy's symbols lived inside a link-group DSO carrying the group's own name. Nothing in the process answered to `libibverbs.so.1`, so the loader mapped the system file, and the image now held two.

That is the ledger to hold for everything that follows. **Copy A**: bundled, exported into the global scope, the in-house library linked to it. **Copy B**: the system copy, dlopened `RTLD_LOCAL`, NCCL pinned to its handle. The surprise, and the whole bug: both providers register into A.

<figure class="frame diagram">
  <span class="frame-title">fig. 3 · the two rails: every registration ran left, NCCL read right</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 410" role="img" aria-label="Diagram of the incident topology: two live copies of libibverbs — the bundled copy A exported from a link group, and the system copy B dlopened RTLD_LOCAL. Both mlx5 provider constructors' registration imports resolve through the global scope into copy A's registry, including the system provider's, which is captured. NCCL is pinned by handle-scoped lookup to copy B, whose registry stays empty, so it finds zero devices, while the in-house library reads copy A and sees both.">
      <defs>
        <marker id="p2f3w" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ldr)"/>
        </marker>
        <marker id="p2f3a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/>
        </marker>
        <marker id="p2f3r" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--sec)"/>
        </marker>
        <marker id="p2f3s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--seg)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <!-- copy A -->
        <rect x="28" y="36" width="320" height="118" fill="var(--sec)" opacity="0.07"/>
        <rect x="28" y="36" width="320" height="118" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="44" y="58" fill="var(--sec)">bundled libibverbs — copy A</text>
        <text x="44" y="74" font-size="10" fill="var(--muted)">link group: exported into the global scope</text>
        <rect x="46" y="88" width="284" height="52" fill="none" stroke="var(--muted)" stroke-dasharray="3 3"/>
        <text x="58" y="109" fill="var(--sec)">driver registry</text>
        <text x="58" y="128" font-size="10" fill="var(--ldr)">mlx5 registered — every write landed here</text>
        <!-- copy B -->
        <rect x="392" y="36" width="300" height="118" fill="var(--seg)" opacity="0.07"/>
        <rect x="392" y="36" width="300" height="118" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="408" y="58" fill="var(--seg)">system libibverbs — copy B</text>
        <text x="408" y="74" font-size="10" fill="var(--muted)">dlopen'd RTLD_LOCAL: names stay private</text>
        <rect x="410" y="88" width="264" height="52" fill="none" stroke="var(--muted)" stroke-dasharray="3 3"/>
        <text x="422" y="109" fill="var(--seg)">driver registry</text>
        <text x="422" y="128" font-size="10" fill="var(--muted)">empty — no constructor could reach it</text>
        <!-- actors -->
        <rect x="24" y="300" width="152" height="56" fill="var(--sec)" opacity="0.14"/>
        <rect x="24" y="300" width="152" height="56" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="36" y="321" fill="var(--sec)">in-house lib</text>
        <text x="36" y="338" font-size="10" fill="var(--muted)">linked to copy A</text>
        <rect x="198" y="300" width="152" height="56" fill="var(--ldr)" opacity="0.14"/>
        <rect x="198" y="300" width="152" height="56" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="210" y="321" fill="var(--ldr)">bundled mlx5 ctor</text>
        <text x="210" y="338" font-size="10" fill="var(--muted)">verbs_register_…_&lt;N&gt;</text>
        <rect x="372" y="300" width="152" height="56" fill="var(--ldr)" opacity="0.14"/>
        <rect x="372" y="300" width="152" height="56" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="384" y="321" fill="var(--ldr)">system mlx5 ctor</text>
        <text x="384" y="338" font-size="10" fill="var(--muted)">dlopened by copy B</text>
        <rect x="546" y="300" width="150" height="56" fill="var(--seg)" opacity="0.14"/>
        <rect x="546" y="300" width="150" height="56" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="558" y="321" fill="var(--seg)">NCCL</text>
        <text x="558" y="338" font-size="10" fill="var(--muted)">pinned to B's handle</text>
      </g>
      <g stroke-width="1.5" fill="none">
        <path d="M 100 296 L 100 158" stroke="var(--sec)" marker-end="url(#p2f3r)"/>
        <path d="M 274 296 L 274 158" stroke="var(--ldr)" marker-end="url(#p2f3w)"/>
        <path d="M 448 296 C 448 230, 330 220, 322 158" stroke="var(--accent)" marker-end="url(#p2f3a)"/>
        <path d="M 621 296 L 621 158" stroke="var(--seg)" marker-end="url(#p2f3s)"/>
      </g>
      <g font-family="var(--font-display)" font-size="10">
        <text x="108" y="248" fill="var(--sec)">reads A — 2 devices ✓</text>
        <text x="282" y="212" fill="var(--ldr)">registers</text>
        <text x="440" y="252" fill="var(--accent)">captured — global scope first</text>
        <text x="613" y="212" text-anchor="end" fill="var(--seg)">reads B — 0 devices</text>
      </g>
      <text x="360" y="394" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">the import binds by scope, not by caller: order doesn't matter, so it failed the same way every time</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>copy A: bundled, exported</span>
      <span><span class="k" style="background:var(--seg)"></span>copy B: system, private</span>
      <span><span class="k" style="background:var(--ldr)"></span>registration writes</span>
      <span><span class="k" style="background:var(--accent)"></span>the captured import</span>
    </p>
  </div>
</figure>

Before the scope mechanics, the failure in one plain sentence: the system provider meant to register with the system libibverbs loaded right beside it, and the global-scope lookup sent that registration into the bundled copy instead. Here is how, step by step.

Neither consumer was ever confused about which instance it was calling. NCCL takes its entry points by `dlvsym` on its own handle, a lookup that sees only that handle's little world, so the pointers NCCL held were pinned to copy B, unreachable by interposition, by construction. The pinning covers the pointers NCCL holds, not the copy itself: copy B's own outward references still resolve like anyone else's, and that is about to matter. The in-house library was equally settled the other way: its verbs references resolved through the global scope to copy A, the only definition on offer there. Two consumers, each faithfully wired to one instance. So far, that is just the omnibus arrangement with the curtain open. The bug needs one more reference, one that has to *cross* between the worlds.

That reference lives in how rdma-core keeps its books. Every instance of libibverbs carries its own file-static state: the device list `ibv_get_device_list()` hands back lives in [a static inside `device.c`](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/device.c), and the driver registry it is built from is [a static `driver_list` inside `init.c`](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/init.c). And rdma-core's [own version script](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/libibverbs.map.in) marks the machinery around them local, so each instance's discovery is welded to its own tables at link time.

Providers are not handed over; they announce themselves: [libmlx5](https://github.com/linux-rdma/rdma-core/blob/master/providers/mlx5/mlx5.c) runs an ELF constructor, the [`PROVIDER_DRIVER` macro](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/driver.h), that calls `verbs_register_driver_<N>()` (rdma-core bakes its private-ABI number into the symbol name itself) to append the mlx5 driver to a registry. But *which* registry is not the loading instance's choice to make.

And ELF binds that call by lookup scope, not by which library loaded the caller. The constructor's call is a plain extern function call, not a function pointer, not a `dlsym`: an undefined import that the dynamic linker resolves against the global scope *first* and the dlopen's own dependency scope second. The provider's `DT_NEEDED` on `libibverbs.so.1` decides what gets loaded beside it ([Part 1's dependency-discovery step](/blog/ELF-Linking-101/#32-dependency-discovery)), not who wins the lookup. And `RTLD_DEEPBIND`, the one switch that flips the order, is nowhere in this stack. The global scope now offered copy A's exported definition.

So every registration in the process (the bundled provider's, the system provider's that copy B dlopened for NCCL, whichever fired first) resolved to copy A and filled *copy A's* registry. Interposition, doing exactly what Part 1 promised, to the one call that crossed the boundary. Copy B, the instance NCCL was pinned to, sitting right there as the provider's own `DT_NEEDED` dependency, kept a registry that no constructor could ever reach.

Discovery then walks sysfs and matches what the kernel reports against its *own* instance's registry, and a device that matches no registered driver is silently dropped from the result (the warning that would have named the problem hides behind an `IBV_SHOW_WARNINGS` environment variable).

Run the two consumers side by side. The in-house library reads copy A: the registrations landed there, every device matches, MTIA trains. NCCL reads copy B: registry empty, every device the kernel reported matches no driver, zero devices. `No IB devices found`, from the fleet's most trusted RDMA consumer, on a machine where the library that had smuggled the second copy in could see them all.

Note what the mechanism does *not* depend on: load order among the consumers. Copy A's exports entered the global scope at startup (`DT_NEEDED` wiring, in place before either consumer moved), so whichever consumer initializes first, the registration import binds by scope, not by caller. That is why every binary was consistent about failing, every time.

(The stability is a property of this startup topology, mind. A second copy arriving *late*, by an `RTLD_GLOBAL` dlopen mid-run, would not rebind references already resolved. Here there was nothing to rebind: the scope was set before the first constructor fired.)

And that is section 1's riddle solved: same commit, opposite behavior, because binaries still on omnibus carried a hidden copy that captured nothing, and binaries on link groups carried an exported copy that captured every registration. Whether NCCL could see the hardware depended on which side of a build-system migration its binary stood. (The dlopen road is also a preview: it is exactly the runtime-scope machinery that returns in section 7 as Route B.)

That is the whole disease, and it is worth naming in its own right: **split-state linking**, two live copies of one library's state in a single process, with references silently partitioned between them.

The topology did the capturing; on top of it, two conditions specific to rdma-core still had to hold for the collision to land at all: the copies must agree on rdma-core's private ABI number (it is baked into the registration symbol's name), and symbol versioning must not block the cross-copy match (it does not, for reasons glibc's own source comments on). [Appendix B](#appendix-b-two-preconditions-for-the-capture) walks both.

## 4. Reproducing it

A claim like that is easy to state and easy to doubt, so here are two reproducers you can run in minutes, no special hardware needed. The first rebuilds section 3's binding topology directly and watches the registration get captured. The second strips the mechanism down to a model and builds the split six ways, to pin down exactly which ingredients produce it.

### The production topology, reproduced

The first lab, [`scope-capture/`](https://github.com/dshah133/howtf/tree/main/demo/rdma-symbol-collision/scope-capture) in the repo, restages the incident's own door: the split from scope alone, no self-binding flag, no copy in the executable. Three shared libraries, all default visibility:

- `libbundle.so` is copy A, a `DT_NEEDED` dependency of the app, so its `register_driver` sits in the global scope from startup.
- `libregistryB.so.1` is copy B, the system libibverbs, `dlopen`ed `RTLD_LOCAL` so its names stay private, reached only through its own handle.
- `libproviderB.so` is the provider. Its `DT_NEEDED` is copy B, and its constructor calls `register_driver` as a plain extern import.

That constructor call is the whole incident in one line. It is an undefined import, so the loader resolves it against the global scope first and the provider's own dependency group second. Copy A is global; copy B is only in the local group. The registration lands in copy A, captured, even though copy B is the provider's own dependency sitting right beside it. `LD_DEBUG=bindings` says so directly:

```text title="make trace: the provider's registration, captured by the global scope"
binding file libproviderB.so to libbundle.so: normal symbol `register_driver' [VERB_1.0]
binding file libregistryB.so.1 to libregistryB.so.1: normal symbol `get_device_list' [VERB_1.0]
```

The provider binds `register_driver` to libbundle, copy A. Copy B answers only its own `get_device_list`. Then each consumer reads where it was always going to: the in-house side, linked to copy A, sees the device; NCCL, by `dlvsym` on copy B's handle, reads copy B's empty registry.

```text title="make bug: register writes copy A, dlvsym reads copy B"
[bundle / copy A] register_driver(mlx5_from_providerB) -> registry A @0x73da35e88040 now holds 1 device(s)
    in-house consumer sees 1 device(s)   [OK -- reads the copy the registration landed in]
[registryB / copy B] get_device_list  <- registry B @0x73da35e83060 holds 0 device(s)
    NCCL consumer sees 0 device(s)   *** No IB devices found -- the registration went to the OTHER copy ***
```

Two registry addresses, the write on one and the read on the other, no self-binding flag anywhere in the build. `make fixed` confirms the mechanism from the far side: localize copy A's `register_driver` so it leaves the global scope, and the provider's import falls through to copy B. Registration and discovery reunite there, and NCCL sees the device. That is also the lesson section 6's naive visibility fix misses: the copy you hide has to be the one wrongly winning the global lookup, the bundled copy A, not the system copy B.

### The trigger, isolated

The scope-capture lab reproduces the exact door but fixes the topology. To map the boundary, when a split happens and when it doesn't, the second lab strips out the hardware and the scope machinery and models the mechanism directly: a small "verbs" library present in two copies (one static in the executable, one shared), a collective that performs discovery, and a couple of device names the library registers. Nothing links `libibverbs`. The point is the linker, not the hardware. `make matrix` builds the same scenario six ways and prints, for each, the address of the table the constructor wrote and the address of the table discovery read. Same address, no split. Different address, split. Whether a given build splits is a comparison of two hexadecimal numbers, not a matter of interpretation. (A second variant in the repo restages this split on real soft-RoCE (`rdma_rxe`) devices, through `ibv_open_device`, for anyone who wants the hardware path.)

This lab reaches the split through the cleanest switch that produces it on demand, a self-binding flag, rather than the scope-capture lab's dlopen scoping. Section 7 comes back to why that difference matters. Here is the splitting configuration's actual output:

```shellsession title="make matrix: config B, the splitting build"
[constructor in copy=SHARED] registering rxe_train, rxe_store
[register -> copy=SHARED table@0xffff90340028] now holds 2 device(s)
[get_list <- copy=STATIC table@0xaaaad8f00018] this copy holds 0 device(s)
collective: discovered 0 device(s)   *** DEVICE NOT FOUND -- but the
constructor DID register devices, into the OTHER copy ***
```

The constructor registered both devices. Discovery found zero. Different addresses, different copies. That is the production failure's shape, drawn small (the reproducer's simplified topology, not the production one: here the second copy is statically linked into the executable, where the incident's arrived by dlopen):

<figure class="frame diagram">
  <span class="frame-title">fig. 4 · the reproducer, simplified: the constructor filled one copy, discovery read the other</span>
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
        <text x="58" y="115" fill="var(--sec)">vx_devices[] — the exe's copy</text>
        <text x="58" y="137" font-size="10" fill="var(--muted)">0 devices — nobody wrote here</text>
        <!-- shared library side -->
        <rect x="386" y="40" width="306" height="128" fill="var(--seg)" opacity="0.07"/>
        <rect x="386" y="40" width="306" height="128" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="402" y="62" fill="var(--seg)">libverbs_shared.so</text>
        <text x="402" y="78" font-size="10" fill="var(--muted)">built -Bsymbolic-functions: self-binding</text>
        <rect x="404" y="92" width="270" height="60" fill="none" stroke="var(--muted)" stroke-dasharray="3 3"/>
        <text x="416" y="115" fill="var(--seg)">vx_devices[] — the .so's copy</text>
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
        <text x="187" y="186" fill="var(--sec)">reads the exe's copy</text>
        <text x="187" y="200" fill="var(--sec)">— empty</text>
        <text x="557" y="192" fill="var(--ldr)">writes the .so's copy</text>
      </g>
      <line x1="360" y1="34" x2="360" y2="296" stroke="var(--accent)" stroke-width="1.4" stroke-dasharray="5 5"/>
      <text x="360" y="330" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">the interposition boundary: discovery bound left, the constructor ran right</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>the exe's copy (read)</span>
      <span><span class="k" style="background:var(--seg)"></span>the .so's copy</span>
      <span><span class="k" style="background:var(--ldr)"></span>constructor writes</span>
    </p>
  </div>
</figure>

The matrix pins down exactly when this happens and, just as important, when it doesn't. Config **A**, the default build with no special flags, concedes the obvious objection first: no split. The shared library's own constructor is interposed onto the executable's copy, so everyone agrees on one winner. Config **B** builds the shared library with `-Bsymbolic-functions`: split, the constructor writes the .so's copy while discovery reads the executable's, and the collective reports "device not found." Config **C**, protected visibility on the library's internals, is an equivalent self-binding trigger: split again. The remaining rows probe the edges (hidden visibility, and two data-symbol variants where copy relocation rescues one case); [Appendix C](#appendix-c-the-reproducers-edge-rows-hidden-visibility-and-copy-relocation) walks them.

<figure class="frame diagram">
  <span class="frame-title">fig. 5 · the reproducer's gate: a split by this route needs all three conditions at once</span>
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
      <span><span class="k" style="background:var(--accent)"></span>split: two live copies</span>
      <span><span class="k" style="background:var(--ldr)"></span>no split</span>
    </p>
  </div>
</figure>

So duplicate copies alone are not the bug; you need the self-binding trigger on top. And that trigger is not exotic: `-Bsymbolic-functions` is used by some build systems to cut binding overhead and symbol preemption, with one nasty property. Unlike full `-Bsymbolic`, which sets a `DF_SYMBOLIC` flag in the output, `-Bsymbolic-functions` leaves *no explicit marker in the binary*. No flag, no dynamic tag. The linker simply resolves the internal calls and moves on. What remains is an absence, the relocations those calls no longer need, and you cannot grep a .so for an absence. (Section 7's scanner learns to read exactly that residue.) The dangerous shape is a *function* (or state reached through one) in a *self-binding* library.

The reproducer also demonstrates the part that made the production incident so disorienting. Build the application twice from byte-identical source, once with the redundant static copy on the link line and once without:

```shellsession title="same source, two link lines"
### app_with_static    (redundant static copy linked):
  collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
### app_without_static (single copy):
  collective: discovered 2 device(s)
```

Same source, opposite behavior, decided entirely by link composition. That pair echoes the omnibus-to-link-groups migration: a different trigger, the same phenotype. In a fleet where composition varies per application, it is precisely "some binaries fine, some not, same commit."

Toolchain, for the record: gcc 13.3.0, binutils 2.42, Ubuntu 24.04, kernel 6.17-aws for the soft-RoCE variant; reproduced on both aarch64 and x86_64, and re-validated on a clean EC2 instance from the scripts alone, fresh RDMA GUIDs and all. The repo is at [the reproducer repo](https://github.com/dshah133/howtf/tree/main/demo/rdma-symbol-collision).

## 5. Why nothing warned

The uncomfortable part is that every component behaved exactly to spec. The ELF gABI forbids multiple `STB_GLOBAL` definitions only among the objects that *enter a link*, and a shared library's definition never enters the executable's link. (Part 1's build-time flashback showed this from the other side: [the linker consulted `libmath.so` only to verify the symbol existed and record `DT_NEEDED`](/blog/ELF-Linking-101/#step-3-synthesis-plt--got). It never took the code.) The GNU ld manual describes archive members being pulled lazily, left to right, once. lld's documentation states the situation without alarm: two links can "both succeed but they have selected different objects from different archives that both define the same symbols." C's one-external-definition rule (§6.9) carries no required diagnostic and no COMDAT machinery to enforce it here; nothing even checks that the two definitions are the same code.

So no diagnostic fires by default, and the opt-in diagnostics that exist each miss this class. `--warn-backrefs` catches order-dependent archive resolution, not cross-boundary duplication. gold's `--detect-odr-violations` is scoped to C++ mangled names and weak definitions, and needs debug info. `-z muldefs` governs duplicates *within* a link, and this pair never shares one.

People have run into this before, of course. Sergei Trofimovich wrote up a shared-library collision breaking real programs and landed on the same verdict, that the toolchain does not help much here. What has been missing is the recognition that these one-off war stories are a single failure class with a describable trigger.

A failure that produces a crash gets a stack trace. A failure that produces a wrong answer gets silence.

## 6. Fixes: the folklore one that fails, and the ones that work

The instinct, once you know two copies of a symbol are colliding, is to reach for visibility: rebuild the shared library with `-fvisibility=hidden`, or slap a `local: *` version script on it, and the duplicate should stop being exported. It does not fix this. Verified against the reproducer, both leave the split fully in place:

```shellsession title="the fix ladder: naive rungs"
NAIVE FIXES THAT DO NOT WORK:
  nofix-visibility     :   collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
  nofix-version-script :   collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
```

They hide the wrong copy. Visibility controls what the shared library *exports*; it does nothing about the executable's copy, which is the one discovery was binding to all along. (Hiding the library's symbols can also get it dropped by `--as-needed` entirely, trading a split for a constructor that never runs.)

What works, verified, is making the two copies stop being the same symbol, or stop being two:

```shellsession title="the fix ladder: rungs that hold"
FIXES THAT WORK:
  fix-drop-duplicate :   collective: discovered 2 device(s)
  fix-exclude-libs   :   collective: discovered 2 device(s)
  fix-prefix-rename  :   collective: discovered 2 device(s)
```

These are not equal in durability. The root-cause fix is one canonical copy, so there is only ever one instance to bind to. Where two copies genuinely must coexist, make them different symbols outright with `objcopy --redefine-sym`, so they can never collide. And a narrower, link-local measure is to stop the executable exporting its static copy: `-Wl,--exclude-libs,libverbs_static.a`, naming the offending archive rather than `-Wl,--exclude-libs,ALL`, which hides every archive's symbols and can suppress plugin or callback exports the process actually needs. The rename fix is not hypothetical. Meta's public [torchcomms repository](https://github.com/meta-pytorch/torchcomms) ships a [`rename_symbols.sh`](https://github.com/meta-pytorch/torchcomms/blob/e01f9bf0b44b37e35425c2250e040fca328557af/rename_symbols.sh) that prefixes every `nccl*` symbol, with a comment saying it exists to avoid conflicting with the OSS `nccl*` bundled with PyTorch. The ecosystem shipped the rename fix years before the disease had a name.

In the incident, both rungs got used, in the order SEV pressure dictates. The immediate mitigation was to make the in-house collective library opt-in: binaries that didn't need MTIA stopped pulling the verbs stack into the composition at all, so nothing exported a second copy into the global scope. The provider's registration import, with nothing left to capture it, fell through to the system libibverbs: registration and discovery reunited on the one copy NCCL was pinned to, and NCCL healed. The bug was defused by removing one of the two copies from most processes, not by fixing the collision.

The principled fix came after: statically link libibverbs and libmlx5, one canonical copy for the image, kept out of the dynamic namespace. That is the posture the omnibus merge had been providing by accident, restored on purpose. In the fix ladder's terms it removes the duplicate from the scope where the registration was being captured (the first rung's spirit), shipped in production before the reproducer existed to validate it.

## 7. How common is this, really?

One of the layers the SEV dig descended through was Python native linking: how the interpreter `dlopen`s extension modules and the libraries bundled alongside them. That detour turns out not to be a detour at all, because the wheel ecosystem lives under the same pressure that built the incident and pushes back the opposite way. The monorepo *merges* (omnibus, link groups, one canonical copy per image) and breaks the day a canonical copy's symbols escape into a scope that already holds the same names, the hazard Meta's 2018 write-up warned about. The wheel ecosystem *vendors* (auditwheel grafts a private copy of every native dependency into each wheel) and breaks the day two of those private copies co-load and each runs its own state. Same pressure, opposite mitigations, one disease.

The two worlds also differ in route, and the distinction organizes everything measured below, because split-state linking arrives by *two* routes, not one. **Route A (interposition capture)** is the reproducer's shape: a duplicate strong symbol, a self-binding library, and an interposing module sharing one symbol scope. **Route B (scope partition)** needs neither self-binding nor interposition. If two modules are loaded into separate local scopes (`RTLD_LOCAL`, the default for every `dlopen`, which is [how Python loads extension modules](/blog/ELF-Linking-101/#appendix-h-runtime-loading-dlopendlsym)) and each carries its own vendored copy of a library, then each side binds its own copy and runs its own state. Same disease, reached without any special flag at all. The incident stood with one foot in each route: Route A's interposition did the capturing (the registration import, resolved through the global scope into the bundled copy), and Route B's scope machinery did the isolating (the victim pinned by handle to an `RTLD_LOCAL` copy the capture could never fill).

Measuring either route takes more than a duplicate-symbol lister, because the trigger leaves no marker in the binary (section 4) and the base rate of benign duplication is enormous: in a sweep of 788 stock system binaries, 468 had duplicate symbols somewhere in their closures, and not one was a split. So I built `symsplit`, a binding *simulator*: it models what `ld.so` actually does and flags a split only when two modules in one image would genuinely resolve the same name to different definitions. Against the reproducer matrix it flags exactly the splitting configuration and clears the rest. Against those 788 system binaries: zero flags (a quiet-corpus result rather than ground-truth validation; the corpus is presumed clean). [Appendix D](#appendix-d-symsplit-a-binding-simulator-not-a-duplicate-lister) covers what it models, how it infers self-binding from a relocation absence, and where its limits lie.

Pointed at the manylinux ML-wheel ecosystem, the picture that comes back is specific.

**Route B is live in stock wheels.** Import faiss, scikit-learn, and torch into one Python process and `/proc/self/maps` shows two distinct builds each of libgomp, libgfortran, and libquadmath: two OpenMP runtimes, two Fortran runtimes, each with its own global state, resident in one process. And this is not merely structural: trace an actual compute workload under `LD_DEBUG=bindings` and 206 duplicated compute symbols bind to two different definitions at once in the same process, almost all of them OpenBLAS kernels, with faiss's statically embedded copy answering faiss's calls while numpy's libopenblas answers numpy's (partitioned binding caught live, not a demonstrated wrong answer). The ecosystem half-knows this. It's the "multiple OpenMP runtimes" problem, and Intel ships a runtime kill-switch for it, `KMP_DUPLICATE_LIB_OK`, silencing an error whose own text warns the duplication "can cause incorrect results." The full survey numbers live in [Appendix E](#appendix-e-the-wheel-survey-full-numbers).

**Route A's exact trigger is absent from public wheels, which is itself the finding.** `DF_SYMBOLIC` is set on zero of the 366 libraries examined, and `symsplit` predicts zero Route A splits across all eight co-load configurations tested. The trigger lives where the incident lived: inside monorepo native-link builds (Buck, Bazel, symbolic-binding hardening, omnibus-to-link-group migrations) that you cannot download from PyPI. That inaccessibility is a good part of why the class went undiagnosed for so long. But the ingredient that *promotes* Route A is one line away in software everyone runs: `import torch` executes `ctypes.CDLL("libtorch_global_deps.so", RTLD_GLOBAL)`, lifting torch's OpenMP into the global scope. An `LD_DEBUG` probe shows the consequence directly: import faiss alone and its extension module's OpenMP references bind faiss's bundled libgomp; import torch first and every one of those traced references rebinds to torch's copy instead. Which copy of a runtime your library gets is decided by Python import order.

The honest shape of the result: the preconditions are everywhere, the full Route A alignment is rare in public and lives behind corporate build systems, and Route B is quietly resident in the stock ML stacks tested here and, by the arithmetic of auditwheel's vendoring, in any process that co-loads two wheels carrying the same runtime. The training binary's disease, one `import` away, and nothing warns at any tier. The ecosystem survives by paying a scattered tax: `KMP_DUPLICATE_LIB_OK`, auditwheel's content-hashed sonames (which *enable* coexisting copies rather than prevent them), torchcomms' `rename_symbols.sh`, conda's one-copy-per-environment discipline. Four patches for one disease, none of them labeled with what they treat.

## 8. What should change

The diagnostic nobody built already has a name in the record. A `--warn-interposition` warning was floated on the GCC mailing list in May 2021 and never implemented in ld or lld. The reason it stalled is documented too: Fangrui Song (MaskRay), lld's maintainer, scoping the equivalent check, noted that the mechanics are easy but that "in the absence of an ignore list mechanism, this extension will not be useful". Interposition is a load-bearing ELF feature, and the base rate of benign duplication is enormous.

That missing ignore-list mechanism is exactly what `symsplit` is. The allowlist for intentional interposers (allocators, sanitizers), the weak/versioned/hidden/symtab-only filtering, the self-binding inference: all of it demonstrated against real binaries, silent across a 788-binary sweep of presumed-clean system binaries. The tool stands alone today; the question worth putting to the linker maintainers, and I intend to, is whether an opt-in, allowlist-first version of the check belongs in lld or ld proper.

Until then, the checklist for anyone shipping large statically-or-mixed-linked binaries. If a dependency is built `-Bsymbolic` or `-Bsymbolic-functions`, and a strong C symbol it defines also exists anywhere else in your image, you have a latent split-state hazard (it fires when the duplicated symbol guards state and both copies end up live in the same scope), and no default tool will flag it. Scan for it. Prefer one canonical copy, or make the copies different symbols outright. And file the lesson somewhere it will be found at 2 a.m.: `No IB devices found` can mean the devices are right there — enumerated, registered, waiting — in the copy of the world you didn't ask.

---

*Reproducer, scanner, and survey artifacts: [the reproducer repo](https://github.com/dshah133/howtf/tree/main/demo/rdma-symbol-collision) (scanner at [`tools/symsplit`](https://github.com/dshah133/howtf/tree/main/tools/symsplit)). Everything quoted above (the scope-capture bindings, the address matrix, the fix ladder, the sweep, the wheel survey) is a captured artifact in the repo, rerunnable from scripts.*


## Appendices

Evidence lockers: the full dumps and gnarlier details the body text points at. Skip freely; return when a claim needs its receipts.

### Appendix A: The Buck machinery (omnibus, link groups, and the 2 GiB wall)

Section 1 compressed the build story into one contrast: omnibus hides, link groups publish. Here is the machinery behind both halves, all of it readable in public tooling.

#### 1) Omnibus: the merge

A Python training program pulls in an enormous amount of native code (torch and everything under it), and not all of it can be statically compiled into one executable. Buck's [omnibus](https://buck.build/javadoc/com/facebook/buck/cxx/Omnibus.html) strategy does the next-best merge: statically link most of the native code "into a single giant shared library" (a `libomnibus.so`), leaving only the extensions Python imports directly as separate .so's. You pay the full static-link cost once, at build time; at runtime the binary `dlopen`s roughly one big library instead of hundreds.

The merge comes with a symbol discipline you can read in the open-source prelude. The omnibus body is linked behind a [generated version script](https://github.com/facebook/buck2/blob/main/prelude/cxx/omnibus.bzl) whose [last line is `local: *;`](https://github.com/facebook/buck2/blob/main/prelude/cxx/symbols.bzl), localizing every symbol merged into the blob except the exact set the Python-facing roots need.

#### 2) The 2 GiB wall

On x86-64 a PC-relative reference reaches ±2 GiB (`R_X86_64_PC32` spans [-2³¹, 2³¹)), and a merged native library at training scale eventually outgrows it. The link fails with `relocation truncated to fit`. The medium and large PIC code models do exist on x86-64, but neither is a clean retrofit for an image this size: metadata like `.eh_frame` keeps 32-bit `R_X86_64_PC32` relocations even in `-mcmodel=large` output, prebuilt small-model objects carry the same limit, and large-model code costs size and speed. So the documented way out is the move [MaskRay's relocation-overflow survey](https://maskray.me/blog/2023-05-14-relocation-overflow-and-code-models) prescribes: "partition the large monolithic executable into the main executable and a few shared objects."

#### 3) Link groups: the partition

In buck2, that partitioning is [link groups](https://github.com/facebook/buck2/blob/main/prelude/linking/link_groups_explained.md): a `link_group_map` carves the binary's native dependency graph into multiple shared libraries, each under the limit, with per-group control over what links statically and what dynamically. A link group is still a merge (each group statically links its members into one shared library), but its boundary symbols are exported: the [same document](https://github.com/facebook/buck2/blob/main/prelude/linking/link_groups_explained.md) describes public nodes linked `--whole-archive` so all their symbols survive, and `--dynamic-list` entries feeding names into the main binary's dynamic symbol table so the pieces can find each other at runtime.

None of this machinery is gentle around torch. The public tracker has [buck2 #62](https://github.com/facebook/buck2/issues/62), libomnibus turning a symbol undefined that libtorch_cpu, libc10, and libtorch_python all keep weak.

### Appendix B: Two preconditions for the capture

Section 3 asserted that two preconditions had to hold for the registration to be captured. Here they are, with the receipts.

#### 1) The copies must agree on rdma-core's private ABI number

The registration entry point is `verbs_register_driver_<N>`: rdma-core bakes its private-ABI number into the symbol name itself. Copies that disagree on the number define *different* symbols and cannot collide there at all. Same rdma-core lineage, same number: one name, two definitions, a collision waiting for a scope.

#### 2) Symbol versioning does not block the match

The bundled definition had to be visible where the resolver looked, and symbol versioning ([the version contract Part 1 read out of `.gnu.version_r`](/blog/ELF-Linking-101/#71-version-glibc_234-not-found)) does not prevent that. glibc's [`check_match`](https://github.com/bminor/glibc/blob/master/elf/dl-lookup.c) takes an exact version match; failing that, an unversioned definition still gets in through two doors. If the defining object carries no version table at all, the symbol is accepted outright. The comment there reads, of all things, "This can happen during symbol interposition." And if it does carry one, a definition sitting at the global, unversioned index is kept as the fallback that wins when no exact match exists anywhere. What gets skipped is a definition under a *different* version tag. Same rdma-core lineage, same number, exported names: the capture follows.

### Appendix C: The reproducer's edge rows (hidden visibility and copy relocation)

The body kept the matrix's headline rows: A (default, no split), B (`-Bsymbolic-functions`, split), C (protected visibility, split). The full matrix has three more rows, and each edge teaches something:

| config | what changed | result |
|---|---|---|
| **A** | default build, no special flags | **no split**: same address; the shared library's own constructor is interposed onto the executable's copy, so everyone agrees |
| **B** | shared lib built `-Bsymbolic-functions` | **SPLIT**: constructor writes the .so copy, discovery reads the exe copy; "device not found" |
| **C** | protected visibility on the lib's internals | **SPLIT**: an equivalent self-binding trigger |
| **C′** | hidden visibility | the DSO is dropped by `--as-needed`, so the constructor never runs at all (a different failure); force it to load and the split reappears |
| **D1** | the colliding thing is a *data* table, static in the .so, global in the exe | **SPLIT** |
| **D2** | data table, global on both sides | **no split**: copy relocation quietly unifies everyone onto the executable's copy |

**C′ (hidden visibility)** is a reminder that "just hide the symbols" changes the failure rather than removing it: with nothing exported, `--as-needed` drops the unreferenced DSO from the link, and the constructor never runs at all. Force the DSO to load and the split reappears.

**D2 (copy relocation)** is the trap turned inside out. The "obvious" version of this bug, a duplicated plain data global, is the one the toolchain saved us from here, because copy relocation unified the copies: the executable gets its own copy of the data, and the shared library's references are pointed at it. That rescue is itself configuration-dependent (it hinges on how the executable references the data, and flags like `-z nocopyreloc` switch it off), not a law. D1 shows the same data table splitting the moment the .so side's copy is a file-static the rescue cannot reach.

### Appendix D: symsplit (a binding simulator, not a duplicate lister)

The distinction is the whole tool. `nm | sort | uniq -d` answers "does a duplicate exist," and on any real system it screams constantly about things that are fine: 468 of the 788 stock system binaries in the sweep had duplicate symbols somewhere in their closures, and not one was a split. bash defines its own `getenv` over libc's, which is benign because libc keeps an interposable reference to the name and unifies onto bash's copy. Thousands of weak libc aliases exist to be overridden. Versioned symbols with disjoint version sets can't collide.

`symsplit` models what `ld.so` actually does instead: `.dynsym` versus `.symtab` visibility, scope order, symbol versioning, and per-library self-binding inferred from relocations. The inference reads the absence section 4 described: a library that retains an interposable `JUMP_SLOT` or `GLOB_DAT` reference to one of its own exports demonstrably did *not* self-bind; one with none probably did. It flags a split only when two modules in one image would genuinely resolve the same name to different definitions. When it fires, it says why:

```text title="symsplit: verdict on the splitting config"
VERDICT  SEV     SYMBOL               WHY
SPLIT    MEDIUM  vx_get_device_list   libverbs_shared.so is probably
  self-binding (no JUMP_SLOT/GLOB_DAT to any own export =
  -Bsymbolic-functions signature); its own copy answers its constructor
  calls, while libcollective.so's reference resolves to app_B's copy
  -> two live copies diverge (split state)
```

It is honest about its own limits, too. `-Bsymbolic-functions` can't be proven from the ELF (a library with no self-references *looks* self-bound), so that inference carries a confidence label in the output. And dlopen scope is a runtime property the ELF doesn't record, so Route B modeling takes the scope layout as input rather than pretending to know it.

### Appendix E: The wheel survey (full numbers)

The survey behind section 7's headlines, all of it rerunnable from the scripts in [the reproducer repo](https://github.com/dshah133/howtf/tree/main/demo/rdma-symbol-collision).

#### 1) Route B: resident duplicate runtimes

Import faiss, scikit-learn, and torch into one Python process and `/proc/self/maps` shows two distinct builds each of libgomp, libgfortran, and libquadmath. numpy plus scipy alone maps two libgfortran and two libquadmath.

Tracing an actual compute workload (numpy matmul, torch matmul, a faiss index search) under `LD_DEBUG=bindings` shows 206 duplicated compute symbols binding to two different definitions at once in the same process, almost all of them OpenBLAS kernels, with faiss's statically embedded copy answering faiss's calls while numpy's libopenblas answers numpy's. Those kernels are code, not divergent state (partitioned binding caught live, not a demonstrated wrong answer), but the runtimes underneath them carry genuinely mutable state, thread pools and locks, and the same partition carries each side's runtime state with it. That is the substance behind Intel's `KMP_DUPLICATE_LIB_OK` kill-switch: the error it silences warns that duplicate OpenMP runtimes "can cause incorrect results."

#### 2) Route A: the trigger census

`DF_SYMBOLIC` is set on zero of the 366 libraries examined across the surveyed wheels, and `symsplit` predicts zero Route A splits across all eight co-load configurations tested (combinations of numpy, scipy, torch, faiss, and scikit-learn imported together).

#### 3) The import-order probe

The ingredient that promotes Route A is observable directly. `import torch` executes `ctypes.CDLL("libtorch_global_deps.so", RTLD_GLOBAL)`, lifting torch's OpenMP into the global scope. Under `LD_DEBUG=bindings`: import faiss alone and its extension module's OpenMP references bind faiss's bundled libgomp; import torch first and every one of those traced references rebinds to torch's copy instead.
