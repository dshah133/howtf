---
title: "howtf did a four-byte buffer crash the checkpoint worker?"
description: "A GPUDirect visibility fence registered four bytes of host memory. Forked children started segfaulting on pointers the parent could read fine. The page-granular fork rule hiding under ibv_reg_mr()."
date: 2026-08-09
tags: [rdma, gpudirect, linux, fork, memory]
draft: false
---

## 1. The crash was in the wrong process

The first crash was in checkpointing. That was the problem.

We had just changed a network transport: the receive side of a collective-communications library. The change registered four bytes of host memory and used them as scratch for a GPUDirect RDMA visibility fence. Focused tests passed. Collectives produced the right answers. Performance looked normal. The new object was so small it barely felt like an allocation.

Then production training jobs began dying in code that had nothing to do with networking. Sometimes a checkpoint worker crashed walking metadata. Sometimes a data-loading child faulted dereferencing an object that was demonstrably valid in the parent. Stack traces pointed into serialization, input processing, allocator internals. The main training process ran on fine, which made the failures look stochastic. Rebuilding the same source could make a crash disappear, or move it somewhere else.

The useful clue was not the faulting instruction. It was the process boundary. Every victim was a child created with `fork()`. The same pointer, the same virtual address: readable in the parent, a segfault in the child.

That's the howtf. Memory that survived in the parent and did not exist in the child, delivered by a four-byte change on the other side of the codebase. The feature was measured in bytes. Its side effect was measured in pages.

> **Scope note.** This reconstructs a production incident from first-hand experience, with products, hardware generations, and deployment details deliberately blurred. Every mechanism claim is checked against public source: NVIDIA's NCCL, rdma-core, and the Linux kernel, linked throughout. The public NCCL commit used below as the reference design also already contains the defense this post ends with. Hold that thought for §8.

## 2. Two kinds of done

The receive path used GPUDirect RDMA: the NIC (an RNIC, an RDMA-capable NIC) writes incoming data straight into GPU memory over PCIe, skipping the bounce through host RAM.

It is tempting to collapse every notion of "done" into one completion bit. On this path that is unsafe. NVIDIA's [GPUDirect documentation](https://docs.nvidia.com/cuda/gpudirect-rdma/#synchronization-and-memory-ordering) says it directly: even after a third-party device has issued its PCIe writes, a concurrently running GPU kernel can observe stale or partially written data. The DMA write has to be made visible to the scope that will consume it, which is why modern CUDA exposes [`cuFlushGPUDirectRDMAWrites()`](https://docs.nvidia.com/cuda/cuda-driver-api/group__CUDA__DEVICE.html) as an explicit operation. Transport completion and GPU visibility are two different claims.

The historical NCCL InfiniBand transport, the public skeleton of this design (and a returning character on this site: [it last lost its device list to the linker](/blog/split-state-linking/)), answers both. For transport completion: the receiver posts a receive work request that is really a notification credit, then advertises the GPU destination address, rkey, and size to the sender [through a FIFO](https://github.com/NVIDIA/nccl/blob/c38f174bd436031dbc79dce19ff969f377976a8a/src/transport/net_ib.cc#L663-L671). The sender posts [`IBV_WR_RDMA_WRITE_WITH_IMM`](https://github.com/NVIDIA/nccl/blob/c38f174bd436031dbc79dce19ff969f377976a8a/src/transport/net_ib.cc#L639-L642): the write lands the payload one-sided in GPU memory, and the immediate consumes the receiver's credit, generating a completion whose `imm_data` [carries the message size](https://github.com/NVIDIA/nccl/blob/c38f174bd436031dbc79dce19ff969f377976a8a/src/transport/net_ib.cc#L783-L784).

For GPU visibility: after that completion, the receiver posts a signaled `IBV_WR_RDMA_READ` on [a small QP connected back to itself](https://github.com/NVIDIA/nccl/blob/c38f174bd436031dbc79dce19ff969f377976a8a/src/transport/net_ib.cc#L496-L506). The read's remote side is the GPU receive buffer. Its local destination is a tiny registered host object. The value read does not matter. An RDMA read is a non-posted operation that cannot complete until its response comes back, and on this platform that response was the ordering point: the plugin API [describes this callback](https://github.com/NVIDIA/nccl/blob/c38f174bd436031dbc79dce19ff969f377976a8a/src/include/nccl_net.h#L49-L51) as the flush that makes data received into CUDA memory visible to the GPU, and a later NCCL commit states the principle in one line: [a CPU read of GPU BAR memory drains prior PCIe posted writes](https://github.com/NVIDIA/nccl/commit/a12d73a8b692d62af7dbb475212e608630cb1752). (That is a platform-specific fence design, not a universal theorem about RDMA reads. The linked source shows its exact mechanics; don't generalize it past them.)

<figure class="frame diagram">
  <span class="frame-title">fig. 1 · two kinds of done on one receive</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 366" role="img" aria-label="Diagram: an RDMA write with immediate lands the payload in GPU memory and produces a receive completion, the transport's notion of done. A separate loopback RDMA read of one byte from the GPU buffer into a registered four-byte host word produces a second completion, the visibility point for dependent GPU work.">
      <defs>
        <marker id="f1a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="f1b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="30" y="40" width="130" height="40" fill="var(--seg)" opacity="0.14"/>
        <rect x="30" y="40" width="130" height="40" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="95" y="64" text-anchor="middle" fill="var(--seg)">sender RNIC</text>
        <rect x="270" y="40" width="140" height="40" fill="var(--seg)" opacity="0.14"/>
        <rect x="270" y="40" width="140" height="40" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="340" y="64" text-anchor="middle" fill="var(--seg)">receiver RNIC</text>
        <rect x="520" y="40" width="170" height="40" fill="var(--ldr)" opacity="0.14"/>
        <rect x="520" y="40" width="170" height="40" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="605" y="58" text-anchor="middle" fill="var(--ldr)">GPU memory</text>
        <text x="605" y="73" text-anchor="middle" font-size="10" fill="var(--muted)">receive buffer</text>
        <rect x="270" y="130" width="270" height="34" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="405" y="151" text-anchor="middle" fill="var(--muted)">done #1: recv CQE, size in imm_data</text>
        <rect x="30" y="216" width="200" height="40" fill="var(--accent)" opacity="0.14"/>
        <rect x="30" y="216" width="200" height="40" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="130" y="234" text-anchor="middle" fill="var(--accent)">4-byte host word</text>
        <text x="130" y="249" text-anchor="middle" font-size="10" fill="var(--accent)">registered with ibv_reg_mr</text>
        <rect x="270" y="282" width="330" height="34" fill="none" stroke="var(--accent)" stroke-width="1.4"/>
        <text x="435" y="303" text-anchor="middle" fill="var(--accent)">done #2: read CQE = writes visible to GPU</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.4" fill="none" marker-end="url(#f1a)">
        <path d="M 160 60 L 266 60"/>
        <path d="M 410 60 L 516 60"/>
        <path d="M 340 80 L 340 126"/>
      </g>
      <g stroke="var(--accent)" stroke-width="1.4" fill="none" marker-end="url(#f1b)">
        <path d="M 605 80 C 605 180, 400 236, 234 236"/>
        <path d="M 130 256 C 130 299, 180 299, 266 299"/>
      </g>
      <g font-family="var(--font-display)" font-size="10">
        <text x="213" y="32" text-anchor="middle" fill="var(--muted)">RDMA WRITE_WITH_IMM</text>
        <text x="463" y="32" text-anchor="middle" fill="var(--muted)">PCIe posted writes</text>
        <text x="470" y="236" text-anchor="middle" fill="var(--accent)">loopback RDMA READ, 1 byte</text>
      </g>
      <text x="360" y="350" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">done #1 answers "did it arrive". done #2 answers "can the GPU see it".</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>RNIC</span>
      <span><span class="k" style="background:var(--ldr)"></span>GPU memory</span>
      <span><span class="k" style="background:var(--accent)"></span>the four bytes, and the visibility point</span>
    </p>
  </div>
</figure>

The design carries an almost comic detail. The registered host object is an `int`, because the scratch word was declared as one. The read itself transfers a single byte:

```c title="NCCL net_ib.cc @ c38f174, abridged: the fence and its four bytes"
struct ncclIbGpuFlush {
  int enabled;
  int hostMem;              // the four-byte scratch word
  struct ibv_mr* hostMr;
  struct ibv_sge sge;
  struct ibv_qp* qp;        // loopback QP
};

// ncclIbAccept():
NCCLCHECK(wrap_ibv_reg_mr(&rComm->gpuFlush.hostMr, rComm->verbs.pd,
                          &rComm->gpuFlush.hostMem, sizeof(int),
                          IBV_ACCESS_LOCAL_WRITE));
rComm->gpuFlush.sge.addr   = (uint64_t)&rComm->gpuFlush.hostMem;
rComm->gpuFlush.sge.length = 1;        // one byte transferred

// ncclIbFlush():
wr.wr.rdma.remote_addr = (uint64_t)data;   // the GPU receive buffer
wr.opcode              = IBV_WR_RDMA_READ;
wr.send_flags          = IBV_SEND_SIGNALED;
```

`ibv_reg_mr()` takes a byte address and a byte length. There is no four-byte minimum, no page rounding in the interface. Four bytes is what was asked for.

That registration was the trigger.

## 3. What ibv_reg_mr does when fork safety is armed

At the verbs API boundary, the registration line means: let the RNIC use these four bytes as a local write destination. On a fork-safe libibverbs, it also means something the signature never hints at: change the fork-inheritance policy of the entire virtual-memory page containing them.

Fork safety was armed in this stack, and that is not exotic. At the commit above, NCCL's IB init [calls `ibv_fork_init()` unconditionally](https://github.com/NVIDIA/nccl/blob/c38f174bd436031dbc79dce19ff969f377976a8a/src/transport/net_ib.cc#L93-L94). For everything else, rdma-core [honors `RDMAV_FORK_SAFE` / `IBV_FORK_SAFE`](https://github.com/linux-rdma/rdma-core/blob/c1c5bf1f480312c07ed4d23f0feecf8b5fd73289/libibverbs/init.c#L664-L671) from the environment, and real fleets set it: AWS's EFA network plugin for NCCL exports `RDMAV_FORK_SAFE=1` on your behalf and logs that it did. Training stacks fork constantly (data loaders, checkpoint writers), so fork safety was the responsible setting. What it actually does is the surprise.

`ibv_fork_init()` sets up an interval tree of registered ranges. From then on, every ordinary (non-ODP) registration passes through [`ibv_dontfork_range()`](https://github.com/linux-rdma/rdma-core/blob/c1c5bf1f480312c07ed4d23f0feecf8b5fd73289/libibverbs/verbs.c) before the provider ever sees it, and that function rounds the byte interval out to page boundaries and applies `madvise(MADV_DONTFORK)`:

```c title="rdma-core libibverbs/memory.c, abridged: bytes in, pages out"
start = (uintptr_t) base & ~(range_page_size - 1);
end   = ((uintptr_t) (base + size + range_page_size - 1) &
         ~(range_page_size - 1)) - 1;
/* ... interval-tree bookkeeping, then, on the 0 -> 1 transition ... */
madvise(start, end - start + 1, MADV_DONTFORK);
```

Deregistration reference-counts the overlap and applies `MADV_DOFORK` when the last registration touching a range goes away. The accounting is careful. The granularity is the problem. On a 4 KiB-page machine:

```text
requested MR length:        4 bytes
fork-policy side effect: 4096 bytes
```

And `MADV_DONTFORK` is blunt. Per [madvise(2)](https://man7.org/linux/man-pages/man2/madvise.2.html): "Do not make the pages in this range available to the child after a fork(2)." Not copy-on-write, not zero-filled. Per [fork(2)](https://man7.org/linux/man-pages/man2/fork.2.html), the mapping is simply not inherited. The parent keeps the page. The child has a hole at that address, and the first dereference into the hole is a segfault.

So the full expansion of one innocent-looking line, on the fork-safe path, is:

> Register four bytes for the RNIC, and quietly withhold the surrounding page from every child this process will ever fork.

The scratch word did not live on a private page. It was a small field inside a heap object, and the rest of its page belonged to whatever else the allocator had placed there. When that happened to include state a checkpoint or data-loading child would later walk, the child inherited an address space with a hole where its data should have been. The checkpoint object was never corrupted. Its page was withheld.

<figure class="frame diagram">
  <span class="frame-title">fig. 2 · four bytes wide, one page deep</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 350" role="img" aria-label="Diagram: a four-byte registered word shares a 4 KiB page with neighbor state. MADV_DONTFORK applies to the whole page. After fork the parent keeps the page while the child has no mapping there, so touching the neighbor state in the child faults.">
      <defs>
        <marker id="f2a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="360" y="24" text-anchor="middle" fill="var(--krn)">madvise(MADV_DONTFORK) applies to the whole 4096-byte page</text>
        <path d="M 60 34 L 60 42 M 60 38 L 660 38 M 660 34 L 660 42" stroke="var(--krn)" fill="none" stroke-width="1.4"/>
        <rect x="60" y="52" width="600" height="52" fill="var(--sec)" opacity="0.10"/>
        <rect x="60" y="52" width="600" height="52" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <rect x="60" y="52" width="26" height="52" fill="var(--accent)" opacity="0.35"/>
        <rect x="60" y="52" width="26" height="52" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="100" y="83" fill="var(--sec)">neighbor state: whatever else the allocator put here</text>
        <path d="M 73 106 L 73 114" stroke="var(--accent)" stroke-width="1.2" fill="none"/>
        <text x="73" y="126" text-anchor="middle" fill="var(--accent)" font-size="10">4 B, registered</text>
        <rect x="60" y="210" width="270" height="94" fill="none" stroke="var(--border)" stroke-width="1.2"/>
        <text x="76" y="232" fill="var(--text)">parent</text>
        <rect x="76" y="246" width="238" height="30" fill="var(--sec)" opacity="0.10"/>
        <rect x="76" y="246" width="238" height="30" fill="none" stroke="var(--sec)" stroke-width="1.4"/>
        <text x="195" y="265" text-anchor="middle" fill="var(--sec)" font-size="10">page mapped · RNIC keeps its DMA view</text>
        <text x="76" y="296" fill="var(--ldr)" font-size="10">reads fine ✓</text>
        <rect x="390" y="210" width="270" height="94" fill="none" stroke="var(--border)" stroke-width="1.2"/>
        <text x="406" y="232" fill="var(--text)">child, after fork()</text>
        <rect x="406" y="246" width="238" height="30" fill="none" stroke="var(--muted)" stroke-width="1.4" stroke-dasharray="4 4"/>
        <text x="525" y="265" text-anchor="middle" fill="var(--muted)" font-size="10">no mapping at this address</text>
        <text x="406" y="296" fill="var(--accent)" font-size="10">dereference → SIGSEGV</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.3" fill="none" marker-end="url(#f2a)">
        <path d="M 195 104 L 195 206"/>
        <path d="M 525 104 L 525 206"/>
      </g>
      <text x="360" y="336" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">the API spoke in bytes. the fork policy spoke in pages.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--accent)"></span>registered word</span>
      <span><span class="k" style="background:var(--sec)"></span>collateral neighbor state</span>
      <span><span class="k" style="background:var(--krn)"></span>kernel policy</span>
    </p>
  </div>
</figure>

## 4. Why the page had to leave the child

Why would a library ever consider withholding your memory from your children a *safety* feature? Because for pinned DMA memory, the alternative was corruption. This section is the why; it is also the part of the mental model that survives past RDMA.

CPU copy-on-write works because the CPU asks permission. After `fork()`, parent and child share physical page `P`, and both mappings are write-protected. When either side writes, the CPU takes a page fault *before* the store lands, the kernel copies `P` to a fresh page `Q`, repoints the writer, and retries. The essential ingredient is the synchronous trap before the destructive write: at copy time, the old contents still exist.

Memory registration builds a second translation path that never asks. A classic (non-ODP) MR is designed to remove page faults from the fast path: at registration time the kernel [resolves and pins the pages long-term](https://github.com/torvalds/linux/blob/a7c7074b58d28c4206d666a12aa2e33447b3c581/drivers/infiniband/core/umem.c) (`pin_user_pages_fast()` with `FOLL_LONGTERM`, plus `FOLL_WRITE` for writable MRs), builds a scatter-gather list, and maps it into the device's DMA address space. From then on the RNIC resolves RDMA addresses through its own MR translation to those pinned pages. It does not walk the process page tables, it cannot see a copy-on-write bit, and its DMA write cannot take a CPU page fault. If the process forks and a COW-shared `P` is still the RNIC's target, the device writes `P` directly, and whichever process the kernel had decided should get "the old contents" gets the new bytes instead. Even a notification after the DMA would be too late: the fork-time snapshot is already destroyed.

<figure class="frame diagram">
  <span class="frame-title">fig. 3 · one page, two translation paths, one asks permission</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 320" role="img" aria-label="Diagram: a CPU store goes through the process page table, hits a write-protected copy-on-write entry, faults, and the kernel copies the page before the write. An RNIC DMA write goes through the MR and DMA translations built at registration time straight to the pinned physical page, with no fault possible.">
      <defs>
        <marker id="f3a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="f3b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--seg)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="60" y="36" fill="var(--krn)">CPU store, COW path</text>
        <rect x="60" y="48" width="240" height="32" fill="var(--krn)" opacity="0.10"/>
        <rect x="60" y="48" width="240" height="32" fill="none" stroke="var(--krn)" stroke-width="1.4"/>
        <text x="180" y="68" text-anchor="middle" fill="var(--krn)">VA → PTE: write-protected (COW)</text>
        <rect x="60" y="112" width="240" height="32" fill="var(--accent)" opacity="0.10"/>
        <rect x="60" y="112" width="240" height="32" fill="none" stroke="var(--accent)" stroke-width="1.4"/>
        <text x="180" y="132" text-anchor="middle" fill="var(--accent)">page fault, before the write</text>
        <rect x="60" y="176" width="240" height="32" fill="var(--krn)" opacity="0.10"/>
        <rect x="60" y="176" width="240" height="32" fill="none" stroke="var(--krn)" stroke-width="1.4"/>
        <text x="180" y="196" text-anchor="middle" fill="var(--krn)">kernel copies P → Q, retries</text>
        <text x="420" y="36" fill="var(--seg)">RNIC DMA, pinned-MR path</text>
        <rect x="420" y="48" width="240" height="32" fill="var(--seg)" opacity="0.10"/>
        <rect x="420" y="48" width="240" height="32" fill="none" stroke="var(--seg)" stroke-width="1.4"/>
        <text x="540" y="68" text-anchor="middle" fill="var(--seg)">MKey / MR translation (built at reg)</text>
        <rect x="420" y="112" width="240" height="32" fill="var(--seg)" opacity="0.10"/>
        <rect x="420" y="112" width="240" height="32" fill="none" stroke="var(--seg)" stroke-width="1.4"/>
        <text x="540" y="132" text-anchor="middle" fill="var(--seg)">DMA / IOVA mapping</text>
        <rect x="300" y="248" width="120" height="36" fill="var(--ldr)" opacity="0.14"/>
        <rect x="300" y="248" width="120" height="36" fill="none" stroke="var(--ldr)" stroke-width="1.6"/>
        <text x="360" y="270" text-anchor="middle" fill="var(--ldr)">page P (pinned)</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.3" fill="none" marker-end="url(#f3a)">
        <path d="M 180 80 L 180 108"/>
        <path d="M 180 144 L 180 172"/>
        <path d="M 180 208 C 180 236, 250 254, 296 262"/>
      </g>
      <g stroke="var(--seg)" stroke-width="1.6" fill="none" marker-end="url(#f3b)">
        <path d="M 540 80 L 540 108"/>
        <path d="M 540 144 C 540 210, 470 246, 424 260"/>
      </g>
      <text x="548" y="196" font-family="var(--font-display)" font-size="10" fill="var(--seg)">no PTE consulted, no fault</text>
      <text x="360" y="310" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--muted)">COW interposes on the left path only. the right path was built to skip it.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--krn)"></span>kernel / page tables</span>
      <span><span class="k" style="background:var(--seg)"></span>device translation</span>
      <span><span class="k" style="background:var(--ldr)"></span>physical page</span>
    </p>
  </div>
</figure>

This is an old problem, and `MADV_DONTFORK` is its old solution. It has been in Linux since 2.6.16, and madvise(2) still carries the original rationale: preventing copy-on-write from changing the physical location of a page, because "such page relocations cause problems for hardware that DMAs into the page." The historical fork-safety policy is two sentences: do not try to make DMA participate in COW. Prevent the child from sharing the DMA page at all.

That solved the corruption problem. It also created our crash, because the unit it operates on is the page, and the page contained more than the four bytes anyone asked about. (Faulting device translations exist too, under on-demand paging, IOMMU page faults, and shared virtual addressing. Different memory model. The classic pinned MR is the one this transport used, and its whole point is the pre-resolved fast path.)

## 5. Why it looked random

Once the page layout is fixed, the mechanism is fully deterministic. The page layout is not fixed.

- **Co-tenancy is a layout lottery.** A field's offset inside its struct is stable per build, but the allocation's base address modulo page size, and its page neighbors, depend on allocator history: size classes, arena state, allocation order, thread timing, feature flags, ASLR. A rebuild reshuffles the lottery, which is why the crash moved between builds.
- **The page keeps accepting tenants.** The registered page's free space stays in the allocator's inventory. Objects malloc'd *after* the registration can move in next to the scratch word, and they vanish from children too.
- **The child must touch the hole.** A networking test that never forks cannot see it. A child that forks and immediately execs never touches inherited heap. A checkpoint worker does exactly the dangerous thing: it keeps executing in the inherited address space and walks parent-built state.

So "one binary failed and another did not" was directionally right, and the precise condition was: this build, times this allocator history, times this page placement, times whether the child dereferences the collateral. Every factor except the last one is invisible in source code.

## 6. Debugging backward from the process boundary

The investigation got short once we stopped asking "which object is corrupt" and started asking "what happened to this virtual page."

```text title="the diagnosis chain"
SIGSEGV only in fork children
  -> same pointer valid in the parent          (kills use-after-free theories)
  -> diff /proc/<pid>/maps, parent vs child    (the page is absent, not remapped)
  -> round fault address down to page start
  -> grep /proc/<parent>/smaps: VmFlags has dc (VM_DONTCOPY: "do not copy on fork")
  -> inventory every ibv_reg_mr touching that page
```

The `dc` VmFlag is the kernel telling you, in its own handwriting, that someone madvised this range `MADV_DONTFORK`. From there the registration inventory is finite, and a four-byte MR sitting inside a shared heap page is hard to miss once you are actually looking for it.

One disambiguation worth writing down: a `DONTFORK` hole is *absent*, not zeroed. If a child observes zero-filled memory, that is a different mechanism (`MADV_WIPEONFORK` supplies zero pages on fork) or a tooling artifact from reading around the hole. The child of a `DONTFORK` page does not read zeros. It faults.

The closing experiment was the causal one: move the RDMA-owned scratch onto its own dedicated, page-rounded allocation, change nothing in checkpointing, and the crash disappears. Reintroduce the shared-page registration and it comes back. That experiment matters because it flips exactly the variable the hypothesis names, page co-tenancy, and nothing else.

## 7. Reproducing it, no RNIC required

The whole failure lives in one `madvise` call, so it reproduces in a hundred-odd lines of C on any Linux box, no RDMA hardware involved. The demo's `fake_reg_mr()` does literally what fork-safe libibverbs does: round the byte range to page boundaries, `madvise(MADV_DONTFORK)`. A 4-byte "registration" shares a page with state a forked child needs ([demo/rdma-fork-dontfork](https://github.com/dshah133/howtf/tree/main/demo/rdma-fork-dontfork)):

```shellsession title="./dontfork demo: the incident, in miniature"
page size: 4096 bytes
layout: flush_word at 0x29031000, neighbor_state at 0x29031004 (same page)
reg_mr: asked for 4 bytes at 0x29031000
reg_mr: marked DONTFORK [0x29031000, 0x29032000) = 4096 bytes
smaps:  VmFlags: rd wr mr mw me dc ac
parent: neighbor_state = "epoch=41 step=118000"
child:  dereferencing the same pointer...
parent: child killed by signal 11 (Segmentation fault)
parent: neighbor_state still "epoch=41 step=118000"
```

There is the whole story in nine lines: four bytes requested, 4096 marked, `dc` in VmFlags, the parent reading happily, the child dead on the same pointer. And the fix, same registration, dedicated page:

```shellsession title="./dontfork fixed: the invariant, in miniature"
reg_mr: asked for 4 bytes at 0x721a000
reg_mr: marked DONTFORK [0x721a000, 0x721b000) = 4096 bytes
parent: neighbor_state = "epoch=41 step=118000"
child:  dereferencing the same pointer...
parent: child exited 0
```

## 8. The fixes: own the page, or let the kernel copy early

**The application-level invariant.** Memory that may be registered must not share a page with state a forked child may need. That takes two properties, not one: a page-aligned base *and* a page-rounded dedicated extent. Alignment alone still lets the allocator move another tenant into the tail of the page.

This is not my invariant. It is in NCCL's tree, with the incident's lesson written out as a comment ([src/include/alloc.h](https://github.com/NVIDIA/nccl/blob/b221128ecacf4ce1b3054172b9f30163307042c5/src/include/alloc.h#L52-L64)):

```c title="NCCL alloc.h: the lesson, encoded as an allocator"
// Allocate memory to be potentially ibv_reg_mr'd. This needs to be
// allocated on separate pages as those pages will be marked DONTFORK
// and if they are shared, that could cause a crash in a child process
static ncclResult_t ncclIbMalloc(void** ptr, size_t size) {
  size_t page_size = sysconf(_SC_PAGESIZE);
  ...
  int ret = posix_memalign(&p, page_size, size_aligned);
```

And here the scope note's planted thought pays off: at the very commit this post has been quoting, the IB transport's connection structs are [already allocated through this helper](https://github.com/NVIDIA/nccl/blob/c38f174bd436031dbc79dce19ff969f377976a8a/src/transport/net_ib.cc#L57-L70). In fact they always were: the helper, comment and all, is present in the [first public commit that added the IB transport](https://github.com/NVIDIA/nccl/commit/f93fe9bfd94884cec2ba711897222e0df5569a53) (2.3.5-5, September 2018). No released public NCCL ever had the shared-page hazard. The comment is scar tissue from a lesson learned before open-sourcing, and the transport in this story re-derived that lesson independently. That is what makes this worth writing down as a class rather than a bug: any codebase that registers small heap objects on a fork-safe verbs stack re-derives it on schedule.

Two footnotes on the invariant. First, it degrades gracefully: on the old path the only memory a child loses is RDMA-owned state it should never touch anyway. Second, huge pages have their own trap: fork-safe rounding uses the base page size unless `RDMAV_HUGEPAGES_SAFE` is also set, which rdma-core's own docs call required if the application uses huge pages at all.

**The kernel fix.** Linux eventually implemented the direct thing: copy early. Since [Linux 5.9 for ordinary PTEs](https://github.com/torvalds/linux/commit/70e806e4e645019102d0e09d4933654fb5fb58ce) and [5.12 for hugetlb](https://github.com/torvalds/linux/commit/4eae4efa2c299f85b7ebfbeeda56c19c5eba2768) (both by Peter Xu), `fork()` detects pages that may be DMA-pinned and copies them for the child *at fork time*, before any future DMA can touch them. The parent and the RNIC keep the pinned page and the established translation. The child gets a correct snapshot. No fault in the DMA path was ever needed, only a copy moved earlier in time.

rdma-core exposes the boundary between the two worlds: [`ibv_is_fork_initialized()`](https://github.com/linux-rdma/rdma-core/blob/c1c5bf1f480312c07ed4d23f0feecf8b5fd73289/libibverbs/man/ibv_is_fork_initialized.3.md) returns `IBV_FORK_UNNEEDED` when the kernel reports copy-on-fork support (over RDMA netlink, not a version sniff), and on such kernels `ibv_fork_init()` short-circuits to a no-op. On a heterogeneous fleet, though, the oldest kernel in the pool sets your rules. The allocator invariant costs almost nothing and is correct in both worlds. Keep it.

<figure class="frame diagram">
  <span class="frame-title">fig. 4 · two eras of fork safety</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 282" role="img" aria-label="Diagram: on older kernels with fork-safe userspace, the registered page is withheld from the child entirely while the RNIC keeps the pinned page. On Linux 5.9 and later the kernel copies DMA-pinned pages at fork time, so the parent and RNIC keep the pinned page while the child receives a correct snapshot copy.">
      <g font-family="var(--font-mono)" font-size="11">
        <text x="40" y="36" fill="var(--text)">before: DONTFORK (userspace policy)</text>
        <rect x="40" y="52" width="140" height="34" fill="var(--ldr)" opacity="0.14"/>
        <rect x="40" y="52" width="140" height="34" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="110" y="73" text-anchor="middle" fill="var(--ldr)">parent: page P ✓</text>
        <rect x="200" y="52" width="110" height="34" fill="var(--seg)" opacity="0.14"/>
        <rect x="200" y="52" width="110" height="34" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="255" y="73" text-anchor="middle" fill="var(--seg)">RNIC → P</text>
        <rect x="40" y="120" width="140" height="34" fill="none" stroke="var(--muted)" stroke-width="1.4" stroke-dasharray="4 4"/>
        <text x="110" y="141" text-anchor="middle" fill="var(--muted)">child: hole</text>
        <text x="40" y="190" fill="var(--accent)" font-size="10">child faults on collateral state</text>
        <text x="400" y="36" fill="var(--text)">Linux 5.9+: early copy-on-fork (kernel)</text>
        <rect x="400" y="52" width="140" height="34" fill="var(--ldr)" opacity="0.14"/>
        <rect x="400" y="52" width="140" height="34" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="470" y="73" text-anchor="middle" fill="var(--ldr)">parent: page P ✓</text>
        <rect x="560" y="52" width="120" height="34" fill="var(--seg)" opacity="0.14"/>
        <rect x="560" y="52" width="120" height="34" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="620" y="73" text-anchor="middle" fill="var(--seg)">RNIC → P</text>
        <rect x="400" y="120" width="140" height="34" fill="var(--krn)" opacity="0.10"/>
        <rect x="400" y="120" width="140" height="34" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="470" y="141" text-anchor="middle" fill="var(--krn)">child: copy Q ✓</text>
        <text x="400" y="190" fill="var(--krn)" font-size="10">copied at fork(), before any future DMA</text>
        <text x="400" y="208" fill="var(--muted)" font-size="10">ibv_fork_init() becomes a no-op (IBV_FORK_UNNEEDED)</text>
      </g>
      <line x1="330" y1="30" x2="330" y2="220" stroke="var(--border)" stroke-dasharray="5 5"/>
      <text x="360" y="266" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">two eras, one invariant: a child never shares a DMA-pinned page</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--ldr)"></span>pinned page</span>
      <span><span class="k" style="background:var(--krn)"></span>fork-time copy</span>
      <span><span class="k" style="background:var(--seg)"></span>device translation</span>
    </p>
  </div>
</figure>

## 9. What generalizes

**Completion must name a scope.** "The operation completed" is not a systems statement. Completed at the sender, at the receiver's CQ, in host memory, or for a GPU consumer are different claims, and most ordering bugs start when two components use the same word for different ones.

**A byte-range API can carry page-range consequences.** The interface accepted four bytes. The enforcement unit was a page. The same shape hides under cache-line false sharing, huge-page mappings, IOMMU granules, and filesystem blocks: the unit you asked in is not necessarily the unit the system acts in.

**Process topology is part of the interface.** The transport passed its tests because the data movement was correct. Production added `fork()`, inherited heaps, checkpoint children. Registered memory couples to all of it, which makes `ibv_reg_mr()` a lifecycle contract with the whole process tree, not a permission slip for one NIC.

**Fix with invariants, not layouts.** "Move this integer until the crash stops" survives until the next rebuild. "Registered memory owns its pages and contains nothing a child needs" survives allocator changes, rebuilds, and time. The fix that lasts is the one you can state without mentioning an address.

## Epilogue

Every local decision in this incident was reasonable. The GPU needed a visibility fence. The fence needed a registered local buffer, and four bytes was honestly all it needed. Libibverbs needed to keep pinned DMA pages from tearing forked children, and page granularity was the only granularity `fork()` offers. The checkpoint worker expected inherited memory to be there. Every layer kept its contract.

The contracts did not compose, because four bytes and one page were treated as the same unit. That was the bug.

---

*The hardware-free reproducer (the `madvise` model, the crash, and the fixed layout) lives at [demo/rdma-fork-dontfork](https://github.com/dshah133/howtf/tree/main/demo/rdma-fork-dontfork). Mechanism sources are pinned commits linked inline: NCCL's historical IB transport, rdma-core's fork tracking, the kernel's umem pinning path, and Peter Xu's copy-on-fork series.*
