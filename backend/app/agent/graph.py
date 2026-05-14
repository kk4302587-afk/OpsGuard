"""LangGraph Agent workflow definition.

Uses LangGraph StateGraph to implement the Plan → Approve → Execute → Verify control loop.
Each node is a step in the reasoning pipeline, with full audit logging.
"""

import json
from typing import TypedDict, Annotated, Callable
from datetime import datetime

from loguru import logger
from langgraph.graph import StateGraph, END

from app.agent.llm import call_llm, SYSTEM_PROMPT
from app.agent.tools_registry import tools_registry, RiskLevel
from app.safety.guardrail import SafetyGuardrail
from app.audit.logger import audit_logger, AuditPhase, AuditEventType
from app.knowledge.store import knowledge_store


# === State Definition ===

class AgentState(TypedDict):
    """State maintained throughout the Agent workflow."""
    session_id: str
    user_message: str
    messages: list  # Conversation history (OpenAI format)
    final_response: str
    is_blocked: bool
    block_reason: str
    risk_warning: str
    knowledge_hint: str
    iteration: int
    send_to_client: object  # Callable, not serializable but used in-memory


# === Safety Guardrail ===
_guardrail = SafetyGuardrail()


# === Node Functions ===

async def safety_check_node(state: AgentState) -> dict:
    """Node 1: Check user input through safety guardrail layers."""
    session_id = state["session_id"]
    user_message = state["user_message"]
    send_to_client = state["send_to_client"]

    await send_to_client({"type": "trace", "phase": "safety_check", "event_type": "start", "content": "正在进行安全校验..."})
    await audit_logger.log(session_id, AuditPhase.SAFETY_CHECK, AuditEventType.START, f"检查输入: {user_message[:100]}")

    safety_result = _guardrail.check_input(user_message)

    if not safety_result.is_safe:
        await audit_logger.log(session_id, AuditPhase.SAFETY_CHECK, AuditEventType.BLOCKED, f"输入被拦截: {safety_result.detail}")
        await send_to_client({"type": "trace", "phase": "safety_check", "event_type": "blocked", "content": safety_result.detail})
        return {
            "is_blocked": True,
            "block_reason": safety_result.detail,
            "final_response": f"安全校验未通过: {safety_result.detail}\n\n您的请求被安全系统拦截。如果这是误判，请换一种方式描述您的需求。",
        }

    await audit_logger.log(session_id, AuditPhase.SAFETY_CHECK, AuditEventType.SUCCESS, "安全校验通过")
    await send_to_client({"type": "trace", "phase": "safety_check", "event_type": "success", "content": f"安全校验通过 ({', '.join(safety_result.layers_checked)})"})

    # Check high-risk intent
    risk_warning = ""
    intent_result = _guardrail.check_high_risk_intent(user_message)
    if intent_result.is_warning:
        risk_warning = f"\n\n系统检测到此请求涉及高风险操作（{intent_result.detail}）。请务必在执行前向用户确认，并详细说明影响范围。"
        await send_to_client({"type": "trace", "phase": "safety_check", "event_type": "start", "content": f"高风险意图警告: {intent_result.detail}"})

    return {"is_blocked": False, "risk_warning": risk_warning}


async def knowledge_retrieval_node(state: AgentState) -> dict:
    """Node 2: Retrieve relevant knowledge from history."""
    session_id = state["session_id"]
    user_message = state["user_message"]
    send_to_client = state["send_to_client"]

    await send_to_client({"type": "trace", "phase": "knowledge_retrieval", "event_type": "start", "content": "检索历史经验..."})

    knowledge_context = await knowledge_store.search(user_message, limit=3)
    knowledge_hint = ""
    if knowledge_context:
        knowledge_hint = "\n\n## 历史经验参考\n"
        for entry in knowledge_context:
            knowledge_hint += f"- 问题: {entry['problem_signature']}\n  解决: {entry['solution']}\n"
        await send_to_client({"type": "trace", "phase": "knowledge_retrieval", "event_type": "success", "content": f"找到 {len(knowledge_context)} 条相关经验"})
    else:
        await send_to_client({"type": "trace", "phase": "knowledge_retrieval", "event_type": "success", "content": "无相关历史经验"})

    return {"knowledge_hint": knowledge_hint}


async def reasoning_node(state: AgentState) -> dict:
    """Node 3: LLM reasoning with tool calling loop."""
    session_id = state["session_id"]
    user_message = state["user_message"]
    send_to_client = state["send_to_client"]
    risk_warning = state.get("risk_warning", "")
    knowledge_hint = state.get("knowledge_hint", "")

    await send_to_client({"type": "trace", "phase": "planning", "event_type": "start", "content": "正在分析问题并制定方案..."})
    await audit_logger.log(session_id, AuditPhase.PLANNING, AuditEventType.START, "开始推理")

    # Build messages
    messages = list(state.get("messages", []))
    user_content = user_message + knowledge_hint + risk_warning
    messages.append({"role": "user", "content": user_content})

    all_tools = tools_registry.get_all_tools_for_llm()
    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        llm_response = await call_llm(messages, tools=all_tools)

        if llm_response["tool_calls"]:
            for tool_call in llm_response["tool_calls"]:
                tool_name = tool_call["name"]
                tool_args = tool_call["arguments"]
                tool_def = tools_registry.get_tool(tool_name)

                if not tool_def:
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                    ]})
                    messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": f"Error: Unknown tool '{tool_name}'"})
                    continue

                await send_to_client({"type": "trace", "phase": "tool_call", "event_type": "start", "content": f"调用工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})"})
                await audit_logger.log(session_id, AuditPhase.TOOL_CALL, AuditEventType.START, f"工具调用: {tool_name}", {"args": tool_args})

                # Approval for write operations
                if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                    cmd_check = _guardrail.check_command(json.dumps(tool_args))
                    if not cmd_check.is_safe:
                        messages.append({"role": "assistant", "content": None, "tool_calls": [
                            {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                        ]})
                        messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": f"BLOCKED: {cmd_check.detail}"})
                        await send_to_client({"type": "trace", "phase": "tool_call", "event_type": "blocked", "content": f"被拦截: {cmd_check.detail}"})
                        continue

                    # Request approval
                    from app.websocket.approval import approval_manager
                    import asyncio as _asyncio

                    request_id = tool_call.get("id", "") or f"{tool_name}_{iteration}"
                    impact_text = await assess_impact(tool_name, tool_args, session_id, send_to_client)

                    loop = _asyncio.get_running_loop()
                    approval_future = loop.create_future()
                    approval_manager.register_pending(request_id, session_id, tool_name, tool_args, tool_def.risk_level, tool_def.description, approval_future)

                    await send_to_client({
                        "type": "approval_request",
                        "request_id": request_id,
                        "command": f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)})",
                        "risk_level": tool_def.risk_level,
                        "description": tool_def.description,
                        "impact": impact_text,
                    })
                    await send_to_client({"type": "trace", "phase": "approval_request", "event_type": "pending", "content": f"等待用户审批: {tool_name}"})

                    try:
                        approved = await _asyncio.wait_for(approval_future, timeout=300.0)
                    except _asyncio.TimeoutError:
                        approved = False
                    finally:
                        approval_manager.remove_pending(request_id)

                    if not approved:
                        messages.append({"role": "assistant", "content": None, "tool_calls": [
                            {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                        ]})
                        messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": "REJECTED: User denied this operation"})
                        await send_to_client({"type": "trace", "phase": "approval_response", "event_type": "failure", "content": "用户拒绝了操作"})
                        continue

                    await send_to_client({"type": "trace", "phase": "approval_response", "event_type": "success", "content": "用户已批准"})

                # Execute tool
                try:
                    # Backup before write
                    backup_record = None
                    if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                        from app.mcp_tools.backup import backup_manager
                        target_path = tool_args.get("filepath") or tool_args.get("path") or tool_args.get("service")
                        if target_path and isinstance(target_path, str):
                            backup_record = backup_manager.backup_file(target_path, operation=f"{tool_name}")
                            if backup_record:
                                await send_to_client({"type": "trace", "phase": "execution", "event_type": "start", "content": f"已备份: {target_path}"})

                    result = tool_def.function(**tool_args)
                    tool_result_str = json.dumps(result.__dict__ if hasattr(result, '__dict__') else result, ensure_ascii=False, default=str)

                    # Post-action verification for write operations
                    if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                        verification = _verify_tool_result(tool_name, tool_args, result)
                        if verification:
                            await send_to_client({"type": "trace", "phase": "verification", "event_type": verification["status"], "content": verification["message"]})

                        # Before/After change diff
                        change_diff = _capture_change_diff(tool_name, tool_args, backup_record)
                        if change_diff:
                            await send_to_client({"type": "trace", "phase": "verification", "event_type": "success", "content": f"变更对比:\n{change_diff}"})

                    await audit_logger.log(session_id, AuditPhase.EXECUTION, AuditEventType.SUCCESS, f"工具执行成功: {tool_name}")
                    await send_to_client({"type": "trace", "phase": "execution", "event_type": "success", "content": f"执行成功: {tool_name}"})

                except Exception as e:
                    tool_result_str = json.dumps({"error": str(e)})
                    await send_to_client({"type": "trace", "phase": "execution", "event_type": "failure", "content": f"执行失败: {tool_name} - {e}"})

                messages.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                ]})
                messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": tool_result_str})
        else:
            break

    final_response = llm_response.get("content", "") or "分析完成，请查看推理链路了解详情。"
    await audit_logger.log(session_id, AuditPhase.RESPONSE, AuditEventType.SUCCESS, f"生成回复: {final_response[:200]}")
    await send_to_client({"type": "trace", "phase": "response", "event_type": "success", "content": "回复已生成"})

    return {"final_response": final_response, "messages": messages, "iteration": iteration}


async def knowledge_save_node(state: AgentState) -> dict:
    """Node 4: Save knowledge and generate Runbook from successful resolution."""
    session_id = state["session_id"]
    user_message = state["user_message"]
    final_response = state["final_response"]
    messages = state.get("messages", [])
    send_to_client = state["send_to_client"]

    if not any(msg.get("role") == "tool" for msg in messages):
        return {}

    # Knowledge extraction
    try:
        import re
        summary_messages = [
            {"role": "system", "content": "你是一个运维知识提取器。严格按JSON格式回复：{\"problem\": \"问题简述\", \"diagnosis\": \"诊断步骤\", \"solution\": \"解决方案\"}。如果没有成功解决问题，回复 null"},
            {"role": "user", "content": f"用户问题: {user_message}\n\nAgent回复: {final_response[:500]}"},
        ]
        summary_response = await call_llm(summary_messages)
        summary_text = summary_response.get("content", "").strip()

        if summary_text and summary_text.lower() != "null" and "{" in summary_text:
            json_match = re.search(r'\{.*?\}', summary_text, re.DOTALL)
            if json_match:
                summary_data = json.loads(json_match.group())
                problem = summary_data.get("problem", "")
                solution = summary_data.get("solution", "")
                if problem and solution:
                    await knowledge_store.save_resolution(
                        problem_signature=problem,
                        diagnosis_path=summary_data.get("diagnosis", ""),
                        solution=solution,
                        tools_used=["agent"],
                    )
                    await send_to_client({"type": "trace", "phase": "knowledge_save", "event_type": "success", "content": f"经验已保存: {problem[:30]}"})
    except Exception as e:
        logger.warning(f"Knowledge save failed: {e}")

    # Runbook generation
    tool_call_sequence = []
    for msg in messages:
        if msg.get("tool_calls") and isinstance(msg["tool_calls"], list):
            for tc in msg["tool_calls"]:
                if isinstance(tc, dict) and "function" in tc:
                    func = tc["function"]
                    tool_call_sequence.append({
                        "tool_name": func.get("name", ""),
                        "tool_args": json.loads(func["arguments"]) if isinstance(func.get("arguments"), str) else func.get("arguments", {}),
                    })

    if len(tool_call_sequence) >= 2:
        try:
            import aiosqlite, uuid as _uuid
            from app.database import get_knowledge_db_path

            runbook_steps = []
            for tc in tool_call_sequence:
                td = tools_registry.get_tool(tc["tool_name"])
                if td:
                    runbook_steps.append({"tool_name": tc["tool_name"], "tool_args": tc["tool_args"], "description": td.description, "risk_level": td.risk_level.value})

            if runbook_steps:
                async with aiosqlite.connect(get_knowledge_db_path()) as db:
                    await db.execute("CREATE TABLE IF NOT EXISTS runbooks (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, trigger_pattern TEXT, steps TEXT NOT NULL, run_count INTEGER DEFAULT 0, last_run TEXT, created_at TEXT NOT NULL)")
                    await db.execute(
                        "INSERT INTO runbooks (id, name, description, trigger_pattern, steps, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (str(_uuid.uuid4()), user_message[:40], f"自动生成", user_message[:100], json.dumps(runbook_steps, ensure_ascii=False), datetime.now().isoformat()),
                    )
                    await db.commit()
                await send_to_client({"type": "trace", "phase": "knowledge_save", "event_type": "success", "content": "Runbook 已保存"})
        except Exception as e:
            logger.warning(f"Runbook generation failed: {e}")

    return {}


# === Routing Functions ===

def should_continue_after_safety(state: AgentState) -> str:
    """Route after safety check: blocked → END, safe → knowledge_retrieval."""
    if state.get("is_blocked"):
        return END
    return "knowledge_retrieval"


# === Build the Graph ===

def build_agent_graph():
    """Construct the LangGraph StateGraph for the Agent workflow."""
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("safety_check", safety_check_node)
    workflow.add_node("knowledge_retrieval", knowledge_retrieval_node)
    workflow.add_node("reasoning", reasoning_node)
    workflow.add_node("knowledge_save", knowledge_save_node)

    # Set entry point
    workflow.set_entry_point("safety_check")

    # Add edges
    workflow.add_conditional_edges("safety_check", should_continue_after_safety)
    workflow.add_edge("knowledge_retrieval", "reasoning")
    workflow.add_edge("reasoning", "knowledge_save")
    workflow.add_edge("knowledge_save", END)

    return workflow.compile()


# Compiled graph instance
agent_graph = build_agent_graph()


# === Public API ===

async def run_agent(
    session_id: str,
    user_message: str,
    conversation_history: list[dict],
    send_to_client: Callable,
) -> str:
    """Run the Agent pipeline using LangGraph.

    This is the main entry point called by the WebSocket handler.
    """
    initial_state: AgentState = {
        "session_id": session_id,
        "user_message": user_message,
        "messages": conversation_history,
        "final_response": "",
        "is_blocked": False,
        "block_reason": "",
        "risk_warning": "",
        "knowledge_hint": "",
        "iteration": 0,
        "send_to_client": send_to_client,
    }

    # Run the graph
    final_state = await agent_graph.ainvoke(initial_state)

    return final_state.get("final_response", "处理完成。")


# === Helper Functions ===

async def assess_impact(tool_name: str, tool_args: dict, session_id: str, send_to_client) -> str | None:
    """Pre-execution impact assessment for high-risk operations."""
    impact_lines = []

    if tool_name == "kill_process":
        import psutil
        pid = tool_args.get("pid")
        if pid:
            try:
                proc = psutil.Process(pid)
                children = proc.children(recursive=True)
                connections = proc.net_connections()
                impact_lines.append(f"目标进程: {proc.name()} (PID: {pid})")
                if children:
                    impact_lines.append(f"子进程数: {len(children)}")
                if connections:
                    ports = [c.laddr.port for c in connections if c.status == 'LISTEN']
                    if ports:
                        impact_lines.append(f"监听端口: {ports}")
            except Exception:
                pass

    elif tool_name in ("restart_service", "stop_service"):
        import subprocess
        service = tool_args.get("service")
        if service:
            action = "重启" if tool_name == "restart_service" else "停止"
            impact_lines.append(f"操作: {action} {service}")
            impact_lines.append("影响: 服务短暂不可用")

    if impact_lines:
        impact_text = "\n".join(impact_lines)
        await send_to_client({"type": "trace", "phase": "planning", "event_type": "start", "content": f"影响评估:\n{impact_text}"})
        return impact_text
    return None


def _verify_tool_result(tool_name: str, tool_args: dict, result) -> dict | None:
    """Post-action verification: check if the tool execution achieved its goal."""
    result_data = result.__dict__ if hasattr(result, '__dict__') else {}
    success = result_data.get("success", True) if isinstance(result_data, dict) else True

    if not success:
        error = result_data.get("error", "未知错误") if isinstance(result_data, dict) else "执行返回失败"
        return {"status": "failure", "message": f"验证失败: {error}"}

    if tool_name == "kill_process":
        import psutil
        pid = tool_args.get("pid")
        if pid and not psutil.pid_exists(pid):
            return {"status": "success", "message": f"验证通过: PID {pid} 已终止"}
        elif pid:
            return {"status": "failure", "message": f"验证失败: PID {pid} 仍然存在"}

    elif tool_name == "restart_service":
        import subprocess
        service = tool_args.get("service")
        if service:
            try:
                check = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=5)
                if check.stdout.strip() == "active":
                    return {"status": "success", "message": f"验证通过: {service} 已重启并运行中"}
                return {"status": "failure", "message": f"验证失败: {service} 状态为 {check.stdout.strip()}"}
            except Exception:
                pass

    elif tool_name == "stop_service":
        import subprocess
        service = tool_args.get("service")
        if service:
            try:
                check = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=5)
                if check.stdout.strip() == "inactive":
                    return {"status": "success", "message": f"验证通过: {service} 已停止"}
                return {"status": "failure", "message": f"验证失败: {service} 仍在运行"}
            except Exception:
                pass

    if success:
        return {"status": "success", "message": f"执行完成: {tool_name}"}
    return None


def _capture_change_diff(tool_name: str, tool_args: dict, backup_record: dict | None) -> str | None:
    """Capture before/after diff for write operations."""
    diff_lines = []

    if tool_name == "kill_process":
        import psutil
        pid = tool_args.get("pid")
        if pid:
            exists = psutil.pid_exists(pid)
            diff_lines.append(f"[Before] PID {pid}: 运行中")
            diff_lines.append(f"[After]  PID {pid}: {'仍在运行' if exists else '已终止'}")

    elif tool_name == "restart_service":
        import subprocess
        service = tool_args.get("service")
        if service:
            try:
                check = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=5)
                diff_lines.append(f"[Before] {service}: 运行中 (重启前)")
                diff_lines.append(f"[After]  {service}: {check.stdout.strip()}")
            except Exception:
                pass

    elif tool_name == "stop_service":
        import subprocess
        service = tool_args.get("service")
        if service:
            try:
                check = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=5)
                diff_lines.append(f"[Before] {service}: 运行中")
                diff_lines.append(f"[After]  {service}: {check.stdout.strip()}")
            except Exception:
                pass

    elif backup_record:
        from pathlib import Path
        original_path = Path(backup_record.get("original_path", ""))
        if original_path.exists():
            current_size = original_path.stat().st_size
            backup_size = backup_record.get("size", 0)
            if current_size != backup_size:
                diff_lines.append(f"[Before] 文件大小: {backup_size} bytes")
                diff_lines.append(f"[After]  文件大小: {current_size} bytes")
                diff_lines.append(f"[Diff]   变化: {current_size - backup_size:+d} bytes")
        elif backup_record.get("original_path"):
            diff_lines.append(f"[Before] 文件存在: {backup_record['original_path']}")
            diff_lines.append(f"[After]  文件已删除")

    return "\n".join(diff_lines) if diff_lines else None
