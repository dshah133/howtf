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

Four configurations pin exactly when the split is real. The trace prints which
copy the constructor wrote (`register ->`) versus which copy discovery read
(`get_list <-`). Full output in `artifacts/01_matrix.txt`:

| config | DSO self-binds? | exe exports its copy? | constructor writes | discovery reads | result |
|---|---|---|---|---|---|
| **A** | no (default) | yes (`-rdynamic`) | STATIC (interposed) | STATIC | **found — no split** |
| **B** | yes (`-Bsymbolic-functions`) | yes | SHARED (self-bound) | STATIC (empty) | **NOT FOUND — split** |
| **C** | yes (`-Bsymbolic` full) | yes | SHARED | STATIC (empty) | **NOT FOUND — split** |
| **D** | yes (`-Bsymbolic-functions`) | no (`--exclude-libs,ALL`) | SHARED | SHARED | **found — no split** |

Verdict: the split occurs in exactly **B and C**. Config **A** removes the
self-binding; config **D** removes the executable's interposition — either one
alone eliminates the split. So the bug needs a duplicate copy **plus** a
self-binding DSO **plus** an interposing executable.

## The bug, and the evidence

`bug` runtime trace (config B, `artifacts/01_matrix.txt` section B) — constructor
fills the SHARED copy, discovery reads the STATIC copy:

```
[constructor in copy=SHARED] registering rxe_train, rxe_store
[register -> copy=SHARED] this copy's table now holds 2 device(s)
[get_list <- copy=STATIC] this copy holds 0 device(s)
collective: discovered 0 device(s)   *** DEVICE NOT FOUND -- ...into the OTHER copy ***
```

Dynamic-linker proof (`artifacts/03_ld_debug_bindings.txt`, `LD_DEBUG=bindings`):
the collective's `vx_get_device_list` binds to the executable's empty static
copy. `nm` (`artifacts/02_nm_duplicate_symbols.txt`) shows the same `vx_*`
symbols defined in **both** the executable and the DSO.

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
