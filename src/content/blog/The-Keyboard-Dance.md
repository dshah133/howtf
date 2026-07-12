---
title: "The keyboard dance: what happens before your shell wakes up"
description: "Your shell doesn't read your keystrokes — it's asleep. The kernel and your terminal emulator run the whole show: PTYs, line discipline, and why Ctrl+C sometimes can't save you."
date: 2026-07-12
tags: [tty, kernel, terminals]
draft: true
---

> This post grew out of an appendix to [What actually happens between exec() and main()](/blog/ELF-Linking-101/) — it kept answering a different question, so it became its own dive.

One of the most confusing parts of Unix is typing into a terminal.

- **The myth:** "The shell reads my keystrokes and draws them on the screen."
- **The reality:** the shell is usually asleep. The **kernel** and our **terminal emulator (GUI or TUI)** do most of the work.

## See it for yourself: the shell is "asleep"

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

## The setup: how the pipe is built (PTY master/slave)

So how does your shell "read from your terminal window" at all?

Modern terminals are built on **pseudo-terminals (PTYs)**, a master/slave pair that behaves like a virtual serial terminal.

At a high level:

- The **terminal emulator** (GNOME Terminal, iTerm, Windows Terminal via WSL, etc.) owns the **PTY master**.
- The **shell** (bash/zsh/fish) owns the **PTY slave**.
- The kernel sits in the middle and implements "terminal semantics" (line editing modes, echo, job control signals, window size, etc.).

Here's the typical creation sequence:

1. **Open the master**

- The terminal emulator calls `posix_openpt()` (or `openpty()` / `forkpty()`).
- The kernel returns a **master FD** (e.g., FD 3). This is the emulator's "control end."

2. **Make the slave usable and discover it**

- The emulator calls `grantpt()` and `unlockpt()` (permissions + unlock the slave).
- It calls `ptsname()` to learn the slave path, e.g. `/dev/pts/5`.

3. **Spawn the shell and wire its stdio**

- The emulator forks a child process (or `forkpty()` does it for us).
- In the child, it typically does:
  - `setsid()` to start a new session
  - `ioctl(TIOCSCTTY)` to make the **slave** the **controlling terminal**
  - `dup2(slave, 0)`, `dup2(slave, 1)`, `dup2(slave, 2)` to replace stdin/stdout/stderr
  - `execve()` to run the shell

**Result:** the shell thinks it is connected to a hardware terminal on FD 0/1/2, but it's actually connected to a kernel PTY device whose other end is controlled by the emulator.

## The flow: the journey of a single `k`

Here's the full trip a single keystroke takes, from finger to pixels.

### Step 1: the hardware spark

- You press `k`. The keyboard triggers an interrupt; the kernel's input stack translates scancodes into a key event.
- Because many apps/windows exist, the OS's GUI stack (compositor/window system) acts as a traffic cop and delivers the "k pressed" event to the focused terminal window.

### Step 2: the terminal emulator (GUI or TUI)

- The terminal emulator receives the event.
- It **writes the byte** `k` into the **PTY master FD**.
- **Important:** it does *not* draw `k` yet. It has only injected input into the PTY pipeline.

### Step 3: the kernel TTY layer (line discipline)

Now the kernel's TTY subsystem becomes the middleman. This is where "terminal behavior" lives.

- **Canonical mode (`ICANON`)**: the kernel buffers input into a line and delivers it to the slave only when Enter is pressed (classic cooked mode).
- **Noncanonical ("raw-ish") mode**: shells and editors usually disable `ICANON` so they can do their own line editing; exactly which flags are enabled varies.
- **Echo (`ECHO`)**: if enabled, the kernel itself can echo typed characters back through the PTY stream.
- **Signals (`ISIG`)**: if enabled, special control characters trigger signals:
  - `VINTR` (often Ctrl+C, byte `0x03`) → `SIGINT`
  - `VQUIT` (often Ctrl+\\) → `SIGQUIT`
  - `VSUSP` (often Ctrl+Z) → `SIGTSTP`

So the folk claim is correct, with one precise condition:

> Ctrl+C becomes `SIGINT` **only if** the terminal is configured with `ISIG` and `VINTR` set appropriately.

### Step 4: the shell (zsh/bash)

- The shell was blocked on `read(0, ...)`. When input arrives on the slave side, it wakes up.
- It reads `k` and updates its internal line buffer.
- A "smart" shell may decide to render it as syntax-green (or do completion previews, etc.).
- It writes the resulting bytes (including ANSI escape sequences) to **stdout** (FD 1).

### Step 5: the loop closes

- FD 1 is still the PTY **slave**.
- The kernel transfers the output stream from slave → master.

### Step 6: rendering (pixels happen here)

- The terminal emulator's event loop wakes up because there's data on the **master**.
- It reads the bytes, parses ANSI escape codes, and **renders glyphs** (possibly colored) into pixels on your screen.

**Net result:** the character appearing on screen is not the shell "drawing." It's the emulator rendering output bytes that flowed *back* through the PTY.

## Why emulate? (why not read hardware directly?)

Why go through this PTY dance? Why can't `bash` just read the keyboard device directly?

1. **Isolation (the traffic cop problem)**
   There's one physical keyboard and many processes. If every program read from the hardware device directly, our `k` would land in *every* terminal and *every* app. We rely on the GUI stack to route events to the focused terminal, which then injects bytes into the correct PTY.

2. **Virtualization (SSH / remote terminals)**
   Often the "keyboard" isn't local at all. When you SSH into a server, the server has no physical keyboard attached to your process. The SSH daemon typically allocates a PTY for the remote session so the remote shell gets real terminal semantics (echo control, job control, Ctrl+C handling, window resize).

3. **Necessity (why not just pipes?)**
   Plain pipes (`|`) move bytes, but they don't carry terminal semantics:

- **Signals:** Ctrl+C wouldn't automatically become `SIGINT` via `VINTR`/`ISIG`.
- **Geometry:** editors like `vim` wouldn't learn rows/cols (`TIOCGWINSZ`).
- **Echo/security:** `sudo` couldn't reliably disable echo for password entry.
- **Job control:** foreground/background process groups and terminal ownership wouldn't behave like a "real terminal."

PTYs exist because interactive programs need more than a byte stream. They need a *terminal*.

## The modern flaw (when the GUI crashes)

By moving the terminal emulator into user space (a GUI app like GNOME Terminal/iTerm), we introduced a fragility. The interactive chain becomes:

**Keyboard → kernel input → GUI stack → terminal emulator → PTY master → kernel TTY → PTY slave → shell → (back outward)**

If the **GUI stack** (compositor/window server) or the **terminal emulator** hangs:

1. You press Ctrl+C.
2. The kernel still receives the keyboard interrupt and produces an input event…
3. …but the event never gets delivered through the GUI stack to the terminal emulator.
4. The terminal emulator never writes `0x03` into the PTY master.
5. The TTY line discipline never sees `VINTR`, so it never generates `SIGINT`.
6. **Result:** you can't use Ctrl+C as your emergency stop *from that frozen GUI terminal*, even though the kernel is alive.

This is why production folks love having *more than one control plane*.

### The Linux escape hatch (virtual consoles)

Linux keeps **virtual consoles** (`tty1`–`tty6`) that bypass the GUI stack entirely and use the kernel console subsystem. On many systems you can switch with:

- `Ctrl + Alt + F3` (or F2/F4/F5/F6)

These give you an "emergency stop" even if the desktop is frozen. macOS, unfortunately, does not provide an equivalent user-facing virtual console switch in the same way.
