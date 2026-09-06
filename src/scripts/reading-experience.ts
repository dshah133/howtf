/** Keep navigation and figure inspection out of the article's reading column. */
export function mountReadingExperience() {
  const root = document.querySelector<HTMLElement>(".article-body")
  const header = document.querySelector<HTMLElement>(".post-header")
  const bar = document.querySelector<HTMLElement>(".reading-bar")
  const contents = document.querySelector<HTMLDialogElement>(".contents-dialog")
  const inspection = document.querySelector<HTMLDialogElement>(".figure-dialog")
  if (!root || !header || !bar || !contents || !inspection) return

  const toc = document.querySelector<HTMLElement>("[data-toc]")
  const details = toc?.querySelector<HTMLDetailsElement>("details")
  const desktop = matchMedia("(min-width: 1001px)")
  const syncToc = () => {
    if (details) details.open = desktop.matches
  }
  desktop.addEventListener("change", syncToc)
  syncToc()

  const links = [...(toc?.querySelectorAll<HTMLAnchorElement>('a[href^="#"]') ?? [])]
  const headings = links
    .map((a) => document.getElementById(decodeURIComponent(a.hash.slice(1))))
    .filter((h): h is HTMLElement => !!h)
  const chapters = [...root.querySelectorAll<HTMLElement>("h2")]
  const chapterLabels = new Map(
    chapters.map((h) => [h, h.textContent?.replace(/^\d+[.)]\s*/, "") ?? ""]),
  )
  const list = contents.querySelector<HTMLElement>(".contents-list")!
  const locationLink = bar.querySelector<HTMLAnchorElement>(".reading-location")!
  const locationLabel = bar.querySelector<HTMLElement>(".reading-heading")!
  const locationIndex = bar.querySelector<HTMLElement>(".reading-index")!
  const articleTitle = header.querySelector("h1")?.textContent ?? ""

  for (const link of links) {
    const a = link.cloneNode(true) as HTMLAnchorElement
    if (link.closest(".sub")) a.classList.add("sub")
    list.append(a)
  }
  const navigationLinks = [...links, ...list.querySelectorAll<HTMLAnchorElement>("a")]
  bar.querySelector("[data-open-contents]")?.addEventListener("click", () => {
    contents.showModal()
    contents
      .querySelector<HTMLElement>('a[aria-current="location"]')
      ?.scrollIntoView({ block: "center", behavior: "instant" })
  })

  // Reveal closed appendices before following their native fragment links.
  const revealTarget = (a: HTMLAnchorElement) => {
    const h = document.getElementById(decodeURIComponent(a.hash.slice(1)))
    if (!h) return
    let parent = h.parentElement
    while (parent) {
      if (parent instanceof HTMLDetailsElement) parent.open = true
      parent = parent.parentElement
    }
    h.tabIndex = -1
    h.focus({ preventScroll: true })
  }
  for (const a of navigationLinks) {
    a.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      if (contents.open) contents.close()
      revealTarget(a)
    })
  }
  for (const d of [contents, inspection]) {
    d.querySelector("[data-close-dialog]")?.addEventListener("click", () => d.close())
    d.addEventListener("click", (event) => {
      if (event.target !== d) return
      const r = d.getBoundingClientRect()
      if (
        event.clientX < r.left ||
        event.clientX > r.right ||
        event.clientY < r.top ||
        event.clientY > r.bottom
      )
        d.close()
    })
  }

  let scheduled = false
  let active: HTMLElement | undefined
  const update = () => {
    scheduled = false
    const reading = header.getBoundingClientRect().bottom < 50
    bar.classList.toggle("is-visible", reading)
    bar.inert = !reading
    bar.setAttribute("aria-hidden", String(!reading))

    let current: HTMLElement | undefined
    let chapter: HTMLElement | undefined
    for (const h of headings) {
      if (h.getClientRects().length === 0) continue
      if (h.getBoundingClientRect().top > 110) break
      current = h
      if (chapterLabels.has(h)) chapter = h
    }
    if (current !== active) {
      active = current
      for (const a of navigationLinks) {
        const selected = !!current && a.hash === "#" + current.id
        a.classList.toggle("active", selected)
        if (selected) a.setAttribute("aria-current", "location")
        else a.removeAttribute("aria-current")
      }
      const selected = links.find((a) => a.hash === "#" + current?.id)
      if (toc && selected && desktop.matches && !toc.matches(":hover,:focus-within")) {
        const r = selected.getBoundingClientRect()
        const t = toc.getBoundingClientRect()
        if (r.bottom > t.bottom - 20 || r.top < t.top + 40) toc.scrollTop += r.top - t.top - 70
      }
    }
    locationIndex.textContent = chapter ? "#" + chapters.indexOf(chapter) : ""
    locationLabel.textContent = chapter ? chapterLabels.get(chapter)! : articleTitle
    locationLink.href = chapter ? "#" + chapter.id : "#main"
  }
  const scheduleUpdate = () => {
    if (!scheduled) {
      scheduled = true
      requestAnimationFrame(update)
    }
  }
  addEventListener("scroll", scheduleUpdate, { passive: true })
  addEventListener("resize", scheduleUpdate, { passive: true })
  addEventListener("pageshow", scheduleUpdate)
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleUpdate()
  })
  root.addEventListener("toggle", scheduleUpdate, true)
  update()

  const inspectionContent = inspection.querySelector<HTMLElement>(".inspection-content")!
  const size = inspection.querySelector<HTMLButtonElement>("[data-inspect-size]")!
  let placeholder: HTMLElement | null = null
  let inspected: HTMLElement | null = null
  let trigger: HTMLButtonElement | null = null
  let horizontalPosition = 0
  size.addEventListener("click", () => {
    const actual = inspection.classList.toggle("actual-size")
    size.setAttribute("aria-pressed", String(actual))
    size.textContent = actual ? "Fit to window" : "Actual size"
  })
  const restoreInspection = () => {
    if (!inspected || !placeholder) return
    placeholder.replaceWith(inspected)
    inspected.scrollLeft = horizontalPosition
    inspected = null
    placeholder = null
    inspection.classList.remove("actual-size")
    size.setAttribute("aria-pressed", "false")
    size.textContent = "Actual size"
    trigger?.focus({ preventScroll: true })
  }
  inspection.addEventListener("close", restoreInspection)
  inspection.addEventListener("cancel", (event) => {
    event.preventDefault()
    inspection.close()
    restoreInspection()
  })

  root.querySelectorAll<HTMLElement>("figure.diagram, div.diagram").forEach((figure, index) => {
    const body = figure.querySelector<HTMLElement>(".diagram-body")
    if (!body) return
    const wasFocusable = body.hasAttribute("tabindex")
    const originalLabel = body.getAttribute("aria-label")
    const title = figure.querySelector<HTMLElement>(".frame-title")
    const toolbar = document.createElement("div")
    toolbar.className = "figure-toolbar"
    if (title) toolbar.append(title)
    const expand = document.createElement("button")
    expand.type = "button"
    expand.className = "figure-expand"
    expand.textContent = "Expand ↗"
    expand.setAttribute(
      "aria-label",
      `Expand ${title?.textContent?.trim() || "diagram " + (index + 1)}`,
    )
    toolbar.append(expand)
    figure.prepend(toolbar)

    const hint = document.createElement("p")
    hint.className = "diagram-hint"
    hint.textContent = "Scroll to inspect the full diagram →"
    body.after(hint)
    const checkOverflow = () => {
      const overflowing = body.scrollWidth > body.clientWidth + 2
      hint.hidden = !overflowing
      if (overflowing) {
        body.tabIndex = 0
        body.setAttribute("aria-label", "Diagram. Scroll horizontally to inspect.")
      } else {
        if (!wasFocusable) body.removeAttribute("tabindex")
        if (originalLabel) body.setAttribute("aria-label", originalLabel)
        else body.removeAttribute("aria-label")
      }
    }
    new ResizeObserver(checkOverflow).observe(body)
    checkOverflow()
    const svg = body.querySelector("svg")
    if (svg)
      body.style.setProperty(
        "--diagram-natural-width",
        `${Math.max(svg.viewBox.baseVal.width, 640)}px`,
      )
    expand.addEventListener("click", () => {
      trigger = expand
      inspected = body
      horizontalPosition = body.scrollLeft
      placeholder = document.createElement("div")
      placeholder.setAttribute("aria-hidden", "true")
      placeholder.style.height = `${body.getBoundingClientRect().height}px`
      body.before(placeholder)
      inspection.querySelector(".inspection-title")!.textContent =
        title?.textContent?.trim() || "Diagram"
      inspectionContent.append(body)
      inspection.showModal()
    })
  })

  for (const h of root.querySelectorAll<HTMLElement>("h2[id], h3[id]")) {
    const a = document.createElement("a")
    a.className = "heading-anchor"
    a.href = "#" + h.id
    a.setAttribute("aria-label", "Link to section: " + h.textContent)
    a.textContent = "#"
    h.append(a)
  }
}
