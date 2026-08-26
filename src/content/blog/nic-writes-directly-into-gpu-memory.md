---
title: "howtf does a NIC write directly into GPU memory?"
description: "A ground-up walk through DMA, RDMA, PCIe BARs, memory registration, MKeys, nvidia-peermem, and DMA-BUF. Part 1 of Memory Registration, All the Way Down."
date: 2026-08-20
series:
  name: "Memory Registration, All the Way Down"
  part: 1
tags: [rdma, gpudirect, pcie, linux]
draft: false
---

The original production logs are no longer available, but the failure line was in this family:

```text
Call to ibv_reg_mr_iova2 failed with error Cannot allocate memory
```

The `ibv` prefix comes from **InfiniBand Verbs**, the userspace programming interface exposed by `libibverbs`. The same verbs model is also used by ConnectX adapters carrying RoCE; “verbs” names the programming interface, not necessarily the wire protocol.

It was ordinary enough that the first searches led to the standard list: locked-memory limits, BAR1 exhaustion, driver mismatches, too many registered regions, an unhealthy network adapter. All reasonable. None explains what the machine was actually trying to construct when that call failed.

Before following the failure, we need that construction in our heads.

What does it mean to “register” memory with a NIC? Which address does the NIC use? How does a CPU virtual address become a table inside a ConnectX adapter? Why does GPU memory involve BAR1? What does the NVIDIA driver do during registration, and what does it *not* do when the actual network write arrives?

This post builds that path from the bottom up. The [production failure](/blog/gpu-registration-failure-from-host-ram/) comes in Part 2. The [Linux pinning conflict underneath it](/blog/pinned-memory-still-needs-to-move/) comes in Part 3.

> **Scope note.** The hardware model here is an NVIDIA H100-class GPU and a ConnectX-7-class RDMA NIC on Linux. The interfaces are verified against the public NVIDIA Collective Communications Library (NCCL), rdma-core, Linux’s `mlx5` ConnectX driver, and NVIDIA driver source. Hardware implementations change, so names such as MTT and PAS should be read as the concrete mlx5 form of a more general idea: a device-side translation from an address in a memory region to DMA-reachable pages.

---

## 1. Start with DMA, not RDMA

A CPU normally moves data with loads and stores:

```text
CPU load from A
CPU store to B
CPU load from A+8
CPU store to B+8
...
```

That is a terrible way to move a large packet. The CPU would spend its time acting as a copy engine.

**Direct Memory Access**, or DMA, gives the copy engine to the device. The CPU still sets the operation up. It allocates descriptors, tells the device where they are, rings a doorbell, and handles completion. But once the operation is running, the device issues the memory transactions itself.

```text
                setup                       data movement

CPU  ----------------------------->  device DMA engine
      descriptor: source, length,          |
      destination, permissions             | memory reads/writes
                                            v
                                      system memory
```

“Direct” does not mean “the CPU has no involvement.” It means the CPU is not executing one instruction per transferred cache line.

A network adapter already needs DMA. For transmit, it reads packet bytes from memory. For receive, it writes packet bytes into memory. Conventional networking normally puts kernel-owned buffers in the middle:

```text
application buffer
      |
      | copy / protocol processing
      v
kernel socket buffer
      |
      | NIC DMA
      v
network
```

RDMA changes who is allowed to name the final memory and how much of the CPU networking stack sits in the data path.

---

## 2. RDMA is network-triggered DMA into registered memory

Consider two machines, A and B. A process on B owns a buffer. It wants A to write directly into that buffer.

B cannot safely tell A, “write to virtual address `0x7f...`.” That address belongs to B’s process page tables. A’s NIC cannot walk them, and B may not even keep the same physical pages underneath that virtual range.

Instead, B registers the range with its local RDMA NIC—an **RNIC**, or RDMA-capable NIC. InfiniBand documentation often calls the same class of device a **Host Channel Adapter (HCA)**. Registration creates a memory region, usually shortened to **MR**. B then gives A two important values:

```text
remote address: where inside the MR to start
rkey:           the capability authorizing remote access
```

A posts an RDMA-write work request containing its local source buffer and B’s remote address and `rkey`.

<figure class="frame diagram">
  <span class="frame-title">fig. 1 · RDMA is network-triggered DMA into registered memory</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 330" role="img" aria-label="Diagram: on machine A, an application posts an RDMA write to its local RNIC. RNIC A sends network packets to RNIC B on machine B. RNIC B validates the rkey, checks bounds, translates the address, and performs a DMA write into the target buffer that machine B's application registered in advance. The remote CPU does not copy the payload.">
      <defs>
        <marker id="p1f1a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="p1f1b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="20" y="24" width="310" height="264" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="34" y="44" fill="var(--muted)">machine A</text>
        <rect x="390" y="24" width="310" height="264" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="404" y="44" fill="var(--muted)">machine B</text>
        <rect x="50" y="60" width="250" height="38" fill="none" stroke="var(--muted)" stroke-width="1"/>
        <text x="175" y="83" text-anchor="middle" fill="var(--text)">application</text>
        <rect x="420" y="60" width="250" height="38" fill="none" stroke="var(--muted)" stroke-width="1"/>
        <text x="545" y="76" text-anchor="middle" fill="var(--text)">application</text>
        <text x="545" y="91" text-anchor="middle" font-size="10" fill="var(--muted)">owns the target buffer</text>
        <rect x="50" y="168" width="250" height="42" fill="var(--seg)" opacity="0.14"/>
        <rect x="50" y="168" width="250" height="42" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="175" y="193" text-anchor="middle" fill="var(--seg)">RNIC A</text>
        <rect x="420" y="168" width="250" height="42" fill="var(--seg)" opacity="0.14"/>
        <rect x="420" y="168" width="250" height="42" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="545" y="193" text-anchor="middle" fill="var(--seg)">RNIC B</text>
        <rect x="420" y="244" width="250" height="34" fill="var(--accent)" opacity="0.14"/>
        <rect x="420" y="244" width="250" height="34" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="545" y="265" text-anchor="middle" fill="var(--accent)">target memory · registered MR</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.4" fill="none" marker-end="url(#p1f1a)">
        <path d="M 175 98 L 175 164"/>
        <path d="M 300 185 L 416 185"/>
        <path d="M 300 193 L 416 193"/>
      </g>
      <g stroke="var(--accent)" stroke-width="1.4" fill="none" marker-end="url(#p1f1b)">
        <path d="M 545 210 L 545 240"/>
        <path d="M 545 98 C 700 110, 706 200, 674 250"/>
      </g>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)">
        <text x="185" y="120" >post RDMA write</text>
        <text x="185" y="133">local: addr + lkey</text>
        <text x="185" y="146">remote: addr + rkey</text>
        <text x="358" y="172" text-anchor="middle">network packets</text>
        <text x="556" y="222">validate rkey · translate · DMA write</text>
      </g>
      <text x="652" y="140" text-anchor="end" font-family="var(--font-display)" font-size="10" fill="var(--accent)">registered in advance,</text>
      <text x="652" y="153" text-anchor="end" font-family="var(--font-display)" font-size="10" fill="var(--accent)">rkey handed to A</text>
      <text x="360" y="316" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">the remote CPU never copies the payload. "registered in advance" carries this series.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>RNIC</span>
      <span><span class="k" style="background:var(--accent)"></span>registered memory region</span>
    </p>
  </div>
</figure>

The remote CPU does not copy the payload. It may have participated earlier—creating the queue pair, registering memory, exchanging metadata—but the data can arrive without a receive-side system call for every transfer. The registration mechanism described here is shared by InfiniBand and RDMA over Converged Ethernet (RoCE), even though their network transports differ.

That is the useful one-sentence definition:

> **RDMA lets a remote peer cause a local NIC to perform DMA against memory that was registered in advance.**

The phrase “registered in advance” contains most of this series.

---

## 3. PCIe is an addressed fabric

The GPU and RNIC are PCIe devices. It is tempting to picture PCIe as a collection of wires connecting devices to the CPU. A better model for this story is an **addressed transaction fabric**.

A PCIe requester can issue transactions such as:

```text
Memory Read  address=X length=N
Memory Write address=Y payload=...
```

Root ports and switches route those transactions through the PCIe topology. The destination may be system RAM, a device register window, or a peer device’s memory aperture.

A simplified machine-wide map might look like this:

<figure class="frame diagram">
  <span class="frame-title">fig. 2 · one addressed fabric, many windows</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 330" role="img" aria-label="Diagram: the PCIe and host physical address space drawn as one vertical map. From the top: system RAM, holes and firmware regions, ConnectX BARs, GPU BAR0 for control MMIO, GPU BAR1 as the framebuffer aperture, and other devices. On the left, a PCIe requester such as the RNIC issues Memory Read and Memory Write transactions; switches and root ports route each transaction to whichever window its address falls in.">
      <defs>
        <marker id="p1f2a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="p1f2b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ldr)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="400" y="30" fill="var(--muted)">PCIe / host physical address space</text>
        <rect x="400" y="42" width="290" height="44" fill="var(--sec)" opacity="0.12"/>
        <rect x="400" y="42" width="290" height="44" fill="none" stroke="var(--sec)" stroke-width="1.4"/>
        <text x="545" y="68" text-anchor="middle" fill="var(--sec)">system RAM</text>
        <rect x="400" y="86" width="290" height="30" fill="none" stroke="var(--muted)" stroke-width="1" stroke-dasharray="4 3"/>
        <text x="545" y="105" text-anchor="middle" fill="var(--muted)">holes / firmware regions</text>
        <rect x="400" y="116" width="290" height="36" fill="var(--seg)" opacity="0.14"/>
        <rect x="400" y="116" width="290" height="36" fill="none" stroke="var(--seg)" stroke-width="1.4"/>
        <text x="545" y="138" text-anchor="middle" fill="var(--seg)">ConnectX BARs</text>
        <rect x="400" y="152" width="290" height="36" fill="var(--ldr)" opacity="0.10"/>
        <rect x="400" y="152" width="290" height="36" fill="none" stroke="var(--ldr)" stroke-width="1.4"/>
        <text x="545" y="174" text-anchor="middle" fill="var(--ldr)">GPU BAR0 · control MMIO</text>
        <rect x="400" y="188" width="290" height="58" fill="var(--ldr)" opacity="0.18"/>
        <rect x="400" y="188" width="290" height="58" fill="none" stroke="var(--ldr)" stroke-width="1.8"/>
        <text x="545" y="212" text-anchor="middle" fill="var(--ldr)">GPU BAR1 · framebuffer aperture</text>
        <text x="545" y="228" text-anchor="middle" font-size="10" fill="var(--muted)">a window, not a copy of VRAM</text>
        <rect x="400" y="246" width="290" height="30" fill="none" stroke="var(--muted)" stroke-width="1"/>
        <text x="545" y="265" text-anchor="middle" fill="var(--muted)">other devices</text>
        <text x="392" y="52" text-anchor="end" font-size="10" fill="var(--muted)">0x0000_0000_0000</text>
        <rect x="30" y="86" width="180" height="40" fill="var(--seg)" opacity="0.14"/>
        <rect x="30" y="86" width="180" height="40" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="120" y="110" text-anchor="middle" fill="var(--seg)">PCIe requester · RNIC</text>
        <rect x="30" y="176" width="180" height="52" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="120" y="197" text-anchor="middle" fill="var(--text)">root ports / switches</text>
        <text x="120" y="213" text-anchor="middle" font-size="10" fill="var(--muted)">route by address</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.4" fill="none" marker-end="url(#p1f2a)">
        <path d="M 120 126 L 120 172"/>
        <path d="M 210 190 L 396 64"/>
      </g>
      <g stroke="var(--ldr)" stroke-width="1.6" fill="none" marker-end="url(#p1f2b)">
        <path d="M 210 212 L 396 216"/>
      </g>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)">
        <text x="128" y="147">Memory Write addr=Y</text>
        <text x="128" y="160">Memory Read  addr=X</text>
        <text x="238" y="120">to system RAM</text>
        <text x="238" y="240" fill="var(--ldr)">to a peer device window</text>
      </g>
      <text x="360" y="312" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">a transaction's destination is whatever owns its address: RAM, a register window, a peer.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>system RAM</span>
      <span><span class="k" style="background:var(--seg)"></span>RNIC / ConnectX</span>
      <span><span class="k" style="background:var(--ldr)"></span>GPU windows</span>
    </p>
  </div>
</figure>

The exact addresses differ by machine. The important point is that devices own windows in an address domain that PCIe transactions can target.

How are those windows established? Through PCI configuration space and Base Address Registers.

---

## 4. A PCI BAR is a request for an address window

Every PCIe function exposes configuration space. Among the standard fields are up to six Base Address Registers: BAR0 through BAR5.

Despite the name, a BAR is not a giant array of device data stored inside the register. It is a compact description of an address window the device needs.

At enumeration, firmware or the operating system performs a sizing exchange with the device, reserves an appropriately sized region in the host/PCI address map, and writes the chosen base address into the BAR. Linux then records that region as a PCI resource. Drivers can claim and map it.

```text
PCI configuration space                  PCIe address space

BAR0 = 0xC000_0000  ------------------->  [device window 0]
BAR1 = 0x8000_0000_0000  ------------->  [device window 1]
```

BAR regions have a useful hardware constraint: their sizes are powers of two and their bases are naturally aligned to those sizes. That is why a GPU with 80 GiB of framebuffer can expose a 128 GiB BAR1 aperture. The BAR is an address-decoding window, not a byte-for-byte statement of installed VRAM. The next power-of-two aperture can contain unused address space or device-defined regions that do not correspond to usable framebuffer.

A 64-bit BAR consumes two adjacent 32-bit BAR slots because the base address itself needs 64 bits.

### BAR0 and BAR1 on an NVIDIA GPU

The names are conventions tied to the device implementation, not universal PCI meanings. On NVIDIA data-center GPUs, the useful mental model is:

```text
BAR0   control and register MMIO
BAR1   aperture through which framebuffer memory can be reached
```

BAR0 lets software interact with the device’s control machinery. BAR1 makes selected GPU framebuffer pages visible in the PCIe address space so a CPU or peer device can access them. NVIDIA’s NVML documentation describes BAR1 as the mapping used for direct CPU or third-party-device access to framebuffer memory.

The word **aperture** matters. BAR1 is not another copy of VRAM. It is an address window through which the GPU exposes framebuffer mappings.

<figure class="frame diagram">
  <span class="frame-title">fig. 3 · BAR1 is an aperture, not a second copy of VRAM</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 300" role="img" aria-label="Diagram: on the left, GPU framebuffer pages A, B, and C inside VRAM. On the right, the PCIe-visible BAR1 aperture with numbered slots. The NVIDIA driver maps page A to slot 17, page B to slot 18, and page C to slot 42. Unmapped slots remain empty. A PCIe write that lands in a BAR1 slot is steered by the GPU's aperture translation to the underlying framebuffer page.">
      <defs>
        <marker id="p1f3a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ldr)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="40" y="46" width="220" height="216" fill="var(--ldr)" opacity="0.07"/>
        <rect x="40" y="46" width="220" height="216" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="54" y="66" fill="var(--ldr)">GPU framebuffer · VRAM</text>
        <rect x="60" y="82" width="180" height="32" fill="var(--ldr)" opacity="0.18"/>
        <rect x="60" y="82" width="180" height="32" fill="none" stroke="var(--ldr)" stroke-width="1.2"/>
        <text x="150" y="102" text-anchor="middle" fill="var(--text)">GPU page A</text>
        <rect x="60" y="124" width="180" height="32" fill="var(--ldr)" opacity="0.18"/>
        <rect x="60" y="124" width="180" height="32" fill="none" stroke="var(--ldr)" stroke-width="1.2"/>
        <text x="150" y="144" text-anchor="middle" fill="var(--text)">GPU page B</text>
        <rect x="60" y="166" width="180" height="32" fill="var(--ldr)" opacity="0.18"/>
        <rect x="60" y="166" width="180" height="32" fill="none" stroke="var(--ldr)" stroke-width="1.2"/>
        <text x="150" y="186" text-anchor="middle" fill="var(--text)">GPU page C</text>
        <text x="150" y="238" text-anchor="middle" font-size="10" fill="var(--muted)">…the rest of VRAM,</text>
        <text x="150" y="252" text-anchor="middle" font-size="10" fill="var(--muted)">not necessarily mapped</text>
        <rect x="470" y="46" width="210" height="216" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="484" y="66" fill="var(--seg)">BAR1 aperture · PCIe-visible</text>
        <rect x="490" y="82" width="170" height="28" fill="var(--seg)" opacity="0.14"/>
        <rect x="490" y="82" width="170" height="28" fill="none" stroke="var(--seg)" stroke-width="1.2"/>
        <text x="575" y="100" text-anchor="middle" fill="var(--text)">slot 17</text>
        <rect x="490" y="118" width="170" height="28" fill="var(--seg)" opacity="0.14"/>
        <rect x="490" y="118" width="170" height="28" fill="none" stroke="var(--seg)" stroke-width="1.2"/>
        <text x="575" y="136" text-anchor="middle" fill="var(--text)">slot 18</text>
        <rect x="490" y="154" width="170" height="28" fill="none" stroke="var(--muted)" stroke-width="1" stroke-dasharray="4 3"/>
        <text x="575" y="172" text-anchor="middle" fill="var(--muted)">…</text>
        <rect x="490" y="190" width="170" height="28" fill="var(--seg)" opacity="0.14"/>
        <rect x="490" y="190" width="170" height="28" fill="none" stroke="var(--seg)" stroke-width="1.2"/>
        <text x="575" y="208" text-anchor="middle" fill="var(--text)">slot 42</text>
        <text x="575" y="248" text-anchor="middle" font-size="10" fill="var(--muted)">nvidia-smi -q: total / used / free</text>
      </g>
      <g stroke="var(--ldr)" stroke-width="1.4" fill="none" marker-end="url(#p1f3a)">
        <path d="M 240 98 L 486 96"/>
        <path d="M 240 140 L 486 132"/>
        <path d="M 240 182 L 486 204"/>
      </g>
      <text x="363" y="80" text-anchor="middle" font-family="var(--font-display)" font-size="10" fill="var(--muted)">driver-managed mappings</text>
      <text x="360" y="288" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">selected framebuffer pages become PCIe-reachable through the window. nothing is copied.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--ldr)"></span>GPU framebuffer pages</span>
      <span><span class="k" style="background:var(--seg)"></span>PCIe-visible aperture slots</span>
    </p>
  </div>
</figure>

On a large-BAR system the aperture may be big enough to cover all framebuffer memory at once. On smaller-BAR systems the driver manages a limited window and consumes BAR1 space as peer mappings are created. `nvidia-smi -q` reports total, used, and free BAR1 space; NVIDIA’s GPUDirect documentation notes that mappings are managed in fixed-size chunks and that some space is reserved internally.

### Resizable BAR

Classic BAR sizes are selected from capabilities exposed by the device. PCIe Resizable BAR lets software choose among multiple supported aperture sizes. Platform firmware still has to reserve enough address space—hence the familiar “Above 4G Decoding” and Resizable BAR firmware settings on systems with large GPU apertures.

None of this yet tells the RNIC which CUDA allocation it may access. BAR1 establishes that GPU memory *can* be represented in PCIe space. Registration establishes the exact pages, permissions, lifetime, and RNIC translation.

---

## 5. There is no single “physical address”

Most confusion in GPUDirect discussions comes from using the word “address” without naming the address space.

For this path, hold at least five different kinds of address:

```text
1. CPU process virtual address
2. CUDA / GPU virtual address
3. CPU physical page number or host physical address
4. DMA address visible to a particular device
5. MR IOVA: the address the RNIC exposes through the memory key
```

They can sometimes have the same numeric value. That does not make them the same concept.

### CPU process virtual address

A normal pointer such as `0x7f23...` is interpreted through a process’s CPU page tables:

```text
CPU virtual address
        |
        | CPU page-table walk
        v
host physical page
```

Linux represents ordinary RAM pages with `struct page` objects and page frame numbers, or PFNs.

### CUDA virtual address

A pointer returned by CUDA belongs to a CUDA-managed virtual-address space. For device memory, the CPU cannot simply walk its own page tables to discover ordinary RAM underneath it. The NVIDIA driver and GPU page tables own the mapping from the CUDA virtual range to framebuffer pages.

```text
CUDA virtual address
        |
        | GPU/NVIDIA translation state
        v
GPU framebuffer pages
```

Unified Virtual Addressing makes CPU and GPU pointers share a single-looking process address space, but it does not erase the different backing stores or page-table owners.

### DMA address

Linux’s DMA API gives a device an address it can use for DMA. With the **I/O Memory Management Unit (IOMMU)** disabled or in passthrough, that value may closely resemble a host physical or peer PCIe address. With an IOMMU enabled, it may be an I/O virtual address translated again before the PCIe transaction reaches its target.

```text
RNIC DMA address
        |
        | optional IOMMU translation
        v
host RAM or peer PCIe address
```

This is why “the RDMA driver copies the GPU physical page numbers into the NIC” is too loose. The RNIC needs **DMA addresses valid from that RNIC’s point of view**. NVIDIA added `nvidia_p2p_dma_map_pages()` precisely because a peer resource’s CPU-visible physical address and a particular I/O device’s usable DMA address need not be identical.

### MR IOVA

The memory region has its own externally visible address range. `ibv_reg_mr_iova2()` lets the caller specify the base IOVA that the RNIC should associate with the region.

```c
mr = ibv_reg_mr_iova2(pd, addr, length, iova, access);
```

`addr` identifies the userspace range whose backing memory must be registered. `iova` identifies the virtual base the RNIC will expose through the MR. NCCL commonly uses the same numeric value for both, but the API keeps them conceptually separate.

That distinction lets an application register one userspace range while presenting a chosen virtual base to the device.

---

## 6. What memory registration actually builds

A memory region is not just a “pinned” flag attached to a pointer. Registration creates a capability and a translation object inside the RDMA stack and RNIC.

Conceptually:

```text
Memory Region

  owner:        protection domain PD
  virtual base: IOVA
  length:       N bytes
  permissions:  local write, remote read, remote write, ...
  translations:
      IOVA page 0 -> DMA address A
      IOVA page 1 -> DMA address B
      IOVA page 2 -> DMA address C
  keys:
      lkey
      rkey
```

### Protection domain

A protection domain, or PD, groups RDMA objects that are allowed to interact. Queue pairs and memory regions must belong to compatible protection domains. It is a software and hardware isolation boundary, not a Linux process namespace.

### `lkey`

A local scatter/gather entry includes an address, length, and `lkey`. The RNIC uses the `lkey` to verify that the local work request is allowed to read or write that range.

### `rkey`

A remote operation carries an `rkey`. The destination RNIC uses it to find the memory region, verify remote permissions and bounds, and reach the translation state.

An `rkey` is therefore a capability. Knowing an address without the matching key is insufficient.

### MKey, MTT, and PAS

On mlx5 hardware, the kernel driver creates a **memory key**, or MKey. The MKey carries the access policy and points to address-translation information. Different generations and code paths use terms such as MTT—Memory Translation Table—and PAS arrays—physical-address lists—for the page translations loaded into that object.

A useful approximation is:

```text
MKey = permissions + bounds + page geometry + pointer to translation entries

MTT/PAS entries:
    virtual page index 0 -> DMA page address 0
    virtual page index 1 -> DMA page address 1
    virtual page index 2 -> DMA page address 2
```

The mlx5 Linux driver may create the MKey directly or use UMR—User-mode Memory Registration machinery—to load or update its translation. The implementation is more sophisticated than a flat array: it can select larger page sizes, cache MKeys, and update translations in hardware. But “the RNIC gets a protected page table for this MR” is the right first model.

The RNIC does **not** consult the CPU page table on every packet. Registration resolves and installs the alternate translation path ahead of time.

<figure class="frame diagram">
  <span class="frame-title">fig. 4 · two translation paths to the same physical page</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 320" role="img" aria-label="Diagram: two parallel translation paths end at one physical page. On the left, the CPU path: a process virtual address walks the CPU page table, owned by the Linux MM. On the right, the RNIC path: an MR IOVA plus a key goes through MKey lookup with permissions and bounds, then MTT or PAS translation entries, producing a DMA address for the same page. Registration built the right path in advance; the RNIC never consults the CPU page table per packet.">
      <defs>
        <marker id="p1f4a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--krn)"/>
        </marker>
        <marker id="p1f4b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--seg)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="170" y="34" text-anchor="middle" fill="var(--muted)">CPU access</text>
        <text x="550" y="34" text-anchor="middle" fill="var(--muted)">RNIC access</text>
        <rect x="70" y="48" width="200" height="34" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="170" y="69" text-anchor="middle" fill="var(--text)">process VA</text>
        <rect x="70" y="118" width="200" height="42" fill="var(--krn)" opacity="0.14"/>
        <rect x="70" y="118" width="200" height="42" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="170" y="136" text-anchor="middle" fill="var(--krn)">CPU page table</text>
        <text x="170" y="152" text-anchor="middle" font-size="10" fill="var(--muted)">owned by Linux MM</text>
        <rect x="450" y="48" width="200" height="34" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="550" y="69" text-anchor="middle" fill="var(--text)">MR IOVA + key</text>
        <rect x="450" y="118" width="200" height="42" fill="var(--seg)" opacity="0.14"/>
        <rect x="450" y="118" width="200" height="42" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="550" y="136" text-anchor="middle" fill="var(--seg)">MKey lookup</text>
        <text x="550" y="152" text-anchor="middle" font-size="10" fill="var(--muted)">permissions + bounds</text>
        <rect x="450" y="180" width="200" height="42" fill="var(--seg)" opacity="0.14"/>
        <rect x="450" y="180" width="200" height="42" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="550" y="198" text-anchor="middle" fill="var(--seg)">MTT / PAS entries</text>
        <text x="550" y="214" text-anchor="middle" font-size="10" fill="var(--muted)">installed at registration</text>
        <rect x="260" y="252" width="200" height="40" fill="var(--accent)" opacity="0.14"/>
        <rect x="260" y="252" width="200" height="40" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="360" y="276" text-anchor="middle" fill="var(--accent)">physical page</text>
      </g>
      <g stroke="var(--krn)" stroke-width="1.4" fill="none" marker-end="url(#p1f4a)">
        <path d="M 170 82 L 170 114"/>
        <path d="M 170 160 C 170 220, 220 258, 256 266"/>
      </g>
      <g stroke="var(--seg)" stroke-width="1.4" fill="none" marker-end="url(#p1f4b)">
        <path d="M 550 82 L 550 114"/>
        <path d="M 550 160 L 550 176"/>
        <path d="M 550 222 C 550 260, 510 270, 464 274"/>
      </g>
      <text x="502" y="252" font-family="var(--font-display)" font-size="10" fill="var(--muted)" text-anchor="end">DMA address</text>
      <text x="360" y="312" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">two translations agree on one page. only one updates when Linux changes its mind.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--krn)"></span>CPU / Linux MM translation</span>
      <span><span class="k" style="background:var(--seg)"></span>RNIC translation state</span>
      <span><span class="k" style="background:var(--accent)"></span>the one physical page</span>
    </p>
  </div>
</figure>

That second path is why memory lifetime matters. If Linux moved the physical page while the RNIC still held the old translation, the next packet would DMA into the wrong place.

---

## 7. Registering ordinary CPU memory

Now walk the ordinary host-memory path used by a classic mlx5 userspace MR.

NCCL eventually reaches its InfiniBand network plugin, which calls either `ibv_reg_mr()` or `ibv_reg_mr_iova2()`. The latter is used in the NCCL 2.17 generation when relaxed ordering is enabled.

```text
NCCL
  -> libibverbs
    -> mlx5 userspace provider
      -> uverbs ioctl
        -> mlx5_ib kernel driver
```

Inside the kernel, `mlx5_ib_reg_user_mr()` asks RDMA core to obtain an `ib_umem` for the range:

```text
mlx5_ib_reg_user_mr()
    |
    v
ib_umem_get()
```

`ib_umem_get()` performs four important jobs.

### 7.1 Account the pin

Linux checks the process’s locked-memory allowance, represented by `RLIMIT_MEMLOCK` unless the process has the relevant capability. This is one reason `ibv_reg_mr()` can return `ENOMEM` even when the machine has plenty of free RAM: the word describes a resource-accounting failure, not necessarily exhausted DRAM.

### 7.2 Pin the pages

RDMA core calls `pin_user_pages_fast()` with `FOLL_LONGTERM`, and with `FOLL_WRITE` when the device may write the region.

```text
userspace VA range
      |
      | pin_user_pages_fast(FOLL_LONGTERM)
      v
array of struct page pointers
```

Pinning stabilizes the backing pages so they cannot be reclaimed, migrated, or replaced in ways that would invalidate the device mapping for the lifetime of the MR.

It does **not** make the pages physically contiguous. A 16 MiB virtual range can be backed by thousands of scattered 4 KiB pages.

### 7.3 Build a scatter/gather representation

RDMA core merges adjacent pages where possible into a scatter/gather table:

```text
virtual range

page 0 -> PFN 91
page 1 -> PFN 92    } one contiguous SG segment
page 2 -> PFN 501
page 3 -> PFN 900
```

### 7.4 DMA-map it for the RNIC

The table is passed through the Linux DMA API for the RNIC. That step produces the DMA addresses valid for the ConnectX device, including any IOMMU mapping.

```text
struct page / host PFN
        |
        | dma_map_sgtable(RNIC)
        v
RNIC-visible DMA address
```

The mlx5 driver then creates an MKey and loads those DMA addresses into its translation state.

The whole path is:

<figure class="frame diagram">
  <span class="frame-title">fig. 5 · what a host-memory registration actually builds</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 330" role="img" aria-label="Diagram: the ordinary host-memory registration pipeline as six stages. A CPU virtual address is accounted against the locked-memory limit, then pinned long-term into Linux pages via pin_user_pages_fast with FOLL_LONGTERM, merged into a scatter-gather table, DMA-mapped to produce RNIC-visible DMA addresses, loaded into an MKey with MTT or PAS translation entries, and finally exposed as an lkey and rkey.">
      <defs>
        <marker id="p1f5a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="30" y="40" width="190" height="40" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="125" y="64" text-anchor="middle" fill="var(--text)">CPU virtual address</text>
        <rect x="265" y="40" width="190" height="40" fill="var(--krn)" opacity="0.14"/>
        <rect x="265" y="40" width="190" height="40" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="360" y="64" text-anchor="middle" fill="var(--krn)">pinned Linux pages</text>
        <rect x="500" y="40" width="190" height="40" fill="var(--krn)" opacity="0.14"/>
        <rect x="500" y="40" width="190" height="40" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="595" y="64" text-anchor="middle" fill="var(--krn)">scatter/gather table</text>
        <rect x="500" y="170" width="190" height="40" fill="var(--seg)" opacity="0.14"/>
        <rect x="500" y="170" width="190" height="40" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="595" y="194" text-anchor="middle" fill="var(--seg)">RNIC DMA addresses</text>
        <rect x="265" y="170" width="190" height="40" fill="var(--seg)" opacity="0.14"/>
        <rect x="265" y="170" width="190" height="40" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="360" y="188" text-anchor="middle" fill="var(--seg)">MKey + MTT/PAS</text>
        <text x="360" y="203" text-anchor="middle" font-size="10" fill="var(--muted)">translations loaded</text>
        <rect x="30" y="170" width="190" height="40" fill="var(--accent)" opacity="0.14"/>
        <rect x="30" y="170" width="190" height="40" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="125" y="194" text-anchor="middle" fill="var(--accent)">lkey / rkey</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.4" fill="none" marker-end="url(#p1f5a)">
        <path d="M 220 60 L 261 60"/>
        <path d="M 455 60 L 496 60"/>
        <path d="M 595 80 L 595 166"/>
        <path d="M 496 190 L 459 190"/>
        <path d="M 261 190 L 224 190"/>
      </g>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)">
        <text x="240 " y="34" text-anchor="middle">1. account RLIMIT_MEMLOCK</text>
        <text x="240" y="96" text-anchor="middle">2. pin_user_pages_fast(</text>
        <text x="240" y="109" text-anchor="middle">FOLL_LONGTERM)</text>
        <text x="475" y="34" text-anchor="middle">3. merge adjacent pages</text>
        <text x="712" y="120" text-anchor="end">4. dma_map_sgtable(RNIC)</text>
        <text x="712" y="133" text-anchor="end">IOMMU applies here</text>
        <text x="477" y="164" text-anchor="middle">mlx5 programs the device</text>
        <text x="242" y="164" text-anchor="middle">handed back to userspace</text>
      </g>
      <text x="360" y="252" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">an MR is not a flag on a pointer. it is state held in the kernel and in the device.</text>
      <g font-family="var(--font-mono)" font-size="10" fill="var(--muted)">
        <text x="360" y="286" text-anchor="middle">NCCL -> libibverbs -> mlx5 provider -> uverbs ioctl -> mlx5_ib_reg_user_mr() -> ib_umem_get()</text>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--krn)"></span>Linux MM state</span>
      <span><span class="k" style="background:var(--seg)"></span>RNIC translation state</span>
      <span><span class="k" style="background:var(--accent)"></span>capabilities returned to the caller</span>
    </p>
  </div>
</figure>

Registration succeeds only after every layer agrees that the mapping can remain valid.

---

## 8. Why GPU memory needs a broker

For GPU memory, the ordinary path breaks at its first assumption.

`ib_umem_get()` knows how to resolve CPU virtual addresses backed by Linux-managed pages. A CUDA device pointer is backed by GPU framebuffer pages managed by the NVIDIA driver and GPU MMU. Linux RDMA core cannot call the normal GUP path and obtain an array of ordinary host `struct page` objects for VRAM.

The two drivers therefore need a memory-export protocol.

The legacy protocol on NVIDIA/ConnectX systems was `nvidia-peermem`.

---

## 9. Legacy GPUDirect registration with `nvidia-peermem`

`nvidia-peermem` registers as an RDMA peer-memory client. When the RDMA stack receives a userspace range, the peer-memory layer asks registered clients whether one of them owns it.

```text
RDMA registration request
        |
        v
peer-memory clients
        |
        +-- nvidia-peermem: "is this an NVIDIA GPU range?"
```

For an NVIDIA CUDA allocation, the public R525 module follows a path like this:

```text
CUDA virtual range
    |
    | nvidia_p2p_get_pages()
    v
NVIDIA P2P page table
    |
    | nvidia_p2p_dma_map_pages(RNIC PCI device)
    v
RNIC-valid peer DMA addresses
    |
    v
scatter/gather table
    |
    v
mlx5 MKey translations
```

### 9.1 Claim the range

`nvidia-peermem` aligns the CUDA virtual range to NVIDIA’s peer-page granularity and invokes `nvidia_p2p_get_pages()`.

This call does more than “look up physical addresses.” It asks the NVIDIA driver to establish and hold a peer mapping for the GPU allocation and return a P2P page table describing it. The allocation must remain valid while a third-party device can access it.

The driver also has an invalidation story: if the CUDA allocation is freed or its mapping can no longer remain valid, the peer client must be notified or use a persistent-lifetime API with an explicit teardown contract.

### 9.2 Map for this RNIC

`nvidia_p2p_dma_map_pages()` takes the requesting PCI device—the ConnectX RNIC—and maps the GPU pages into addresses usable by that device.

That device argument is load-bearing. The correct output is not a universal “GPU physical address.” It is a DMA mapping for a particular peer.

### 9.3 Hand the mappings to mlx5

`nvidia-peermem` converts the returned DMA addresses into a scatter/gather table. The RDMA driver can then build its MKey just as it would for any other DMA-mapped memory.

At the end, the RNIC holds translations that lead to PCIe addresses in the GPU’s peer-visible aperture.

### CPU memory through the same probe

If the address is ordinary CPU memory, the NVIDIA peer client should decline ownership and the RDMA stack should continue through the normal `ib_umem_get()` path. In the public module, the ownership callback’s contract is effectively “one means mine, zero means not mine.”

That detail matters in debugging: seeing an NVIDIA peer-memory function reject an address does not automatically mean the MR failed. It may be the expected classification step before host registration.

---

## 10. DMA-BUF replaces the private ownership exchange

The newer GPU-registration path uses Linux DMA-BUF.

That path is gated by the whole software stack, not by the NVIDIA branch number alone. NVIDIA documents DMA-BUF GPUDirect RDMA as requiring the open kernel-module flavor, CUDA 11.7 or newer, Linux 5.12 or newer, and compatible network-driver support. NCCL 2.17 then probes support at runtime: the network plugin must expose `regMrDmaBuf`, the CUDA driver and GPU must report DMA-BUF capability, and the selected network device must advertise `NCCL_PTR_DMABUF`. If any gate fails, installing R535 does not by itself switch the buffer to DMA-BUF registration.

DMA-BUF is a kernel framework for sharing a memory object between drivers. One driver exports the object as a file descriptor. Another driver imports it, attaches its device, and asks for a DMA mapping.

For GPUDirect RDMA:

```text
NVIDIA driver                          mlx5 RDMA driver

exports GPU allocation                imports DMA-BUF fd
as DMA-BUF fd             --------->  attaches RNIC
                                      maps attachment
                                      receives SG table
```

At userspace, NCCL can obtain a DMA-BUF file descriptor for a supported CUDA allocation and call:

```c
ibv_reg_dmabuf_mr(pd, offset, length, iova, fd, access)
```

The kernel path is conceptually:

```text
ibv_reg_dmabuf_mr()
    |
    v
mlx5_ib_reg_user_mr_dmabuf()
    |
    v
ib_umem_dmabuf_get()
    |
    +-- dma_buf_get(fd)
    +-- dma_buf_dynamic_attach(RNIC)
    +-- dma_buf_pin()
    +-- dma_buf_map_attachment()
    |
    v
exporter-provided DMA SG table
    |
    v
mlx5 MKey
```

The NVIDIA driver remains responsible for the GPU allocation and its peer mapping. DMA-BUF does not remove the GPU driver; it replaces the special peer-memory-client handshake with a standard exporter/importer lifetime model.

That is a major architectural improvement:

```text
legacy:
RDMA subsystem <-> out-of-tree/private peer-memory client <-> NVIDIA driver

DMA-BUF:
RDMA importer <-> standard DMA-BUF framework <-> NVIDIA exporter
```

It also gives the kernel a standard place for attachment, reservation fences, invalidation, and unmapping.

### What DMA-BUF does not change

DMA-BUF only applies to buffers that are exported through it. In stock NCCL 2.17, CUDA protocol buffers may take the DMA-BUF path, while host buffers continue through ordinary `ibv_reg_mr_iova2()`.

This distinction becomes the hinge of Part 2.

---

## 11. One RDMA write, all the way to VRAM

We can finally trace one remote write without skipping a layer.

Assume machine B has registered a GPU receive buffer and sent its address and `rkey` to machine A.

### Step 1: A posts a work request

A’s software creates a work queue entry:

```text
local source:
    address + length + lkey

remote destination:
    address + rkey

operation:
    RDMA WRITE
```

The `lkey` authorizes A’s RNIC to read the local source. The `rkey` will authorize B’s RNIC to write the remote destination.

### Step 2: A’s RNIC reads the source

A’s RNIC resolves the local `lkey`, translates the local address, and DMA-reads the payload from A’s memory—possibly GPU memory on the sending side as well.

### Step 3: packets cross the network

The RDMA transport carries the operation, destination virtual address, key, and payload according to the wire protocol. Large writes are segmented across packets.

### Step 4: B’s RNIC validates the capability

B’s RNIC looks up the `rkey` and checks:

```text
Does this key exist?
Does it permit remote write?
Is the requested range within the MR?
Is the queue pair allowed to use it?
```

A random address and guessed key should not be enough.

### Step 5: the RNIC translates the MR address

The remote address must fall inside the MR’s IOVA range. The RNIC subtracts the MR base, applies the page geometry, and uses the MKey’s translation state to obtain one or more DMA addresses.

```text
remote VA / MR IOVA
        |
        | MKey lookup
        | bounds + permissions
        v
MTT/PAS entry
        |
        v
RNIC DMA address
```

### Step 6: the RNIC issues PCIe writes

For a GPU MR, the DMA address targets the peer-visible GPU aperture. The RNIC becomes a PCIe requester and emits memory-write transactions. At the PCIe protocol layer, those requests travel as **Transaction Layer Packets (TLPs)**.

<figure class="frame diagram">
  <span class="frame-title">fig. 6 · one remote write, all the way to VRAM</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 340" role="img" aria-label="Diagram: the receive side of a GPUDirect RDMA write. Packets arrive at the ConnectX-7 RNIC, which validates the rkey and translates the MR address through MKey and MTT state into a DMA address. The RNIC then emits PCIe Memory Write TLPs that are routed through the PCIe switch or root complex into the GPU BAR1 address window, where the GPU's aperture translation steers them into framebuffer pages. No CPU and no driver appears in this fast path.">
      <defs>
        <marker id="p1f6a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--seg)"/>
        </marker>
        <marker id="p1f6b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ldr)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="60" y="36" width="240" height="66" fill="var(--seg)" opacity="0.14"/>
        <rect x="60" y="36" width="240" height="66" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="180" y="58" text-anchor="middle" fill="var(--seg)">ConnectX-7 RNIC</text>
        <text x="180" y="76" text-anchor="middle" font-size="10" fill="var(--muted)">packets in: raddr + rkey + payload</text>
        <text x="180" y="91" text-anchor="middle" font-size="10" fill="var(--muted)">rkey -> MKey -> MTT/PAS -> DMA addr</text>
        <rect x="60" y="150" width="240" height="44" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="180" y="169" text-anchor="middle" fill="var(--text)">PCIe switch / root complex</text>
        <text x="180" y="185" text-anchor="middle" font-size="10" fill="var(--muted)">routes by address, fig. 2's map</text>
        <rect x="430" y="150" width="230" height="44" fill="var(--ldr)" opacity="0.10"/>
        <rect x="430" y="150" width="230" height="44" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="545" y="169" text-anchor="middle" fill="var(--ldr)">GPU BAR1 address window</text>
        <text x="545" y="185" text-anchor="middle" font-size="10" fill="var(--muted)">fig. 3's aperture</text>
        <rect x="430" y="252" width="230" height="48" fill="var(--ldr)" opacity="0.18"/>
        <rect x="430" y="252" width="230" height="48" fill="none" stroke="var(--ldr)" stroke-width="1.8"/>
        <text x="545" y="272" text-anchor="middle" fill="var(--ldr)">framebuffer pages</text>
        <text x="545" y="289" text-anchor="middle" font-size="10" fill="var(--muted)">the registered receive buffer</text>
      </g>
      <g stroke="var(--seg)" stroke-width="1.6" fill="none" marker-end="url(#p1f6a)">
        <path d="M 180 102 L 180 146"/>
        <path d="M 300 172 L 426 172"/>
      </g>
      <g stroke="var(--ldr)" stroke-width="1.6" fill="none" marker-end="url(#p1f6b)">
        <path d="M 545 194 L 545 248"/>
      </g>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)">
        <text x="190" y="128">PCIe Memory Write TLPs</text>
        <text x="363" y="164" text-anchor="middle">posted writes</text>
        <text x="556" y="224">GPU aperture translation</text>
      </g>
      <text x="360" y="326" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">all of it was decided in advance: the route by the BARs, the translation by the MKey.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>RNIC fast path</span>
      <span><span class="k" style="background:var(--ldr)"></span>GPU aperture and memory</span>
    </p>
  </div>
</figure>

### Step 7: the GPU consumes the data

Transport completion and GPU visibility are related but not identical. PCIe posted writes, GPU cache/coherency rules, and CUDA synchronization determine when a GPU kernel may safely consume the new bytes. NCCL and CUDA contain explicit ordering mechanisms for this boundary.

The important negative statement is:

> The CPU does not ask the NVIDIA driver to translate every arriving packet.

The drivers did their work during registration. The fast path uses the RNIC’s MKey and the PCIe mappings already established.

---

## 12. Why BAR1 was such a plausible suspect

When a GPUDirect registration fails, BAR1 is a reasonable hypothesis.

Registration can consume BAR1 mapping space. Small-BAR GPUs can exhaust the aperture. Firmware can misassign a large BAR. A topology may not support peer routing. An IOMMU configuration may block the intended P2P mapping. NVIDIA’s own documentation recommends checking `nvidia-smi -q` and platform large-BAR support.

But BAR1 is only one resource in a long transaction:

```text
userspace range
    -> allocation owner identified
    -> lifetime stabilized
    -> pages resolved
    -> peer mapping created
    -> DMA addresses produced
    -> SG table built
    -> MKey allocated
    -> translations loaded
```

`ENOMEM` can escape from many of those steps:

```text
locked-memory accounting
kernel allocation failure
page-pinning failure
page migration failure
DMA mapping failure
BAR aperture exhaustion
MKey/cache exhaustion
provider bookkeeping failure
```

The top-level NCCL line does not preserve which one happened.

That was the first problem in the production incident: the error named the API boundary, not the failed contract.

---

## 13. The machine model to carry forward

The whole post compresses into three registrations.

### Ordinary host memory

```text
process VA
  -> pin Linux pages long-term
  -> DMA-map for RNIC
  -> load MKey translations
```

### GPU memory through `nvidia-peermem`

```text
CUDA VA
  -> NVIDIA P2P page table
  -> map GPU pages for RNIC
  -> load MKey translations
```

### GPU memory through DMA-BUF

```text
CUDA allocation
  -> NVIDIA exports DMA-BUF
  -> mlx5 attaches and maps
  -> load MKey translations
```

All three end in the same fast-path promise:

> Given this key and an address in this region, the RNIC can reach stable DMA destinations without consulting a process page table.

Part 2 begins where that model misled us. The failing NCCL connection used GPUDirect RDMA, the logs mentioned memory registration, and the fleet had already spent time investigating GPU mappings.

The page that finally explained the failure was in host RAM.

---

## Source map

The mechanism above is grounded in:

- NVIDIA, [GPUDirect RDMA documentation](https://docs.nvidia.com/cuda/gpudirect-rdma/): PCI BARs, BAR mappings, `nvidia_p2p_get_pages()`, device-specific DMA mapping, synchronization, and `nvidia-peermem`.
- NVIDIA NVML, [`nvmlDeviceGetBAR1MemoryInfo`](https://docs.nvidia.com/deploy/nvml-api/group__nvmlDeviceQueries.html): BAR1 as the framebuffer mapping used by CPUs and third-party PCIe devices.
- rdma-core, [`ibv_reg_mr(3)`](https://github.com/linux-rdma/rdma-core/blob/master/libibverbs/man/ibv_reg_mr.3): MR APIs, access flags, IOVA, `lkey`, and `rkey`.
- Linux v6.6, [`drivers/infiniband/core/umem.c`](https://github.com/torvalds/linux/blob/v6.6/drivers/infiniband/core/umem.c): long-term page pinning, SG construction, and DMA mapping for ordinary userspace MRs.
- Linux v6.6, [`drivers/infiniband/hw/mlx5/mr.c`](https://github.com/torvalds/linux/blob/v6.6/drivers/infiniband/hw/mlx5/mr.c): mlx5 user-MR and DMA-BUF-MR creation.
- NCCL 2.17.1, [`src/transport/net_ib.cc`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/transport/net_ib.cc): `ibv_reg_mr_iova2()` and `ibv_reg_dmabuf_mr()` selection in the IB plugin.
- NCCL 2.17.1, [`src/init.cc`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/init.cc) and [`src/transport/net.cc`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/transport/net.cc): DMA-BUF capability probing and per-network-device pointer-support checks.
- NVIDIA GPU Operator, [GPUDirect RDMA prerequisites](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html): open kernel modules, CUDA, kernel, GPU, and network-driver requirements for the DMA-BUF path.
- NVIDIA R525, [`nvidia-peermem.c`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/525.105.17/kernel-open/nvidia-peermem/nvidia-peermem.c): peer ownership, P2P page acquisition, and RNIC-specific DMA mapping.
- Linux, [DMA-BUF documentation](https://docs.kernel.org/driver-api/dma-buf.html): exporter/importer attachment and mapping model.

