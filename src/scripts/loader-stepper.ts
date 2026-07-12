// fig. 0b — interactive loader stepper. Vanilla, dependency-free: the post is
// plain markdown (no MDX), so the layout mounts this onto [data-loader-stepper].
// State is a step index; each step reveals address-space boxes and a caption.
// All numbers match the regenerated artifacts in demo/elf-linking/.

type Box = {
  id: string;
  label: string;
  sub?: string;
  entity: "sec" | "seg" | "ldr" | "krn";
  y: number;
  h: number;
  appearsAt: number;
};

const BOXES: Box[] = [
  {
    id: "app",
    label: "dynamic_app — 5 VMAs (one per LOAD)",
    sub: "at load_bias 0x555555554000",
    entity: "sec",
    y: 24,
    h: 52,
    appearsAt: 1,
  },
  {
    id: "libmath",
    label: "libmath.so",
    sub: "found via RUNPATH $ORIGIN",
    entity: "seg",
    y: 96,
    h: 40,
    appearsAt: 3,
  },
  {
    id: "libc",
    label: "libc.so.6",
    sub: "from /etc/ld.so.cache",
    entity: "seg",
    y: 148,
    h: 40,
    appearsAt: 3,
  },
  {
    id: "ld",
    label: "ld-linux-x86-64.so.2",
    sub: "mapped by the kernel (PT_INTERP)",
    entity: "ldr",
    y: 200,
    h: 40,
    appearsAt: 2,
  },
  {
    id: "stack",
    label: "[stack] — argv, envp, auxv",
    entity: "krn",
    y: 252,
    h: 34,
    appearsAt: 1,
  },
];

const STEPS: { title: string; caption: string; actor: string }[] = [
  {
    title: "execve(\"./dynamic_app\")",
    actor: "kernel",
    caption:
      "The shell's child calls execve. The kernel throws away the old address space — what you see is a blank slate about to be filled from one file's program headers.",
  },
  {
    title: "kernel maps the PT_LOADs",
    actor: "kernel",
    caption:
      "load_elf_binary() walks the program headers and mmaps five VMAs at a randomized load_bias, plus the stack seeded with argv, envp, and the auxiliary vector (AT_PHDR, AT_ENTRY, AT_BASE).",
  },
  {
    title: "kernel maps the interpreter",
    actor: "kernel",
    caption:
      "PT_INTERP names /lib64/ld-linux-x86-64.so.2, so the kernel maps it too — via load_elf_interp(), not recursion — and sets the first jump to the loader's entry, not ours.",
  },
  {
    title: "ld.so bootstraps and maps dependencies",
    actor: "loader",
    caption:
      "First act: the loader applies its own R_X86_64_RELATIVE relocations (nobody relocated it). Then it walks DT_NEEDED, finds libmath.so through RUNPATH $ORIGIN and libc through the cache, and maps both.",
  },
  {
    title: "GOT filled, RELRO sealed",
    actor: "loader",
    caption:
      "This binary is BIND_NOW (Ubuntu's default): every GOT slot is resolved up front, then the GOT's page is mprotect'd read-only — full RELRO. With -z lazy, slots would instead point back into the PLT, waiting.",
  },
  {
    title: "jmp *AT_ENTRY — _start → main()",
    actor: "our code",
    caption:
      "The loader jumps to our e_entry. _start hands off to __libc_start_main, constructors run, and main(5+10=15) finally executes. The relay is complete.",
  },
];

function h<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string>,
  text?: string,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text) el.textContent = text;
  return el;
}

export function mountLoaderStepper(root: HTMLElement): void {
  const body = root.querySelector(".diagram-body");
  if (!body) return;
  body.innerHTML = "";

  let step = 0;
  const max = STEPS.length - 1;

  const svg = h("svg", {
    viewBox: "0 0 640 300",
    role: "img",
    "aria-label": "Interactive step-through of the process address space being assembled",
  });
  svg.style.minWidth = "480px";
  svg.style.width = "100%";
  svg.style.height = "auto";
  svg.style.display = "block";

  svg.appendChild(
    h("text", {
      x: "16",
      y: "40",
      "font-family": "var(--font-display)",
      "font-size": "11",
      fill: "var(--muted)",
    }, "low VA"),
  );
  svg.appendChild(
    h("text", {
      x: "16",
      y: "286",
      "font-family": "var(--font-display)",
      "font-size": "11",
      fill: "var(--muted)",
    }, "high VA"),
  );

  const boxEls = new Map<string, SVGGElement>();
  for (const b of BOXES) {
    const g = h("g", { "font-family": "var(--font-mono)", "font-size": "12" });
    const stroke = `var(--${b.entity})`;
    const fill = h("rect", {
      x: "120",
      y: String(b.y),
      width: "440",
      height: String(b.h),
      fill: stroke,
      opacity: "0.14",
    });
    const outline = h("rect", {
      x: "120",
      y: String(b.y),
      width: "440",
      height: String(b.h),
      fill: "none",
      stroke,
      "stroke-width": "1.5",
      ...(b.id === "stack" ? { "stroke-dasharray": "5 3" } : {}),
    });
    g.appendChild(fill);
    g.appendChild(outline);
    g.appendChild(
      h("text", {
        x: "340",
        y: String(b.y + (b.sub ? 20 : b.h / 2 + 4)),
        "text-anchor": "middle",
        fill: stroke,
      }, b.label),
    );
    if (b.sub)
      g.appendChild(
        h("text", {
          x: "340",
          y: String(b.y + 36),
          "text-anchor": "middle",
          "font-size": "10",
          fill: "var(--muted)",
        }, b.sub),
      );
    boxEls.set(b.id, g);
    svg.appendChild(g);
  }

  // RELRO lock + main() marker, revealed late
  const lock = h("text", {
    x: "16",
    y: "58",
    "font-family": "var(--font-display)",
    "font-size": "10",
    fill: "var(--accent)",
  }, "GOT: r-- ▣");
  const mainMark = h("text", {
    x: "16",
    y: "74",
    "font-family": "var(--font-display)",
    "font-size": "10",
    fill: "var(--accent)",
  }, "▶ in main()");
  svg.appendChild(lock);
  svg.appendChild(mainMark);

  const caption = document.createElement("p");
  caption.style.cssText =
    "margin-top:1rem;max-width:60ch;font-size:0.95rem;line-height:1.55;min-height:5.5em;";
  const capTitle = document.createElement("p");
  capTitle.style.cssText =
    "margin-top:1.25rem;font-family:var(--font-display);font-size:0.85rem;";

  const controls = document.createElement("div");
  controls.style.cssText =
    "display:flex;gap:0.75rem;align-items:center;margin-top:1rem;font-family:var(--font-display);font-size:0.8rem;";
  const mkBtn = (label: string) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.style.cssText =
      "font:inherit;cursor:pointer;background:var(--surface-2);color:var(--text);border:1px solid var(--border);padding:0.3rem 0.8rem;";
    return b;
  };
  const prev = mkBtn("◀ prev");
  const next = mkBtn("next ▶");
  const counter = document.createElement("span");
  counter.style.color = "var(--muted)";
  controls.append(prev, next, counter);

  const render = () => {
    for (const b of BOXES) {
      const g = boxEls.get(b.id)!;
      g.style.opacity = step >= b.appearsAt ? "1" : "0.06";
    }
    lock.style.opacity = step >= 4 ? "1" : "0";
    mainMark.style.opacity = step >= 5 ? "1" : "0";
    const s = STEPS[step];
    capTitle.textContent = `step ${step}/${max} — ${s.title}`;
    capTitle.style.color = "var(--accent)";
    caption.textContent = `[${s.actor}] ${s.caption}`;
    counter.textContent = `${step}/${max}`;
    prev.disabled = step === 0;
    next.disabled = step === max;
    prev.style.opacity = prev.disabled ? "0.4" : "1";
    next.style.opacity = next.disabled ? "0.4" : "1";
  };

  prev.addEventListener("click", () => {
    step = Math.max(0, step - 1);
    render();
  });
  next.addEventListener("click", () => {
    step = Math.min(max, step + 1);
    render();
  });
  root.tabIndex = 0;
  root.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") {
      step = Math.min(max, step + 1);
      render();
      e.preventDefault();
    } else if (e.key === "ArrowLeft") {
      step = Math.max(0, step - 1);
      render();
      e.preventDefault();
    }
  });

  body.append(svg, capTitle, caption, controls);
  render();
}
