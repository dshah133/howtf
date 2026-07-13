---
title: "howtf does ./app reach main()?"
description: "Trace one command from keystroke to main() — kernel, loader, PLT/GOT — until two classic deploy errors stop being mysterious."
date: 2026-02-16
updated: 2026-07-12
series:
  name: "Linking & Loading"
  part: 1
tags: [elf, linker, loader, x86-64]
---

We have all been there. You deploy a binary that worked perfectly on your development machine, but the production environment crashes with:

`/lib64/libc.so.6: version 'GLIBC_2.34' not found`

or

`error while loading shared libraries: libfoo.so: cannot open shared object file`

You do a frantic search, blindly paste `export LD_LIBRARY_PATH=` commands, and install random packages until the error disappears. We often treat the execution process as a black box, something that "just works" until it doesn't. These errors are symptoms of a system most engineers never look at closely, and that lack of understanding compounds when you are debugging at scale.

In this post, we take a different approach. We trace the life of a command from the moment you hit `Enter` until it reaches `main()`, watching the kernel, linker, and loader coordinate to turn a file on disk into a running process. Then we flash back to build time (Parts V–VI) to see where the machinery was set up — and at the end, **we reproduce both errors above on purpose and read the diagnosis straight off the binary**. Every dump in this post comes from one reproducible container; the demo and a `regenerate.sh` live [in the site repo](https://github.com/dshah133/howtf/tree/v4/demo/elf-linking).

**Scope & assumptions.** This walkthrough uses **Linux on x86‑64** as the concrete reference, with the **glibc dynamic loader** (`ld-linux-x86-64.so.2`) as "the loader" we talk about. The big ideas transfer to other architectures and libcs, but some details (relocation types, syscall entry, loader internals, memory-ordering constraints etc.) might differ.

**Who is this for?** If you have ever wondered what actually happens between hitting Enter and your code running, this is for you. Some comfort with C helps, and we will touch on assembly and kernel internals in places, but the main narrative is designed to be followed without deep expertise in either. The appendices are where the really gnarly details live.


<figure class="frame diagram">
  <span class="frame-title">fig. 0 — the relay race, keystroke to main()</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 300" role="img" aria-label="Two-lane diagram: user mode hands off to the kernel at execve, the kernel maps segments and the loader, the loader relocates itself and resolves dependencies, then control passes through _start to main">
      <defs>
        <marker id="f0a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
      </defs>
      <text x="14" y="30" font-family="var(--font-display)" font-size="11" fill="var(--muted)">ring 3</text>
      <text x="14" y="286" font-family="var(--font-display)" font-size="11" fill="var(--muted)">ring 0</text>
      <line x1="10" y1="150" x2="710" y2="150" stroke="var(--border)" stroke-dasharray="5 5"/>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="46" y="42" width="104" height="36" fill="var(--sec)" opacity="0.14"/>
        <rect x="46" y="42" width="104" height="36" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="98" y="64" text-anchor="middle" fill="var(--sec)">shell: ./app ⏎</text>
        <rect x="180" y="42" width="120" height="36" fill="var(--sec)" opacity="0.14"/>
        <rect x="180" y="42" width="120" height="36" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="240" y="58" text-anchor="middle" fill="var(--sec)">fork() then</text>
        <text x="240" y="72" text-anchor="middle" fill="var(--sec)">execve(2)</text>
        <rect x="160" y="196" width="270" height="52" fill="var(--krn)" opacity="0.14"/>
        <rect x="160" y="196" width="270" height="52" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="295" y="218" text-anchor="middle" fill="var(--krn)">kernel: load_elf_binary()</text>
        <text x="295" y="234" text-anchor="middle" fill="var(--krn)">map PT_LOADs · map ld.so via PT_INTERP</text>
        <rect x="380" y="42" width="150" height="36" fill="var(--ldr)" opacity="0.14"/>
        <rect x="380" y="42" width="150" height="36" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="455" y="58" text-anchor="middle" fill="var(--ldr)">ld.so: self-relocate,</text>
        <text x="455" y="72" text-anchor="middle" fill="var(--ldr)">load deps, fill GOT</text>
        <rect x="560" y="42" width="120" height="36" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="620" y="58" text-anchor="middle" fill="var(--muted)">_start →</text>
        <text x="620" y="72" text-anchor="middle" fill="var(--muted)">__libc_start_main</text>
        <rect x="586" y="112" width="68" height="30" fill="var(--accent)" opacity="0.18"/>
        <rect x="586" y="112" width="68" height="30" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="620" y="131" text-anchor="middle" fill="var(--accent)">main()</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.4" fill="none" marker-end="url(#f0a)">
        <path d="M 150 60 L 176 60"/>
        <path d="M 240 82 C 240 130, 240 160, 250 192"/>
        <path d="M 380 192 C 400 150, 420 110, 445 82"/>
        <path d="M 530 60 L 556 60"/>
        <path d="M 620 82 L 620 108"/>
      </g>
      <text x="255" y="136" font-family="var(--font-display)" font-size="10" fill="var(--muted)">the only trip below the line</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>our code</span>
      <span><span class="k" style="background:var(--krn)"></span>kernel</span>
      <span><span class="k" style="background:var(--ldr)"></span>loader</span>
      <span><span class="k" style="background:var(--accent)"></span>the destination</span>
    </p>
  </div>
</figure>

---

## Follow Along

We will use a standard Linux environment. If you are on macOS or Windows, use Docker Desktop to get deterministic userspace behavior (specifically for x86‑64 relocation types).


> **A Note on Architecture (Apple Silicon & Windows ARM):**
> If you are running on an ARM chip (M1/M2/M3, etc), you can still follow along.
>- **macOS:** Docker Desktop can run `linux/amd64` containers using Rosetta‑based translation wired through `binfmt_misc` when configured to do so. This is [documented by Apple](https://developer.apple.com/documentation/virtualization/running-intel-binaries-in-linux-vms-with-rosetta) and by Docker Desktop [settings](https://docs.docker.com/desktop/features/vmm/).
>- **Windows (ARM):** the common mechanism for running `linux/amd64` binaries under an ARM64 Linux environment (including WSL2-based backends) is **QEMU user-mode emulation** wired through Linux's `binfmt_misc`. Whether it's already configured "out of the box" depends on the Docker/WSL2 setup, versions, and registration state, but most likely it is.
>
> Curious how this cross-architecture magic works under the hood? See *[Appendix A](#appendix-a-the-cross-architecture-magic-rosetta--qemu)*.


**A note on prompts:** `❯` is my host machine; `root@container:/code#` is inside the container. Every dump in this post was captured in the container described below (gcc 11.4, glibc 2.35), by [`demo/elf-linking/regenerate.sh`](https://github.com/dshah133/howtf/tree/v4/demo/elf-linking).

**1. The Source Files**

Three files. One program, one shared library, nothing hidden:

```c title="main.c"
// main.c — the entire demo program. The interesting part is what links it.
#include <unistd.h>

extern int add(int a, int b);

int main(void) {
    int sum = add(5, 10);
    sleep(60); /* keeps the process alive so we can read /proc/<pid>/maps */
    return sum;
}
```

```c title="math.c"
// math.c
int add(int a, int b) {
    return a + b;
}
```

```make title="Makefile"
CC = gcc

all: libmath.so dynamic_app dynamic_app_lazy

libmath.so: math.c
	$(CC) -shared -fPIC -o libmath.so math.c

dynamic_app: main.c libmath.so
	$(CC) -o dynamic_app main.c -L. -lmath -Wl,-rpath,'$$ORIGIN'

# explicit lazy-binding variant for the PLT/GOT walkthrough (Part III)
dynamic_app_lazy: main.c libmath.so
	$(CC) -o dynamic_app_lazy main.c -L. -lmath -Wl,-z,lazy -Wl,-rpath,'$$ORIGIN'
```

Two things here are load-bearing, and both will pay off later: we link with `-L. -lmath` (**not** by naming `./libmath.so` directly — the difference reproduces one of our two opening errors, as we'll see in Part VII), and we build a second binary with `-Wl,-z,lazy` (Part III explains why we need to ask for lazy binding explicitly in 2026).

**2. Start the container**

```bash
# Force x86-64 to align with our assembly examples
❯ docker run --rm -it \
  --platform=linux/amd64 \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  -v "$PWD"/code:/code -w /code \
  ubuntu:22.04 bash

# Install tools
root@container:/code# apt-get update && apt-get install -y build-essential binutils gdb strace
```

**3. Compile the project:**

```bash
root@container:/code# make
gcc -shared -fPIC -o libmath.so math.c
gcc -o dynamic_app main.c -L. -lmath -Wl,-rpath,'$ORIGIN'
gcc -o dynamic_app_lazy main.c -L. -lmath -Wl,-z,lazy -Wl,-rpath,'$ORIGIN'

root@container:/code# ls
Makefile  dynamic_app  dynamic_app_lazy  libmath.so  main.c  math.c
```

---

You type `./dynamic_app` and hit Enter.

Your shell calls `fork()` to create a child process. That child process calls `execve("./dynamic_app")`, and your app starts running. Simple — as long as nobody asks what `execve` actually did.

---

## Part I: The Hardware Gate and Kernel Entry

### 1.1 The Wake Up

Your shell (bash/zsh) was actually asleep, blocked on a `read()` system call waiting for input. The kernel, tty, keyboard driver, etc. work together to let your shell know exactly what command the user executed.

> How your keystroke actually reaches the shell — PTYs, the line discipline, and why Ctrl+C sometimes can't save you — is a whole story of its own. It's coming as a separate post: *The keyboard dance*.

### 1.2 The `fork()` syscall (cloning)

The shell parses your command and decides to run a new program. But first, it must duplicate itself. It calls `fork()`.

This triggers a hardware transition.

1. **The Trap:** The CPU executes the syscall instruction (opcode `0F 05`).
2. **The Switch:** The hardware instantly elevates privileges to Ring 0.
3. **The Lookup:** It consults the Model Specific Registers (MSRs) to jump straight into the kernel's entry point (`entry_SYSCALL_64`) — stashing the user return address in `RCX` and the flags in `R11` on the way. `SYSCALL` touches no stack; the kernel's entry stub switches to a kernel stack in software before it pushes anything.

> The hardware gate deserves more than three bullets — IDT vs `SYSCALL` entry, TSS/IST stack rules, and KPTI are a separate post: *Before the kernel answers*.

It creates a near‑identical copy of the shell (the child process). In practice, the kernel does not duplicate physical memory. It marks the writable private pages as copy‑on‑write (COW), so the two processes share the same physical pages until one of them writes. This child is now running, but it is still running the shell's code.

### 1.3 The `execve` Syscall

The transition for the syscall remains the same as fork, but the handler will be different. execve kernel handler discards the child's old memory map (the shell code) and prepares to load the new binary.

The Operating System has taken the wheel. It is now sitting in Ring 0 with the file path ./dynamic_app and a mandate to start executing it.

### 1.4 Inside the Kernel: `fs/exec.c`

Once inside the kernel, execution eventually reaches `do_execveat_common` in [fs/exec.c](https://elixir.bootlin.com/linux/v6.8/source/fs/exec.c#L1908).

The kernel opens the file and iterates through a list of "binary handlers" to find one that understands the file format. Since this is an ELF file, it lands in `load_elf_binary` in [fs/binfmt_elf.c](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L819).

### 1.5 The Magic Check

First, the kernel validates that this is actually an ELF file. It reads the first 4 bytes. If they aren't `0x7F 'E' 'L' 'F'`, it rejects the file immediately.

```c title="fs/binfmt_elf.c (v6.8, lightly trimmed)"
struct elfhdr *elf_ex = (struct elfhdr *)bprm->buf;

if (memcmp(elf_ex->e_ident, ELFMAG, SELFMAG) != 0)
    goto out;
```

([view at v6.8](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L843))

---

## Part II: Mapping the Memory

The kernel does **not** care about "sections" (like `.text` or `.data`). Those are build/link time constructions, mainly for the linker. The kernel cares about **segments** (Program Headers), which tell the kernel what exactly to load and where.

<figure class="frame diagram">
  <span class="frame-title">fig. 1 — one file, two readings</span>
  <div class="diagram-body">
    <svg viewBox="0 0 640 330" role="img" aria-label="Diagram mapping ELF file sections to memory segments">
      <text x="120" y="24" text-anchor="middle" font-family="var(--font-display)" font-size="12" fill="var(--muted)">the file (offsets)</text>
      <text x="520" y="24" text-anchor="middle" font-family="var(--font-display)" font-size="12" fill="var(--muted)">memory (virtual addresses)</text>
      <g font-family="var(--font-mono)" font-size="12">
        <rect x="40" y="40" width="160" height="34" rx="0" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="120" y="61" text-anchor="middle" fill="var(--krn)">ELF + program headers</text>
        <rect x="40" y="82" width="160" height="40" fill="var(--sec)" opacity="0.14"/>
        <rect x="40" y="82" width="160" height="40" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="120" y="106" text-anchor="middle" fill="var(--sec)">.text</text>
        <rect x="40" y="130" width="160" height="34" fill="var(--sec)" opacity="0.14"/>
        <rect x="40" y="130" width="160" height="34" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="120" y="151" text-anchor="middle" fill="var(--sec)">.rodata</text>
        <rect x="40" y="172" width="160" height="34" fill="var(--sec)" opacity="0.14"/>
        <rect x="40" y="172" width="160" height="34" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="120" y="193" text-anchor="middle" fill="var(--sec)">.data / .bss</text>
        <text x="120" y="235" text-anchor="middle" fill="var(--muted)">sections: the linker's view</text>
      </g>
      <g font-family="var(--font-mono)" font-size="12">
        <rect x="440" y="40" width="160" height="46" fill="var(--seg)" opacity="0.14"/>
        <rect x="440" y="40" width="160" height="46" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="520" y="60" text-anchor="middle" fill="var(--seg)">LOAD  R--</text>
        <text x="520" y="76" text-anchor="middle" fill="var(--muted)" font-size="10">headers · .rodata</text>
        <rect x="440" y="94" width="160" height="46" fill="var(--seg)" opacity="0.14"/>
        <rect x="440" y="94" width="160" height="46" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="520" y="114" text-anchor="middle" fill="var(--seg)">LOAD  R-X</text>
        <text x="520" y="130" text-anchor="middle" fill="var(--muted)" font-size="10">.text</text>
        <rect x="440" y="148" width="160" height="46" fill="var(--seg)" opacity="0.14"/>
        <rect x="440" y="148" width="160" height="46" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="520" y="168" text-anchor="middle" fill="var(--seg)">LOAD  RW-</text>
        <text x="520" y="184" text-anchor="middle" fill="var(--muted)" font-size="10">.data · .bss</text>
        <rect x="440" y="210" width="160" height="38" fill="var(--ldr)" opacity="0.14"/>
        <rect x="440" y="210" width="160" height="38" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="520" y="233" text-anchor="middle" fill="var(--ldr)">ld-linux-x86-64.so.2</text>
        <rect x="440" y="256" width="160" height="30" fill="none" stroke="var(--border)" stroke-dasharray="4 4"/>
        <text x="520" y="275" text-anchor="middle" fill="var(--muted)">[stack]</text>
      </g>
      <g stroke="var(--krn)" stroke-width="1.5" fill="none" marker-end="url(#arr)">
        <path d="M 204 102 C 320 92, 340 108, 436 114"/>
        <path d="M 204 147 C 320 120, 330 58, 436 60"/>
        <path d="M 204 189 C 320 186, 330 170, 436 168"/>
      </g>
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--krn)"/>
        </marker>
      </defs>
      <text x="320" y="308" text-anchor="middle" font-family="var(--font-display)" font-size="11" fill="var(--krn)">mmap'd by the kernel from program headers · loader mapped via PT_INTERP</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>file sections</span>
      <span><span class="k" style="background:var(--seg)"></span>memory segments</span>
      <span><span class="k" style="background:var(--ldr)"></span>loader</span>
      <span><span class="k" style="background:var(--krn)"></span>kernel</span>
    </p>
  </div>
</figure>

### 2.1 Iterating Segments (`load_elf_binary`)

The kernel loops over the program headers (`PT_LOAD`) to figure out what to map. ([View Source in `binfmt_elf.c`](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L1066))

```c title="fs/binfmt_elf.c (v6.8, simplified)"
for(i = 0, elf_ppnt = elf_phdata; i < elf_ex->e_phnum; i++, elf_ppnt++) {
    if (elf_ppnt->p_type == PT_LOAD) {
        // Create the memory mapping
        error = elf_map(bprm->file, load_bias + vaddr, elf_ppnt, ...);
    }
}
```

It is common to *conceptually* talk about "two main regions":

1. **Code-ish mappings:** read + execute (your code + PLT stubs + some read-only metadata).
2. **Data-ish mappings:** read + write (globals, `.bss`, GOT areas, dynamic data).

However, and this matters for correctness, modern toolchains frequently emit **more than two `PT_LOAD` segments** (e.g., separate read-only segments for constants, plus layouts that support RELRO cleanly). In our demo binary, `readelf -l ./dynamic_app` reveals **four** distinct `PT_LOAD` segments:

1. **Read-Only Metadata (`R`):** ELF headers and dynamic symbol tables.
2. **The Text Segment (`R E`):** Your actual code (`.text`) and the PLT stubs. This is the only memory executable by the CPU.
3. **Read-Only Data (`R`):** Constants (`.rodata`) and unwind info. Kept out of the executable mapping (`-z separate-code` is another modern default) so constant data can never be fetched as instructions — it shrinks the attack surface, though it does not by itself prevent ROP.
4. **Writable Data (`RW`):** Global variables (`.data`) and the Global Offset Table (GOT).

See [Appendix D](#appendix-d-segments-deep-dive) for the full `readelf -l` output and a detailed walkthrough.



### 2.2 Finding the Correct Address for the Segments

The code above already hints at the answer: each segment's virtual address is `load_bias + vaddr`, where `vaddr` comes straight from the program header's `p_vaddr` field. But look at the actual values in our binary (from [Appendix D](#appendix-d-segments-deep-dive)):

```text
  LOAD           0x0000000000000000 0x0000000000000000 0x0000000000000000
                 0x0000000000000638 0x0000000000000638  R      0x1000
```
A `p_vaddr` of 0x0? That would map over the NULL page. Something is off.

The explanation is that our binary is not a traditional fixed-address executable (`ET_EXEC`). On modern distros, GCC defaults to building **Position-Independent Executables (PIE)**, which use type **`ET_DYN`** in the ELF header. This does not mean it is a shared library. It means the entire image can be loaded at an arbitrary base address, which is what enables ASLR.

```bash
root@container:/code# readelf -h ./dynamic_app | egrep 'Type:|Entry'
  Type:                              DYN (Position-Independent Executable file)
  Entry point address:               0x1080
```

This is where the variable [`load_bias`](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L1087) in the kernel code is conceptually coming from:

- With PIE: the kernel chooses a randomized base address (ASLR).
- Runtime virtual address = `load_bias + p_vaddr`
- Runtime entry point = `load_bias + e_entry`

See [Appendix D](#appendix-d-segments-deep-dive) to see this load_bias in action for our demo app.

At this point, assume that segments are loaded into the process's address space (or more precisely, mmapped).

> **A note on "mapped" vs "loaded":**
> When we say "mapped," we do not mean "copied to RAM." The `elf_map` call essentially creates a **VMA (Virtual Memory Area)** that tells the kernel: "If the CPU asks for virtual address `X`, the bytes live in this file at offset `Y`." The physical RAM can be **empty**. When the CPU tries to execute the first instruction, a **page fault** fires. The kernel catches it, fetches the page from disk (via the page cache), and resumes execution as if nothing happened. This is demand paging.


### 2.3 The Fork in the Road: `PT_INTERP`

Then the kernel checks for a specific header: `PT_INTERP`. ([View Source](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L868))

```c title="fs/binfmt_elf.c (pseudo-code)"
if (elf_ppnt->p_type == PT_INTERP) {
    interpreter = open_exec(interp_name); // e.g., /lib64/ld-linux-x86-64.so.2
    ...
    entry = load_elf_interp(&interp_elf_ex, interpreter, ...); // its own mapper, no recursion
}
```

Because `dynamic_app` has this header, the kernel maps the dynamic loader (`ld-linux.so`) into memory with a dedicated helper, `load_elf_interp()` — the interpreter's own `PT_INTERP`, if it had one, would be ignored (which is exactly why `ld.so` must bootstrap itself, as we'll see in Part III). The kernel then sets the instruction pointer to the *loader's* entry point, not your `dynamic_app`'s. ([View Source](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L1200))

One more thing before the kernel leaves the stage: how will the loader know where *our* binary landed? The kernel writes the answer onto the new process's stack as the **auxiliary vector** — `AT_PHDR` (where the program headers were mapped), `AT_ENTRY` (the app's real entry point), `AT_BASE` (where the interpreter itself landed), and friends. That auxv is the kernel→loader handshake; you can watch it with `LD_SHOW_AUXV=1 ./dynamic_app`.

---

## Part III: The Loader Takes Control (User Mode)

Control returns to User Mode. The program running is now the dynamic loader (`ld-linux.so`), appearing in `glibc` source as `elf/rtld.c`.

### 3.1 Self-Relocation (The Bootstrap)

The loader itself is also just a program, just a bit special one as it wakes up in a hostile environment. Because of ASLR, it has been loaded at a random address, meaning all its internal pointers to global variables are wrong. It cannot call functions or access static data yet. Before it can do anything else, the loader must fix these addresses. This happens in the `_dl_start` path. See [Appendix E: The Loader's Bootstrap](#appendix-e-the-loaders-bootstrap-self-relocation) for more details.

### 3.2 Dependency Discovery

Once the loader has healed itself, it becomes a fully functional C program running inside your process. It can now inspect your `dynamic_app`. It reads the `PT_DYNAMIC` segment to find `DT_NEEDED` tags — `libmath.so` and `libc.so.6` in our case — finds each library, and maps it into the process with `mmap`.

Where does it look? The precedence is specific, and worth stating exactly because our second opening error lives here: `DT_RPATH` (only honored if `DT_RUNPATH` is absent) → `LD_LIBRARY_PATH` → `DT_RUNPATH` (which applies only to the object's *direct* dependencies) → `/etc/ld.so.cache` → the default dirs (`/lib`, `/usr/lib`, …). And one rule that overrides all of it: **if the stored name contains a `/`, it is treated as a path and no search happens at all.** Hold that thought for Part VII.

You can watch the search happen — this is `LD_DEBUG=libs` running our binary, showing the `RUNPATH`-driven probe sequence for `libmath.so`:

```bash
root@container:/# LD_DEBUG=libs /code/dynamic_app
      3932:	find library=libmath.so [0]; searching
      3932:	 search path=/code/glibc-hwcaps/x86-64-v3:...:/code		(RUNPATH from file /code/dynamic_app)
      3932:	  trying file=/code/glibc-hwcaps/x86-64-v3/libmath.so
      3932:	  trying file=/code/tls/haswell/libmath.so
      ...
```

### 3.3 Filling the GOT: now, or later?

With every library mapped, the loader must make cross-object calls work. Your `main()` calls `add()`, but `add` lives in `libmath.so` at an address nobody knew until two milliseconds ago. The fix-up table for this is the **GOT (Global Offset Table)**: a table of pointers, one per external thing, that the loader fills in with the real addresses. Calls and data accesses go *through* the GOT instead of embedding addresses directly.

There are two strategies for *when* the function-call slots get filled:

- **Eager (`BIND_NOW`):** resolve every symbol at startup, before your code runs.
- **Lazy:** leave function slots pointing at a resolver, and fix each one the *first time it's called*.

Textbooks — and the previous version of this post — describe lazy as "the default." **On your distro, it probably isn't.** Look at what Ubuntu's gcc actually passed to the linker (this is from `gcc -v`, Part V shows the full line): `-pie -z now -z relro`. That `-z now` means our default build is eager. The binary says so:

```bash
root@container:/code# readelf -d ./dynamic_app | grep -E 'FLAGS'
 0x000000000000001e (FLAGS)              BIND_NOW
 0x000000006ffffffb (FLAGS_1)            Flags: NOW PIE

root@container:/code# readelf -d ./dynamic_app_lazy | grep -E 'FLAGS'
 0x000000006ffffffb (FLAGS_1)            Flags: PIE
```

This is why our Makefile builds `dynamic_app_lazy` with `-Wl,-z,lazy`: in 2026 you have to *ask* for lazy binding to study it.

The difference is also a security posture, and it's visible in RELRO. **RELRO (RELocation Read-Only)** is the `GNU_RELRO` segment: after the loader finishes its patches, it `mprotect`s that region read-only. With `-z now` you get **full RELRO** — every GOT slot is resolved up front, so *all* of them (including the function-call slots) sit inside the protected region. With lazy binding you get **partial RELRO** — the function-call slots (`.got.plt`) must stay writable so the resolver can patch them later. Our two builds show it directly: the `R_X86_64_JUMP_SLOT` entries for `add` and `sleep` land at `0x3fc8/0x3fd0` in the eager build — *inside* its RELRO region `[0x3d90, 0x4000)` — but at `0x4018/0x4020` in the lazy build, *past the end* of its RELRO region `[0x3dc8, 0x4000)`. Same program, same symbols; one layout locks the slots, the other leaves them writable forever. (That writable-GOT window is exactly the classic GOT-overwrite target — a point we'll return to below.)

### 3.4 Lazy binding, watched live

Eager binding is easy to imagine: a loop over relocation entries at startup (Appendix F walks it record by record). Lazy binding is the clever one, so let's *watch* it. Here is the machinery in the lazy binary, straight from `objdump`:

```asm title="objdump -d dynamic_app_lazy (trimmed)" {5}
0000000000001169 <main>:
    ...
    1175:  be 0a 00 00 00        mov    $0xa,%esi
    117a:  bf 05 00 00 00        mov    $0x5,%edi
    117f:  e8 dc fe ff ff        call   1060 <add@plt>

0000000000001060 <add@plt>:                          ; .plt.sec
    1060:  f3 0f 1e fa           endbr64
    1064:  f2 ff 25 ad 2f 00 00  bnd jmp *0x2fad(%rip)   # 4018 <add's GOT slot>

0000000000001030 <.plt entry for add>:
    1030:  f3 0f 1e fa           endbr64
    1034:  68 00 00 00 00        push   $0x0             ; relocation index for 'add'
    1039:  f2 e9 e1 ff ff ff     bnd jmp 1020            ; the common resolver stub
```

`main` doesn't call `add` — it calls `add@plt`, a tiny trampoline that jumps *through GOT slot `0x4018`*. And what does that slot contain before the first call? The file itself tells us — `readelf -x .got.plt` shows slot `0x4018` holding `0x1030`: **it points back into the PLT**, at the very next instruction of the dance. So the first call goes `main → add@plt → (through GOT) → push $0x0 → resolver`, the resolver figures out which symbol relocation index 0 is, finds `add` in `libmath.so`, and **patches the GOT slot** so every later call jumps straight there.

Don't take my word for the patch — the demo binary can watch its own GOT slot change. `got_watch.c` (in the demo repo) reads the slot for `add` before and after the first call:

```bash
root@container:/code# ./got_watch
GOT slot for add lives at 0x555555558018
  before first call : 0x0000555555555030  <- points back into our own .plt
  add(5, 10) returns: 15      <- first call takes the resolver detour
  after first call  : 0x00007fffff7bd0f9  <- patched!
  libmath.so code   : 7fffff7bd000-7fffff7be000 r-xp ... /code/libmath.so
```

The before-value is our own image base plus `0x1030` — exactly the `push $0x0` stub in the `.plt` dump above. The after-value lands inside `libmath.so`'s executable mapping: it's `add` itself.

One trap worth knowing (it bit this demo): `got_watch.c` is careful **never to take `&add`**. The moment a program takes a function's address, the linker must guarantee pointer equality across all objects, so it resolves that symbol eagerly through `.plt.got` and the lazy `JUMP_SLOT` you wanted to watch never exists.

<figure class="frame diagram">
  <span class="frame-title">fig. 3 — the same stub, two routes</span>
  <div class="diagram-body">
    <svg viewBox="0 0 680 330" role="img" aria-label="Side-by-side panels showing the first call through the PLT going via the resolver which patches the GOT slot, and every later call going straight through the patched slot">
      <defs>
        <marker id="f3a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="f3l" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--ldr)"/>
        </marker>
      </defs>
      <text x="170" y="24" text-anchor="middle" font-family="var(--font-display)" font-size="12" fill="var(--muted)">first call (lazy)</text>
      <text x="510" y="24" text-anchor="middle" font-family="var(--font-display)" font-size="12" fill="var(--muted)">every call after</text>
      <line x1="340" y1="14" x2="340" y2="316" stroke="var(--border)" stroke-dasharray="4 4"/>
      <g font-family="var(--font-mono)" font-size="11">
        <!-- left panel -->
        <rect x="110" y="40" width="120" height="30" fill="var(--sec)" opacity="0.14"/>
        <rect x="110" y="40" width="120" height="30" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="170" y="59" text-anchor="middle" fill="var(--sec)">main: call add</text>
        <rect x="110" y="100" width="120" height="30" fill="var(--sec)" opacity="0.14"/>
        <rect x="110" y="100" width="120" height="30" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="170" y="119" text-anchor="middle" fill="var(--sec)">add@plt stub</text>
        <rect x="110" y="160" width="120" height="30" fill="var(--seg)" opacity="0.14"/>
        <rect x="110" y="160" width="120" height="30" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="170" y="179" text-anchor="middle" fill="var(--seg)">GOT slot</text>
        <rect x="30" y="222" width="150" height="34" fill="var(--ldr)" opacity="0.14"/>
        <rect x="30" y="222" width="150" height="34" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="105" y="239" text-anchor="middle" fill="var(--ldr)">_dl_runtime_resolve</text>
        <text x="105" y="252" text-anchor="middle" fill="var(--ldr)">finds add, patches slot</text>
        <rect x="210" y="282" width="100" height="30" fill="var(--sec)" opacity="0.14"/>
        <rect x="210" y="282" width="100" height="30" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="260" y="301" text-anchor="middle" fill="var(--sec)">add()</text>
        <!-- right panel -->
        <rect x="450" y="40" width="120" height="30" fill="var(--sec)" opacity="0.14"/>
        <rect x="450" y="40" width="120" height="30" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="510" y="59" text-anchor="middle" fill="var(--sec)">main: call add</text>
        <rect x="450" y="100" width="120" height="30" fill="var(--sec)" opacity="0.14"/>
        <rect x="450" y="100" width="120" height="30" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="510" y="119" text-anchor="middle" fill="var(--sec)">add@plt stub</text>
        <rect x="450" y="160" width="120" height="30" fill="var(--seg)" opacity="0.14"/>
        <rect x="450" y="160" width="120" height="30" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="510" y="179" text-anchor="middle" fill="var(--seg)">GOT slot ✓ patched</text>
        <rect x="450" y="282" width="120" height="30" fill="var(--sec)" opacity="0.14"/>
        <rect x="450" y="282" width="120" height="30" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="510" y="301" text-anchor="middle" fill="var(--sec)">add()</text>
      </g>
      <g font-family="var(--font-display)" font-size="10">
        <!-- left arrows: 5 numbered hops -->
        <g stroke="var(--muted)" stroke-width="1.4" fill="none" marker-end="url(#f3a)">
          <path d="M 170 70 L 170 96"/>
          <path d="M 170 130 L 170 156"/>
          <path d="M 148 190 C 130 202, 122 208, 112 218"/>
        </g>
        <g stroke="var(--ldr)" stroke-width="1.4" fill="none" marker-end="url(#f3l)">
          <path d="M 105 222 C 105 205, 128 192, 140 188" stroke-dasharray="4 3"/>
          <path d="M 180 250 C 215 262, 235 270, 252 278"/>
        </g>
        <text x="182" y="88" fill="var(--muted)">1</text>
        <text x="182" y="148" fill="var(--muted)">2 slot → back into stub</text>
        <text x="96" y="208" fill="var(--muted)">3</text>
        <text x="150" y="204" fill="var(--ldr)">4 patch</text>
        <text x="238" y="262" fill="var(--ldr)">5</text>
        <!-- right arrows: 3 hops -->
        <g stroke="var(--muted)" stroke-width="1.4" fill="none" marker-end="url(#f3a)">
          <path d="M 510 70 L 510 96"/>
          <path d="M 510 130 L 510 156"/>
          <path d="M 510 190 L 510 278"/>
        </g>
        <text x="522" y="88" fill="var(--muted)">1</text>
        <text x="522" y="148" fill="var(--muted)">2</text>
        <text x="522" y="238" fill="var(--muted)">3 jmp *slot</text>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>our code</span>
      <span><span class="k" style="background:var(--seg)"></span>GOT (data)</span>
      <span><span class="k" style="background:var(--ldr)"></span>loader</span>
    </p>
  </div>
</figure>

The full static evidence — the complete PLT disassembly, the initial `.got.plt` bytes, the relocation table — is in [Appendix F](#appendix-f-loaders-relocation-mechanism). (If you'd rather drive this with gdb, use native x86-64 Linux: under Rosetta emulation `ptrace` is unavailable, which is exactly why the self-inspecting approach exists.)

### 3.5 Why not just call through the GOT directly?

A fair question: if calls go through a GOT slot anyway, why bother with the PLT stub at all? Why doesn't the compiler emit `call *GOT_entry` directly?

It can (`-fno-plt` does roughly that — and consequently forces eager binding). The traditional PLT exists to solve the *"who called me?"* problem that lazy binding creates. If an unresolved `call *GOT_entry` landed in the resolver, the resolver would have no idea *which* symbol you wanted — `add`? `sleep`? The PLT stub's `push $0x0` is the missing ID: it pushes the relocation index so the resolver can look up exactly the right `R_X86_64_JUMP_SLOT` entry in `DT_JMPREL` and resolve precisely the intended symbol.

For the record-by-record version of everything above — how `PT_DYNAMIC` maps out the string/symbol/relocation tables, how `R_X86_64_GLOB_DAT` entries for things like `__libc_start_main` get resolved, and the full transcripts — see [Appendix F](#appendix-f-loaders-relocation-mechanism).


## Part IV: The Handoff (Loader → User)
The loader is now ready to hand control to your application. But it doesn't just call `main()`. In fact, it doesn't even know `main` exists.

The transition from the loader to your code happens in two steps.

**1: The loader's exit ([`_dl_start_user`](https://elixir.bootlin.com/glibc/glibc-2.42.9000/source/sysdeps/x86_64/dl-machine.h#L144))**
First, the loader runs the constructors (`.init` / `.init_array`) for all shared libraries (e.g., `libmath.so`) to ensure they are ready.

**2: The application's entry (`_start`)**
The CPU lands at a function called `_start`. This is not your code. It is a small assembly stub provided by the C runtime (`Scrt1.o` — the position-independent sibling of the classic `crt1.o`, since our binary is a PIE) that was linked into your binary at build time. Its job is to set up the stack and pass arguments (`argc`, `argv`) to the C library helper `__libc_start_main` — which runs the constructors for your executable (the loader's `_dl_init` already ran the shared libraries' constructors) and finally calls your `main`.

(Curious what this assembly looks like? See [Appendix G: The Assembly Handoff](#appendix-g-the-assembly-handoff-_start).)

That completes the relay from fig. 0 — every leg of it has now crossed the page. Replay the whole thing, one step at a time; each caption should read as review, not news:

<div class="frame diagram" data-loader-stepper>
  <span class="frame-title">fig. 0b — the relay, replayed step by step</span>
  <div class="diagram-body">
    <noscript><p>This figure is interactive and needs JavaScript; fig. 0 back in the intro tells the same story statically.</p></noscript>
  </div>
</div>

## Part V: The Flashback (Build Time)

When we run `gcc -c main.c`, GCC acts as a driver. It runs `cc1` (compiler) and `as` (assembler) to produce `main.o`.

At this stage, the compiler does not know where `add` is. It creates a relocation entry, basically a "to‑do" note for the linker.

Let's inspect `main.o`'s relocation table, and the machine code it refers to:

```bash
root@container:/code# gcc -c main.c
root@container:/code# readelf -r main.o
Relocation section '.rela.text' at offset 0x1a0 contains 2 entries:
  Offset          Info           Type           Sym. Value    Sym. Name + Addend
000000000017  000400000004 R_X86_64_PLT32    0000000000000000 add - 4
000000000024  000500000004 R_X86_64_PLT32    0000000000000000 sleep - 4

root@container:/code# objdump -d main.o
0000000000000000 <main>:
   0:	f3 0f 1e fa          	endbr64
   ...
   c:	be 0a 00 00 00       	mov    $0xa,%esi
  11:	bf 05 00 00 00       	mov    $0x5,%edi
  16:	e8 00 00 00 00       	call   1b <main+0x1b>
```

Look at offset `0x16`: a `call` instruction (`e8`) whose 4-byte operand is **all zeroes** — it "calls" the next instruction, because the compiler had nothing to put there. That's the hole.

- **Offset `0x17`:** the relocation points at the *operand*, one byte past the `e8` opcode — the exact 4 bytes the linker must patch. (There are two entries because `main` also calls `sleep`.)
- **Type `R_X86_64_PLT32`:** tells the linker: "I need a 32-bit PC-relative address to a PLT entry for symbol `add`."

### 5.1 The Hidden Startup Files

In Section 4 we saw that the real entry point is `_start`, not `main()`, and that it comes from the C runtime's startup object. But we never asked GCC to link that file. Where did it come from?

When you run `gcc`, it silently injects several startup objects provided by glibc: `Scrt1.o` (which contains `_start`; the plain `crt1.o` is used for non-PIE links), `crti.o` (init prologue), and `crtn.o` (init epilogue). The naming is historical: the original was called `crt0.o` (C RunTime, file zero), and the split into multiple files came later as initialization grew more complex. The `S` suffix marks the PIC/PIE variants.

You can see this hidden injection by running GCC with verbose flags:

```bash
root@container:/code# gcc -v -o dynamic_app main.o -L. -lmath 2>&1 | grep collect2
 .../collect2 ... -pie -z now -z relro -o dynamic_app
   .../x86_64-linux-gnu/Scrt1.o .../x86_64-linux-gnu/crti.o .../11/crtbeginS.o
   -L. ... main.o -lmath ... -lc ... .../11/crtendS.o .../x86_64-linux-gnu/crtn.o
```

Two things hide in that line. First, the startup files: since our binary is a PIE, gcc injects `Scrt1.o` (the position-independent variant of `crt1.o`; a non-PIE link would use `crt1.o` itself), plus `crti.o`/`crtn.o` and gcc's own `crtbeginS.o`/`crtendS.o`. Second — look again at the flags gcc chose without asking us: **`-pie -z now -z relro`**. That's the paper trail for both PIE-by-default (Part II) and eager-binding-by-default (Part III), sitting in one `gcc -v` invocation.

### 5.2 The Linker (`ld`)

Now `ld` runs. It has `main.o`, `Scrt1.o`, and `libmath.so`. It needs to create one file.

#### Step 1: The Blueprint (Linker Script)

The linker follows a script to decide memory layout.

```bash
root@container:/code# ld --verbose | grep -A 5 "SECTIONS"
```

Among many other directives, it tells the linker things like: "collect all input `.text` sections into one output `.text` section, all `.rodata` into one `.rodata`," and so on. It also defines segment boundaries, alignment, and the order things appear in the final binary.

#### Step 2: Weaving Sections Together

The linker maps the output file into memory (using `mmap`). It then performs a "scatter-gather" copy.

1. It copies `Scrt1.o`'s `.text` to the beginning of the output buffer.
2. It copies `main.o`'s `.text` right after it.
3. It updates its internal symbol map: `main` is no longer at offset `0`; it is now at some final virtual address (and in PIE, that address is a *relative* virtual address that will receive a load bias at runtime).

#### Step 3: Synthesis (PLT & GOT)

The linker sees the `R_X86_64_PLT32` relocation for `add`. It checks `libmath.so` and sees `add` is a shared symbol.

1. **Allocate:** it reserves space in `.plt` and `.got`.
2. **Write:** it writes the machine code instructions ("trampoline") into the PLT section, and reserves a GOT slot for the symbol.

But if `add` is resolved at runtime, why did the linker need `libmath.so` at all? It needs the file to verify that `add` actually exists, to record symbol and version requirements, and to write the `DT_NEEDED` tag so the loader knows to find and load `libmath.so` at runtime. (Linker flags like `--allow-shlib-undefined` or `--unresolved-symbols` can relax the existence check, but the default is to fail fast if a symbol can't be found.)

#### Step 4: Patching the Holes (Relocations)

Remember the relocation entry we saw earlier in `main.o`?

```text
Offset 0x17    Type R_X86_64_PLT32    Symbol: add    Addend: -4
```

The linker now processes this. It does not scan the machine code looking for call instructions. It walks the `.rela.text` table, and for each entry it knows exactly which byte to patch and how.

For our `add` entry, the process is:

1. The linker looks at the **Offset** (`0x17`). That is where the placeholder bytes sit inside `.text` — the operand of the `call` at `0x16`.
2. It knows from Step 3 that `add@plt` now lives at some address in the PLT section.
3. It computes: "how far is `add@plt` from this call site?" That distance is a 32-bit relative offset, which is what `R_X86_64_PLT32` asks for. (The addend `-4` accounts for the fact that x86 measures the offset from the *end* of the instruction — `0x17 + 4` — not from the operand itself.)
4. It writes that offset into the 4 bytes at position `0x17`, replacing the placeholder.

You can see the patched result in the final binary: the same instruction that read `e8 00 00 00 00` in `main.o` reads `e8 dc fe ff ff` in `dynamic_app_lazy` — a PC-relative hop to `add@plt` (we saw it in Part III's disassembly).

Now when the CPU executes this `call` instruction at runtime, the offset points straight to `add@plt`.

#### Step 5: Sections to Segments

Finally, the linker maps output sections (`.text`, `.data`) to program headers (`PT_LOAD`).

It groups read-only sections (`.text`, `.plt`, `.rodata`) into segments so the kernel can protect them efficiently, and it may emit multiple `PT_LOAD` segments to match permissions and RELRO constraints.

---

## Part VI: Static Linking

We have just spent multiple sections detailing the immense complexity of dynamic loading: the PLT, the GOT, the loader, runtime patching, and startup costs.

For many teams, this complexity is a feature: when a security vulnerability is found in a shared library like `openssl`, the OS can patch it once and every application that links against it picks up the fix without recompiling.

But for hyperscalers (like Meta, Google, and Netflix), this complexity is often a liability. They may opt for static linking, where every dependency is merged into a single executable file.

### 6.1 Why Hyperscalers Link Statically

Companies like Google and Meta prefer to statically link their production services. The reasons are practical:

**Hermeticity (the "dependency hell" problem):**
Imagine a service that depends on PyTorch, which depends on `libcuda.so`, which depends on `libgcc_s.so`. If you deploy a dynamically linked binary to a production machine that has a slightly different version of `libgcc`, your service crashes at 3 AM. With static linking, the binary is self-contained: if it works on the build machine, it works in production.

**Startup speed:**
Dynamic linking waits until runtime to resolve symbols, and for a large program this cost is not trivial. It can take seconds to calculate relocation mappings for large applications with hundreds of shared libraries. The loader must walk symbol hash tables, process relocations, and patch GOT entries, all the machinery we traced in Parts II through IV, before `main()` even starts. Static linking eliminates this entirely: there is no loader, no symbol resolution, and no PLT/GOT patching at runtime.

**Real-world example: Meta's build system (Buck2)**
Meta's build systems (Buck/Buck2) were designed to manage these trade-offs using build modes. (This is based on my experience working on Meta's build infrastructure.)

- `@mode/dev` (dynamic): used on developer laptops for fast iteration and quick incremental builds.
- `@mode/opt` (static / more hermetic): used for production deployments to guarantee performance and hermeticity.

Buck2's open-source configuration system exposes `dev` and `opt` as standard constraint values, and the choice between them changes how every C++ dependency in the graph is linked.

### 6.2 The Consequence: The 2 GiB Relocation Barrier

Static linking sounds perfect until physics gets in the way. When you bundle an entire AI stack, or the transitive closure of a massive monorepo, into a single binary, it can grow to gigabytes. While working on Meta's build infrastructure, I regularly saw Buck2-built binaries exceed 25 GiB (including debug symbols) for large C++ services. At that point, a fundamental x86-64 limitation surfaces.

Recall from Step 4 in Section 5.2: the `call` instruction uses a 32-bit signed PC-relative offset. That gives it a reach of roughly **±2 GiB**. If the linker cannot place a call target within that range, the link fails:

```
relocation truncated to fit: R_X86_64_PC32
```

This was not a theoretical problem. Large services with deep dependency graphs would hit this barrier, especially when built with instrumentation like `-fprofile-generate` or sanitizers that inflate code and data sections. Engineers sometimes refer to it colloquially as the "4 GB trap," but the underlying limit is the **signed 32-bit reach of PC-relative relocations**.

The brute-force fix is to compile with `-mcmodel=large`, which replaces the 5-byte relative `call` with a 12-byte `movabs` + `call` sequence that can reach any address. But this bloats every call site and increases register pressure, a steep price when you have millions of them.

**The practical solution: link groups**
Rather than choosing between 10,000 tiny shared objects (too slow to load) or 1 giant static binary (too big to link), hyperscalers split the difference. They group related code into "islands": everything inside a group is statically linked together into one medium-sized `.so`, and the main binary dynamically links against just a handful of these groups.

Buck2 has this concept built directly into its C++ rules. A `prebuilt_cxx_library_group` bundles related libraries that must be linked together, and the `auto_link_groups` and `link_group_map` attributes on `cxx_binary` let the build system automatically partition the dependency graph into groups. The result: you might resolve 5 or 6 groups at startup instead of 50,000 individual DSOs, while keeping each group well within the 2 GiB barrier.

### 6.3 The Execution Flow (No Loader Involved)

If you build a fully static binary, the execution flow changes drastically. The loader (`ld-linux.so`) is removed from the picture entirely.

You can see this logic in the Linux kernel source `fs/binfmt_elf.c`. When you run a binary, the kernel checks for the `PT_INTERP` segment (which specifies the loader).

**If `PT_INTERP` is missing (static binary):**
- **No interpreter:** the kernel does not map `ld-linux.so` into memory.
- **Direct entry:** instead of setting RIP to the loader's `_start`, the kernel sets it directly to the binary's entry point (`e_entry` from the ELF header).
- **The new beginning:** execution usually begins at `_start` (from the startup object — `crt1.o` for classic static builds), which sets up the stack and calls `main`.

There is no PLT indirection into shared libraries and no loader to wait for. (One nuance for the pedantic: even fully static glibc binaries perform a small amount of startup self-fixup — `R_X86_64_IRELATIVE` relocations for IFUNC symbols like the optimized `memcpy` variants — and a *static-PIE* binary relocates itself the way `ld.so` does, with no loader involved.) To a first approximation, though: the CPU just jumps straight into your code.

Everything we have covered so far happens before `main()` starts. But sometimes you need to load code *after* the program is already running: plugins, optional features, or hot-loaded extensions. This is what `dlopen` and `dlsym` provide. See [Appendix H: Runtime Loading (dlopen/dlsym)](#appendix-h-runtime-loading-dlopendlsym) for how the loader handles this and why it reuses much of the same machinery we have already seen.

---

## Part VII: The Payoff — Both Errors, Solved

We opened with two production errors and a promise. Everything needed to keep it is now on the table.

### 7.1 `version 'GLIBC_2.34' not found`

Let's manufacture the error honestly: build the demo on a **newer** distro, run it on an **older** one.

```bash
# build on ubuntu:24.04 (glibc 2.39) ... then run on ubuntu:20.04 (glibc 2.31):
root@ubuntu20:/code# ./dynamic_app_glibc234
./dynamic_app_glibc234: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.34'
    not found (required by ./dynamic_app_glibc234)
```

Where did the binary get the nerve to *demand* a specific glibc version? From the version tables we've been stepping around all post. Every dynamic symbol can carry a **version requirement**; `readelf -V` reads them straight out of our own default build:

```bash
root@container:/code# readelf -V ./dynamic_app
Version symbols section '.gnu.version' contains 8 entries:
  000:   0 (*local*)       2 (GLIBC_2.34)    1 (*global*)      1 (*global*)
  004:   1 (*global*)      1 (*global*)      3 (GLIBC_2.2.5)   3 (GLIBC_2.2.5)

Version needs section '.gnu.version_r' contains 1 entry:
  000000: Version: 1  File: libc.so.6  Cnt: 2
  0x0010:   Name: GLIBC_2.2.5  Flags: none  Version: 3
  0x0020:   Name: GLIBC_2.34  Flags: none  Version: 2
```

Read it as a contract: *"I need `libc.so.6`, and from it I need symbols at version `GLIBC_2.2.5` (that's `sleep`) and `GLIBC_2.34` (that's `__libc_start_main`)."* At link time, the linker recorded the version each symbol had in the libc it linked against — glibc 2.34 restructured its startup symbols, so anything linked against glibc ≥ 2.34 requires `__libc_start_main@GLIBC_2.34`. At load time, the loader checks `.gnu.version_r` against what the target's `libc.so.6` actually exports (`VERDEF` tables), and refuses to start if a required version is missing. The error isn't mystical — it's the loader reading a table we can read ourselves.

**The fix follows from the mechanism:** build against the *oldest* glibc you must support (build in an old container — glibc versions are backward-compatible, not forward), ship the runtime with the binary (containers), or take the loader out of the picture entirely (static linking, Part VI).

### 7.2 `cannot open shared object file`

Our Makefile's history contains this bug on purpose. Watch what happens if you link the "obvious" way — naming the file directly — instead of with `-L. -lmath`:

```bash
root@container:/code# gcc -o dynamic_app_broken main.c ./libmath.so -Wl,-rpath,'$ORIGIN'
root@container:/code# readelf -d ./dynamic_app_broken | grep -E 'NEEDED|RUNPATH'
 0x0000000000000001 (NEEDED)             Shared library: [./libmath.so]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN]

root@container:/# cd / && /code/dynamic_app_broken
/code/dynamic_app_broken: error while loading shared libraries: ./libmath.so:
    cannot open shared object file: No such file or directory
```

There's our second opening error, self-inflicted. The `DT_NEEDED` entry became the literal string `./libmath.so` — and remember the rule from Part III: **a needed name containing `/` is used as a path, and every search mechanism is skipped.** The `RUNPATH [$ORIGIN]` we carefully asked for is dead code; the binary only works when your *current directory* happens to contain the library. It ran fine in `/code` during development, then broke in production the first time someone ran it from anywhere else. Sound familiar?

The fixed link (`-L. -lmath`) stores a bare `NEEDED [libmath.so]`, the search machinery engages, `RUNPATH` expands `$ORIGIN` to the binary's own directory, and it runs from anywhere:

```bash
root@container:/# /code/dynamic_app && echo "runs fine from /"
```

**Triage order for this error in the wild:** `readelf -d` the binary — if `DT_NEEDED` contains a slash, you have this exact bug. If it's a bare name, run with `LD_DEBUG=libs` to watch the search and see which directories were probed (we did exactly this in Part III). Then fix it structurally — `-Wl,-rpath,'$ORIGIN'` for relocatable bundles, or `ldconfig` for system-wide installs — rather than exporting `LD_LIBRARY_PATH` in your shell profile and hoping.

---

## Conclusion: The Full Cycle

1. **Compiler:** generates `main.o` with relocation entries ("holes").
2. **Linker:**
   - injects `Scrt1.o` (the true entry point),
   - weaves `.text` sections together based on the script,
   - synthesizes PLT/GOT for dynamic symbols,
   - patches the holes using the relocation table.
3. Running the application is a dance between user apps (terminal) and the kernel.
4. **Kernel:** maps segments, writes the auxv handshake, and invokes the interpreter (if `PT_INTERP` exists).
5. **Loader:** loads DSOs, applies relocations (eagerly under `-z now`, or lazily via the PLT), locks down RELRO. Calls `_start` → `__libc_start_main` → `main()`.

The "simple" act of running `./app` is a relay race passing the baton between the compiler, linker, kernel, and dynamic loader. And the two errors we started with are just the baton being dropped at two specific hand-offs: a version contract the loader can't satisfy, and a library search that never ran.

That follow-up now exists: [Part 2 — *howtf can a device be both present and not found?*](/blog/split-state-linking/) traces a production incident where this machinery failed at scale — two collective communication libraries in one binary, a symbol collision that silently split one library's state into two live copies, and RDMA devices that were present, registered, and "not found." The resolution pipeline we just traced is the key to that diagnosis.


## Appendices

Evidence lockers: the full dumps and gnarlier details the body text points at. Skip freely; return when a claim needs its receipts.

### Appendix A: The Cross-Architecture Magic (Rosetta & QEMU)

If you ran this lab on an Apple Silicon Mac (M1/M2/M3) or a Windows ARM machine, you likely noticed that the x86-64 binary simply executed. It didn't crash, and it didn't require a manual emulator command.

Three pieces coordinate to make it happen:

- a translation or emulation layer (Rosetta or QEMU),
- the container/VM runtime (e.g., Docker Desktop / WSL2 / Apple's Virtualization Framework),
- and the Linux kernel's [`binfmt_misc`](https://docs.kernel.org/admin-guide/binfmt-misc.html) dispatch mechanism.


#### 1) The Architecture Gap

Our host CPU speaks a different ISA than the guest binary. There are two broad approaches:

- **Emulation (QEMU-style):** interpret/translate instructions and emulate architectural effects in software.
- **Translation (Rosetta-style):** translate blocks of guest instructions into host instructions and cache/execute the translations.

Either way, the translator must preserve **architectural semantics**, not just instruction-by-instruction behavior. One example is **memory ordering**: x86's memory model is stronger (often described as TSO-like) than ARM's default. Translators must ensure the program observes x86-legal outcomes, which can require extra ordering constraints (i.e. inserting memory barriers) in the generated code or other clever mechanisms. That can affect performance.


* **Windows (QEMU Emulation):** On Windows ARM, Docker commonly runs Linux containers inside a Linux VM (via WSL2). Cross‑arch support is frequently implemented by registering QEMU handlers with `binfmt_misc`, so that when the kernel encounters an x86‑64 ELF, it transparently invokes a QEMU interpreter (e.g., `qemu-x86_64`) to run it.

* **macOS (Rosetta + Hardware TSO):** On macOS, Docker Desktop runs Linux containers inside a lightweight Linux VM and can integrate Rosetta into that VM so x86‑64 Linux binaries can run on Apple Silicon. Apple solved the memory ordering bottleneck at the silicon level. Their M-series chips include a hardware switch to enable **Total Store Ordering (TSO)**. This allows the Rosetta translator to run without the heavy software barrier overhead, achieving near-native speeds.


#### 2) How Rosetta Gets into the VM (VirtioFS Injection)

The Linux kernel inside our Docker VM does not ship with Rosetta. It is injected from macOS. Docker uses the **Apple Virtualization Framework (AVF)** to create the Linux VM. AVF exposes a specialized directory share called [`VZLinuxRosettaDirectoryShare`](https://developer.apple.com/documentation/virtualization/vzlinuxrosettadirectoryshare). This is not a standard network share; it is a high-performance channel handled via [**VirtioFS**](https://virtio-fs.gitlab.io/) (Virtual I/O File System).

When the VM boots, it detects this share and mounts it (usually to `/run/rosetta`). This makes the macOS `rosetta` binary visible and executable inside the Linux VM.

#### 3) The Registration Command

Regardless of whether we use QEMU or Rosetta, the *Linux Kernel* mechanism is identical. It uses **`binfmt_misc`** (Binary Formats Miscellaneous).

When Docker Desktop starts (before our container is even created), its internal boot process sends a registration command to the kernel. Something equivalent of:

```bash
echo ':rosetta:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x3e\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:/run/rosetta/rosetta:POCF' > /proc/sys/fs/binfmt_misc/register
```

(modeled on the registration example in [Apple's documentation](https://developer.apple.com/documentation/virtualization/running-intel-binaries-in-linux-vms-with-rosetta))

The kernel matches the file header against these bytes. The crucial part that identifies x86-64 is at **Offset 18**, which is `0x3e`.

To verify this ourselves on an M1 with Docker Desktop (after making sure Rosetta is enabled in Settings):

```bash
# Start a privileged container
❯ docker run --rm -it --privileged ubuntu:22.04 bash

# Mount the binfmt filesystem
root@container:/# mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc

# Inspect the configuration
root@container:/# cat /proc/sys/fs/binfmt_misc/rosetta

interpreter /run/rosetta/rosetta
flags: POCF
magic 7f454c4602010100000000000000000002003e00
```

The POCF flags are documented in the [kernel binfmt_misc docs](https://docs.kernel.org/admin-guide/binfmt-misc.html): **P** (preserve argv[0]), **O** (open binary, pass an open fd to the interpreter), **C** (credentials, use the binary's credentials, not the interpreter's), and **F** (fix binary, keep the interpreter loaded so it works even inside mount namespaces/containers).




### Appendix D: Segments Deep Dive

The full program-header dump behind Part II, plus the process's actual memory map.

<details>
<summary>readelf -lW ./dynamic_app (full output)</summary>

```bash
root@container:/code# readelf -lW ./dynamic_app

Elf file type is DYN (Position-Independent Executable file)
Entry point 0x1080
There are 13 program headers, starting at offset 64

Program Headers:
  Type           Offset   VirtAddr           PhysAddr           FileSiz  MemSiz   Flg Align
  PHDR           0x000040 0x0000000000000040 0x0000000000000040 0x0002d8 0x0002d8 R   0x8
  INTERP         0x000318 0x0000000000000318 0x0000000000000318 0x00001c 0x00001c R   0x1
      [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
  LOAD           0x000000 0x0000000000000000 0x0000000000000000 0x000670 0x000670 R   0x1000
  LOAD           0x001000 0x0000000000001000 0x0000000000001000 0x0001a5 0x0001a5 R E 0x1000
  LOAD           0x002000 0x0000000000002000 0x0000000000002000 0x0000e4 0x0000e4 R   0x1000
  LOAD           0x002d90 0x0000000000003d90 0x0000000000003d90 0x000280 0x000288 RW  0x1000
  DYNAMIC        0x002da0 0x0000000000003da0 0x0000000000003da0 0x000210 0x000210 RW  0x8
  NOTE           0x000338 0x0000000000000338 0x0000000000000338 0x000030 0x000030 R   0x8
  NOTE           0x000368 0x0000000000000368 0x0000000000000368 0x000044 0x000044 R   0x4
  GNU_PROPERTY   0x000338 0x0000000000000338 0x0000000000000338 0x000030 0x000030 R   0x8
  GNU_EH_FRAME   0x002004 0x0000000000002004 0x0000000000002004 0x000034 0x000034 R   0x4
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RW  0x10
  GNU_RELRO      0x002d90 0x0000000000003d90 0x0000000000003d90 0x000270 0x000270 R   0x1

 Section to Segment mapping:
  Segment Sections...
   00     
   01     .interp 
   02     .interp .note.gnu.property .note.gnu.build-id .note.ABI-tag .gnu.hash .dynsym .dynstr .gnu.version .gnu.version_r .rela.dyn .rela.plt 
   03     .init .plt .plt.got .plt.sec .text .fini 
   04     .rodata .eh_frame_hdr .eh_frame 
   05     .init_array .fini_array .dynamic .got .data .bss 
   06     .dynamic 
   07     .note.gnu.property 
   08     .note.gnu.build-id .note.ABI-tag 
   09     .note.gnu.property 
   10     .eh_frame_hdr 
   11     
   12     .init_array .fini_array .dynamic .got
```

</details>

This output confirms that modern binaries are far more complex than the simple "Code vs. Data" model. The linker has split our binary into **4 distinct memory regions (LOAD segments)** to maximize security and efficiency.

#### Explanation of the Output

**1. The Header: `DYN (Position-Independent Executable)`**
This confirms our binary is a **PIE**. It has no fixed address. The Kernel will choose a random base address (ASLR) at runtime, and all `VirtAddr` values below (like `0x1000`) are just offsets relative to that random base.

**2. The `INTERP` Header**

```text
INTERP ... Requesting program interpreter: /lib64/ld-linux-x86-64.so.2

```

This is the first thing the kernel looks for. If found, the kernel maps this interpreter into memory and passes control to it.

**3. The `LOAD` Segments (The Real Memory Map)**

These 4 segments tell the Kernel exactly how to set up the Virtual Memory Areas (VMAs).

| Segment | Flags | Offset | Content | Purpose |
|---------|-------|--------|---------|---------|
| **LOAD #1** (Metadata) | `R` | `0x000` | ELF Header, Program Headers, dynamic linking metadata (`.hash`, `.dynsym`) | Needed by the Loader, but should never be executed (security) or written to (integrity). |
| **LOAD #2** (Code) | `R E` | `0x1000` | `.text` (your code), `.init`, `.plt` | The **only** region where the CPU can fetch instructions. Executing code anywhere else triggers an NX fault. |
| **LOAD #3** (Constants) | `R` | `0x2000` | `.rodata` (string literals, constants), `.eh_frame` (unwind info) | Separated from executable code to prevent ROP gadgets from using data bytes as instructions. |
| **LOAD #4** (Data) | `RW` | `0x3d90` | `.data` (globals), `.bss`, **GOT** (Global Offset Table) | The only writable memory. Backed by the file on disk until written, then Copy-on-Write kicks in. |



**4. The `GNU_RELRO` Segment (Security)**

```text
GNU_RELRO      0x...2d90 ... Flags R

```

This is a security overlay. Notice that its address (`0x2d90`) overlaps with the start of the **LOAD #4 (RW)** segment. See the [RELRO section in Appendix F](#relro-relocation-read-only-partial-vs-full) for more details.



**5. `GNU_STACK` (NX Bit)**

```text
GNU_STACK ... Flags RW

```

The absence of the `E` flag here is critical. It tells the Kernel: "The stack is for data, not code." This prevents code-injection attacks on the stack.



Once the app starts running (that `sleep(60)` in `main.c` exists precisely so the process sticks around), we can read where everything actually landed. One honesty note: this capture comes from the emulated (Rosetta) container, where the kernel handed us `0x555555554000` — the canonical *no-randomization* PIE base — on every run. On native x86-64 Linux you'll see a different `0x55...` bias per run; that per-run difference is ASLR, and `load_bias` from Section 2.2 is whatever the kernel picked. The *structure* below is identical either way: each LOAD segment became a VMA at `load_bias + p_vaddr`.

<details>
<summary>/proc/$pid/maps (rows for our binary, libmath, libc, ld-linux, stack)</summary>

```bash
root@container:/code# ./dynamic_app & pid=$!
root@container:/code# grep -E 'dynamic_app|libmath|libc|ld-linux|stack' /proc/$pid/maps
555555554000-555555555000 r--p 00000000 00:2d 86                         /code/dynamic_app
555555555000-555555556000 r-xp 00001000 00:2d 86                         /code/dynamic_app
555555556000-555555557000 r--p 00002000 00:2d 86                         /code/dynamic_app
555555557000-555555558000 r--p 00002000 00:2d 86                         /code/dynamic_app
555555558000-555555559000 rw-p 00003000 00:2d 86                         /code/dynamic_app
7fffff590000-7fffff5b8000 r--p 00000000 00:50 5451                       /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff5b8000-7fffff74d000 r-xp 00028000 00:50 5451                       /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff74d000-7fffff7a5000 r--p 001bd000 00:50 5451                       /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff7a5000-7fffff7a6000 ---p 00215000 00:50 5451                       /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff7a6000-7fffff7aa000 r--p 00215000 00:50 5451                       /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff7aa000-7fffff7ac000 rw-p 00219000 00:50 5451                       /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff7bc000-7fffff7bd000 r--p 00000000 00:2d 85                         /code/libmath.so
7fffff7bd000-7fffff7be000 r-xp 00001000 00:2d 85                         /code/libmath.so
7fffff7be000-7fffff7bf000 r--p 00002000 00:2d 85                         /code/libmath.so
7fffff7bf000-7fffff7c0000 r--p 00002000 00:2d 85                         /code/libmath.so
7fffff7c0000-7fffff7c1000 rw-p 00003000 00:2d 85                         /code/libmath.so
7ffffffc4000-7ffffffc6000 r--p 00000000 00:50 5433                       /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
7ffffffc6000-7fffffff0000 r-xp 00002000 00:50 5433                       /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
7fffffff0000-7fffffffb000 r--p 0002c000 00:50 5433                       /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
7fffffffc000-7fffffffe000 r--p 00037000 00:50 5433                       /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
7fffffffe000-800000000000 rw-p 00039000 00:50 5433                       /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
ffffedd69000-ffffedd8a000 rw-p 00000000 00:00 0                          [stack]
```

<figure class="frame diagram">
  <span class="frame-title">fig. 4 — the finished address space (from the maps dump above)</span>
  <div class="diagram-body">
    <svg viewBox="0 0 640 360" role="img" aria-label="Vertical virtual-address map: the app's LOAD VMAs at the load bias, then libmath, libc, and ld-linux mapped higher, with the kernel-managed stack at the top">
      <g font-family="var(--font-mono)" font-size="11">
        <text x="20" y="30" fill="var(--muted)">low VA</text>
        <text x="20" y="348" fill="var(--muted)">high VA</text>
        <line x1="150" y1="16" x2="150" y2="352" stroke="var(--border)"/>
        <!-- app -->
        <rect x="170" y="30" width="300" height="66" fill="var(--sec)" opacity="0.14"/>
        <rect x="170" y="30" width="300" height="66" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="320" y="52" text-anchor="middle" fill="var(--sec)">dynamic_app — 5 VMAs, one per LOAD</text>
        <text x="320" y="68" text-anchor="middle" fill="var(--muted)" font-size="10">r-- · r-x · r-- · r-- · rw-  (RELRO turned the GOT page r--)</text>
        <text x="142" y="40" text-anchor="end" fill="var(--muted)" font-size="10">0x555555554000</text>
        <text x="142" y="54" text-anchor="end" fill="var(--sec)" font-size="10">= load_bias</text>
        <!-- gap -->
        <text x="320" y="122" text-anchor="middle" fill="var(--muted)">… unmapped gap: touch it and you get SIGSEGV …</text>
        <!-- libmath -->
        <rect x="170" y="140" width="300" height="44" fill="var(--seg)" opacity="0.14"/>
        <rect x="170" y="140" width="300" height="44" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="320" y="160" text-anchor="middle" fill="var(--seg)">libmath.so — 5 VMAs</text>
        <text x="320" y="176" text-anchor="middle" fill="var(--muted)" font-size="10">mapped by ld.so during dependency discovery</text>
        <text x="142" y="152" text-anchor="end" fill="var(--muted)" font-size="10">0x7fffff7bc000</text>
        <!-- libc -->
        <rect x="170" y="196" width="300" height="44" fill="var(--seg)" opacity="0.14"/>
        <rect x="170" y="196" width="300" height="44" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="320" y="216" text-anchor="middle" fill="var(--seg)">libc.so.6</text>
        <text x="320" y="232" text-anchor="middle" fill="var(--muted)" font-size="10">every dynamic process maps one</text>
        <!-- ld -->
        <rect x="170" y="252" width="300" height="44" fill="var(--ldr)" opacity="0.14"/>
        <rect x="170" y="252" width="300" height="44" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="320" y="272" text-anchor="middle" fill="var(--ldr)">ld-linux-x86-64.so.2</text>
        <text x="320" y="288" text-anchor="middle" fill="var(--muted)" font-size="10">the kernel mapped this one (PT_INTERP) — it mapped everything above</text>
        <text x="142" y="264" text-anchor="end" fill="var(--muted)" font-size="10">0x7ffffffc4000</text>
        <!-- stack -->
        <rect x="170" y="308" width="300" height="34" fill="var(--krn)" opacity="0.14"/>
        <rect x="170" y="308" width="300" height="34" fill="none" stroke="var(--krn)" stroke-width="1.5" stroke-dasharray="5 3"/>
        <text x="320" y="329" text-anchor="middle" fill="var(--krn)">[stack] — kernel-managed, grows down</text>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--sec)"></span>our app</span>
      <span><span class="k" style="background:var(--seg)"></span>mapped libraries</span>
      <span><span class="k" style="background:var(--ldr)"></span>loader</span>
      <span><span class="k" style="background:var(--krn)"></span>kernel</span>
    </p>
  </div>
</figure>

</details>


### Appendix E: The Loader's Bootstrap (Self-Relocation)

In Section 3, we mentioned the loader must "fix itself." Here are the details.

#### The "Chicken and Egg" Problem

Normal programs rely on the loader to fix their addresses before they run. But `ld-linux.so` *is* the loader. Who loads the loader? No one.

When the kernel maps the loader, it just maps segments.

- **ASLR:** loader is at a random address (e.g., `0x7f34...`) instead of its link-time base.
- **Broken GOT:** internal pointers may assume link-time addresses.
- **No libc:** it can't call most libc routines yet.

#### The Solution: `_dl_start`

The entry point passes control to `_dl_start` in `elf/rtld.c`. This function is written with extreme care to avoid accesses that rely on unrelocated global state.

A simplified sketch:

```c
/* elf/rtld.c */
// https://elixir.bootlin.com/glibc/glibc-2.39/source/elf/rtld.c#L517
static ElfW(Addr) __attribute_used__
_dl_start (void *arg)
{
    /* 1. Calculate the load bias */
    ElfW(Addr) l_addr = elf_machine_load_address ();

    /* 2. Apply bootstrap relocations (self-patch) */
    elf_machine_rela (l_addr, ...);   /* x86-64 is a RELA architecture */

    /* 3. Now the loader can safely run complex code */
    return _dl_start_final (arg, ...);
}
```

Step 1 finds the bias (often via RIP-relative tricks). Step 2 applies `R_X86_64_RELATIVE`-style relocations to itself. Once that's done, it becomes a "real program" and can load your app.


### Appendix F: Loader's Relocation Mechanism

#### 1) High-Level Sequence (What We're About to Zoom Into)

1. The loader starts running, but it itself is at a different location due to ASLR than what the linker had in mind.

2. Loader does the self-relocation as explained in [Appendix E](#appendix-e-the-loaders-bootstrap-self-relocation).

3. Now, it looks at the `PT_DYNAMIC` segment of the binary (note that these sections were already mapped as part of `PT_LOAD` `mmap()`ing by the kernel).

4. `PT_DYNAMIC` sort of creates a map of different entries (dynamic tags) that point the loader at the relevant tables/relocation lists:

   `map[DT_STRTAB]` -> address of `.dynstr` (string table)
   `map[DT_SYMTAB]` -> address of `.dynsym` (dynamic symbol table, not .symtab)
   `map[DT_NEEDED]` -> list of libraries to load (actually, its an array of offsets into `.dynstr`, which contains these lib names)
   `map[DT_RUNPATH]` -> provided runpath (again, as an offset into `.dynstr`)
   `map[RELA]` -> address of `.rela.dyn` section, this has all the non-PLT relocations
   `map[JMPREL]` -> address of `.rela.plt` section, this has all the PLT relocations
   and a few more..

<details>
<summary>readelf -p .dynstr and readelf -d (string table + dynamic section)</summary>

```bash
# see how the lib names (libmath.so, libc.so.6) and $ORIGIN live here
root@container:/code# readelf -p .dynstr ./dynamic_app
String dump of section '.dynstr':
  [     1]  __cxa_finalize
  [    10]  _ITM_registerTMCloneTable
  [    2a]  _ITM_deregisterTMCloneTable
  [    46]  __gmon_start__
  [    55]  add
  [    59]  __libc_start_main
  [    6b]  sleep
  [    71]  libmath.so
  [    7c]  libc.so.6
  [    86]  GLIBC_2.2.5
  [    92]  GLIBC_2.34
  [    9d]  $ORIGIN

# inspect dynamic section
root@container:/code# readelf -d ./dynamic_app

Dynamic section at offset 0x2da0 contains 29 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [libmath.so]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN]
 0x000000000000000c (INIT)               0x1000
 0x000000000000000d (FINI)               0x1198
 0x0000000000000019 (INIT_ARRAY)         0x3d90
 0x000000000000001b (INIT_ARRAYSZ)       8 (bytes)
 0x000000000000001a (FINI_ARRAY)         0x3d98
 0x000000000000001c (FINI_ARRAYSZ)       8 (bytes)
 0x000000006ffffef5 (GNU_HASH)           0x3b0
 0x0000000000000005 (STRTAB)             0x498
 0x0000000000000006 (SYMTAB)             0x3d8
 0x000000000000000a (STRSZ)              165 (bytes)
 0x000000000000000b (SYMENT)             24 (bytes)
 0x0000000000000015 (DEBUG)              0x0
 0x0000000000000003 (PLTGOT)             0x3fb0
 0x0000000000000002 (PLTRELSZ)           48 (bytes)
 0x0000000000000014 (PLTREL)             RELA
 0x0000000000000017 (JMPREL)             0x640
 0x0000000000000007 (RELA)               0x580
 0x0000000000000008 (RELASZ)             192 (bytes)
 0x0000000000000009 (RELAENT)            24 (bytes)
 0x000000000000001e (FLAGS)              BIND_NOW
 0x000000006ffffffb (FLAGS_1)            Flags: NOW PIE
 0x000000006ffffffe (VERNEED)            0x550
 0x000000006fffffff (VERNEEDNUM)         1
 0x000000006ffffff0 (VERSYM)             0x53e
 0x000000006ffffff9 (RELACOUNT)          3
 0x0000000000000000 (NULL)               0x0
```

</details>
5. Iterates through `DT_NEEDED` entries. In our case: `libmath.so` and `libc.so.6`, as we can see in the output. (Note the `FLAGS: BIND_NOW` in this default build — the loader will resolve everything up front, per Part III.)


6. For `libmath.so`, the loader runs the search order from Part III — here, `RUNPATH`'s `$ORIGIN` expands to the binary's directory and wins. It `mmap`s the library into the process, performing `libmath`'s own relocations along the way, and does the same for `libc.so.6`. (Had the stored name contained a `/` — like Part VII's broken build — it would have been used as a literal path with no search at all.)

7. Then it will move to doing relocations for your executable. First it will look at `map[RELA]` (`.rela.dyn`) section.

---

#### 2) Relocations for the Main Executable

First, let's see how the relocations information looks in our ELF binary.

<details>
<summary>readelf -rW ./dynamic_app (relocation tables)</summary>

```bash
root@container:/code# readelf -rW ./dynamic_app

Relocation section '.rela.dyn' at offset 0x580 contains 8 entries:

    Offset             Info             Type               Symbol's Value  Symbol's Name + Addend
0000000000003d90  0000000000000008 R_X86_64_RELATIVE                         1160
0000000000003d98  0000000000000008 R_X86_64_RELATIVE                         1120
0000000000004008  0000000000000008 R_X86_64_RELATIVE                         4008
0000000000003fd8  0000000100000006 R_X86_64_GLOB_DAT      0000000000000000 __libc_start_main@GLIBC_2.34 + 0
0000000000003fe0  0000000200000006 R_X86_64_GLOB_DAT      0000000000000000 _ITM_deregisterTMCloneTable + 0
0000000000003fe8  0000000400000006 R_X86_64_GLOB_DAT      0000000000000000 __gmon_start__ + 0
0000000000003ff0  0000000500000006 R_X86_64_GLOB_DAT      0000000000000000 _ITM_registerTMCloneTable + 0
0000000000003ff8  0000000700000006 R_X86_64_GLOB_DAT      0000000000000000 __cxa_finalize@GLIBC_2.2.5 + 0

Relocation section '.rela.plt' at offset 0x640 contains 2 entries:

    Offset             Info             Type               Symbol's Value  Symbol's Name + Addend
0000000000003fc8  0000000300000007 R_X86_64_JUMP_SLOT     0000000000000000 add + 0
0000000000003fd0  0000000600000007 R_X86_64_JUMP_SLOT     0000000000000000 sleep@GLIBC_2.2.5 + 0
```

</details>

It will go through each of the non-PLT relocations (i.e. in .rela.dyn) first.

* Each entry is decoded into `r_info` first: `r_info = index of this symbol into .dynsym (high 32 bits) || relocation type (low 32 bits)`.

* The relocation type decides how to apply the relocation.

* `R_X86_64_RELATIVE` (0x8) are relatively straightforward. It simply says that at this `Offset + load_bias` (remember load_bias from 2.2 Finding the Correct Address for the Segments?), put this value: `addend + load_bias`.

* For `R_X86_64_GLOB_DAT` (0x6), it will look at the symbol in `.dynsym`. `.dynsym` usually contains symbol information like name (sym_name), type, visibility, value (sym_value) etc. For the name it points to an offset in `.dynstr`. Let's see it in action.

* For `__libc_start_main`, it will look at the `.dynsym[1]` entry. 1 because, `r_info = 0000000100000006` (first 32 bits is the index as mentioned before).

As we can see, index 1 has __libc_start_main.
<details>
<summary>readelf -sW --dyn-syms ./dynamic_app (dynamic symbol table)</summary>

```bash
root@container:/code# readelf -sW --dyn-syms ./dynamic_app

Symbol table '.dynsym' contains 8 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND
     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __libc_start_main@GLIBC_2.34 (2)
     2: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND _ITM_deregisterTMCloneTable
     3: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND add
     4: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND __gmon_start__
     5: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND _ITM_registerTMCloneTable
     6: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND sleep@GLIBC_2.2.5 (3)
     7: 0000000000000000     0 FUNC    WEAK   DEFAULT  UND __cxa_finalize@GLIBC_2.2.5 (3)
     ...
```

</details>

  * Loader will see that `__libc_start_main` is not defined (UND, usually indicated by sym_value being 0). It will try to find it. It will check where is this defined. Loader will get the symbol name (fetches it via `.dynstr[dynsym[1].symbol_name]`).
  
  * Each DSO's ELF would have maintained some sort of metadata (hash table) which loader will use to see which DSO defines this symbol. We have not dug deep into this part yet (we know .gnu.hash section does some fancy bloom filter things, but let's keep it for later).

  * Loader will find that `libc.so` defines `__libc_start_main`. It will find its value `sym_value` from `.dynsym` of `libc.so`, will add `base_address_of_libc + sym_value` and return that value. Note that, `.dynsym` contains a subset of symbols for that binary/DSO and not all symbols. It mainly contains the symbols used for import/export. The local symbols are not present/needed in `.dynsym` as they can be already resolved during build.

  * Then it will patch this value (i.e. __libc_start_main's absolute address) at `(load_bias + 0000000000003fd8)` address, which would be a GOT entry for this symbol.

  * Same thing for all the other `R_X86_64_GLOB_DAT` entries.

  * At runtime, the instruction will look like:

```asm
0000000000001080 <_start>:
    1080:   f3 0f 1e fa             endbr64
    1084:   31 ed                   xor    %ebp,%ebp
    1086:   49 89 d1                mov    %rdx,%r9
    ...
    1098:   48 8d 3d d9 00 00 00    lea    0xd9(%rip),%rdi        # 1178 <main>
    109f:   ff 15 33 2f 00 00       call   *0x2f33(%rip)        # 3fd8 <__libc_start_main@GLIBC_2.34>
    ...
```

* this would automatically make a call to the address stored at relative `0x3fd8` address, which would now have absolute address of `__libc_start_main` in the process image.

- Loader will go through PLT relocations next. `map[JMPREL]` points to `.rela.plt` table. Same way, we will go through these entries. For eager binding (when LD_BIND_NOW=1 is set) we will do the relocations at the startup time; for lazy binding (default) this sequence will take place at runtime when the first call is made. Regardless, the same sequence of events take place. `R_X86_64_JUMP_SLOT` type of entry indicates PLT entry.

---

#### 3) The Lazy-Binding Path: the Full Evidence

Part III (§3.4) watches lazy binding happen live with `got_watch`. Here is the complete static evidence from the `-Wl,-z,lazy` build, so every number in that walkthrough can be checked against the file on disk.

The PLT relocations the resolver will service:

```text
Relocation section '.rela.plt' at offset 0x640 contains 2 entries:
    Offset             Info             Type               Symbol's Value  Symbol's Name + Addend
0000000000004018  0000000300000007 R_X86_64_JUMP_SLOT     0000000000000000 add + 0
0000000000004020  0000000600000007 R_X86_64_JUMP_SLOT     0000000000000000 sleep@GLIBC_2.2.5 + 0
```

The PLT machinery itself (note the modern `endbr64`/`bnd` — that's CET/IBT hardening — and the `.plt.sec` split):

<details>
<summary>objdump -d dynamic_app_lazy, .plt and .plt.sec sections</summary>

```text
./dynamic_app_lazy:     file format elf64-x86-64


Disassembly of section .plt:

0000000000001020 <.plt>:
    1020:	ff 35 e2 2f 00 00    	push   0x2fe2(%rip)        # 4008 <_GLOBAL_OFFSET_TABLE_+0x8>
    1026:	f2 ff 25 e3 2f 00 00 	bnd jmp *0x2fe3(%rip)        # 4010 <_GLOBAL_OFFSET_TABLE_+0x10>
    102d:	0f 1f 00             	nopl   (%rax)
    1030:	f3 0f 1e fa          	endbr64 
    1034:	68 00 00 00 00       	push   $0x0
    1039:	f2 e9 e1 ff ff ff    	bnd jmp 1020 <_init+0x20>
    103f:	90                   	nop
    1040:	f3 0f 1e fa          	endbr64 
    1044:	68 01 00 00 00       	push   $0x1
    1049:	f2 e9 d1 ff ff ff    	bnd jmp 1020 <_init+0x20>
    104f:	90                   	nop

Disassembly of section .plt.sec:

0000000000001060 <add@plt>:
    1060:	f3 0f 1e fa          	endbr64 
    1064:	f2 ff 25 ad 2f 00 00 	bnd jmp *0x2fad(%rip)        # 4018 <add@Base>
    106b:	0f 1f 44 00 00       	nopl   0x0(%rax,%rax,1)

0000000000001070 <sleep@plt>:
    1070:	f3 0f 1e fa          	endbr64 
    1074:	f2 ff 25 a5 2f 00 00 	bnd jmp *0x2fa5(%rip)        # 4020 <sleep@GLIBC_2.2.5>
    107b:	0f 1f 44 00 00       	nopl   0x0(%rax,%rax,1)
```

</details>

And the initial contents of `.got.plt` in the file, before the loader has touched anything — the slot for `add` (vaddr `0x4018`) holds `0x1030`, the address of its own PLT push-stub:

```text
Hex dump of section '.got.plt':
 NOTE: This section has relocations against it, but these have NOT been applied to this dump.
  0x00004000 d83d0000 00000000 00000000 00000000 .=..............
  0x00004010 00000000 00000000 30100000 00000000 ........0.......
  0x00004020 40100000 00000000                   @.......
```

So the chain at first call is exactly: `call add@plt` → `jmp *slot(0x4018)` → lands back at `0x1030` → `push $0x0` (the relocation index) → common stub at `0x1020` → `_dl_runtime_resolve`, which finds `add` in `libmath.so`, patches slot `0x4018`, and jumps there. Every later call short-circuits: `call add@plt` → `jmp *slot` → `add`.

(If you want to poke this in gdb yourself, run it on native x86-64 Linux — under Rosetta emulation `ptrace` is unavailable, which is why the in-process `got_watch` approach exists.)

#### RELRO (RELocation Read-Only): partial vs full

At the end of relocation processing the loader `mprotect`s the `GNU_RELRO` region read-only. How much protection that buys depends entirely on the binding mode — compare our two builds:

```text
# default build (-z now, eager):
GNU_RELRO      0x002d90 0x0000000000003d90 0x0000000000003d90 0x000270 0x000270 R   0x1
# lazy build (-Wl,-z,lazy):
GNU_RELRO      0x002dc8 0x0000000000003dc8 0x0000000000003dc8 0x000238 0x000238 R   0x1
```

Both regions end at `0x4000`. In the **eager** build the `R_X86_64_JUMP_SLOT` entries live at `0x3fc8`/`0x3fd0` — *inside* the region. Everything is resolved before `main()` runs, so the loader can seal the entire GOT: **full RELRO**. In the **lazy** build the jump slots live at `0x4018`/`0x4020` — *past the end*. They must stay writable so the resolver can patch them at first call: **partial RELRO**.

The security consequence, stated precisely: under full RELRO, a memory-corruption bug cannot overwrite GOT entries to hijack library calls, because those pages are read-only by the time user code runs. Under partial RELRO, the *data* GOT is sealed but `.got.plt` remains writable for the process's lifetime — and a writable function-pointer table that the program jumps through is the canonical GOT-overwrite target. That trade — startup latency vs a locked GOT — is exactly why hardened distros ship `-z now` by default.

- Once these relocations are done, we are ready to handoff to `_start`.


### Appendix G: The Assembly Handoff (_start)

In Section 4, we glossed over the assembly handoff. Here are the exact mechanics of how the loader passes control to the user.

#### 1) The Exit Stub (`_dl_start_user`)

The loader is written in C, but the final handoff requires assembly to manipulate registers precisely. This happens in architecture-specific glue (e.g., `sysdeps/x86_64/dl-machine.h` in glibc).

A schematic flow:

```asm
_dl_start_user:
    mov %rsp, %rdi         # Save stack pointer (argc/argv live here)
    call _dl_init          # Run init functions for DSOs
    jmp *%r12              # Jump to user entry point (_start)
```

#### 2) The User Entry Point (`_start`)

The CPU lands at `_start`. This is provided by `Scrt1.o` (the PIE variant of `crt1.o`). Its primary job is to align the stack (16‑byte alignment required by the x86‑64 ABI) and set up arguments for `__libc_start_main`.

Conceptually:

```asm
_start:
    xor %ebp, %ebp         # End-of-stack marker for debuggers
    pop %rsi               # argc
    mov %rsp, %rdx         # argv
    and $-16, %rsp         # align stack
    call __libc_start_main
```

See the [exact](https://elixir.bootlin.com/glibc/glibc-2.42.9000/source/sysdeps/x86_64/start.S#L57) source code. Then `__libc_start_main` runs constructors for this binary (remember that the loader (`_dl_init`) already initialized shared libraries. `__libc_start_main` only runs constructors for the main executable) and [calls our `main`](https://elixir.bootlin.com/glibc/glibc-2.42.9000/source/sysdeps/nptl/libc_start_call_main.h#L58).


### Appendix H: Runtime Loading (dlopen/dlsym)

Everything in the main article happens before `main()` starts. But many real programs need to load code later: a web server that loads authentication modules on demand, a game engine that loads renderer backends based on the GPU it detects, or a language runtime loading compiled extensions. The mechanism for this is `dlopen` and `dlsym`.

#### 1) Loading a Library After Startup

Suppose your program has an optional plugin system. At runtime, you decide to load a plugin:

```c
void *handle = dlopen("./libplugin.so", RTLD_LAZY);
```

Under the hood, this calls back into the same dynamic loader (`ld-linux.so`) that set up your process at startup. The loader finds `libplugin.so`, maps it into the process's address space with `mmap`, resolves its dependencies (if `libplugin.so` itself depends on other libraries), and performs relocations, the same machinery we saw in [Appendix F](#appendix-f-loaders-relocation-mechanism), just happening after `main()` instead of before it.

#### 2) Initialization: Why `dlopen` Can Be Slow (or Crash)

Before `dlopen` returns, the loader must run the constructors (`.init_array`) of `libplugin.so` and all of its dependencies. This is the same initialization step the loader performs for startup libraries, but it happens synchronously inside your `dlopen` call.

This has a practical consequence: if `libplugin.so` contains a C++ global like `MyClass instance;`, that constructor runs inside `dlopen`. If it crashes, allocates a lot of memory, or takes a long time, your `dlopen` call inherits that behavior. The library must be fully initialized before you get the handle back.

#### 3) Looking Up Symbols (`dlsym`)

Once `dlopen` returns successfully, you have an opaque handle. Internally, this is a pointer to the `link_map` structure the loader created when it mapped the library, the same structure it uses to track every shared library in the process.

To call a function from the loaded library:

```c
void (*func)() = dlsym(handle, "run_plugin");
func();
```

The loader searches starting from that `link_map` — the object itself and its dependency subtree, not just the single object — and returns the memory address of `run_plugin`. From this point on, you call `func()` like any other function pointer.

This is conceptually how Python loads C extensions: `import numpy` eventually triggers a `dlopen` on the compiled NumPy shared object, and `dlsym` is used to find the entry points that bridge Python calls to the C implementation.

