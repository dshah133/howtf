# Silent static-linking symbol collision (RDMA device misdirection)

Reproducible demo for a howtf.io post: how linking two static archives that both
define the same strong C symbol can **silently** make one library run the
other's implementation, with **no "multiple definition" error**. Dressed up as
RDMA device enumeration, the failure is concrete: a checkpoint collective that
should target the **storage NIC** silently initializes the **training NIC**.

## The mechanism

Static linking pulls archive members **on demand**. When the linker reaches an
archive, it only copies in the members needed to satisfy still-undefined
symbols. Once `vx_open_device` is defined by an earlier archive, that reference
is closed — the linker will not look at any later archive for another copy. So a
later library that shipped its own `vx_open_device` never gets its member
pulled, and silently runs the earlier archive's version. The precondition that
keeps it silent (instead of a loud `multiple definition` error) is **one
colliding symbol per archive member**, so the conflicting member is never forced
into the link for another reason.

## Two parts

- **[`local/`](local/)** — the must-have, hardware-free demo. Runs inside a
  Linux/GNU-toolchain container anywhere. Reproduces the silent misdirection, the
  loud-error contrast, the link-order dependence, and honestly tests every fix
  (`--start-group`, `-fvisibility=hidden`, `objcopy --localize-symbol` /
  `--redefine-sym`, plus the correctly-vendored co-located case). Full forensic
  chain (`nm`, `ld -Map`/`--cref`, runtime) captured in `local/artifacts/`.
- **[`ec2/`](ec2/)** — the real-RDMA flavor on an EC2 box: the colliding symbol
  is the device-selection path in front of **real `ibv_open_device`** against two
  soft-RoCE (`rxe`) devices, so "wrong device" is literal. Evidence in
  `ec2/artifacts/`; instance details and teardown in `ec2/README.md`.

## Honest verdict on fixes (see `local/README.md` for the full table)

For the silent **cross-library** trap: `--start-group`, `-fvisibility=hidden`,
and `objcopy --localize-symbol` do **not** fix it (the colliding definition and
its intended caller are in different objects, and the second member is never
pulled). What does work: **namespacing** (`objcopy --redefine-sym` / rename), a
**single canonical copy**, or **vendoring correctly** so each library privately
owns its copy (compile hidden + localize, demonstrated on the co-located
`vendored-*` targets). Every claim is backed by a captured artifact.

Start at [`local/README.md`](local/README.md).
