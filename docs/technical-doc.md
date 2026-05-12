# OpsGuard 技术文档

## 1. 项目概述

OpsGuard 是一套部署于 Linux 操作系统的智能运维 Agent，通过实现 MCP（Model Context Protocol）协议，赋予大模型感知系统实时状态、采集运维指标及执行管理任务的能力。

核心设计理念：**通用问题解决能力优先**——Agent 不是场景匹配器，而是具备通用 Linux 运维推理能力的智能体。

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户浏览器                               │
│   React 18 + Ant Design 5 + ECharts · 深色运维控制台风格      │
├─────────────────────────── WebSocket ───────────────────────┤
│                      后端服务 (FastAPI)                       │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  WebSocket  │  │  安全护栏     │  │  Agent 引擎       │  │
│  │  Gateway    │  │  (三层纵深)   │  │  (LangGraph)      │  │
│  └─────────────┘  └──────────────┘  └───────────────────┘  │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  MCP 工具层  │  │  知识库       │  │  审计日志         │  │
│  │  (32个工具)  │  │  (SQLite)    │  │  (全链路溯源)     │  │
│  └─────────────┘  └──────────────┘  └───────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                   操作系统层 (opsguard 用户)                   │
│            sudoers 白名单 · 最小权限 · 自动备份               │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心模块详解

### 3.1 Agent 引擎 (LangGraph)

采用 Plan → Approve → Execute → Verify 闭环控制流：

1. **安全校验**：三层纵深过滤用户输入
2. **知识检索**：从历史经验库中查找相关案例
3. **LLM 推理**：动态组合工具链，多轮工具调用（最多10轮）
4. **审批等待**：高危操作暂停，WebSocket 推送审批请求
5. **执行验证**：执行后自动检查结果是否符合预期
6. **知识沉淀**：成功解决后自动提炼经验存入知识库

### 3.2 MCP 工具层 (32个原子化工具)

| 类别 | 工具数 | 示例 |
|------|--------|------|
| 进程管理 | 4 | list_processes, find_zombie_processes, kill_process |
| 磁盘文件 | 5 | get_disk_usage, find_large_files, check_file_info |
| 网络诊断 | 5 | get_listening_ports, get_connections, ping_host |
| 日志分析 | 5 | get_journal_logs, get_recent_errors, search_logs |
| 服务管理 | 6 | list_services, get_service_status, restart_service |
| 配置检查 | 5 | read_config_file, check_config_syntax, diff_config |
| 系统概览 | 4 | system_overview, health_check, get_crontab_list |

每个工具标注风险等级（READ/WRITE/DESTRUCTIVE），WRITE 及以上自动触发审批。

### 3.3 安全护栏（三层纵深 + 意图检测）

```
用户输入
    │
    ▼
【第一层：规则引擎】 71条规则，零延迟
    │ 命中 → 直接拒绝
    ▼
【第二层：BERT 分类器】 DeBERTa-v3 ONNX，~30ms
    │ 注入概率 > 85% → 拒绝
    ▼
【意图风险检测】 12条高危意图模式
    │ 命中 → 警告（不拦截，但 Agent 会主动确认）
    ▼
【第三层：LLM System Prompt】 始终生效的安全约束
    │
    ▼
  正常处理
```

- 规则引擎：36条注入模式 + 35条危险命令模式
- BERT 分类器：protectai/deberta-v3-base-prompt-injection-v2，ONNX 格式仅 2.5MB
- 自适应降级：低配环境自动退回两层模式

### 3.4 最小权限执行

- Agent 以 `opsguard` 专用系统用户运行
- sudoers 白名单精确控制可提权命令
- 受保护路径列表（/etc/passwd, /etc/shadow, /boot 等）绝对不可写
- 写操作前自动备份，支持一键回滚
- 命令执行超时限制（30秒）

### 3.5 推理链路溯源

完整记录每次交互的全链路：
```
接收指令 → 安全校验 → 知识检索 → 推理规划 → 工具调用 → 
审批请求 → 审批响应 → 执行操作 → 结果验证 → 生成回复 → 知识沉淀
```

- 后端：SQLite 审计表，结构化 JSON 存储
- 前端：TracePanel 实时展示，支持历史回放
- 对话内嵌诊断进度条，实时显示当前阶段

---

## 4. 创新功能

### 4.1 执行后验证 (Post-Action Verify)
工具执行后自动验证结果：kill_process 后检查 PID 是否消失，restart_service 后检查服务是否 active。

### 4.2 故障关联图谱
实时构建进程→端口→服务→配置的关联关系，ECharts 力导向图可视化。Agent 诊断时动态添加节点，故障相关节点红色高亮。

### 4.3 安全攻防演示模式
内置红队测试页面，预设 8 种注入攻击 + 10 种危险命令 + 5 种正常请求。评委可直接测试，实时看到三层防御的拦截过程。

### 4.4 运维知识自动沉淀
每次成功解决问题后，LLM 自动提炼"问题特征→诊断路径→解决方案"存入知识库。下次遇到类似问题时自动引用历史经验。

### 4.5 操作影响预评估
高危操作前自动分析影响范围：检查子进程、监听端口、服务依赖、文件引用关系。评估结果展示在审批弹窗中。

### 4.6 健康巡检报告
一键生成系统健康报告（CPU/内存/磁盘/网络四维），自动识别问题并给出优化建议。支持 PDF 导出。

---

## 5. 技术栈

| 层 | 技术 | 选型理由 |
|---|---|---|
| 前端 | React 18 + TypeScript + Vite | 现代化开发体验，类型安全 |
| UI 组件 | Ant Design 5 + Ant Design X | 国产大厂出品，AI 对话组件原生支持 |
| 可视化 | ECharts | 国产图表库，关系图支持好 |
| 后端 | Python 3.11 + FastAPI | 异步高性能，生态丰富 |
| Agent 框架 | LangGraph | 图式编排，checkpoint 持久化 |
| LLM 接入 | LiteLLM | 统一接口，一行配置切换模型 |
| 大模型 | Qwen3（主）+ DeepSeek（备） | 国产开源，tool calling 支持好 |
| 通信 | WebSocket | 双向实时，流式输出 + 审批推送 |
| 存储 | SQLite | 轻量零依赖，适合单机部署 |
| 安全 | ONNX Runtime + DeBERTa | 轻量推理，无需 GPU |

---

## 6. 部署说明

### 环境要求
- 麒麟高级服务器版 V11 (LoongArch / x86_64)
- Python 3.10+
- Node.js 18+（仅构建时需要）

### 一键部署
```bash
git clone <repo>
cd OpsGuard
bash scripts/install.sh
```

### 配置
```bash
cp backend/config.yaml.example backend/config.yaml
# 编辑 config.yaml，填入 LLM API Key
```

### 启动
```bash
sudo systemctl start opsguard
# 访问 http://localhost:8000
```

---

## 7. 安全设计总结

| 防御层 | 机制 | 延迟 | 覆盖 |
|--------|------|------|------|
| 输入层-规则 | 正则匹配 71 条模式 | 0ms | 已知攻击模式 |
| 输入层-AI | BERT 语义分类 | ~30ms | 变体/伪装攻击 |
| 输入层-意图 | 高危意图检测 | 0ms | 合法但危险的请求 |
| 推理层 | System Prompt 约束 | 0ms | 最后兜底 |
| 执行层 | 命令白名单 + 参数校验 | 0ms | 危险命令 |
| 执行层 | 人类审批 | 等待用户 | 所有写操作 |
| 系统层 | opsguard 用户 + sudoers | 0ms | 权限隔离 |
| 恢复层 | 自动备份 + 回滚 | ~10ms | 操作可逆 |
