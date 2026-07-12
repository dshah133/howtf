# Local (hardware-free) demos

Two related static/dynamic linking traps, each fully reproducible in a
Linux/GNU-toolchain container (macOS `ld64`/Mach-O does not reproduce ELF
archive/interposition semantics, and `objcopy` doesn't exist there — so both
demos run inside the shared `howtf-rdma-lab` container image).

## [`split-state/`](split-state/) — PRIMARY

Mixed **static + dynamic** symbol interposition. Two copies of the same symbols
are live at once (one statically in the executable, one in a `.so`); a
constructor registers devices into one copy while discovery reads the other's
empty table → **"device not found"** with the devices demonstrably registered.
Includes both directions, per-binary nondeterminism from identical sources, and
an honest fix ladder (with `LD_DEBUG=bindings` proof). This is the shape of the
real incident.

## [`archive-order/`](archive-order/) — SECONDARY (teaching demo)

The simpler, purely **static-archive** version: two `.a`s define the same strong
symbol, the linker satisfies the reference from whichever it scans first, and a
later library silently runs the wrong one — no `multiple definition` error. Good
for introducing "the linker picks a copy for you" before the harder dynamic
case.

Each subdirectory is self-contained: `./build-image.sh` once, then `./run.sh
<target>` and `./evidence-in-docker.sh`. See each README for the target list and
the captured `artifacts/`.
