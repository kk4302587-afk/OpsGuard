import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import '../styles/markdown.css'

interface MarkdownRendererProps {
  content: string
}

/**
 * Renders markdown content with dark theme styling.
 * Supports GFM (tables, strikethrough, task lists).
 */
function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <div className="md-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

export default MarkdownRenderer
