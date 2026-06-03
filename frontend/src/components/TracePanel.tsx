import { useMemo, useState } from 'react'
import { Collapse, Typography, Tag, Empty, Button } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  SafetyOutlined,
  ToolOutlined,
  BulbOutlined,
  DatabaseOutlined,
  SearchOutlined,
  ApartmentOutlined,
  StopOutlined,
  MessageOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'
import {
  TOOL_DISPLAY_NAMES,
  displayTraceName,
  localizeTraceContent,
  translateTraceClaim,
  translateTraceText,
} from '../utils/traceLocalization'
import { parseOperationCommand, operationTarget, operationTitle } from '../utils/operationSummary'

const { Text, Title } = Typography

interface TraceEvidence {
  id?: string
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
  status: 'running' | 'success' | 'warning' | 'failure' | 'blocked' | 'corrected' | 'rejected'
}

interface TraceDisplayItem {
  id: string
  kind: 'planning' | 'tool' | 'approval' | 'verification' | 'response' | 'guard' | 'other'
  phase: string
  timestamp: string
  title: string
  detail: string
  status: 'running' | 'success' | 'warning' | 'failure' | 'blocked' | 'rejected' | 'skipped'
  toolName?: string
  target?: string
  executionCount?: number
  events: TraceEvidence[]
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

    return groups.map((group, groupIndex) => {
      const response = [...group.events].reverse().find((event) => event.phase === 'response')
      const rejected = group.events.some((event) => isApprovalRejectedEvent(event))
      const failureIndexes = group.events
        .map((event, index) => (isFatalFailureEvent(event) ? index : -1))
        .filter((index) => index >= 0)
      const latestFailureIndex = failureIndexes[failureIndexes.length - 1] ?? -1
      const failed = failureIndexes.length > 0
      const recovered = (
        failed
        && !!response
        && (
          isCompletionResponse(response)
          || response.event_type === 'success'
          || group.events.some((event, index) => (
            index > latestFailureIndex && isOperationalSuccessEvent(event)
          ))
        )
      )
      const blocked = group.events.some((event) => event.event_type === 'blocked')
      const corrected = group.events.some((event) => isNonFatalGuardEvent(event))
      const superseded = !response && groupIndex < groups.length - 1
      return {
        ...group,
        status: blocked ? 'blocked' : rejected ? 'rejected' : failed && recovered ? 'warning' : failed ? 'failure' : response?.event_type === 'success' || superseded ? 'success' : corrected ? 'corrected' : 'running',
      }
    })
  }

  const traceText = (event: TraceEvidence) => {
    const metadataEvidence = event.metadata?.evidence as Record<string, unknown> | undefined
    const metadataObserved = typeof metadataEvidence?.observed === 'string' ? metadataEvidence.observed : ''
    const metadataClaim = typeof metadataEvidence?.claim === 'string' ? metadataEvidence.claim : ''
    return [event.content, event.observed, event.claim, metadataObserved, metadataClaim]
      .filter(Boolean)
      .join(' ')
  }

  const isFatalFailureEvent = (event: TraceEvidence) => (
    event.event_type === 'failure'
    && !isNonFatalGuardEvent(event)
    && !isApprovalRejectedEvent(event)
  )

  const isOperationalSuccessEvent = (event: TraceEvidence) => (
    event.event_type === 'success'
    && ['execution', 'verification', 'tool_call'].includes(event.phase)
  )

  const isCompletionResponse = (event: TraceEvidence) => {
    if (event.phase !== 'response' || event.event_type !== 'success') return false
    const text = traceText(event)
    const cancellationMarkers = [
      '操作已取消',
      '未执行',
      '未完成',
      '未成功',
      '没有成功',
      '无法完成',
      '不能确认',
      '没有真实执行',
    ]
    const completionMarkers = [
      '成功',
      '已完成',
      '操作完成',
      '已执行',
      '已复制',
      '已写入',
      '已创建',
      '已删除',
      '已移动',
      '已重命名',
      '已启动',
      '已停止',
      '已重启',
      '内容完全一致',
    ]
    return completionMarkers.some((marker) => text.includes(marker))
      && !cancellationMarkers.some((marker) => text.includes(marker))
  }

  const isApprovalRejectedEvent = (event: TraceEvidence) => {
    const metadataEvidence = event.metadata?.evidence as Record<string, unknown> | undefined
    const source = event.source || (typeof metadataEvidence?.source === 'string' ? metadataEvidence.source : '')
    const failureReason = event.failure_reason || (typeof metadataEvidence?.failure_reason === 'string' ? metadataEvidence.failure_reason : '')
    const observed = event.observed || (typeof metadataEvidence?.observed === 'string' ? metadataEvidence.observed : '')
    const text = `${event.content || ''} ${failureReason} ${observed}`
    return (
      event.phase === 'approval_response'
      && event.event_type === 'failure'
      && source === 'approval_manager'
      && (
        text.includes('用户拒绝')
        || text.includes('用户未批准')
        || text.includes('审批超时')
      )
    )
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
      alert: '告警',
      dashboard_link: '面板链接',
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

  const getMetadataRecord = (event: TraceEvidence, key: string): Record<string, unknown> | null => {
    const value = event.metadata?.[key]
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
  }

  const getEvidenceRecord = (event: TraceEvidence): Record<string, unknown> | null => (
    getMetadataRecord(event, 'evidence')
  )

  const getEventSource = (event: TraceEvidence) => {
    const evidence = getEvidenceRecord(event)
    return event.source || (typeof evidence?.source === 'string' ? evidence.source : '')
  }

  const getEventObserved = (event: TraceEvidence): unknown => {
    const evidence = getEvidenceRecord(event)
    return event.observed ?? evidence?.observed
  }

  const isToolName = (value?: string) => Boolean(value && TOOL_DISPLAY_NAMES[value])

  const extractToolInfo = (event: TraceEvidence): { toolName: string; args: Record<string, unknown>; target: string } | null => {
    const metadataArgs = getMetadataRecord(event, 'args')
    const metadataCommand = typeof event.metadata?.command === 'string' ? parseOperationCommand(event.metadata.command) : null
    const observed = getEventObserved(event)
    const observedArgs = observed && typeof observed === 'object' && !Array.isArray(observed)
      ? observed as Record<string, unknown>
      : null
    const source = getEventSource(event)

    let toolName = isToolName(source) ? source : ''
    let args = metadataArgs || metadataCommand?.args || observedArgs || {}

    const content = event.content || ''
    const named = content.match(/(?:工具调用|工具执行成功|工具执行失败):\s*([A-Za-z_][A-Za-z0-9_]*)/)
      || content.match(/(?:准备调用工具|调用工具):\s*([A-Za-z_][A-Za-z0-9_]*)/)
    if (!toolName && named?.[1]) toolName = named[1]

    const directCommand = parseOperationCommand(content)
    if (!toolName && directCommand) {
      toolName = directCommand.toolName
      args = directCommand.args
    }

    const argsMatch = content.match(/参数：(\{.*\})/s)
    if (argsMatch && !Object.keys(args).length) {
      try {
        const parsed = JSON.parse(argsMatch[1])
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          args = parsed as Record<string, unknown>
        }
      } catch {
        // Keep the compact timeline readable even if old trace args are malformed.
      }
    }

    if (!toolName || !isToolName(toolName)) return null
    return { toolName, args, target: operationTarget(args) }
  }

  const getToolKey = (tool: { toolName: string; target: string }) => `${tool.toolName}:${tool.target || 'unknown'}`

  const findMergeableToolIndex = (
    items: TraceDisplayItem[],
    tool: { toolName: string; target: string },
    preferredIndex?: number,
  ) => {
    if (preferredIndex !== undefined) return preferredIndex
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index]
      if (item.kind !== 'tool' || item.toolName !== tool.toolName) continue
      if (!tool.target || !item.target || item.target === tool.target) return index
      break
    }
    return undefined
  }

  const eventStatus = (event: TraceEvidence): TraceDisplayItem['status'] => {
    if (isApprovalRejectedEvent(event)) return 'rejected'
    if (event.event_type === 'blocked') return 'blocked'
    if (event.event_type === 'failure') return 'failure'
    if (event.event_type === 'success') return 'success'
    if (event.event_type === 'start') return 'running'
    if (event.execution_state === 'skipped') return 'skipped'
    return 'running'
  }

  const statusRank: Record<TraceDisplayItem['status'], number> = {
    failure: 6,
    blocked: 5,
    rejected: 4,
    warning: 4,
    success: 4,
    skipped: 3,
    running: 1,
  }

  const mergeStatus = (current: TraceDisplayItem['status'], next: TraceDisplayItem['status']) => {
    if ((current === 'failure' && next === 'success') || (current === 'success' && next === 'failure')) {
      return 'warning'
    }
    if (current === 'warning' && next === 'success') return 'warning'
    return statusRank[next] > statusRank[current] ? next : current
  }

  const shouldSkipStandaloneEvent = (event: TraceEvidence) => (
    event.phase === 'input_received'
    || event.phase === 'recent_changes'
    || isCoarseAuditDuplicate(event)
    || (
      event.phase === 'planning'
      && (event.content || '').includes('策略层检测到本轮已处理等价工具调用')
    )
  )

  const isCoarseAuditDuplicate = (event: TraceEvidence) => (
    typeof event.id === 'string'
    && event.id.startsWith('audit:')
    && (
      (event.phase === 'safety_check' && (event.content || '').startsWith('检查输入:'))
      || (event.phase === 'planning' && event.content === '开始推理')
      || (event.phase === 'tool_call' && (event.content || '').startsWith('工具调用:'))
      || (event.phase === 'tool_call' && (event.content || '').startsWith('Runbook step '))
      || (event.phase === 'execution' && (event.content || '').startsWith('工具执行'))
      || (event.phase === 'execution' && (event.content || '').startsWith('Runbook step '))
      || (event.phase === 'response' && (event.content || '').startsWith('生成回复:'))
      || (event.phase === 'response' && (event.content || '').startsWith('Runbook replay finished:'))
    )
  )

  const isAggregatedPhase = (phase: string) => (
    [
      'safety_check',
      'knowledge_retrieval',
      'image_recognition',
      'voice_recognition',
      'multimodal_recognition',
      'context_management',
      'planning',
      'verification',
      'response',
      'knowledge_save',
      'error',
    ].includes(phase)
  )

  const phaseKind = (phase: string): TraceDisplayItem['kind'] => {
    if (phase === 'planning') return 'planning'
    if (phase === 'verification') return 'verification'
    if (phase === 'response') return 'response'
    if (phase === 'safety_check') return 'guard'
    return 'other'
  }

  const phaseTitle = (phase: string) => {
    if (phase === 'safety_check') return '安全校验'
    if (phase === 'knowledge_retrieval') return '知识检索'
    if (phase === 'planning') return '分析并制定方案'
    if (phase === 'verification') return '结果验证'
    if (phase === 'response') return '生成回复'
    if (phase === 'knowledge_save') return '知识沉淀'
    if (phase === 'image_recognition') return '图片识别'
    if (phase === 'voice_recognition') return '语音识别'
    if (phase === 'multimodal_recognition') return '多模态识别'
    if (phase === 'context_management') return '上下文管理'
    return getPhaseLabel(phase)
  }

  const phaseDetail = (phase: string, events: TraceEvidence[]) => {
    const latest = [...events].reverse().find((event) => event.event_type === 'success' || event.event_type === 'failure' || event.event_type === 'blocked')
      || events[events.length - 1]
    if (!latest) return ''
    if (phase === 'safety_check') {
      if (latest.event_type === 'success') return '检查通过，继续分析'
      if (latest.event_type === 'blocked') return '请求被安全规则拦截'
      return '正在检查用户输入'
    }
    if (phase === 'knowledge_retrieval') {
      if (latest.event_type === 'success') return '已检索历史经验'
      if (latest.event_type === 'failure') return '知识检索失败'
      return '正在检索历史经验'
    }
    if (phase === 'planning') {
      const hasImpact = events.some((event) => (event.content || '').includes('影响评估'))
      const hasCompiledTool = events.some((event) => (event.content || '').includes('策略层已将高确定性运维意图编译为工具调用'))
      if (hasImpact) return '已识别操作意图，并完成影响评估'
      if (hasCompiledTool) return '已识别操作意图，并补全工具调用'
      if (latest.event_type === 'success') return '已完成意图判断和执行规划'
      if (latest.event_type === 'failure') return '规划失败'
      return '正在分析问题并制定方案'
    }
    if (phase === 'context_management') {
      if (latest.event_type === 'success') return '已完成上下文分层和预算注入'
      if (latest.event_type === 'failure') return '上下文管理失败'
      return '正在整理上下文'
    }
    if (phase === 'verification') {
      if (latest.event_type === 'success') return '验证通过'
      if (latest.event_type === 'failure') return '验证失败'
      return '正在验证执行结果'
    }
    if (phase === 'response') return '已生成面向用户的结果'
    if (phase === 'knowledge_save') return '已尝试沉淀本次经验'
    return getCompactEventContent(latest)
  }

  const getDetailPhaseLabel = (event: TraceEvidence) => {
    if (event.phase !== 'planning') return getPhaseLabel(event.phase)
    const content = event.content || ''
    const source = getEventSource(event)
    if (content.includes('影响评估') || source === 'assess_impact') return '影响评估'
    if (content.includes('策略层已将高确定性运维意图编译为工具调用') || source === 'intent_policy_compiler') return '策略补全'
    if (event.claim?.includes('规划下一步') || event.claim?.includes('planning next checks')) return '意图识别'
    if (event.event_type === 'start') return '开始规划'
    return '规划事件'
  }

  const getDetailContent = (event: TraceEvidence) => {
    if (event.phase === 'planning') {
      const content = event.content || ''
      if (content.includes('影响评估')) return content
      if (content.includes('正在分析问题并制定方案')) return '正在分析用户意图和可执行方案'
      if (content.includes('策略层已将高确定性运维意图编译为工具调用')) {
        return content.replace('策略层已将高确定性运维意图编译为工具调用：', '补全工具调用：')
      }
      const observed = getEventObserved(event)
      if (typeof observed === 'string' && observed) return observed
    }
    return getDisplayContent(event)
  }

  const shouldPreferPhaseEvent = (existing: TraceEvidence, next: TraceEvidence) => {
    if (isCoarseAuditDuplicate(existing) && !isCoarseAuditDuplicate(next)) return true
    const rank: Record<string, number> = { blocked: 5, failure: 4, success: 3, pending: 2, start: 1 }
    return (rank[next.event_type] || 0) >= (rank[existing.event_type] || 0)
  }

  const buildDisplayItems = (events: TraceEvidence[]): TraceDisplayItem[] => {
    const items: TraceDisplayItem[] = []
    const toolIndexes = new Map<string, number>()
    const phaseIndexes = new Map<string, number>()
    let approvalIndex = -1

    events.forEach((event, index) => {
      if (shouldSkipStandaloneEvent(event)) return

      const tool = extractToolInfo(event)
      if (tool && ['tool_call', 'execution', 'verification'].includes(event.phase)) {
        const key = getToolKey(tool)
        const existingIndex = findMergeableToolIndex(items, tool, toolIndexes.get(key))
        const executionHit = event.phase === 'execution' && (
          event.event_type === 'success'
          || event.event_type === 'failure'
          || (event.content || '').includes('工具执行')
        )

        if (existingIndex !== undefined) {
          const item = items[existingIndex]
          item.events.push(event)
          item.status = mergeStatus(item.status, eventStatus(event))
          item.executionCount = (item.executionCount || 0) + (executionHit ? 1 : 0)
          if (!item.target && tool.target) item.target = tool.target
          if (!item.detail && tool.target) item.detail = `目标：${tool.target}`
          if (event.phase === 'verification' && event.event_type === 'success' && item.status !== 'failure' && item.status !== 'warning') {
            item.status = 'success'
          }
          toolIndexes.set(getToolKey({ toolName: tool.toolName, target: item.target || tool.target }), existingIndex)
          return
        }

        const item: TraceDisplayItem = {
          id: `${event.timestamp}-${event.phase}-${index}`,
          kind: 'tool',
          phase: event.phase,
          timestamp: event.timestamp,
          title: operationTitle(tool.toolName),
          detail: tool.target ? `目标：${tool.target}` : getCompactEventContent(event),
          status: eventStatus(event),
          toolName: tool.toolName,
          target: tool.target,
          executionCount: executionHit ? 1 : 0,
          events: [event],
        }
        toolIndexes.set(key, items.length)
        items.push(item)
        return
      }

      if (event.phase === 'approval_request' || event.phase === 'approval_response') {
        const command = typeof event.metadata?.command === 'string' ? event.metadata.command : ''
        const parsed = parseOperationCommand(command)
        const title = parsed ? `审批：${operationTitle(parsed.toolName)}` : '审批确认'
        const detail = parsed ? operationTarget(parsed.args) : getCompactEventContent(event)
        if (approvalIndex >= 0) {
          const item = items[approvalIndex]
          item.events.push(event)
          item.status = mergeStatus(item.status, eventStatus(event))
          return
        }
        approvalIndex = items.length
        items.push({
          id: `${event.timestamp}-${event.phase}-${index}`,
          kind: 'approval',
          phase: event.phase,
          timestamp: event.timestamp,
          title,
          detail,
          status: eventStatus(event),
          events: [event],
        })
        return
      }

      if (isAggregatedPhase(event.phase)) {
        const existingIndex = phaseIndexes.get(event.phase)
        if (existingIndex !== undefined) {
          const item = items[existingIndex]
          item.events.push(event)
          item.status = mergeStatus(item.status, eventStatus(event))
          if (shouldPreferPhaseEvent(item.events[0], event)) {
            item.timestamp = event.timestamp
          }
          item.detail = phaseDetail(event.phase, item.events)
          return
        }
        phaseIndexes.set(event.phase, items.length)
        items.push({
          id: `${event.timestamp}-${event.phase}-${index}`,
          kind: phaseKind(event.phase),
          phase: event.phase,
          timestamp: event.timestamp,
          title: phaseTitle(event.phase),
          detail: phaseDetail(event.phase, [event]),
          status: eventStatus(event),
          events: [event],
        })
        return
      }

      items.push({
        id: `${event.timestamp}-${event.phase}-${index}`,
        kind: phaseKind(event.phase),
        phase: event.phase,
        timestamp: event.timestamp,
        title: getPhaseLabel(event.phase),
        detail: getCompactEventContent(event),
        status: eventStatus(event),
        events: [event],
      })
    })

    return items
  }

  const getItemIcon = (item: TraceDisplayItem) => {
    const iconStyle = { fontSize: 14 }
    if (item.status === 'blocked') return <StopOutlined style={{ ...iconStyle, color: 'var(--accent-red)' }} />
    if (item.status === 'failure') return <CloseCircleOutlined style={{ ...iconStyle, color: 'var(--accent-red)' }} />
    if (item.status === 'warning') return <CheckCircleOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
    if (item.status === 'rejected') return <CloseCircleOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
    if (item.phase === 'knowledge_retrieval') return <SearchOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
    if (item.phase === 'context_management') return <ApartmentOutlined style={{ ...iconStyle, color: 'var(--accent-purple)' }} />
    if (item.phase === 'knowledge_save') return <DatabaseOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
    if (item.kind === 'tool') return <ToolOutlined style={{ ...iconStyle, color: 'var(--accent-purple)' }} />
    if (item.kind === 'approval') return <SafetyOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
    if (item.kind === 'planning') return <BulbOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
    if (item.kind === 'response') return <MessageOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
    if (item.kind === 'verification') return <CheckCircleOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
    if (item.kind === 'guard') return <SafetyOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
    return <CheckCircleOutlined style={iconStyle} />
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
    if (isApprovalRejectedEvent(event)) return 'orange'
    return getEventColor(event.event_type)
  }

  const getItemColor = (item: TraceDisplayItem): string => {
    if (item.status === 'failure' || item.status === 'blocked') return 'red'
    if (item.status === 'warning' || item.status === 'rejected' || item.status === 'skipped') return 'orange'
    if (item.status === 'success') return 'green'
    if (item.kind === 'tool') return 'purple'
    if (item.kind === 'approval') return 'orange'
    return 'blue'
  }

  const getItemLabel = (item: TraceDisplayItem) => {
    if (item.kind === 'tool') return '工具执行'
    if (item.kind === 'approval') return '审批'
    if (item.kind === 'planning') return '规划'
    if (item.kind === 'verification') return '验证'
    if (item.kind === 'response') return '回复'
    if (item.kind === 'guard') return '安全'
    return getPhaseLabel(item.phase)
  }

  const getItemStatusLabel = (item: TraceDisplayItem) => {
    if (item.status === 'failure') return '失败'
    if (item.status === 'warning') return '有警告'
    if (item.status === 'blocked') return '已拦截'
    if (item.status === 'rejected') return '已拒绝'
    if (item.status === 'skipped') return '已跳过'
    if (item.status === 'success') return item.kind === 'tool' ? '已执行' : '已完成'
    return '进行中'
  }

  const getPhaseLabel = (phase: string) => {
    const labels: Record<string, string> = {
      input_received: '接收指令',
      safety_check: '安全校验',
      knowledge_retrieval: '知识检索',
      image_recognition: '图片识别',
      voice_recognition: '语音识别',
      multimodal_recognition: '多模态识别',
      context_management: '上下文管理',
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

  const getCompactEventContent = (event: TraceEvidence) => {
    const content = event.content || ''
    const planned = content.match(/(?:准备调用工具：|调用工具:\s*)([\w_]+)\s*(?:\n参数：|\()?(.*)?$/s)
    if (planned) {
      const toolName = planned[1]
      const rawArgs = (planned[2] || '').replace(/\)$/, '').trim()
      let target = ''
      try {
        const args = rawArgs ? JSON.parse(rawArgs) as Record<string, unknown> : {}
        target = operationTarget(args)
      } catch {
        target = ''
      }
      return target ? `${operationTitle(toolName)}：${target}` : operationTitle(toolName)
    }

    const directCommand = parseOperationCommand(content)
    if (directCommand) {
      const target = operationTarget(directCommand.args)
      return target ? `${operationTitle(directCommand.toolName)}：${target}` : operationTitle(directCommand.toolName)
    }

    const localized = getDisplayContent(event).replace(/\n参数：.*$/s, '')
    return localized.length > 120 ? `${localized.slice(0, 120)}...` : localized
  }

  const getGroupStatusColor = (status: TraceGroup['status']) => {
    switch (status) {
      case 'success': return 'green'
      case 'warning': return 'orange'
      case 'failure': return 'red'
      case 'blocked': return 'red'
      case 'corrected': return 'orange'
      case 'rejected': return 'orange'
      default: return 'blue'
    }
  }

  const getGroupStatusLabel = (status: TraceGroup['status']) => {
    switch (status) {
      case 'success': return '已完成'
      case 'warning': return '有警告'
      case 'failure': return '失败'
      case 'blocked': return '已拦截'
      case 'corrected': return '已修正'
      case 'rejected': return '已拒绝'
      default: return '进行中'
    }
  }

  const compactGroupTitle = (title: string) => {
    const text = title.replace(/\s+/g, ' ').trim()
    if (!text) return '当前请求'
    return text.length > 34 ? `${text.slice(0, 34)}...` : text
  }

  const renderRawEventDetail = (event: TraceEvidence, index: number) => (
    <div
      key={`${event.timestamp}-${event.phase}-${index}`}
      style={{
        padding: '8px 0',
        borderTop: index > 0 ? '1px solid var(--border-color)' : 'none',
      }}
    >
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
        <Tag color={getTraceEventColor(event)} style={{ fontSize: 10, margin: 0 }}>
          {getDetailPhaseLabel(event)}
        </Tag>
        <Text style={{ fontSize: 10, color: 'var(--text-muted)' }}>
          {new Date(event.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </Text>
      </div>
      <Text
        style={{
          display: 'block',
          fontSize: 11,
          color: 'var(--text-secondary)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {getDetailContent(event)}
      </Text>
      {renderEvidence(event)}
    </div>
  )

  const detailEventsForItem = (item: TraceDisplayItem) => {
    const filtered = item.events.filter((event) => !isCoarseAuditDuplicate(event))
    return filtered.length ? filtered : item.events
  }

  const renderTraceItem = (item: TraceDisplayItem, index: number, total: number) => {
    const detailEvents = detailEventsForItem(item)
    return (
    <div
      key={item.id}
      style={{
        display: 'flex',
        gap: 10,
        marginBottom: 12,
        paddingBottom: 12,
        borderBottom: index < total - 1 ? '1px solid var(--border-color)' : 'none',
      }}
    >
      <div style={{ paddingTop: 2 }}>
        {getItemIcon(item)}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, flexWrap: 'wrap' }}>
          <Tag color={getItemColor(item)} style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', margin: 0 }}>
            {getItemLabel(item)}
          </Tag>
          <Tag color={getItemColor(item)} style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', margin: 0 }}>
            {getItemStatusLabel(item)}
          </Tag>
          {(item.executionCount || 0) > 1 && (
            <Tag color="orange" style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', margin: 0 }}>
              执行 {item.executionCount} 次
            </Tag>
          )}
          <Text style={{ fontSize: 10, color: 'var(--text-muted)' }}>
            {new Date(item.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </Text>
        </div>
        <Text style={{ display: 'block', fontSize: 12, color: 'var(--text-primary)', marginBottom: 2 }}>
          {item.title}
        </Text>
        {item.detail && (
          <Text style={{ display: 'block', fontSize: 11, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>
            {item.detail}
          </Text>
        )}
        <Collapse
          ghost
          size="small"
          className="trace-detail-collapse"
          items={[{
            key: 'trace-detail',
            label: `详细事件（${detailEvents.length}）`,
            children: detailEvents.map(renderRawEventDetail),
          }]}
        />
        {item.kind === 'tool' && item.toolName && (
          <Button
            size="small"
            type="link"
            icon={<BulbOutlined />}
            style={{ padding: 0, marginTop: 2, fontSize: 11, height: 'auto' }}
            onClick={() => sendMessage(`请解释这个操作的含义和作用：${item.title}${item.target ? `，目标：${item.target}` : ''}`)}
          >
            解释
          </Button>
        )}
      </div>
    </div>
    )
  }

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
            const displayItems = buildDisplayItems(group.events)
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
                    {displayItems.length} 个步骤
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
                  {displayItems.map((item, index) => renderTraceItem(item, index, displayItems.length))}
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
