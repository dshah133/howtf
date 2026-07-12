# howtf.io design system — "Core Dump", TUI edition

Not a website about terminals — **a TUI rendered to the web**. The design
language of tmux/htop/gdb: bordered panes on a flat ground, panel titles inset
into the border, inverse-video selection, a statusline footer, bitmap display
type, CRT-amber phosphor. No cards, no shadows, no border-radius, no pills.

## Type

| Role    | Face                      | Notes                                    |
| ------- | ------------------------- | ---------------------------------------- |
| Display | Departure Mono (400 only) | self-hosted, OFL — masthead, headings, chrome labels |
| Body    | Alegreya Sans 400/500/700 + italic | long-form prose at 1.125rem/1.65, 66ch measure |
| Code    | JetBrains Mono 400/700    | code blocks + inline code only           |

## Color

Dark is canonical (phosphor); light is the "printout".

| Token       | Dark      | Light     |
| ----------- | --------- | --------- |
| bg          | `#0e0c09` | `#f4eee0` |
| surface     | `#17130e` | `#ece4d2` |
| surface-2   | `#221c14` | `#e2d8c0` |
| border      | `#3a3226` | `#c9bda0` |
| text        | `#e6dcc8` | `#241c10` |
| muted       | `#a4977e` | `#63573f` |
| accent      | `#f2a900` | `#7a5200` |

Terminal frames are theme-invariant (real sessions are dark): bg `#12100b`,
text `#e6dcc8`, muted `#a4977e`, accent `#f2a900`.

Key contrast ratios (WCAG, computed): text/bg dark 14.8 · muted/bg dark 7.0 ·
amber/bg dark 9.3 · ink/paper light 13.9 · muted/paper 5.9 · accent/paper 5.6.
Everything that renders text must stay ≥ 4.5:1 — check any new pair.

## Fixed entity color code (site-wide, every diagram, every post)

| Entity          | Dark      | Light     |
| --------------- | --------- | --------- |
| file sections   | `#f2a900` | `#a34d05` |
| memory segments | `#d9b380` | `#77571a` |
| loader (ld.so)  | `#9dbd7f` | `#4e6423` |
| kernel          | `#8fb4e3` | `#3d5687` |

Readers should never re-parse a legend: a section is amber in Part II and
amber in Appendix F and amber in next year's post.

## Devices

- **Pane**: `.frame` + `.frame-title` (+ optional `.frame-tag`) — bordered
  panel with the title inset into the border line. The site's signature.
- **Breakpoint aside**: markdown blockquote → amber-left-rule pane. Reserved
  for the `/* howtf?! */` moment — the wtf→aha turn of a post.
- **Inverse video**: hover/selection states swap to amber bg + ink text.
  Links underline at rest, invert on hover. The TOC's active frame number
  inverts.
- **Backtrace TOC**: post h2s are gdb frames `#0…#n`, h3s indent beneath.
- **Bracket labels**: `[start here]`, `[theme: dark]` — never rounded pills.
- **Statusline**: header and footer are tmux-style bars with segment blocks.
- **Terminal vs source**: bash/shellsession fences render as always-dark
  terminal frames; source listings get `title="file.c"` tabs, line numbers
  via `showLineNumbers`, highlights via `{n}` / `ins=` / `del=`, long dumps
  collapsed via `collapse={x-y}`.
