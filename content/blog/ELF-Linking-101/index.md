---
title: "The Architecture of Execution: A Deep Dive into ELF, Linking, and Loading"
---

**From the terminal to `main()` ... and back to the source.**

We have all been there. You deploy a binary that worked perfectly on your development machine, but the production environment crashes with:

`/lib64/libc.so.6: version 'GLIBC_2.34' not found`

or

`error while loading shared libraries: libfoo.so: cannot open shared object file`

You do a frantic search through your favorite LLMs, cross-verifying responses, blindly pasting `export LD_LIBRARY_PATH=` commands, and installing random packages until the error disappears. We often treat the execution process as a black box, something that "just works" until it doesn't. These errors are symptoms of a system most engineers never look at closely, and that lack of understanding compounds when you are debugging at scale.

In this post, we will take a different approach. We will trace the life of a command starting at the Runtime, from the moment you hit `Enter` in your terminal until it reaches `main()`. We will observe the coordination of the Kernel, Linker, and Loader that transforms a simple binary file on disk into a living, breathing process.

**Scope & assumptions.** This walkthrough uses **Linux on x86‑64** as the concrete reference, with the **glibc dynamic loader** (`ld-linux-x86-64.so.2`) as "the loader" we talk about. The big ideas transfer to other architectures and libcs, but some details (relocation types, syscall entry, loader internals, memory-ordering constraints etc.) might differ.

**Who is this for?** If you have ever wondered what actually happens between hitting Enter and your code running, this is for you. Some comfort with C helps, and we will touch on assembly and kernel internals in places, but the main narrative is designed to be followed without deep expertise in either. The appendices are where the really gnarly details live.


---

## Follow Along

We will use a standard Linux environment. If you are on macOS or Windows, use Docker Desktop to get deterministic userspace behavior (specifically for x86‑64 relocation types).


> **A Note on Architecture (Apple Silicon & Windows ARM):**
> If you are running on an ARM chip (M1/M2/M3, etc), you can still follow along.
>- **macOS:** Docker Desktop can run `linux/amd64` containers using Rosetta‑based translation wired through `binfmt_misc` when configured to do so. This is [documented by Apple](https://developer.apple.com/documentation/virtualization/running-intel-binaries-in-linux-vms-with-rosetta) and by Docker Desktop [settings](https://docs.docker.com/desktop/features/vmm/).
>- **Windows (ARM):** the common mechanism for running `linux/amd64` binaries under an ARM64 Linux environment (including WSL2-based backends) is **QEMU user-mode emulation** wired through Linux's `binfmt_misc`. Whether it's already configured "out of the box" depends on the Docker/WSL2 setup, versions, and registration state, but most likely it is.
>
> Curious how this cross-architecture magic works under the hood? See *[Appendix A](#appendix-a-the-cross-architecture-magic-rosetta--qemu)*.


**1. The Source Files**

Look at demo code. It has all the files. My local directory looks like this:

```bash
❯ ls code
main.c   Makefile math.c


❯ cat main.c
// main.c
extern int add(int, int);

int main(void) {
    return add(5, 10);
}

❯ cat math.c
// math.c
int add(int a, int b) {
    return a + b;
}

❯ cat Makefile
all: libmath.so dynamic_app
libmath.so: math.c
    gcc -shared -fPIC -o libmath.so math.c
dynamic_app: main.c libmath.so
	gcc -o dynamic_app main.c ./libmath.so -Wl,-rpath,'$$ORIGIN'
```

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
root@container:/code# make dynamic_app
gcc -shared -fPIC -o libmath.so math.c
gcc -o dynamic_app main.c ./libmath.so -Wl,-rpath,'$ORIGIN'

root@container:/code# ls
Makefile  dynamic_app  libmath.so  main.c  math.c
```

---

You type `./dynamic_app` and hit Enter.

Your shell calls `fork()` to create a child process. That child process calls `execve("./dynamic_app")`, and your app starts running. Simple enough? Oh well..

---

## Part I: The Hardware Gate and Kernel Entry

### 1.1 The Wake Up

Your shell (bash/zsh) was actually asleep, blocked on a `read()` system call waiting for input. The kernel, tty, keyboard driver, etc. work together to let your shell know exactly what command the user executed.

> (See **[Appendix B: The Keyboard Dance](#appendix-b-the-keyboard-dance-tty-architecture)** for the deep dive on TTYs and PTYs).

### 1.2 The `fork()` syscall (cloning)

The shell parses your command and decides to run a new program. But first, it must duplicate itself. It calls `fork()`.

This triggers a hardware transition.

1. **The Trap:** The CPU executes the syscall instruction (opcode `0F 05`).
2. **The Switch:** The hardware instantly elevates privileges to Ring 0.
3. **The Lookup:** It consults the Model Specific Registers (MSRs) to jump straight into the kernel's entry point (`entry_SYSCALL_64`) after saving current state of the user code on the stack.

> *(For the hardcore details on IDTs, MSRs, and the "Hidden Storm" of context switching, see **[Appendix C: Under the Hood](#appendix-c-under-the-hood-idt-msrs--syscalls)**).*

It creates a near‑identical copy of the shell (the child process). In practice, the kernel does not duplicate physical memory. It marks all pages as copy‑on‑write (COW), so the two processes share the same physical pages until one of them writes. This child is now running, but it is still running the shell's code.

### 1.3 The `execve` Syscall

The transition for the syscall remains the same as fork, but the handler will be different. execve kernel handler discards the child's old memory map (the shell code) and prepares to load the new binary.

The Operating System has taken the wheel. It is now sitting in Ring 0 with the file path ./dynamic_app and a mandate to start executing it.

### 1.4 Inside the Kernel: `fs/exec.c`

Once inside the kernel, execution eventually reaches `do_execveat_common` in [fs/exec.c](https://elixir.bootlin.com/linux/v6.8/source/fs/exec.c#L1908).

The kernel opens the file and iterates through a list of "binary handlers" to find one that understands the file format. Since this is an ELF file, it lands in `load_elf_binary` in [fs/binfmt_elf.c](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L819).

### 1.5 The Magic Check

First, the kernel validates that this is actually an ELF file. It reads the first 4 bytes. If they aren't `0x7F 'E' 'L' 'F'`, it rejects the file immediately.

```c
// linux/fs/binfmt_elf.c (https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L843)
if (memcmp(loc->elf_ex.e_ident, ELFMAG, SELFMAG) != 0)
    goto out;
```

---

## Part II: Mapping the Memory

The kernel does **not** care about "sections" (like `.text` or `.data`). Those are build/link time constructions, mainly for the linker. The kernel cares about **segments** (Program Headers), which tell the kernel what exactly to load and where.

### 2.1 Iterating Segments (`load_elf_binary`)

The kernel loops over the program headers (`PT_LOAD`) to figure out what to map. ([View Source in `binfmt_elf.c`](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L1066))

```c
// Simplified logic from fs/binfmt_elf.c
for(i = 0, elf_ppnt = elf_phdata; i < loc->elf_ex.e_phnum; i++, elf_ppnt++) {
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
3. **Read-Only Data (`R`):** Constants (`.rodata`) and unwind info. Separated from code to prevent ROP attacks.
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
  Entry point address:               0x1060
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

```c
// (pseudo-code)
if (elf_ppnt->p_type == PT_INTERP) {
    elf_interpreter = open_exec(interp_name); // e.g., /lib64/ld-linux-x86-64.so.2
    ...
        load_elf_binary(..., interpreter); // Recursively map the interpreter!
}
```

Because `dynamic_app` has this header, the kernel maps the dynamic loader (`ld-linux.so`) into memory and sets the instruction pointer to the *loader's* entry point, not your dynamic_app's entry point. ([View Source](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L1200))

---

## Part III: The Loader Takes Control (User Mode)

Control returns to User Mode. The program running is now the dynamic loader (`ld-linux.so`), appearing in `glibc` source as `elf/rtld.c`.

### 3.1 Self-Relocation (The Bootstrap)

The loader itself is also just a program, just a bit special one as it wakes up in a hostile environment. Because of ASLR, it has been loaded at a random address, meaning all its internal pointers to global variables are wrong. It cannot call functions or access static data yet. Before it can do anything else, the loader must fix these addresses. This happens in the `_dl_start` path. See [Appendix E: The Loader's Bootstrap](#appendix-e-the-loaders-bootstrap-self-relocation) for more details.

### 3.2 Dependency Discovery

Once the loader has healed itself, it becomes a fully functional C program running inside your process. It can now inspect your `dynamic_app`. It reads the `PT_DYNAMIC` segment to find `DT_NEEDED` tags, then recursively finds `libmath.so` and `libc.so.6` (checking `RUNPATH`/`RPATH`, `LD_LIBRARY_PATH`, and caches), and maps them into the current process's memory space using `mmap`.


### 3.3 Visualizing the Scaffolding (Procedure Linkage Table (PLT) & Global Offset Table (GOT))


Finally, the loader prepares the mechanism that lets your code call functions outside this executable (like libc routines, or add from libmath)

It populates the GOT based on the finally loaded addresses. The GOT acts as a cache for addresses that must be resolved at runtime. I wish it was as simple as it sounds and you are welcome to read [Appendix F: Loader's Relocation Mechanism](#appendix-f-loaders-relocation-mechanism) for all the gory relocation related details, where we go through the sequence of events in detail.



## Part IV: The Handoff (Loader → User)
The loader is now ready to hand control to your application. But it doesn't just call `main()`. In fact, it doesn't even know `main` exists.

The transition from the loader to your code happens in three steps.

**1: The loader's exit ([`_dl_start_user`](https://elixir.bootlin.com/glibc/glibc-2.42.9000/source/sysdeps/x86_64/dl-machine.h#L144))** 
First, the loader runs the constructors (`.init` / `.init_array`) for all shared libraries (e.g., `libmath.so`) to ensure they are ready. 

**2: The application's entry (`_start`)**  
The CPU lands at a function called `_start`. This is not your code. It is a small assembly stub provided by the C runtime (`crt1.o`) that was linked into your binary at build time. Its job is to set up the stack and pass arguments (`argc`, `argv`) to the C library helper __libc_start_main. __libc_start_main is the one that runs the constructors for your executable and finally calls your main.

(Curious what this assembly looks like? See [Appendix G: The Assembly Handoff](#appendix-g-the-assembly-handoff-_start).)



## Part V: The Flashback (Build Time)

When we run `gcc -c main.c`, GCC acts as a driver. It runs `cc1` (compiler) and `as` (assembler) to produce `main.o`.

At this stage, the compiler does not know where `add` is. It creates a relocation entry, basically a "to‑do" note for the linker.

Let's inspect `main.o`'s relocation table:

```bash
root@container:/code# readelf -r main.o
```

**Output (example):**

```text
Relocation section '.rela.text' at offset 0xc8 contains 1 entry:
  Offset          Info           Type           Sym. Value    Sym. Name + Addend
00000000000e  000b00000004 R_X86_64_PLT32    0000000000000000 add - 4
```

- **Offset `0x0e`:** the exact byte in the `.text` section where the `call` instruction argument sits.
- **Type `R_X86_64_PLT32`:** tells the linker: "I need a 32-bit PC-relative address to a PLT entry for symbol `add`."

### 5.1 The Hidden Startup Files

In Section 4 we saw that the real entry point is `_start`, not `main()`, and that it comes from a file called `crt1.o`. But we never asked GCC to link that file. Where did it come from?

When you run `gcc`, it silently injects several startup objects provided by glibc: `crt1.o` (which contains `_start`), `crti.o` (init prologue), and `crtn.o` (init epilogue). The naming is historical: the original was called `crt0.o` (C RunTime, file zero), and the split into multiple files came later as initialization grew more complex.

You can see this hidden injection by running GCC with verbose flags:

```bash
root@container:/code# gcc -v -o dynamic_app main.o ./libmath.so 2>&1 | grep collect2
```

You will see `crt1.o` passed to the linker command line automatically, even though you never mentioned it.

### 5.2 The Linker (`ld`)

Now `ld` runs. It has `main.o`, `crt1.o`, and `libmath.so`. It needs to create one file.

#### Step 1: The Blueprint (Linker Script)

The linker follows a script to decide memory layout.

```bash
root@container:/code# ld --verbose | grep -A 5 "SECTIONS"
```

Among many other directives, it tells the linker things like: "collect all input `.text` sections into one output `.text` section, all `.rodata` into one `.rodata`," and so on. It also defines segment boundaries, alignment, and the order things appear in the final binary.

#### Step 2: Weaving Sections Together

The linker maps the output file into memory (using `mmap`). It then performs a "scatter-gather" copy.

1. It copies `crt1.o`'s `.text` to the beginning of the output buffer.
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
Offset 0x0e    Type R_X86_64_PLT32    Symbol: add    Addend: -4
```

The linker now processes this. It does not scan the machine code looking for call instructions. It walks the `.rela.text` table, and for each entry it knows exactly which byte to patch and how.

For our `add` entry, the process is:

1. The linker looks at the **Offset** (`0x0e`). That is where the placeholder bytes sit inside `.text`, right where the `call` instruction expects its target.
2. It knows from Step 3 that `add@plt` now lives at some address in the PLT section.
3. It computes: "how far is `add@plt` from this call site?" That distance is a 32-bit relative offset, which is what `R_X86_64_PLT32` asks for. (The addend `-4` accounts for the fact that x86 measures the offset from the *end* of the instruction, not the start.)
4. It writes that offset into the 4 bytes at position `0x0e`, replacing the placeholder.

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
- **The new beginning:** execution usually begins at `_start` (from `crt1.o`), which sets up the stack and calls `main`.

There is no GOT patching. There is no PLT indirection. The CPU just jumps straight into your code.

Everything we have covered so far happens before `main()` starts. But sometimes you need to load code *after* the program is already running: plugins, optional features, or hot-loaded extensions. This is what `dlopen` and `dlsym` provide. See [Appendix H: Runtime Loading (dlopen/dlsym)](#appendix-h-runtime-loading-dlopendlsym) for how the loader handles this and why it reuses much of the same machinery we have already seen.

---

## Conclusion: The Full Cycle

1. **Compiler:** generates `main.o` with relocation entries ("holes").
2. **Linker:**
   - injects `crt1.o` (the true entry point),
   - weaves `.text` sections together based on the script,
   - synthesizes PLT/GOT for dynamic symbols,
   - patches the holes using the relocation table.
3. Running the application is a dance between user apps (terminal) and the kernel.
4. **Kernel:** maps segments and invokes the interpreter (if `PT_INTERP` exists).
5. **Loader:** loads DSOs, applies relocations, sets up lazy binding, locks down RELRO. Calls `_start` → `__libc_start_main` → `main()`.

The "simple" act of running `./app` is a relay race passing the baton between the compiler, linker, kernel, and dynamic loader. Understanding who holds the baton at each stage helps in truly understanding what is happening under the hood.

In a follow-up post, we will see how this machinery can fail at scale. We will trace a production incident where two collective communication libraries were linked into the same binary, causing a symbol collision that silently redirected RDMA verb calls to the wrong device. Understanding the PLT/GOT resolution pipeline was the key to diagnosing it.


<details class="appendix">
<summary>

## Appendix A: The Cross-Architecture Magic (Rosetta & QEMU)

</summary>

If you ran this lab on an Apple Silicon Mac (M1/M2/M3) or a Windows ARM machine, you likely noticed that the x86-64 binary simply executed. It didn't crash, and it didn't require a manual emulator command.

This is not magic. It is a coordinated interplay between:

- a translation or emulation layer (Rosetta or QEMU),
- the container/VM runtime (e.g., Docker Desktop / WSL2 / Apple's Virtualization Framework),
- and the Linux kernel's [`binfmt_misc`](https://docs.kernel.org/admin-guide/binfmt-misc.html) dispatch mechanism.


### 1) The Architecture Gap

Our host CPU speaks a different ISA than the guest binary. There are two broad approaches:

- **Emulation (QEMU-style):** interpret/translate instructions and emulate architectural effects in software.
- **Translation (Rosetta-style):** translate blocks of guest instructions into host instructions and cache/execute the translations.

Either way, the translator must preserve **architectural semantics**, not just instruction-by-instruction behavior. One example is **memory ordering**: x86's memory model is stronger (often described as TSO-like) than ARM's default. Translators must ensure the program observes x86-legal outcomes, which can require extra ordering constraints (i.e. inserting memory barriers) in the generated code or other clever mechanisms. That can affect performance.


* **Windows (QEMU Emulation):** On Windows ARM, Docker commonly runs Linux containers inside a Linux VM (via WSL2). Cross‑arch support is frequently implemented by registering QEMU handlers with `binfmt_misc`, so that when the kernel encounters an x86‑64 ELF, it transparently invokes a QEMU interpreter (e.g., `qemu-x86_64`) to run it.

* **macOS (Rosetta + Hardware TSO):** On macOS, Docker Desktop runs Linux containers inside a lightweight Linux VM and can integrate Rosetta into that VM so x86‑64 Linux binaries can run on Apple Silicon. Apple solved the memory ordering bottleneck at the silicon level. Their M-series chips include a hardware switch to enable **Total Store Ordering (TSO)**. This allows the Rosetta translator to run without the heavy software barrier overhead, achieving near-native speeds.


### 2) How Rosetta Gets into the VM (VirtioFS Injection)

The Linux kernel inside our Docker VM does not ship with Rosetta. It is injected from macOS. Docker uses the **Apple Virtualization Framework (AVF)** to create the Linux VM. AVF exposes a specialized directory share called [`VZLinuxRosettaDirectoryShare`](https://developer.apple.com/documentation/virtualization/vzlinuxrosettadirectoryshare). This is not a standard network share; it is a high-performance channel handled via [**VirtioFS**](https://virtio-fs.gitlab.io/) (Virtual I/O File System).

When the VM boots, it detects this share and mounts it (usually to `/run/rosetta`). This makes the macOS `rosetta` binary visible and executable inside the Linux VM.

### 3) The Registration Command

Regardless of whether we use QEMU or Rosetta, the *Linux Kernel* mechanism is identical. It uses **`binfmt_misc`** (Binary Formats Miscellaneous).

When Docker Desktop starts (before our container is even created), its internal boot process sends a registration command to the kernel. Something equivalent of:

```bash
echo ':rosetta:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x3e\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:/run/rosetta/rosetta:POCF' > /proc/sys/fs/binfmt_misc/register
```

(guessing from the example in https://developer.apple.com/documentation/virtualization/running-intel-binaries-in-linux-vms-with-rosetta)

The Kernel matches the file header against these bytes. The crucial part that identifies x86-64 is at **Offset 18**, which is `0x3e`.

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

</details>

<details class="appendix">
<summary>

## Appendix B: The Keyboard Dance (TTY Architecture)

</summary>

One of the most confusing parts of Unix is typing into a terminal.

* **The Myth:** "The shell reads my keystrokes and draws them on the screen."
* **The Reality:** the shell is usually asleep. The **kernel** and our **terminal emulator (GUI or TUI)** do most of the work.

---

### 1) See it for Yourself: The Shell is "Asleep"

Before explaining the architecture, let's prove that the shell is *blocked* and waiting for the kernel.

You *can* attach `strace` to the shell you are currently typing in, but it's messy (the trace output competes with your interactive terminal). Using **two terminal windows** is cleaner.

**Step 1 (Terminal A):** get the shell's PID.

```bash
❯ echo $$
4331
```

**Step 2 (Terminal B):** attach `strace` to that PID.

```bash
❯ strace -p 4331
strace: Process 4331 attached
read(0,
```

**Result:** it immediately blocks on `read(0, ...)`. That's the shell waiting for input to appear on file descriptor 0 (stdin). It is not polling the keyboard.

---

### 2) The Setup: How the Pipe is Built (PTY Master/Slave)

So how does your shell "read from your terminal window" at all?

Modern terminals are built on **pseudo-terminals (PTYs)**, a master/slave pair that behaves like a virtual serial terminal.

At a high level:

* The **terminal emulator** (GNOME Terminal, iTerm, Windows Terminal via WSL, etc.) owns the **PTY master**.
* The **shell** (bash/zsh/fish) owns the **PTY slave**.
* The kernel sits in the middle and implements "terminal semantics" (line editing modes, echo, job control signals, window size, etc.).

Here's the typical creation sequence:

1. **Open the master**

* The terminal emulator calls `posix_openpt()` (or `openpty()` / `forkpty()`).
* The kernel returns a **master FD** (e.g., FD 3). This is the emulator's "control end."

2. **Make the slave usable and discover it**

* The emulator calls `grantpt()` and `unlockpt()` (permissions + unlock the slave).
* It calls `ptsname()` to learn the slave path, e.g. `/dev/pts/5`.

3. **Spawn the shell and wire its stdio**

* The emulator forks a child process (or `forkpty()` does it for us).
* In the child, it typically does:

  * `setsid()` to start a new session
  * `ioctl(TIOCSCTTY)` to make the **slave** the **controlling terminal**
  * `dup2(slave, 0)`, `dup2(slave, 1)`, `dup2(slave, 2)` to replace stdin/stdout/stderr
  * `execve()` to run the shell

**Result:** the shell thinks it is connected to a hardware terminal on FD 0/1/2, but it's actually connected to a kernel PTY device whose other end is controlled by the emulator.

---

### 3) The Flow: The Journey of a Single `k`

Here's the full trip a single keystroke takes, from finger to pixels.

#### Step 1: The Hardware Spark

* You press `k`. The keyboard triggers an interrupt; the kernel's input stack translates scancodes into a key event.
* Because many apps/windows exist, the OS's GUI stack (compositor/window system) acts as a traffic cop and delivers the "k pressed" event to the focused terminal window.

#### Step 2: The Terminal Emulator (GUI or TUI)

* The terminal emulator receives the event.
* It **writes the byte** `k` into the **PTY master FD**.
* **Important:** it does *not* draw `k` yet. It has only injected input into the PTY pipeline.

#### Step 3: The Kernel TTY Layer (Line Discipline)

Now the kernel's TTY subsystem becomes the middleman. This is where "terminal behavior" lives.

* **Canonical mode (`ICANON`)**: the kernel buffers input into a line and delivers it to the slave only when Enter is pressed (classic cooked mode).
* **Noncanonical ("raw-ish") mode**: shells and editors usually disable `ICANON` so they can do their own line editing; exactly which flags are enabled varies.
* **Echo (`ECHO`)**: if enabled, the kernel itself can echo typed characters back through the PTY stream.
* **Signals (`ISIG`)**: if enabled, special control characters trigger signals:

  * `VINTR` (often Ctrl+C, byte `0x03`) → `SIGINT`
  * `VQUIT` (often Ctrl+) → `SIGQUIT`
  * `VSUSP` (often Ctrl+Z) → `SIGTSTP`

So the earlier point is correct, with one precise condition:

> Ctrl+C becomes `SIGINT` **only if** the terminal is configured with `ISIG` and `VINTR` set appropriately.

#### Step 4: The Shell (zsh/bash)

* The shell was blocked on `read(0, ...)`. When input arrives on the slave side, it wakes up.
* It reads `k` and updates its internal line buffer.
* A "smart" shell may decide to render it as syntax-green (or do completion previews, etc.).
* It writes the resulting bytes (including ANSI escape sequences) to **stdout** (FD 1).

#### Step 5: The Loop Closes

* FD 1 is still the PTY **slave**.
* The kernel transfers the output stream from slave → master.

#### Step 6: Rendering (Pixels Happen Here)

* The terminal emulator's event loop wakes up because there's data on the **master**.
* It reads the bytes, parses ANSI escape codes, and **renders glyphs** (possibly colored) into pixels on your screen.

**Net result:** the character appearing on screen is not the shell "drawing." It's the emulator rendering output bytes that flowed *back* through the PTY.

---

### 4) Why Emulate? (Why Not Read Hardware Directly?)

Why go through this PTY dance? Why can't `bash` just read the keyboard device directly?

1. **Isolation (the traffic cop problem)**
   There's one physical keyboard and many processes. If every program read from the hardware device directly, our `k` would land in *every* terminal and *every* app. We rely on the GUI stack to route events to the focused terminal, which then injects bytes into the correct PTY.

2. **Virtualization (SSH / remote terminals)**
   Often the "keyboard" isn't local at all. When you SSH into a server, the server has no physical keyboard attached to your process. The SSH daemon typically allocates a PTY for the remote session so the remote shell gets real terminal semantics (echo control, job control, Ctrl+C handling, window resize).

3. **Necessity (why not just pipes?)**
   Plain pipes (`|`) move bytes, but they don't carry terminal semantics:

* **Signals:** Ctrl+C wouldn't automatically become `SIGINT` via `VINTR`/`ISIG`.
* **Geometry:** editors like `vim` wouldn't learn rows/cols (`TIOCGWINSZ`).
* **Echo/security:** `sudo` couldn't reliably disable echo for password entry.
* **Job control:** foreground/background process groups and terminal ownership wouldn't behave like a "real terminal."

PTYs exist because interactive programs need more than a byte stream. They need a *terminal*.

### 5) The Modern Flaw (When the GUI Crashes)

By moving the terminal emulator into user space (a GUI app like GNOME Terminal/iTerm), we introduced a fragility. The interactive chain becomes:

**Keyboard → Kernel input → GUI stack → Terminal emulator → PTY master → Kernel TTY → PTY slave → Shell → (back outward)**

If the **GUI stack** (compositor/window server) or the **terminal emulator** hangs:

1. You press Ctrl+C.
2. The kernel still receives the keyboard interrupt and produces an input event…
3. …but the event never gets delivered through the GUI stack to the terminal emulator.
4. The terminal emulator never writes `0x03` into the PTY master.
5. The TTY line discipline never sees `VINTR`, so it never generates `SIGINT`.
6. **Result:** you can't use "Ctrl+C" as your emergency stop *from that frozen GUI terminal*, even though the kernel is alive.

This is why production folks love having *more than one control plane*.

#### The Linux Escape Hatch (Virtual Consoles)

Linux keeps **virtual consoles** (`tty1`–`tty6`) that bypass the GUI stack entirely and use the kernel console subsystem. On many systems you can switch with:

* `Ctrl + Alt + F3` (or F2/F4/F5/F6)

These give you an "emergency stop" even if the desktop is frozen. macOS, unfortunately, does not provide an equivalent user-facing virtual console switch in the same way.

</details>

<details class="appendix">
<summary>

## Appendix C: Under the Hood (IDT, MSRs & Syscalls)

</summary>

In Part I, we glossed over the "Hardware Gate." Here is what happens on **modern x86-64** when we interact with the kernel, with the crucial clarification we discussed:

* **User → Kernel entry via IDT (interrupts/exceptions):** the CPU **does switch** to a kernel-controlled stack in hardware (via the TSS, optionally IST).
* **User → Kernel entry via `syscall`:** the CPU **does not** switch stacks in hardware; the kernel's entry stub switches stacks in software **before touching the stack**, so the kernel does not meaningfully "run on the user stack."

---

### 1) The Interrupt Descriptor Table (IDT)

When we press a key, the keyboard generates an external interrupt. On modern systems the interrupt routing logic (APIC/IO-APIC, etc.) delivers an **interrupt *vector*** to the CPU. People often say "IRQ 1 for keyboard," but that's a legacy naming convention: what the CPU uses to index the IDT is the **vector number**, and Linux's own docs refer to "IDT vector assignments" (e.g., in `arch/x86/include/asm/irq_vectors.h`). ([Kernel][1])

#### The Lookup

The CPU consults the **IDT**, a table mapping interrupt/exception vectors to entry stubs (interrupt/trap gates). Linux registers many of these entry points in `traps.c` and implements the mechanics in `entry_64.S`. ([Kernel][1])

#### The Stack Switch (TSS & IST): **Kernel Must Not Run on a User Stack**

This is the security-critical guarantee: **on a privilege transition (CPL 3 → CPL 0), the CPU cannot safely execute on the user stack**, so it switches to a kernel-controlled stack.

There are two related mechanisms:

1. **Normal ring transition stack (TSS RSP0 / "the regular kernel stack")**
   If the IDT gate does **not** request an IST stack, then on CPL 3 → CPL 0 entry the CPU loads the kernel stack pointer from the TSS (the ring-0 stack slot) and begins building the entry frame there.

2. **Interrupt Stack Table (IST): optional per-vector "known-good" stacks**
   If the IDT gate specifies a non-zero **IST index**, the CPU loads the stack pointer from that IST slot in the TSS. Linux explicitly calls out that **IST-based entry needs special handling**, and that "super-atomic" vectors and certain contexts rely on the more careful entry logic; it also notes that some entries push an error code and others do not, and that IST stack mechanism changes the stack-frame mechanics. ([Kernel][1])

**Why is IST "optional"?**
Because IST is a limited and specialized tool: Linux tries to "only use IST entries … for vectors that absolutely need" the more paranoid handling, and uses normal entry paths for the rest. ([Kernel][1])

#### The Save: What Actually Gets Pushed

On interrupt/exception entry, the CPU builds a defined stack frame (more than just RIP/RSP). At minimum it preserves the instruction pointer / flags / code segment, and on privilege transitions it also saves the old stack context; certain exceptions add an **error code**. Linux's entry documentation explicitly notes this split ("Some of the IDT entries push an error code onto the stack; others don't."). ([Kernel][1])

#### The Handler

Only after the CPU has (1) selected the correct entry, (2) landed on a safe stack (TSS/IST rules), and (3) preserved the interrupted context does the kernel's handler code run.

---

### 2) The `syscall` Instruction (The Fast Path) and Why it's "Special"

Historically, system calls used the IDT as well (e.g., `int 0x80`). That path necessarily uses the interrupt/trap machinery: IDT lookup, hardware frame push, and (when coming from user mode) an automatic stack switch via the TSS.

Modern x86-64 adds `SYSCALL` specifically to make this transition cheaper.

#### The Setup (MSRs: `IA32_LSTAR`, `IA32_STAR`, `IA32_FMASK`)

When the OS boots, it programs model-specific registers (MSRs) so the CPU knows where to enter the kernel on `SYSCALL`:

* `IA32_LSTAR`: the 64-bit kernel entry RIP for `SYSCALL`
* `IA32_STAR`: encodes the code/stack segment selectors
* `IA32_FMASK`: specifies which RFLAGS bits are cleared on entry

(These are the architectural contract that makes `SYSCALL` a direct jump into kernel entry stubs.) ([Félix Cloutier][2])

#### The Jump: What Hardware Does on `SYSCALL`

When user code executes `syscall`:

* The CPU loads RIP from `IA32_LSTAR`
* It saves the user return address into **RCX**
* It saves user flags into **R11**, then masks flags via `IA32_FMASK`

And here's the key point:

> **`SYSCALL` does not save the stack pointer (RSP), and does not switch stacks in hardware.** ([Félix Cloutier][2])

This is exactly what makes `SYSCALL` "fast": the CPU avoids doing the full interrupt-frame push and stack switching that happens through an IDT gate.

#### "Wait, Does the Kernel Run on the User Stack Then?"

In the strictest sense, **for a brief window of instructions**, `RSP` still contains the user value right after entering ring 0 via `SYSCALL`. That sounds scary, but the kernel entry stub is carefully written around this:

* **It does not touch the stack** (no `push`, no stack spills) until it switches stacks.
* It immediately switches to a kernel-controlled stack in software as part of the entry sequence.

This is why system-call teaching material (and kernel entry docs) can correctly summarize the end result as: during the user→kernel transition "the stack is also switched from the user stack to the kernel stack". But for the `SYSCALL` path that switching is performed by the kernel's entry code, not by hardware. ([Linux Kernel Labs][3])

**So the crisp, correct statement is:**

* **Interrupt/exception entry from user mode:** hardware stack switch via TSS/IST.
* **`SYSCALL` entry from user mode:** hardware does *not* switch stacks; kernel entry code switches immediately **before using the stack**. ([Félix Cloutier][2])

---

### 3) KPTI / PTI (Kernel Page Table Isolation)

On CPUs affected by Meltdown-class issues, entering the kernel can involve an additional heavyweight transition: changing which page tables are active so kernel mappings aren't present (or are severely constrained) in user mode.

#### The Core Idea

With PTI enabled, the kernel maintains two page-table views:

* **User page tables:** map user space plus only the minimal kernel entry/exit structures required for safe transitions.
* **Kernel page tables:** map full kernel + user mappings.

Linux's PTI documentation explains that user page tables map only what's needed for kernel entry/exit (via structures like `cpu_entry_area`) and describes the duplication/sharing at the top level (PGD) used to keep user mappings consistent. ([Kernel][4])

#### The Cost: CR3 Switching (and How PCID Reduces the Pain)

PTI adds runtime overhead primarily because:

* We must manipulate **CR3** to switch between the two page-table sets on syscall/interrupt/exception entry/exit (this can be skipped in some cases if the kernel is interrupted while already in kernel mode). ([Kernel][4])
* On systems **without PCID**, CR3 writes flush the TLB broadly, making each entry/exit more expensive. ([Kernel][4])
* With **PCID**, the CPU can avoid flushing the entire TLB on each switch; Linux's PTI docs describe how PCID makes switching cheaper and how some flush work can be deferred to reduce cost. ([Kernel][4])

#### PTI + `SYSCALL`: The Trampoline and "Stacks Must be Switched at Entry Time"

Linux's PTI documentation calls out an additional nuance: PTI uses a **trampoline** for `SYSCALL` entry with a smaller mapped resource set, and explicitly notes "the downside is that stacks must be switched at entry time." This is the exact place where the "`SYSCALL` doesn't change RSP" architectural rule meets the kernel's need to get onto a safe stack immediately. ([Kernel][4])

---

#### Summary (the "No Contradictions" Version)

* **IDT-based entry from user mode:** CPU consults IDT, selects a kernel stack via TSS (optionally IST), pushes an entry frame, then runs kernel code. IST is **optional** and reserved for vectors that need a known-good stack and/or paranoid entry behavior. ([Kernel][1])
* **`SYSCALL` entry:** CPU jumps to `IA32_LSTAR`, saves return state in registers (RCX/R11), and does **not** change RSP; the kernel entry stub switches to a kernel stack in software **before touching the stack**, preserving security. ([Félix Cloutier][2])
* **PTI/KPTI:** adds page-table switching (CR3) on entry/exit; PCID reduces TLB-flush cost; PTI's syscall trampoline makes early stack switching even more central. ([Kernel][4])

[1]: https://www.kernel.org/doc/html/v5.10/x86/entry_64.html "7. Kernel Entries — The Linux Kernel  documentation"
[2]: https://www.felixcloutier.com/x86/syscall?utm_source=chatgpt.com "SYSCALL — Fast System Call - felixcloutier.com"
[3]: https://linux-kernel-labs.github.io/refs/heads/master/lectures/syscalls.html?utm_source=chatgpt.com "System Calls — The Linux Kernel documentation"
[4]: https://www.kernel.org/doc/html/next/x86/pti.html "21. Page Table Isolation (PTI) — The Linux Kernel  documentation"

</details>

<details class="appendix">
<summary>

## Appendix D: Segments Deep Dive

</summary>

Here is the detailed explanation of the `readelf -l` output, formatted to fit directly into the "Mapping the Memory" section.

<details>
<summary>readelf -l ./dynamic_app (full output)</summary>

```bash
root@container:/code# readelf -l ./dynamic_app

Elf file type is DYN (Position-Independent Executable file)
Entry point 0x1060
There are 13 program headers, starting at offset 64

Program Headers:
  Type           Offset             VirtAddr           PhysAddr
                 FileSiz            MemSiz              Flags  Align
  PHDR           0x0000000000000040 0x0000000000000040 0x0000000000000040
                 0x00000000000002d8 0x00000000000002d8  R      0x8
  INTERP         0x0000000000000318 0x0000000000000318 0x0000000000000318
                 0x000000000000001c 0x000000000000001c  R      0x1
      [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
  LOAD           0x0000000000000000 0x0000000000000000 0x0000000000000000
                 0x0000000000000638 0x0000000000000638  R      0x1000
  LOAD           0x0000000000001000 0x0000000000001000 0x0000000000001000
                 0x0000000000000171 0x0000000000000171  R E    0x1000
  LOAD           0x0000000000002000 0x0000000000002000 0x0000000000002000
                 0x00000000000000e4 0x00000000000000e4  R      0x1000
  LOAD           0x0000000000002d98 0x0000000000003d98 0x0000000000003d98
                 0x0000000000000278 0x0000000000000280  RW     0x1000
  DYNAMIC        0x0000000000002da8 0x0000000000003da8 0x0000000000003da8
                 0x0000000000000210 0x0000000000000210  RW     0x8
  NOTE           0x0000000000000338 0x0000000000000338 0x0000000000000338
                 0x0000000000000030 0x0000000000000030  R      0x8
  NOTE           0x0000000000000368 0x0000000000000368 0x0000000000000368
                 0x0000000000000044 0x0000000000000044  R      0x4
  GNU_PROPERTY   0x0000000000000338 0x0000000000000338 0x0000000000000338
                 0x0000000000000030 0x0000000000000030  R      0x8
  GNU_EH_FRAME   0x0000000000002004 0x0000000000002004 0x0000000000002004
                 0x0000000000000034 0x0000000000000034  R      0x4
  GNU_STACK      0x0000000000000000 0x0000000000000000 0x0000000000000000
                 0x0000000000000000 0x0000000000000000  RW     0x10
  GNU_RELRO      0x0000000000002d98 0x0000000000003d98 0x0000000000003d98
                 0x0000000000000268 0x0000000000000268  R      0x1

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

This output confirms that modern binaries are far more complex than the simple "Code vs. Data" model. The Linker has split our binary into **4 distinct memory regions (LOAD segments)** to maximize security and efficiency.

### Explanation of the Output

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
| **LOAD #4** (Data) | `RW` | `0x3d98` | `.data` (globals), `.bss`, **GOT** (Global Offset Table) | The only writable memory. Backed by the file on disk until written, then Copy-on-Write kicks in. |



**4. The `GNU_RELRO` Segment (Security)**

```text
GNU_RELRO      0x...2d98 ... Flags R

```

This is a security overlay. Notice that its address (`0x2d98`) overlaps with the start of the **LOAD #4 (RW)** segment. See the [RELRO section in Appendix F](#relro-relocation-read-only) for more details.



**5. `GNU_STACK` (NX Bit)**

```text
GNU_STACK ... Flags RW

```

The absence of the `E` flag here is critical. It tells the Kernel: "The stack is for data, not code." This prevents code-injection attacks on the stack.



Once the app starts running we can check where it finally gets loaded (yes, we cheated and added a 60 second sleep in main.c to get the pid). We see that the final loaded address has a bias of ≈ 0x555555554000 for the PT_LOAD sections. That's the load_bias mentioned in Section 2.2 that the kernel adds for ASLR purposes.

<details>
<summary>cat /proc/$pid/maps (full process memory map)</summary>

```bash
root@container:/code# ./dynamic_app & pid=$!
root@container:/code# cat /proc/$pid/maps | sed -n '1,120p'

555555554000-555555555000 r--p 00000000 00:2d 53                         /code/dynamic_app
555555555000-555555556000 r-xp 00001000 00:2d 53                         /code/dynamic_app
555555556000-555555557000 r--p 00002000 00:2d 53                         /code/dynamic_app
555555557000-555555558000 r--p 00002000 00:2d 53                         /code/dynamic_app
555555558000-555555559000 rw-p 00003000 00:2d 53                         /code/dynamic_app
7fffff58d000-7fffff590000 rw-p 00000000 00:00 0
7fffff590000-7fffff5b8000 r--p 00000000 00:50 34167223                   /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff5b8000-7fffff74d000 r-xp 00028000 00:50 34167223                   /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff74d000-7fffff7a5000 r--p 001bd000 00:50 34167223                   /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff7a5000-7fffff7a6000 ---p 00215000 00:50 34167223                   /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff7a6000-7fffff7aa000 r--p 00215000 00:50 34167223                   /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff7aa000-7fffff7ac000 rw-p 00219000 00:50 34167223                   /usr/lib/x86_64-linux-gnu/libc.so.6
7fffff7ac000-7fffff7b9000 rw-p 00000000 00:00 0
7fffff7bc000-7fffff7bd000 r--p 00000000 00:2d 43                         /code/libmath.so
7fffff7bd000-7fffff7be000 r-xp 00001000 00:2d 43                         /code/libmath.so
7fffff7be000-7fffff7bf000 r--p 00002000 00:2d 43                         /code/libmath.so
7fffff7bf000-7fffff7c0000 r--p 00002000 00:2d 43                         /code/libmath.so
7fffff7c0000-7fffff7c1000 rw-p 00003000 00:2d 43                         /code/libmath.so
7fffff7c1000-7fffff7c3000 rw-p 00000000 00:00 0
7fffff7c3000-7fffff7c4000 ---p 00000000 00:00 0
7fffff7c4000-7ffffffc4000 rw-p 00000000 00:00 0
7ffffffc4000-7ffffffc6000 r--p 00000000 00:50 34167205                   /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
7ffffffc6000-7fffffff0000 r-xp 00002000 00:50 34167205                   /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
7fffffff0000-7fffffffb000 r--p 0002c000 00:50 34167205                   /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
7fffffffb000-7fffffffc000 ---p 00000000 00:00 0
7fffffffc000-7fffffffe000 r--p 00037000 00:50 34167205                   /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
7fffffffe000-800000000000 rw-p 00039000 00:50 34167205                   /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
800000000000-800000025000 r--p 00000000 00:35 2                          /run/rosetta/rosetta
800000025000-800000093000 r-xp 00025000 00:35 2                          /run/rosetta/rosetta
8000000a0000-8000001a6000 rw-p 000a0000 00:35 2                          /run/rosetta/rosetta
effff7dd3000-effff7de7000 rw-p 00000000 00:00 0
effff7de7000-effff7de8000 ---p 00000000 00:00 0
effff7de8000-effff7dec000 rw-p 00000000 00:00 0
effff7dec000-effff7ded000 ---p 00000000 00:00 0
effff7ded000-effff7ff8000 rw-p 00000000 00:00 0
effff7ff8000-efffffff8000 rwxp 00000000 00:00 0
efffffff8000-f000138a4000 rw-p 00000000 00:00 0
ffffb2901000-ffffb2903000 r--p 00000000 00:00 0                          [vvar]
ffffb2903000-ffffb2904000 r-xp 00000000 00:00 0                          [vdso]
ffffc85bd000-ffffc85de000 rw-p 00000000 00:00 0                          [stack]
```

</details>

</details>

<details class="appendix">
<summary>

## Appendix E: The Loader's Bootstrap (Self-Relocation)

</summary>

In Section 3, we mentioned the loader must "fix itself." Here are the details.

### The "Chicken and Egg" Problem

Normal programs rely on the loader to fix their addresses before they run. But `ld-linux.so` *is* the loader. Who loads the loader? No one.

When the kernel maps the loader, it just maps segments.

- **ASLR:** loader is at a random address (e.g., `0x7f34...`) instead of its link-time base.
- **Broken GOT:** internal pointers may assume link-time addresses.
- **No libc:** it can't call most libc routines yet.

### The Solution: `_dl_start`

The entry point passes control to `_dl_start` in `elf/rtld.c`. This function is written with extreme care to avoid accesses that rely on unrelocated global state.

A simplified sketch:

```c
/* elf/rtld.c */
// for more curious souls: https://elixir.bootlin.com/glibc/glibc-2.1.94/source/elf/rtld.c#L165
static ElfW(Addr) __attribute_used__
_dl_start (void *arg)
{
    /* 1. Calculate the load bias */
    ElfW(Addr) l_addr = elf_machine_load_address ();

    /* 2. Apply bootstrap relocations (self-patch) */
    elf_machine_rel (l_addr, ...);

    /* 3. Now the loader can safely run complex code */
    return _dl_start_final (arg, ...);
}
```

Step 1 finds the bias (often via RIP-relative tricks). Step 2 applies `R_X86_64_RELATIVE`-style relocations to itself. Once that's done, it becomes a "real program" and can load your app.

</details>

<details class="appendix">
<summary>

## Appendix F: Loader's Relocation Mechanism

</summary>

### F.0 High-Level Sequence (What We're About to Zoom Into)

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
# see how libnames (./libmath.so and libc.so.6 are here, same for $ORIGIN)
root@container:/code# readelf -p .dynstr ./dynamic_app
String dump of section '.dynstr':
  [     1]  __cxa_finalize
  [    10]  _ITM_registerTMCloneTable
  [    2a]  _ITM_deregisterTMCloneTable
  [    46]  __gmon_start__
  [    55]  add
  [    59]  __libc_start_main
  [    6b]  sleep
  [    71]  ./libmath.so
  [    7e]  libc.so.6
  [    88]  GLIBC_2.2.5
  [    94]  GLIBC_2.34
  [    9f]  $ORIGIN

# inspect dynamic section
root@container:/code# readelf -d ./dynamic_app

Dynamic section at offset 0x2dd8 contains 28 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [./libmath.so]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN]
 0x000000000000000c (INIT)               0x1000
 0x000000000000000d (FINI)               0x11bc
 0x0000000000000019 (INIT_ARRAY)         0x3dc8
 0x000000000000001b (INIT_ARRAYSZ)       8 (bytes)
 0x000000000000001a (FINI_ARRAY)         0x3dd0
 0x000000000000001c (FINI_ARRAYSZ)       8 (bytes)
 0x000000006ffffef5 (GNU_HASH)           0x3b0
 0x0000000000000005 (STRTAB)             0x498
 0x0000000000000006 (SYMTAB)             0x3d8
 0x000000000000000a (STRSZ)              167 (bytes)
 0x000000000000000b (SYMENT)             24 (bytes)
 0x0000000000000015 (DEBUG)              0x0
 0x0000000000000003 (PLTGOT)             0x4000
 0x0000000000000002 (PLTRELSZ)           48 (bytes)
 0x0000000000000014 (PLTREL)             RELA
 0x0000000000000017 (JMPREL)             0x640
 0x0000000000000007 (RELA)               0x580
 0x0000000000000008 (RELASZ)             192 (bytes)
 0x0000000000000009 (RELAENT)            24 (bytes)
 0x000000006ffffffb (FLAGS_1)            Flags: PIE
 0x000000006ffffffe (VERNEED)            0x550
 0x000000006fffffff (VERNEEDNUM)         1
 0x000000006ffffff0 (VERSYM)             0x540
 0x000000006ffffff9 (RELACOUNT)          3
 0x0000000000000000 (NULL)               0x0
```

</details>


5. Iterates through `DT_NEEDED` entries. In our case it will be `./libmath.so` and `libc.so` as we can see in the output.


6. For `./libmath.so` it will be interpreted as the path relative to CWD. Loader will find this shared library, `mmap` it into the current process memory. As part of doing this it will perform all the relocations, etc. required for `libmath` itself. It will do the same for `libc.so`.

7. Then it will move to doing relocations for your executable. First it will look at `map[RELA]` (`.rela.dyn`) section.

---

### F.1 Relocations for the Main Executable

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

### F.2 The Lazy-Binding PLT Path in Practice (`add@plt → .got.plt → resolver → patch`)

[Note: Don't try to compare gdb snippets below with above snippets, we cheated a bit because gdb for x64 binary on an M1 was giving us a hard time, so we just booted up a linux VM on another x64 machine. It shouldn't matter though for understanding the plt relocations.]

* the original callsite of add function will look something like:

```asm
(gdb) disas main
Dump of assembler code for function main:
   0x0000000000401126 <+0>:     push   %rbp
   0x0000000000401127 <+1>:     mov    %rsp,%rbp
   0x000000000040112a <+4>:     mov    $0xa,%esi
   0x000000000040112f <+9>:     mov    $0x5,%edi
   0x0000000000401134 <+14>:    call   0x401030 <add@plt> <==== see this
   0x0000000000401139 <+19>:    pop    %rbp
   0x000000000040113a <+20>:    ret
End of assembler dump.
```

* This will call a function in the PLT table. The PLT table looks like below.

```asm
Disassembly of section .plt:

(gdb) disas 0x401030
Dump of assembler code for function add@plt:
   0x0000000000401030 <+0>:     jmp    *0x2fca(%rip)        # 0x404000 <add@got.plt>
   0x0000000000401036 <+6>:     push   $0x0
   0x000000000040103b <+11>:    jmp    0x401020
```

* this entry will jump to the address pointed by `add@got.plt` (at `0x404000`), which is an entry in the `.got` table.

```asm
(gdb) x/4gx 0x404000
0x404000 <add@got.plt>: 0x0000000000401036      0x0000000000000000
0x404010:       0x0000000000000000      0x0000000000000000
```

* this entry points back to `0x0000000000401036`, which is the immediate next instruction in `add@plt` which redirected us to got in the first place. This instruction will push relocation index (index in `.rela.plt`, add's index was 0) onto the stack and calls the resolver stub.

* After pushing relocation index, it jumps to `0x401020`, which has some stub for setting up the stack, and eventually calls `0x403ff8`.

```asm
(gdb) x/10i 0x401020
   0x401020:    push   0x2fca(%rip)        # 0x403ff0
=> 0x401026:    jmp    *0x2fcc(%rip)        # 0x403ff8
```

* one more step, and we land at:

```asm
(gdb) stepi
_dl_runtime_resolve_xsavec () at ../sysdeps/x86_64/dl-trampoline.h:67
67              _CET_ENDBR
(gdb) disas
Dump of assembler code for function _dl_runtime_resolve_xsavec:
=> 0x00007ffff7fd9d70 <+0>:     endbr64
   0x00007ffff7fd9d74 <+4>:     push   %rbx
   0x00007ffff7fd9d75 <+5>:     mov    %rsp,%rbx
   0x00007ffff7fd9d78 <+8>:     and    $0xffffffffffffffc0,%rsp
```

* we will not go into details of what `_dl_runtime_resolve_xsavec` does, but ultimately it will find the absolute address of `add` function (same way it found __libc_start_main from libc) and will patch the got entry which redirected us back to PLT originally.

Let the function run and check the GOT entry again.

```asm
(gdb) fin
Run till exit from #0  _dl_runtime_resolve_xsavec () at ../sysdeps/x86_64/dl-trampoline.h:75
0x0000000000401139 in main ()

(gdb) x/4gx 0x404000
0x404000 <add@got.plt>: 0x00007ffff7fb90f9      0x0000000000000000
0x404010:       0x0000000000000000      0x0000000000000000

(gdb) p/x &add
$2 = 0x7ffff7fb90f9
```

Voila! `0x404000` points to `0x00007ffff7fb90f9` now instead of `0x0000000000401036`. So next time `add` is called the PLT will directly call actual `add` function at `0x00007ffff7fb90f9`.

Again, the above thing would happen at runtime, not during startup in the default case (when PLT entries are lazily relocated), but we showed it here for completeness.

* Once these relocations are done, we are ready to handoff to `_start`.


### The PLT/GOT Dance (Why Not Call GOT Directly?)

A common question is: why do we need the PLT at all? Why can't the compiler just generate `call *GOT_entry`?

Technically, it can (and flags like `-fno-plt` change some call patterns, but disable lazy binding too). However, the traditional PLT exists to solve the "who called me?" problem required for lazy binding.

If we simply did `call *GOT_entry` and the function wasn't resolved yet, the GOT would point to the resolver. But when the resolver wakes up, it has no context: it doesn't know if we wanted `add`, `printf`, or `exit`.

The PLT injects the missing ID. A canonical x86-64 PLT stub looks like:

```asm
PLT_add:
  jmp *GOT_add   ; 1. Jump to GOT (first time: jumps to resolver path)
  push $0x1      ; 2. Push relocation index / ID for 'add'
  jmp PLT_0      ; 3. Jump to common resolver
```

Line 2 is the secret sauce: it pushes the relocation index so the resolver can find the right `R_X86_64_JUMP_SLOT` entry in `DT_JMPREL` and resolve exactly the intended symbol.

---

#### RELRO (RELocation Read-Only)

At last, the loader takes care of the sections defined at RELRO. Revisiting output of `readelf -l` from [Appendix D](#appendix-d-segments-deep-dive).

The relevant excerpt from `readelf -l`:

```text
  GNU_RELRO      0x...2d98 0x...3d98 0x...3d98
                 0x...0268 0x...0268  R      0x1

 Section to Segment mapping:
   12     .init_array .fini_array .dynamic .got
```

`GNU_RELRO` is the segment that includes `.init_array .fini_array .dynamic .got` sections. These sections are initially mapped as read/write at startup time for the loader to perform all the patches, etc. but once that is done, the loader calls `mprotect` on these pages to make them read-only.

Note how `.got.plt` is not present here? In **this build/layout**, that's exactly what we would expect for **lazy binding**: `.got.plt` needs to stay writable at runtime so the resolver can patch PLT slots on first call, so the linker typically keeps it out of the RELRO-protected region.

If an attacker finds a buffer overflow in your app later, they cannot overwrite the GOT to hijack different lib calls via GOT indirections, because that memory is now not writeable.

</details>

<details class="appendix">
<summary>

## Appendix G: The Assembly Handoff (_start)

</summary>

In Section 4, we glossed over the assembly handoff. Here are the exact mechanics of how the loader passes control to the user.

### 1. The Exit Stub (`_dl_start_user`)

The loader is written in C, but the final handoff requires assembly to manipulate registers precisely. This happens in architecture-specific glue (e.g., `sysdeps/x86_64/dl-machine.h` in glibc).

A schematic flow:

```asm
_dl_start_user:
    mov %rsp, %rdi         # Save stack pointer (argc/argv live here)
    call _dl_init          # Run init functions for DSOs
    jmp *%r12              # Jump to user entry point (_start)
```

### 2. The User Entry Point (`_start`)

The CPU lands at `_start`. This is provided by `crt1.o`. Its primary job is to align the stack (16‑byte alignment required by the x86‑64 ABI) and set up arguments for `__libc_start_main`.

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

</details>

<details class="appendix">
<summary>

## Appendix H: Runtime Loading (dlopen/dlsym)

</summary>

Everything in the main article happens before `main()` starts. But many real programs need to load code later: a web server that loads authentication modules on demand, a game engine that loads renderer backends based on the GPU it detects, or a language runtime loading compiled extensions. The mechanism for this is `dlopen` and `dlsym`.

### H.1 Loading a Library After Startup

Suppose your program has an optional plugin system. At runtime, you decide to load a plugin:

```c
void *handle = dlopen("./libplugin.so", RTLD_LAZY);
```

Under the hood, this calls back into the same dynamic loader (`ld-linux.so`) that set up your process at startup. The loader finds `libplugin.so`, maps it into the process's address space with `mmap`, resolves its dependencies (if `libplugin.so` itself depends on other libraries), and performs relocations, the same machinery we saw in [Appendix F](#appendix-f-loaders-relocation-mechanism), just happening after `main()` instead of before it.

### H.2 Initialization: Why `dlopen` Can Be Slow (or Crash)

Before `dlopen` returns, the loader must run the constructors (`.init_array`) of `libplugin.so` and all of its dependencies. This is the same initialization step the loader performs for startup libraries, but it happens synchronously inside your `dlopen` call.

This has a practical consequence: if `libplugin.so` contains a C++ global like `MyClass instance;`, that constructor runs inside `dlopen`. If it crashes, allocates a lot of memory, or takes a long time, your `dlopen` call inherits that behavior. The library must be fully initialized before you get the handle back.

### H.3 Looking Up Symbols (`dlsym`)

Once `dlopen` returns successfully, you have an opaque handle. Internally, this is a pointer to the `link_map` structure the loader created when it mapped the library, the same structure it uses to track every shared library in the process.

To call a function from the loaded library:

```c
void (*func)() = dlsym(handle, "run_plugin");
func();
```

The loader walks the symbol hash table of that specific `link_map` and returns the memory address of `run_plugin`. From this point on, you call `func()` like any other function pointer.

This is conceptually how Python loads C extensions: `import numpy` eventually triggers a `dlopen` on the compiled NumPy shared object, and `dlsym` is used to find the entry points that bridge Python calls to the C implementation.

</details>