# System-binary sweep — adjudication

Acceptance test 3: run `symsplit` against stock system binaries and their DSO
closures, and **adjudicate every flag**. A "flag" is a `SPLIT` verdict (the
only verdict that sets a nonzero exit and asserts a real bug). The benign
verdicts (`WEAK-PATTERN`, `VERSIONED-BENIGN`, `HIDDEN-BENIGN`,
`NOT-DYNAMIC-BENIGN`, `ALLOWLISTED`, `NO-SPLIT`) are informational.

## Environment

Debian (stable-slim), aarch64, gcc + binutils, `pyelftools==0.31`. Reproduce:

```
make sweep      # from tools/symsplit, inside the container harness
```

## Result

Scanned every dynamically-linked ELF in `/usr/bin`, `/bin`, `/usr/sbin`,
`/sbin` (the `make sweep` default), plus `python3` composed with a real
C-extension via `--module`.

| metric | value |
|---|---|
| binaries scanned | 788 |
| binaries with ≥1 duplicate-symbol finding | 468 |
| **`SPLIT` verdicts** | **0** |
| errors / crashes | 0 |

Finding distribution across all closures:

| verdict | count |
|---|---|
| `WEAK-PATTERN` | 2956 |
| `NO-SPLIT` | 2280 |
| `VERSIONED-BENIGN` | 166 |
| `SPLIT` | **0** |

**Zero unadjudicated flags** — because there are zero `SPLIT` flags. This is
the expected result for a healthy system, and it is the credibility claim: the
predicate is tight enough that thousands of real cross-boundary duplicates on a
stock system produce no false positives. (If it flagged many, the heuristic
would be too loose and would need tightening.)

## Why the duplicates that DO exist are not splits (worked examples)

The sweep is not vacuous — 420 binaries have genuine duplicate symbols across
their closures. Each was cleared for a concrete, ELF-visible reason:

1. **`bash` — `getenv` / `putenv` (`NO-SPLIT`).** `bash` ships its own
   `getenv`/`putenv` (sizes 356/196) that duplicate libc's (236/284). Not a
   split: libc **retains an interposable `GLOB_DAT`/`JUMP_SLOT`** to these
   names, so libc's own internal uses bind to the winning global-scope copy
   (`bash`'s). Both writer and readers unify on one copy. This is exactly the
   intended interposition of the allocator/environment functions.

2. **`ls` — `error`, `error_at_line` (`WEAK-PATTERN`).** The exe provides a
   strong definition; libc provides a **weak** one. global+weak is the
   deliberate override idiom, not a same-strength collision. (2956 such pairs
   system-wide — overwhelmingly libc weak aliases.)

3. **`chage` — `explicit_bzero` (`VERSIONED-BENIGN`).** Defined by both
   `libbsd` (`LIBBSD_0.8`) and glibc (`GLIBC_2.25`) under **different version
   nodes**. Versioned references bind precisely; the definitions cannot
   collide.

4. **version-node pseudo-symbols (`GLIBC_2.x`, filtered).** glibc's `.dynsym`
   contains `STT_OBJECT`/`SHN_ABS` entries whose names are version nodes
   (`GLIBC_2.17`, …). These are not real API and are filtered out during
   parsing, so they never reach the verdict stage.

## New rules / heuristics adopted as a result of the sweep

- **Filter version-node pseudo-symbols** (`SHN_ABS` + name ∈ verdef nodes).
  Without this they appeared as thousands of harmless `NO-SPLIT` duplicates
  between `libc.so.6` and `ld-linux.so`, cluttering output. They can never
  split (nothing references a version node as a symbol).
- **`NO-SPLIT` and shadow findings are hidden by default** (shown with
  `--all`). The default table surfaces only actionable/benign-classified real
  dups, keeping a clean-system sweep quiet.

No `SPLIT` verdict required adjudication into a new allowlist entry, because
none occurred. The allowlist (`data/allowlist.txt`) is pre-seeded with the
known intentional interposers (allocator, `operator new`/`delete`, `__cxa_*`,
jemalloc/tcmalloc, sanitizer runtimes, pthread shims) so that if a future
target legitimately interposes one of those, it is reported `ALLOWLISTED`
rather than `SPLIT`.
