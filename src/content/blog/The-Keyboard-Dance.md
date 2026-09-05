---
title: "The keyboard dance: what happens before your shell wakes up"
description: "Follow a keystroke through the terminal emulator, PTY, and shell, including line editing, echo, and why a frozen terminal can stop Ctrl+C from reaching the process."
date: 2026-07-12
tags: [tty, kernel, terminals]
draft: true
---

> This expands the terminal-input section of [What actually happens between exec() and main()](/blog/ELF-Linking-101/).

Typing a character into a terminal involves the kernel, the terminal emulator, and the shell. While the shell waits for input, the other two handle the path from a keyboard event to bytes the shell can read.

## See it for yourself: the shell is "asleep"

Start by tracing a shell while it waits for input.

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

In this trace, the shell is blocked on `read(0, ...)`, waiting for input on standard input. It is not polling the keyboard.

## Connecting the emulator and shell

The connection uses a **pseudo-terminal (PTY)**, a master/slave pair that behaves like a virtual serial terminal. The emulator owns the master end, and the shell uses the slave end. Between them, the kernel implements line modes, echo, job control signals, and window-size handling.

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

The shell’s standard input, output, and error now refer to the PTY slave. The emulator controls the other end.

## The flow: the journey of a single `k`

Consider what happens when you press `k` in a focused terminal window.

### Step 1: the input event

The key press reaches the kernel’s input stack as a key event. The GUI stack routes it to the focused terminal window.

### Step 2: the terminal emulator (GUI or TUI)

The terminal emulator writes the byte `k` to the PTY master. At this point it has supplied input to the terminal, but it has not yet drawn the resulting character.

### Step 3: the kernel TTY layer (line discipline)

The kernel’s TTY layer applies the terminal settings to the incoming byte:

- **Canonical mode (`ICANON`)**: the kernel buffers input into a line and delivers it to the slave only when Enter is pressed (classic cooked mode).
- **Noncanonical ("raw-ish") mode**: shells and editors usually disable `ICANON` so they can do their own line editing; exactly which flags are enabled varies.
- **Echo (`ECHO`)**: if enabled, the kernel itself can echo typed characters back through the PTY stream.
- **Signals (`ISIG`)**: if enabled, special control characters trigger signals:
  - `VINTR` (often Ctrl+C, byte `0x03`) → `SIGINT`
  - `VQUIT` (often Ctrl+\\) → `SIGQUIT`
  - `VSUSP` (often Ctrl+Z) → `SIGTSTP`

> Ctrl+C becomes `SIGINT` **only if** the terminal is configured with `ISIG` and `VINTR` set appropriately.

### Step 4: the shell (zsh/bash)

When input becomes available on the slave side, the shell’s `read()` returns. The shell updates its line buffer and writes output to standard output, which is also the PTY slave. A shell that displays syntax highlighting or completion previews includes the appropriate escape sequences in that output.

### Step 5: the loop closes

The kernel transfers those output bytes from the PTY slave to the master.

### Step 6: rendering (pixels happen here)

The emulator wakes when output is available on the master. It reads the bytes, interprets the escape sequences, and draws the characters. The visible `k` therefore comes from output that has traveled back through the PTY.

## Why emulate? (why not read hardware directly?)

The PTY gives an interactive program terminal behavior without tying it to a particular keyboard or display. The GUI stack routes local keyboard events to the focused window, which sends bytes to the corresponding PTY.

The same interface works remotely. An SSH server can allocate a PTY for a session even though the user’s keyboard is on another machine.

Plain pipes move bytes, but do not supply the terminal controls these programs expect. Programs use those controls for signals such as Ctrl+C through `VINTR` and `ISIG`, window dimensions through `TIOCGWINSZ`, disabling echo for passwords, and foreground/background job control.

## When the GUI input path freezes

This input path depends on the GUI stack and terminal emulator continuing to handle events:

**Keyboard → kernel input → GUI stack → terminal emulator → PTY master → kernel TTY → PTY slave → shell → (back outward)**

If the **GUI stack** (compositor/window server) or the **terminal emulator** hangs:

1. You press Ctrl+C.
2. The kernel still receives the keyboard interrupt and produces an input event…
3. …but the event never gets delivered through the GUI stack to the terminal emulator.
4. The terminal emulator never writes `0x03` into the PTY master.
5. The TTY line discipline never sees `VINTR`, so it never generates `SIGINT`.
6. **Result:** you can't use Ctrl+C as your emergency stop *from that frozen GUI terminal*, even though the kernel is alive.

A separate way to reach the machine can help when that GUI input path is frozen.

### The Linux escape hatch (virtual consoles)

Linux keeps **virtual consoles** (`tty1`–`tty6`) that bypass the GUI stack entirely and use the kernel console subsystem. On many systems you can switch with:

- `Ctrl + Alt + F3` (or F2/F4/F5/F6)

These give you an "emergency stop" even if the desktop is frozen. macOS, unfortunately, does not provide an equivalent user-facing virtual console switch in the same way.
