import { create } from 'zustand'
import { localizeTraceContent } from '../utils/traceLocalization'

interface Message {
  id: string
  role: 'user' | 'assistant' | 'progress'
  content: string
  timestamp: string
  toolCalls?: ToolCall[]
  progressSteps?: ProgressStep[]
  attachments?: MessageAttachment[]
}

export interface MessageAttachment {
  id: string
  type: 'image' | 'audio'
  filename: string
  previewUrl?: string
}

interface ToolCall {
  tool: string
  args: Record<string, unknown>
  result?: string
}

interface ProgressStep {
  title: string
  status: 'wait' | 'process' | 'finish' | 'error'
  description?: string
}

interface TraceEvent {
  phase: string
  event_type: string
  content: string
  timestamp: string
  metadata?: Record<string, unknown>
  claim?: string
  evidence_type?: 'command' | 'log' | 'config' | 'metric' | 'topology' | 'knowledge' | 'user input' | 'alert' | 'dashboard_link'
  source?: string
  observed?: string
  confidence?: 'low' | 'medium' | 'high'
  execution_state?: 'executed' | 'inferred' | 'skipped' | 'failed'
  failure_reason?: string
  next_check?: string
}

export interface OperationPreview {
  status?: string
  preview_type?: string
  target?: string
  before_summary?: string
  after_summary?: string
  diff?: string
  warnings?: string[]
  limitations?: string[]
  metadata?: Record<string, unknown>
}

export interface ChangePlan {
  id?: string
  kind?: string
  tool_name?: string
  tool_display_name?: string
  description?: string
  target?: string
  risk_level?: string
  approval?: Record<string, unknown>
  steps?: Array<Record<string, unknown>>
  risks?: Array<Record<string, unknown>>
  rollback?: Record<string, unknown>
  validation?: Array<Record<string, unknown>>
  preview?: Record<string, unknown>
  policy?: Record<string, unknown>
  runbook?: Record<string, unknown> | null
}

const isApprovalTrace = (event: TraceEvent): boolean => (
  event.phase === 'approval_request'
  && event.event_type === 'pending'
  && event.metadata?.type === 'approval_request'
  && typeof event.metadata?.command === 'string'
)

const valueAsString = (value: unknown): string => (
  typeof value === 'string' ? value : ''
)

const approvalCommandFromTrace = (event: TraceEvent): string => {
  const metadataCommand = valueAsString(event.metadata?.command)
  return metadataCommand.trim()
}

const approvalMessageFromTrace = (event: TraceEvent): Message | null => {
  if (!isApprovalTrace(event)) return null
  const command = approvalCommandFromTrace(event)
  if (!command) return null

  const riskLevel = valueAsString(event.metadata?.risk_level)
  const impact = valueAsString(event.metadata?.impact)
  const description = valueAsString(event.metadata?.description)
  const detail = valueAsString(event.content)
  const lines = [`[需要确认] ${command}`]
  if (riskLevel) lines.push(`风险等级: ${riskLevel}`)
  if (description && description !== command) lines.push(`操作说明: ${description}`)
  if (impact) lines.push(`影响评估: ${impact}`)
  else if (detail && detail !== command && detail !== description) lines.push(`影响评估: ${detail}`)

  return {
    id: `approval-history-${event.timestamp || command}`,
    role: 'assistant',
    content: lines.join('\n'),
    timestamp: event.timestamp || new Date().toISOString(),
  }
}

const approvalCommandFromMessage = (message: Message): string => (
  message.content.startsWith('[需要确认] ')
    ? message.content.split('\n')[0].replace('[需要确认] ', '').trim()
    : ''
)

const mergeMessagesWithHistoricalApprovals = (
  messages: Message[],
  traceEvents: TraceEvent[],
): Message[] => {
  const existingApprovalCommands = new Set(
    messages.map(approvalCommandFromMessage).filter(Boolean),
  )
  const approvalMessages = traceEvents
    .map(approvalMessageFromTrace)
    .filter((message): message is Message => {
      if (!message) return false
      const command = approvalCommandFromMessage(message)
      if (!command || existingApprovalCommands.has(command)) return false
      existingApprovalCommands.add(command)
      return true
    })

  if (!approvalMessages.length) return messages
  return [...messages, ...approvalMessages]
    .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''))
}

export interface MultimodalRecognitionResult {
  input_type: 'image' | 'audio'
  summary?: string
  extracted_text?: string
  raw_transcript?: string
  normalized_transcript?: string
  entities?: Record<string, unknown>
  diagnosis_hints?: string[]
  recommended_tools?: Array<Record<string, unknown>>
  corrections?: Array<Record<string, unknown>>
  warnings?: string[]
  confidence?: 'low' | 'medium' | 'high'
  provider?: string
  model?: string
  attachment_id?: string
  file?: Record<string, unknown>
  attachment?: MessageAttachment & {
    url?: string
    content_type?: string
    size?: number
    sha256?: string
  }
  requires_write_confirmation?: boolean
  needs_user_confirmation?: boolean
}

interface Session {
  id: string
  title: string
  created_at: string
}

interface ChatStore {
  // Sessions
  sessions: Session[]
  activeSessionId: string | null
  createSession: () => Promise<void>
  setActiveSession: (id: string) => void
  deleteSession: (id: string) => Promise<void>
  fetchSessions: () => Promise<void>

  // Messages
  messages: Message[]
  inputValue: string
  isThinking: boolean
  setInputValue: (value: string) => void
  sendMessage: (
    content: string,
    multimodalContext?: MultimodalRecognitionResult[],
    attachments?: MessageAttachment[],
  ) => void

  // Trace
  traceEvents: TraceEvent[]

  // Approval
  pendingApproval: {
    request_id: string
    command: string
    risk_level: string
    description: string
    impact?: string
    rollback_strategy?: string
    supports_rollback?: boolean
    preview_strategy?: string
    preview?: OperationPreview
    change_plan?: ChangePlan
    policy?: Record<string, unknown>
    approval_level?: string
    execution_identity?: Record<string, unknown>
  } | null
  clearApproval: () => void

  // Runbook suggestion (proactive proposal before Agent runs)
  pendingRunbookSuggestion: {
    runbook_id: string
    name: string
    description: string
    step_count: number
    match_ratio: number
    version: number
    success_count: number
    failure_count: number
    success_rate?: number | null
    staleness_status: 'fresh' | 'warning' | 'stale'
    last_success?: string | null
    last_failure_reason?: string | null
    rollback_steps?: Array<Record<string, unknown>>
    original_message: string
    preflight?: {
      status?: 'applicable' | 'uncertain' | 'not_applicable'
      summary?: string
      extracted_variables?: Record<string, string>
      missing_variables?: string[]
      checks?: Array<Record<string, unknown>>
      requires_clarification?: boolean
      clarification_prompt?: string
      preconditions_summary?: {
        total?: number
        label?: string
        counts?: Record<string, number>
      }
      rollback_coverage?: {
        covered_steps?: number
        total_mutating_steps?: number
        has_explicit_rollback?: boolean
        label?: string
      }
    }
  } | null
  acceptRunbookSuggestion: () => void
  dismissRunbookSuggestion: () => void
  runRunbookDirectly: (runbookId: string, runbookName?: string) => void

  // WebSocket
  ws: WebSocket | null
  connectWebSocket: (sessionId: string) => void
  disconnectWebSocket: () => void
}

const createClientId = (prefix: string) => {
  if (globalThis.crypto?.randomUUID) {
    return `${prefix}-${globalThis.crypto.randomUUID()}`
  }

  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

const createDefaultProgressSteps = (): ProgressStep[] => [
  { title: '安全校验', status: 'process' },
  { title: '知识检索', status: 'wait' },
  { title: '推理分析', status: 'wait' },
  { title: '工具执行', status: 'wait' },
  { title: '结果验证', status: 'wait' },
]

const phaseToProgressStep: Record<string, number> = {
  safety_check: 0,
  knowledge_retrieval: 1,
  context_management: 2,
  planning: 2,
  tool_call: 3,
  execution: 3,
  approval_request: 3,
  approval_response: 3,
  verification: 4,
  response: 4,
  knowledge_save: 4,
}

const applyTraceToProgressSteps = (
  steps: ProgressStep[],
  event: Pick<TraceEvent, 'phase' | 'event_type' | 'content'>,
): ProgressStep[] => {
  const stepIdx = phaseToProgressStep[event.phase]
  if (stepIdx === undefined) return steps

  const nextSteps = [...steps]
  for (let i = 0; i < stepIdx; i++) {
    if (nextSteps[i].status !== 'error') {
      nextSteps[i] = { ...nextSteps[i], status: 'finish' }
    }
  }

  if (event.event_type === 'start' || event.event_type === 'pending') {
    nextSteps[stepIdx] = { ...nextSteps[stepIdx], status: 'process', description: localizeTraceContent(event.content) }
  } else if (event.event_type === 'success') {
    if (event.phase === 'response') {
      for (let i = 0; i < nextSteps.length; i++) {
        nextSteps[i] = { ...nextSteps[i], status: 'finish' }
      }
    }
    nextSteps[stepIdx] = { ...nextSteps[stepIdx], status: 'finish', description: localizeTraceContent(event.content) }
  } else if (event.event_type === 'failure' || event.event_type === 'blocked') {
    nextSteps[stepIdx] = { ...nextSteps[stepIdx], status: 'error', description: localizeTraceContent(event.content) }
  }

  return nextSteps
}

const buildProgressStepsFromTrace = (traceEvents: TraceEvent[]): ProgressStep[] => (
  traceEvents.reduce(
    (steps, event) => applyTraceToProgressSteps(steps, event),
    createDefaultProgressSteps(),
  )
)

const createProgressMessage = (traceEvents: TraceEvent[] = []): Message => ({
  id: createClientId('progress'),
  role: 'progress',
  content: '',
  timestamp: new Date().toISOString(),
  progressSteps: traceEvents.length > 0 ? buildProgressStepsFromTrace(traceEvents) : createDefaultProgressSteps(),
})

const mergeMessagesPreservingRuntime = (
  loadedMessages: Message[],
  currentMessages: Message[],
  isThinking: boolean,
  traceEvents: TraceEvent[],
): Message[] => {
  const runtimeProgress = currentMessages.find((message) => message.role === 'progress')
  if (!isThinking) return loadedMessages
  return [
    ...loadedMessages.filter((message) => message.role !== 'progress'),
    runtimeProgress || createProgressMessage(traceEvents),
  ]
}

const traceKey = (event: TraceEvent): string => [
  event.timestamp || '',
  event.phase || '',
  event.event_type || '',
  event.content || '',
  event.source || '',
].join('\u0000')

const getTraceTimestamp = (event: TraceEvent) => event.timestamp || ''

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null
)

const normalizeTraceEvent = (event: TraceEvent): TraceEvent => {
  const evidence = event.metadata?.evidence
  if (!isRecord(evidence)) return event
  return {
    ...event,
    claim: event.claim ?? evidence.claim as TraceEvent['claim'],
    evidence_type: event.evidence_type ?? evidence.evidence_type as TraceEvent['evidence_type'],
    source: event.source ?? evidence.source as TraceEvent['source'],
    observed: event.observed ?? evidence.observed as TraceEvent['observed'],
    confidence: event.confidence ?? evidence.confidence as TraceEvent['confidence'],
    execution_state: event.execution_state ?? evidence.execution_state as TraceEvent['execution_state'],
    failure_reason: event.failure_reason ?? evidence.failure_reason as TraceEvent['failure_reason'],
    next_check: event.next_check ?? evidence.next_check as TraceEvent['next_check'],
  }
}

const dedupeTraceEvents = (events: TraceEvent[]): TraceEvent[] => {
  const seen = new Set<string>()
  return events
    .map(normalizeTraceEvent)
    .filter((event) => event.phase !== 'recent_changes')
    .filter((event) => {
      const key = traceKey(event)
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
}

const mergeTraceEvents = (existing: TraceEvent[], incoming: TraceEvent[]): TraceEvent[] => (
  dedupeTraceEvents([...existing, ...incoming])
)

const mergeHistoricalTraceEvents = (existing: TraceEvent[], incoming: TraceEvent[]): TraceEvent[] => {
  const localInputByContent = new Map(
    existing
      .filter((event) => event.phase === 'input_received' && event.source === 'frontend')
      .map((event) => [event.content, event]),
  )

  return dedupeTraceEvents([
    ...existing.filter((event) => (
      !(event.phase === 'input_received' && event.source === 'frontend' && incoming.some((item) => (
        item.phase === 'input_received' && item.content === event.content
      )))
    )),
    ...incoming.map((event) => {
      if (event.phase !== 'input_received') return event
      const local = localInputByContent.get(event.content)
      if (!local) return event
      return { ...event, timestamp: local.timestamp }
    }),
  ])
    .sort((a, b) => getTraceTimestamp(a).localeCompare(getTraceTimestamp(b)))
}

const appendUserTurn = (
  state: Pick<ChatStore, 'messages' | 'traceEvents'>,
  userMessage: Message,
) => ({
  messages: [...state.messages, userMessage],
  traceEvents: state.traceEvents,
  isThinking: true,
})

export const useChatStore = create<ChatStore>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  inputValue: '',
  isThinking: false,
  traceEvents: [],
  pendingApproval: null,
  pendingRunbookSuggestion: null,
  ws: null,

  clearApproval: () => set({ pendingApproval: null }),

  acceptRunbookSuggestion: () => {
    const { ws, pendingRunbookSuggestion } = get()
    if (!pendingRunbookSuggestion || !ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({
      type: 'runbook_decision',
      decision: 'execute',
      runbook_id: pendingRunbookSuggestion.runbook_id,
    }))
    set({ pendingRunbookSuggestion: null, isThinking: true })
  },

  dismissRunbookSuggestion: () => {
    const { ws, pendingRunbookSuggestion } = get()
    if (!pendingRunbookSuggestion || !ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({
      type: 'runbook_decision',
      decision: 'dismiss',
      runbook_id: pendingRunbookSuggestion.runbook_id,
      original_message: pendingRunbookSuggestion.original_message,
    }))
    set({ pendingRunbookSuggestion: null, isThinking: true })
  },

  runRunbookDirectly: (runbookId: string, runbookName?: string) => {
    const { ws } = get()
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.error('Cannot run runbook: WebSocket not connected')
      return
    }
    const displayName = runbookName || runbookId
    const userMessage: Message = {
      id: createClientId('user'),
      role: 'user',
      content: `执行 Runbook「${displayName}」`,
      timestamp: new Date().toISOString(),
    }
    ws.send(JSON.stringify({ type: 'run_runbook', runbook_id: runbookId, runbook_name: runbookName }))
    set((state) => ({
      ...appendUserTurn(state, userMessage),
    }))
  },

  fetchSessions: async () => {
    try {
      const res = await fetch('/api/sessions/')
      if (res.ok) {
        const data = await res.json()
        set({ sessions: data.sessions || [] })
      }
    } catch (err) {
      console.error('Failed to fetch sessions:', err)
    }
  },

  createSession: async () => {
    try {
      const res = await fetch('/api/sessions/', { method: 'POST' })
      if (res.ok) {
        const session = await res.json()
        set((state) => ({
          sessions: [session, ...state.sessions],
          activeSessionId: session.id,
          messages: [],
          traceEvents: [],
        }))
        get().connectWebSocket(session.id)
      }
    } catch (err) {
      console.error('Failed to create session:', err)
    }
  },

  setActiveSession: (id: string) => {
    const { activeSessionId, disconnectWebSocket, connectWebSocket } = get()
    if (id === activeSessionId) return

    disconnectWebSocket()
    set({
      activeSessionId: id,
      messages: [],
      traceEvents: [],
      isThinking: false,
      pendingApproval: null,
      pendingRunbookSuggestion: null,
    })
    connectWebSocket(id)

    // Load messages for this session
    fetch(`/api/sessions/${id}/messages`)
      .then((res) => res.json())
      .then((data) => {
        if (get().activeSessionId !== id) return
        if (data.messages) {
          set((state) => ({
            messages: mergeMessagesPreservingRuntime(
              data.messages,
              state.messages,
              state.isThinking,
              state.traceEvents,
            ),
          }))
        }
      })
      .catch((err) => console.error('Failed to load messages:', err))

    // Load trace events for this session
    fetch(`/api/sessions/${id}/trace`)
      .then((res) => res.json())
      .then((data) => {
        if (get().activeSessionId !== id) return
        if (data.trace) {
          set((state) => {
            const traceEvents = mergeHistoricalTraceEvents(state.traceEvents, data.trace)
            const messages = state.messages.map((msg) => (
              msg.role === 'progress'
                ? { ...msg, progressSteps: buildProgressStepsFromTrace(traceEvents) }
                : msg
            ))
            return {
              traceEvents,
              messages: mergeMessagesWithHistoricalApprovals(messages, traceEvents),
            }
          })
        }
      })
      .catch((err) => console.error('Failed to load trace:', err))
  },

  deleteSession: async (id: string) => {
    try {
      await fetch(`/api/sessions/${id}`, { method: 'DELETE' })
      set((state) => ({
        sessions: state.sessions.filter((s) => s.id !== id),
        activeSessionId: state.activeSessionId === id ? null : state.activeSessionId,
        messages: state.activeSessionId === id ? [] : state.messages,
      }))
    } catch (err) {
      console.error('Failed to delete session:', err)
    }
  },

  setInputValue: (value: string) => set({ inputValue: value }),

  sendMessage: (
    content: string,
    multimodalContext: MultimodalRecognitionResult[] = [],
    attachments: MessageAttachment[] = [],
  ) => {
    const { ws, activeSessionId, createSession } = get()

    // Auto-create session if none exists
    if (!activeSessionId || !ws || ws.readyState !== WebSocket.OPEN) {
      // Create session first, then send message after connection
      createSession().then(() => {
        // Wait a bit for WebSocket to connect
        setTimeout(() => {
          const { ws: newWs } = get()
          if (newWs && newWs.readyState === WebSocket.OPEN) {
            const userMessage: Message = {
              id: createClientId('user'),
              role: 'user',
              content,
              timestamp: new Date().toISOString(),
              attachments,
            }
            set((state) => ({
              ...appendUserTurn(state, userMessage),
            }))
            newWs.send(JSON.stringify({ type: 'message', content, multimodal_context: multimodalContext, attachments }))
          }
        }, 500)
      })
      return
    }

    const userMessage: Message = {
      id: createClientId('user'),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
      attachments,
    }

    set((state) => ({
      ...appendUserTurn(state, userMessage),
    }))

    ws.send(JSON.stringify({ type: 'message', content, multimodal_context: multimodalContext, attachments }))
  },

  connectWebSocket: (sessionId: string) => {
    const existingWs = get().ws
    if (existingWs && existingWs.readyState !== WebSocket.CLOSED && existingWs.readyState !== WebSocket.CLOSING) {
      existingWs.close()
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/${sessionId}`
    const ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      console.log(`WebSocket connected: ${sessionId}`)
    }

    ws.onmessage = (event) => {
      if (get().activeSessionId !== sessionId) return

      const data = JSON.parse(event.data)

      switch (data.type) {
        case 'thinking':
          set((state) => {
            // Insert a progress message if not already present
            const hasProgress = state.messages.some(m => m.role === 'progress')
            if (hasProgress) return { isThinking: true }
            return {
              isThinking: true,
              messages: [
                ...state.messages,
                {
                  id: createClientId('progress'),
                  role: 'progress' as const,
                  content: '',
                  timestamp: new Date().toISOString(),
                  progressSteps: [
                    { title: '安全校验', status: 'process' as const },
                    { title: '知识检索', status: 'wait' as const },
                    { title: '推理分析', status: 'wait' as const },
                    { title: '工具执行', status: 'wait' as const },
                    { title: '结果验证', status: 'wait' as const },
                  ],
                },
              ],
            }
          })
          break

        case 'response':
          set((state) => ({
            // Remove progress message and add the real response
            messages: [
              ...state.messages.filter(m => m.role !== 'progress'),
              {
                id: data.message_id || createClientId('assistant'),
                role: 'assistant' as const,
                content: data.content,
                timestamp: data.timestamp || new Date().toISOString(),
              },
            ],
            isThinking: false,
          }))
          break

        case 'trace':
          set((state) => {
            // Update trace events panel
            const newTraceEvents = mergeTraceEvents(
              state.traceEvents,
              [{
                phase: data.phase,
                event_type: data.event_type,
                content: data.content,
                timestamp: data.timestamp || new Date().toISOString(),
                metadata: data.metadata,
                claim: data.claim,
                evidence_type: data.evidence_type,
                source: data.source,
                observed: data.observed,
                confidence: data.confidence,
                execution_state: data.execution_state,
                failure_reason: data.failure_reason,
                next_check: data.next_check,
              }],
            )

            // Update progress message steps based on trace phase
            const updatedMessages = state.messages.map(msg => {
              if (msg.role !== 'progress' || !msg.progressSteps) return msg

              const steps = [...msg.progressSteps]
              const phaseToStep: Record<string, number> = {
                'safety_check': 0,
                'knowledge_retrieval': 1,
                'planning': 2,
                'tool_call': 3,
                'execution': 3,
                'approval_request': 3,
                'approval_response': 3,
                'verification': 4,
                'response': 4,
                'knowledge_save': 4,
              }

              const stepIdx = phaseToStep[data.phase]
              if (stepIdx !== undefined) {
                // Mark previous steps as finished
                for (let i = 0; i < stepIdx; i++) {
                  if (steps[i].status !== 'error') steps[i] = { ...steps[i], status: 'finish' }
                }
                // Update current step
                if (data.event_type === 'start' || data.event_type === 'pending') {
                  steps[stepIdx] = { ...steps[stepIdx], status: 'process', description: localizeTraceContent(data.content) }
                } else if (data.event_type === 'success') {
                  if (data.phase === 'response') {
                    for (let i = 0; i < steps.length; i++) {
                      steps[i] = { ...steps[i], status: 'finish' }
                    }
                  }
                  steps[stepIdx] = { ...steps[stepIdx], status: 'finish', description: localizeTraceContent(data.content) }
                } else if (data.event_type === 'failure' || data.event_type === 'blocked') {
                  steps[stepIdx] = { ...steps[stepIdx], status: 'error', description: localizeTraceContent(data.content) }
                }
              }

              return { ...msg, progressSteps: steps }
            })

            return { traceEvents: newTraceEvents, messages: updatedMessages }
          })
          break

        case 'approval_request':
          set((state) => ({
            pendingApproval: {
              request_id: data.request_id,
              command: data.command,
              risk_level: data.risk_level,
              description: data.description || '',
              impact: data.impact || undefined,
              rollback_strategy: data.rollback_strategy,
              supports_rollback: data.supports_rollback,
              preview_strategy: data.preview_strategy,
              preview: isRecord(data.preview) ? data.preview : undefined,
              change_plan: isRecord(data.change_plan) ? data.change_plan : undefined,
              policy: isRecord(data.policy) ? data.policy : undefined,
              approval_level: typeof data.approval_level === 'string' ? data.approval_level : undefined,
              execution_identity: isRecord(data.execution_identity) ? data.execution_identity : undefined,
            },
            messages: [
              ...state.messages,
              {
                id: createClientId('approval'),
                role: 'assistant',
                content: `[需要确认] ${data.command}\n风险等级: ${data.risk_level}${data.impact ? '\n影响评估: ' + data.impact : ''}`,
                timestamp: new Date().toISOString(),
              },
            ],
            isThinking: false,
          }))
          break

        case 'runbook_suggestion':
          // Server matched a saved Runbook for the user's message.
          // Show an inline card; user picks execute or dismiss.
          set((state) => ({
            pendingRunbookSuggestion: {
              runbook_id: data.runbook_id,
              name: data.name,
              description: data.description || '',
              step_count: data.step_count || 0,
              match_ratio: data.match_ratio || 0,
              version: data.version || 1,
              success_count: data.success_count || 0,
              failure_count: data.failure_count || 0,
              success_rate: data.success_rate,
              staleness_status: data.staleness_status || 'fresh',
              last_success: data.last_success || null,
              last_failure_reason: data.last_failure_reason || null,
              rollback_steps: data.rollback_steps || [],
              original_message: data.original_message || '',
              preflight: data.preflight || undefined,
            },
            messages: [
              ...state.messages.filter((m) => m.role !== 'progress'),
              {
                id: createClientId('runbook'),
                role: 'assistant',
                content: `[Runbook建议] ${data.name}\n步骤数: ${data.step_count}\n修订版本: v${data.version || 1}\nRunbook状态: ${data.staleness_status || 'fresh'}\n预检结论: ${data.preflight?.status || 'uncertain'}${data.preflight?.summary ? '\n预检说明: ' + data.preflight.summary : ''}\n历史执行: 成功 ${data.success_count || 0} / 失败 ${data.failure_count || 0}\n匹配度: ${Math.round((data.match_ratio || 0) * 100)}%${data.last_success ? '\n最后成功: ' + data.last_success : ''}${data.preflight?.preconditions_summary?.label ? '\n预检条件: ' + data.preflight.preconditions_summary.label : ''}${data.preflight?.rollback_coverage?.label ? '\n回滚覆盖: ' + data.preflight.rollback_coverage.label : ''}${data.last_failure_reason ? '\n最近失败: ' + data.last_failure_reason : ''}\n${data.description || ''}`,
                timestamp: new Date().toISOString(),
              },
            ],
            isThinking: false,
          }))
          break

        case 'error':
          set((state) => ({
            messages: [
              ...state.messages,
              {
                id: createClientId('error'),
                role: 'assistant',
                content: `[错误] ${data.content}`,
                timestamp: new Date().toISOString(),
              },
            ],
            isThinking: false,
          }))
          break
      }
    }

    ws.onclose = () => {
      console.log(`WebSocket disconnected: ${sessionId}`)
      if (get().ws === ws) {
        set({ ws: null })
      }
    }

    ws.onerror = (err) => {
      console.error('WebSocket error:', err)
    }

    set({ ws })
  },

  disconnectWebSocket: () => {
    const { ws } = get()
    if (ws) {
      ws.close()
      if (get().ws === ws) {
        set({ ws: null })
      }
    }
  },
}))
