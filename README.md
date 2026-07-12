# howtf.io

Deep dives from the bottom of the systems stack. Every post starts with a real
bug and the question: *howtf did that happen?*

Built with [Astro](https://astro.build) +
[Expressive Code](https://expressive-code.com). Design direction: **Core Dump**
— dark-first, terminal-native, warm ember (see `DESIGN.md`).

## Develop

```sh
npm install
npm run dev        # http://localhost:4321
npm run build      # static build → dist/
npm run preview
```

## Write a post

Add `src/content/blog/<Slug>.md` (the filename is the URL: `/blog/<Slug>/`):

```yaml
---
title: "What actually happens between exec() and main()"
description: "One-line dek shown on cards, in meta tags, and in RSS."
date: 2026-02-16
series: { name: "Linking & Loading", part: 1 } # optional
tags: [linker, elf]                             # optional
discussion:                                     # optional, added post-publish
  "hacker news": "https://news.ycombinator.com/item?id=..."
draft: true                                     # keep out of builds until ready
---
```

Code fences use [Expressive Code](https://expressive-code.com):
`bash`/`sh`/`shellsession` render as always-dark terminal frames; give source
listings a `title="main.c"` and use `{7}` to highlight lines,
`collapse={10-40}` for collapsible dumps.

Demo code for the Linking & Loading series lives in `demo/elf-linking/`.

## Deploy

Pushes to `v4` build and deploy to GitHub Pages via
`.github/workflows/deploy.yaml`. OG images are generated at build time
(`/og/...png`); RSS is full-content at `/rss.xml`.

## Publish checklist

- [ ] `date` set, `description` written, `draft` removed
- [ ] validate OG card + JSON-LD (Rich Results test) on the built page
- [ ] regenerate any tool output in the canonical container (`demo/`)
- [ ] after posting to an aggregator, add the `discussion` link
- [ ] log traction in `impact-log.md` (private)
