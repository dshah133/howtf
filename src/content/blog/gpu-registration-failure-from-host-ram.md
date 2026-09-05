---
title: "howtf did a GPU memory-registration error come from host RAM?"
description: "A production debugging story across NCCL, ConnectX, nvidia-peermem, DMA-BUF, retsnoop, and an inherited six-gigabyte CMA reserve. Part 2 of Memory Registration, All the Way Down."
date: 2026-08-23
updated: 2026-09-05
series:
  name: "Memory Registration, All the Way Down"
  part: 2
tags: [rdma, nccl, gpudirect, linux, memory]
draft: false
---

> **Memory Registration, All the Way Down, Part 2.** [Part 1](/blog/nic-writes-directly-into-gpu-memory/) builds the registration and PCIe data path. [Part 3](/blog/pinned-memory-still-needs-to-move/) opens the Linux pinning and migration mechanism behind the root cause.

The exact production logs are no longer available, but the failure was easy to describe and hard to catch:

```text
Call to ibv_reg_mr_iova2 failed with error Cannot allocate memory
```

A distributed training job would run on an H100 cluster, complete some number of steps, and then lose a network connection while the NVIDIA Collective Communications Library (NCCL) registered memory. The same job on the same machine might succeed on retry. The hosts were not out of RAM. The GPU still had memory. The ConnectX-7 devices were present and healthy. Kernel and driver matrices did not produce a clean boundary.

At fleet scale, “rare” was not small. Meta’s broader H100 deployment was measured in hundreds of thousands of accelerators; the directly affected jobs were spread across multiple Grand Teton clusters rather than every GPU failing at once. At bad points, this signature appeared hundreds of times in a week. A failed training job could throw away hours of expensive accelerator work.

The obvious place to look was GPU memory. The page that explained the failure was a CPU page inside a Linux **Contiguous Memory Allocator (CMA)** region.

> **Scope note.** This reconstructs an incident I worked on at Meta. The symptom existed in some form before I joined the final investigation; I became directly involved in early 2024, and the closing investigation took roughly four months. Exact production logs, the private NVIDIA patch, and the precise internal kernel build are no longer available. Observed facts are stated as observations. Kernel call stacks labelled **reconstructed** are built from public NCCL 2.17-generation source, Linux 6.x, rdma-core, and NVIDIA R525/R535/R555 source; those R-numbers name NVIDIA Linux driver branches. The mechanisms are source-checked; the placeholder addresses are not historical evidence.

---

## 1. The first model was entirely reasonable

Part 1 built the expected GPUDirect registration path:

```text
CUDA buffer
    -> ibv_reg_mr_iova2()
    -> RDMA peer-memory lookup
    -> nvidia-peermem
    -> nvidia_p2p_get_pages()
    -> map GPU pages for ConnectX
    -> program an MKey
```

If that call returns `ENOMEM`, several GPU-side explanations are plausible:

```text
BAR1 mapping space exhausted
GPU peer mapping failed
NVIDIA registration cache inconsistent
MKey or HCA resource exhausted
IOMMU / peer topology unsupported
GPU allocation being torn down concurrently
```

One explanation deserved explicit elimination: the process's locked-memory limit, `RLIMIT_MEMLOCK`. Public NCCL incidents with this exact surface line are often fixed by raising `ulimit -l`. That was not this failure. RDMA checks the locked-memory budget before it pins userspace pages. Once the investigation had identified the backing page as `MIGRATE_CMA`, this failure had necessarily progressed below that accounting gate and into page pinning and migration. The final one-variable rollout changed `hugetlb_cma` and no memlock policy. A static locked-memory limit cannot explain why removing only the CMA reserve made this signature disappear.

BAR1 was especially attractive. NVIDIA’s forums and GPUDirect documentation contain real cases where a small or misconfigured BAR1 aperture prevents a peer device from mapping enough GPU memory. The error surface was consistent with that class, and the R525 fleet used the proprietary NVIDIA kernel module, which made the NVIDIA side difficult to inspect.

So the investigation began where the architecture pointed: GPU virtual addresses, peer memory, BAR mappings, and the NVIDIA Linux GPU kernel driver.

That was not wasted work. One of the bugs was there.

---

## 2. The first clue was checkpointing

The failures seemed to cluster around checkpoint activity.

The checkpoint path used forked workers. Those workers continued executing in an inherited address space while the main training process kept running. Checkpointing also created host-memory pressure, page-cache activity, and a large change in allocator history. Depending on the workload, it could overlap communicator setup or first use of a network path.

One mitigation moved the relevant memory registration earlier, before the checkpoint phase. The failure rate appeared to drop.

That result was persuasive for two reasons:

1. It changed only the temporal overlap with checkpointing.
2. Memory-registration failures are sensitive to lifetime and pressure.

The working theory became:

```text
checkpointing
    -> host-memory pressure or process-lifecycle activity
    -> peer-memory registration becomes unreliable
```

For a while, the workaround was good enough to let training proceed.

The mitigation was operationally valuable because it returned capacity while the investigation continued. It also made the checkpoint correlation easy to over-weight.

---

## 3. There was also a real NVIDIA teardown race

The R525-era path depended on `nvidia-peermem` and the NVIDIA P2P page APIs. Those APIs needed a teardown protocol. If a CUDA allocation disappeared while the RNIC still held a mapping, the peer driver had to invalidate or release its state in the right order.

NVIDIA identified a race and supplied a patched driver. The private patch itself is no longer available, so I cannot claim its exact data structure or callback sequence. The closest public match is documented in NVIDIA’s GPUDirect RDMA guide for the R515-through-early-R535 family:

```text
GPU driver invokes the invalidation callback
                ||
I/O driver calls nvidia_p2p_put_pages()
                ||
             race
```

NVIDIA introduced persistent `get_pages` / `put_pages` APIs to avoid that callback race, updated `nvidia-peermem`, shipped the change in R535.14+, and backported it to R525.105.17+.

Installing the vendor fix reduced the failures, but jobs were still failing with similar NCCL errors. We had fixed one defect and still had another to find.

At the NCCL layer, both looked like this:

```text
memory registration failed
```

The error had collapsed two different mechanisms into one sentence.

---

## 4. Moving to R535 and DMA-BUF removed one whole path

The next large experiment was architectural rather than local.

The fleet moved from the R525 proprietary kernel-module setup toward an R535 open-kernel-module setup and enabled NCCL’s DMA-BUF registration for supported CUDA buffers.

R535 alone was not the switch. In this generation, DMA-BUF GPUDirect required the open kernel-module flavor, CUDA 11.7 or newer, Linux 5.12 or newer, and compatible RDMA provider support. NCCL also probed the network plugin, CUDA driver/device capability, and the chosen network device's pointer support at runtime. Telemetry showed that eligible GPU buffers were actually taking the DMA-BUF branch; unsupported or host buffers still used their ordinary registration paths.

Before:

```text
CUDA buffer
  -> ibv_reg_mr_iova2()
  -> nvidia-peermem
  -> NVIDIA P2P page APIs
  -> mlx5 MKey
```

After:

```text
CUDA buffer
  -> export DMA-BUF fd
  -> ibv_reg_dmabuf_mr()
  -> mlx5 attaches to NVIDIA exporter
  -> exporter supplies DMA mapping
  -> mlx5 MKey
```

The change removed the legacy peer-memory ownership protocol, its private callback interface, and much of the stale-mapping surface from GPU-buffer registration.

The failure rate dropped again. For a short period, the remaining failures looked like rollout noise or unrelated resource problems. Then a new workload started failing with the same family of messages despite having no checkpoint overlap. Checkpoint overlap could no longer explain every failure.

<figure class="frame diagram">
  <span class="frame-title">fig. 1 · four months of plausible fixes</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 300" role="img" aria-label="Diagram: a timeline of the investigation with a stepped failure-rate line. The rate starts high on the R525 fleet with intermittent ENOMEM registration failures. It drops after the mitigation of registering before checkpoints, drops again after NVIDIA's P2P teardown fix, and drops further after the R535 and DMA-BUF rollout, but never reaches zero. A new workload then fails without any checkpoint overlap. Only setting hugetlb_cma to zero takes the rate to zero, where it stays for weeks and then months.">
      <g font-family="var(--font-mono)" font-size="10">
        <line x1="40" y1="228" x2="700" y2="228" stroke="var(--border)" stroke-width="1.2"/>
        <path d="M 40 70 L 175 70 L 175 100 L 285 100 L 285 122 L 395 122 L 395 160 L 505 160 L 505 150 L 615 150 L 615 226 L 700 226" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <g stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3">
          <line x1="175" y1="70" x2="175" y2="228"/>
          <line x1="285" y1="100" x2="285" y2="228"/>
          <line x1="395" y1="122" x2="395" y2="228"/>
          <line x1="505" y1="160" x2="505" y2="228"/>
          <line x1="615" y1="150" x2="615" y2="228"/>
        </g>
        <g fill="var(--muted)">
          <text x="44" y="52">hundreds of failures</text>
          <text x="44" y="64">in bad weeks · R525</text>
          <text x="175" y="244" text-anchor="middle">register before</text>
          <text x="175" y="256" text-anchor="middle">checkpoints</text>
          <text x="285" y="244" text-anchor="middle">NVIDIA P2P</text>
          <text x="285" y="256" text-anchor="middle">teardown fix</text>
          <text x="395" y="244" text-anchor="middle">R535 +</text>
          <text x="395" y="256" text-anchor="middle">DMA-BUF</text>
          <text x="505" y="244" text-anchor="middle">recurrence, no</text>
          <text x="505" y="256" text-anchor="middle">checkpoint overlap</text>
          <text x="615" y="244" text-anchor="middle" fill="var(--accent)">hugetlb_cma=0</text>
        </g>
        <text x="512" y="140" fill="var(--muted)">retsnoop probes go out</text>
        <text x="698" y="214" text-anchor="end" fill="var(--accent)">zero, for weeks, then months</text>
      </g>
      <text x="360" y="288" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">every drop was a real fix for a real bug. only the last one was this bug.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--accent)"></span>rate of "Call to ibv_reg_mr_iova2 failed" (not to scale)</span>
    </p>
  </div>
</figure>

---

## 5. The fleet could not become a laboratory

Reserving a cluster and running the job until it failed would have tied up too much training capacity.

A rare failure on one H100 node is already expensive to reproduce. A distributed failure may require many nodes, the right allocator history, the right process timing, and enough runtime for a lazy connection or secondary communicator to initialize. Parking that capacity indefinitely removes it from training. Rebooting into instrumented kernels or repeatedly changing drivers adds another operational cost.

The affected clusters needed to keep doing useful work. A retry was cheaper than reserving a large slice of the fleet for an open-ended experiment—even though, across the fleet, the retries were expensive.

We needed to collect the relevant evidence when a registration failed in production:

```text
Do not trace every successful registration.
Do not dump every kernel call.
Capture the deepest failing path, the pointer, and the return code
only when the rare error occurs.
```

That is where retsnoop became useful.

---

## 6. Retsnoop: record the error path, not the whole machine

[Retsnoop](https://github.com/anakryiko/retsnoop) is a BPF kernel tracing tool that let us follow rare error returns without recording every call on the machine.

It can attach to a selected entry function, trace an allowed set of callees, and emit only calls that satisfy an error filter. It can also capture arguments and, on supported CPUs, use Last Branch Records to look inside a function whose return value alone is too generic.

```text
ordinary tracing:
    millions of calls -> enormous trace -> find one failure later

failure-triggered retsnoop:
    watch selected call tree -> emit only the failing invocation
```

We deployed progressively narrower probes around RDMA MR creation and the NVIDIA host-memory import path. Each failure told us which layer to instrument next.

The original trace is gone, but the investigation ladder was approximately:

```text
ibv registration returned ENOMEM
    |
    v
mlx5 user-MR creation failed
    |
    v
memory could not be made stable for the requested registration
    |
    v
inspect the actual userspace address and its backing page
```

The critical step was to stop treating the address as “a GPU pointer because NCCL is using GDR.”

---

## 7. The pointer was in host memory

As reconstructed from the surviving classification, the failing address belonged to the process's host address space. `/proc/<pid>/maps` was one of the checks used while following it, together with the CUDA/NVIDIA import path. The retained conclusion was that this was CPU-backed memory made accessible to CUDA, not an ordinary `cudaMalloc()` framebuffer allocation.

I no longer have the exact mapping label, whether anonymous, shmem, or memfd-like. The surviving evidence did establish where the memory lived:

```text
not GPU framebuffer
not a BAR1 virtual mapping
CPU-backed host memory
```

That left a question about NCCL itself: why was a GPUDirect connection registering CPU memory?

---

## 8. The host buffer hidden inside NCCL

The public NCCL 2.17.1 source makes the split visible.

NCCL supports multiple protocols, including LL (“low latency”), LL128, and SIMPLE. On a dedicated **send** connection with GPUDirect enabled, the public code places most protocol buffers in device memory but deliberately leaves `NCCL_PROTO_LL` in host memory.

Abridged to the decision rather than the exact source syntax:

```cpp
for each protocol p:
    use_device_memory = use_gdr && (p != NCCL_PROTO_LL)
    allocate buffer in selected memory bank
```

In the same-process proxy configuration used by these jobs, NCCL allocates its host-memory bank through CUDA mapped host memory. Public NCCL 2.17 uses `cudaHostAlloc(..., cudaHostAllocMapped)` in that path. Other shared-memory paths use `cudaHostRegister()` over a mapped host range.

NCCL builds a small number of backing allocations, or banks, and places several subranges inside them. On this send path, the host bank contains the LL protocol buffer and the `ncclSendMem` / `ncclRecvMem` control structures. That is the CPU control plane inside the connection.

The registration loop does **not**, however, register the entire bank once. It walks the active protocols and passes each protocol buffer's pointer and length to the network plugin:

<figure class="frame diagram">
  <span class="frame-title">fig. 2 · the CPU control plane inside a GPUDirect connection</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 350" role="img" aria-label="Diagram: an NCCL send connection with GPUDirect enabled keeps two backing banks. The device bank in GPU memory holds the SIMPLE and LL128 protocol subranges; after the R535 rollout each registers with the RNIC through ibv_reg_dmabuf_mr. The host bank in CPU memory holds the LL protocol subrange, which registers through the ordinary ibv_reg_mr_iova2 path, plus the ncclSendMem and ncclRecvMem control structures, which share the bank but are not the object of the registration loop.">
      <defs>
        <marker id="p2f2a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ldr)"/>
        </marker>
        <marker id="p2f2b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="30" y="30" width="280" height="130" fill="var(--ldr)" opacity="0.07"/>
        <rect x="30" y="30" width="280" height="130" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="44" y="50" fill="var(--ldr)">device bank · GPU memory</text>
        <rect x="50" y="62" width="240" height="30" fill="var(--ldr)" opacity="0.16"/>
        <rect x="50" y="62" width="240" height="30" fill="none" stroke="var(--ldr)" stroke-width="1.2"/>
        <text x="170" y="81" text-anchor="middle" fill="var(--text)">SIMPLE subrange</text>
        <rect x="50" y="100" width="240" height="30" fill="var(--ldr)" opacity="0.16"/>
        <rect x="50" y="100" width="240" height="30" fill="none" stroke="var(--ldr)" stroke-width="1.2"/>
        <text x="170" y="119" text-anchor="middle" fill="var(--text)">LL128 subrange</text>
        <rect x="30" y="190" width="280" height="130" fill="var(--sec)" opacity="0.06"/>
        <rect x="30" y="190" width="280" height="130" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="44" y="210" fill="var(--sec)">host bank · CPU memory</text>
        <rect x="50" y="222" width="240" height="30" fill="var(--accent)" opacity="0.14"/>
        <rect x="50" y="222" width="240" height="30" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="170" y="241" text-anchor="middle" fill="var(--accent)">LL protocol subrange</text>
        <rect x="50" y="260" width="240" height="44" fill="none" stroke="var(--muted)" stroke-width="1" stroke-dasharray="4 3"/>
        <text x="170" y="278" text-anchor="middle" fill="var(--muted)">ncclSendMem / ncclRecvMem</text>
        <text x="170" y="294" text-anchor="middle" font-size="10" fill="var(--muted)">share the bank, not this loop's target</text>
        <rect x="440" y="62" width="250" height="52" fill="none" stroke="var(--ldr)" stroke-width="1.4"/>
        <text x="565" y="84" text-anchor="middle" fill="var(--ldr)">ibv_reg_dmabuf_mr()</text>
        <text x="565" y="101" text-anchor="middle" font-size="10" fill="var(--muted)">DMA-BUF path, after R535 rollout</text>
        <rect x="440" y="222" width="250" height="52" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="565" y="244" text-anchor="middle" fill="var(--accent)">ibv_reg_mr_iova2()</text>
        <text x="565" y="261" text-anchor="middle" font-size="10" fill="var(--muted)">ordinary host MR, FOLL_LONGTERM</text>
      </g>
      <g stroke="var(--ldr)" stroke-width="1.4" fill="none" marker-end="url(#p2f2a)">
        <path d="M 290 77 L 436 82"/>
        <path d="M 290 115 L 436 96"/>
      </g>
      <g stroke="var(--accent)" stroke-width="1.6" fill="none" marker-end="url(#p2f2b)">
        <path d="M 290 237 L 436 245"/>
      </g>
      <text x="565" y="48" text-anchor="middle" font-family="var(--font-display)" font-size="10" fill="var(--muted)">one registration per active protocol subrange</text>
      <text x="360" y="340" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">GPUDirect never meant every buffer lives in VRAM. the LL buffer stayed on the CPU.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--ldr)"></span>GPU memory · DMA-BUF registration</span>
      <span><span class="k" style="background:var(--sec)"></span>host memory</span>
      <span><span class="k" style="background:var(--accent)"></span>the host subrange that failed</span>
    </p>
  </div>
</figure>

The control structures share the host backing allocation, but they are not the intentional object of this registration loop. Verbs rounds a requested subrange to page boundaries while pinning it, so adjacent padding can share a pinned page; that is different from NCCL registering the whole host bank as one MR.

For a CUDA buffer, DMA-BUF may be selected.

For a host buffer, stock NCCL falls through to the ordinary MR path. With relaxed ordering enabled in the IB plugin, that path calls `ibv_reg_mr_iova2()`, exactly as fig. 2 shows.

This reconciles an otherwise confusing observation: moving GPU buffers to DMA-BUF removed `nvidia-peermem` from the GPU path, but it did not remove ordinary host-memory registration from the connection.

I no longer have the allocation record. The public NCCL source points to `NCCL_PROTO_LL`, but I can’t rule out another CUDA-mapped NCCL host buffer. The mechanism is the same either way.

The important topology is:

<figure class="frame diagram">
  <span class="frame-title">fig. 3 · one page, three opinions about its lifetime</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 280" role="img" aria-label="Diagram: one host page sits in the center. Three subsystems hold claims on it: the CPU can access it through the process page table, the GPU can access it because CUDA mapped and pinned it, and the RNIC must access it because NCCL registered it for RDMA. Each arrow carries a different lifetime contract.">
      <defs>
        <marker id="p2f3a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--krn)"/>
        </marker>
        <marker id="p2f3b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ldr)"/>
        </marker>
        <marker id="p2f3c" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--seg)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="290" y="112" width="140" height="52" fill="var(--accent)" opacity="0.14"/>
        <rect x="290" y="112" width="140" height="52" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="360" y="134" text-anchor="middle" fill="var(--accent)">one host page</text>
        <text x="360" y="151" text-anchor="middle" font-size="10" fill="var(--muted)">4 KiB of CPU RAM</text>
        <rect x="40" y="40" width="170" height="40" fill="var(--krn)" opacity="0.14"/>
        <rect x="40" y="40" width="170" height="40" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="125" y="64" text-anchor="middle" fill="var(--krn)">CPU</text>
        <rect x="510" y="40" width="170" height="40" fill="var(--ldr)" opacity="0.14"/>
        <rect x="510" y="40" width="170" height="40" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="595" y="64" text-anchor="middle" fill="var(--ldr)">GPU</text>
        <rect x="510" y="196" width="170" height="40" fill="var(--seg)" opacity="0.14"/>
        <rect x="510" y="196" width="170" height="40" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="595" y="220" text-anchor="middle" fill="var(--seg)">RNIC</text>
      </g>
      <g stroke-width="1.4" fill="none">
        <path d="M 210 66 C 260 80, 270 100, 288 116" stroke="var(--krn)" marker-end="url(#p2f3a)"/>
        <path d="M 510 66 C 470 80, 456 100, 434 116" stroke="var(--ldr)" marker-end="url(#p2f3b)"/>
        <path d="M 510 212 C 480 200, 460 180, 434 160" stroke="var(--seg)" marker-end="url(#p2f3c)"/>
      </g>
      <g font-family="var(--font-display)" font-size="10">
        <text x="150" y="106" fill="var(--krn)">can access it</text>
        <text x="150" y="119" fill="var(--muted)">process page table</text>
        <text x="452" y="90" fill="var(--ldr)">can access it</text>
        <text x="452" y="103" fill="var(--muted)">CUDA mapped + pinned it</text>
        <text x="498" y="180" text-anchor="end" fill="var(--seg)">must access it</text>
        <text x="498" y="193" text-anchor="end" fill="var(--muted)">NCCL registered it for RDMA</text>
      </g>
      <text x="360" y="266" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">each arrow is a different contract, and none of the three knew the other two existed.</text>
    </svg>
  </div>
</figure>

---

## 9. Following the host page into the NVIDIA driver

One way CUDA makes pre-existing host memory GPU-accessible is to import and lock the userspace pages. The production evidence showed that an NVIDIA host-memory pin was involved, but it did not preserve the exact userspace allocation API.

The exact production R525 module was proprietary. The exact CUDA entry point is also no longer recoverable: it may have been a mapped-host allocation, registration of pre-existing file-backed memory, or another NCCL host-memory path. Public open-module source therefore gives us the closest inspectable version of the lifetime contract, not proof of the exact production call stack.

### Why a CUDA host allocation can still be a CMA page

There are two different NVIDIA system-memory paths, and confusing them breaks the diagnosis.

The NVIDIA kernel driver can allocate its **own** pages. In public R535 source, that allocator starts with `GFP_KERNEL` and never adds `__GFP_MOVABLE`. Such driver-owned pages are unmovable and cannot be supplied from a `MIGRATE_CMA` pageblock.

The driver can also **import userspace memory**. In the public RM path, userspace supplies a virtual address through an `NV01_MEMORY_SYSTEM_OS_DESCRIPTOR`; `RmCreateOsDescriptor()` passes that range to `os_lock_user_pages()`. Anonymous userspace pages are faulted with `GFP_HIGHUSER_MOVABLE`, which includes `__GFP_MOVABLE`, so they are eligible to occupy CMA before they are pinned.

NVIDIA's proprietary CUDA userspace implementation is not public. An independent clean-room NVIDIA backend built from traced RM ioctls corroborates one host-allocation sequence—anonymous `mmap()`, followed by the OS-descriptor import—but that is corroborating evidence, not an NVIDIA API contract. The stronger production evidence is the page itself: once the traced backing page was classified as `MIGRATE_CMA`, it could not have come from the driver's `GFP_KERNEL` allocator. Its placement identifies it as movable userspace backing imported and pinned by the NVIDIA path.

For the source-visible path that imports and locks existing userspace host memory, public R525.105.17 and R535.104.05 implement `os_lock_user_pages()` with flags like this:

```text
FOLL_WRITE, when writable
```

and calls the kernel’s page-pinning API. It does **not** add `FOLL_LONGTERM`.

In public R555.42.02, the same function adds:

```text
FOLL_LONGTERM on x86
```

That source difference is one of the most useful pieces of corroboration in the whole incident. It says that, in this R525/R535-era public host-registration path, NVIDIA could pin pages without declaring the long-term DMA lifetime that Linux uses to enforce special placement rules. It should not be read as proof that the proprietary production binary reached this exact wrapper.

CUDA had successfully pinned the page, but the later RNIC registration failed. The page’s location explains why.

---

## 10. The page belonged to `MIGRATE_CMA`

Linux groups physical memory into pageblocks and assigns each pageblock a **migratetype**. The failing host page belonged to a pageblock marked:

```text
MIGRATE_CMA
```

This is more precise than saying “the page had a migratable bit.” `MIGRATE_CMA` is a pageblock policy. Pages allocated from that block are expected to remain movable so the entire physical range can be reclaimed for a future contiguous allocation.

The reconstructed diagnosis looked like this:

```text
RECONSTRUCTED DIAGNOSIS LADDER
Not original production output.

failed userspace address: 0x7f2c...
    |
    +-- /proc/<pid>/maps: host-backed VMA
    |
    +-- resolve backing page / PFN in kernel trace
    |
    +-- get_pageblock_migratetype(page) == MIGRATE_CMA
    |
    +-- PFN lies inside boot-reserved HugeTLB CMA area
```


> Why would NCCL host memory come from CMA on an H100 training node?

Because the GPU node had inherited a memory policy designed for a very different fleet.

---

## 11. The inherited six-gigabyte reserve

The relevant boot configuration was approximately:

```text
hugetlb_cma=6G
```

`hugetlb_cma` reserves CMA memory for dynamically allocating **gigantic HugeTLB pages**. On x86-64, the gigantic size is normally 1 GiB. A six-gigabyte request is therefore enough aggregate capacity to construct up to six such pages later without requiring six specific pages to be allocated at boot.

On a NUMA system, `hugetlb_cma=6G` is not necessarily one six-gigabyte physical interval. Unless node-specific sizes are supplied, Linux apportions the aggregate request across online nodes. On a two-node host, that normally means up to roughly three GiB per node, subject to gigantic-page alignment and successful reservation. Per-node allocator state therefore affects whether a particular NCCL page lands in CMA.

Until a HugeTLB user asks for that contiguous memory, Linux can lend each per-node reserve to ordinary movable pages.

<figure class="frame diagram">
  <span class="frame-title">fig. 4 · a six-gigabyte promise, split across two nodes</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 330" role="img" aria-label="Diagram: the aggregate boot request hugetlb_cma equals six gigabytes is apportioned across two NUMA nodes as roughly three gigabytes of MIGRATE_CMA extents each. While no gigantic page is requested, ordinary movable pages temporarily occupy the reserve. When a one-gigabyte HugeTLB request later arrives on one node, the kernel migrates the temporary occupants out and returns one contiguous physical extent.">
      <defs>
        <marker id="p2f4a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--krn)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="360" y="30" text-anchor="middle" fill="var(--muted)">aggregate boot request: hugetlb_cma=6G</text>
        <rect x="40" y="48" width="300" height="92" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="54" y="68" fill="var(--muted)">NUMA node 0</text>
        <rect x="56" y="78" width="268" height="48" fill="var(--krn)" opacity="0.10"/>
        <rect x="56" y="78" width="268" height="48" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="190" y="96" text-anchor="middle" fill="var(--krn)">~3 GiB MIGRATE_CMA extents</text>
        <g opacity="0.85">
          <rect x="66" y="106" width="34" height="14" fill="var(--sec)" opacity="0.35"/>
          <rect x="106" y="106" width="52" height="14" fill="var(--sec)" opacity="0.35"/>
          <rect x="176" y="106" width="26" height="14" fill="var(--sec)" opacity="0.35"/>
          <rect x="226" y="106" width="60" height="14" fill="var(--sec)" opacity="0.35"/>
        </g>
        <rect x="380" y="48" width="300" height="92" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="394" y="68" fill="var(--muted)">NUMA node 1</text>
        <rect x="396" y="78" width="268" height="48" fill="var(--krn)" opacity="0.10"/>
        <rect x="396" y="78" width="268" height="48" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="530" y="96" text-anchor="middle" fill="var(--krn)">~3 GiB MIGRATE_CMA extents</text>
        <g opacity="0.85">
          <rect x="406" y="106" width="44" height="14" fill="var(--sec)" opacity="0.35"/>
          <rect x="470" y="106" width="30" height="14" fill="var(--sec)" opacity="0.35"/>
          <rect x="520" y="106" width="58" height="14" fill="var(--sec)" opacity="0.35"/>
        </g>
        <text x="360" y="162" text-anchor="middle" font-size="10" fill="var(--muted)">temporary movable occupants, lent out while no gigantic page is needed</text>
        <rect x="120" y="206" width="480" height="58" fill="var(--krn)" opacity="0.10"/>
        <rect x="120" y="206" width="480" height="58" fill="none" stroke="var(--krn)" stroke-width="1.8"/>
        <text x="360" y="230" text-anchor="middle" fill="var(--krn)">later: one 1 GiB HugeTLB request on one node</text>
        <text x="360" y="249" text-anchor="middle" font-size="10" fill="var(--muted)">migrate occupants elsewhere · return one contiguous extent</text>
      </g>
      <g stroke="var(--krn)" stroke-width="1.4" fill="none" marker-end="url(#p2f4a)">
        <path d="M 190 140 C 190 175, 240 195, 268 204"/>
        <path d="M 530 140 C 530 175, 480 195, 452 204"/>
      </g>
      <text x="360" y="298" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">the reserve works only while "temporary" stays true. every occupant must remain movable.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--krn)"></span>MIGRATE_CMA reserve</span>
      <span><span class="k" style="background:var(--sec)"></span>ordinary movable occupants</span>
    </p>
  </div>
</figure>

The reserve existed for web-serving machines—systems where dynamic gigantic pages and memory efficiency justified the policy. GPU training nodes shared enough of the kernel and boot configuration machinery to inherit it, even though they did not meaningfully need the reserve.

That alone would make CMA placement possible. A second policy made it common.

---

## 12. The allocator intentionally consumed CMA first

The CMA-first policy made the placement frequent, but it was not required for the underlying bug. Stock Linux v6.6 already lets eligible movable allocations fall back to CMA when CMA pages make up more than half of a zone's free pages. An unpatched kernel can therefore reproduce the mechanism under sufficient ordinary-memory pressure.

A public Linux patch from July 2023 matches the remembered fleet policy closely: **“mm: page_alloc: consume available CMA space first.”** It is not proof of the exact internal patch deployed at Meta, but its policy and stated motivation are unusually close.

Its motivation describes machines reserving CMA for potential HugeTLB users, ordinary movable allocations being allowed to use the space, and premature OOMs occurring because non-CMA memory filled while CMA remained free. The proposed fix was to allocate movable pages from CMA first because those pages could later be migrated out.

The policy is locally sensible:

```text
Movable allocation options:

1. CMA memory
   can later be evacuated for a contiguous request

2. ordinary memory
   needed by allocations that cannot use CMA

Prefer CMA first to preserve flexible ordinary memory.
```

On the web fleet, that improved memory utilization.

On the GPU fleet, it increased the probability that a CUDA-mapped NCCL host buffer would be backed by a CMA page.

The interaction was now fully assembled:

```text
hugetlb_cma=6G
    |
    v
movable allocations prefer CMA
    |
    v
NCCL host buffer lands in MIGRATE_CMA pageblock
    |
    v
CUDA pins it without FOLL_LONGTERM
    |
    v
RDMA later requests a long-term pin
```

Part 3 explains the final two steps in kernel detail. The short version is that Linux must move a CMA page out of the reserve before allowing a long-term DMA pin. By the time RDMA asked, CUDA had already made that move impossible.

---

## 13. The reconstructed failing stack

The surviving host-memory path is source-consistent with this stack:

```text
RECONSTRUCTED TRACE
Derived from NCCL 2.17, Linux 6.x, and public NVIDIA source.
Not original production output.

NCCL host protocol buffer
  ncclNetRegMr(type = NCCL_PTR_HOST)
    ncclIbRegMrDmaBuf(fd = -1)  // common NCCL helper; -1 selects ordinary MR
      wrap_ibv_reg_mr_iova2()
        mlx5_ib_reg_user_mr()
          ib_umem_get()
            pin_user_pages_fast(
                FOLL_WRITE | FOLL_LONGTERM)
              long-term GUP validation
                pageblock = MIGRATE_CMA
                migrate page out of CMA
                  existing CUDA pin prevents migration
                return -ENOMEM
          return ERR_PTR(-ENOMEM)
        return NULL
      errno = ENOMEM

NCCL WARN:
Call to ibv_reg_mr_iova2 failed with error Cannot allocate memory
```

The exact GUP helper names vary across Linux versions. Depending on kernel generation and fast/slow-path behavior, the trace may include names such as `__gup_longterm_locked()`, `check_and_migrate_movable_pages()`, or `migrate_pages()`.

The contract is stable even when the symbols move:

```text
RDMA asks for a long-term pin
Linux finds a CMA-backed page
Linux tries to relocate it
relocation cannot complete
registration returns ENOMEM
```

This is why the top-level error was so unhelpful. “Cannot allocate memory” did not mean “no free memory.” It meant “the kernel could not construct a valid long-lived mapping for this device.”

---

## 14. Why R535 and DMA-BUF helped but did not finish the job

The rate reduction after the R535/DMA-BUF rollout is consistent with removing a separate failure class.

Before the rollout, GPU buffers depended on `nvidia-peermem` and the P2P invalidation lifecycle. NVIDIA had a documented race in that family. Fixing or bypassing it should reduce failures.

After the rollout, GPU buffers used DMA-BUF, but the host NCCL buffer remained:

```text
host pointer
    -> ordinary ibv_reg_mr_iova2()
    -> ib_umem_get()
    -> FOLL_LONGTERM
```

The public R535 NVIDIA host-page pin still did not request `FOLL_LONGTERM`, so the CMA ordering hazard remained possible.

This gives us a clean two-bug model:

```text
Bug class A: GPU peer-mapping lifecycle
    legacy nvidia-peermem / invalidation race
    reduced by vendor fix and DMA-BUF

Bug class B: CPU host page in CMA
    CUDA pin without long-term placement contract
    later RDMA long-term registration
    eliminated by removing HugeTLB CMA reserve
```

Both could surface at the NCCL layer as registration failures. One intervention reduced A. Only the final intervention removed B.

---

## 15. Why it looked random

Once the physical page is chosen, the mechanism is not random. The choice of physical page is.

The failure required several conditions to align:

```text
1. NCCL creates or first uses a host buffer.
2. Its pages are allocated from the HugeTLB CMA reserve.
3. CUDA pins those pages first.
4. RDMA registration happens later.
5. Linux cannot migrate at least one page out of CMA.
```

Allocator state depends on:

- which NUMA node served the allocation;
- which free lists contained pages at that instant;
- whether the CMA-first policy was active;
- prior anonymous and page-cache allocations;
- checkpoint worker activity;
- timing of lazy connections or new communicators;
- and the lifetime of other pins and references.

A retry changes most of that state. The same virtual address may receive different physical backing. A different rank may be the first to initialize a path. A checkpoint may begin a few milliseconds earlier or later.

The observed randomness was physical-page placement hidden behind a stable virtual-address API.

---

## 16. What checkpointing probably changed

Checkpointing was an amplifier, not a prerequisite.

We know that checkpointing used forked workers and that moving registration earlier reduced failures. We no longer have enough evidence to name one exclusive mechanism. Several are plausible and compatible:

```text
checkpointing may have:

- changed allocator history and host-memory pressure;
- increased page-cache and writeback activity;
- retained mappings or references in forked workers;
- overlapped peer-memory teardown;
- caused a lazy NCCL path to initialize at an unlucky time.
```

The later workload mattered because it removed checkpoint overlap and still produced the failure. That falsified the statement:

```text
checkpointing is required
```

It did not falsify:

```text
checkpointing increases the probability
```

This incident is separate from the page-granular `MADV_DONTFORK` bug in [the four-byte-buffer post](/blog/four-bytes-one-page/). Both involve RDMA registration and checkpoint workers, but the mechanisms differ:

```text
four-byte incident:
    libibverbs changes fork inheritance of an entire page

this incident:
    CUDA pins a CMA-backed page before RDMA requests long-term placement
```


---

## 17. The one-variable experiment

The final production change was small:

```text
before: hugetlb_cma=6G
after:  hugetlb_cma=0
```

The kernel build, NVIDIA R535 setup, DMA-BUF mode, NCCL generation, and workload remained the same.

The specific registration failure dropped to zero and stayed absent across the affected clusters for weeks and then months.

That result was stronger evidence than the reconstructed stack: it removed the implicated reserve while leaving the kernel, driver setup, registration mode, and workload unchanged. The nearby public driver source could not establish the exact proprietary call path on its own.

```text
CMA enabled
    -> order of hundreds of failures in bad weeks

CMA disabled
    -> no observed recurrence over weeks/months
```

The fix did not make `ibv_reg_mr_iova2()` more tolerant. It prevented the problematic page placement from existing on training nodes.

---

## 18. The causal chain

The final incident fits in one diagram:

<figure class="frame diagram">
  <span class="frame-title">fig. 5 · the causal chain, policy to log line</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 520" role="img" aria-label="Diagram: the full causal chain. A web-fleet requirement for dynamic one-gigabyte HugeTLB pages becomes the shared boot policy hugetlb_cma equals six gigabytes. The allocator policy consumes CMA first, so an NCCL CPU host buffer lands in a MIGRATE_CMA pageblock. CUDA maps and pins that host page without declaring FOLL_LONGTERM. NCCL later registers the same range with the ConnectX RNIC, and RDMA requests FOLL_LONGTERM. Linux must migrate the page out of CMA, the existing pin prevents migration, and the result is ENOMEM, printed as Call to ibv_reg_mr_iova2 failed.">
      <defs>
        <marker id="p2f5a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="180" y="24" width="380" height="40" fill="var(--krn)" opacity="0.10"/>
        <rect x="180" y="24" width="380" height="40" fill="none" stroke="var(--krn)" stroke-width="1.4"/>
        <text x="370" y="41" text-anchor="middle" fill="var(--krn)">web-fleet requirement</text>
        <text x="370" y="57" text-anchor="middle" font-size="10" fill="var(--muted)">dynamic 1 GiB HugeTLB pages</text>
        <rect x="180" y="86" width="380" height="34" fill="var(--krn)" opacity="0.10"/>
        <rect x="180" y="86" width="380" height="34" fill="none" stroke="var(--krn)" stroke-width="1.4"/>
        <text x="370" y="107" text-anchor="middle" fill="var(--krn)">shared boot policy: hugetlb_cma=6G</text>
        <rect x="180" y="142" width="380" height="34" fill="var(--krn)" opacity="0.10"/>
        <rect x="180" y="142" width="380" height="34" fill="none" stroke="var(--krn)" stroke-width="1.4"/>
        <text x="370" y="163" text-anchor="middle" fill="var(--krn)">allocator policy consumes CMA first</text>
        <rect x="180" y="198" width="380" height="34" fill="var(--sec)" opacity="0.10"/>
        <rect x="180" y="198" width="380" height="34" fill="none" stroke="var(--sec)" stroke-width="1.4"/>
        <text x="370" y="219" text-anchor="middle" fill="var(--sec)">NCCL host buffer lands in MIGRATE_CMA</text>
        <rect x="180" y="254" width="380" height="40" fill="var(--ldr)" opacity="0.10"/>
        <rect x="180" y="254" width="380" height="40" fill="none" stroke="var(--ldr)" stroke-width="1.4"/>
        <text x="370" y="271" text-anchor="middle" fill="var(--ldr)">CUDA maps and pins the host page</text>
        <text x="370" y="287" text-anchor="middle" font-size="10" fill="var(--muted)">without declaring FOLL_LONGTERM</text>
        <rect x="180" y="316" width="380" height="40" fill="var(--seg)" opacity="0.10"/>
        <rect x="180" y="316" width="380" height="40" fill="none" stroke="var(--seg)" stroke-width="1.4"/>
        <text x="370" y="333" text-anchor="middle" fill="var(--seg)">NCCL registers the same range with ConnectX</text>
        <text x="370" y="349" text-anchor="middle" font-size="10" fill="var(--muted)">RDMA requests FOLL_LONGTERM</text>
        <rect x="180" y="378" width="380" height="40" fill="var(--krn)" opacity="0.10"/>
        <rect x="180" y="378" width="380" height="40" fill="none" stroke="var(--krn)" stroke-width="1.4"/>
        <text x="370" y="395" text-anchor="middle" fill="var(--krn)">Linux must migrate the page out of CMA</text>
        <text x="370" y="411" text-anchor="middle" font-size="10" fill="var(--muted)">the existing pin prevents migration</text>
        <rect x="180" y="440" width="380" height="40" fill="var(--accent)" opacity="0.14"/>
        <rect x="180" y="440" width="380" height="40" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="370" y="457" text-anchor="middle" fill="var(--accent)">-ENOMEM</text>
        <text x="370" y="473" text-anchor="middle" font-size="10" fill="var(--accent)">"Call to ibv_reg_mr_iova2 failed"</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.3" fill="none" marker-end="url(#p2f5a)">
        <path d="M 370 64 L 370 82"/>
        <path d="M 370 120 L 370 138"/>
        <path d="M 370 176 L 370 194"/>
        <path d="M 370 232 L 370 250"/>
        <path d="M 370 294 L 370 312"/>
        <path d="M 370 356 L 370 374"/>
        <path d="M 370 418 L 370 436"/>
      </g>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)" text-anchor="end">
        <text x="168" y="46">another fleet's need</text>
        <text x="168" y="108">inherited config</text>
        <text x="168" y="164">a sensible optimization</text>
        <text x="168" y="220">allocator luck</text>
        <text x="168" y="276">NVIDIA driver</text>
        <text x="168" y="338">NCCL + RDMA core</text>
        <text x="168" y="400">Linux MM</text>
        <text x="168" y="462">what the operator saw</text>
      </g>
      <text x="360" y="506" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">the operator sees the last box. the cause lives seven boxes up, in another fleet's flags.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--krn)"></span>kernel / boot policy</span>
      <span><span class="k" style="background:var(--sec)"></span>host memory placement</span>
      <span><span class="k" style="background:var(--ldr)"></span>CUDA</span>
      <span><span class="k" style="background:var(--seg)"></span>RDMA</span>
      <span><span class="k" style="background:var(--accent)"></span>the failure</span>
    </p>
  </div>
</figure>

Around that chain sat a second real defect—the legacy NVIDIA P2P teardown race—which made the chronology harder to read and made partial fixes look complete.

---

## 19. What the incident changed in how I debug registration failures

### The transport label is not the allocation type

`GDRDMA` does not prove that the failed pointer is VRAM. Instrument the pointer type, allocation owner, direction, protocol, and size.

A useful registration log should include:

```text
address
length
host vs CUDA
send vs receive
LL / LL128 / SIMPLE
ordinary MR vs DMA-BUF MR
underlying errno
```

### `ENOMEM` needs provenance

The error should preserve the deepest failing layer:

```text
memlock accounting?
GUP pin?
page migration?
DMA map?
BAR map?
MKey allocation?
```

Collapsing all of them to `ncclSystemError` removes the information needed to distinguish them.

### A successful partial fix can hide a second bug

The NVIDIA patch and DMA-BUF rollout were not wrong. They removed real risk. The mistake would have been to infer that every later error with the same top-level text had the same cause.

### Configuration is an interface

A boot argument chosen for web servers changed which physical pages backed NCCL host buffers on H100 nodes. Shared boot configuration therefore needs to be checked against the workloads of each fleet.

### Production observability can be the experiment

When hardware is too expensive and the trigger too rare for a reserved lab, the debugging loop becomes:

```text
form a narrower hypothesis
    -> deploy a selective probe
    -> wait for a natural failure
    -> preserve one more layer
```

Retsnoop made that loop fast enough to use across real workloads.

---

## 20. What remains uncertain

The root mechanism is strong, but the historical record has limits.

Known from the incident:

- R525-era memory registrations failed intermittently.
- failures correlated with checkpointing but later occurred without it;
- NVIDIA supplied a P2P-related fix that reduced the rate;
- R535 and DMA-BUF reduced the rate again;
- the surviving failed pointer was host memory;
- its backing page belonged to `MIGRATE_CMA`;
- the fleet had an inherited approximately six-gigabyte HugeTLB CMA reserve;
- disabling that reserve was the only final change;
- the failure did not recur for weeks or months.

Verified from public source:

- NCCL 2.17 keeps a send-side LL buffer in host memory under GDR;
- same-process host memory is CUDA mapped;
- host buffers use ordinary MR registration in stock NCCL;
- RDMA host registration uses `FOLL_LONGTERM`;
- the public NVIDIA RM OS-descriptor path imports a userspace VA through `os_lock_user_pages()`;
- anonymous userspace faults use movable allocation flags, while NVIDIA's driver-owned `GFP_KERNEL` pages do not;
- public NVIDIA R525/R535 host pinning lacks `FOLL_LONGTERM`;
- public R555 adds it;
- Linux migrates long-term-unpinnable CMA pages;
- NVIDIA documented a separate P2P invalidation/put-pages race;
- the public CMA-first patch was motivated by HugeTLB reserves across a large fleet.

Reconstructed:

- the exact production buffer was `NCCL_PROTO_LL` rather than another NCCL host buffer;
- the precise symbol sequence inside the production kernel;
- which checkpoint activity most increased the probability;
- whether the private NVIDIA patch was exactly the publicly documented persistent-P2P change.

---

<div id="epilogue"></div>

[Part 3](/blog/pinned-memory-still-needs-to-move/) follows the two pinning calls into Linux: why RDMA needed the page to move, why the earlier CUDA pin prevented it, and how the failure became `ENOMEM`.

---

## Source map

- NCCL 2.17.1, [`src/transport/net.cc`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/transport/net.cc): host/device protocol-buffer placement and ordinary-versus-DMA-BUF registration.
- NCCL 2.17.1, [`src/include/alloc.h`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/include/alloc.h): CUDA mapped host allocation.
- NCCL 2.17.1, [`src/misc/shmutils.cc`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/misc/shmutils.cc): shared host mappings and `cudaHostRegister()`.
- NCCL 2.17.1, [`src/transport/net_ib.cc`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/transport/net_ib.cc): `ibv_reg_mr_iova2()` and `ibv_reg_dmabuf_mr()` wrappers.
- NCCL 2.17.1, [`src/init.cc`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/init.cc): CUDA-driver/device DMA-BUF capability probing.
- NVIDIA GPU Operator, [GPUDirect RDMA prerequisites](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html): complete DMA-BUF stack prerequisites.
- NVIDIA, [GPUDirect RDMA changes in CUDA 12.2](https://docs.nvidia.com/cuda/gpudirect-rdma/#changes-in-cuda-12-2): the P2P invalidation/put-pages race and persistent APIs.
- NVIDIA open modules, [`os-mlock.c` in R525.105.17](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/525.105.17/kernel-open/nvidia/os-mlock.c), [`R535.104.05`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/535.104.05/kernel-open/nvidia/os-mlock.c), and [`R555.42.02`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/555.42.02/kernel-open/nvidia/os-mlock.c): the host-page pinning flag change.
- NVIDIA open modules R535, [`escape.c`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/535.104.05/src/nvidia/arch/nvalloc/unix/src/escape.c), [`nv-linux.h`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/535.104.05/kernel-open/common/inc/nv-linux.h), and [`nv-vm.c`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/535.104.05/kernel-open/nvidia/nv-vm.c): userspace OS-descriptor import versus the driver's own `GFP_KERNEL` allocation path.
- Linux v6.6, [`mm/memory.c`](https://github.com/torvalds/linux/blob/v6.6/mm/memory.c), [`include/linux/highmem.h`](https://github.com/torvalds/linux/blob/v6.6/include/linux/highmem.h), and [`include/linux/gfp_types.h`](https://github.com/torvalds/linux/blob/v6.6/include/linux/gfp_types.h): anonymous page faults use `GFP_HIGHUSER_MOVABLE`, which includes `__GFP_MOVABLE`.
- tinygrad, [`tinygrad/runtime/ops_nv.py`](https://github.com/tinygrad/tinygrad/blob/07268b724fe63e45ba33be193ee679dbf02b163f/tinygrad/runtime/ops_nv.py): independent clean-room corroboration of anonymous host mapping followed by NVIDIA OS-descriptor import; not an official libcuda specification.
- Linux v6.6, [`drivers/infiniband/core/umem.c`](https://github.com/torvalds/linux/blob/v6.6/drivers/infiniband/core/umem.c): memlock accounting followed by RDMA's `FOLL_LONGTERM` host registration.
- Linux v6.6, [`mm/page_alloc.c`](https://github.com/torvalds/linux/blob/v6.6/mm/page_alloc.c): stock conditional CMA fallback for movable allocations.
- Linux v6.6, [`mm/hugetlb.c`](https://github.com/torvalds/linux/blob/v6.6/mm/hugetlb.c): distribution of a global `hugetlb_cma` request across online NUMA nodes.
- Linux kernel parameters, [`hugetlb_cma`](https://www.kernel.org/doc/html/v6.6/admin-guide/kernel-parameters.html): CMA reserved for gigantic HugeTLB allocation.
- Johannes Weiner, [`mm: page_alloc: consume available CMA space first`](https://lkml.iu.edu/hypermail/linux/kernel/2307.3/04508.html): the CMA-first policy and its fleet/HugeTLB motivation.
- Andrii Nakryiko, [retsnoop](https://github.com/anakryiko/retsnoop): selective error-path kernel tracing.
- Meta Engineering, [Building Meta’s GenAI Infrastructure](https://engineering.fb.com/2024/03/12/data-center-engineering/building-metas-genai-infrastructure/): public context for the scale and Grand Teton/H100 deployment.
- PyTorch forum, [representative NCCL `ibv_reg_mr` `ENOMEM` signature](https://discuss.pytorch.org/t/pytorch-ddp-nccl-error-call-to-ibv-reg-mr-failed-with-error-cannot-allocate-memory/191767): an example of the generic surface error, not evidence of this root cause.

