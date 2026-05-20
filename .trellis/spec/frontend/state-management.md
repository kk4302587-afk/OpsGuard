# State Management

> How state is managed in OpsGuard frontend.

---

## Library

**Zustand** — lightweight, no boilerplate, TypeScript-native.

---

## Stores

| Store | File | Manages |
|-------|------|---------|
| `useChatStore` | `chatStore.ts` | Sessions, messages, WebSocket, approval, trace events |
| `useSystemStore` | `systemStore.ts` | System metrics (CPU/MEM/DISK), polling |

---

## When to Use Global State (Zustand)

- Data shared across multiple components
- WebSocket connection state
- Data that persists across page navigation
- Server-fetched data that multiple components read

## When to Use Local State (useState)

- Loading spinners
- Form input values
- UI toggle states (expanded/collapsed)
- Component-specific temporary data

---

## Patterns

### Async actions in stores
```typescript
export const useStore = create<Store>((set, get) => ({
  data: null,
  fetchData: async () => {
    const res = await fetch('/api/data')
    if (res.ok) {
      const data = await res.json()
      set({ data })
    }
  },
}))
```

### Accessing other state in actions
```typescript
sendMessage: (content) => {
  const { ws, activeSessionId } = get()
  // use ws and activeSessionId
}
```

### WebSocket message ownership
Chat WebSocket handlers must verify the incoming message still belongs to the
store's `activeSessionId` before mutating global chat state. Closing a socket
for a session switch should not imply the backend operation is cancelled; the
backend owns submitted operation lifecycle by session.

## Scenario: Session Switch During Running Chat Operation

### 1. Scope / Trigger
- Trigger: chat state combines WebSocket runtime events with REST-loaded session history.
- Applies when changing `activeSessionId`, reconnecting `/ws/{session_id}`, or loading `/api/sessions/{session_id}/messages` and `/api/sessions/{session_id}/trace`.

### 2. Signatures
- `GET /api/sessions/{session_id}/messages -> { messages: Message[] }`
- `GET /api/sessions/{session_id}/trace -> { trace: TraceEvent[] }`
- WebSocket `/ws/{session_id}` may emit `thinking`, `trace`, `approval_request`, `runbook_suggestion`, `response`, or `error`.

### 3. Contracts
- REST responses are historical snapshots and may arrive before or after WebSocket reconnect snapshots.
- Store updates from REST must check `get().activeSessionId === session_id` before mutating state.
- Historical messages must be merged with any runtime `progress` message while `isThinking` is true.
- Historical trace must be merged/deduplicated with live trace events, then can update the visible progress steps.

### 4. Validation & Error Matrix
- Stale REST response for inactive session -> ignore it.
- Messages arrive after WebSocket `thinking` -> keep the runtime progress message.
- Trace arrives after live trace events -> merge and dedupe; do not replace live events blindly.
- Backend operation still running after reconnect -> UI should show progress plus recovered trace, not only a bare spinner.

### 5. Good/Base/Bad Cases
- Good: send a message, switch to another session, switch back; the chat shows progress and the trace panel shows recovered events.
- Base: open a completed historical session; messages and trace load without a progress message.
- Bad: `set({ messages: data.messages })` while `isThinking` is true, because it removes the runtime progress card.

### 6. Tests Required
- Frontend type/build check: `npm.cmd run build`.
- Backend trace replay regression: `python backend/test_session_trace_replay.py`.
- WebSocket lifecycle regression: `python backend/test_websocket_lifecycle.py`.

### 7. Wrong vs Correct
#### Wrong
```typescript
fetch(`/api/sessions/${id}/messages`).then((data) => set({ messages: data.messages }))
fetch(`/api/sessions/${id}/trace`).then((data) => set({ traceEvents: data.trace }))
```

#### Correct
```typescript
if (get().activeSessionId !== id) return
set((state) => ({
  messages: mergeMessagesPreservingRuntime(
    data.messages,
    state.messages,
    state.isThinking,
    state.traceEvents,
  ),
}))
```

---

## Forbidden Patterns

- No Redux, no MobX, no Context API for global state
- No `useEffect` for data fetching that should be in a store
- No storing derived data (compute it in the component)
- No writing WebSocket messages from an inactive/stale session into the active
  chat state
