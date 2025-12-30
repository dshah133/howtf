import { QuartzComponent, QuartzComponentConstructor, QuartzComponentProps } from "./types"
import { classNames } from "../util/lang"

const ArticleTitle: QuartzComponent = ({ fileData, displayClass }: QuartzComponentProps) => {
  const title = fileData.frontmatter?.title
  const subtitle = fileData.frontmatter?.subtitle
  if (title) {
    return (
      <div class={classNames(displayClass, "article-title-container")}>
        <h1 class="article-title">{title}</h1>
        {subtitle && <p class="article-subtitle">{subtitle}</p>}
      </div>
    )
  } else {
    return null
  }
}

ArticleTitle.css = `
.article-title-container {
  margin: 2rem 0 1.5rem 0;
}
.article-title {
  margin: 0 0 1rem 0;
}
.article-subtitle {
  margin: 0 0 0.5rem 0;
  font-size: 1.05rem;
  color: var(--gray);
  font-style: italic;
  line-height: 1.6;
}
`

export default (() => ArticleTitle) satisfies QuartzComponentConstructor
