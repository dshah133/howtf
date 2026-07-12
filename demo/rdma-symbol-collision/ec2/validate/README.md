# Fresh-instance validation

A stranger-reproduces-it check: a brand-new clean EC2 instance, set up from the
scripted steps **alone** (no manual fixups), reproduces the gating matrix.

## Result: PASS (clean, from scripts alone)

- Instance: fresh `t3.large`, Ubuntu 24.04.4, x86_64, kernel `6.17.0-1019-aws`
  (AMI `ami-0a02a779008fa3b99`), tagged `Name=howtf-rdma-repro-validate`,
  `project=howtf`. Launched clean, validated, then **terminated** (disposable).
- Steps run, in order, nothing else:
  1. `apt-get install -y build-essential binutils make rdma-core ibverbs-utils libibverbs-dev iproute2 linux-modules-extra-$(uname -r)`
  2. `bash ec2/setup-rxe.sh` — brought up two soft-RoCE devices (`rxe_train`,
     `rxe_store`) ACTIVE with no manual steps.
  3. `cd local/split-state && make matrix` — the canonical four-config gating
     table (`artifacts/01_local_matrix.txt`).
  4. `cd ec2/split-state/src && make matrix` — the same gating on **real** rxe
     devices (`artifacts/02_ec2_realhw_matrix.txt`).

## What reproduced

Canonical `make matrix` (`01_local_matrix.txt`) — identical verdict to the
ground-truth box and the local aarch64 run, proven by table addresses:

- (A) default → same `table@` for register and get_list → **no split**, 2 devices
- (B) `-Bsymbolic-functions` → different addresses → **SPLIT**, 0 devices
- (C) protected → SPLIT; hidden → dropped by `--as-needed`, forced-load → SPLIT
- (D1) data table static/global → different addresses → **SPLIT**
- (D2) data table global-both → **same** address (copy relocation) → **no split**

Real-hardware `make matrix` (`02_ec2_realhw_matrix.txt`) — the constructor
enumerated the real `rxe_train`/`rxe_store` devices; (A) and (D) find 2 devices,
(B)/(C) report 0 ("NO DEVICE FOUND") with a different table address.

## Gaps found in the README/scripts

None that blocked reproduction. The scripted path (`setup-rxe.sh` + the two
Makefiles) reproduced the full table with no manual intervention. The one thing a
stranger needs that isn't a script is the `apt-get install` line for the
toolchain + rdma-core (now listed above and in the top-level README).

See `artifacts/00_environment.txt` for the `rdma link` / `ibv_devices` / gcc / ld
/ kernel of the fresh box.
