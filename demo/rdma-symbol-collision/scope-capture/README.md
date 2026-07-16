# Scope-capture reproducer (the production topology, no self-binding flag)

The [`../local/split-state`](../local/split-state) matrix triggers split-state
linking with the cleanest switch that produces it on demand: a self-binding
shared library (`-Bsymbolic-functions` / protected visibility) with a second
copy statically linked into the **executable**. That proves the disease, but it
is a *different door* from the production incident, where neither copy was in the
executable and no self-binding flag was involved.

This lab reconstructs the incident's **actual** binding topology, hardware-free,
and shows the split arising from **scope alone**.

## The topology

```
STARTUP / GLOBAL SCOPE
  libbundle.so ........... COPY A. DT_NEEDED dep of app -> global scope.
    owns registry A        Exports register_driver + get_device_list
                           (default visibility, VERB_1.0). No -Bsymbolic-functions.
                                          ^
                                          | register_driver  (global-first lookup)
                                          | -- the captured import --
LOCAL DLOPEN GROUP (RTLD_LOCAL)           |
  libregistryB.so.1 ...... COPY B. dlopen("libregistryB.so.1", RTLD_LOCAL).
    owns registry B        Names stay private. NCCL reads it by dlvsym(handle).
  libproviderB.so ........ "system mlx5". DT_NEEDED on copy B. Its ELF ctor
                           calls register_driver() as a plain extern import.
```

- **The write path.** `libproviderB.so`'s constructor calls `register_driver()`.
  That is an undefined import, resolved by the dynamic linker against the
  **global scope first** and the dlopen's own dependency group second. Copy A is
  in the global scope; copy B is only in the local group. So the registration
  lands in **copy A** -- captured -- even though copy B is the provider's own
  `DT_NEEDED` dependency sitting right beside it.
- **The read path.** NCCL takes its entry points by `dlvsym(handle_B, ...)`, a
  handle-scoped lookup that sees only copy B and its dependencies. So discovery
  reads **copy B's** registry, which no constructor ever filled.

Registration writes A; discovery reads B. Same device, two answers.

## Run it

Requires a GNU/Linux toolchain (glibc `ld.so`; the mechanism is ELF-specific).
No special hardware, no `libibverbs`.

```sh
make bug      # register writes copy A; NCCL (copy B) sees 0 devices
make fixed    # localize copy A's registrar; register writes copy B; NCCL sees it
make trace    # LD_DEBUG=bindings: provider's register_driver -> libbundle (copy A)
make evidence # capture all of the above into artifacts/
```

### `make bug` (captured in `artifacts/01_bug_run.txt`)

```
[providerB] ctor: register_driver(mlx5_from_providerB)  [plain extern import -> resolved by scope, not by caller]
[bundle / copy A] register_driver(mlx5_from_providerB) -> registry A @0x...88040 now holds 1 device(s)
...
    in-house consumer sees 1 device(s)   [OK -- reads the copy the registration landed in]
...
[registryB / copy B] get_device_list  <- registry B @0x...83060 holds 0 device(s)
    NCCL consumer sees 0 device(s)   *** No IB devices found -- the registration went to the OTHER copy ***
```

Registry A (`...88040`) and registry B (`...83060`) are different objects; the
write hit A, the `dlvsym` read hit B.

### `make trace` (captured in `artifacts/03_ld_bindings.txt`)

The loader's own binding trace, the decisive line first:

```
binding file libproviderB.so to libbundle.so: normal symbol `register_driver' [VERB_1.0]
binding file libregistryB.so.1 to libregistryB.so.1: normal symbol `get_device_list' [VERB_1.0]
binding file build/app to libbundle.so: normal symbol `get_device_list' [VERB_1.0]
```

The provider binds `register_driver` to **libbundle (copy A)**, not to its own
`DT_NEEDED` copy B. Copy B answers only its own `get_device_list`.

## The fix, and which side to hide (`make fixed`)

`bundle-fixed.map` localizes **copy A's** `register_driver`, so copy A stops
being the process's global registrar. The provider's import then falls through to
copy B, and registration and discovery reunite on the copy NCCL reads:

```
[registryB / copy B] register_driver(...) -> registry B @0x...c9060 now holds 1 device(s)
    in-house consumer sees 0 device(s)
    NCCL consumer sees 1 device(s)
```

This is the instructive part. Hiding the **accidental global interposer (copy A)**
fixes the split. That is the *opposite side* from the `-Bsymbolic-functions` lab,
where hiding the shared copy changes the wrong side and leaves the split in place.
Visibility fixes are topology-dependent: you have to hide the copy that is
wrongly winning the global lookup, which here is the bundled one.
`artifacts/04_symbols.txt` shows `register_driver` present in copy A's `.dynsym`
in the bug build and absent in the fixed build.

## Toolchain (validated)

| component | version |
|---|---|
| gcc | 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1) |
| GNU binutils (ld) | 2.42 |
| kernel / OS | 6.17.0-1019-aws / Ubuntu 24.04, x86_64 |

Validated on a clean EC2 x86_64 instance. The mechanism is architecture- and
hardware-independent; only a glibc `ld.so` is required.
