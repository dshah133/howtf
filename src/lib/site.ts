export const SITE = {
  title: "howtf.io",
  description:
    "Deep dives from the bottom of the systems stack — kernel, loader, GPU, virtual machine monitor. Every story starts with a bug and the question: howtf did that happen?",
  author: "Deep Shah",
  url: "https://howtf.io",
  github: "https://github.com/dshah133",
  linkedin: "https://www.linkedin.com/in/shde/",
  // Buttondown newsletter — form posts here once the account exists
  newsletterAction: "https://buttondown.com/api/emails/embed-subscribe/howtf",
};

const WPM = 240;

export function readingTime(body: string): string {
  const words = body
    .replace(/```[\s\S]*?```/g, " ") // code blocks read faster than prose; count words only
    .split(/\s+/)
    .filter(Boolean).length;
  const codeLines = (body.match(/\n/g) || []).length;
  // rough honest estimate: prose words at 240wpm + a small tax for code density
  const minutes = Math.max(1, Math.round(words / WPM + codeLines / 200));
  return `${minutes} min read`;
}

export function fmtDate(d: Date): string {
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
