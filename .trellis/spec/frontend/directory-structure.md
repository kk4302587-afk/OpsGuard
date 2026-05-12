# Directory Structure

> How frontend code is organized in OpsGuard.

---

## Directory Layout

```
frontend/src/
├── main.tsx              # Entry point, ConfigProvider with dark theme
├── App.tsx               # Root layout, page routing, nav rail
├── components/           # All UI components (flat, no nesting)
│   ├── ChatPanel.tsx     # Main chat conversation
│   ├── Sidebar.tsx       # Session list
│   ├── TracePanel.tsx    # Reasoning trace visualization
│   ├── StatusBar.tsx     # Top system metrics bar
│   ├── ApprovalModal.tsx # High-risk operation approval dialog
│   ├── DiagnosisProgress.tsx # Inline diagnosis steps
│   ├── TopologyGraph.tsx # ECharts fault correlation graph
│   ├── HealthReport.tsx  # Health check report page
│   ├── SecurityDemo.tsx  # Red team testing page
│   └── KnowledgePanel.tsx # Knowledge base viewer
├── stores/               # Zustand state management
│   ├── chatStore.ts      # Chat, sessions, WebSocket, approval
│   └── systemStore.ts    # System metrics polling
└── styles/               # CSS files
    ├── global.css        # CSS variables, reset, scrollbar
    ├── layout.css        # App layout, nav rail, panels
    └── chat.css          # Chat-specific styles
```

---

## Naming Conventions

- Components: `PascalCase.tsx` (one component per file)
- Stores: `camelCaseStore.ts`
- Styles: `kebab-case.css`
- No `index.ts` barrel files (direct imports)
