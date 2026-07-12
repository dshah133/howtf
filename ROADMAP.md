# Content roadmap

Priority order (each post opens with a real bug, per the house style):

1. **The RDMA symbol collision** — the production incident teased in the ELF
   post's conclusion: two collective-communication libraries linked into one
   binary, symbol interposition silently redirecting RDMA verb calls to the
   wrong device. The only post on this list nobody else can write; it
   retroactively makes the ELF post its background reading. (Linking &
   Loading, Part 2.)
2. **The Keyboard Dance** (drafted — extracted from the ELF post's Appendix
   B): TTY/PTY architecture, what actually happens between keypress and
   shell.
3. **Before the kernel answers** (drafted — extracted from Appendix C):
   IDT/MSRs/SYSCALL/KPTI — the syscall entry path.
4. **GPU series opener** — CUDA memory/UVM or NCCL collectives debugging at
   Llama scale; overlaps cuda-reverse material (MLSys 2026).
5. **VMM series opener** — what a virtual machine monitor actually does;
   EPT/shadow paging from the VMware Monitor-team perspective.
6. **PTP/IEEE 1588** — how Linux time sync really works, from the kernel
   maintainer-adjacent trenches.

## Per-post publish playbook (from the distribution research)

- Title: plain, artifact-specific, question-shaped travels best.
- Publish, validate OG card (Meta debugger, LinkedIn inspector) + JSON-LD
  (Rich Results test) BEFORE announcing — unfurls cache aggressively.
- HN: submit Tue–Thu 08:00–10:00 PT, plain title, no Show HN, be in the
  comments the first hour. If it dies on /new, email hn@ycombinator.com for
  the second-chance pool (don't resubmit).
- lobste.rs: authored-by checkbox, linux/debugging tags, keep self-submissions
  <25% of activity (or let a peer submit and claim via chat).
- dev.to cross-post 5–7 days later with canonical_url.
- Add the `discussion:` frontmatter links once threads exist.
- Log every rank/citation/talk-invite in impact-log.md the day it happens.
- Pitch each major post to 1–2 CFPs (FOSDEM, All Systems Go, Linux Plumbers)
  and consider an LWN/InfoQ republication offer — outlet publication is what
  converts a blog post into EB-1A "authorship" evidence.
