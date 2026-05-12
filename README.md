# OpsGuard - 智能运维 Agent

> 基于 MCP 协议的智能运维 Agent，通过自然语言与 Linux 操作系统交互，配备三层安全护栏。

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│                   Frontend (React)                    │
│  Ant Design 5 + Ant Design X + ECharts               │
│  深色主题 · WebSocket 实时通信                         │
├─────────────────────────────────────────────────────┤
│                   Backend (FastAPI)                   │
│  WebSocket Gateway │ Safety Guardrail │ Agent Engine  │
│  MCP Server        │ Knowledge Store  │ Audit Logger  │
├─────────────────────────────────────────────────────┤
│              OS Layer (opsguard user)                 │
│  sudoers whitelist · Linux capabilities              │
└─────────────────────────────────────────────────────┘
```

## 核心特性

- **通用运维推理**：非模板匹配，真正理解问题并动态组合工具解决
- **三层安全护栏**：规则引擎 + BERT 分类器 + LLM 约束，纵深防御
- **MCP 协议工具化**：原子化 OS 操作工具，Agent 自由组合
- **推理链路溯源**：完整记录思考过程，前端可视化回放
- **Plan→Approve→Execute→Verify**：高危操作人类审批闭环
- **知识自动沉淀**：从历史问题中学习，持续提升诊断效率

## 快速开始

### 后端

```bash
cd backend
pip install -r requirements.txt
python -m app.main
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18 + TypeScript + Vite + Ant Design 5 + Ant Design X + ECharts |
| 后端 | Python 3.11 + FastAPI + LangGraph + FastMCP + LiteLLM |
| 通信 | WebSocket (流式输出 + 审批推送) |
| 存储 | SQLite (会话 + 知识库 + 审计日志) |
| 大模型 | Qwen3 (主) + DeepSeek (备)，OpenAI 兼容接口 |
| 安全 | 规则引擎 + BERT 分类器 + LLM 约束 + sudoers 白名单 |

## 项目结构

```
OpsGuard/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置管理
│   │   ├── database.py          # 数据库初始化
│   │   ├── api/                 # REST API
│   │   ├── agent/               # LangGraph Agent 编排
│   │   ├── mcp_tools/           # MCP 工具插件
│   │   ├── safety/              # 安全护栏
│   │   ├── knowledge/           # 知识库
│   │   ├── audit/               # 审计日志
│   │   └── websocket/           # WebSocket 网关
│   ├── config.yaml              # 配置文件
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/          # UI 组件
│   │   ├── stores/              # Zustand 状态管理
│   │   └── styles/              # 样式
│   └── package.json
└── README.md
```

## 部署环境

- 目标平台：LoongArch + 麒麟高级服务器版 V11
- 开发阶段：任意 Linux/macOS/Windows + 云端 LLM API
