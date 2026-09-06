import { getCollection } from "astro:content"
import { OGImageRoute } from "astro-og-canvas"

const posts = await getCollection("blog")

const pages: Record<string, { title: string; description: string }> = {
  default: {
    title: "howtf.io",
    description: "deep dives from the bottom of the systems stack",
  },
}
for (const post of posts) {
  pages[`blog/${post.id}`] = {
    title: post.data.title,
    description: post.data.description,
  }
}

export const { getStaticPaths, GET } = await OGImageRoute({
  pages,
  getImageOptions: (_path, page) => ({
    title: page.title,
    description: page.description,
    bgGradient: [[248, 246, 239]], // warm paper
    border: { color: [37, 62, 53], width: 5, side: "inline-start" }, // green ink
    padding: 72,
    font: {
      title: {
        size: 60,
        lineHeight: 1.25,
        families: ["iA Writer Quattro S", "monospace"],
        color: [37, 62, 53], // --text light
      },
      description: {
        size: 30,
        lineHeight: 1.5,
        families: ["iA Writer Quattro S", "sans-serif"],
        color: [96, 108, 100], // --muted light
      },
    },
    fonts: ["./public/fonts/iAWriterQuattroS-Regular.woff2"],
  }),
})
