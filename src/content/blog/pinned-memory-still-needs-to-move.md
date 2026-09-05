---
title: "howtf can pinned memory still need to move?"
description: "The Linux memory-management mechanism underneath the incident: pages, pageblocks, CMA, FOLL_PIN, FOLL_LONGTERM, migration, and the second pin that returned ENOMEM. Part 3 of Memory Registration, All the Way Down."
date: 2026-08-25
updated: 2026-09-05
series:
  name: "Memory Registration, All the Way Down"
  part: 3
tags: [linux, memory, rdma, cma]
draft: false
---

> **Memory Registration, All the Way Down, Part 3.** [Part 1](/blog/nic-writes-directly-into-gpu-memory/) builds the RNIC translation path. [Part 2](/blog/gpu-registration-failure-from-host-ram/) follows the production failure to a host page in `MIGRATE_CMA`.

By the end of Part 2, one fact still sounds contradictory:

```text
CUDA had already pinned the host page.
RDMA then tried to pin the same page.
The second pin failed because Linux needed to move it.
```

The two pinning calls asked for different things. CUDA held the current page in place. RDMA requested a long-lived DMA mapping, which required Linux to check whether the page could safely stay in its current location.

The page was in CMA, where ordinary allocations must remain movable. Linux needed to move it before accepting the long-term pin, but the earlier CUDA pin prevented that. We’ll follow those checks from the page’s physical placement through to `ENOMEM`.

> **Scope note.** The source walk uses Linux 6.x and public NVIDIA open-kernel-module releases. Helper names have changed across kernel versions, but the invariant is stable: long-term DMA pins cannot strand ordinary pages in memory that must remain migratable. The production kernel had internal patches, so reconstructed traces are illustrative rather than original logs.

---

## 1. A virtual buffer is not a physical neighborhood

A process allocates a 16 KiB buffer:

```text
virtual address range

0x7f00_0000  +-------------------+
             | page 0            |
0x7f00_1000  +-------------------+
             | page 1            |
0x7f00_2000  +-------------------+
             | page 2            |
0x7f00_3000  +-------------------+
             | page 3            |
             +-------------------+
```

The virtual pages are contiguous. Their physical backing does not have to be:

```text
virtual page 0 -> physical frame 91
virtual page 1 -> physical frame 8002
virtual page 2 -> physical frame 92
virtual page 3 -> physical frame 410
```

On a typical x86-64 Linux system, the base page size is 4 KiB. Linux identifies a physical base page by a **page frame number**, or PFN, and usually represents it with a `struct page`. A **folio** is a newer Linux abstraction for one or more physically contiguous base pages managed as one unit.

The CPU’s page table connects virtual pages to PFNs. A device using a registered MR has a separate translation, built at registration time.

```text
CPU translation                      device translation

process VA                           MR IOVA + key
    |                                    |
CPU page table                           | MKey / MTT
    v                                    v
physical page P  <------------------- DMA address for P
```

If Linux replaces physical page `P` with page `Q`, it can update the CPU page table. It cannot silently update every third-party device translation unless the device subsystem participates.

That is why a device pin is more consequential than keeping a page in RAM. It stabilizes an address relationship outside the CPU MMU.

---

## 2. Zones and pageblocks are different layers of policy

Linux groups physical memory at several scales.

### Zones

A **zone** is a large allocator domain based on addressing or migration constraints: `ZONE_DMA`, `ZONE_DMA32`, `ZONE_NORMAL`, `ZONE_MOVABLE`, and others depending on architecture.

A zone answers questions such as:

```text
Can this device address the memory?
Can unmovable kernel allocations live here?
Is this region intended primarily for movable pages?
```

### Pageblocks

Inside a zone, Linux divides memory into **pageblocks**. On common 4 KiB-page x86 systems, a pageblock is usually 2 MiB, though the exact size is architecture/configuration dependent.

<figure class="frame diagram">
  <span class="frame-title">fig. 1 · a zone is divided into pageblocks, and pageblocks carry policy</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 240" role="img" aria-label="Diagram: ZONE_NORMAL drawn as a horizontal strip of pageblocks, each usually two megabytes on x86. Each pageblock has a migratetype: some are MIGRATE_UNMOVABLE, some MIGRATE_MOVABLE, one is MIGRATE_CMA and highlighted. A pageblock's migratetype describes the kind of allocation it should serve; it is allocator policy, not a property burned into each page.">
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="30" y="50" width="660" height="86" fill="none" stroke="var(--muted)" stroke-width="1.4"/>
        <text x="44" y="42" fill="var(--muted)">ZONE_NORMAL</text>
        <rect x="46" y="66" width="120" height="54" fill="var(--muted)" opacity="0.12"/>
        <rect x="46" y="66" width="120" height="54" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="106" y="88" text-anchor="middle" fill="var(--text)" font-size="10">pageblock</text>
        <text x="106" y="105" text-anchor="middle" fill="var(--muted)" font-size="10">UNMOVABLE</text>
        <rect x="174" y="66" width="120" height="54" fill="var(--sec)" opacity="0.10"/>
        <rect x="174" y="66" width="120" height="54" fill="none" stroke="var(--sec)" stroke-width="1.2"/>
        <text x="234" y="88" text-anchor="middle" fill="var(--text)" font-size="10">pageblock</text>
        <text x="234" y="105" text-anchor="middle" fill="var(--sec)" font-size="10">MOVABLE</text>
        <rect x="302" y="66" width="120" height="54" fill="var(--sec)" opacity="0.10"/>
        <rect x="302" y="66" width="120" height="54" fill="none" stroke="var(--sec)" stroke-width="1.2"/>
        <text x="362" y="88" text-anchor="middle" fill="var(--text)" font-size="10">pageblock</text>
        <text x="362" y="105" text-anchor="middle" fill="var(--sec)" font-size="10">MOVABLE</text>
        <rect x="430" y="66" width="120" height="54" fill="var(--accent)" opacity="0.14"/>
        <rect x="430" y="66" width="120" height="54" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="490" y="88" text-anchor="middle" fill="var(--text)" font-size="10">pageblock</text>
        <text x="490" y="105" text-anchor="middle" fill="var(--accent)" font-size="10">MIGRATE_CMA</text>
        <rect x="558" y="66" width="120" height="54" fill="var(--muted)" opacity="0.06"/>
        <rect x="558" y="66" width="120" height="54" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="618" y="88" text-anchor="middle" fill="var(--text)" font-size="10">pageblock</text>
        <text x="618" y="105" text-anchor="middle" fill="var(--muted)" font-size="10">RECLAIMABLE</text>
        <text x="360" y="162" text-anchor="middle" font-size="10" fill="var(--muted)">usually 2 MiB each on 4 KiB-page x86 · migratetype = get_pageblock_migratetype(page)</text>
      </g>
      <text x="360" y="204" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">"the page was migrate CMA" means: its PFN fell inside a pageblock with this policy.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>movable allocations expected</span>
      <span><span class="k" style="background:var(--accent)"></span>CMA reserve: occupants must stay evictable</span>
    </p>
  </div>
</figure>

Each pageblock has a migratetype describing the kind of allocation it should serve:

```text
MIGRATE_UNMOVABLE
MIGRATE_RECLAIMABLE
MIGRATE_MOVABLE
MIGRATE_HIGHATOMIC
MIGRATE_CMA
MIGRATE_ISOLATE
```

The migratetype describes the allocator’s policy for a pageblock.

A page from a `MIGRATE_MOVABLE` block is expected to be movable. A page from a `MIGRATE_CMA` block occupies physical space reserved for the Contiguous Memory Allocator and must remain removable when CMA needs the range back.

The phrase from the incident—“the page was migrate CMA”—means more precisely:

> The page’s PFN fell inside a pageblock whose migratetype was `MIGRATE_CMA`.

That distinction matters when instrumenting the kernel. The relevant question is often `get_pageblock_migratetype(page)`, not a single `PageCma` flag on the object.

---

## 3. Why physically contiguous memory is hard to allocate late

Linux’s **HugeTLB** subsystem manages explicitly reserved huge pages. “TLB” refers to the CPU’s **Translation Lookaside Buffer**, a cache of recent virtual-to-physical translations. Mapping a large region with a huge page reduces page-table entries and TLB pressure compared with mapping the same bytes as thousands of 4 KiB pages. HugeTLB is distinct from Transparent Huge Pages: applications reserve or request its pages explicitly.

A 1 GiB HugeTLB page requires 1 GiB of contiguous physical address space. With 4 KiB base pages, that is 262,144 adjacent frames.

At boot, finding such a range is easy. After the machine has run for hours, physical memory looks more like this:

```text
physical frames

[free][anon][free][page cache][kernel][free][anon][pinned][free]...
```

The machine may have many gigabytes free in total and still have no free 1 GiB run.

This is **external fragmentation**: sufficient capacity, insufficient contiguity.

Linux can compact memory by moving movable pages together and freeing larger extents. But some pages cannot move:

```text
kernel allocations with embedded physical addresses
long-term DMA pins
some device mappings
hardware-reserved pages
```

One immovable 4 KiB page can spoil an otherwise free 1 GiB candidate.

That is why gigantic pages are commonly reserved at boot or backed by a dedicated CMA area.

---

## 4. What `hugetlb_cma=6G` creates

The **Contiguous Memory Allocator (CMA)** reserves physical ranges that can be evacuated and handed to users needing large contiguous allocations. The boot parameter from the incident was approximately:

```text
hugetlb_cma=6G
```

Linux documents this as a CMA area used to allocate **gigantic HugeTLB pages**. On x86-64, the relevant gigantic page size is normally 1 GiB. The six-gigabyte size is a multiple of that unit.

At boot, Linux carves out physical ranges and marks their pageblocks for CMA use. A global size is apportioned across online NUMA nodes unless the command line specifies node-specific sizes. On a two-node host, `hugetlb_cma=6G` normally means up to roughly three GiB per node, subject to alignment and successful reservation—not one universal six-gigabyte interval.

```text
physical memory, two-node example

NUMA node 0                         NUMA node 1
+-----------------------------+     +-----------------------------+
| ordinary memory             |     | ordinary memory             |
+-----------------------------+     +-----------------------------+
| ~3 GiB MIGRATE_CMA extents  |     | ~3 GiB MIGRATE_CMA extents  |
+-----------------------------+     +-----------------------------+
```

The reservation can provide up to six 1 GiB huge pages across the nodes, but it does not necessarily create those pages at boot. The per-node split also affects whether a later allocation lands in CMA.

The kernel can later ask CMA for a 1 GiB contiguous extent. Until then, leaving six GiB idle would waste memory, so CMA allows ordinary **movable** pages to occupy it temporarily.

```text
CMA reserve while no huge page is requested

+--------------------------------------------------+
| anon | page cache | free | anon | free | ...     |
| all temporary occupants must remain movable      |
+--------------------------------------------------+
```

When a huge-page allocation arrives, CMA isolates the target range, migrates temporary occupants elsewhere, and returns the now-contiguous physical memory.

<figure class="frame diagram">
  <span class="frame-title">fig. 2 · evacuating the reserve for one gigantic page</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 300" role="img" aria-label="Diagram: before evacuation, a CMA range holds temporary movable occupants A, B, C, and D interleaved with free space. When a HugeTLB request arrives, the kernel migrates A, B, C, and D out to ordinary memory. After evacuation, the CMA range is entirely free and one contiguous physical extent is returned to the requester.">
      <defs>
        <marker id="p3f2a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--sec)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="40" y="38" fill="var(--muted)">before: lent-out reserve</text>
        <rect x="40" y="48" width="440" height="46" fill="var(--accent)" opacity="0.06"/>
        <rect x="40" y="48" width="440" height="46" fill="none" stroke="var(--accent)" stroke-width="1.5"/>
        <rect x="52" y="58" width="64" height="26" fill="var(--sec)" opacity="0.30"/>
        <text x="84" y="75" text-anchor="middle" fill="var(--text)" font-size="10">A</text>
        <rect x="124" y="58" width="64" height="26" fill="var(--sec)" opacity="0.30"/>
        <text x="156" y="75" text-anchor="middle" fill="var(--text)" font-size="10">B</text>
        <text x="228" y="75" text-anchor="middle" fill="var(--muted)" font-size="10">free</text>
        <rect x="268" y="58" width="64" height="26" fill="var(--sec)" opacity="0.30"/>
        <text x="300" y="75" text-anchor="middle" fill="var(--text)" font-size="10">C</text>
        <text x="372" y="75" text-anchor="middle" fill="var(--muted)" font-size="10">free</text>
        <rect x="408" y="58" width="64" height="26" fill="var(--sec)" opacity="0.30"/>
        <text x="440" y="75" text-anchor="middle" fill="var(--text)" font-size="10">D</text>
        <rect x="560" y="48" width="130" height="46" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="625" y="66" text-anchor="middle" fill="var(--muted)" font-size="10">ordinary</text>
        <text x="625" y="81" text-anchor="middle" fill="var(--muted)" font-size="10">memory</text>
        <text x="40" y="182" fill="var(--muted)">after: the promise is kept</text>
        <rect x="40" y="192" width="440" height="46" fill="var(--accent)" opacity="0.14"/>
        <rect x="40" y="192" width="440" height="46" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="260" y="220" text-anchor="middle" fill="var(--accent)">one contiguous physical extent · 1 GiB</text>
      </g>
      <g stroke="var(--sec)" stroke-width="1.3" fill="none" marker-end="url(#p3f2a)">
        <path d="M 84 84 C 84 130, 400 120, 556 74"/>
        <path d="M 156 84 C 156 136, 420 126, 556 80"/>
        <path d="M 300 84 C 300 140, 440 132, 556 86"/>
        <path d="M 440 84 C 440 130, 500 116, 556 92"/>
      </g>
      <text x="360" y="152" text-anchor="middle" font-family="var(--font-display)" font-size="10" fill="var(--muted)">migrate A, B, C, D to ordinary pages</text>
      <text x="360" y="278" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">this same migration, attempted later on one pinned page, is the whole incident.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--accent)"></span>CMA reserve</span>
      <span><span class="k" style="background:var(--sec)"></span>temporary movable occupants</span>
    </p>
  </div>
</figure>

CMA can reclaim the range only while its temporary occupants remain movable.

---

## 5. Why the kernel consumed CMA first

Ordinary movable allocations can use either regular free memory or CMA free memory. Unmovable allocations usually cannot use CMA, because they would defeat its purpose. Stock Linux already has a conditional CMA fallback: in v6.6, eligible movable allocations may draw from CMA when CMA accounts for more than half of the zone's free pages. The fleet's CMA-first policy made that choice earlier and more frequent; it was an amplifier, not a prerequisite for the mechanism.

Suppose a machine has:

```text
ordinary free memory:  1 GiB
CMA free memory:       5 GiB
```

If movable anonymous pages consume ordinary memory first, an unmovable allocation may later fail with five GiB still free in CMA. The unmovable page cannot go there, and Linux has no general mechanism to move existing anonymous pages *into* CMA to free ordinary space.

The CMA-first policy solves that local problem:

```text
movable allocation arrives
    |
    +-- take CMA page first
    |
    +-- preserve ordinary memory for less flexible users
```

The public patch matching the incident says exactly that: movable pages can be migrated **out** of CMA later, so consume CMA first and preserve the memory that unmovable allocations need. The public proposal is evidence for the policy and rationale, not proof that its exact diff was the internal fleet change.

The assumption underneath the optimization is:

```text
anything allocated from CMA remains movable
```

A normal anonymous page satisfies that assumption—until a device pins it.

---

## 6. “Resident,” “locked,” and “pinned” are not synonyms

Linux has several ways to make memory less movable or less reclaimable.

### Faulted-in / resident

A resident page currently has physical backing. It may still be reclaimed, swapped, migrated, or replaced later.

### `mlock()`ed

`mlock()` asks Linux not to page the range out. It is a userspace residency policy. It is not the same device-DMA contract as GUP pinning, and mlocked pages can participate in some migration paths.

### GUP reference

Historically, device drivers used `get_user_pages()`—GUP—to obtain references to userspace pages. A reference keeps the page object alive, but Linux struggled to distinguish short software access from device DMA pins.

### `FOLL_PIN`

Modern drivers use `pin_user_pages*()`. Those wrappers set `FOLL_PIN` and account the page as DMA-pinned. For base pages, Linux uses a biased reference count; for large folios it may maintain a dedicated pin count.

This lets the VM recognize that the page is participating in a device mapping and requires special treatment.

### `FOLL_LONGTERM`

`FOLL_LONGTERM` adds a lifetime and placement declaration:

> This pin may persist long enough that normal VM operations cannot treat it as a brief interruption.

`FOLL_LONGTERM` describes the intended lifetime of a mapping, such as a classic RDMA MR. The API does not define a duration threshold in seconds.

`FOLL_LONGTERM` implies that Linux must reject or relocate pages whose location cannot safely be stranded for that lifetime.

The diagram compares these forms of residency and pinning:

<figure class="frame diagram">
  <span class="frame-title">fig. 3 · "pinned" is a ladder of contracts, not a Boolean</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 330" role="img" aria-label="Diagram: five rungs of increasing memory-stability contracts. Resident means the page currently has physical backing. mlock means the range is not paged out, a userspace residency policy. A GUP reference keeps the page object alive. FOLL_PIN declares device DMA and is accounted as a pin. FOLL_PIN plus FOLL_LONGTERM, the strongest rung, additionally validates that the page's location can be stranded for a long-lived DMA mapping. Only the top rung checks placement.">
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="60" y="252" width="380" height="34" fill="none" stroke="var(--muted)" stroke-width="1"/>
        <text x="74" y="273" fill="var(--text)">resident</text>
        <text x="430" y="273" text-anchor="end" fill="var(--muted)" font-size="10">has physical backing right now</text>
        <rect x="90" y="200" width="380" height="34" fill="none" stroke="var(--muted)" stroke-width="1.1"/>
        <text x="104" y="221" fill="var(--text)">mlock()ed</text>
        <text x="460" y="221" text-anchor="end" fill="var(--muted)" font-size="10">not paged out · may still migrate</text>
        <rect x="120" y="148" width="380" height="34" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="134" y="169" fill="var(--text)">GUP reference</text>
        <text x="490" y="169" text-anchor="end" fill="var(--muted)" font-size="10">page object stays alive</text>
        <rect x="150" y="96" width="380" height="34" fill="var(--krn)" opacity="0.12"/>
        <rect x="150" y="96" width="380" height="34" fill="none" stroke="var(--krn)" stroke-width="1.4"/>
        <text x="164" y="117" fill="var(--krn)">FOLL_PIN</text>
        <text x="520" y="117" text-anchor="end" fill="var(--muted)" font-size="10">accounted device-DMA pin · page stays put</text>
        <rect x="180" y="44" width="380" height="34" fill="var(--accent)" opacity="0.14"/>
        <rect x="180" y="44" width="380" height="34" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="194" y="65" fill="var(--accent)">FOLL_PIN + FOLL_LONGTERM</text>
        <text x="550" y="65" text-anchor="end" fill="var(--muted)" font-size="10">+ placement validated</text>
        <path d="M 596 262 L 596 62" stroke="var(--border)" stroke-width="1.2" fill="none"/>
        <path d="M 590 74 L 596 60 L 602 74" stroke="var(--border)" stroke-width="1.2" fill="none"/>
        <text x="608" y="150" fill="var(--muted)" font-size="10">stronger</text>
        <text x="608" y="165" fill="var(--muted)" font-size="10">contract</text>
      </g>
      <text x="360" y="316" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">only the top rung asks whether the page may stay put. CUDA's pin stopped one rung short.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--krn)"></span>DMA pin</span>
      <span><span class="k" style="background:var(--accent)"></span>DMA pin with placement validation</span>
    </p>
  </div>
</figure>

A long-term pin also requires Linux to validate the page’s placement.

---

## 7. Why classic pinned RDMA MRs declare the long-term contract

A classic RDMA memory region can live for minutes, hours, or the lifetime of a process. The RNIC may issue DMA whenever a work request or remote packet references its key.

Linux RDMA core therefore begins ordinary userspace MR registration with:

```text
FOLL_LONGTERM
```

and adds `FOLL_WRITE` if the device can write the pages.

The public `ib_umem_get()` path is conceptually:

```text
check locked-memory allowance
allocate ib_umem
pin_user_pages_fast(FOLL_LONGTERM | maybe FOLL_WRITE)
build SG table
DMA-map SG table for RNIC
```

The long-term flag tells GUP that simply finding a present page is insufficient. The page’s *kind and location* must support a long-lived DMA pin.

CMA pages do not, in place.

---

## 8. Why a long-term pin cannot stay inside CMA

CMA needs to reclaim contiguous physical ranges by moving their occupants. A long-term DMA pin requires a stable physical destination for the device. Those requirements conflict when they apply to the same page:

```text
CMA promise:
    "I can move this page when I need the physical range."

long-term DMA promise:
    "This page must stay at this physical DMA destination."
```

They cannot both hold indefinitely.

Linux resolves the conflict by moving the page **before** accepting the long-term pin.

<figure class="frame diagram">
  <span class="frame-title">fig. 4 · the repair path: move first, then pin</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 300" role="img" aria-label="Diagram: page P sits inside a CMA pageblock. A long-term pin is requested. Linux allocates page Q in ordinary memory, copies P's contents to Q, replaces the CPU mappings so the virtual address now points at Q, frees P back to the CMA reserve, and takes the long-term pin on Q. The CMA promise and the DMA promise end up on different pages.">
      <defs>
        <marker id="p3f4a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--krn)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="40" y="60" width="250" height="150" fill="var(--accent)" opacity="0.06"/>
        <rect x="40" y="60" width="250" height="150" fill="none" stroke="var(--accent)" stroke-width="1.5"/>
        <text x="54" y="80" fill="var(--accent)">MIGRATE_CMA pageblock</text>
        <rect x="90" y="110" width="150" height="52" fill="var(--sec)" opacity="0.25"/>
        <rect x="90" y="110" width="150" height="52" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="165" y="132" text-anchor="middle" fill="var(--text)">page P</text>
        <text x="165" y="149" text-anchor="middle" font-size="10" fill="var(--muted)">movable occupant</text>
        <rect x="430" y="60" width="250" height="150" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="444" y="80" fill="var(--muted)">ordinary memory</text>
        <rect x="480" y="110" width="150" height="52" fill="var(--krn)" opacity="0.14"/>
        <rect x="480" y="110" width="150" height="52" fill="none" stroke="var(--krn)" stroke-width="1.8"/>
        <text x="555" y="132" text-anchor="middle" fill="var(--krn)">page Q</text>
        <text x="555" y="149" text-anchor="middle" font-size="10" fill="var(--muted)">long-term pinned</text>
      </g>
      <g stroke="var(--krn)" stroke-width="1.6" fill="none" marker-end="url(#p3f4a)">
        <path d="M 240 136 L 476 136"/>
      </g>
      <g font-family="var(--font-display)" font-size="10" fill="var(--muted)">
        <text x="358" y="112" text-anchor="middle">1. allocate Q  ·  2. copy P -> Q</text>
        <text x="358" y="126" text-anchor="middle">3. CPU mappings now point at Q</text>
        <text x="358" y="168" text-anchor="middle">4. free P back to the reserve</text>
        <text x="358" y="182" text-anchor="middle">5. pin Q long-term</text>
      </g>
      <text x="165" y="238" text-anchor="middle" font-family="var(--font-display)" font-size="10" fill="var(--accent)">CMA keeps its movable contract</text>
      <text x="555" y="238" text-anchor="middle" font-family="var(--font-display)" font-size="10" fill="var(--krn)">the RNIC gets a stable DMA target</text>
      <text x="360" y="282" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">both promises hold, because they end up on different pages.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>movable page inside CMA</span>
      <span><span class="k" style="background:var(--krn)"></span>relocated, long-term-pinned page</span>
    </p>
  </div>
</figure>

This is why a direct RDMA registration of an otherwise unpinned CMA page can succeed. The kernel is allowed to relocate it as part of registration.

The incident required an earlier pin.

---

## 9. The first pin: CUDA mapped host memory

The production evidence established a CUDA/NVIDIA pin on CPU-backed memory. The exact userspace operation is no longer recoverable: stock same-process NCCL can allocate mapped host memory directly, while file-backed NCCL paths can register an existing mapping.

The public NVIDIA R525.105.17 and R535.104.05 open-module source gives us an inspectable analogue for the latter case. Its host-page locking function builds GUP flags from writability and then calls `NV_PIN_USER_PAGES()`. Treat this as the source-backed lifetime model, not a recovered production stack.

The observed page placement also tells us which broad NVIDIA sysmem path was involved. Driver-owned NVIDIA pages use `GFP_KERNEL` in the public R535 allocator and are not movable, so they cannot originate from a `MIGRATE_CMA` pageblock. By contrast, the public RM OS-descriptor path imports a userspace virtual range through `RmCreateOsDescriptor()` and `os_lock_user_pages()`. Anonymous userspace faults use `GFP_HIGHUSER_MOVABLE`. A traced `MIGRATE_CMA` backing page is therefore evidence for movable userspace memory imported and pinned by NVIDIA, not for the driver's own unmovable page allocator.

Simplified:

```c
flags = writable ? FOLL_WRITE : 0;
pin_user_pages(address, count, flags, pages);
```

The public path does not add `FOLL_LONGTERM` in those releases.

Therefore, if the host page already belongs to a CMA pageblock, the first pin can follow this path:

```text
NCCL host allocation
    -> Linux supplies a page from MIGRATE_CMA
    -> CUDA/NVIDIA pins it with FOLL_PIN
    -> no long-term placement check
    -> page remains physically inside CMA
```

The page is now immobile in practice while CUDA holds the pin, but it entered that state without passing the placement validation designed for long-lived DMA.

> The first pin stabilized the current page. It did not first establish that the page lived in a location safe for a long-term pin.

---

## 10. The second pin: RDMA asks for placement correctness

Later, NCCL registers the same host range with the ConnectX RNIC:

```text
ibv_reg_mr_iova2()
    -> mlx5_ib_reg_user_mr()
    -> ib_umem_get()
    -> pin_user_pages_fast(FOLL_LONGTERM)
```

Linux now examines the backing page and finds:

```text
pageblock migratetype = MIGRATE_CMA
```

The long-term GUP path cannot leave it there. It collects the unpinnable/movable page, drops the temporary pin it took while inspecting the range, and attempts synchronous migration to an acceptable page.

A simplified control flow is:

```text
try long-term pin
    |
    v
find page not valid for long-term placement
    |
    v
unpin temporary GUP references
    |
    v
migrate page to ordinary memory
    |
    +-- success -> retry pin on new page
    |
    +-- failure -> return error
```

On Linux 6.x, the source contains helpers with names such as `check_and_migrate_movable_pages()` or their folio-oriented successors. The migration reason is `MR_LONGTERM_PIN`.

The migration can repair the placement if CUDA has not already pinned the page. With the earlier pin still held, that repair fails.

---

## 11. Why the earlier pin prevents migration

To migrate page `P` to page `Q`, Linux must know that it controls every relevant mapping and reference to `P`.

The migration operation roughly does this:

```text
1. isolate P from normal VM lists
2. allocate Q
3. freeze / validate references to P
4. copy contents P -> Q
5. replace CPU mappings
6. transfer metadata
7. free P
```

A device pin is an external promise that some DMA translation still points to `P`.

Linux cannot solve that by updating the CPU page tables:

```text
CPU mapping after migration:     VA -> Q
existing device mapping:         DMA -> P
```

If it freed and reused `P`, the device could corrupt an unrelated allocation. If it kept `P`, CMA would not regain the contiguous range. Neither is acceptable.

`FOLL_PIN` accounting makes these hidden device references visible enough for migration to fail rather than silently corrupt data. The extra page references or pin count prevent the migration code from freezing the page in the expected state.

```text
page P expected references: VM mappings + migration reference
page P actual references:   expected + CUDA DMA pin

actual != expected
    -> cannot safely move P
```

The earlier CUDA pin prevented the move that RDMA’s placement check required.

---

## 12. Why the error is `ENOMEM`

The deepest failure is “could not migrate this page into an acceptable location.” Linux reports that from the long-term GUP migration helper as `-ENOMEM` when migration does not complete.

That value propagates:

```text
migrate_pages() does not migrate every page
    -> long-term GUP returns -ENOMEM
    -> ib_umem_get() returns ERR_PTR(-ENOMEM)
    -> mlx5_ib_reg_user_mr() returns error
    -> userspace provider returns NULL
    -> errno = ENOMEM
    -> NCCL prints "Cannot allocate memory"
```

Migration needs a suitable replacement page and a source page that can be moved. Free RAM alone does not make the source movable.

This is one of several meanings hidden behind MR `ENOMEM`:

```text
memory capacity exhausted
memlock quota exceeded
kernel metadata allocation failed
MKey resource unavailable
long-term page placement could not be established  <-- this incident
```

A useful error message would have preserved the last one.

---

## 13. The ordering bug in one page

The entire mechanism fits in two timelines.

### Safe ordering

The first device declares the long-term lifetime:

<figure class="frame diagram">
  <span class="frame-title">fig. 5 · safe ordering: the strongest contract goes first</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 200" role="img" aria-label="Diagram: a left-to-right timeline. Page P is allocated from CMA. CUDA asks for a long-term-aware pin, so Linux migrates P to an ordinary page Q while P is still movable. CUDA pins Q, RDMA registers Q, and the registration succeeds.">
      <defs>
        <marker id="p3f5a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="10">
        <rect x="18" y="60" width="104" height="56" fill="var(--sec)" opacity="0.12"/>
        <rect x="18" y="60" width="104" height="56" fill="none" stroke="var(--sec)" stroke-width="1.3"/>
        <text x="70" y="84" text-anchor="middle" fill="var(--text)">page P</text>
        <text x="70" y="99" text-anchor="middle" fill="var(--muted)">from CMA</text>
        <rect x="134" y="60" width="104" height="56" fill="var(--ldr)" opacity="0.12"/>
        <rect x="134" y="60" width="104" height="56" fill="none" stroke="var(--ldr)" stroke-width="1.8"/>
        <text x="186" y="79" text-anchor="middle" fill="var(--ldr)">CUDA asks</text>
        <text x="186" y="93" text-anchor="middle" fill="var(--ldr)">long-term-</text>
        <text x="186" y="107" text-anchor="middle" fill="var(--ldr)">aware pin</text>
        <rect x="250" y="60" width="104" height="56" fill="var(--krn)" opacity="0.12"/>
        <rect x="250" y="60" width="104" height="56" fill="none" stroke="var(--krn)" stroke-width="1.3"/>
        <text x="302" y="79" text-anchor="middle" fill="var(--krn)">Linux migrates</text>
        <text x="302" y="93" text-anchor="middle" fill="var(--krn)">P -> Q</text>
        <text x="302" y="107" text-anchor="middle" fill="var(--muted)">still movable</text>
        <rect x="366" y="60" width="104" height="56" fill="var(--ldr)" opacity="0.12"/>
        <rect x="366" y="60" width="104" height="56" fill="none" stroke="var(--ldr)" stroke-width="1.3"/>
        <text x="418" y="91" text-anchor="middle" fill="var(--ldr)">CUDA pins Q</text>
        <rect x="482" y="60" width="104" height="56" fill="var(--seg)" opacity="0.12"/>
        <rect x="482" y="60" width="104" height="56" fill="none" stroke="var(--seg)" stroke-width="1.3"/>
        <text x="534" y="84" text-anchor="middle" fill="var(--seg)">RDMA</text>
        <text x="534" y="99" text-anchor="middle" fill="var(--seg)">registers Q</text>
        <rect x="598" y="60" width="104" height="56" fill="var(--accent)" opacity="0.14"/>
        <rect x="598" y="60" width="104" height="56" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="650" y="91" text-anchor="middle" fill="var(--accent)">success</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.3" fill="none" marker-end="url(#p3f5a)">
        <path d="M 122 88 L 130 88"/>
        <path d="M 238 88 L 246 88"/>
        <path d="M 354 88 L 362 88"/>
        <path d="M 470 88 L 478 88"/>
        <path d="M 586 88 L 594 88"/>
      </g>
      <text x="186" y="46" text-anchor="middle" font-family="var(--font-display)" font-size="10" fill="var(--muted)">the declaration arrives first</text>
      <text x="360" y="160" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">the placement check runs while the page can still be moved.</text>
    </svg>
  </div>
</figure>

### Incident ordering

The first device pins without the placement declaration:

<figure class="frame diagram">
  <span class="frame-title">fig. 6 · incident ordering: the weak pin gets there first</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 200" role="img" aria-label="Diagram: the same timeline with one change. Page P is allocated from CMA. CUDA pins P without FOLL_LONGTERM, so no placement check runs and P stays inside CMA. RDMA later asks for a long-term pin, Linux must migrate P out of CMA, but the existing CUDA pin prevents the migration, and the registration fails with ENOMEM.">
      <defs>
        <marker id="p3f6a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="10">
        <rect x="18" y="60" width="104" height="56" fill="var(--sec)" opacity="0.12"/>
        <rect x="18" y="60" width="104" height="56" fill="none" stroke="var(--sec)" stroke-width="1.3"/>
        <text x="70" y="84" text-anchor="middle" fill="var(--text)">page P</text>
        <text x="70" y="99" text-anchor="middle" fill="var(--muted)">from CMA</text>
        <rect x="134" y="60" width="104" height="56" fill="var(--ldr)" opacity="0.12"/>
        <rect x="134" y="60" width="104" height="56" fill="none" stroke="var(--ldr)" stroke-width="1.8" stroke-dasharray="5 3"/>
        <text x="186" y="79" text-anchor="middle" fill="var(--ldr)">CUDA pins P</text>
        <text x="186" y="93" text-anchor="middle" fill="var(--ldr)">without</text>
        <text x="186" y="107" text-anchor="middle" fill="var(--ldr)">FOLL_LONGTERM</text>
        <rect x="250" y="60" width="104" height="56" fill="var(--seg)" opacity="0.12"/>
        <rect x="250" y="60" width="104" height="56" fill="none" stroke="var(--seg)" stroke-width="1.3"/>
        <text x="302" y="79" text-anchor="middle" fill="var(--seg)">RDMA asks for</text>
        <text x="302" y="93" text-anchor="middle" fill="var(--seg)">long-term pin</text>
        <rect x="366" y="60" width="104" height="56" fill="var(--krn)" opacity="0.12"/>
        <rect x="366" y="60" width="104" height="56" fill="none" stroke="var(--krn)" stroke-width="1.3"/>
        <text x="418" y="84" text-anchor="middle" fill="var(--krn)">Linux must</text>
        <text x="418" y="99" text-anchor="middle" fill="var(--krn)">migrate P</text>
        <rect x="482" y="60" width="104" height="56" fill="var(--krn)" opacity="0.12"/>
        <rect x="482" y="60" width="104" height="56" fill="none" stroke="var(--krn)" stroke-width="1.3"/>
        <text x="534" y="79" text-anchor="middle" fill="var(--krn)">CUDA pin</text>
        <text x="534" y="93" text-anchor="middle" fill="var(--krn)">prevents</text>
        <text x="534" y="107" text-anchor="middle" fill="var(--krn)">migration</text>
        <rect x="598" y="60" width="104" height="56" fill="var(--accent)" opacity="0.14"/>
        <rect x="598" y="60" width="104" height="56" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="650" y="91" text-anchor="middle" fill="var(--accent)">-ENOMEM</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.3" fill="none" marker-end="url(#p3f6a)">
        <path d="M 122 88 L 130 88"/>
        <path d="M 238 88 L 246 88"/>
        <path d="M 354 88 L 362 88"/>
        <path d="M 470 88 L 478 88"/>
        <path d="M 586 88 L 594 88"/>
      </g>
      <text x="186" y="46" text-anchor="middle" font-family="var(--font-display)" font-size="10" fill="var(--muted)">no placement check · P stays in CMA</text>
      <text x="360" y="160" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--accent)">same actors as fig. 5, one missing flag at step two. everything after it is forced.</text>
    </svg>
  </div>
</figure>

The failure depends on the order of the pinning calls. They do not have to run simultaneously: once the first pin holds this physical page, the later placement check can fail deterministically.

The apparent randomness comes from whether the allocation landed in CMA and when the two registrations occurred.

---

## 14. The NVIDIA source change that mirrors the mechanism

The public source provides a useful before-and-after.

### R525 and R535

The host-page lock path uses `pin_user_pages` with writability flags, but no `FOLL_LONGTERM`.

```text
R525/R535 public path:
    FOLL_PIN + maybe FOLL_WRITE
```

### R555

The R555.42.02 public source adds `FOLL_LONGTERM` on x86 before calling the same pinning API.

```text
R555 public path:
    FOLL_PIN + FOLL_LONGTERM + maybe FOLL_WRITE
```

The newer code also contains a workaround for kernels where a large long-term GUP request can hit an allocation limit while building VMA metadata, retrying in smaller chunks on `ENOMEM`.

The public commit history does not link this change to the Meta incident. It does show the placement check moving to the first pin:

```text
before:
    pin first, discover incompatible placement later

after:
    declare long-term intent at the first NVIDIA host-page pin
```

If the page is in CMA, the first pin can now trigger migration while the page is still movable.

---

## 15. Why disabling CMA was the right production fix

Several fixes are theoretically possible.

### Make the first pin long-term aware

This addresses the contract at its source. A modern driver path that uses `FOLL_LONGTERM` before establishing the CUDA host mapping should avoid stranding the page in CMA.

But upgrading a fleet driver is not always an immediate or isolated change, and it still leaves a memory policy on nodes that do not need it.

### Force the allocation outside CMA

An allocator could request unmovable memory or use a dedicated pool. That is difficult from userspace and may have broader fragmentation costs. NCCL should not need to know the fleet’s HugeTLB reservation policy to allocate a small host buffer.

### Share one registration/lifetime object

DMA-BUF and other exporter/importer models reduce duplicated, independent lifetime decisions. But stock NCCL’s host LL path was ordinary host memory, not an exported CUDA device allocation.

### Remove the irrelevant reserve

The GPU fleet did not need the six-gigabyte HugeTLB CMA area. Disabling it eliminated the problematic placement and returned the memory to ordinary allocation policy.

```text
hugetlb_cma=0
```

Without that reserve, registration no longer needed to move pages out of CMA:

```text
no CMA page
    -> no need to migrate before long-term pin
    -> first and second pins can agree on the same ordinary page
```

The production result—no recurrence for weeks and months—confirmed that choice.

---

## 16. A hardware-independent reproducer

The core mechanism does not require an H100 or a ConnectX-7. It requires:

```text
1. a userspace page backed by CMA
2. a first GUP pin without FOLL_LONGTERM
3. a second long-term GUP pin
```

A rigorous lab can use a VM, a small kernel module, and Soft-RoCE.

### 16.1 Create CMA memory

Boot with a CMA area. To mirror production closely, use `hugetlb_cma` on a sufficiently large VM; for a smaller lab, a generic `cma=` reserve can exercise the same pageblock property.

There are two ways to make the test page land there. On an unmodified stock kernel, consume enough ordinary free memory that the allocator's conditional CMA fallback engages. Alternatively, apply a CMA-first policy to raise the hit rate and mirror production. The first route is the stronger proof: the two-pin mechanism does not depend on the fleet-specific policy; that policy only made the placement common.

### 16.2 Find a userspace page in `MIGRATE_CMA`

Fault anonymous pages until a probe reports:

```text
get_pageblock_migratetype(page) == MIGRATE_CMA
```

A test module can expose a debug ioctl that resolves a userspace address and prints its PFN and pageblock migratetype. This avoids relying on restricted `/proc/pagemap` access.

### 16.3 Take the first pin

The test module calls:

```text
pin_user_pages(..., no FOLL_LONGTERM)
```

and holds the returned page reference.

### 16.4 Attempt a real RDMA MR

Create an `rdma_rxe` software RDMA device and call `ibv_reg_mr()` on the same range. `rdma_rxe` is Linux’s software implementation of the RDMA over Converged Ethernet (RoCE) verbs interface, so it exercises RDMA core’s memory-registration path without requiring a physical RNIC. The provider still enters RDMA core’s ordinary `ib_umem_get()` path and requests `FOLL_LONGTERM`, even though the final data movement is software-emulated.

Expected result:

```text
ibv_reg_mr() -> NULL
errno        -> ENOMEM
```

### 16.5 Run the causal matrix

```text
Case A
CMA page + no first pin
    -> RDMA migrates page
    -> registration succeeds

Case B
CMA page + first non-long-term pin
    -> migration blocked
    -> registration fails

Case C
CMA page + first FOLL_LONGTERM pin
    -> first pin migrates page
    -> later registration succeeds

Case D
ordinary page + first non-long-term pin
    -> no CMA migration required
    -> registration succeeds
```

That matrix is more valuable than reproducing one failure. It proves each edge of the hypothesis separately.

### 16.6 Trace the transition

Useful tracepoints or probes include:

```text
pin_user_pages_fast
long-term GUP helper
get_pageblock_migratetype
migrate_pages
ib_umem_get
mlx5/rxe user-MR registration
```

A reconstructed successful repair path would look like:

```text
long-term pin requested
  pageblock = MIGRATE_CMA
  migrate P -> Q
  retry GUP
  pageblock(Q) != MIGRATE_CMA
  success
```

The failing case differs by one fact:

```text
migrate P -> Q
  source P has an existing device pin
  migration fails
  -ENOMEM
```

---

## 17. Better telemetry for memory registration

The incident took months partly because each layer discarded context.

A production-quality registration event should preserve:

```text
userspace address and length
allocation class: host / CUDA / DMA-BUF
protocol and direction
MR API used
process and communicator identity
underlying errno
memlock usage and limit
pageblock migratetype for host pages
whether FOLL_LONGTERM was requested
DMA-BUF exporter name
BAR1 usage for GPU mappings
MKey/provider failure stage
```

Not every field belongs in every log line. A structured trace or error report can gather them conditionally on failure.

In this case, a registration failure report needed to preserve `pageblock=MIGRATE_CMA` and the failed migration, rather than stopping at “NCCL system error.”

---

## 18. What generalizes

### Pinning is a contract, not a property

Asking “is the page pinned?” is incomplete. Ask:

```text
Who pinned it?
Through which API?
With FOLL_PIN or only a reference?
Was FOLL_LONGTERM declared?
Which device translation depends on it?
Who owns invalidation?
```

Two callers can both say “pinned” and still disagree about placement and lifetime.

### Physical placement is part of device correctness

Most application code treats physical memory as an implementation detail. Device DMA makes placement observable. `ZONE_MOVABLE`, CMA, device memory, DAX, and filesystem-backed pages all carry rules that a long-term pin must respect.

<div id="the-first-successful-operation-can-create-the-later-failure"></div>
<div id="an-optimization-can-spend-another-subsystems-invariant"></div>
<div id="error-names-describe-the-callers-view"></div>

### Fleet roles need different memory policy

HugeTLB reserves, IOMMU modes, NUMA balancing, transparent huge pages, and reclaim settings can affect DMA-heavy workloads. Sharing a kernel across fleet roles does not mean they all need the same boot policy.

---

## 19. The invariant that would have prevented the incident

> **A page must pass its strongest lifetime and placement contract before any subsystem makes it immovable.**

For this incident:

```text
strongest contract = long-term device DMA
```

Therefore one of the following must happen first:

```text
- allocate from a location valid for long-term pins;
- request FOLL_LONGTERM on the first pin so Linux can relocate it;
- export one shared lifetime object used by all devices;
- or remove the movable reserve from a workload that does not need it.
```

What cannot safely happen is:

```text
pin now under a weak placement contract
validate long-term placement later
```


## 20. A note on modern stacks

This series describes an R525/R535-era stack. It should not be read as a claim that an unchanged failure path exists on every current cluster.

Three later changes matter:

- In public R555 source on x86, NVIDIA's host-page import adds `FOLL_LONGTERM`. That moves the placement check to the first NVIDIA pin and closes the exact missing-contract window on that public path.
- Starting with R560, NVIDIA makes the open kernel-module flavor the default and suggested installation on supported GPUs, making DMA-BUF the normal direction for modern GPUDirect deployments when the rest of the stack supports it.
- NCCL 2.19 introduced explicit user-buffer registration for NVLS through `ncclCommRegister()` / `ncclMemAlloc()`, and later releases expanded registration to more paths. Modern NCCL can therefore create and reuse registrations differently from the internal-buffer flow described for 2.17.

Those changes rearrange or close this particular path; they do not make memory-lifetime contracts irrelevant. Older driver branches, vendor forks, and third-party device pins can still create the same general ordering error. The first debugging step should always be to identify the exact allocation, registration API, driver flavor, and GUP flags on the versions actually running.

<div id="epilogue"></div>

## Source map

- Linux v6.6, [`mm/gup.c`](https://github.com/torvalds/linux/blob/v6.6/mm/gup.c): `FOLL_PIN`, long-term-pinnability checks, migration, and `MR_LONGTERM_PIN`.
- Linux v6.6, [`include/linux/mm.h`](https://github.com/torvalds/linux/blob/v6.6/include/linux/mm.h): long-term-pinnable page/folio rules.
- Linux v6.6, [`drivers/infiniband/core/umem.c`](https://github.com/torvalds/linux/blob/v6.6/drivers/infiniband/core/umem.c): RDMA host MR registration with `FOLL_LONGTERM`.
- Linux, [`mm/Kconfig`](https://github.com/torvalds/linux/blob/v6.6/mm/Kconfig): CMA's movable-page design.
- Linux v6.6, [`mm/page_alloc.c`](https://github.com/torvalds/linux/blob/v6.6/mm/page_alloc.c): stock conditional fallback from movable allocations into CMA.
- Linux v6.6, [`mm/memory.c`](https://github.com/torvalds/linux/blob/v6.6/mm/memory.c), [`include/linux/highmem.h`](https://github.com/torvalds/linux/blob/v6.6/include/linux/highmem.h), and [`include/linux/gfp_types.h`](https://github.com/torvalds/linux/blob/v6.6/include/linux/gfp_types.h): anonymous fault allocation through `GFP_HIGHUSER_MOVABLE`.
- Linux, [`hugetlb_cma` kernel parameter](https://docs.kernel.org/admin-guide/kernel-parameters.html): gigantic HugeTLB CMA reservation.
- Linux, [`mm/hugetlb.c`](https://github.com/torvalds/linux/blob/v6.6/mm/hugetlb.c): HugeTLB CMA allocation and per-NUMA-node reservation.
- Johannes Weiner, [`mm: page_alloc: consume available CMA space first`](https://lkml.iu.edu/hypermail/linux/kernel/2307.3/04508.html): the allocator policy and rationale.
- NVIDIA open modules, [`os-mlock.c` R525.105.17](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/525.105.17/kernel-open/nvidia/os-mlock.c), [`R535.104.05`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/535.104.05/kernel-open/nvidia/os-mlock.c), and [`R555.42.02`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/555.42.02/kernel-open/nvidia/os-mlock.c): host-page pinning before and after `FOLL_LONGTERM` was added.
- NVIDIA open modules R535, [`escape.c`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/535.104.05/src/nvidia/arch/nvalloc/unix/src/escape.c), [`nv-linux.h`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/535.104.05/kernel-open/common/inc/nv-linux.h), and [`nv-vm.c`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/535.104.05/kernel-open/nvidia/nv-vm.c): imported userspace pages versus driver-owned `GFP_KERNEL` pages.
- NVIDIA, [kernel module installation guide](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/latest/kernel-modules.html): open modules become the default and suggested flavor starting with R560.
- NVIDIA NCCL 2.19.3, [User Buffer Registration](https://docs.nvidia.com/deeplearning/nccl/archives/nccl_2193/user-guide/docs/usage/bufferreg.html): the initial `ncclCommRegister()` / `ncclMemAlloc()` registration model for NVLS.
- Linux kernel documentation, [pinning user-space pages](https://docs.kernel.org/core-api/pin_user_pages.html): the distinction between GUP references and DMA pins.

