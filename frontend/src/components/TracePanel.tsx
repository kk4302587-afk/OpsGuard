import { useMemo, useState } from 'react'
import { Typography, Tag, Empty, Button } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  SafetyOutlined,
  SearchOutlined,
  ToolOutlined,
  BulbOutlined,
  ApartmentOutlined,
  StopOutlined,
  DatabaseOutlined,
  MessageOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'
import {
  displayTraceName,
  localizeTraceContent,
  translateTraceClaim,
  translateTraceText,
} from '../utils/traceLocalization'

const { Text, Title } = Typography

interface TraceEvidence {
  phase: string
  event_type: string
  content: string
  timestamp: string
  claim?: string
  evidence_type?: string
  source?: string
  observed?: string
  confidence?: string
  execution_state?: string
  failure_reason?: string
  next_check?: string
  metadata?: Record<string, unknown>
}

interface TraceGroup {
  id: string
  title: string
  startTime: string
  events: TraceEvidence[]
  status: 'running' | 'success' | 'failure' | 'blocked' | 'corrected'
}

/**
 * Right panel showing the reasoning trace (ThoughtChain).
 * Visualizes: input → safety check → plan → tool calls → verify → respond
 */
function TracePanel() {
  const { traceEvents, sendMessage } = useChatStore()
  const visibleTraceEvents = traceEvents.filter((event) => event.phase !== 'recent_changes')
  const [expandedGroupIds, setExpandedGroupIds] = useState<Record<string, boolean>>({})

  const getRequestGroups = (events: TraceEvidence[]): TraceGroup[] => {
    const groups: TraceGroup[] = []
    let current: TraceGroup | null = null

    events.forEach((event, index) => {
      if (!current || event.phase === 'input_received') {
        current = {
          id: `${event.timestamp || index}-${groups.length}`,
          title: event.phase === 'input_received' ? localizeTraceContent(event.content) : '当前请求',
          startTime: event.timestamp,
          events: [],
          status: 'running',
        }
        groups.push(current)
      }
      current.events.push(event)
    })

    return groups.map((group) => {
      const response = [...group.events].reverse().find((event) => event.phase === 'response')
      const failed = group.events.some((event) => event.event_type === 'failure' && !isNonFatalGuardEvent(event))
      const blocked = group.events.some((event) => event.event_type === 'blocked')
      const corrected = group.events.some((event) => isNonFatalGuardEvent(event))
      return {
        ...group,
        status: blocked ? 'blocked' : failed ? 'failure' : response?.event_type === 'success' ? 'success' : corrected ? 'corrected' : 'running',
      }
    })
  }

  const isNonFatalGuardEvent = (event: TraceEvidence) => {
    const metadataEvidence = event.metadata?.evidence as Record<string, unknown> | undefined
    const source = event.source || (typeof metadataEvidence?.source === 'string' ? metadataEvidence.source : '')
    const text = `${source} ${event.content || ''} ${event.claim || ''}`
    return (
      source === 'read_tool_truthfulness_guard' ||
      text.includes('真实性守卫') ||
      text.includes('回复引用未执行的只读工具')
    )
  }

  const traceGroups = useMemo(() => getRequestGroups(visibleTraceEvents as TraceEvidence[]), [visibleTraceEvents])
  const latestGroupId = traceGroups[traceGroups.length - 1]?.id

  const isGroupExpanded = (group: TraceGroup) => (
    expandedGroupIds[group.id] ?? group.id === latestGroupId
  )

  const toggleGroup = (groupId: string) => {
    setExpandedGroupIds((state) => ({ ...state, [groupId]: !(state[groupId] ?? groupId === latestGroupId) }))
  }

  const getEvidenceStateColor = (state?: string): string => {
    switch (state) {
      case 'executed': return 'green'
      case 'failed': return 'red'
      case 'skipped': return 'orange'
      case 'inferred': return 'blue'
      default: return 'default'
    }
  }

  const getConfidenceColor = (confidence?: string): string => {
    switch (confidence) {
      case 'high': return 'green'
      case 'medium': return 'blue'
      case 'low': return 'orange'
      default: return 'default'
    }
  }

  const getEvidenceLabel = (key: string, value?: string) => {
    const labels: Record<string, string> = {
      command: '命令',
      log: '日志',
      config: '配置',
      metric: '指标',
      topology: '拓扑',
      knowledge: '知识库',
      'user input': '用户输入',
      executed: '已执行',
      inferred: '推断',
      skipped: '未执行',
      failed: '失败',
      high: '高',
      medium: '中',
      low: '低',
    }
    return labels[value || key] || value || key
  }

  const renderEvidence = (event: TraceEvidence) => {
    if (!event.claim && !event.source && !event.observed && !event.execution_state) return null

    return (
      <div
        style={{
          marginTop: 8,
          padding: '8px 10px',
          border: '1px solid var(--border-color)',
          borderRadius: 6,
          background: 'rgba(255, 255, 255, 0.03)',
        }}
      >
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
          {event.execution_state && (
            <Tag color={isNonFatalGuardEvent(event) ? 'orange' : getEvidenceStateColor(event.execution_state)} style={{ fontSize: 10, margin: 0 }}>
              {isNonFatalGuardEvent(event) ? '已修正' : getEvidenceLabel('execution_state', event.execution_state)}
            </Tag>
          )}
          {event.evidence_type && (
            <Tag color="geekblue" style={{ fontSize: 10, margin: 0 }}>
              {getEvidenceLabel('evidence_type', event.evidence_type)}
            </Tag>
          )}
          {event.confidence && (
            <Tag color={getConfidenceColor(event.confidence)} style={{ fontSize: 10, margin: 0 }}>
              置信度 {getEvidenceLabel('confidence', event.confidence)}
            </Tag>
          )}
        </div>
        {event.claim && (
          <Text style={{ display: 'block', fontSize: 11, color: 'var(--text-primary)', marginBottom: 4 }}>
            {translateTraceClaim(event.claim)}
          </Text>
        )}
        {event.source && (
          <Text style={{ display: 'block', fontSize: 10, color: 'var(--text-muted)', wordBreak: 'break-all' }}>
            来源: {displayTraceName(event.source)}
          </Text>
        )}
        {event.observed && (
          <Text
            style={{
              display: 'block',
              marginTop: 4,
              fontSize: 10,
              color: 'var(--text-secondary)',
              fontFamily: 'var(--font-mono)',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {translateTraceText(event.observed)}
          </Text>
        )}
        {event.failure_reason && (
          <Text style={{ display: 'block', marginTop: 4, fontSize: 10, color: isNonFatalGuardEvent(event) ? 'var(--text-muted)' : 'var(--accent-red)' }}>
            {isNonFatalGuardEvent(event) ? '修正原因' : '失败原因'}: {translateTraceText(event.failure_reason)}
          </Text>
        )}
        {event.next_check && (
          <Text style={{ display: 'block', marginTop: 4, fontSize: 10, color: 'var(--text-muted)' }}>
            下一步: {translateTraceText(event.next_check)}
          </Text>
        )}
      </div>
    )
  }

  const getPhaseIcon = (event: TraceEvidence) => {
    const iconStyle = { fontSize: 14 }
    const { phase, event_type: eventType } = event

    if (isNonFatalGuardEvent(event)) return <BulbOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
    if (eventType === 'blocked') return <StopOutlined style={{ ...iconStyle, color: 'var(--accent-red)' }} />
    if (eventType === 'failure') return <CloseCircleOutlined style={{ ...iconStyle, color: 'var(--accent-red)' }} />

    switch (phase) {
      case 'safety_check': return <SafetyOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
      case 'knowledge_retrieval': return <SearchOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      case 'image_recognition': return <SearchOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      case 'voice_recognition': return <MessageOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      case 'multimodal_recognition': return <SearchOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      case 'planning': return <BulbOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
      case 'tool_call': return <ToolOutlined style={{ ...iconStyle, color: 'var(--accent-purple)' }} />
      case 'execution': return <LoadingOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      case 'approval_request': return <SafetyOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
      case 'approval_response': return <CheckCircleOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
      case 'verification': return <CheckCircleOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
      case 'response': return <MessageOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
      case 'knowledge_save': return <DatabaseOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      default: return <CheckCircleOutlined style={iconStyle} />
    }
  }

  const getEventColor = (eventType: string): string => {
    switch (eventType) {
      case 'success': return 'green'
      case 'failure': return 'red'
      case 'blocked': return 'red'
      case 'pending': return 'orange'
      case 'start': return 'blue'
      default: return 'default'
    }
  }

  const getTraceEventColor = (event: TraceEvidence): string => {
    if (isNonFatalGuardEvent(event)) return 'orange'
    return getEventColor(event.event_type)
  }

  const getPhaseLabel = (phase: string) => {
    const labels: Record<string, string> = {
      input_received: '接收指令',
      safety_check: '安全校验',
      knowledge_retrieval: '知识检索',
      image_recognition: '图片识别',
      voice_recognition: '语音识别',
      multimodal_recognition: '多模态识别',
      planning: '推理规划',
      tool_call: '工具调用',
      approval_request: '审批请求',
      approval_response: '审批响应',
      execution: '执行操作',
      verification: '结果验证',
      response: '生成回复',
      error: '错误',
      knowledge_save: '知识沉淀',
    }
    return labels[phase] || phase
  }

  const getDisplayContent = (event: TraceEvidence & { phase?: string; content?: string }) => {
    const content = event.content || ''
    return localizeTraceContent(content)
  }

  const getGroupStatusColor = (status: TraceGroup['status']) => {
    switch (status) {
      case 'success': return 'green'
      case 'failure': return 'red'
      case 'blocked': return 'red'
      case 'corrected': return 'orange'
      default: return 'blue'
    }
  }

  const getGroupStatusLabel = (status: TraceGroup['status']) => {
    switch (status) {
      case 'success': return '已完成'
      case 'failure': return '失败'
      case 'blocked': return '已拦截'
      case 'corrected': return '已修正'
      default: return '进行中'
    }
  }

  const compactGroupTitle = (title: string) => {
    const text = title.replace(/\s+/g, ' ').trim()
    if (!text) return '当前请求'
    return text.length > 34 ? `${text.slice(0, 34)}...` : text
  }

  const renderTraceEvent = (event: TraceEvidence, index: number, total: number) => (
    <div
      key={`${event.timestamp}-${event.phase}-${index}`}
      style={{
        display: 'flex',
        gap: 10,
        marginBottom: 12,
        paddingBottom: 12,
        borderBottom: index < total - 1 ? '1px solid var(--border-color)' : 'none',
      }}
    >
      <div style={{ paddingTop: 2 }}>
        {getPhaseIcon(event)}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
          <Tag
            color={getTraceEventColor(event)}
            style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', margin: 0 }}
          >
            {getPhaseLabel(event.phase)}
          </Tag>
          <Text style={{ fontSize: 10, color: 'var(--text-muted)' }}>
            {new Date(event.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </Text>
        </div>
        <Text
          style={{
            fontSize: 11,
            color: event.event_type === 'blocked' ? 'var(--accent-red)' : 'var(--text-secondary)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {getDisplayContent(event)}
        </Text>
        {renderEvidence(event)}
        {event.phase === 'tool_call' && (event.content.includes('调用工具') || event.content.includes('准备调用工具')) && (
          <Button
            size="small"
            type="link"
            icon={<BulbOutlined />}
            style={{ padding: 0, marginTop: 4, fontSize: 11, height: 'auto' }}
            onClick={() => sendMessage(`请解释这个操作的含义和作用：${getDisplayContent(event)}`)}
          >
            解释
          </Button>
        )}
      </div>
    </div>
  )

  return (
    <div style={{ padding: 16, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Title level={5} style={{ color: 'var(--text-primary)', marginBottom: 16, fontSize: 14 }}>
        <ApartmentOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
        推理链路
      </Title>

      {visibleTraceEvents.length === 0 ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Empty
            description={<Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>发送消息后查看推理过程</Text>}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        </div>
      ) : (
        <div style={{ flex: 1, overflow: 'auto' }}>
          {traceGroups.map((group, groupIndex) => {
            const expanded = isGroupExpanded(group)
            return (
            <div
              key={group.id}
              style={{
                marginBottom: 10,
                borderBottom: groupIndex < traceGroups.length - 1 ? '1px solid var(--border-color)' : 'none',
                paddingBottom: 10,
              }}
            >
              <button
                type="button"
                onClick={() => toggleGroup(group.id)}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 0',
                  background: 'transparent',
                  border: 0,
                  color: 'inherit',
                  textAlign: 'left',
                  cursor: 'pointer',
                }}
              >
                {expanded ? <DownOutlined style={{ fontSize: 10, color: 'var(--text-muted)' }} /> : <RightOutlined style={{ fontSize: 10, color: 'var(--text-muted)' }} />}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Text
                    style={{
                      display: 'block',
                      fontSize: 12,
                      color: 'var(--text-primary)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {compactGroupTitle(group.title)}
                  </Text>
                  <Text style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                    {new Date(group.startTime).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    {' · '}
                    {group.events.length} 个事件
                  </Text>
                </div>
                <Tag color={getGroupStatusColor(group.status)} style={{ fontSize: 10, margin: 0 }}>
                  {getGroupStatusLabel(group.status)}
                </Tag>
              </button>
              {expanded && (
                <div
                  style={{
                    marginTop: 4,
                    paddingLeft: 2,
                  }}
                >
                  {group.events.map((event, index) => renderTraceEvent(event, index, group.events.length))}
                </div>
              )}
            </div>
          )})}
        </div>
      )}
    </div>
  )
}

export default TracePanel
