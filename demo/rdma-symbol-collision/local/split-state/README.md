# Split-state: mixed static/dynamic symbol interposition ("device not found")

The **primary** demo for the post. Two copies of the same "verbs" symbols are
live at once — one **statically linked into the executable**, one inside
**`libverbs_shared.so`**. A load-time **constructor** registers devices into one
copy's private table, while a dynamically-linked **collective** does discovery
against a copy. When registration and discovery bind to **different** copies, the
collective sees an empty table → **"device not found"** even though the
constructor demonstrably registered the devices. No linker error, no crash; just
an empty list.

## Read this first: the split is NOT automatic

A naive telling of this bug is **wrong for default builds**, and the demo says so
up front. On ELF, a symbol defined in the executable and exported to the dynamic
table **interposes** on same-named symbols in shared libraries. If the DSO's
constructor calls its registration through the normal PLT/GOT, that call is
**also** interposed onto the executable's copy — so registration and discovery
both land on the executable's copy and there is **no split** (the devices are
found). The split is real only when **two conditions hold at once**:

1. the executable **exports/interposes** its static copy (e.g. `-rdynamic`, or
   simply because a linked DSO defines the same name), and
2. the DSO **self-binds** its own internal calls, so the constructor writes the
   **DSO's** copy instead of the interposed executable copy — which is exactly
   what `-Bsymbolic-functions` / `-Bsymbolic` (very common hardening/optimization
   flags) do.

## The gating experiment (`make matrix`) — the centerpiece

Four configurations pin exactly when the split is real. Every register/lookup
prints the **address** of the table it touched, so "constructor wrote one table,
discovery read another" is proven by comparing addresses, not by trusting a
label. Full output in `artifacts/01_matrix.txt`.

| config | what it is | constructor writes | discovery reads | split? |
|---|---|---|---|---|
| **A** default | DSO exports verbs fns, no flags | exe copy (interposed) | exe copy — **same address** | **NO** |
| **B** `-Bsymbolic-functions` | DSO self-binds its function calls | DSO copy (self-bound) | exe copy — **different address** | **YES** |
| **C** protected visibility | DSO internals protected | DSO copy | exe copy — different | **YES** |
| **C** hidden visibility | DSO internals hidden | — | exe copy (empty) | **DSO dropped** by `--as-needed` — constructor never runs (a *different* failure); forced to load → split |
| **D1** data table, DSO static | colliding thing is the `vx_devices[]` array | DSO's static table | exe's global table — different | **YES** |
| **D2** data table, global in both | same, but table global on both sides | exe BSS copy (copy-reloc) | exe BSS copy — **same address** | **NO** |

Verdict: the real split is in **B, C-protected, C-hidden(forced-load), and D1**.
It does **not** happen in **A** (the executable's copy interposes the DSO's own
constructor call, so both land on the exe copy), in **D2** (a plain data global
gets a **copy relocation** into the executable's BSS, so everyone shares the exe
copy), or in **C-hidden by default** (the hidden DSO is dropped by `--as-needed`,
which is a *dropped-library* failure, not a state split).

**Precondition, in one sentence:** the split needs two live copies of the symbol
**and** a DSO that self-binds its own internal use — via `-Bsymbolic(-functions)`
or protected/hidden visibility — so the DSO's constructor writes the DSO's copy
while the rest of the program reads the executable's copy; a plain data global
does *not* split, because copy relocation gives everyone the executable's copy.

## The bug, and the evidence

`bug` runtime trace (config B, `artifacts/01_matrix.txt` section B) — constructor
fills the SHARED copy, discovery reads the STATIC copy:

```
[constructor in copy=SHARED] registering rxe_train, rxe_store
[register -> copy=SHARED table@0xffffa4a10028] now holds 2 device(s)
[get_list <- copy=STATIC table@0xaaaae6100018] this copy holds 0 device(s)
collective: discovered 0 device(s)   *** DEVICE NOT FOUND -- ...into the OTHER copy ***
```

The two different `table@` addresses are the proof of the split. Dynamic-linker
proof (`artifacts/02_ld_debug_bindings.txt`, `LD_DEBUG=bindings,symbols`): in (A)
both the DSO's own `vx_register_device` and the collective's `vx_get_device_list`
bind to the executable; in (B) only the collective binds to the executable while
the DSO's register is self-bound. `nm` (`artifacts/03_nm_duplicate_symbols.txt`)
shows the same `vx_*` symbols defined in **both** the executable and the DSO.

### Both directions, and nondeterminism

- **`bug-b`** — the inverse: constructor in the static copy, discovery bound to
  the shared (empty) copy (`artifacts/04_bug_b_run.txt`).
- **`nondeterminism`** (`artifacts/05_nondeterminism.txt`) — `app.c` and both
  `.so`s are byte-identical; the **only** difference is one token on the app link
  line (whether the redundant static copy is linked): one binary finds the
  devices, the other does not.

## Fix ladder — verified, honest (`artifacts/06_fix_ladder.txt`)

**Fixes that work:** `fix-drop-duplicate` (one canonical copy — the root-cause
fix), `fix-exclude-libs` (`-Wl,--exclude-libs,ALL` so the executable stops
exporting its copy = config D), `fix-prefix-rename` (`objcopy --redefine-sym`
namespacing so the two copies are distinct symbols).

**Naive fixes that DO NOT work here (honest negatives):** `-fvisibility=hidden`
on the DSO and a `local: *;` version script on the DSO both still produce "device
not found" — they hide the DSO's copy, but discovery was binding to the
**executable's** copy, not the DSO's. Wrong side.

**`-Bsymbolic-functions` is a TRIGGER, not a fix** (see the matrix: it moves
config A to config B).

## Run it

```sh
./build-image.sh          # one-time toolchain image (shared with ../archive-order)
./run.sh matrix           # the four-configuration gating experiment
./run.sh bug bug-b nondeterminism
./run.sh fix-drop-duplicate fix-exclude-libs fix-prefix-rename
./run.sh nofix-visibility nofix-version-script
./run.sh                  # all targets
./evidence-in-docker.sh   # capture the matrix + nm + LD_DEBUG + fix ladder into artifacts/
```

`build/` is disposable (git-ignored); `artifacts/` is the committed evidence.

## How this maps to the real scenario, and divergences

**Faithful:** ELF executable-interposes-DSO resolution, load-time constructors
registering into a table, and a plugin/collective doing discovery are the real
mechanism; `-Bsymbolic(-functions)`, `-rdynamic`, and `--exclude-libs` are the
real knobs that decide which copy wins; `nm` and `LD_DEBUG=bindings` are the real
diagnostic tools.

**Divergences:** symbol names are contrived (`vx_*`) rather than real `ibv_*`;
"registering a device" fills an in-process array instead of touching hardware
(the `../../ec2/split-state/` companion puts the same split in front of real
`ibv_*` enumeration on soft-RoCE devices); and the two copies are one `verbs.c`
compiled twice, whereas a real incident has one copy from a vendored static
`rdma-core` and one from the system `libibverbs.so`. The decisive property — two
live copies of one symbol across a static/dynamic boundary, plus a self-binding
DSO and an interposing executable — is identical.
