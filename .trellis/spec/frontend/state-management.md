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

---

## Forbidden Patterns

- No Redux, no MobX, no Context API for global state
- No `useEffect` for data fetching that should be in a store
- No storing derived data (compute it in the component)
- No writing WebSocket messages from an inactive/stale session into the active
  chat state
