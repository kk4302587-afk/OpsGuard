"""LangGraph Agent workflow definition.

Implements the Plan → Approve → Execute → Verify control loop.
Each node is a step in the reasoning pipeline, with full audit logging.
"""

import json
from typing import TypedDict, Callable
from datetime import datetime

from loguru import logger

from app.agent.llm import call_llm, SYSTEM_PROMPT
from app.agent.tools_registry import tools_registry, RiskLevel
from app.safety.guardrail import SafetyGuardrail
from app.audit.logger import audit_logger, AuditPhase, AuditEventType
from app.knowledge.store import knowledge_store


class AgentState(TypedDict):
    """State maintained throughout the Agent workflow."""
    session_id: str
    user_message: str
    messages: list[dict]  # Full conversation history (OpenAI format)
    plan: str | None
    tool_calls: list[dict]
    tool_results: list[dict]
    needs_approval: bool
    approval_callback: object | None  # asyncio.Future for approval
    response: str | None
    error: str | None
    trace: list[dict]


# Safety guardrail instance
_guardrail = SafetyGuardrail()


async def run_agent(
    session_id: str,
    user_message: str,
    conversation_history: list[dict],
    send_to_client: Callable,
) -> str:
    """
    Run the full Agent pipeline for a user message.

    This is the main entry point called by the WebSocket handler.
    Implements: Safety Check → Knowledge Retrieval → LLM Reasoning (with tool loop) → Verify → Respond

    Args:
        session_id: Current session ID
        user_message: The user's natural language input
        conversation_history: Previous messages in this session
        send_to_client: Async callback to send messages to the WebSocket client

    Returns:
        The final response text
    """

    # === Phase 1: Safety Check ===
    await send_to_client({"type": "trace", "phase": "safety_check", "event_type": "start", "content": "正在进行安全校验..."})
    await audit_logger.log(session_id, AuditPhase.SAFETY_CHECK, AuditEventType.START, f"检查输入: {user_message[:100]}")

    safety_result = _guardrail.check_input(user_message)

    if not safety_result.is_safe:
        await audit_logger.log(
            session_id, AuditPhase.SAFETY_CHECK, AuditEventType.BLOCKED,
            f"输入被拦截: {safety_result.detail}",
            {"blocked_by": safety_result.blocked_by, "layers_checked": safety_result.layers_checked},
        )
        await send_to_client({"type": "trace", "phase": "safety_check", "event_type": "blocked", "content": safety_result.detail})
        return f"⚠️ 安全校验未通过: {safety_result.detail}\n\n您的请求被安全系统拦截。如果这是误判，请换一种方式描述您的需求。"

    await audit_logger.log(session_id, AuditPhase.SAFETY_CHECK, AuditEventType.SUCCESS, "安全校验通过", {"layers_checked": safety_result.layers_checked})
    await send_to_client({"type": "trace", "phase": "safety_check", "event_type": "success", "content": f"安全校验通过 ({', '.join(safety_result.layers_checked)})"})

    # Check for high-risk intent (warning, not block)
    intent_result = _guardrail.check_high_risk_intent(user_message)
    risk_warning = ""
    if intent_result.is_warning:
        risk_warning = f"\n\n⚠️ 系统检测到此请求涉及高风险操作（{intent_result.detail}）。请务必在执行前向用户确认，并详细说明影响范围。"
        await send_to_client({"type": "trace", "phase": "safety_check", "event_type": "start", "content": f"高风险意图警告: {intent_result.detail}"})
    # === Phase 2: Knowledge Retrieval ===
    await send_to_client({"type": "trace", "phase": "knowledge_retrieval", "event_type": "start", "content": "检索历史经验..."})

    knowledge_context = await knowledge_store.search(user_message, limit=3)
    knowledge_hint = ""
    if knowledge_context:
        knowledge_hint = "\n\n## 历史经验参考\n"
        for entry in knowledge_context:
            knowledge_hint += f"- 问题: {entry['problem_signature']}\n  解决: {entry['solution']}\n"
        await audit_logger.log(
            session_id, AuditPhase.KNOWLEDGE_RETRIEVAL, AuditEventType.SUCCESS,
            f"找到 {len(knowledge_context)} 条相关经验",
        )
        await send_to_client({"type": "trace", "phase": "knowledge_retrieval", "event_type": "success", "content": f"找到 {len(knowledge_context)} 条相关经验"})
    else:
        await send_to_client({"type": "trace", "phase": "knowledge_retrieval", "event_type": "success", "content": "无相关历史经验"})

    # === Phase 3: LLM Reasoning with Tool Loop ===
    await send_to_client({"type": "trace", "phase": "planning", "event_type": "start", "content": "正在分析问题并制定方案..."})
    await audit_logger.log(session_id, AuditPhase.PLANNING, AuditEventType.START, "开始推理")

    # Build messages for LLM
    messages = list(conversation_history)
    user_content = user_message
    if knowledge_hint:
        user_content += knowledge_hint
    if risk_warning:
        user_content += risk_warning
    messages.append({"role": "user", "content": user_content})

    # Get available tools
    all_tools = tools_registry.get_all_tools_for_llm()

    # Tool calling loop (max iterations to prevent infinite loops)
    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # Call LLM
        llm_response = await call_llm(messages, tools=all_tools)

        # If LLM wants to call tools
        if llm_response["tool_calls"]:
            for tool_call in llm_response["tool_calls"]:
                tool_name = tool_call["name"]
                tool_args = tool_call["arguments"]
                tool_def = tools_registry.get_tool(tool_name)

                if not tool_def:
                    # Unknown tool
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                    ]})
                    messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": f"Error: Unknown tool '{tool_name}'"})
                    continue

                await send_to_client({
                    "type": "trace", "phase": "tool_call", "event_type": "start",
                    "content": f"调用工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})",
                })
                await audit_logger.log(
                    session_id, AuditPhase.TOOL_CALL, AuditEventType.START,
                    f"工具调用: {tool_name}", {"args": tool_args, "risk_level": tool_def.risk_level},
                )

                # Check if tool needs approval (WRITE or DESTRUCTIVE)
                if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                    # Check command safety
                    cmd_check = _guardrail.check_command(json.dumps(tool_args))
                    if not cmd_check.is_safe:
                        await audit_logger.log(
                            session_id, AuditPhase.TOOL_CALL, AuditEventType.BLOCKED,
                            f"工具调用被拦截: {cmd_check.detail}",
                        )
                        messages.append({"role": "assistant", "content": None, "tool_calls": [
                            {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                        ]})
                        messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": f"BLOCKED: {cmd_check.detail}"})
                        await send_to_client({"type": "trace", "phase": "tool_call", "event_type": "blocked", "content": f"被拦截: {cmd_check.detail}"})
                        continue

                    # Request approval from user via WebSocket
                    from app.websocket.approval import approval_manager

                    request_id = tool_call.get("id", "") or f"{tool_name}_{iteration}"

                    # Pre-execution impact assessment
                    impact_text = await assess_impact(tool_name, tool_args, session_id, send_to_client)

                    await send_to_client({
                        "type": "approval_request",
                        "request_id": request_id,
                        "command": f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)})",
                        "risk_level": tool_def.risk_level,
                        "description": tool_def.description,
                        "impact": impact_text,
                    })
                    await audit_logger.log(
                        session_id, AuditPhase.APPROVAL_REQUEST, AuditEventType.PENDING,
                        f"等待审批: {tool_name}",
                    )
                    await send_to_client({"type": "trace", "phase": "approval_request", "event_type": "pending", "content": f"等待用户审批: {tool_name}"})

                    # Wait for user approval (blocks until user responds or timeout)
                    approved = await approval_manager.request_approval(
                        request_id=request_id,
                        session_id=session_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level=tool_def.risk_level,
                        description=tool_def.description,
                        timeout=300.0,
                    )

                    if not approved:
                        messages.append({"role": "assistant", "content": None, "tool_calls": [
                            {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                        ]})
                        messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": "REJECTED: User denied this operation"})
                        await audit_logger.log(session_id, AuditPhase.APPROVAL_RESPONSE, AuditEventType.FAILURE, "用户拒绝了操作")
                        await send_to_client({"type": "trace", "phase": "approval_response", "event_type": "failure", "content": "用户拒绝了操作"})
                        continue

                    await audit_logger.log(session_id, AuditPhase.APPROVAL_RESPONSE, AuditEventType.SUCCESS, "用户已批准")
                    await send_to_client({"type": "trace", "phase": "approval_response", "event_type": "success", "content": "用户已批准"})

                # Execute the tool
                try:
                    # Auto-backup before write operations
                    backup_record = None
                    if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                        from app.mcp_tools.backup import backup_manager
                        # Determine what file/resource might be affected
                        target_path = tool_args.get("filepath") or tool_args.get("path") or tool_args.get("service")
                        if target_path and isinstance(target_path, str):
                            backup_record = backup_manager.backup_file(
                                target_path,
                                operation=f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)})",
                            )
                            if backup_record:
                                await send_to_client({"type": "trace", "phase": "execution", "event_type": "start", "content": f"已备份: {target_path}"})

                    result = tool_def.function(**tool_args)
                    tool_result_str = json.dumps(result.__dict__ if hasattr(result, '__dict__') else result, ensure_ascii=False, default=str)

                    # Post-action verification for write operations
                    if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                        verification = _verify_tool_result(tool_name, tool_args, result)
                        if verification:
                            await send_to_client({"type": "trace", "phase": "verification", "event_type": verification["status"], "content": verification["message"]})
                            await audit_logger.log(
                                session_id, AuditPhase.VERIFICATION,
                                AuditEventType.SUCCESS if verification["status"] == "success" else AuditEventType.FAILURE,
                                verification["message"],
                            )
                            # Append verification info to tool result
                            tool_result_str = json.dumps({
                                "result": result.__dict__ if hasattr(result, '__dict__') else result,
                                "verification": verification["message"],
                                "backup_id": backup_record["id"] if backup_record else None,
                            }, ensure_ascii=False, default=str)

                    await audit_logger.log(
                        session_id, AuditPhase.EXECUTION, AuditEventType.SUCCESS,
                        f"工具执行成功: {tool_name}",
                        {"result_preview": tool_result_str[:500], "backup_id": backup_record["id"] if backup_record else None},
                    )
                    await send_to_client({"type": "trace", "phase": "execution", "event_type": "success", "content": f"执行成功: {tool_name}"})

                except Exception as e:
                    tool_result_str = json.dumps({"error": str(e)})
                    await audit_logger.log(
                        session_id, AuditPhase.EXECUTION, AuditEventType.FAILURE,
                        f"工具执行失败: {tool_name} - {e}",
                    )
                    await send_to_client({"type": "trace", "phase": "execution", "event_type": "failure", "content": f"执行失败: {tool_name} - {e}"})

                # Add tool call and result to messages
                messages.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                ]})
                messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": tool_result_str})

        else:
            # LLM returned a text response (no more tool calls)
            break

    # === Phase 4: Final Response ===
    final_response = llm_response.get("content", "")

    if not final_response:
        final_response = "分析完成，但未能生成总结。请查看推理链路了解详情。"

    await audit_logger.log(session_id, AuditPhase.RESPONSE, AuditEventType.SUCCESS, f"生成回复: {final_response[:200]}")
    await send_to_client({"type": "trace", "phase": "response", "event_type": "success", "content": "回复已生成"})

    # === Phase 5: Knowledge Save (if applicable) ===
    # Detect if tools were used successfully and save the resolution pattern
    if any(messages_entry.get("role") == "tool" for messages_entry in messages):
        try:
            # Ask LLM to summarize the resolution for knowledge base
            summary_messages = [
                {"role": "system", "content": "你是一个运维知识提取器。请根据以下对话提取关键信息，严格按照JSON格式回复，不要添加任何其他文字：\n{\"problem\": \"一句话描述问题\", \"diagnosis\": \"诊断步骤摘要\", \"solution\": \"解决方案摘要\"}\n如果对话中没有成功解决问题，只回复: null"},
                {"role": "user", "content": f"用户问题: {user_message}\n\nAgent回复: {final_response[:500]}"},
            ]
            summary_response = await call_llm(summary_messages)
            summary_text = summary_response.get("content", "").strip()

            logger.debug(f"Knowledge extraction response: {summary_text[:200]}")

            if summary_text and summary_text.lower() != "null" and "{" in summary_text:
                import re
                # Match JSON object (handle multi-line and nested quotes)
                json_match = re.search(r'\{.*?\}', summary_text, re.DOTALL)
                if json_match:
                    try:
                        summary_data = json.loads(json_match.group())
                        problem = summary_data.get("problem", "")
                        diagnosis = summary_data.get("diagnosis", "")
                        solution = summary_data.get("solution", "")

                        if problem and solution:  # Only save if we have meaningful data
                            tools_used = [
                                tool_call["name"]
                                for msg in messages if msg.get("tool_calls")
                                for tool_call in (msg["tool_calls"] if isinstance(msg.get("tool_calls"), list) else [])
                                if isinstance(tool_call, dict) and "name" in tool_call
                            ]
                            # Deduplicate
                            tools_used = list(set(tools_used)) if tools_used else ["unknown"]

                            await knowledge_store.save_resolution(
                                problem_signature=problem,
                                diagnosis_path=diagnosis,
                                solution=solution,
                                tools_used=tools_used,
                            )
                            await audit_logger.log(session_id, AuditPhase.KNOWLEDGE_SAVE, AuditEventType.SUCCESS, f"知识已沉淀: {problem[:50]}")
                            await send_to_client({"type": "trace", "phase": "knowledge_save", "event_type": "success", "content": f"经验已保存: {problem[:30]}"})
                        else:
                            logger.debug("Knowledge extraction: empty problem or solution, skipping")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Knowledge JSON parse failed: {e}, raw: {json_match.group()[:100]}")
        except Exception as e:
            logger.warning(f"Knowledge save failed (non-critical): {e}")

    return final_response


def _verify_tool_result(tool_name: str, tool_args: dict, result) -> dict | None:
    """Post-action verification: check if the tool execution achieved its goal.

    Returns a dict with 'status' ('success'/'failure') and 'message', or None if no verification needed.
    """
    # Only verify write/destructive operations
    result_data = result.__dict__ if hasattr(result, '__dict__') else {}
    success = result_data.get("success", True) if isinstance(result_data, dict) else True

    if not success:
        error = result_data.get("error", "未知错误") if isinstance(result_data, dict) else "执行返回失败"
        return {"status": "failure", "message": f"验证失败: {error}"}

    # Tool-specific verification
    if tool_name == "kill_process":
        pid = tool_args.get("pid")
        if pid:
            import psutil
            if psutil.pid_exists(pid):
                return {"status": "failure", "message": f"验证失败: PID {pid} 仍然存在"}
            return {"status": "success", "message": f"验证通过: PID {pid} 已终止"}

    elif tool_name == "restart_service":
        service = tool_args.get("service")
        if service:
            import subprocess
            try:
                check = subprocess.run(
                    ["systemctl", "is-active", service],
                    capture_output=True, text=True, timeout=5,
                )
                if check.stdout.strip() == "active":
                    return {"status": "success", "message": f"验证通过: {service} 已重启并运行中"}
                else:
                    return {"status": "failure", "message": f"验证失败: {service} 状态为 {check.stdout.strip()}"}
            except Exception:
                pass

    elif tool_name == "stop_service":
        service = tool_args.get("service")
        if service:
            import subprocess
            try:
                check = subprocess.run(
                    ["systemctl", "is-active", service],
                    capture_output=True, text=True, timeout=5,
                )
                if check.stdout.strip() == "inactive":
                    return {"status": "success", "message": f"验证通过: {service} 已停止"}
                else:
                    return {"status": "failure", "message": f"验证失败: {service} 仍在运行"}
            except Exception:
                pass

    # Generic success for other tools
    if success:
        return {"status": "success", "message": f"执行完成: {tool_name}"}

    return None


async def assess_impact(tool_name: str, tool_args: dict, session_id: str, send_to_client) -> str | None:
    """Pre-execution impact assessment for high-risk operations.

    Analyzes what might be affected by the operation and returns a human-readable summary.
    Called before requesting approval to give the user context.
    """
    impact_lines = []

    if tool_name == "kill_process":
        pid = tool_args.get("pid")
        if pid:
            import psutil
            try:
                proc = psutil.Process(pid)
                name = proc.name()
                children = proc.children(recursive=True)
                connections = proc.net_connections()

                impact_lines.append(f"目标进程: {name} (PID: {pid})")
                if children:
                    impact_lines.append(f"子进程数: {len(children)} (将一并终止)")
                if connections:
                    ports = [c.laddr.port for c in connections if c.status == 'LISTEN']
                    if ports:
                        impact_lines.append(f"监听端口: {ports} (终止后端口将不可用)")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    elif tool_name in ("restart_service", "stop_service"):
        service = tool_args.get("service")
        if service:
            import subprocess
            try:
                # Check what depends on this service
                result = subprocess.run(
                    ["systemctl", "list-dependencies", "--reverse", service],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    deps = [l.strip() for l in result.stdout.split("\n") if l.strip() and service not in l]
                    if deps:
                        impact_lines.append(f"依赖此服务的组件: {', '.join(deps[:5])}")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            action = "重启" if tool_name == "restart_service" else "停止"
            impact_lines.append(f"操作: {action} {service}")
            impact_lines.append("影响: 服务短暂不可用，相关连接将断开")

    elif tool_name == "delete_file":
        filepath = tool_args.get("filepath") or tool_args.get("path")
        if filepath:
            import subprocess
            # Check what references this file
            try:
                result = subprocess.run(
                    ["lsof", filepath],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    procs = set()
                    for line in result.stdout.strip().split("\n")[1:]:
                        parts = line.split()
                        if parts:
                            procs.add(parts[0])
                    if procs:
                        impact_lines.append(f"正在使用此文件的进程: {', '.join(procs)}")
                        impact_lines.append("删除后这些进程可能异常")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    if impact_lines:
        impact_text = "\n".join(impact_lines)
        await send_to_client({
            "type": "trace",
            "phase": "planning",
            "event_type": "start",
            "content": f"影响评估:\n{impact_text}",
        })
        return impact_text

    return None
