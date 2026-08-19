import type { APIRoute } from "astro";
import { getCollection } from "astro:content";

// llms-full.txt: the complete markdown of every post in one file, so an LLM
// can read the whole site in a single fetch. Regenerated on every build.
export const GET: APIRoute = async (context) => {
  const site = (context.site?.href ?? "https://howtf.io/").replace(/\/$/, "");
  const posts = (await getCollection("blog", ({ data }) => !data.draft)).sort(
    (a, b) => a.data.date.valueOf() - b.data.date.valueOf(),
  );

  const sections = posts.map((post) => {
    const date = post.data.date.toISOString().slice(0, 10);
    return `# ${post.data.title}

URL: ${site}/blog/${post.id}/
Published: ${date}
${post.data.description}

${post.body ?? ""}`;
  });

  const text = `howtf.io — full post content for LLM readers. Index: ${site}/llms.txt

${sections.join("\n\n---\n\n")}
`;

  return new Response(text, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
