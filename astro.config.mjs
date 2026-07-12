// @ts-check
import { defineConfig } from "astro/config";
import mdx from "@astrojs/mdx";
import sitemap from "@astrojs/sitemap";
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
    sitemap(),
    preact(),
  ],
});
