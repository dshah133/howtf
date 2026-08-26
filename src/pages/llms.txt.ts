import type { APIRoute } from "astro";
import { getCollection } from "astro:content";
import { TOPIC_HUBS, seriesSlug } from "../lib/site";

// llms.txt (https://llmstxt.org/): a curated index for LLM crawlers.
// The preamble is hand-written; everything below it (posts, series, topic
// hubs) is generated from the content collection and TOPIC_HUBS, so a new
// post or series is included by the same build that publishes it.

// Build-time sync check: every page under src/pages/topics/ must have a
// TOPIC_HUBS entry and vice versa. A mismatch fails the build loudly
// instead of silently dropping a hub from this index.
const topicPages = Object.keys(import.meta.glob("./topics/*.astro")).map(
  (path) => `/topics/${path.replace("./topics/", "").replace(".astro", "")}/`,
);
const hubHrefs = Object.values(TOPIC_HUBS).map((hub) => hub.href);
for (const href of topicPages) {
  if (!hubHrefs.includes(href))
    throw new Error(`llms.txt: topic page ${href} missing from TOPIC_HUBS`);
}
for (const href of hubHrefs) {
  if (!topicPages.includes(href))
    throw new Error(`llms.txt: TOPIC_HUBS entry ${href} has no page in src/pages/topics/`);
}

export const GET: APIRoute = async (context) => {
  const site = (context.site?.href ?? "https://howtf.io/").replace(/\/$/, "");
  const posts = (await getCollection("blog", ({ data }) => !data.draft)).sort(
    (a, b) => a.data.date.valueOf() - b.data.date.valueOf(),
  );

  const postLines = posts.map((post) => {
    const series =
      post.data.series && !post.data.description.includes(post.data.series.name)
        ? ` ${post.data.series.name}, Part ${post.data.series.part}.`
        : "";
    return `- [${post.data.title}](${site}/blog/${post.id}/):\n  ${post.data.description}${series}`;
  });

  // series hubs, in order of each series' first published part
  const seriesMap = new Map<string, { name: string; parts: number }>();
  for (const post of posts) {
    if (!post.data.series) continue;
    const slug = seriesSlug(post.data.series.name);
    const entry = seriesMap.get(slug) ?? { name: post.data.series.name, parts: 0 };
    entry.parts += 1;
    seriesMap.set(slug, entry);
  }
  const seriesLines = [...seriesMap.entries()].map(
    ([slug, s]) =>
      `- [${s.name}](${site}/series/${slug}/): ${s.parts} part${s.parts === 1 ? "" : "s"}.`,
  );

  const hubLines = Object.values(TOPIC_HUBS).map(
    (hub) => `- [${hub.title}](${site}${hub.href}): ${hub.blurb}`,
  );

  const text = `# howtf.io

> Systems-engineering deep dives by Deep Shah. Every post reconstructs a real
> production failure from the bottom of the stack (linkers, loaders, RDMA,
> GPUDirect, NCCL, Linux memory management, TCP socket semantics), verifies
> the mechanism against public source with pinned commits, and ships a
> runnable reproducer.

Topics: RDMA, InfiniBand, libibverbs, GPUDirect, NCCL, ELF, linkers, dynamic
loading, Linux kernel, fork(), file descriptors, TCP, memory registration,
GPU training infrastructure, production debugging.

## Posts

${postLines.join("\n")}

## Series

${seriesLines.join("\n")}

## Topic hubs

${hubLines.join("\n")}

## Reproducers

- [Demo code](https://github.com/dshah133/howtf/tree/main/demo): container-based
  reproducers for every post (MIT licensed).

## Full content

- [llms-full.txt](${site}/llms-full.txt): every post's complete markdown.
- [RSS](${site}/rss.xml): full-content feed.
`;

  return new Response(text, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
