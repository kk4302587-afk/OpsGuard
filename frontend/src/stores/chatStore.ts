import { create } from 'zustand'

interface Message {
  id: string
  role: 'user' | 'assistant' | 'progress'
  content: string
  timestamp: string
  toolCalls?: ToolCall[]
  progressSteps?: ProgressStep[]
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
  evidence_type?: 'command' | 'log' | 'config' | 'metric' | 'topology' | 'knowledge' | 'user input'
  source?: string
  observed?: string
  confidence?: 'low' | 'medium' | 'high'
  execution_state?: 'executed' | 'inferred' | 'skipped' | 'failed'
  failure_reason?: string
  next_check?: string
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
  sendMessage: (content: string) => void

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
    last_failure_reason?: string | null
    original_message: string
  } | null
  acceptRunbookSuggestion: () => void
  dismissRunbookSuggestion: () => void
  runRunbookDirectly: (runbookId: string) => void

  // WebSocket
  ws: WebSocket | null
  connectWebSocket: (sessionId: string) => void
  disconnectWebSocket: () => void
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
  recent_changes: 1,
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
    nextSteps[stepIdx] = { ...nextSteps[stepIdx], status: 'process', description: event.content }
  } else if (event.event_type === 'success') {
    nextSteps[stepIdx] = { ...nextSteps[stepIdx], status: 'finish', description: event.content }
  } else if (event.event_type === 'failure' || event.event_type === 'blocked') {
    nextSteps[stepIdx] = { ...nextSteps[stepIdx], status: 'error', description: event.content }
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
  id: 'progress-' + crypto.randomUUID(),
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
].join('\u0000')

const mergeTraceEvents = (existing: TraceEvent[], incoming: TraceEvent[]): TraceEvent[] => {
  const seen = new Set<string>()
  return [...existing, ...incoming]
    .filter((event) => {
      const key = traceKey(event)
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''))
}

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

  runRunbookDirectly: (runbookId: string) => {
    const { ws } = get()
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.error('Cannot run runbook: WebSocket not connected')
      return
    }
    ws.send(JSON.stringify({ type: 'run_runbook', runbook_id: runbookId }))
    set({ isThinking: true })
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
    set({ activeSessionId: id, messages: [], traceEvents: [], isThinking: false })
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
            const traceEvents = mergeTraceEvents(state.traceEvents, data.trace)
            const messages = state.messages.map((msg) => (
              msg.role === 'progress'
                ? { ...msg, progressSteps: buildProgressStepsFromTrace(traceEvents) }
                : msg
            ))
            return { traceEvents, messages }
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

  sendMessage: (content: string) => {
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
              id: crypto.randomUUID(),
              role: 'user',
              content,
              timestamp: new Date().toISOString(),
            }
            set((state) => ({
              messages: [...state.messages, userMessage],
              isThinking: true,
            }))
            newWs.send(JSON.stringify({ type: 'message', content }))
          }
        }, 500)
      })
      return
    }

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
    }

    set((state) => ({
      messages: [...state.messages, userMessage],
      isThinking: true,
    }))

    ws.send(JSON.stringify({ type: 'message', content }))
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
                  id: 'progress-' + crypto.randomUUID(),
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
                id: data.message_id || crypto.randomUUID(),
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
            const newTraceEvents = [
              ...state.traceEvents,
              {
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
              },
            ]

            // Update progress message steps based on trace phase
            const updatedMessages = state.messages.map(msg => {
              if (msg.role !== 'progress' || !msg.progressSteps) return msg

              const steps = [...msg.progressSteps]
              const phaseToStep: Record<string, number> = {
                'safety_check': 0,
                'knowledge_retrieval': 1,
                'recent_changes': 1,
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
                  steps[stepIdx] = { ...steps[stepIdx], status: 'process', description: data.content }
                } else if (data.event_type === 'success') {
                  steps[stepIdx] = { ...steps[stepIdx], status: 'finish', description: data.content }
                } else if (data.event_type === 'failure' || data.event_type === 'blocked') {
                  steps[stepIdx] = { ...steps[stepIdx], status: 'error', description: data.content }
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
            },
            messages: [
              ...state.messages,
              {
                id: crypto.randomUUID(),
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
              last_failure_reason: data.last_failure_reason || null,
              original_message: data.original_message || '',
            },
            messages: [
              ...state.messages.filter((m) => m.role !== 'progress'),
              {
                id: crypto.randomUUID(),
                role: 'assistant',
                content: `[Runbook建议] ${data.name}\n步骤数: ${data.step_count}\n版本: v${data.version || 1}\n健康: ${data.staleness_status || 'fresh'}\n成功/失败: ${data.success_count || 0}/${data.failure_count || 0}\n相似度: ${Math.round((data.match_ratio || 0) * 100)}%${data.last_failure_reason ? '\n最近失败: ' + data.last_failure_reason : ''}\n${data.description || ''}`,
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
                id: crypto.randomUUID(),
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
