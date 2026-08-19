// @ts-check
import { readdirSync, readFileSync } from "node:fs";
import { defineConfig } from "astro/config";
import mdx from "@astrojs/mdx";
import sitemap from "@astrojs/sitemap";

// Per-post <lastmod> for the sitemap, read from frontmatter (updated wins
// over date). Google only honors lastmod when it is accurate and stable
// across deploys, so it must come from the content, never from build time.
const postLastmod = new Map();
for (const file of readdirSync("./src/content/blog")) {
  const m = file.match(/^(.+)\.(md|mdx)$/);
  if (!m) continue;
  const fm = readFileSync(`./src/content/blog/${file}`, "utf8").match(
    /^---\n([\s\S]*?)\n---/,
  );
  if (!fm) continue;
  const pick = (key) => fm[1].match(new RegExp(`^${key}:\\s*(\\S+)`, "m"))?.[1];
  const lastmod = pick("updated") ?? pick("date");
  if (lastmod) postLastmod.set(`/blog/${m[1]}/`, new Date(lastmod));
}
import preact from "@astrojs/preact";
import expressiveCode from "astro-expressive-code";
import { pluginCollapsibleSections } from "@expressive-code/plugin-collapsible-sections";
import { pluginLineNumbers } from "@expressive-code/plugin-line-numbers";

export default defineConfig({
  site: "https://howtf.io",
  integrations: [
    expressiveCode({
      themes: ["gruvbox-dark-medium", "gruvbox-light-medium"],
      themeCssSelector: (theme) =>
        `[data-theme="${theme.type === "dark" ? "dark" : "light"}"]`,
      useDarkModeMediaQuery: false,
      plugins: [pluginCollapsibleSections(), pluginLineNumbers()],
      defaultProps: {
        // line numbers only on source listings, never on sessions
        showLineNumbers: false,
      },
      styleOverrides: {
        borderRadius: "0",
        borderColor: "var(--border)",
        codeFontFamily: "var(--font-mono)",
        codeFontSize: "0.8rem",
        codeLineHeight: "1.65",
        uiFontFamily: "var(--font-mono)",
        frames: {
          editorActiveTabIndicatorTopColor: "var(--accent)",
          editorActiveTabForeground: "var(--text)",
          editorTabBarBackground: "var(--surface-2)",
          editorBackground: "var(--surface)",
          terminalBackground: "var(--term-bg)",
          terminalTitlebarBackground: "var(--term-bar)",
          terminalTitlebarForeground: "var(--term-muted)",
          terminalTitlebarBorderBottomColor: "var(--term-border)",
          shadowColor: "transparent",
        },
      },
    }),
    mdx(),
    sitemap({
      serialize(item) {
        const lastmod = postLastmod.get(new URL(item.url).pathname);
        if (lastmod) item.lastmod = lastmod.toISOString();
        return item;
      },
    }),
    preact(),
  ],
});
