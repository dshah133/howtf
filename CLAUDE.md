# howtf.io — project guide for Claude

Personal systems-engineering blog by Deep Shah. Deep, sharp debugging war
stories from the bottom of the stack (kernel, linker/loader, GPU,
virtualization). Live at **https://howtf.io**.

## Stack & layout
- **Astro** static site, "Core Dump" TUI design system (light warm-paper
  theme, Departure Mono display, iA Writer Quattro body, JetBrains Mono
  code, CRT-amber accent). Expressive Code for code blocks.
- Posts live in `src/content/blog/*.md`. Series so far: **Linking &
  Loading** — Part 1 `ELF-Linking-101.md`, Part 2 `split-state-linking.md`.
- Reproducers/tools: `demo/`, `tools/symsplit/`. In-post artifact links
  point to `github.com/dshah133/howtf/tree/main/...` — keep those intact.

## Deploy
- Single branch: **`main`** is the only branch. It is both the working branch
  and the production branch: GitHub Pages deploys on push to `main`, via
  `.github/workflows/deploy.yaml`.
- Pushing `main` publishes live to howtf.io. **Confirm with Deep before any
  push that deploys, unless he asked for that specific push.**
- gh account for this repo: **dshah133** (personal).

## Writing style (IMPORTANT — applies to all prose on this site)
- **Minimize em dashes.** They are overused across the site. Prefer a comma,
  colon, period, or parentheses, and rewrite the sentence rather than
  mechanically swapping punctuation. Aim for as few em dashes as possible;
  keep one only where removing it genuinely hurts clarity.
- Avoid semicolons where a period or comma works.
- First person, plain words, no filler or clichés. Sound like a sharp human,
  not AI marketing copy.
- **Say each thing once.** Don't restate the same beat two or three times in
  a section; make the reveal land once and move on.
- Every technical claim stays source-accurate; calibrate certainty honestly
  (this is Deep's firsthand material — never retract or invent facts to match
  a reviewer).

## Voice notes
- Keep Deep's original bio/framing verbatim unless he asks to change it; never
  edit personal facts (location, employer, background) on reviewer feedback
  alone. Deep is in the **Bay Area**.
