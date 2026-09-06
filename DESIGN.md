# howtf.io design system

Warm paper, green ink, and a quiet reading column. The same type, fine rules,
spacing, and controls apply to the homepage, archives, series, tools, about,
and complete articles. The editorial content stays in its existing files.

## Site mark

An open stack of layers, with an offset upper layer and a small exposed
accent. The header, footer, running header, and SVG favicon share the same
geometry. The favicon adapts to the browser color scheme; inline marks use
the page theme. Keep the silhouette clear at 16px.

## Typography

| Role | Face | Size |
| --- | --- | --- |
| Editorial headings | Georgia, Times New Roman, serif | 24–54px by level; responsive |
| Body | Self-hosted iA Writer Quattro | 17px / 1.85 in articles |
| Navigation and metadata | iA Writer Quattro | Generally 12–13px |
| Code and technical diagrams | Self-hosted JetBrains Mono 400/700 | Code: 13px / 1.65 |

The article column is at most 680px. On phones, it uses the available width
with 22px outer margins. Source code and diagrams scroll within their own
containers. Code uses a true monospace face so ASCII diagrams remain aligned.
Font licenses live beside the files in `public/fonts/`.

## Color

| Token | Light | Dark |
| --- | --- | --- |
| Background | `#f8f6ef` | `#16221d` |
| Surface | `#f0efe5` | `#1e2e25` |
| Secondary surface | `#e6e8dc` | `#283a2e` |
| Text | `#253e35` | `#e7e4d4` |
| Muted text | `#606c64` | `#b1bcae` |
| Border | `#c4ccbf` | `#475c4b` |
| Accent | `#90442d` | `#e4b881` |

Honor the reader's saved `howtf-theme` preference, falling back to the system
color scheme. Controls in the site header and the article's running header
share the same state. Color preferences do not affect readable no-JavaScript
content.

Terminal frames remain dark in both themes. Their syntax tokens must use the
dark token set as well. Source listings follow the selected theme.

### Stable diagram entities

| Entity | Light | Dark |
| --- | --- | --- |
| File sections | `#914300` | `#f2b96b` |
| Memory segments | `#77571a` | `#d9c090` |
| Loader | `#4e6423` | `#b5c795` |
| Kernel | `#3d5687` | `#a4c2e4` |

Keep entity meanings consistent between posts. Check text on its actual
background, including tinted SVG regions, before introducing new colors.

## Layout and reading behavior

- Post lists put the date and label in a left margin on desktop, above the
  text on phones. Thin rules separate entries.
- Series show reading order with a restrained numbered sequence. Tool
  categories and requirements occupy the same visual margin.
- Article titles and metadata appear before the table of contents on phones.
  Desktop articles have a sticky backtrace with an active section indicator.
- Once the title block leaves the viewport, a compact running header shows
  the current section, Contents, and the theme control. Scrolling and fragment
  navigation remain native. Reduced-motion preferences disable transitions
  and smooth scrolling.
- Contents opens in a native dialog. Section links retain their original
  fragments and reveal closed appendices where needed.
- Figures offer Expand, then Fit to window / Actual size. Move the existing
  diagram into the inspector rather than cloning it. A placeholder preserves
  document height; closing restores the figure, its interactive state,
  horizontal position, and keyboard focus.
- Keep essential explanations visible without JavaScript. Interactive figures
  enhance their existing static presentation.
- Print styles remove navigation and controls and retain the article.

## Production behavior

Keep canonical URLs, social metadata, JSON-LD, RSS, and analytics intact.
Newsletter forms POST to the configured Buttondown action. Do not ship preview
banners, noindex tags, preview theme keys, or demo form interception.
