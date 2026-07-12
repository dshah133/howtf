import { getCollection } from "astro:content";
import { OGImageRoute } from "astro-og-canvas";

const posts = await getCollection("blog");

const pages: Record<string, { title: string; description: string }> = {
  default: {
    title: "howtf.io",
    description: "deep dives from the bottom of the systems stack",
  },
};
for (const post of posts) {
  pages[`blog/${post.id}`] = {
    title: post.data.title,
    description: post.data.description,
  };
}

export const { getStaticPaths, GET } = await OGImageRoute({
  pages,
  getImageOptions: (_path, page) => ({
    title: page.title,
    description: page.description,
    bgGradient: [[14, 12, 9]], // --bg dark #0e0c09
    border: { color: [242, 169, 0], width: 14, side: "inline-start" }, // amber
    padding: 72,
    font: {
      title: {
        size: 60,
        lineHeight: 1.25,
        families: ["Departure Mono", "monospace"],
        color: [230, 220, 200], // --text dark
      },
      description: {
        size: 30,
        lineHeight: 1.5,
        families: ["Alegreya Sans", "sans-serif"],
        color: [164, 151, 126], // --muted dark
      },
    },
    fonts: [
      "./public/fonts/DepartureMono-Regular.woff2",
      "https://cdn.jsdelivr.net/fontsource/fonts/alegreya-sans@latest/latin-400-normal.ttf",
    ],
  }),
});
