import { isValidElement, useState, type MouseEvent, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import '../styles/markdown.css'

interface MarkdownRendererProps {
  content: string
}

function nodeToText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeToText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeToText(node.props.children)
  return ''
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    event.stopPropagation()
    try {
      await navigator.clipboard?.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch (error) {
      console.error('Failed to copy command:', error)
    }
  }

  return (
    <button
      type="button"
      className="md-copy-btn"
      onClick={handleCopy}
      aria-label={copied ? '已复制' : '复制命令'}
      title={copied ? '已复制' : '复制命令'}
    >
      {copied ? <CheckOutlined /> : <CopyOutlined />}
      <span>{copied ? '已复制' : '复制'}</span>
    </button>
  )
}

const markdownComponents: Components = {
  pre({ children }) {
    const text = nodeToText(children).trimEnd()
    return (
      <div className="md-code-block">
        <CopyButton text={text} />
        <pre>{children}</pre>
      </div>
    )
  },
  code({ className, children, ...props }) {
    return (
      <code className={className} {...props}>
        {children}
      </code>
    )
  },
}

/**
 * Renders markdown content with dark theme styling.
 * Supports GFM (tables, strikethrough, task lists).
 */
function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <div className="md-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

export default MarkdownRenderer
