# EC2 / real-rdma split-state ("no RDMA device found" — but they're right there)

The real-hardware mirror of [`../../local/split-state/`](../../local/split-state/).
A load-time **constructor** enumerates the **real** rdma devices
(`ibv_get_device_list`) into one copy's registry table, while the collective's
discovery binds to the **other** (empty) copy. The app then reports **no rdma
devices** even though `ibv_devices` — and the app's own preamble — list two.

## The gating experiment on real hardware (`make matrix`, `artifacts/02_matrix.txt`)

The same four-configuration matrix as the local demo, but the constructor
enumerates the **real** rxe devices. The split is real in exactly configs B and
C (DSO self-binds via `-Bsymbolic-functions`/`-Bsymbolic` **and** the executable
exports its copy); configs A (default, no self-bind) and D (`--exclude-libs,ALL`,
no export) find both real devices:

```
(A) default          register->STATIC  get_list<-STATIC  -> registry reports 2 rdma device(s)
(B) -Bsymbolic-funcs  register->SHARED  get_list<-STATIC  -> 0 device(s)  *** NO DEVICE FOUND ***
(C) -Bsymbolic full   register->SHARED  get_list<-STATIC  -> 0 device(s)  *** NO DEVICE FOUND ***
(D) -Bsym-funcs +excl register->SHARED  get_list<-SHARED  -> registry reports 2 rdma device(s)
```

This is the real-hardware confirmation that the default build does NOT split; you
need a self-binding DSO plus an interposing executable.

## What else reproduced (captured in `artifacts/`)

`bug` run (config B, `artifacts/05_bug_run.txt`):

```
[constructor in copy=SHARED] enumerating 2 REAL rdma device(s)
[register -> copy=SHARED] rxe_train (this copy now holds 1)
[register -> copy=SHARED] rxe_store (this copy now holds 2)
[get_list <- copy=STATIC] this copy holds 0 device(s)
== real rdma devices present on this host (ibv_get_device_list): 2 ==
  [0] rxe_train
  [1] rxe_store
== app: collective discovery via the (split) verbs registry ==
  collective: registry reports 0 rdma device(s)   *** NO DEVICE FOUND -- but the constructor enumerated the real devices into the OTHER copy ***
```

`fixed` run (single canonical copy, `artifacts/06_fixed_run.txt`):

```
  collective: registry reports 2 rdma device(s)
    opened rxe_train guid=0caff1fffeda5a37
    opened rxe_store guid=00e4e1fffe8815f9
```

`nm` (`03_nm_duplicate_symbols.txt`) shows `vx_get_device_list` defined in both
the executable and the DSO; `LD_DEBUG=bindings` (`04_ld_debug_bindings.txt`)
proves the collective bound to the executable's copy:

```
binding file build/libcollective.so [0] to ./build/rapp_bug [0]: normal symbol `vx_get_device_list'
```

## Reproduce

```sh
# on the instance (rxe devices already up via ../setup-rxe.sh):
cd ~/rdma-split/src   # or scp this src/ over
make matrix           # four-configuration gating experiment on the real rxe devices
make bug              # constructor fills the shared copy; discovery reads the static (empty) copy
make fixed            # single copy -> registry reports both real devices and opens them
make evidence         # capture matrix + nm + LD_DEBUG + runtime into ../artifacts/
```

Instance / teardown details are in [`../README.md`](../README.md). This mirror
uses the same box, key, and security group.

## Caveat

Same as the local split-state demo: the two copies are one `rverbs.c` compiled
twice rather than a vendored static `rdma-core` vs. the system `libibverbs.so`,
and the registry is contrived (`vx_*`) in front of the real `ibv_*` enumeration.
The device names/GUIDs and the `ibv_open_device` calls are real; the split
mechanism (two live copies, constructor fills one, discovery reads the other) is
identical to the real failure.
