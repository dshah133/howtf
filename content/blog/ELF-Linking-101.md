---
title: "ELF Linking 101 for People Who Ship Big Binaries"
description: "A practical guide to ELF sections, segments, and symbols—so the next post's bug makes sense."
created: 2025-12-28
tags:
  - elf
  - linux
  - linking
  - systems-programming
---

A practical "what am I looking at?" guide to sections, segments, and symbols—so the next post's bug makes sense.

If you've ever stared at a production crash and thought, "How can the same process behave like it has two different copies of the same library?"—you're already in ELF country.

This first post builds a working mental model of ELF linking on Linux: what the linker writes, what the loader reads, and why `.symtab` and `.dynsym` are not interchangeable. In the next post, we'll use that model to reproduce a real class of failure: "device discovery works… until a different backend loads and suddenly nothing is found."

## Who this is for

You build and deploy large native binaries (often with plugins, Python extension modules, or multiple backends in one process). You don't need to be an ELF expert, but you do need to be dangerous with:

- `readelf`
- `nm`
- `objdump`
- `ldd`
- and the runtime linker's debug knobs

## The 2-view model: link-time vs run-time

ELF deliberately supports two different "views" of the same file:

- **Linker / section view**: "How do I combine `.o` files into a final artifact?"
- **Loader / segment view**: "What memory ranges should I map at runtime, with what permissions?"

That's why you'll see both sections (think: "compiler/linker organization") and segments (think: "OS/runtime mapping") in the same file.
