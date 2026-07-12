# Split-state: mixed static/dynamic symbol interposition ("device not found")

The **primary** demo for the post. Two copies of the same "verbs" symbols are
live at once — one **statically linked into the executable**, one inside
**`libverbs_shared.so`**. A load-time **constructor** registers devices into one
copy's private table, while a dynamically-linked **collective** does discovery
against the **other** copy's (empty) table. Result: **"device not found"** even
though the constructor demonstrably registered the devices — they just landed in
the copy nobody reads. No linker error, no crash; just an empty list.

## The bug in one paragraph

On ELF, a symbol defined in the **executable** and exported to the dynamic
symbol table **interposes** on same-named symbols in shared libraries: every
dynamic lookup of that name resolves to the executable's copy first. So when the
executable statically links its own copy of the verbs layer (and exports it, via
`-rdynamic` or simply because a DSO also defines the name), a plugin/collective
that calls `vx_get_device_list()` binds to the **executable's** copy. If the
provider `.so`'s constructor registered devices into the **`.so`'s** copy
(because the `.so` was built `-Bsymbolic`, or its symbols are otherwise
self-bound), registration and discovery are now looking at two different tables.
The devices are registered; discovery reads the wrong table; you get "device not
found."

## What reproduced (captured in `artifacts/`)

`bug-a` runtime trace (`artifacts/02_bug_a_run.txt`) — the constructor fills the
SHARED copy, discovery reads the STATIC copy:

```
[constructor in copy=SHARED] registering rxe_train, rxe_store
[register -> copy=SHARED] this copy's table now holds 1 device(s)
[register -> copy=SHARED] this copy's table now holds 2 device(s)
[get_list <- copy=STATIC] this copy holds 0 device(s)
collective: discovered 0 device(s)   *** DEVICE NOT FOUND -- ...into the OTHER copy ***
```

Dynamic-linker proof (`artifacts/03_ld_debug_bindings.txt`, `LD_DEBUG=bindings`):

```
binding file build/libcollective.so [0] to ./build/app_bug_a [0]: normal symbol `vx_get_device_list'
```

The collective's `vx_get_device_list` bound to `app_bug_a` — the executable's
empty static copy. `nm` (`artifacts/01_nm_duplicate_symbols.txt`) shows the same
`vx_*` symbols defined in **both** the executable and the DSO.

### Both directions reproduce

- **`bug-a`** — registration → shared (DSO) copy, discovery → static (exe) copy.
- **`bug-b`** — the inverse: constructor in the static copy, discovery bound to
  the shared copy (`artifacts/04_bug_b_run.txt`). Same "device not found".

### Per-binary nondeterminism (identical sources)

`artifacts/05_nondeterminism.txt` — `app.c` and both `.so`s are byte-identical;
the **only** difference is one token on the app link line (whether the redundant
static copy is linked):

```
app_with_static    (redundant static copy linked):  discovered 0 device(s)   *** DEVICE NOT FOUND ***
app_without_static (single copy):                    discovered 2 device(s)
```

## Fix ladder — verified, honest (`artifacts/06_fix_ladder.txt`)

**Fixes that work:**

- **`fix-drop-duplicate`** — don't statically link a second copy; keep one
  canonical provider. (The real root-cause fix.)
- **`fix-exclude-libs`** — keep the static copy but stop the executable
  exporting it (`-Wl,--exclude-libs,ALL`), so discovery binds to the shared copy
  where the constructor registered.
- **`fix-prefix-rename`** — `objcopy --redefine-sym` to namespace the provider's
  symbols (and the collective's reference), so the two copies are distinct
  symbols that cannot interpose.

**Naive fixes that DO NOT work here (honest negatives):**

- **`nofix-visibility`** — `-fvisibility=hidden` on the DSO: still "device not
  found". It hides the DSO's copy, but the collective was binding to the
  **executable's** copy, not the DSO's. Wrong side.
- **`nofix-version-script`** — a `local: *;` version script on the DSO: same
  result, same reason.

**`-Bsymbolic` is a TRIGGER, not a fix** (`trigger-bsymbolic`): building the DSO
`-Bsymbolic` self-binds its constructor to the DSO's own copy, creating the
split. Without it, the constructor's registration is interposed onto the same
static copy discovery reads, and the devices are found. This is the opposite of
how `-Bsymbolic` is usually described, which is exactly why the bug is so
confusing in the wild.

The through-line: the defect is having **two copies of one symbol** across a
static/dynamic boundary. Every "fix" that only adjusts one side's visibility can
move the bug around (or trigger it); the reliable fixes remove the duplicate or
make the two copies genuinely distinct symbols.

## Run it

```sh
./build-image.sh          # one-time toolchain image (shared with ../archive-order)
./run.sh bug-a            # the primary bug
./run.sh bug-b nondeterminism trigger-bsymbolic
./run.sh fix-drop-duplicate fix-exclude-libs fix-prefix-rename
./run.sh nofix-visibility nofix-version-script
./run.sh                  # all targets
./evidence-in-docker.sh   # capture nm + LD_DEBUG + runtime into artifacts/
```

`build/` is disposable (git-ignored); `artifacts/` is the committed evidence.

## How this maps to the real scenario, and divergences

**Faithful:** ELF executable-interposes-DSO resolution, load-time constructors
registering into a global table, and a plugin/collective doing discovery are
exactly the real mechanism. `nm` and `LD_DEBUG=bindings` are the real tools you'd
use to diagnose it. `-Bsymbolic` / version scripts / `-rdynamic` are the real
knobs that decide which copy wins.

**Divergences:** symbol names are contrived (`vx_*`) rather than real `ibv_*`;
"registering a device" fills an in-process array instead of touching hardware
(the `ec2/` companion puts the same split in front of real `ibv_*`); and the two
copies are made from one `verbs.c` compiled twice, whereas a real incident has
one copy from a vendored static `rdma-core` and one from the system
`libibverbs.so`. The decisive property — two live copies of one symbol across a
static/dynamic boundary — is identical.
