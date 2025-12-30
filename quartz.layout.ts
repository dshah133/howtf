import { PageLayout, SharedLayout } from "./quartz/cfg"
import * as Component from "./quartz/components"

// components shared across all pages
export const sharedPageComponents: SharedLayout = {
  head: Component.Head(),
  header: [],
  afterBody: [],
  footer: Component.Footer(),
}

// components for pages that display a single page (e.g. a single note)
export const defaultContentPageLayout: PageLayout = {
  beforeBody: [
    Component.ArticleTitle(),
    Component.ContentMeta({ showReadingTime: false }),
    Component.TagList(),
  ],
  left: [
    Component.Flex({
      components: [
        { Component: Component.PageTitle(), grow: true },
        { Component: Component.SidebarToggle() },
      ],
    }),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [
        {
          Component: Component.Search(),
          grow: true,
        },
        { Component: Component.Darkmode() },
      ],
    }),
    Component.DesktopOnly(
      Component.RecentNotes({
        title: "posts",
        limit: 10,
        showTags: false,
        filter: (f) => f.slug?.startsWith("blog/") ?? false,
      }),
    ),
  ],
  right: [
    Component.DesktopOnly(Component.TableOfContents()),
  ],
}

// components for pages that display lists of pages  (e.g. tags or folders)
export const defaultListPageLayout: PageLayout = {
  beforeBody: [Component.ArticleTitle(), Component.ContentMeta({ showReadingTime: false })],
  left: [
    Component.Flex({
      components: [
        { Component: Component.PageTitle(), grow: true },
        { Component: Component.SidebarToggle() },
      ],
    }),
    Component.MobileOnly(Component.Spacer()),
    Component.Flex({
      components: [
        {
          Component: Component.Search(),
          grow: true,
        },
        { Component: Component.Darkmode() },
      ],
    }),
    Component.DesktopOnly(
      Component.RecentNotes({
        title: "posts",
        limit: 10,
        showTags: false,
        filter: (f) => f.slug?.startsWith("blog/") ?? false,
      }),
    ),
  ],
  right: [],
}
