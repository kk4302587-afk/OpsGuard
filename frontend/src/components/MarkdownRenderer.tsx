import { isValidElement, useState, type MouseEvent, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import '../styles/markdown.css'

interface MarkdownRendererProps {
  content: string
}

const OPS_COMMAND_PATTERN = /^(sudo\s+)?(systemctl|service|journalctl|cat|tail|grep|awk|sed|find|ls|df|du|free|top|ps|ss|netstat|lsof|ping|curl|wget|dig|nslookup|ip|iptables|firewall-cmd|nginx|apachectl|docker|kubectl|crontab|chmod|chown|mkdir|cp|mv|rm|tar|gzip|mount|umount|rsync|scp|hostnamectl|timedatectl|getenforce|setenforce)\b/
const INLINE_VALUE_PATTERN = /(^\/|\.service$|^[\w.-]+$)/

function nodeToText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeToText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeToText(node.props.children)
  return ''
}

function CopyButton({ text, compact = false }: { text: string; compact?: boolean }) {
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
      className={compact ? 'md-copy-btn md-copy-btn-inline' : 'md-copy-btn'}
      onClick={handleCopy}
      aria-label={copied ? '已复制' : '复制命令'}
      title={copied ? '已复制' : '复制命令'}
    >
      {copied ? <CheckOutlined /> : <CopyOutlined />}
      {!compact && <span>{copied ? '已复制' : '复制'}</span>}
    </button>
  )
}

function isLikelyOpsCommand(text: string): boolean {
  const normalized = text.trim()
  return (
    normalized.length >= 3
    && normalized.length <= 180
    && /\s/.test(normalized)
    && !INLINE_VALUE_PATTERN.test(normalized)
    && OPS_COMMAND_PATTERN.test(normalized)
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
    const text = nodeToText(children)
    if (!className && !text.includes('\n') && isLikelyOpsCommand(text)) {
      return (
        <span className="md-inline-command">
          <code {...props}>{children}</code>
          <CopyButton text={text.trim()} compact />
        </span>
      )
    }
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
