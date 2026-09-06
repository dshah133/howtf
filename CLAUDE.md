# howtf.io — project guide for Claude

Personal systems-engineering blog by Deep Shah. Deep, sharp debugging war
stories from the bottom of the stack (kernel, linker/loader, GPU,
virtualization). Live at **https://howtf.io**.

## Stack & layout
- **Astro** static site, warm-paper and green-ink design system (Georgia headings,
  self-hosted iA Writer Quattro body, self-hosted JetBrains Mono code, light
  and dark palettes). See `DESIGN.md` for reading and diagram behavior. Expressive Code for code blocks.
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

## Publishing a post (the whole workflow)

A post is ONE markdown file in `src/content/blog/<slug>.md`. The filename is
the URL slug, casing preserved (`/blog/<slug>/`). Everything else — home and
blog lists, series hub, topic hub, RSS, sitemap `lastmod`, llms.txt,
llms-full.txt, OG image, IndexNow submission — is generated from its
frontmatter by the build that deploys it. Never hand-edit any of those.

Frontmatter (schema enforced by zod in `src/content.config.ts`; build fails
on violations):

```yaml
---
title: "howtf ...?"            # required
description: "One-sentence hook."   # required; used in lists, RSS, llms.txt, OG
date: 2026-08-25               # required; publication date, drives sitemap lastmod
updated: 2026-09-01            # optional; set on material revision (wins over date)
series:                        # optional; creates/extends a series hub page
  name: "Memory Registration, All the Way Down"
  part: 3
tags: [rdma]                   # optional; a tag in TOPIC_HUBS links the post to its hub
featured: true                 # optional; adds "[start here]" tag in place
draft: true                    # excluded from EVERYTHING until flipped
---
```

Workflow:
- **Draft**: create the file with `draft: true`. Safe to commit and push;
  drafts are excluded from all lists, feeds, sitemap, and llms files.
- **Publish**: set the real `date`, remove `draft` (or set `false`), run
  `npm run build` locally to catch schema/build errors, then push to main
  (deploys — needs Deep's per-push confirmation, see Deploy above).
- **Revise a published post**: edit content; set/update `updated:` only for
  material changes (it feeds the sitemap `lastmod` crawlers act on).
- **New topic hub**: a page in `src/pages/topics/` and its entry in
  `TOPIC_HUBS` (`src/lib/site.ts`) must be added together — the build
  asserts they match and fails on drift. Series need no registration.

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
