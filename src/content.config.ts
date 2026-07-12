import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const blog = defineCollection({
  loader: glob({
    pattern: "**/*.{md,mdx}",
    base: "./src/content/blog",
    // preserve filename casing in URLs (/blog/ELF-Linking-101/ predates this engine)
    generateId: ({ entry }) => entry.replace(/\.(md|mdx)$/, ""),
  }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    date: z.coerce.date(),
    updated: z.coerce.date().optional(),
    series: z
      .object({
        name: z.string(),
        part: z.number(),
      })
      .optional(),
    tags: z.array(z.string()).default([]),
    // links to where the post is being discussed, keyed by venue
    // e.g. { "hacker news": "https://news.ycombinator.com/item?id=..." }
    discussion: z.record(z.string(), z.string()).optional(),
    draft: z.boolean().default(false),
  }),
});

export const collections = { blog };
