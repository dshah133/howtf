# EC2 / real-RDMA flavor of the symbol collision

Two real-hardware variants run on this box:

- **this directory (`src/`)** — the static-archive collision driving real
  `ibv_open_device`: a collective opens the **wrong** rxe device.
- **[`split-state/`](split-state/)** — the mixed static/dynamic interposition
  bug: a constructor enumerates the real rxe devices into one copy while
  discovery reads the other, so the app reports **no rdma device** while
  `ibv_devices` shows two. (Mirror of `../local/split-state/`.)

The rest of this file documents the static-archive variant.

Same silent static-linking collision as `../local/archive-order/`, but the
colliding symbol (`vx_select_device`) sits in front of **real
`ibv_open_device`** against two **soft-RoCE (rxe)** devices. The payoff is literal: a checkpoint collective that
should open the **storage** NIC silently opens the **training** NIC via a real
rdma-core call.

## What reproduced (captured in `artifacts/`)

Two real rdma devices on the box (`artifacts/01_rdma_link.txt`,
`02_ibv_devices.txt`):

```
link rxe_train/1 state ACTIVE ... netdev ens5
link rxe_store/1 state ACTIVE ... netdev rxedummy0
    rxe_train   0caff1fffeda5a37
    rxe_store   00e4e1fffe8815f9
```

Collision run against real rdma (`artifacts/07_collision_run.txt`):

```
  collective_a (training/allreduce)  selected=rxe_train opened=rxe_train guid=0caff1fffeda5a37  [expected rxe_train]  OK
  collective_b (checkpoint/storage)  selected=rxe_train opened=rxe_train guid=0caff1fffeda5a37  [expected rxe_store]  *** WRONG DEVICE ***
```

`collective_b` called the real `ibv_open_device` on `rxe_train` (training NIC
GUID) when it intended `rxe_store`. No link error. The linker map
(`artifacts/06_collision_which_member.txt`) proves the vendor member was pulled
and the bundled member was **never pulled** (0 occurrences), and `nm`
(`05_nm_duplicate_symbols.txt`) shows both archives define a strong
`T vx_select_device`.

Fix run (`artifacts/08_fixed_run.txt`), objcopy `--redefine-sym` namespacing:

```
  collective_b (checkpoint/storage)  selected=rxe_store opened=rxe_store guid=00e4e1fffe8815f9  [expected rxe_store]  OK
```

## The instance

| | |
|---|---|
| instance-id | `i-0921470a840598528` |
| public IP | `54.81.26.174` (SSH restricted to Deep's IP only) |
| type / region / AZ | `t3.large` / `us-east-1` / `us-east-1d` |
| AMI | `ami-0a02a779008fa3b99` (Ubuntu 24.04 amd64) |
| kernel | `6.17.0-1019-aws` |
| security group | `sg-0aa45b47a44de365c` (inbound: tcp/22 from Deep's IP) |
| key pair | `howtf-rdma-key` (private key ONLY in the session scratchpad, never in the repo) |
| approx cost | t3.large on-demand ~**$0.083/hr** (~$2/day) + 20 GB gp3 EBS ~$1.60/mo |

The instance is intentionally **left running** for iteration. Terminate it with
`./teardown.sh` when done.

## Reproduce from scratch

```sh
# 0. from your machine: launch (see the exact aws commands in the parent report)
#    then SSH in with the scratchpad key:
ssh -i <scratchpad>/howtf-rdma-key.pem ubuntu@54.81.26.174

# 1. install toolchain + rdma-core
sudo apt-get update
sudo apt-get install -y rdma-core ibverbs-utils libibverbs-dev build-essential \
    iproute2 linux-modules-extra-$(uname -r)

# 2. bring up two soft-RoCE devices (rxe_train on the real NIC, rxe_store on a dummy)
./setup-rxe.sh          # or run the commands inside it

# 3. build + run
cd src
make collision          # silent: collective_b opens the WRONG real device
make fixed              # namespacing fix: collective_b opens rxe_store
make evidence           # capture nm + link map + ibv state + runtime to ../artifacts/
```

## Teardown

```sh
./teardown.sh
# terminates i-0921470a840598528, deletes sg-0aa45b47a44de365c and key pair
# howtf-rdma-key, then reminds you to delete the scratchpad private key.
```

Equivalently, by hand:

```sh
aws ec2 terminate-instances --region us-east-1 --instance-ids i-0921470a840598528
aws ec2 wait instance-terminated --region us-east-1 --instance-ids i-0921470a840598528
aws ec2 delete-security-group --region us-east-1 --group-id sg-0aa45b47a44de365c
aws ec2 delete-key-pair --region us-east-1 --key-name howtf-rdma-key
```

## Notes / caveats

- **Soft-RoCE worked** on this AWS kernel (`6.17.0-1019-aws`): `rdma_rxe`
  loaded, two `rxe` devices came up ACTIVE, and `ibv_open_device` opened them.
  No special/EFA hardware was needed.
- The `rxe_store` device rides on a `dummy` netdev, so it carries no real
  traffic; it exists to give `ibv_open_device` a genuinely distinct second
  device (distinct name + GUID) to open. The collision and the misdirected
  `ibv_open_device` call are real; end-to-end RDMA data transfer over the dummy
  link is not exercised.
- Symbol names remain contrived (`vx_select_device`) rather than real `ibv_*`;
  the mechanism (duplicate strong symbol, first-definition-wins, member never
  pulled) is identical to a real vendored-`rdma-core` collision.
