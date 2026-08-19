import type { APIRoute } from "astro";
import { getCollection } from "astro:content";

// llms.txt (https://llmstxt.org/): a curated index for LLM crawlers.
// The preamble is hand-written; the post list is generated from the content
// collection so a new post is included by the same build that publishes it.
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

## Topic hubs

- [RDMA deep dives](${site}/topics/rdma/): all RDMA/InfiniBand posts.

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
