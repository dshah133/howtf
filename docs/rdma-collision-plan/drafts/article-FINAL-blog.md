# howtf can a device be both present and not found?

> Publication draft. Sections 1, 2, and one beat of 6 contain bracketed ⟦Deep: …⟧ prompts — firsthand texture only Deep can supply. Everything else is finished prose over verified artifacts. Pin the torchcomms permalink and the repo URL before publishing. Figure placeholders are marked inline.

---

## 1. Present, and not found

The failure that starts this story fits in one line: a training job died at startup because its collective-communication library said the accelerator device was not found.

⟦Deep: the literal first line the job printed, and where you first saw it — a failed run, a task page, someone's ping. One or two sentences of scene.⟧

The device was not missing. It showed up in enumeration. The driver was loaded. On the same host, in the same kind of process, NCCL could see the very RDMA devices this library claimed didn't exist.

Some context, because the shape of the build matters later. This was at Meta. The binaries were application training binaries composed by Buck, with PyTorch built in-house and statically linked — hermetic builds and fast startup are worth a great deal at that scale. Static linking at that size has its own physics: once a binary pushes past the 2 GiB relocation barrier, the composition gets carved into link groups to keep it linkable at all. Torch was one ingredient; the final artifact was each application's own training binary. And some of those binaries, depending on what they trained on, carried support for MTIA, Meta's own accelerator — provided by an in-house collective-communication library that discovers its devices through RDMA the same way NCCL does.

So: two collective libraries in one process. NCCL/NCCLX for the GPUs, the in-house library for MTIA. Both walk the same device list at startup.

The GPU path worked everywhere. The MTIA path worked in most binaries and failed in others — "device not found," at startup, every time. All of them built from the same torch commit. The device was present by every check anyone could run, and absent according to the one piece of code whose opinion mattered.

That's the howtf. Same source. Same fleet. Same device, verifiably there. Whether a binary could see it depended on the binary.

## 2. The investigation

A missing device points you at the layers that own devices, and that's where the time went first.

⟦Deep: your actual first dead end — what got blamed first (firmware? driver? a flaky host? the accelerator itself?) and what ruled it out. Even one concrete suspect-and-acquittal makes this section yours.⟧

The checks that survived were the ones that made the problem stranger. The device enumerated — the in-house library sees the same RDMA device list NCCL sees, and NCCL, running in the same process image, found the devices fine. So the kernel was serving the device list correctly to a process that then reported it empty. The hardware and driver stack were effectively vouched for by a second, working consumer inside the same address space.

The source was vouched for too. These binaries came from one torch commit. Whatever was different, it wasn't the code anyone had written.

⟦Deep: how long this stayed open, roughly how many people got pulled in, and who first noticed the pattern below — the un-fakeable specifics.⟧

The observation that broke it open was about the failures' distribution: working versus broken didn't track hosts, and it didn't track commits. It tracked *binaries*. A given application binary either always found the device or never did. Different binaries, same source, opposite behavior — which means the difference had to live in the one step that distinguishes two binaries built from identical code. The link.

And once we looked there: the constructors had run. The dynamically linked libibverbs initialized; the in-house library's constructor ran and populated its device state. Discovery still came back empty. The state the constructor filled and the state discovery read had the same symbol names — and were not the same memory.

## 3. What was actually happening

Two things were true at once, and they should not have been. The library's constructor had run: it had walked the device list and written the results into its table. And the discovery call, a moment later, found that table empty. Both were reaching that state through the same symbol names. They were not reaching the same state.

A dynamically linked program is assembled from modules — the executable and the shared libraries it loads — and the loader resolves each name to exactly one definition per lookup. But "one definition per lookup" is not "one definition." If a strong, non-weak C symbol is defined in two modules, both definitions exist in the process image; which one a given reference binds to depends on where that reference lives and how its module was built. C has no one-definition rule to forbid the duplication, and the linker raises no error, because the two definitions never enter the same link: one is compiled into the executable, the other into a shared library, and a shared library's definitions do not collide at link time with the executable's.

Who wins, then, when both copies exist? Under the default rules, the executable does. The dynamic loader searches the global scope in breadth-first load order, and the executable is always first, so any dynamically resolved reference to the duplicated name — from any module — lands on the executable's copy. This is interposition, and it is a feature, the same one that lets `LD_PRELOAD` swap in a debugging allocator. Crucially, in a default build it applies to the shared library's *own* references too: the library calls its own function through the same lookup, gets the executable's copy like everyone else, and the process stays consistent on one winner. Wasteful, but coherent.

The failure needs one more ingredient: a library that opts out of being interposed. Build a shared library with `-Bsymbolic-functions` (or protected visibility) and its internal references are bound to its own definitions at link time, skipping the runtime lookup. Now the two copies stop agreeing. The library's constructor runs against the library's copy; every other module's references still resolve through the global scope to the executable's copy.

That is what these training binaries were. The device state — and the functions that filled and read it — existed in two places: statically inside the application binary, and inside the dynamically linked library, which had been built to self-bind. Its constructor wrote *its* copy. The code that performed device discovery resolved the same names under the default rules and bound to the executable's copy, which nothing had filled. The process was split into two halves that agreed on every symbol name and disagreed on every symbol's contents.

That is the whole disease, and it deserves a name, because it has none: **split-state linking** — two live copies of one library's state in a single process, with references silently partitioned between them.

*(figure: the two-copy split — exe copy vs. .so copy; constructor writes into the .so copy, discovery reads the exe copy)*

## 4. Proving it

A claim like that is easy to state and easy to doubt, so here is a reproducer you can run in minutes. It uses soft-RoCE (`rdma_rxe`), the kernel's software RDMA provider, so no special hardware is needed: two virtual RDMA devices, a small "verbs" library present in two copies (one static in the executable, one shared), and a collective that performs discovery. `make matrix` builds the same scenario six ways and prints, for each, the address of the table the constructor wrote and the address of the table discovery read. Same address, no split. Different address, split. The proof is a comparison of two hexadecimal numbers, not a matter of interpretation.

Here is the splitting configuration's actual output:

```
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

*(figure: the six-config matrix as a gate diagram — duplicate copies alone don't split; add self-binding and they do)*

Two rows are worth pausing on. Config **A** concedes the obvious objection: a default build does not split. Duplicate copies alone are not the bug; you need the self-binding trigger on top. And that trigger is routinely applied — `-Bsymbolic-functions` is a standard startup-performance and hardening flag, it's in plenty of build templates — with one nasty property: unlike full `-Bsymbolic`, which sets a `DF_SYMBOLIC` flag in the output, `-Bsymbolic-functions` leaves *no trace in the binary at all*. The linker simply resolves the internal calls and moves on. You cannot grep a .so for it after the fact. Config **D2** is the trap turned inside out: the "obvious" version of this bug, a duplicated plain data global, is exactly the one the toolchain saves you from, because copy relocation unifies the copies. The dangerous shape is a *function* (or state reached through one) in a *self-binding* library.

The reproducer also demonstrates the part that made the production incident so disorienting. Build the application twice from byte-identical source, once with the redundant static copy on the link line and once without:

```
### app_with_static    (redundant static copy linked):
  collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
### app_without_static (single copy):
  collective: discovered 2 device(s)
```

Same source, opposite behavior, decided entirely by link composition. In a Buck-style build where link groups and library composition vary per application, that is precisely "some binaries fine, some not, same commit."

Toolchain, for the record: gcc 13.3.0, binutils 2.42, Ubuntu 24.04, kernel 6.17-aws for the soft-RoCE variant; reproduced on both aarch64 and x86_64, and re-validated on a clean EC2 instance from the scripts alone, fresh RDMA GUIDs and all. The repo is at [repo link].

## 5. Why nothing warned

The uncomfortable part is that every component behaved exactly to spec. The ELF gABI forbids multiple `STB_GLOBAL` definitions only among the objects that *enter a link* — and a shared library's definition never enters the executable's link. The GNU ld manual describes archive members being pulled lazily, left to right, once. lld's documentation states the situation without alarm: two links can "both succeed but they have selected different objects from different archives that both define the same symbols." C has no one-definition rule and no COMDAT machinery for these symbols; nothing even checks that the two definitions are the same code.

So no diagnostic fires by default, and the opt-in diagnostics that exist each miss this class. `--warn-backrefs` catches order-dependent archive resolution, not cross-boundary duplication. gold's `--detect-odr-violations` is scoped to C++ mangled names and weak definitions, and needs debug info. `-z muldefs` governs duplicates *within* a link, and this pair never shares one.

People have run into this before, of course — Sergei Trofimovich wrote up a shared-library collision breaking real programs and landed on the same verdict, that the toolchain does not help much here. What has been missing is the recognition that these one-off war stories are a single failure class with a describable trigger.

A failure that produces a crash gets a stack trace. A failure that produces a wrong answer gets silence.

## 6. Fixes: the folklore one that fails, and the ones that work

The instinct, once you know two copies of a symbol are colliding, is to reach for visibility: rebuild the shared library with `-fvisibility=hidden`, or slap a `local: *` version script on it, and the duplicate should stop being exported. It does not fix this. Verified against the reproducer, both leave the split fully in place:

```
NAIVE FIXES THAT DO NOT WORK:
  nofix-visibility     :   collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
  nofix-version-script :   collective: discovered 0 device(s)   *** DEVICE NOT FOUND ***
```

They hide the wrong copy. Visibility controls what the shared library *exports*; it does nothing about the executable's copy, which is the one discovery was binding to all along. (Hiding the library's symbols can also get it dropped by `--as-needed` entirely, trading a split for a constructor that never runs.)

What works, verified, is making the two copies stop being the same symbol — or stop being two:

```
FIXES THAT WORK:
  fix-drop-duplicate :   collective: discovered 2 device(s)
  fix-exclude-libs   :   collective: discovered 2 device(s)
  fix-prefix-rename  :   collective: discovered 2 device(s)
```

Keep a single canonical copy. Or link the executable with `-Wl,--exclude-libs,ALL` so it stops dynamically exporting its static copy. Or rename one side's symbols with `objcopy --redefine-sym`. That last one is not hypothetical. Meta's public torchcomms repository ships a `rename_symbols.sh` that prefixes every `nccl*` symbol, with a comment saying it exists to avoid conflicting with the OSS `nccl*` bundled with PyTorch. The ecosystem shipped the rename fix years before the disease had a name.

⟦Deep: one or two sentences — which fix actually shipped in your incident, and was it the principled one or the expedient one under deadline?⟧

## 7. How common is this, really?

If the trigger is a linker flag that leaves no trace in the binary, you can't answer "how common?" by grepping. You have to model the binding. Doing that revealed something the incident alone didn't show: split-state linking arrives by *two* routes, not one.

**Route A — interposition capture** is the incident's shape: a duplicate strong symbol, a self-binding library, and an interposing module sharing one symbol scope. **Route B — scope partition** needs neither self-binding nor interposition. If two modules are loaded into separate local scopes — `RTLD_LOCAL`, the default for every `dlopen`, which is how Python loads extension modules — and each carries its own vendored copy of a library, then each side binds its own copy and runs its own state. Same disease, two live copies of one library's state, reached without any special flag at all.

To measure both, I built `symsplit`, a binding *simulator* rather than a duplicate lister. The distinction is the whole tool. `nm | sort | uniq -d` answers "does a duplicate exist," and on any real system it screams constantly about things that are fine: in a sweep of 788 stock system binaries, 468 had duplicate symbols somewhere in their closures, and not one was a split. bash defines its own `getenv` over libc's — benign, because libc keeps an interposable reference to the name and unifies onto bash's copy. Thousands of weak libc aliases exist to be overridden. Versioned symbols with disjoint version sets can't collide. `symsplit` models what `ld.so` actually does — `.dynsym` versus `.symtab` visibility, scope order, symbol versioning, and per-library self-binding inferred from relocations (a library that retains an interposable `JUMP_SLOT` or `GLOB_DAT` reference to one of its own exports demonstrably did *not* self-bind; one with none probably did) — and it flags a split only when two modules in one image would genuinely resolve the same name to different definitions. Against the reproducer matrix it flags exactly the splitting configuration and clears the rest. Against those 788 system binaries: zero false positives. When it does fire, it says why:

```
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

**Route A's exact trigger is absent from public wheels — which is itself the finding.** `DF_SYMBOLIC` is set on zero of the 366 libraries examined, and `symsplit` predicts zero Route A splits across all eight co-load configurations tested. The trigger lives where the incident lived: in monorepo static-link builds — Buck, Bazel, symbolic-binding hardening — that you cannot download from PyPI. That inaccessibility is a good part of why the class went undiagnosed for so long. But the ingredient that *promotes* Route A is one line away in software everyone runs: `import torch` executes `ctypes.CDLL("libtorch_global_deps.so", RTLD_GLOBAL)`, lifting torch's OpenMP into the global scope. An `LD_DEBUG` probe shows the consequence directly: import faiss alone and its extension module's OpenMP references bind faiss's bundled libgomp; import torch first and every one of those traced references rebinds to torch's copy instead. Which copy of a runtime your library gets is decided by Python import order.

The honest shape of the result: the preconditions are everywhere, the full Route A alignment is rare in public and lives behind corporate build systems, Route B is quietly resident in essentially every large ML process, and nothing warns at any tier. The ecosystem survives by paying a scattered tax — `KMP_DUPLICATE_LIB_OK`, auditwheel's content-hashed sonames (which *enable* coexisting copies rather than prevent them), torchcomms' `rename_symbols.sh`, conda's one-copy-per-environment discipline — four patches for one disease, none of them labeled with what they treat.

## 8. What should change

The diagnostic nobody built already has a name in the record. A `--warn-interposition` warning was floated on the GCC mailing list in May 2021 and never implemented in ld or lld. The reason it stalled is documented too: Fangrui Song (MaskRay), lld's maintainer, scoping the equivalent check, noted that the mechanics are easy but that "in the absence of an ignore list mechanism, this extension will not be useful" — interposition is a load-bearing ELF feature, and the base rate of benign duplication is enormous.

That missing ignore-list mechanism is exactly what `symsplit` is. The allowlist for intentional interposers (allocators, sanitizers), the weak/versioned/hidden/symtab-only filtering, the self-binding inference — demonstrated against real binaries with a zero-false-positive record on a 788-binary sweep. The tool stands alone today; the question worth putting to the linker maintainers, and I intend to, is whether an opt-in, allowlist-first version of the check belongs in lld or ld proper.

Until then, the checklist for anyone shipping large statically-or-mixed-linked binaries. If a dependency is built `-Bsymbolic` or `-Bsymbolic-functions`, and a strong C symbol it defines also exists anywhere else in your image, you have a latent split-state hazard that no default tool will flag. Scan for it. Prefer one canonical copy, or make the copies different symbols outright. And file the lesson somewhere it will be found at 2 a.m.: "device not found" can mean the device is right there, registered and waiting, in the copy of the world you didn't ask.

---

*Reproducer, scanner, and survey artifacts: [repo link]. Everything quoted above — the address matrix, the fix ladder, the sweep, the wheel survey — is a captured artifact in the repo, rerunnable from scripts.*
