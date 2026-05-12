import { useRef, useEffect } from 'react'
import { Input, Button, Typography, Tag } from 'antd'
import {
  SendOutlined,
  RobotOutlined,
  UserOutlined,
  LoadingOutlined,
  SafetyOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'
import DiagnosisProgress from './DiagnosisProgress'
import '../styles/chat.css'

const { TextArea } = Input
const { Text } = Typography

/**
 * Main chat panel - conversation flow with styled message bubbles.
 */
function ChatPanel() {
  const { messages, inputValue, setInputValue, sendMessage, isThinking } = useChatStore()
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isThinking])

  const handleSend = () => {
    if (inputValue.trim() && !isThinking) {
      sendMessage(inputValue.trim())
      setInputValue('')
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const renderMessageContent = (content: string) => {
    // Detect special message types
    if (content.startsWith('[需要确认]')) {
      const lines = content.split('\n')
      return (
        <div className="msg-approval-card">
          <div className="msg-approval-header">
            <SafetyOutlined style={{ color: 'var(--accent-yellow)', marginRight: 6 }} />
            <Text strong style={{ color: 'var(--accent-yellow)' }}>操作需要确认</Text>
          </div>
          <div className="msg-approval-body">
            <code>{lines[0].replace('[需要确认] ', '')}</code>
            {lines[1] && <Tag color="orange" style={{ marginTop: 6 }}>{lines[1]}</Tag>}
          </div>
        </div>
      )
    }

    if (content.startsWith('[错误]')) {
      return (
        <div className="msg-error-card">
          <Text style={{ color: 'var(--accent-red)' }}>
            {content.replace('[错误] ', '')}
          </Text>
        </div>
      )
    }

    // Normal message - render with line breaks
    return (
      <div className="msg-text">
        {content.split('\n').map((line, i) => (
          <span key={i}>
            {line}
            {i < content.split('\n').length - 1 && <br />}
          </span>
        ))}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Messages area */}
      <div className="messages-container">
        {messages.length === 0 && (
          <div className="welcome-screen">
            <div className="welcome-icon">
              <RobotOutlined />
            </div>
            <h2 className="welcome-title">OpsGuard 智能运维助手</h2>
            <p className="welcome-subtitle">描述您的运维需求，我会帮您分析和解决问题</p>
            <div className="welcome-hints">
              <Tag className="welcome-hint-tag" onClick={() => setInputValue('帮我检查一下系统整体状态')}>系统状态检查</Tag>
              <Tag className="welcome-hint-tag" onClick={() => setInputValue('最近有什么错误日志吗')}>查看错误日志</Tag>
              <Tag className="welcome-hint-tag" onClick={() => setInputValue('磁盘空间快满了，帮我分析一下')}>磁盘空间分析</Tag>
              <Tag className="welcome-hint-tag" onClick={() => setInputValue('有没有僵尸进程需要清理')}>僵尸进程检查</Tag>
            </div>
          </div>
        )}

        {messages.map((msg) => {
          // Skip progress messages - they'll be shown as part of the thinking indicator below
          if (msg.role === 'progress') return null

          return (
            <div
              key={msg.id}
              className={`message-row ${msg.role === 'user' ? 'message-row-user' : 'message-row-assistant'}`}
            >
              {/* Avatar */}
              <div className={`message-avatar ${msg.role === 'user' ? 'avatar-user' : 'avatar-assistant'}`}>
                {msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
              </div>

              {/* Bubble */}
              <div className={`message-bubble ${msg.role === 'user' ? 'bubble-user' : 'bubble-assistant'}`}>
                <div className="message-meta">
                  <Text className="message-sender">
                    {msg.role === 'user' ? '管理员' : 'OpsGuard'}
                  </Text>
                  <Text className="message-time">
                    {new Date(msg.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                  </Text>
                </div>
                {renderMessageContent(msg.content)}
              </div>
            </div>
          )
        })}

        {/* Thinking indicator with diagnosis progress - single avatar */}
        {isThinking && (
          <div className="message-row message-row-assistant">
            <div className="message-avatar avatar-assistant">
              <RobotOutlined />
            </div>
            <div className="message-bubble bubble-assistant">
              <div className="message-meta">
                <Text className="message-sender">OpsGuard</Text>
              </div>
              {(() => {
                const progressMsg = messages.find(m => m.role === 'progress')
                if (progressMsg?.progressSteps) {
                  return <DiagnosisProgress steps={progressMsg.progressSteps} visible={true} />
                }
                return (
                  <div className="thinking-indicator">
                    <LoadingOutlined style={{ color: 'var(--accent-green)', marginRight: 8 }} />
                    <Text style={{ color: 'var(--text-muted)', fontSize: 13 }}>正在分析...</Text>
                  </div>
                )
              })()}
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="input-container">
        <div className="input-wrapper">
          <TextArea
            ref={inputRef as any}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="描述您的运维需求... (Enter 发送, Shift+Enter 换行)"
            autoSize={{ minRows: 1, maxRows: 5 }}
            className="chat-input"
            disabled={isThinking}
          />
          <Button
            type="primary"
            shape="circle"
            icon={<SendOutlined />}
            onClick={handleSend}
            disabled={!inputValue.trim() || isThinking}
            className="send-button"
          />
        </div>
      </div>
    </div>
  )
}

export default ChatPanel
