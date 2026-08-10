# rdma-fork-dontfork

Hardware-free reproducer for the post *howtf did four bytes in the network
path crash checkpointing?*

`dontfork.c` models what libibverbs does on the fork-safe path
(`ibv_fork_init()` / `RDMAV_FORK_SAFE=1`): every `ibv_reg_mr()` rounds the
registered byte range out to page boundaries and marks those pages
`MADV_DONTFORK` (rdma-core, `libibverbs/memory.c`). The demo performs that
exact `madvise` directly, so it needs no RNIC and runs on any Linux.

## Run

```sh
gcc -O2 -Wall -Wextra -o dontfork dontfork.c
./dontfork demo    # 4-byte scratch shares a page with needed state -> child SIGSEGVs
./dontfork fixed   # scratch owns a dedicated page-rounded allocation -> child fine
```

On macOS/Windows, run it in a container:

```sh
docker run --rm -v "$PWD":/code -w /code gcc:12 \
  sh -c 'gcc -O2 -Wall -Wextra -o dontfork dontfork.c && ./dontfork demo; ./dontfork fixed'
```

## What to look at

- `reg_mr: asked for 4 bytes ... marked DONTFORK ... 4096 bytes`: the
  byte-range API, the page-range side effect.
- `smaps: VmFlags: ... dc ...`: the kernel's own marker. `dc` is
  `VM_DONTCOPY`, "do not copy area on fork". This is the forensic signal to
  grep for in a real incident.
- `demo`: the child dies with SIGSEGV dereferencing a pointer that works in
  the parent, because the whole page was withheld from the child.
- `fixed`: identical registration, but the registered word owns its pages
  (page-aligned base AND page-rounded extent), so nothing the child needs is
  collateral.
