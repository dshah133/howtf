# Static/dynamic symbol-collision demos (RDMA device failures)

Reproducible demos for a howtf.io post on how duplicate symbols across a
static/dynamic linking boundary quietly break device initialization — no linker
error, no crash, just the wrong device or an empty device list. Dressed up as
RDMA verbs so the failures are concrete.

## The two mechanisms

**1. Split state (mixed static + dynamic interposition) — the primary story.**
Two copies of the same symbols are live at once: one statically linked into the
executable, one inside a shared library. On ELF, the executable's copy
**interposes** on the DSO's in the global lookup scope. So a load-time
constructor can register devices into one copy's table while a dynamically-linked
collective does discovery against the **other** copy's (empty) table →
**"device not found"** with the devices demonstrably registered.

Crucially, this is **not** automatic: in a default build the DSO's own
constructor is interposed onto the executable's copy too, so registration and
discovery agree and there is no split. The split needs **both** a self-binding
DSO (`-Bsymbolic-functions`/`-Bsymbolic`) **and** an executable that exports its
copy — pinned by a four-configuration gating experiment (`make matrix`). Two
binaries from identical sources, differing only in link composition, can behave
differently.

**2. Static-archive order (the simpler teaching case).** Two `.a` archives
define the same strong C symbol; the linker satisfies the reference from
whichever archive it scans first and never pulls the other's member, so a later
library **silently** runs the wrong copy — again with no `multiple definition`
error. Here it surfaces as a collective opening the wrong NIC.

## Layout

- **[`local/`](local/)** — hardware-free, container-based. Two subdirs:
  - **[`local/split-state/`](local/split-state/)** (PRIMARY) — the interposition
    "device not found" bug, both directions, per-binary nondeterminism, honest
    fix ladder with `LD_DEBUG=bindings` proof.
  - **[`local/archive-order/`](local/archive-order/)** (SECONDARY) — the
    static-archive silent-misdirection bug and its fix taxonomy.
- **[`ec2/`](ec2/)** — the real-RDMA flavor on an EC2 box: the archive-order
  collision driving **real `ibv_open_device`** against two soft-RoCE (`rxe`)
  devices, so "wrong device" is literal.

## Honest headline on fixes

The defect in both cases is **more than one copy of a symbol**. Fixes that only
adjust one side's visibility (`-fvisibility=hidden`, a `local: *` version script,
`objcopy --localize-symbol`, `--start-group`) frequently **do not** fix it and
can move or trigger the bug — verified as honest negatives in the demos. What
reliably works: a **single canonical copy**, **namespacing** so the copies are
distinct symbols, or **not exporting the accidental duplicate**
(`--exclude-libs`). And `-Bsymbolic`, usually described as a fix, is a **trigger**
for the split-state bug. Every claim is backed by a captured artifact.

Start at [`local/README.md`](local/README.md).
