# OpsGuard — 智能运维 Agent

## Goal

开发一套部署于 Linux 操作系统的智能运维 Agent（OpsGuard），通过 MCP 协议赋予大模型感知系统实时状态、采集运维指标及执行管理任务的能力。核心挑战是解决 AI 推理的不可控性，设计"安全护栏"架构，实现高效运维的同时杜绝误操作风险。

## Requirements

### 核心设计理念
* **通用问题解决能力优先**：Agent 不是"场景匹配器"，而是具备通用 Linux 运维推理能力的智能体
* 工具层提供原子化的 OS 操作能力，Agent 自主组合工具链来解决任意问题
* 不预设"场景→流程"的映射，让 LLM 根据实时环境感知结果动态推理下一步
* Demo 场景只是展示用例，不是能力边界

### 基础功能
* OS 环境深度感知（进程/网络/磁盘/日志/配置/内核参数/服务状态/用户会话等）
* MCP 运维插件化（原子化工具 + 少量信息聚合复合工具，Agent 自由组合调用）
* 安全意图校验器（三层纵深：规则引擎 + BERT 分类器 + LLM 安全约束）
* 最小权限代理执行（opsguard 专用用户 + sudoers 白名单提权）
* 推理链路溯源（完整闭环日志 + 前端 ThoughtChain 可视化回放）
* 智能化根因分析（基于实时数据推理，非模板匹配）
* 自然语言交互（支持模糊描述、多轮对话、主动追问澄清）
* 操作建议模式（dry-run）：Agent 先展示计划，用户确认后才执行
* 操作回滚能力：写操作前自动备份，失败可回滚
* 历史经验学习（从已解决问题中积累知识，提升后续诊断效率）

### 创新功能
* 执行后验证（Post-Action Verify）：执行完命令后自动验证结果是否符合预期
* 故障关联图谱：诊断过程中动态构建"进程→端口→服务→日志→配置"关联图，前端可视化
* 安全攻防演示模式：内置红队测试页面，评委可尝试注入攻击，实时展示三层防御拦截过程
* 运维知识自动沉淀：成功解决问题后自动提炼"问题特征→诊断路径→解决方案"存入知识库
* 健康巡检报告：一键生成系统健康报告（Markdown/PDF），含指标、风险、优化建议
* 操作影响预评估：高危操作前预估影响范围（如文件被哪些服务引用）
* 多轮诊断进度可视化：复杂问题诊断时展示排除/验证进度

### Demo 场景（展示用，非能力边界）
1. 磁盘空间清理（赛题原文示例）
2. 僵尸进程堆积
3. 系统日志异常分析
4. 配置文件漂移检测
5. 网络连接异常
6. 高负载根因定位
7. 服务启动失败诊断
8. 定时任务异常排查
9. 文件权限问题修复
10. 内存泄漏初步定位

### 非功能需求
* 确定性与可靠性：未授权不修改关键配置
* 抗注入能力：识别并拒绝 Prompt Injection
* 可审计性：所有操作可追溯
* 自适应降级：低配环境自动退回轻量安全模式

## Acceptance Criteria

* [ ] 用户输入自然语言指令，Agent 能正确理解意图并执行
* [ ] 高危操作被安全护栏拦截并请求确认，确认后才执行
* [ ] 推理链路完整记录并可在前端 ThoughtChain 回放
* [ ] 10 个 Demo 场景均可正常演示
* [ ] Prompt Injection 攻击被识别并拒绝（安全攻防演示模式可验证）
* [ ] 所有写操作在 opsguard 受限用户下执行
* [ ] 执行后自动验证结果符合预期
* [ ] 故障关联图谱可在前端可视化展示
* [ ] 健康巡检报告可一键生成
* [ ] 知识库能积累历史经验并在后续诊断中引用

## Definition of Done

* 前后端功能完整，可演示
* 安全护栏通过基本攻防测试
* 推理链路日志完整可查
* 代码结构清晰，有基本文档
* 可在麒麟 V11 + LoongArch 上部署运行

## Technical Approach

### 架构总览
```
┌─────────────────────────────────────────────────────┐
│                   Frontend (React)                    │
│  Ant Design 5 + Ant Design X + ECharts               │
│  深色主题 · WebSocket 实时通信                         │
├─────────────────────────────────────────────────────┤
│                   Backend (FastAPI)                   │
│  ┌───────────┐  ┌───────────┐  ┌─────────────────┐  │
│  │ WebSocket │  │ Safety    │  │ Agent Engine    │  │
│  │ Gateway   │  │ Guardrail │  │ (LangGraph)     │  │
│  └───────────┘  └───────────┘  └─────────────────┘  │
│  ┌───────────┐  ┌───────────┐  ┌─────────────────┐  │
│  │ MCP Server│  │ Knowledge │  │ Audit Logger    │  │
│  │ (FastMCP) │  │ Store     │  │                 │  │
│  └───────────┘  └───────────┘  └─────────────────┘  │
├─────────────────────────────────────────────────────┤
│              OS Layer (opsguard user)                 │
│  sudoers whitelist · Linux capabilities              │
└─────────────────────────────────────────────────────┘
```

### 技术栈
* **后端**：Python 3.11 + FastAPI + LangGraph + FastMCP + LiteLLM + SQLite
* **前端**：React 18 + TypeScript + Vite + Ant Design 5 + Ant Design X + ECharts
* **通信**：WebSocket（流式输出 + 审批推送）
* **大模型**：Qwen3（主）+ DeepSeek（备），OpenAI 兼容接口
* **安全**：规则引擎 + BERT 分类器（可选）+ LLM system prompt
* **存储**：SQLite（会话 checkpoint + 知识库 + 审计日志）

### 项目结构
```
OpsGuard/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── agent/               # LangGraph Agent 编排
│   │   ├── mcp_tools/           # MCP 工具插件
│   │   ├── safety/              # 安全护栏（规则+分类器+约束）
│   │   ├── knowledge/           # 知识库管理
│   │   ├── audit/               # 审计日志
│   │   └── websocket/           # WebSocket 网关
│   ├── requirements.txt
│   └── config.yaml              # 模型/权限/工具配置
├── frontend/
│   ├── src/
│   │   ├── components/          # UI 组件
│   │   ├── pages/               # 页面
│   │   ├── hooks/               # 自定义 hooks
│   │   ├── stores/              # 状态管理
│   │   └── styles/              # 深色主题样式
│   ├── package.json
│   └── vite.config.ts
├── docs/                        # 文档
└── scripts/                     # 部署/初始化脚本
```

### 关键设计决策
* **Monorepo**：前后端同仓库，联调方便，部署统一
* **LangGraph 图式编排**：Plan→Approve→Execute→Verify 显式节点，checkpoint 即审计日志
* **MCP 工具粒度**：原子化 + 少量聚合工具，保证通用性
* **WebSocket 双向通信**：流式输出 + 审批推送一条连接搞定
* **自适应安全降级**：根据环境自动选择最佳安全模式

## Out of Scope

* 多节点/集群管理（仅单机）
* 模型微调/训练
* 生产级高可用部署
* 移动端适配
* 前端多语言 i18n（LLM 天然支持多语言对话，界面暂只做中文）

## Technical Notes

* Skyflo 架构参考：Engine(Python+FastAPI+LangGraph) + MCP Server(FastMCP) + Command Center(UI)
* LlamaFirewall 安全护栏参考：PromptGuard 2 + AlignmentCheck + CodeShield
* Ant Design X 组件：ThoughtChain、Bubble、Sender、Conversations、useXAgent、useXChat
* FastMCP：@mcp.tool() 装饰器风格，Python 原生
* LangGraph：Checkpoint 持久化 + Human-in-the-loop + 图式编排
* LoongArch 兼容性：Python/FastAPI/SQLite 无问题，BERT 需 ONNX Runtime 或 PyTorch CPU
