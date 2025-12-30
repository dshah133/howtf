# The Architecture of Execution: A Deep Dive into ELF, Linking, and Loading

From the terminal to `main()` ... and back to the source.

We have all been there. You deploy a binary that worked perfectly on your dev machine, but the production crashes with

`/lib64/libc.so.6: version 'GLIBC_2.34' not found`

or

`error while loading shared libraries: libfoo.so: cannot open shared object file`


You do a frantic search through all of your favorite LLMs, cross verifying one's response with another, blindly pasting `export LD_LIBRARY_PATH =` commands, and installing random packages until the error disappears. We treat the execution process as a black box, something that "just works" until it doesn't.

In this post, we will try to do something different. We will start at the Runtime — tracing the life of a command from the moment you hit Enter in your terminal until it reaches `main()`. We will see what sort of background dance of kernel, Linker and Loader transforms a simple binary file on disk into a living, breathing process.


---

## Follow Along

We will use a standard Linux environment. If you are on macOS or Windows, use Docker to get a deterministic Linux behavior (specifically for x86-64 relocation types).

A Note on Architecture (Apple Silicon & Windows ARM): If you are running on an ARM chip (M1/M2/M3 or Snapdragon), don't worry.

macOS: Docker uses Rosetta to transparently translate x86-64 instructions to ARM with near-native performance.

Windows: Docker (via WSL 2) automatically uses QEMU emulation to run these binaries, though it may be slower than native execution.

Curious how this magic works? We’ve included a deep dive on how macOS injects Rosetta into Linux and how binfmt_misc registers these translators in Appendix A: The "Magic" of Cross-Architecture Execution.


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
	gcc -o dynamic_app main.c ./libmath.so -Wl,-rpath,'$ORIGIN'
```

**2. Start the container**

```bash
# Force x86-64 to align with our assembly examples
docker run --rm -it \
  --platform=linux/amd64 \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  -v "$PWD"/code:/code -w /code \
  ubuntu:22.04 bash

# Install tools
apt-get update && apt-get install -y build-essential binutils gdb strace file vim git
```

**3. Compile the project:**

```bash
root@cac6f9acdae7:/code# make dynamic_app
gcc -shared -fPIC -o libmath.so math.c
gcc -o dynamic_app main.c ./libmath.so -Wl,-rpath,'RIGIN'

root@cac6f9acdae7:/code# ls
Makefile  dynamic_app  libmath.so  main.c  math.c
```

---

You type ./dynamic_app and hit Enter.

Your shell calls fork() to create a child process. That child process calls execve("./dynamic_app"). Simple enough?

Not quite.

1.1 The Hardware Gate
If we were to freeze time right now, we would see that simply typing this command triggered a chaotic symphony of hardware events.

Step 1: The Wake Up Your shell (Bash/Zsh) was actually asleep, blocked on a read() system call waiting for input. When you hit Enter, the kernel’s TTY driver saw the newline, decided the input buffer was ready, and woke up the shell. (Curious how the characters appeared on your screen if the shell was asleep? See Appendix D: The Keyboard Dance for the deep dive on TTYs and PTYs).

Step 2: The fork() Syscall (Cloning) The shell parses your command and decides to run a new program. But first, it must duplicate itself. It calls fork().

This triggers a violent hardware transition. The CPU switches from User Mode (Ring 3) to Kernel Mode (Ring 0). It saves the entire state of the processor and creates a near-identical copy of the shell (the child process). This child is now running, but it is still running the shell's code.

Step 3: The execve Syscall (The Transformation) The child process now invokes execve. This is the Point of No Return. The execve syscall tells the kernel: "Replace my brain."

The Trap: The CPU executes the syscall instruction (opcode 0F 05).

The Switch: The hardware instantly elevates privileges to Ring 0.

The Lookup: It consults the Model Specific Registers (MSRs) to jump straight into the kernel's entry point (entry_SYSCALL_64).

The Replacement: The kernel discards the child's old memory map (the shell code) and prepares to load the new binary.

(For the hardcore details on IDTs, MSRs, and the "Hidden Storm" of context switching, see Appendix C: The Hardware Storm).

The Operating System has taken the wheel. It is now sitting in Ring 0 with the file path ./dynamic_app and a mandate to execute it.

### 1.2 The Kernel: `fs/exec.c`

Once inside the kernel, execution eventually reaches `do_execveat_common` in [fs/exec.c](https://elixir.bootlin.com/linux/v6.8/source/fs/exec.c#L1908).

The kernel opens the file and iterates through a list of "binary handlers" to find one that understands the file format. Since this is an ELF file, it lands in `load_elf_binary` in [fs/binfmt_elf.c](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L819).

### 1.3 The Magic Check

First, the kernel validates that this is actually an ELF file. It reads the first 4 bytes. If they aren't `0x7F 'E' 'L' 'F'`, it rejects the file immediately.

```c
// linux/fs/binfmt_elf.c (https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L843)
if (memcmp(loc->elf_ex.e_ident, ELFMAG, SELFMAG) != 0)
    goto out;

```

---

## Mapping the Memory

The kernel does **not** care about "Sections" (like `.text` or `.data`) — those are for the linker. The kernel cares about **Segments** (Program Headers).

### 2.1 Iterating Segments (`load_elf_binary`)

The kernel loops over the Program Headers (`PT_LOAD`) to figure out what to load.
[View Source in binfmt_elf.c](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c%23L1066)

```c
// Simplified logic from fs/binfmt_elf.c
for(i = 0, elf_ppnt = elf_phdata; i < loc->elf_ex.e_phnum; i++, elf_ppnt++) {
    if (elf_ppnt->p_type == PT_LOAD) {
        // Create the memory mapping
        error = elf_map(bprm->file, load_bias + vaddr, elf_ppnt, ...);
    }
}

```

It sees two main `PT_LOAD` segments in `dynamic_app`:

1. **Text Segment (Read-Execute):** Your code.
2. **Data Segment (Read-Write):** Your global variables.

Lets see how it looks in our binary:



### 2.2 The Great Lie: Demand Paging

When we say "mapped," we do not mean "copied to RAM." The `elf_map` call essentially creates a **VMA (Virtual Memory Area)** struct. It tells the Memory Management Unit (MMU):
*"If the CPU asks for address `0x400000`, the data is located in `dynamic_app` on disk at offset 0."*

The physical RAM is **empty**. When the CPU tries to execute the first instruction, a **Page Fault** fires. The kernel catches it, pauses the CPU, fetches the data from disk to RAM, and resumes.

### 2.3 The Fork in the Road: `PT_INTERP`

The kernel checks for a specific header: `PT_INTERP`.
[View Source](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L868)

```c (pseudo-code)
if (elf_ppnt->p_type == PT_INTERP) {
    elf_interpreter = open_exec(interp_name); // e.g., /lib64/ld-linux-x86-64.so.2
    ...
        load_elf_binary(..., interpreter); // Recursively map the interpreter!
}

```

Because `dynamic_app` has this header, the kernel maps the **Dynamic Loader** (`ld-linux.so`) into memory and sets the instruction pointer to the **Loader's entry point**, not yours.
[View Source](https://elixir.bootlin.com/linux/v6.8/source/fs/binfmt_elf.c#L1200)
---

## The Loader Takes Control (User Mode)

Control returns to User Mode. The program running is now the dynamic loader (`ld-linux.so`), appearing in `glibc` source as `elf/rtld.c`.

### 3.1 Self-Relocation

The loader is just another piece of code. But unlike your app, it runs *before* relocations are applied. It must be written carefully to relocate itself.

This happens in `_dl_start` inside `elf/rtld.c`:
[View Source (glibc)](https://sourceware.org/git/%3Fp%3Dglibc.git%3Ba%3Dblob%3Bf%3Delf/rtld.c%3Bh%3D877c804%3Bhb%3DHEAD%23l483)

```c
static ElfW(Addr) __attribute_used__ _dl_start (void *arg) {
  /* The loader relocates itself here! */
  bootstrap_map.l_addr = elf_machine_load_address ();
}

```

### 3.2 Dependency Discovery

Once functional, the loader looks at your `dynamic_app`'s `_DYNAMIC` section to find `DT_NEEDED` tags.
[View Source (dl-deps.c)](https://www.google.com/search?q=https://sourceware.org/git/%3Fp%3Dglibc.git%3Ba%3Dblob%3Bf%3Delf/dl-deps.c%3Bh%3D546c18f%3Bhb%3DHEAD%23l182)

It recursively loads every needed library (`libmath.so`, `libc.so`) by `mmap`-ing them into memory.

### 3.3 Visualizing the Scaffolding (PLT & GOT)

The loader populates the **Global Offset Table (GOT)**.
For **Lazy Binding** (the default), the loader does not resolve the function addresses immediately. Instead, it fills the GOT slots with the address of the *lookup code* inside the PLT.

---

## Act IV: The PLT/GOT Dance (Lazy Binding)

The loader is done. It jumps to your program's `_start`. Eventually, your `main()` calls `add()`.

### 4.1 The Trampoline

Your code calls `add@plt`.

```assembly
0x401030 <add@plt>:
  jmp    *0x2fe2(%rip)        # 1. Jump to address stored in GOT
  push   $0x0                 # 2. Push relocation index (if GOT pointed back here)
  jmp    0x401020 <_init+...> # 3. Jump to dynamic resolver

```

### 4.2 The Resolution (GDB Trace)

Let's watch the resolution happen live.

1. **Start GDB:** `gdb -q ./dynamic_app`
2. **Break:** `break main`, then `run`.
3. **Inspect GOT (Before):**
```bash
(gdb) x/gx 0x404018
0x404018: 0x0000000000401036  <-- Points BACK to the PLT (unresolved)

```


4. **Step:** `si` (step instruction) until you return.
5. **Inspect GOT (After):**
```bash
(gdb) x/gx 0x404018
0x404018: 0x00007ffff7fc50f9  <-- Real address in libmath.so!

```



The loader has patched the memory live. The next time you call `add()`, it jumps straight to the library.

---

## Act V: The Flashback (Build Time)

We have successfully run `add(5, 10)`. But how did we get here?

* How did the executable get the PLT?
* What is `crt1.o`?
* How did the Linker weave the files together?

Let’s rewind time to the `make` command and dissect the **Compilation** and **Linking** stages.

### 5.1 Compilation: The "Holey" Object File

When you run `gcc -c main.c`, GCC acts as a driver. It runs `cc1` (compiler) and `as` (assembler) to produce `main.o`.

At this stage, the compiler **does not know** where `add` is. It creates a **Relocation Entry**—a "To-Do" note for the linker.

Let’s inspect `main.o`'s Relocation Table:

```bash
readelf -r main.o

```

**Output:**

```text
Relocation section '.rela.text' at offset 0xc8 contains 1 entry:
  Offset          Info           Type           Sym. Value    Sym. Name + Addend
00000000000e  000b00000004 R_X86_64_PLT32    0000000000000000 add - 4

```

* **Offset `0x0e**`: This is the exact byte in the `.text` section where the `call` instruction argument sits.
* **Type `PLT32**`: This tells the Linker: *"I need a 32-bit relative address to a PLT entry for symbol `add`."*

### 5.2 The Silent Partner: `crt0` / `crt1.o`

You wrote `main()`, but `main()` is not the first function to run. The entry point is actually `_start`.

When you run `gcc`, it silently adds standard startup files to your command.

* **What is it?** Historically called `crt0.o` (C RunTime 0), on modern Linux it is usually `crt1.o` (Start), `crti.o` (Init), and `crtn.o` (End).
* **Where does it come from?** It is provided by `glibc`.
* **What does it do?** `_start` sets up the stack, aligns variables, and calls `__libc_start_main`, which eventually calls your `main()`.

You can see this hidden injection by running GCC with verbose flags:

```bash
gcc -v -o dynamic_app main.o ./libmath.so 2>&1 | grep collect2

```

You will see `crt1.o` passed to the linker command line automatically.

### 5.3 The Linker (`ld`): The Mosaic Artist

Now `ld` runs. It has `main.o`, `crt1.o`, and `libmath.so`. It needs to create one file.

#### Step 1: The Blueprint (Linker Script)

The Linker follows a script to decide memory layout.
[View Source (ldlang.c)](https://www.google.com/search?q=https://sourceware.org/git/%3Fp%3Dbinutils-gdb.git%3Ba%3Dblob%3Bf%3Dld/ldlang.c%3Bh%3DHEAD%23l7660)

```bash
ld --verbose | grep -A 5 "SECTIONS"

```

It says: *"Put all input `.text` sections into one output `.text` section starting at `0x400000`."*

#### Step 2: The Mosaic (Weaving Sections)

The Linker maps the output file into memory (using `mmap`). It then performs a "Scatter-Gather" copy.

1. It copies `crt1.o`'s `.text` to the beginning of the output buffer.
2. It copies `main.o`'s `.text` right after it.
3. It updates its internal symbol map: `main` is no longer at offset `0`; it is now at `0x401136`.

#### Step 3: Synthesis (PLT & GOT)

The Linker sees the `R_X86_64_PLT32` relocation for `add`. It checks `libmath.so` and sees `add` is a shared symbol.

1. **Allocate:** It calls [`bfd_elf_allocate_dynrelocs`](https://www.google.com/search?q=%5Bhttps://sourceware.org/git/%3Fp%3Dbinutils-gdb.git%3Ba%3Dblob%3Bf%3Dbfd/elf64-x86-64.c%3Bh%3DHEAD%23l3735%5D(https://sourceware.org/git/%3Fp%3Dbinutils-gdb.git%3Ba%3Dblob%3Bf%3Dbfd/elf64-x86-64.c%3Bh%3DHEAD%23l3735)) to reserve space in `.plt` and `.got`.
2. **Write:** It calls [`elf_x86_64_finish_dynamic_symbol`](https://www.google.com/search?q=%5Bhttps://sourceware.org/git/%3Fp%3Dbinutils-gdb.git%3Ba%3Dblob%3Bf%3Dbfd/elf64-x86-64.c%3Bh%3DHEAD%23l4330%5D(https://sourceware.org/git/%3Fp%3Dbinutils-gdb.git%3Ba%3Dblob%3Bf%3Dbfd/elf64-x86-64.c%3Bh%3DHEAD%23l4330)) to write the actual machine code instructions (the "trampoline") into the new PLT section.

#### Step 4: The Patching (Relocations)

The Linker now iterates through the `.rela.text` list from `main.o`.
**It does not scan the code.** It goes exactly to offset `0x401144` (where the call to `add` is).

* **Math:** `PLT Address` - `Current PC` + `Addend`.
* **Action:** It overwrites the 4 zero-bytes with the result.

#### Step 5: Sections to Segments

Finally, the Linker maps the Output Sections (`.text`, `.data`) to Program Headers (`PT_LOAD`).
[View Source (map_input_to_output_sections)](https://www.google.com/search?q=https://sourceware.org/git/%3Fp%3Dbinutils-gdb.git%3Ba%3Dblob%3Bf%3Dld/ldlang.c%3Bh%3DHEAD%23l4575)

It groups Read-Only sections (`.text`, `.plt`, `.rodata`) into one Segment so the Kernel can protect them efficiently.

### 5.4 Why did we need `libmath.so`?

If `add` is resolved at runtime, why did the Linker need the file?

1. **Verification:** To prove `add` exists.
2. **Versioning:** To record that we need `add` version `GLIBC_X.Y`.
3. **Receipt:** To write the `DT_NEEDED` tag so the Loader knows to load `libmath.so`.

---

### Conclusion: The Full Cycle

1. **Compiler:** Generates `main.o` with Relocation Entries ("Holes").
2. **Linker:**
* Injects `crt1.o` (The true entry point).
* Weaves `.text` sections together based on the Script.
* Synthesizes PLT/GOT for dynamic symbols.
* Patches the holes using the Relocation Table.


3. **Kernel:** Maps the Segments and invokes the Interpreter.
4. **Loader:** Lazy binds the symbols.
5. **Execution:** `main()` runs.

The "simple" act of running `./app` is a relay race passing the baton between the Compiler, Linker, Kernel, and Dynamic Loader. Understanding who holds the baton at each stage is the key to mastering system engineering.




Appendix A: The "Magic" of Cross-Architecture Execution
If you ran this lab on an Apple Silicon Mac (M1/M2/M3) or a Windows ARM laptop, you might have noticed something strange: it just worked.

You compiled x86-64 assembly code, linked it into an x86-64 ELF binary, and ran it on an ARM64 processor. How is that possible?

The Two Approaches
When the CPU architecture doesn't match the binary, the OS must translate instructions. There are two main ways this happens today:

1. The macOS Way (Hardware-Assisted Injection) Docker Desktop on Mac boots a lightweight Linux VM. Because your CPU is ARM, the Linux Kernel inside that VM is ARM64. However, Apple allows the virtualization framework to inject the Rosetta translator binary directly from macOS into that Linux VM.

Mechanism: Docker mounts Rosetta into the VM (via VirtioFS).

Speed: Near-native. Apple added hardware support (Total Store Ordering) to their chips specifically to make this translation fast.

2. The Windows Way (Software Emulation) Docker Desktop on Windows runs inside WSL 2, which is also a real Linux VM running a native ARM64 kernel.

Mechanism: When you run an x86 Linux binary, WSL 2 relies on QEMU, a standard open-source emulator.

Speed: Slower. Unlike macOS, Windows does not yet expose its proprietary translator ("Prism") to Linux containers. Prism currently only accelerates Windows .exe files, leaving Linux binaries to rely on pure software emulation.

Appendix B: Deep Dive — How Rosetta Registers Itself
You might wonder: How does a standard Linux kernel know to use a proprietary Apple binary to run a file?

It uses a standard Linux kernel feature called binfmt_misc (Binary Formats Miscellaneous). This feature allows you to tell the kernel: "If you see a file that starts with these specific bytes, pass it to this interpreter."

The Registration Command
When Docker starts the Linux VM on your Mac, it runs a command effectively like this:

Bash

# 1. Mount the Rosetta binary from macOS into Linux
mount -t virtiofs rosetta /mnt/rosetta

# 2. Register the Magic Bytes with the Kernel
echo ':rosetta:M::\x7fELF\x02\x01...:\xff\xff...:/mnt/rosetta:OCF' > /proc/sys/fs/binfmt_misc/register
Decoding the Magic String
That cryptographic-looking string :rosetta:M::\x7fELF... is actually a precise configuration:

\x7fELF: The "Magic Number." It tells Linux to look for the ELF header signature.

\x02: The byte indicating 64-bit architecture.

\x3e: The byte 0x3E is the machine ID for AMD64 / x86_64.

/mnt/rosetta: The Interpreter. This tells the kernel, "Don't execute this file. Instead, launch Rosetta and pass this file as an argument."

OCF Flags:

O (Open): Open the file immediately (crucial for containers).

C (Credentials): Run with the user's current permissions.

F (Fix): Keep Rosetta loaded in memory for performance.

The Execution Flow
So, when you typed ./dynamic_app in the lab:

The ARM Linux Kernel checked the file header: 0x7fELF...0x3E.

It matched the rosetta rule registered in binfmt_misc.

Instead of crashing with "Exec format error," the kernel quietly executed: /mnt/rosetta ./dynamic_app

Rosetta JIT-compiled the x86 instructions to ARM64 on the fly.

This is why strace works, gdb works, and the app runs, even though the hardware speaks a completely different language.



Appendix C: The Hardware Storm (IDT, MSRs & Syscalls)
In Act I, we glossed over the "Hardware Gate." Here is exactly what happens at the silicon level when you interact with the kernel.

1. The Interrupt Descriptor Table (IDT)
When you press a key, the keyboard controller sends an electrical signal to the CPU. The CPU pauses execution and consults the IDT—a special table in memory that maps "Interrupt Numbers" to code addresses (Interrupt Service Routines).

The Context Switch: The CPU cannot just jump to the handler. It must switch stacks. It looks up the Task State Segment (TSS) to find the Interrupt Stack Table (IST)—a known "safe" place in kernel memory.

The Save: It pushes the user's current Register Instruction Pointer (RIP) and Stack Pointer (RSP) onto this kernel stack.

The Handler: Only then does the kernel code run to process the scancode.

2. The syscall Instruction (Fast Path)
Historically, System Calls used the IDT (via int 0x80). This was slow. Modern x86-64 uses the specialized syscall instruction.

MSR_LSTAR: When the OS boots, it writes the address of its syscall entry function (entry_SYSCALL_64) into a special CPU register called MSR_LSTAR.

The Jump: When your app runs syscall, the CPU copies RIP to RCX (to save your place), loads the address from MSR_LSTAR into RIP, and instantly forces the CPL (Current Privilege Level) to 0. This bypasses the complex IDT lookup entirely.

3. KPTI (Kernel Page Table Isolation)
On widely used CPUs affected by Meltdown, this transition is even more complex. The kernel cannot keep its secrets mapped in User Mode memory.

User Mode: The Page Tables only show User space + a tiny sliver of Kernel space (just enough to handle the trap).

The Dance: Upon entering the kernel, the CPU must swap to a different set of Page Tables that map the full kernel memory. This "CR3 switch" is costly but necessary for security.

Appendix D: The Keyboard Dance (TTY Architecture)
One of the most confusing parts of Unix is typing into a terminal.

Myth: "The shell reads my keystrokes and draws them."

Reality: The shell is usually asleep. The Kernel (and your GUI) does the drawing.

Here is the flow of a single keystroke (e.g., the letter k) inside a terminal emulator like iTerm or GNOME Terminal running Zsh.

1. The Setup (Pseudo-Terminals)
Since you don't have a physical teletype printer, the Kernel provides a PTY (Pseudo-TTY) pair:

Master side: Held by iTerm (the GUI).

Slave side: Held by Zsh (the Shell).

2. The Journey of 'k'
Hardware: You press k. macOS/Windows sends the event to the iTerm window.

The GUI (iTerm): iTerm receives the event. It writes the byte k into the Master file descriptor. Note: It does NOT draw k on the screen yet.

The Kernel (Line Discipline): The TTY driver receives k on the Master side. It checks its mode:

Canonical Mode (Cooked): Used by simple apps. The kernel buffers the k (waiting for Enter). Crucially, if ECHO is on, the Kernel writes k back to the Master output.

Raw Mode: Used by editors (Vim) and smart shells (Zsh). The kernel passes k directly to the Slave input without buffering or echoing.

The Shell (Zsh): Zsh (blocked on read) wakes up because data arrived on the Slave. It reads k. It decides to color it syntax-green. It writes green('k') to its stdout (the Slave).

The Loop Closes: The Kernel moves the data from Slave-out to Master-in.

The Render: iTerm's event loop wakes up because data arrived on the Master. It reads the byte k, parses the color, and finally draws the pixels on your monitor.

The Result: You see the character appear instantly, but it took a round trip through the GUI, the Kernel, the Shell, and back to the GUI to make it happen.