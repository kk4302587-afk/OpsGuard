# Component Guidelines

> How components are built in OpsGuard frontend.

---

## Component Structure

```tsx
import { useState } from 'react'
import { Button, Typography } from 'antd'
import { SomeOutlined } from '@ant-design/icons'
import { useSomeStore } from '../stores/someStore'
import '../styles/some.css'  // optional, only if component has dedicated styles

const { Text } = Typography

interface ComponentProps {
  // props if any
}

/**
 * Brief description of what this component does.
 */
function ComponentName({ prop }: ComponentProps) {
  // hooks
  // handlers
  // render
  return (...)
}

export default ComponentName
```

---

## Conventions

- One component per file, named same as the component (`ChatPanel.tsx`)
- Function components only (no class components)
- Props interface defined above the component
- JSDoc comment describing purpose
- Default export (no named exports for components)

---

## Styling

- CSS variables from `global.css` for all colors/fonts
- Inline styles for one-off layout (flexbox, padding)
- Dedicated `.css` file for complex/reusable styles
- No CSS-in-JS libraries
- No Tailwind

---

## State

- Local state: `useState` for UI-only state (loading, input values)
- Global state: Zustand stores for shared data (sessions, messages, system status)
- No prop drilling beyond 2 levels — use store instead

---

## Icons

- Use `@ant-design/icons` exclusively
- No emoji in UI (use icons instead)
- Import only the icons you use (tree-shaking)

---

## Scenario: Copyable Operations Commands in Chat Markdown

### 1. Scope / Trigger
- Trigger: any change to `MarkdownRenderer`, assistant message rendering, or command/recommendation display.
- Goal: Chinese ops users must be able to copy executable Linux commands without selecting text manually.

### 2. Signatures
- Component: `MarkdownRenderer({ content }: { content: string })`
- Markdown contract from backend: copy-worthy commands should be emitted as fenced `bash` code blocks.

### 3. Contracts
- Fenced code blocks render a visible copy button in the top-right corner.
- Inline command-looking code may render a compact copy button as a fallback.
- Copy buttons use Chinese labels/tooltips (`复制命令`, `已复制`) and `@ant-design/icons`.
- Copying must not navigate, submit chat input, or mutate chat state.

### 4. Validation & Error Matrix
- Clipboard API succeeds -> show copied feedback briefly.
- Clipboard API unavailable/fails -> log the error; do not break rendering.
- Normal inline values like service names or file paths -> render as code without forcing command-copy UI unless they match command patterns.

### 5. Good/Base/Bad Cases
- Good: a response contains ` ```bash\nsystemctl status nginx\n``` ` and the block has one copy button.
- Base: inline ``nginx.service`` remains a plain code token.
- Bad: executable commands are only buried inside a paragraph with no copy affordance.

### 6. Tests Required
- Frontend build/type-check: `npm.cmd run build`.
- Manual UI check: command code block shows `复制`, click changes to `已复制`.

### 7. Wrong vs Correct
#### Wrong
```markdown
请执行 `systemctl status nginx` 和 `journalctl -u nginx -n 100 --no-pager`
```

#### Correct
````markdown
**可复制命令**
```bash
systemctl status nginx
journalctl -u nginx -n 100 --no-pager
```
````

## Scenario: Trace Panel Labels and Wrapping

### 1. Scope / Trigger
- Trigger: any change to `TracePanel` phase labels, trace text rendering, or backend trace phase names.
- Goal: right-side reasoning trace must be readable for Chinese operators.

### 2. Signatures
- Component: `TracePanel()`
- Trace event fields: `phase`, `event_type`, `content`, optional evidence fields.

### 3. Contracts
- Every user-visible phase label must be Chinese, including `recent_changes`.
- Trace content should preserve intentional newlines and use natural wrapping, not `break-all`.
- Long raw logs or JSON should be summarized by the backend before reaching the panel.

### 4. Validation & Error Matrix
- Known phase -> render Chinese label.
- Unknown phase -> render raw phase as fallback.
- Long path/log text -> wrap without splitting every character.

### 5. Good/Base/Bad Cases
- Good: `recent_changes` renders as `近期变更`.
- Base: a new unregistered phase still displays its raw name.
- Bad: English labels and large raw JSON blocks fill the panel.

### 6. Tests Required
- Frontend build/type-check: `npm.cmd run build`.
- Manual UI check with a recent-change trace event.

### 7. Wrong vs Correct
#### Wrong
```tsx
wordBreak: 'break-all'
```

#### Correct
```tsx
whiteSpace: 'pre-wrap',
wordBreak: 'break-word'
```
