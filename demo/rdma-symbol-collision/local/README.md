# Silent static-linking symbol collision that misdirects device init

A hardware-free, fully reproducible demo of the trap where two static archives
define the **same strong C symbol** with **different behavior**, the linker
silently picks one, and a library that shipped its own copy ends up running the
*other* library's version — with **no "multiple definition" error ever firing**.

The scenario is dressed up as RDMA-style device enumeration (`vx_open_device`),
so the payoff is concrete: a "checkpoint" collective that should target the
**storage NIC** silently initializes the **training NIC** instead.

## The bug in one paragraph

Static linking pulls object files out of an archive **on demand**: when the
linker hits `libfoo.a`, it only copies in the members that satisfy a symbol it
still needs. Once a symbol like `vx_open_device` is defined, the linker
considers that reference *closed* — it will not look at any later archive for
another definition of it. So if archive A (earlier on the command line) defines
`vx_open_device`, and archive B (later) *also* defines `vx_open_device` in one
of its members, B's member is **never pulled**. Everyone that calls
`vx_open_device` — including B's own code — runs **A's** implementation. No
error, because B's conflicting member never entered the link.

## Cause -> effect, in this demo

- Two "verbs" archives implement the same API but **enumerate devices in
  opposite order**:
  - `libverbs_vendor.a`:  logical 0 -> physical 0 = `training_nic`
  - `libverbs_bundled.a`: logical 0 -> physical 1 = `storage_nic`
- Two collectives each open **logical device 0**:
  - `collective_a` was built/tested against vendor verbs, expects `training_nic`.
  - `collective_b` was built/tested against bundled verbs, expects `storage_nic`.
- Link line (from the blog spec):
  `-lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled`
- The vendor archive is scanned first, so **its** `vx_open_device` satisfies
  *both* collectives. `collective_b`'s bundled copy is never pulled.
- Result at runtime:

```
collective_a (training/allreduce): opened logical dev 0 -> physical 0 -> training_nic [expected training_nic]  OK
collective_b (checkpoint/storage): opened logical dev 0 -> physical 0 -> training_nic [expected storage_nic]   *** WRONG DEVICE ***
```

`collective_b` thinks it opened the storage NIC; it actually opened the training
NIC. Silent misdirection.

## The precondition that makes it *silent*

**One colliding symbol per archive member.** Each archive keeps
`vx_open_device` and `vx_device_name` in **separate** object files
(`vendor_open.o`, `vendor_name.o`, ...). That granularity is what lets the
linker pull exactly the member it needs from the vendor archive and **skip** the
bundled archive's matching member entirely. If instead the colliding symbol
shared a member with something else the link needs, that member would be forced
in and you'd get a **loud** `multiple definition` error instead of silent
misdirection — see the `explicit-error` and `vendored-collision` targets. Whether
you get silence or an error is an accident of member layout, not of intent.

## Why this platform setup

The archive-member-pulling and first-definition-wins semantics here are **GNU
ld / ELF** behavior. macOS `ld64` / Mach-O resolve archives differently and
`objcopy` does not exist there, so the lab runs inside a Linux container with a
GNU toolchain (`gcc` + GNU binutils). The mechanism is architecture-independent;
the container is native `arm64` here, and the same results hold on `x86_64`.

## Run it

```sh
./build-image.sh          # one-time: build the toolchain container image
./run.sh collision        # the silent bug
./run.sh                  # run every variant (make all)
./evidence-in-docker.sh   # capture the full forensic chain into artifacts/
```

`build/` is disposable (git-ignored); `artifacts/` is the committed evidence.

## Targets

### Scenario 1 — cross-library interposition (the 4-archive link line above)

| target | what it shows |
|---|---|
| `collision` | the silent bug: `collective_b` opens the wrong NIC, no link error |
| `link-order` | reverse the two pairs -> bundled wins -> `collective_a` becomes the victim (order-dependent) |
| `explicit-error` | reference a symbol co-located with the bundled `vx_open_device` -> forces that member in -> real `multiple definition` error |
| `fixed-groups` | `--start-group/--end-group` — **does NOT fix it** (rescanning doesn't override first-definition-wins) |
| `fixed-visibility` | rebuild verbs with `-fvisibility=hidden` — **does NOT fix it** (hidden controls dynamic export, not static-archive resolution; nm still shows `T`) |
| `fixed-objcopy` | exp1 `--localize-symbol` on bundled — **no fix** (that member is never pulled anyway); exp2 `--redefine-sym` namespacing on `collective_b`+bundled — **FIXES it** |

### Scenario 2 — vendored copy, definition + use co-located (`ld -r` merged)

This is the shape where each big library statically vendors its *own* copy of the
verbs and calls them internally. Here the colliding symbol rides in the same
merged member as the entry point, so the collision is **loud**, and the
visibility/localize fixes genuinely apply (the internal call is co-located with
the definition).

| target | what it shows |
|---|---|
| `vendored-collision` | merged objects both keep a global `vx_open_device` -> **loud** `multiple definition` (not silent) |
| `vendored-fixed-localize` | `objcopy --localize-symbol` on each merged object -> symbols go `T`->`t` (local) -> each library uses its own copy -> **FIXED** |
| `vendored-fixed-visibility` | `-fvisibility=hidden` + `objcopy --localize-hidden` -> hidden globals become local -> **FIXED** |

## Honest fix verdict

For the **silent cross-library trap (scenario 1)**:

- **Does NOT help:** `--start-group`, `-fvisibility=hidden`, `objcopy
  --localize-symbol`. The first two don't change which definition wins; the
  third localizes a member the linker never pulls.
- **Does help:** **namespacing** (rename the colliding pair via `objcopy
  --redefine-sym`, or at the source), keeping a **single canonical copy** (don't
  vendor a second `rdma-core`), or vendoring **correctly** so each library
  privately owns its copy (scenario 2's `localize` / `hidden + localize-hidden`).

`-fvisibility=hidden` and `--localize-symbol` only work when the caller and the
definition it should bind to are in the **same object** (scenario 2). In the
cross-library layout (scenario 1) the caller and callee are in different
objects, so those tools cannot give `collective_b` a private binding.

## How this maps to the real Meta / RDMA scenario, and where it diverges

**Faithful:**
- GNU ld archive-member pull + first-definition-wins is exactly the real
  mechanism.
- "Two libraries each statically link their own copy of a verbs stack, and one
  silently runs the other's" is the real vendored-`rdma-core` failure mode.
- The forensic tools are the real ones you'd reach for: `nm` for duplicate
  strong symbols, `ld -Map`/`--cref` to see which member satisfied a reference,
  `objcopy` to inspect/fix bindings.

**Divergences (intentional, for a hardware-free demo):**
- Symbol names are contrived (`vx_open_device`) rather than real `ibv_*` from
  `rdma-core`, and "opening a device" just returns an index instead of touching
  hardware. The `ec2/` companion runs the same collision in front of **real**
  `ibv_open_device` against soft-RoCE devices.
- The device "misdirection" is modeled as an enumeration-order difference. Real
  collisions can differ in ABI/struct layout too, which can crash rather than
  quietly mistarget; this demo deliberately keeps behavior well-defined so the
  *silent* nature is unmistakable.
- Real archives contain many members; here each archive is minimal, but the
  decisive property (one colliding symbol per member) is the same one that makes
  real vendored stacks collide silently.

See `artifacts/SUMMARY.txt` for the generated one-screen verdict and the
numbered `artifacts/*.txt` / `*.map` files for the raw evidence behind every
claim above.
