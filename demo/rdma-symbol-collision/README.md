# Static/dynamic symbol-collision demos (RDMA device failures)

Reproducible demos for a howtf.io post on how duplicate symbols across a
static/dynamic linking boundary quietly break device initialization — no linker
error, no crash, just the wrong device or an empty device list. Dressed up as
RDMA verbs so the failures are concrete.

## What "split-state linking" is (one paragraph)

When two copies of the same symbol are live in one process — one statically
linked into the executable, one inside a shared library — ELF's dynamic linker
makes the executable's copy **interpose** on the library's in the global lookup
scope. A load-time constructor can then register devices into one copy's table
while a dynamically-linked plugin does discovery against the **other** copy's
(empty) table, so the program reports **"device not found"** even though the
constructor demonstrably registered the devices. There is no linker error and no
crash. Whether the split actually happens is decided by link/visibility knobs
(`-Bsymbolic`, `-rdynamic`, visibility, copy relocation), which is why two
binaries built from identical sources can behave differently.

## One-command repro

Requires Docker (the mechanism is GNU/ELF-specific; macOS `ld64`/Mach-O does not
reproduce it, so the lab runs in a Linux container). On a native Linux box you
can run `make matrix` directly without the container.

```sh
cd local/split-state
./build-image.sh      # one-time toolchain image
./run.sh matrix       # the four-configuration gating experiment
```

### Expected output (the gating table)

`make matrix` builds the same two-copies scenario six ways and prints, for each,
the **address** of the table the constructor wrote vs. the table discovery read.
Same address = no split; different address = split.

| config | what it is | result |
|---|---|---|
| **A** default | DSO exports verbs fns, no flags | **no split** (constructor interposed onto exe copy; same address) |
| **B** `-Bsymbolic-functions` | DSO self-binds its calls | **SPLIT** (different addresses; "device not found") |
| **C** protected visibility | DSO internals protected | **SPLIT** |
| **C** hidden visibility | DSO internals hidden | DSO dropped by `--as-needed` (constructor never runs); forced to load → SPLIT |
| **D1** data table, DSO static / exe global | colliding thing is `vx_devices[]` | **SPLIT** |
| **D2** data table, global in both | same, global both sides | **no split** (copy relocation unifies onto the exe copy) |

Verdict printed by the run: the real split is in **B, C-protected,
C-hidden(forced-load), D1**; not in **A**, **D2**, or **C-hidden by default**.

## Pinned toolchain (tested)

| component | version |
|---|---|
| gcc | 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1) |
| GNU binutils (ld / nm / objcopy) | 2.42 |
| base image / OS | Ubuntu 24.04 |
| kernel for the soft-RoCE (`ec2/`) variant | 6.17.0-1019-aws (Ubuntu 24.04) |

The results above were reproduced on both `aarch64` (local container) and
`x86_64` (EC2) with this toolchain; the mechanism is architecture-independent.
`local/split-state/run.sh` bakes gcc + binutils 2.42 into the image and prints
`gcc --version` / `ld --version` at the top of `evidence.sh` output, so a wildly
different toolchain is visible in the captured `artifacts/00_toolchain.txt`.

## Layout

- **[`local/`](local/)** — hardware-free, container-based. Two subdirs:
  - **[`local/split-state/`](local/split-state/)** (PRIMARY) — the interposition
    "device not found" bug, the four-config gating experiment, both directions,
    per-binary nondeterminism, honest fix ladder, `LD_DEBUG` + address proofs.
  - **[`local/archive-order/`](local/archive-order/)** (SECONDARY) — the
    static-archive silent-misdirection bug and its fix taxonomy.
- **[`ec2/`](ec2/)** — the real-RDMA flavor on an EC2 box: both mechanisms
  driving **real `ibv_*`** against two soft-RoCE (`rxe`) devices, so "wrong
  device" / "no device found" is literal. See `ec2/split-state/` and `ec2/src/`.
- **[`survey/pilot/`](survey/pilot/)** — a pilot `nm`/`readelf` scan of real ML
  wheels measuring how common the duplicate-strong-symbol *precondition* is.

## What this proves / what it doesn't

**Proves:**
- The silent split-state failure is real and reproducible, on both arm64 and
  x86_64, and on real soft-RoCE devices (`ec2/`).
- It is **gated**: a default build does not split. The split needs two live
  copies *plus* a self-binding DSO (`-Bsymbolic`/protected visibility) *plus* an
  interposing executable — pinned by the four-config matrix with address proofs.
- The precondition (duplicate strong C symbols across co-importable libraries) is
  pervasive in real ML wheels (`survey/pilot/`: `libgomp`/`libgfortran`/OpenBLAS
  shipped as multiple bundled copies).

**Does not prove:**
- That any specific shipping application is broken today — the demos use
  contrived symbol names (`vx_*`) rather than real `ibv_*`, and duplicate symbols
  are a *precondition*, not a bug. A real silent split additionally needs the
  co-load to happen, a self-binding defining object, and diverging behavior
  between the copies.
- The pilot found **zero** `-Bsymbolic` `.so`s in its 8-wheel sample, so the
  split-state-specific *trigger* was not observed in the wild here (the broader
  duplicate-runtime interposition hazard is present regardless). Quantifying the
  trigger's prevalence needs the fuller survey.

## Honest headline on fixes

The defect is **more than one copy of a symbol**. Fixes that only adjust one
side's visibility (`-fvisibility=hidden`, a `local: *` version script,
`objcopy --localize-symbol`, `--start-group`) frequently **do not** fix it and
can move or trigger the bug — verified as honest negatives in the demos. What
reliably works: a **single canonical copy**, **namespacing** so the copies are
distinct symbols (`objcopy --redefine-sym`), or **not exporting the accidental
duplicate** (`--exclude-libs`). And `-Bsymbolic`, usually described as a fix, is
a **trigger** for the split-state bug. Every claim is backed by a captured
artifact.

Start at [`local/README.md`](local/README.md).
