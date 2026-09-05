---
title: "Before the kernel answers: IDT, SYSCALL, and the stack-switch fine print"
description: "On x86-64, interrupts and SYSCALL enter the kernel differently. Follow the stack switch, the saved registers, and the extra work required by KPTI."
date: 2026-07-12
tags: [kernel, x86-64, syscalls]
draft: true
---

> This expands the kernel-entry section of [What actually happens between exec() and main()](/blog/ELF-Linking-101/).

On x86-64, entering the kernel from user mode also requires switching to a kernel-controlled stack. For interrupts and exceptions delivered through the IDT, hardware performs that switch. `SYSCALL` leaves `RSP` unchanged, so Linux’s entry code switches stacks before using the stack.

## 1) The Interrupt Descriptor Table (IDT)

When we press a key, the keyboard generates an external interrupt. On modern systems the interrupt routing logic (APIC/IO-APIC, etc.) delivers an **interrupt *vector*** to the CPU. People often say "IRQ 1 for keyboard," but that's a legacy naming convention: what the CPU uses to index the IDT is the **vector number**, and Linux's own docs refer to "IDT vector assignments" (e.g., in `arch/x86/include/asm/irq_vectors.h`). ([Kernel][1])

The CPU consults the **IDT**, a table mapping interrupt/exception vectors to entry stubs (interrupt/trap gates). Linux registers many of these entry points in `traps.c` and implements the mechanics in `entry_64.S`. ([Kernel][1])

### Switching to a kernel stack

This is the security-critical guarantee: **on a privilege transition (CPL 3 → CPL 0), the CPU cannot safely execute on the user stack**, so it switches to a kernel-controlled stack.

There are two related mechanisms:

1. **Normal ring transition stack (TSS RSP0 / "the regular kernel stack")**
   If the IDT gate does **not** request an IST stack, then on CPL 3 → CPL 0 entry the CPU loads the kernel stack pointer from the TSS (the ring-0 stack slot) and begins building the entry frame there.

2. **Interrupt Stack Table (IST): optional per-vector "known-good" stacks**
   If the IDT gate specifies a non-zero **IST index**, the CPU loads the stack pointer from that IST slot in the TSS. Linux explicitly calls out that **IST-based entry needs special handling**, and that "super-atomic" vectors and certain contexts rely on the more careful entry logic; it also notes that some entries push an error code and others do not, and that the IST stack mechanism changes the stack-frame mechanics. ([Kernel][1])

Linux reserves IST entries for vectors that need the more careful entry handling, rather than using them for every interrupt. ([Kernel][1])

### The save: what actually gets pushed

On interrupt/exception entry, the CPU builds a defined stack frame (more than just RIP/RSP). At minimum it preserves the instruction pointer / flags / code segment, and on privilege transitions it also saves the old stack context; certain exceptions add an **error code**. Linux's entry documentation explicitly notes this split ("Some of the IDT entries push an error code onto the stack; others don't."). ([Kernel][1])

## 2) The `syscall` instruction (the fast path) and why it's "special"

Historically, system calls used the IDT as well (e.g., `int 0x80`). That path necessarily uses the interrupt/trap machinery: IDT lookup, hardware frame push, and (when coming from user mode) an automatic stack switch via the TSS.

Modern x86-64 adds `SYSCALL` specifically to make this transition cheaper.

### The setup (MSRs: `IA32_LSTAR`, `IA32_STAR`, `IA32_FMASK`)

When the OS boots, it programs model-specific registers (MSRs) so the CPU knows where to enter the kernel on `SYSCALL`:

- `IA32_LSTAR`: the 64-bit kernel entry RIP for `SYSCALL`
- `IA32_STAR`: encodes the code/stack segment selectors
- `IA32_FMASK`: specifies which RFLAGS bits are cleared on entry

These registers supply the entry address and state changes used by `SYSCALL`. ([Félix Cloutier][2])

### The jump: what hardware does on `SYSCALL`

When user code executes `syscall`:

- The CPU loads RIP from `IA32_LSTAR`
- It saves the user return address into **RCX**
- It saves user flags into **R11**, then masks flags via `IA32_FMASK`

> **`SYSCALL` does not save the stack pointer (RSP), and does not switch stacks in hardware.** ([Félix Cloutier][2])

This avoids the full interrupt-frame push and hardware stack switch of an IDT entry.

### Switching stacks in the entry code

For the first few instructions after `SYSCALL` enters ring 0, `RSP` still holds the user value. Linux’s entry stub handles that window as follows:

- **It does not touch the stack** (no `push`, no stack spills) until it switches stacks.
- It immediately switches to a kernel-controlled stack in software as part of the entry sequence.

This is why system-call teaching material (and kernel entry docs) can correctly summarize the end result as: during the user→kernel transition "the stack is also switched from the user stack to the kernel stack". But for the `SYSCALL` path that switching is performed by the kernel's entry code, not by hardware. ([Linux Kernel Labs][3])

## 3) KPTI / PTI (Kernel Page Table Isolation)

On CPUs affected by Meltdown-class issues, entering the kernel can involve an additional heavyweight transition: changing which page tables are active so kernel mappings aren't present (or are severely constrained) in user mode.

### The core idea

With PTI enabled, the kernel maintains two page-table views:

- **User page tables:** map user space plus only the minimal kernel entry/exit structures required for safe transitions.
- **Kernel page tables:** map full kernel + user mappings.

Linux's PTI documentation explains that user page tables map only what's needed for kernel entry/exit (via structures like `cpu_entry_area`) and describes the duplication/sharing at the top level (PGD) used to keep user mappings consistent. ([Kernel][4])

### The cost: CR3 switching (and how PCID reduces the pain)

PTI adds runtime overhead primarily because:

- We must manipulate **CR3** to switch between the two page-table sets on syscall/interrupt/exception entry/exit (this can be skipped in some cases if the kernel is interrupted while already in kernel mode). ([Kernel][4])
- On systems **without PCID**, CR3 writes flush the TLB broadly, making each entry/exit more expensive. ([Kernel][4])
- With **PCID**, the CPU can avoid flushing the entire TLB on each switch; Linux's PTI docs describe how PCID makes switching cheaper and how some flush work can be deferred to reduce cost. ([Kernel][4])

### PTI + `SYSCALL`: the trampoline and "stacks must be switched at entry time"

Linux's PTI documentation calls out an additional nuance: PTI uses a **trampoline** for `SYSCALL` entry with a smaller mapped resource set, and explicitly notes "the downside is that stacks must be switched at entry time." This is the exact place where the "`SYSCALL` doesn't change RSP" architectural rule meets the kernel's need to get onto a safe stack immediately. ([Kernel][4])

## Reference summary

- **IDT-based entry from user mode:** CPU consults IDT, selects a kernel stack via TSS (optionally IST), pushes an entry frame, then runs kernel code. IST is **optional** and reserved for vectors that need a known-good stack and/or paranoid entry behavior. ([Kernel][1])
- **`SYSCALL` entry:** CPU jumps to `IA32_LSTAR`, saves return state in registers (RCX/R11), and does **not** change RSP; the kernel entry stub switches to a kernel stack in software **before touching the stack**, preserving security. ([Félix Cloutier][2])
- **PTI/KPTI:** adds page-table switching (CR3) on entry/exit; PCID reduces TLB-flush cost; PTI's syscall trampoline makes early stack switching even more central. ([Kernel][4])

[1]: https://www.kernel.org/doc/html/v5.10/x86/entry_64.html "Kernel Entries — The Linux Kernel documentation"
[2]: https://www.felixcloutier.com/x86/syscall "SYSCALL — Fast System Call"
[3]: https://linux-kernel-labs.github.io/refs/heads/master/lectures/syscalls.html "System Calls — The Linux Kernel documentation"
[4]: https://www.kernel.org/doc/html/next/x86/pti.html "Page Table Isolation (PTI) — The Linux Kernel documentation"
