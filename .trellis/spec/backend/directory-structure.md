# Directory Structure

> How backend code is organized in OpsGuard.

---

## Directory Layout

```
backend/
├── app/
│   ├── __init__.py          # Package init, version
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Config loading from YAML
│   ├── database.py          # SQLite init and connection helpers
│   ├── agent/               # LangGraph Agent engine
│   │   ├── graph.py         # Main workflow (Plan→Approve→Execute→Verify)
│   │   ├── llm.py           # LLM client abstraction (LiteLLM)
│   │   └── tools_registry.py # Tool registration and execution
│   ├── api/                 # REST API endpoints
│   │   ├── routes.py        # Router aggregation
│   │   ├── sessions.py      # Session CRUD + messages
│   │   ├── system.py        # System status metrics
│   │   ├── knowledge.py     # Knowledge base queries
│   │   ├── health_report.py # Health check report
│   │   ├── health_report_pdf.py # PDF export
│   │   ├── topology.py      # Fault correlation graph
│   │   └── security_demo.py # Red team testing
│   ├── mcp_tools/           # MCP tool implementations
│   │   ├── process_tools.py
│   │   ├── disk_tools.py
│   │   ├── network_tools.py
│   │   ├── log_tools.py
│   │   ├── service_tools.py
│   │   ├── config_tools.py
│   │   ├── system_tools.py
│   │   └── backup.py        # Backup/rollback manager
│   ├── safety/              # Security guardrail
│   │   ├── rule_engine.py   # Layer 1: regex rules
│   │   ├── classifier.py    # Layer 2: BERT ONNX
│   │   └── guardrail.py     # Unified entry point
│   ├── knowledge/           # Knowledge store
│   │   └── store.py
│   ├── audit/               # Audit logging
│   │   └── logger.py
│   └── websocket/           # WebSocket communication
│       ├── gateway.py       # Connection handler
│       ├── manager.py       # Connection pool
│       └── approval.py      # Approval wait mechanism
├── config.yaml              # Runtime config (not in git)
├── config.yaml.example      # Config template
├── requirements.txt
└── data/                    # SQLite databases (not in git)
```

---

## Module Organization

- **One module per concern**: Each subdirectory handles one domain
- **No circular imports**: Modules only import from `config`, `database`, or their own submodules
- **Tools are stateless functions**: MCP tools are pure functions returning `ToolResult`

---

## Naming Conventions

- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- API routes: kebab-case in URLs (`/health-report/export-pdf`)
