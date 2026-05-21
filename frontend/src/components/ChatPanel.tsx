import { useRef, useEffect, useState } from 'react'
import { Input, Button, Typography, Tag, Tooltip, message as antdMessage } from 'antd'
import {
  SendOutlined,
  RobotOutlined,
  UserOutlined,
  LoadingOutlined,
  SafetyOutlined,
  BulbOutlined,
  PlayCircleOutlined,
  CloseCircleOutlined,
  BookOutlined,
  AudioOutlined,
  DeleteOutlined,
  FileImageOutlined,
} from '@ant-design/icons'
import { MultimodalRecognitionResult, useChatStore } from '../stores/chatStore'
import DiagnosisProgress from './DiagnosisProgress'
import MarkdownRenderer from './MarkdownRenderer'
import { displayTraceName } from '../utils/traceLocalization'
import '../styles/chat.css'

const { TextArea } = Input
const { Text } = Typography

interface PendingAttachment {
  id: string
  type: 'image' | 'audio'
  filename: string
  size: number
  previewUrl?: string
  status: 'uploading' | 'recognized' | 'failed'
  error?: string
  recognition?: MultimodalRecognitionResult
}

const renderRecommendedToolName = (tool: Record<string, unknown>) => {
  const displayName = typeof tool.display_name === 'string' ? tool.display_name : ''
  const toolId = typeof tool.tool === 'string'
    ? tool.tool
    : typeof tool.name === 'string'
      ? tool.name
      : ''
  return displayName || displayTraceName(toolId) || '推荐检查'
}

const renderRecommendedToolTip = (tool: Record<string, unknown>) => {
  const reason = typeof tool.reason === 'string' ? tool.reason : ''
  const args = tool.args && typeof tool.args === 'object'
    ? JSON.stringify(tool.args)
    : ''
  return [reason, args ? `参数：${args}` : ''].filter(Boolean).join('\n') || '建议先执行只读检查'
}

/**
 * Main chat panel - conversation flow with styled message bubbles.
 */
function ChatPanel() {
  const {
    messages, inputValue, setInputValue, sendMessage, isThinking,
    pendingRunbookSuggestion, acceptRunbookSuggestion, dismissRunbookSuggestion,
  } = useChatStore()
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const imageInputRef = useRef<HTMLInputElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const [attachments, setAttachments] = useState<PendingAttachment[]>([])
  const [isRecording, setIsRecording] = useState(false)
  const [confirmedVoiceIds, setConfirmedVoiceIds] = useState<string[]>([])
  const [confirmedLowConfidenceIds, setConfirmedLowConfidenceIds] = useState<string[]>([])
  const [showVoiceConfirm, setShowVoiceConfirm] = useState(false)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isThinking])

  const handleSend = () => {
    const recognized = attachments
      .filter((item) => item.status === 'recognized' && item.recognition)
      .map((item) => item.recognition as MultimodalRecognitionResult)
    const unconfirmedVoiceWrite = attachments.find((item) => (
      item.type === 'audio'
      && item.status === 'recognized'
      && item.recognition?.requires_write_confirmation
      && !confirmedVoiceIds.includes(item.id)
    ))
    if (unconfirmedVoiceWrite) {
      setShowVoiceConfirm(true)
      antdMessage.warning('语音中可能包含写操作，请先确认识别文本')
      return
    }
    const unconfirmedLowConfidence = attachments.find((item) => (
      item.status === 'recognized'
      && item.recognition?.needs_user_confirmation
      && !confirmedLowConfidenceIds.includes(item.id)
    ))
    if (unconfirmedLowConfidence) {
      antdMessage.warning('存在低置信度识别结果，请先确认')
      return
    }
    const content = inputValue.trim() || (recognized.length ? '请分析我上传的多模态运维信息' : '')
    if (content && !isThinking) {
      sendMessage(content, recognized)
      setInputValue('')
      setAttachments([])
      setConfirmedVoiceIds([])
      setConfirmedLowConfidenceIds([])
      setShowVoiceConfirm(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const updateAttachment = (id: string, patch: Partial<PendingAttachment>) => {
    setAttachments((items) => items.map((item) => (item.id === id ? { ...item, ...patch } : item)))
  }

  const removeAttachment = (id: string) => {
    setAttachments((items) => {
      const target = items.find((item) => item.id === id)
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
      return items.filter((item) => item.id !== id)
    })
    setConfirmedVoiceIds((ids) => ids.filter((itemId) => itemId !== id))
    setConfirmedLowConfidenceIds((ids) => ids.filter((itemId) => itemId !== id))
  }

  const analyzeImage = async (file: File) => {
    const id = crypto.randomUUID()
    const previewUrl = URL.createObjectURL(file)
    setAttachments((items) => [
      ...items,
      { id, type: 'image', filename: file.name, size: file.size, previewUrl, status: 'uploading' },
    ])
    const form = new FormData()
    form.append('file', file)
    try {
      const response = await fetch('/api/multimodal/images/analyze', { method: 'POST', body: form })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail || '图片识别失败')
      updateAttachment(id, { status: 'recognized', recognition: payload.result })
      antdMessage.success('图片识别完成')
    } catch (error) {
      const text = error instanceof Error ? error.message : '图片识别失败'
      updateAttachment(id, { status: 'failed', error: text })
      antdMessage.error(text)
    }
  }

  const handleImageSelect = (files: FileList | null) => {
    const selected = Array.from(files || []).slice(0, 3)
    selected.forEach((file) => analyzeImage(file))
    if (imageInputRef.current) imageInputRef.current.value = ''
  }

  const handlePaste = (event: React.ClipboardEvent) => {
    const files = Array.from(event.clipboardData.files || []).filter((file) => file.type.startsWith('image/'))
    if (files.length) {
      event.preventDefault()
      files.slice(0, 3).forEach((file) => analyzeImage(file))
    }
  }

  const handleDrop = (event: React.DragEvent) => {
    const files = Array.from(event.dataTransfer.files || []).filter((file) => file.type.startsWith('image/'))
    if (files.length) {
      event.preventDefault()
      files.slice(0, 3).forEach((file) => analyzeImage(file))
    }
  }

  const uploadAudio = async (blob: Blob) => {
    const id = crypto.randomUUID()
    setAttachments((items) => [
      ...items,
      { id, type: 'audio', filename: '语音输入.webm', size: blob.size, status: 'uploading' },
    ])
    const form = new FormData()
    form.append('file', blob, 'voice.webm')
    try {
      const response = await fetch('/api/multimodal/audio/transcribe', { method: 'POST', body: form })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail || '语音识别失败')
      const result = payload.result as MultimodalRecognitionResult
      updateAttachment(id, { status: 'recognized', recognition: result })
      setInputValue(result.normalized_transcript || result.raw_transcript || '')
      if (result.requires_write_confirmation) {
        setShowVoiceConfirm(true)
        antdMessage.warning('语音中可能包含写操作，请核对识别文本后再发送')
      } else {
        antdMessage.success('语音识别完成')
      }
    } catch (error) {
      const text = error instanceof Error ? error.message : '语音识别失败'
      updateAttachment(id, { status: 'failed', error: text })
      antdMessage.error(text)
    }
  }

  const toggleRecording = async () => {
    if (isRecording) {
      mediaRecorderRef.current?.stop()
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      audioChunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop())
        setIsRecording(false)
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        if (blob.size > 0) uploadAudio(blob)
      }
      mediaRecorderRef.current = recorder
      recorder.start()
      setIsRecording(true)
    } catch {
      antdMessage.error('无法访问麦克风，请检查浏览器权限')
    }
  }

  const renderAttachments = () => {
    if (!attachments.length) return null
    return (
      <div className="attachment-strip">
        {attachments.map((item) => (
          <div key={item.id} className={`attachment-chip attachment-${item.status}`}>
            {item.previewUrl ? (
              <img src={item.previewUrl} alt={item.filename} className="attachment-thumb" />
            ) : (
              <AudioOutlined className="attachment-audio-icon" />
            )}
            <div className="attachment-info">
              <div className="attachment-name">{item.filename}</div>
              <div className="attachment-status">
                {item.status === 'uploading' && '正在识别...'}
                {item.status === 'recognized' && (
                  item.type === 'audio'
                    ? item.recognition?.normalized_transcript || '语音识别完成'
                    : item.recognition?.summary || '图片识别完成'
                )}
                {item.status === 'failed' && item.error}
              </div>
              {item.status === 'recognized' && item.recognition?.warnings?.length ? (
                <div className="attachment-warning">{item.recognition.warnings[0]}</div>
              ) : null}
              {item.status === 'recognized' && item.recognition?.recommended_tools?.length ? (
                <div className="attachment-tools">
                  {(item.recognition.recommended_tools || []).slice(0, 3).map((tool, index) => (
                    <Tooltip key={index} title={renderRecommendedToolTip(tool)}>
                      <Tag className="attachment-tool-tag">
                        {renderRecommendedToolName(tool)}
                      </Tag>
                    </Tooltip>
                  ))}
                </div>
              ) : null}
              {item.status === 'recognized' && item.recognition?.needs_user_confirmation && !confirmedLowConfidenceIds.includes(item.id) ? (
                <Button
                  size="small"
                  type="link"
                  style={{ padding: 0, height: 'auto', fontSize: 11 }}
                  onClick={() => setConfirmedLowConfidenceIds((ids) => [...ids, item.id])}
                >
                  已核对识别结果
                </Button>
              ) : null}
            </div>
            <Button
              size="small"
              type="text"
              icon={<DeleteOutlined />}
              onClick={() => removeAttachment(item.id)}
            />
          </div>
        ))}
      </div>
    )
  }

  const renderVoiceConfirmation = () => {
    if (!showVoiceConfirm) return null
    const pending = attachments.filter((item) => (
      item.type === 'audio'
      && item.status === 'recognized'
      && item.recognition?.requires_write_confirmation
      && !confirmedVoiceIds.includes(item.id)
    ))
    if (!pending.length) return null
    return (
      <div className="voice-confirm-bar">
        <div>
          <div className="voice-confirm-title">语音可能包含写操作</div>
          <div className="voice-confirm-text">
            系统理解为：{pending.map((item) => item.recognition?.normalized_transcript || item.recognition?.raw_transcript).join('；')}
          </div>
        </div>
        <Button
          size="small"
          type="primary"
          onClick={() => {
            setConfirmedVoiceIds((ids) => [...ids, ...pending.map((item) => item.id)])
            setShowVoiceConfirm(false)
          }}
        >
          确认识别文本
        </Button>
      </div>
    )
  }

  const renderMessageContent = (content: string, role: string) => {
    // Detect special message types
    if (content.startsWith('[需要确认]')) {
      const lines = content.split('\n')
      const command = lines[0].replace('[需要确认] ', '')
      return (
        <div className="msg-approval-card">
          <div className="msg-approval-header">
            <SafetyOutlined style={{ color: 'var(--accent-yellow)', marginRight: 6 }} />
            <Text strong style={{ color: 'var(--accent-yellow)' }}>操作需要确认</Text>
          </div>
          <div className="msg-approval-body">
            <code>{command}</code>
            {lines.slice(1).map((line, i) => (
              <div key={i} style={{ marginTop: 4, fontSize: 12, color: 'var(--text-secondary)' }}>{line}</div>
            ))}
            <Button
              size="small"
              type="link"
              icon={<BulbOutlined />}
              style={{ padding: 0, marginTop: 8, fontSize: 12 }}
              onClick={() => {
                setInputValue(`请解释这条命令的含义、每个参数的作用以及可能的影响：${command}`)
              }}
            >
              解释这条命令
            </Button>
          </div>
        </div>
      )
    }

    if (content.startsWith('[Runbook建议]')) {
      // Inline suggestion card. Buttons are only active for the currently
      // pending suggestion — historical suggestion messages are read-only.
      const lines = content.split('\n')
      const name = lines[0].replace('[Runbook建议] ', '')
      const isActive =
        !!pendingRunbookSuggestion && pendingRunbookSuggestion.name === name
      return (
        <div className="msg-approval-card" style={{ borderColor: 'var(--accent-blue, #61afef)' }}>
          <div className="msg-approval-header">
            <BookOutlined style={{ color: 'var(--accent-blue, #61afef)', marginRight: 6 }} />
            <Text strong style={{ color: 'var(--accent-blue, #61afef)' }}>
              发现匹配的 Runbook
            </Text>
          </div>
          <div className="msg-approval-body">
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{name}</div>
            {lines.slice(1).map((line, i) => (
              line ? (
                <div key={i} style={{ marginTop: 2, fontSize: 12, color: 'var(--text-secondary)' }}>
                  {line}
                </div>
              ) : null
            ))}
            <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
              <Button
                size="small"
                type="primary"
                icon={<PlayCircleOutlined />}
                disabled={!isActive || isThinking}
                onClick={acceptRunbookSuggestion}
              >
                按 Runbook 执行
              </Button>
              <Button
                size="small"
                icon={<CloseCircleOutlined />}
                disabled={!isActive || isThinking}
                onClick={dismissRunbookSuggestion}
              >
                重新分析
              </Button>
              {!isActive && (
                <Text style={{ fontSize: 11, color: 'var(--text-muted)', alignSelf: 'center' }}>
                  （此建议已处理）
                </Text>
              )}
            </div>
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

    // Assistant messages: render as Markdown
    if (role === 'assistant') {
      return <MarkdownRenderer content={content} />
    }

    // User messages: plain text with line breaks
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
    <div
      style={{ display: 'flex', flexDirection: 'column', height: '100%' }}
      onPaste={handlePaste}
      onDrop={handleDrop}
      onDragOver={(event) => event.preventDefault()}
    >
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
                {renderMessageContent(msg.content, msg.role)}
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
        {renderAttachments()}
        {renderVoiceConfirmation()}
        <div className="input-wrapper">
          <input
            ref={imageInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            style={{ display: 'none' }}
            onChange={(event) => handleImageSelect(event.target.files)}
          />
          <Tooltip title="上传运维截图">
            <Button
              shape="circle"
              icon={<FileImageOutlined />}
              onClick={() => imageInputRef.current?.click()}
              disabled={isThinking}
              className="input-tool-button"
            />
          </Tooltip>
          <Tooltip title={isRecording ? '停止录音' : '语音输入'}>
            <Button
              shape="circle"
              icon={isRecording ? <LoadingOutlined /> : <AudioOutlined />}
              onClick={toggleRecording}
              disabled={isThinking}
              className={isRecording ? 'input-tool-button recording' : 'input-tool-button'}
            />
          </Tooltip>
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
            disabled={(!inputValue.trim() && !attachments.some((item) => item.status === 'recognized')) || isThinking}
            className="send-button"
          />
        </div>
      </div>
    </div>
  )
}

export default ChatPanel
