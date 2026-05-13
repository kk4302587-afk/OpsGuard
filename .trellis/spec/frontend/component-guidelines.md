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
