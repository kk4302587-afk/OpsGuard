"""LangGraph Agent workflow definition.

Uses LangGraph StateGraph to implement the Plan → Approve → Execute → Verify control loop.
Each node is a step in the reasoning pipeline, with full audit logging.
"""

import json
from typing import TypedDict, Annotated, Callable
from datetime import datetime

from loguru import logger
from langgraph.graph import StateGraph, END

from app.agent.llm import call_llm
from app.agent.trace_evidence import (
    build_evidence,
    inference_evidence,
    knowledge_evidence,
    tool_plan_evidence,
    tool_result_evidence,
    trace_event,
    verification_evidence,
)
from app.agent.tools_registry import tools_registry, RiskLevel
from app.safety.guardrail import SafetyGuardrail
from app.audit.logger import audit_logger, AuditPhase, AuditEventType
from app.incidents import store as incident_store
from app.knowledge.store import KnowledgeSearchError, knowledge_store


# === State Definition ===

class AgentState(TypedDict):
    """State maintained throughout the Agent workflow."""
    session_id: str
    incident_id: str
    user_message: str
    messages: list  # Conversation history (OpenAI format)
    final_response: str
    is_blocked: bool
    block_reason: str
    risk_warning: str
    knowledge_hint: str
    recent_changes_hint: str
    multimodal_hint: str
    multimodal_context: list[dict]
    current_turn_tool_count: int
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

    await send_to_client(trace_event(
        phase="safety_check",
        event_type="start",
        content="正在进行安全校验...",
        evidence=inference_evidence("User request is being checked by safety rules", "SafetyGuardrail", user_message[:200]),
    ))
    await audit_logger.log(session_id, AuditPhase.SAFETY_CHECK, AuditEventType.START, f"检查输入: {user_message[:100]}")

    safety_result = _guardrail.check_input(user_message)

    if not safety_result.is_safe:
        await audit_logger.log(session_id, AuditPhase.SAFETY_CHECK, AuditEventType.BLOCKED, f"输入被拦截: {safety_result.detail}")
        await send_to_client(trace_event(
            phase="safety_check",
            event_type="blocked",
            content=safety_result.detail,
            evidence=build_evidence(
                claim="安全护栏已拦截该请求",
                evidence_type="user input",
                source="SafetyGuardrail.check_input",
                observed=safety_result.detail,
                confidence="high",
                execution_state="failed",
                failure_reason=safety_result.detail,
            ),
        ))
        return {
            "is_blocked": True,
            "block_reason": safety_result.detail,
            "final_response": f"安全校验未通过: {safety_result.detail}\n\n您的请求被安全系统拦截。如果这是误判，请换一种方式描述您的需求。",
        }

    await audit_logger.log(session_id, AuditPhase.SAFETY_CHECK, AuditEventType.SUCCESS, "安全校验通过")
    await send_to_client(trace_event(
        phase="safety_check",
        event_type="success",
        content=f"安全校验通过 ({', '.join(safety_result.layers_checked)})",
        evidence=build_evidence(
            claim="安全护栏允许该请求继续执行",
            evidence_type="user input",
            source="SafetyGuardrail.check_input",
            observed={"layers_checked": safety_result.layers_checked},
            confidence="high",
            execution_state="executed",
        ),
    ))

    # Check high-risk intent
    risk_warning = ""
    intent_result = _guardrail.check_high_risk_intent(user_message)
    if intent_result.is_warning:
        risk_warning = f"\n\n系统检测到此请求涉及高风险操作（{intent_result.detail}）。请务必在执行前向用户确认，并详细说明影响范围。"
        await send_to_client(trace_event(
            phase="safety_check",
            event_type="start",
            content=f"高风险意图警告: {intent_result.detail}",
            evidence=inference_evidence("High-risk intent was detected", "SafetyGuardrail.check_high_risk_intent", intent_result.detail),
        ))

    return {"is_blocked": False, "risk_warning": risk_warning}


async def knowledge_retrieval_node(state: AgentState) -> dict:
    """Node 2: Retrieve relevant knowledge from history."""
    session_id = state["session_id"]
    user_message = state["user_message"]
    send_to_client = state["send_to_client"]

    await send_to_client(trace_event(
        phase="knowledge_retrieval",
        event_type="start",
        content="检索历史经验...",
        evidence=build_evidence(
            claim="正在检索历史经验",
            evidence_type="knowledge",
            source="knowledge_store.search",
            observed=user_message[:200],
            confidence="medium",
            execution_state="skipped",
        ),
    ))

    try:
        knowledge_context = await knowledge_store.search(user_message, limit=3)
    except KnowledgeSearchError as e:
        await send_to_client(trace_event(
            phase="knowledge_retrieval",
            event_type="failure",
            content=f"知识检索失败: {e}",
            evidence=knowledge_evidence(0, str(e), failed=True),
        ))
        await audit_logger.log(
            session_id,
            AuditPhase.KNOWLEDGE_RETRIEVAL,
            AuditEventType.FAILURE,
            "知识检索失败",
            {"error": str(e)},
        )
        return {"knowledge_hint": ""}

    knowledge_hint = ""
    if knowledge_context:
        knowledge_hint = "\n\n## 历史经验参考\n"
        for entry in knowledge_context:
            knowledge_hint += _format_knowledge_entry_for_prompt(entry)
        await send_to_client(trace_event(
            phase="knowledge_retrieval",
            event_type="success",
            content=_format_knowledge_entries_for_trace(knowledge_context),
            evidence=knowledge_evidence(
                len(knowledge_context),
                [
                    {
                        "problem": entry.get("problem_signature"),
                        "score": entry.get("match_score"),
                        "match_reason": entry.get("match_reason"),
                        "root_cause": entry.get("root_cause"),
                        "safe_to_reuse": entry.get("safe_to_reuse"),
                    }
                    for entry in knowledge_context
                ],
            ),
        ))
    else:
        await send_to_client(trace_event(
            phase="knowledge_retrieval",
            event_type="success",
            content="无相关历史经验",
            evidence=knowledge_evidence(0, "search completed; no matching entries"),
        ))

    return {"knowledge_hint": knowledge_hint}


async def recent_changes_node(state: AgentState) -> dict:
    """Node 3: Collect recent system changes as RCA evidence."""
    session_id = state["session_id"]
    send_to_client = state["send_to_client"]

    tool_def = tools_registry.get_tool("get_recent_changes")
    if not tool_def:
        await send_to_client(trace_event(
            phase="recent_changes",
            event_type="failure",
            content="最近变更检查不可用: get_recent_changes 工具未注册",
            evidence=build_evidence(
                claim="近期变更检查无法执行，因为工具未注册",
                evidence_type="command",
                source="get_recent_changes",
                observed="tool not registered",
                confidence="high",
                execution_state="failed",
                failure_reason="工具未注册",
            ),
        ))
        return {"recent_changes_hint": ""}

    await send_to_client(trace_event(
        phase="recent_changes",
        event_type="start",
        content="检查近期系统变更...",
    ))

    try:
        result = tool_def.function(window_hours=24, limit=30)
    except Exception as e:
        await send_to_client(trace_event(
            phase="recent_changes",
            event_type="failure",
            content=f"近期变更检查异常: {e}",
            evidence=build_evidence(
                claim="近期变更检查执行时出现异常",
                evidence_type="command",
                source="get_recent_changes",
                observed=str(e),
                confidence="high",
                execution_state="failed",
                failure_reason=str(e),
            ),
        ))
        await audit_logger.log(
            session_id,
            AuditPhase.PLANNING,
            AuditEventType.FAILURE,
            "近期变更检查异常",
            {"error": str(e)},
        )
        return {"recent_changes_hint": ""}

    result_success = bool(getattr(result, "success", True))
    result_data = getattr(result, "data", "")
    result_error = getattr(result, "error", None)

    if not result_success:
        await send_to_client(trace_event(
            phase="recent_changes",
            event_type="failure",
            content=f"近期变更检查失败: {result_error or 'success=False'}",
            evidence=tool_result_evidence(
                tool_name="get_recent_changes",
                tool_args={"window_hours": 24, "limit": 30},
                tool_def=tool_def,
                result=result,
            ),
        ))
        await audit_logger.log(
            session_id,
            AuditPhase.PLANNING,
            AuditEventType.FAILURE,
            "近期变更检查失败",
            {"error": result_error},
        )
        return {"recent_changes_hint": ""}

    from app.mcp_tools.recent_changes import compact_recent_changes

    trace_summary = (
        compact_recent_changes(result_data, max_changes=3, include_source_status=False)
        if isinstance(result_data, dict)
        else str(result_data)
    )
    prompt_summary = (
        compact_recent_changes(result_data, max_changes=8, include_source_status=True)
        if isinstance(result_data, dict)
        else str(result_data)
    )
    change_count = len(result_data.get("changes") or []) if isinstance(result_data, dict) else 0
    await send_to_client(trace_event(
        phase="recent_changes",
        event_type="success",
        content=trace_summary,
        evidence=build_evidence(
            claim=f"近期变更检查返回 {change_count} 条候选变更",
            evidence_type="command",
            source="get_recent_changes",
            observed=trace_summary,
            confidence="high",
            execution_state="executed",
        ),
    ))
    await audit_logger.log(
        session_id,
        AuditPhase.PLANNING,
        AuditEventType.SUCCESS,
        f"近期变更检查完成: {change_count} 条",
    )

    return {
        "recent_changes_hint": (
            "\n\n## 近期变更证据\n"
            "这些内容只能作为根因候选线索，不能在没有进一步验证时直接断定因果关系。\n"
            f"{prompt_summary}\n"
        )
    }


async def reasoning_node(state: AgentState) -> dict:
    """Node 4: LLM reasoning with tool calling loop."""
    session_id = state["session_id"]
    incident_id = state.get("incident_id", "")
    user_message = state["user_message"]
    send_to_client = state["send_to_client"]
    risk_warning = state.get("risk_warning", "")
    knowledge_hint = state.get("knowledge_hint", "")
    recent_changes_hint = state.get("recent_changes_hint", "")

    await send_to_client(trace_event(
        phase="planning",
        event_type="start",
        content="正在分析问题并制定方案...",
        evidence=inference_evidence("The agent is planning next checks or actions", "LLM", user_message[:200]),
    ))
    await audit_logger.log(session_id, AuditPhase.PLANNING, AuditEventType.START, "开始推理")

    # Build messages
    messages = list(state.get("messages", []))
    multimodal_hint = state.get("multimodal_hint", "")
    user_content = user_message + multimodal_hint + knowledge_hint + recent_changes_hint + risk_warning
    messages.append({"role": "user", "content": user_content})

    all_tools = tools_registry.get_all_tools_for_llm()
    max_iterations = 10
    iteration = 0
    write_tools_called = 0  # Count of WRITE/DESTRUCTIVE tools genuinely invoked this turn
    write_tools_succeeded = 0
    write_tool_failures: list[str] = []
    current_turn_tool_count = 0
    hallucination_retry_done = False  # Guard so we only retry once

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

                display_name = tool_def.display_name or tool_name
                if _is_read_only_intent(user_message) and tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                    block_reason = (
                        "用户本轮请求是只读查询，不能执行会改变系统状态的操作。"
                        f"已阻断工具: {display_name}"
                    )
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args, ensure_ascii=False)}}
                    ]})
                    messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": f"BLOCKED_READ_ONLY_INTENT: {block_reason}"})
                    await audit_logger.log(
                        session_id,
                        AuditPhase.TOOL_CALL,
                        AuditEventType.BLOCKED,
                        block_reason,
                        {"tool_name": tool_name, "tool_args": tool_args, "user_message": user_message},
                    )
                    await send_to_client(trace_event(
                        phase="tool_call",
                        event_type="blocked",
                        content=block_reason,
                        evidence=build_evidence(
                            claim=f"{display_name} 已被阻断：用户请求是只读查询",
                            evidence_type="user input",
                            source="read_only_intent_guard",
                            observed=user_message,
                            confidence="high",
                            execution_state="skipped",
                            failure_reason="只读意图不能触发写操作或破坏性工具",
                            next_check="请改用服务状态、日志、文件读取等只读工具。",
                        ),
                    ))
                    continue

                await send_to_client(trace_event(
                    phase="tool_call",
                    event_type="start",
                    content=f"准备调用工具：{display_name}\n参数：{json.dumps(tool_args, ensure_ascii=False)}",
                    evidence=tool_plan_evidence(tool_name, tool_args),
                ))
                await audit_logger.log(session_id, AuditPhase.TOOL_CALL, AuditEventType.START, f"工具调用: {tool_name}", {"args": tool_args})

                # Approval for write operations
                if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                    cmd_check = _guardrail.check_command(json.dumps(tool_args))
                    if not cmd_check.is_safe:
                        messages.append({"role": "assistant", "content": None, "tool_calls": [
                            {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                        ]})
                        messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": f"BLOCKED: {cmd_check.detail}"})
                        await send_to_client(trace_event(
                            phase="tool_call",
                            event_type="blocked",
                            content=f"被拦截: {cmd_check.detail}",
                            evidence=build_evidence(
                                claim=f"{display_name} 执行前被安全规则拦截",
                                evidence_type="command",
                                source="SafetyGuardrail.check_command",
                                observed=cmd_check.detail,
                                confidence="high",
                                execution_state="skipped",
                                failure_reason=cmd_check.detail,
                            ),
                        ))
                        continue

                    # Request approval
                    from app.websocket.approval import approval_manager
                    import asyncio as _asyncio

                    request_id = tool_call.get("id", "") or f"{tool_name}_{iteration}"
                    impact_text = await assess_impact(tool_name, tool_args, session_id, send_to_client)

                    loop = _asyncio.get_running_loop()
                    approval_future = loop.create_future()
                    approval_manager.register_pending(request_id, session_id, tool_name, tool_args, tool_def.risk_level, tool_def.description, approval_future)

                    supports_rollback, rollback_strategy = _effective_rollback_capability(tool_name, tool_args, tool_def)
                    await send_to_client({
                        "type": "approval_request",
                        "request_id": request_id,
                        "command": f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)})",
                        "risk_level": tool_def.risk_level,
                        "description": tool_def.description,
                        "impact": impact_text,
                        "rollback_strategy": rollback_strategy,
                        "supports_rollback": supports_rollback,
                        "preview_strategy": tool_def.preview_strategy,
                    })
                    await send_to_client(trace_event(
                        phase="approval_request",
                        event_type="pending",
                        content=f"等待用户审批: {display_name}",
                        evidence=build_evidence(
                            claim=f"{display_name} 需要用户审批后才能执行",
                            evidence_type="user input",
                            source="approval_manager",
                            observed=impact_text or tool_args,
                            confidence="high",
                            execution_state="skipped",
                        ),
                    ))

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
                        await send_to_client(trace_event(
                            phase="approval_response",
                            event_type="failure",
                            content="用户拒绝了操作",
                            evidence=build_evidence(
                                claim=f"{display_name} 因审批未通过或超时而未执行",
                                evidence_type="user input",
                                source="approval_manager",
                                observed="用户拒绝或审批超时",
                                confidence="high",
                                execution_state="skipped",
                                failure_reason="用户未批准该操作",
                            ),
                        ))
                        continue

                    await send_to_client(trace_event(
                        phase="approval_response",
                        event_type="success",
                        content="用户已批准",
                        evidence=build_evidence(
                            claim=f"{display_name} 已通过用户审批",
                            evidence_type="user input",
                            source="approval_manager",
                            observed="已批准",
                            confidence="high",
                            execution_state="executed",
                        ),
                    ))

                # Execute tool
                try:
                    # Backup before write
                    backup_record = None
                    before_change_state = None
                    if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                        from app.mcp_tools.backup import backup_manager
                        target_path = tool_args.get("filepath") or tool_args.get("path") or tool_args.get("service")
                        can_backup_rollback, rollback_strategy = _effective_rollback_capability(tool_name, tool_args, tool_def)
                        if can_backup_rollback and rollback_strategy == "backup" and target_path and isinstance(target_path, str):
                            backup_record = backup_manager.backup_file(target_path, operation=f"{tool_name}")
                            if backup_record:
                                await send_to_client(trace_event(
                                    phase="execution",
                                    event_type="start",
                                    content=f"已创建回滚备份：{target_path}",
                                    evidence=build_evidence(
                                        claim=f"{display_name} 执行前已创建回滚备份",
                                        evidence_type="config",
                                        source="BackupManager.backup_file",
                                        observed=target_path,
                                        confidence="high",
                                        execution_state="executed",
                                    ),
                                ))
                        before_change_state = _capture_pre_change_state(tool_name, tool_args, backup_record)

                    current_turn_tool_count += 1
                    result = tool_def.function(**tool_args)
                    result_success = bool(getattr(result, "success", True))
                    result_error = getattr(result, "error", None)
                    if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                        write_tools_called += 1
                        if result_success:
                            write_tools_succeeded += 1
                        else:
                            write_tool_failures.append(f"{tool_name}: {result_error or 'success=False'}")
                    tool_result_str = json.dumps(result.__dict__ if hasattr(result, '__dict__') else result, ensure_ascii=False, default=str)

                    # Post-action verification for write operations
                    if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE) and result_success:
                        verification = _verify_tool_result(tool_name, tool_args, result)
                        if verification:
                            await send_to_client(trace_event(
                                phase="verification",
                                event_type=verification["status"],
                                content=verification["message"],
                                evidence=verification_evidence(
                                    claim=f"{display_name} 执行后验证",
                                    source=tool_name,
                                    observed=verification["message"],
                                    success=verification["status"] == "success",
                                ),
                            ))

                        # Before/After change diff
                        change_diff = _capture_change_diff(tool_name, tool_args, backup_record, before_change_state)
                        if change_diff:
                            await send_to_client(trace_event(
                                phase="verification",
                                event_type="success",
                                content=f"变更对比:\n{change_diff}",
                                evidence=verification_evidence(
                                    claim=f"{display_name} 已记录执行前后对比",
                                    source=tool_name,
                                    observed=change_diff,
                                    success=True,
                                ),
                            ))

                    if result_success:
                        if backup_record:
                            await send_to_client(trace_event(
                                phase="execution",
                                event_type="success",
                                content=(
                                    "已创建回滚点：\n"
                                    f"- 回滚ID：{backup_record.get('id')}\n"
                                    f"- 目标：{backup_record.get('original_path')}\n"
                                    "- 策略：备份回滚\n"
                                    f"- 创建时间：{backup_record.get('timestamp')}\n"
                                    "- 恢复方式：使用 rollback_backup 或 /api/backups/{id}/rollback"
                                ),
                                evidence=build_evidence(
                                    claim=f"{display_name} 已创建可用回滚点",
                                    evidence_type="config",
                                    source="BackupManager.backup_file",
                                    observed=backup_record,
                                    confidence="high",
                                    execution_state="executed",
                                ),
                            ))
                        await audit_logger.log(session_id, AuditPhase.EXECUTION, AuditEventType.SUCCESS, f"工具执行成功: {tool_name}")
                        await send_to_client(trace_event(
                            phase="execution",
                            event_type="success",
                            content=f"执行成功: {display_name}",
                            evidence=tool_result_evidence(
                                tool_name=tool_name,
                                tool_args=tool_args,
                                tool_def=tool_def,
                                result=result,
                            ),
                        ))
                    else:
                        failure_message = result_error or "工具返回 success=False"
                        await audit_logger.log(session_id, AuditPhase.EXECUTION, AuditEventType.FAILURE, f"工具执行失败: {tool_name} - {failure_message}")
                        await send_to_client(trace_event(
                            phase="execution",
                            event_type="failure",
                            content=f"执行失败: {display_name} - {failure_message}",
                            evidence=tool_result_evidence(
                                tool_name=tool_name,
                                tool_args=tool_args,
                                tool_def=tool_def,
                                result=result,
                            ),
                        ))

                except Exception as e:
                    tool_result_str = json.dumps({"error": str(e)})
                    await send_to_client(trace_event(
                        phase="execution",
                        event_type="failure",
                        content=f"执行失败: {display_name} - {e}",
                        evidence=build_evidence(
                            claim=f"{display_name} 执行时发生异常",
                            evidence_type="command",
                            source=tool_name,
                            observed=str(e),
                            confidence="high",
                            execution_state="failed",
                            failure_reason=str(e),
                            next_check="请检查工具参数，并优先用只读检查确认状态后重试。",
                        ),
                    ))

                messages.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": tool_call.get("id", ""), "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                ]})
                messages.append({"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": tool_result_str})
        else:
            # LLM returned text only. Before accepting, check for hallucination:
            # if the response claims a write operation was completed but NO write tool
            # was actually called this turn, force one retry with an explicit reminder.
            final_content = llm_response.get("content", "") or ""
            has_write_completion_claim = _has_write_intent(user_message) and _claims_write_completion(final_content)
            if (
                write_tools_called == 0
                and has_write_completion_claim
            ):
                if hallucination_retry_done:
                    blocked_claim = final_content
                    final_content = (
                        "本次没有执行该写操作。\n\n"
                        "原因：系统检测到模型回复声称操作已完成，但本轮没有真实调用任何写操作工具，"
                        "因此已阻断该回复。\n\n"
                        "当前状态不能仅凭模型文字确认。请重新发起操作，系统会先展示影响评估并等待你确认；"
                        "只有工具返回成功后，才会回复执行成功。"
                    )
                    llm_response["content"] = final_content
                    await audit_logger.log(
                        session_id,
                        AuditPhase.RESPONSE,
                        AuditEventType.FAILURE,
                        "幻觉守卫: 重试后仍声称完成写操作，已阻断最终回复",
                        {"original_response": blocked_claim[:500]},
                    )
                    await send_to_client(trace_event(
                        phase="response",
                        event_type="failure",
                        content="已阻断未验证的写操作完成声明，改为安全回复",
                        evidence=build_evidence(
                            claim="模型再次声称写操作已完成，但没有真实执行证据",
                            evidence_type="user input",
                            source="write_completion_guard",
                            observed=blocked_claim[:500],
                            confidence="high",
                            execution_state="failed",
                            failure_reason="重试后仍没有调用写操作或破坏性工具",
                            next_check="请重新发起明确执行请求，以便系统走审批和真实工具执行。",
                        ),
                    ))
                    break
                hallucination_retry_done = True
                logger.warning(
                    f"Hallucination guard triggered: LLM claimed completion without "
                    f"invoking any tool. Response head: {final_content[:200]!r}"
                )
                await audit_logger.log(
                    session_id,
                    AuditPhase.RESPONSE,
                    AuditEventType.FAILURE,
                    "幻觉守卫: 模型声称完成写操作但未调用工具，强制重试",
                    {"original_response": final_content[:500]},
                )
                await send_to_client(trace_event(
                    phase="response",
                    event_type="failure",
                    content="⚠️ 检测到模型声称已完成写操作但未真实调用工具，已强制重新分析",
                    evidence=build_evidence(
                        claim="模型声称写操作已完成，但没有真实执行证据",
                        evidence_type="user input",
                        source="write_completion_guard",
                        observed=final_content[:500],
                        confidence="high",
                        execution_state="failed",
                        failure_reason="本轮没有调用写操作或破坏性工具",
                        next_check="请让智能体执行已审批工具，或明确撤回该完成声明。",
                    ),
                ))
                # Inject the assistant's bogus message so the LLM sees its own claim,
                # then a strong system-role correction.
                messages.append({"role": "assistant", "content": final_content})
                messages.append({
                    "role": "system",
                    "content": (
                        "[系统强制纠正] 你上一条回复中声称已完成某个写操作（如\"已重启\"、\"已删除\"、\"已清理\"），"
                        "但本轮你**没有调用任何工具**，操作根本没有发生。"
                        "请立即重新处理用户请求：\n"
                        "1) 如果用户确实需要执行该写操作，**立刻调用对应的工具**（如 start_service、restart_service、delete_file 等）来真正执行；\n"
                        "2) 如果你认为不应执行（参数不全、风险过高、用户其实在询问），明确告知用户'尚未执行 / 我不会执行'并说明原因，绝不要使用'已...'之类的完成措辞。\n"
                        "不允许再次仅用文字声称完成。"
                    ),
                })
                continue  # Retry one more LLM round with reminder in context
            if (
                write_tool_failures
                and write_tools_succeeded == 0
                and has_write_completion_claim
            ):
                failure_summary = "; ".join(write_tool_failures)[:500]
                if hallucination_retry_done:
                    blocked_claim = final_content
                    final_content = (
                        "本次写操作没有成功完成。\n\n"
                        f"真实工具返回失败：{failure_summary}\n\n"
                        "系统已阻断模型继续声称操作成功。请根据失败原因修复后重试；"
                        "只有写操作工具返回成功后，才会回复执行成功。"
                    )
                    llm_response["content"] = final_content
                    await audit_logger.log(
                        session_id,
                        AuditPhase.RESPONSE,
                        AuditEventType.FAILURE,
                        "幻觉守卫: 写操作失败后仍声称完成，已阻断最终回复",
                        {"tool_failures": write_tool_failures[:5], "original_response": blocked_claim[:500]},
                    )
                    await send_to_client(trace_event(
                        phase="response",
                        event_type="failure",
                        content="已阻断失败写操作的完成声明，改为安全回复",
                        evidence=build_evidence(
                            claim="写操作工具失败后，模型再次声称操作已完成",
                            evidence_type="command",
                            source="write_completion_guard",
                            observed=failure_summary,
                            confidence="high",
                            execution_state="failed",
                            failure_reason=failure_summary,
                            next_check="请先处理工具失败原因，再重试写操作。",
                        ),
                    ))
                    break
                hallucination_retry_done = True
                logger.warning(
                    f"Hallucination guard triggered: LLM claimed completion after "
                    f"failed write tools. Failures: {failure_summary!r}"
                )
                await audit_logger.log(
                    session_id,
                    AuditPhase.RESPONSE,
                    AuditEventType.FAILURE,
                    "幻觉守卫: 写工具返回失败但模型声称完成，强制重试",
                    {"tool_failures": write_tool_failures[:5], "original_response": final_content[:500]},
                )
                await send_to_client(trace_event(
                    phase="response",
                    event_type="failure",
                    content="⚠️ 写操作工具返回失败，但模型声称已完成，已强制重新分析",
                    evidence=build_evidence(
                        claim="写操作工具失败后，模型仍声称操作已完成",
                        evidence_type="command",
                        source="write_completion_guard",
                        observed=failure_summary,
                        confidence="high",
                        execution_state="failed",
                        failure_reason=failure_summary,
                        next_check="请基于失败工具输出重新生成回复，明确说明操作未成功。",
                    ),
                ))
                messages.append({"role": "assistant", "content": final_content})
                messages.append({
                    "role": "system",
                    "content": (
                        "[系统强制纠正] 本轮写操作工具已经真实调用，但返回失败："
                        f"{failure_summary}。你上一条回复却声称操作已完成。"
                        "请重新生成回复：必须明确说明操作未成功/未完成，并基于工具错误给出下一步建议；"
                        "除非再次调用工具并得到 success=True，否则绝不能说已完成。"
                    ),
                })
                continue
            break

    final_response = llm_response.get("content", "") or "分析完成，请查看推理链路了解详情。"
    await audit_logger.log(session_id, AuditPhase.RESPONSE, AuditEventType.SUCCESS, f"生成回复: {final_response[:200]}")
    await send_to_client(trace_event(
        phase="response",
        event_type="success",
        content="回复已生成",
        evidence=inference_evidence("Final response was generated from prior evidence and messages", "LLM", final_response[:500]),
    ))

    return {
        "final_response": final_response,
        "messages": messages,
        "iteration": iteration,
        "current_turn_tool_count": current_turn_tool_count,
    }


async def knowledge_save_node(state: AgentState) -> dict:
    """Node 4: Save knowledge and Runbook based on LLM-extracted semantic problem.

    The "problem signature" used for BOTH knowledge_entries and runbooks comes
    from an LLM extraction over the full conversation, NOT from raw user_message.
    This is because the current user_message can be just a confirmation
    (e.g. "继续", "执行", "好的，那就这样吧") whose real underlying problem was
    described in an earlier turn. The LLM is asked explicitly to look past such
    confirmations and identify the substantive problem.
    """
    session_id = state["session_id"]
    user_message = state["user_message"]
    final_response = state["final_response"]
    messages = state.get("messages", [])
    incident_id = state.get("incident_id", "")
    multimodal_context = state.get("multimodal_context", [])
    send_to_client = state["send_to_client"]

    # No tool was executed in the current turn → nothing actionable happened →
    # nothing to save. Historical tool messages in the same conversation must
    # not make a pure image/voice turn look like a verified resolution.
    if int(state.get("current_turn_tool_count", 0) or 0) <= 0:
        return {}

    # === Semantic extraction (single LLM call serves both knowledge and runbook) ===
    summary_data = await _extract_resolution_summary(messages, final_response)
    if not summary_data:
        logger.debug("Skip save: LLM extracted no substantive resolution")
        return {}

    problem = (summary_data.get("problem") or "").strip()
    diagnosis = (summary_data.get("diagnosis") or "").strip()
    solution = (summary_data.get("solution") or "").strip()

    if not problem or not solution:
        logger.debug(f"Skip save: incomplete extraction problem={problem!r}")
        return {}

    # === Knowledge entry ===
    try:
        multimodal_memory = _build_multimodal_memory(multimodal_context)
        evidence = list(summary_data.get("evidence") or [])
        applicability_conditions = list(summary_data.get("applicability_conditions") or [])
        if multimodal_memory:
            evidence.extend(multimodal_memory["evidence"])
            applicability_conditions.append("多模态识别结果仅作为辅助上下文，复用前必须重新执行真实 MCP 检查。")

        await knowledge_store.save_resolution(
            problem_signature=problem,
            diagnosis_path=diagnosis,
            solution=solution,
            tools_used=["agent"],
            incident_memory={
                "symptoms": summary_data.get("symptoms") or [],
                "root_cause": summary_data.get("root_cause") or "",
                "evidence": evidence,
                "successful_actions": summary_data.get("successful_actions") or [],
                "failed_attempts": summary_data.get("failed_attempts") or [],
                "validation_method": summary_data.get("validation_method") or "",
                "applicability_conditions": applicability_conditions,
                "non_applicability_conditions": summary_data.get("non_applicability_conditions") or [],
                "source_incident_id": incident_id,
                "confidence": summary_data.get("confidence") or "medium",
                "source_modalities": multimodal_memory.get("source_modalities", []) if multimodal_memory else ["real_tool_execution"],
                "multimodal_evidence": multimodal_memory.get("items", []) if multimodal_memory else [],
            },
        )
        await send_to_client({"type": "trace", "phase": "knowledge_save", "event_type": "success", "content": f"经验已保存: {problem[:30]}"})
    except Exception as e:
        logger.warning(f"Knowledge save failed: {e}")

    # === Runbook generation (uses the same semantic `problem` as its name) ===
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
            import aiosqlite
            from app.agent.runbook_governance import save_or_update_runbook
            from app.database import get_knowledge_db_path

            runbook_steps = []
            for tc in tool_call_sequence:
                td = tools_registry.get_tool(tc["tool_name"])
                if td:
                    runbook_steps.append({"tool_name": tc["tool_name"], "tool_args": tc["tool_args"], "description": td.description, "risk_level": td.risk_level.value})

            if runbook_steps:
                runbook_name = problem[:40]
                trigger_pattern = problem[:100]
                async with aiosqlite.connect(get_knowledge_db_path()) as db:
                    _, updated = await save_or_update_runbook(
                        db,
                        name=runbook_name,
                        description="auto generated",
                        trigger_pattern=trigger_pattern,
                        steps=runbook_steps,
                        session_id=session_id,
                    )
                    msg_text = f"Runbook {'updated' if updated else 'saved'}: {runbook_name}"
                await send_to_client({"type": "trace", "phase": "knowledge_save", "event_type": "success", "content": msg_text})
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

    # Set entry point
    workflow.set_entry_point("safety_check")

    # Add edges
    workflow.add_conditional_edges("safety_check", should_continue_after_safety)
    workflow.add_edge("knowledge_retrieval", "reasoning")
    workflow.add_edge("reasoning", END)

    return workflow.compile()


# Compiled graph instance
agent_graph = build_agent_graph()


# === Public API ===

async def _run_post_response_persistence(state: AgentState) -> None:
    """Persist learned knowledge/runbooks after the user-visible reply is ready."""
    try:
        await knowledge_save_node(state)
    except Exception as e:
        logger.warning(f"Post-response persistence failed: {e}")

async def run_agent(
    session_id: str,
    user_message: str,
    conversation_history: list[dict],
    send_to_client: Callable,
    multimodal_context: list[dict] | None = None,
) -> str:
    """Run the Agent pipeline using LangGraph.

    This is the main entry point called by the WebSocket handler.
    """
    incident_id = None
    original_send_to_client = send_to_client
    try:
        incident_id = await incident_store.create_incident(
            session_id=session_id,
            problem_statement=user_message,
            source="agent",
        )
    except Exception as e:
        logger.warning(f"Incident creation failed for session {session_id}: {e}")

    if incident_id:
        async def send_to_client(data: dict):
            try:
                await incident_store.record_incident_from_message(
                    incident_id=incident_id,
                    session_id=session_id,
                    message=data,
                )
            except Exception as e:
                logger.warning(f"Incident event recording failed for {incident_id}: {e}")
            await original_send_to_client(data)

    multimodal_context = multimodal_context or []
    if multimodal_context:
        from app.multimodal.provider import trace_events_from_context

        for event in trace_events_from_context(multimodal_context):
            await send_to_client(event)

    initial_state: AgentState = {
        "session_id": session_id,
        "incident_id": incident_id or "",
        "user_message": user_message,
        "messages": conversation_history,
        "final_response": "",
        "is_blocked": False,
        "block_reason": "",
        "risk_warning": "",
        "knowledge_hint": "",
        "recent_changes_hint": "",
        "multimodal_hint": _format_multimodal_context(multimodal_context),
        "multimodal_context": multimodal_context,
        "current_turn_tool_count": 0,
        "iteration": 0,
        "send_to_client": send_to_client,
    }

    try:
        # Run the graph
        final_state = await agent_graph.ainvoke(initial_state)
        final_response = final_state.get("final_response", "处理完成。")
        if incident_id:
            await incident_store.finalize_incident(
                incident_id=incident_id,
                final_summary=final_response,
                status="failed" if final_state.get("is_blocked") else None,
            )
            final_response = await incident_store.append_incident_reference(
                final_response,
                incident_id,
            )
        if (
            not final_state.get("is_blocked")
            and int(final_state.get("current_turn_tool_count", 0) or 0) > 0
        ):
            import asyncio

            persistence_state = dict(final_state)
            persistence_state["final_response"] = final_response
            asyncio.create_task(_run_post_response_persistence(persistence_state))
        return final_response
    except Exception as e:
        if incident_id:
            try:
                await incident_store.record_incident_event(
                    incident_id=incident_id,
                    session_id=session_id,
                    phase="error",
                    event_type="failure",
                    title="Agent execution failed",
                    detail=str(e),
                    evidence={
                        "claim": "Agent execution raised an exception",
                        "evidence_type": "command",
                        "source": "agent_graph",
                        "observed": str(e),
                        "confidence": "high",
                        "execution_state": "failed",
                        "failure_reason": str(e),
                    },
                )
                await incident_store.finalize_incident(
                    incident_id=incident_id,
                    final_summary=str(e),
                    status="failed",
                )
            except Exception as incident_error:
                logger.warning(f"Incident failure bookkeeping failed for {incident_id}: {incident_error}")
        raise


# === Helper Functions ===

def _format_knowledge_entry_for_prompt(entry: dict) -> str:
    """Format structured incident memory for LLM context."""
    lines = [
        f"- 问题: {entry.get('problem_signature')}",
        f"  匹配: {entry.get('match_reason') or '历史文本相似'} (score={entry.get('match_score')})",
    ]
    if entry.get("symptoms"):
        lines.append(f"  症状: {', '.join(map(str, entry.get('symptoms') or []))}")
    if entry.get("root_cause"):
        lines.append(f"  根因: {entry.get('root_cause')}")
    if entry.get("evidence"):
        evidence_preview = "; ".join(map(str, (entry.get("evidence") or [])[:3]))
        lines.append(f"  证据: {evidence_preview}")
    if entry.get("successful_actions"):
        actions_preview = "; ".join(map(str, (entry.get("successful_actions") or [])[:3]))
        lines.append(f"  有效动作: {actions_preview}")
    if entry.get("failed_attempts"):
        failed_preview = "; ".join(map(str, (entry.get("failed_attempts") or [])[:3]))
        lines.append(f"  无效尝试: {failed_preview}")
    if entry.get("validation_method"):
        lines.append(f"  验证方法: {entry.get('validation_method')}")
    if entry.get("applicability_conditions"):
        lines.append(f"  适用条件: {', '.join(map(str, entry.get('applicability_conditions') or []))}")
    if entry.get("non_applicability_conditions"):
        lines.append(f"  不适用条件: {', '.join(map(str, entry.get('non_applicability_conditions') or []))}")
    lines.append(
        "  复用安全性: "
        + ("可作为参考，但写操作仍需重新检查和审批" if entry.get("safe_to_reuse") else "仅供参考，不能直接复用写操作")
    )
    return "\n".join(lines) + "\n"


def _format_multimodal_context(items: list[dict] | None) -> str:
    """Format uploaded image/voice recognition as cautious Agent context."""
    if not items:
        return ""
    try:
        from app.multimodal.provider import build_multimodal_prompt_context

        return build_multimodal_prompt_context(items)
    except Exception as e:
        logger.warning(f"Failed to format multimodal context: {e}")
        return ""


def _build_multimodal_memory(items: list[dict] | None) -> dict:
    """Build source-marked auxiliary memory from multimodal input.

    This helper is called only after ``knowledge_save_node`` has already found
    real tool evidence in the turn. Image/voice recognition alone must never
    create a knowledge entry.
    """
    if not items:
        return {}

    memory_items: list[dict] = []
    evidence: list[str] = []
    source_modalities = {"real_tool_execution"}
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        input_type = item.get("input_type") or item.get("type") or "unknown"
        source = "image_recognition" if input_type == "image" else "voice_transcription" if input_type == "audio" else "multimodal_recognition"
        source_modalities.add(source)
        summary = (
            item.get("summary")
            or item.get("normalized_transcript")
            or item.get("extracted_text")
            or item.get("raw_transcript")
            or ""
        )
        entities = item.get("entities") if isinstance(item.get("entities"), dict) else {}
        memory_items.append({
            "source": source,
            "input_type": input_type,
            "summary": str(summary)[:500],
            "confidence": item.get("confidence") or "medium",
            "entities": entities,
        })
        if summary:
            evidence.append(f"来源:{source}；识别摘要:{str(summary)[:300]}")

    if not memory_items:
        return {}
    return {
        "source_modalities": sorted(source_modalities),
        "items": memory_items,
        "evidence": evidence,
    }


def _format_knowledge_entries_for_trace(entries: list[dict]) -> str:
    """Format knowledge hits for the trace panel."""
    lines = [f"找到 {len(entries)} 条相关事故记忆"]
    for entry in entries:
        lines.append(
            f"- {entry.get('problem_signature')} "
            f"(score={entry.get('match_score')}, safe_to_reuse={entry.get('safe_to_reuse')})"
        )
        if entry.get("match_reason"):
            lines.append(f"  match_reason: {entry.get('match_reason')}")
        if entry.get("root_cause"):
            lines.append(f"  root_cause: {entry.get('root_cause')}")
        if entry.get("validation_method"):
            lines.append(f"  validation: {entry.get('validation_method')}")
    return "\n".join(lines)


def _preview_strategy_label(strategy: str) -> str:
    """Return a Chinese label for an internal preview strategy."""
    return {
        "impact_only": "仅影响评估（不会执行预演命令）",
        "check_mode": "检查模式",
        "diff": "差异对比",
        "dry_run": "预演执行",
        "none": "无预览",
    }.get(strategy or "none", strategy or "无预览")


def _rollback_strategy_label(strategy: str) -> str:
    """Return a Chinese label for an internal rollback strategy."""
    return {
        "backup": "备份回滚",
        "manual": "手动回滚",
        "inverse_action": "反向操作",
        "none": "无可靠自动回滚",
    }.get(strategy or "none", strategy or "无可靠自动回滚")


def _effective_rollback_capability(tool_name: str, tool_args: dict, tool_def) -> tuple[bool, str]:
    """Return rollback capability that can be truthfully claimed before approval."""
    if not tool_def or not tool_def.supports_rollback or tool_def.rollback_strategy != "backup":
        return False, "none"

    path_value = tool_args.get("filepath") or tool_args.get("path")
    if tool_name == "create_file":
        if not tool_args.get("overwrite") or not path_value:
            return False, "none"
        from pathlib import Path

        path = Path(str(path_value))
        if path.exists() and path.is_file():
            return True, tool_def.rollback_strategy
        return False, "none"

    if tool_name in {"write_file", "delete_file", "change_permissions"} and path_value:
        from pathlib import Path

        path = Path(str(path_value))
        if path.exists() and path.is_file():
            return True, tool_def.rollback_strategy
        return False, "none"

    # No complete inverse action or reliable directory/ownership restore exists yet.
    return False, "none"


async def assess_impact(tool_name: str, tool_args: dict, session_id: str, send_to_client) -> str | None:
    """Pre-execution impact assessment for high-risk operations."""
    impact_lines = []
    tool_def = tools_registry.get_tool(tool_name)
    target = (
        tool_args.get("filepath")
        or tool_args.get("dirpath")
        or tool_args.get("path")
        or tool_args.get("service")
        or tool_args.get("pid")
        or tool_args.get("username")
        or tool_args.get("backup_id")
        or "当前系统"
    )
    display_name = tool_def.display_name if tool_def and tool_def.display_name else tool_name
    impact_lines.append(f"目标：{target}")

    if tool_name == "kill_process":
        import psutil
        pid = tool_args.get("pid")
        if pid:
            try:
                proc = psutil.Process(pid)
                children = proc.children(recursive=True)
                connections = proc.net_connections()
                impact_lines.append(f"操作：终止进程 {proc.name()} (PID: {pid})")
                if children:
                    impact_lines.append(f"影响：同时可能影响 {len(children)} 个子进程")
                if connections:
                    ports = [c.laddr.port for c in connections if c.status == 'LISTEN']
                    if ports:
                        impact_lines.append(f"影响：监听端口可能关闭 {ports}")
            except Exception:
                impact_lines.append(f"操作：终止进程 PID {pid}")

    elif tool_name in ("restart_service", "start_service", "stop_service"):
        service = tool_args.get("service")
        if service:
            action_map = {
                "restart_service": "重启",
                "start_service": "启动",
                "stop_service": "停止",
            }
            action = action_map.get(tool_name, "操作")
            impact_lines.append(f"操作：{action}服务 {service}")
            impact_lines.append("影响：服务当前状态会被改变，可能造成短暂中断或连接重建")

    elif tool_name == "rollback_backup":
        impact_lines.append("操作：恢复历史备份")
        impact_lines.append("影响：目标文件或目录会被所选备份覆盖")

    elif tool_name in (
        "create_file",
        "create_directory",
        "write_file",
        "delete_file",
        "delete_directory",
        "move_file",
        "copy_file",
        "change_permissions",
        "change_owner",
    ):
        impact_lines.append(f"操作：{display_name}")
        impact_lines.append("影响：文件系统内容或元数据可能发生变化")

    if tool_def:
        impact_lines.append(f"预览：{_preview_strategy_label(tool_def.preview_strategy)}")
        supports_rollback, rollback_strategy = _effective_rollback_capability(tool_name, tool_args, tool_def)
        if supports_rollback:
            impact_lines.append(f"回滚：支持{_rollback_strategy_label(rollback_strategy)}，备份创建成功后可信度较高")
        else:
            impact_lines.append("回滚：无可靠自动回滚，本次不会声称可自动恢复")
        impact_lines.append(f"验证：支持时会在执行后检查 {display_name} 的结果")

    if impact_lines:
        impact_text = "\n".join(dict.fromkeys(impact_lines))
        await send_to_client(trace_event(
            phase="planning",
            event_type="start",
            content=f"影响评估：\n{impact_text}",
            evidence=build_evidence(
                claim=f"已评估 {display_name} 的操作影响",
                evidence_type="user input",
                source="assess_impact",
                observed=impact_text,
                confidence="medium",
                execution_state="inferred",
            ),
        ))
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

    elif tool_name in ("restart_service", "start_service"):
        import subprocess
        service = tool_args.get("service")
        if service:
            try:
                check = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=5)
                if check.stdout.strip() == "active":
                    action = "已重启并运行中" if tool_name == "restart_service" else "已启动并运行中"
                    return {"status": "success", "message": f"验证通过: {service} {action}"}
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

    elif tool_name == "create_directory":
        from pathlib import Path

        dirpath = tool_args.get("dirpath")
        if dirpath:
            path = Path(str(dirpath))
            if path.exists() and path.is_dir():
                return {"status": "success", "message": f"验证通过: 目录已创建 {dirpath}"}
            return {"status": "failure", "message": f"验证失败: 目录不存在 {dirpath}"}

    if success:
        return {"status": "success", "message": f"执行完成: {tool_name}"}
    return None


def _capture_pre_change_state(tool_name: str, tool_args: dict, backup_record: dict | None) -> dict | None:
    """Capture live state immediately before executing a write operation."""
    if tool_name == "kill_process":
        import psutil
        pid = tool_args.get("pid")
        if pid:
            return {"pid_exists": psutil.pid_exists(pid)}

    if tool_name in ("restart_service", "start_service", "stop_service"):
        service = tool_args.get("service")
        if service:
            return {"service_state": _get_service_active_state(service)}

    if tool_name in {"create_file", "create_directory"}:
        from pathlib import Path

        target_path = tool_args.get("filepath") or tool_args.get("dirpath")
        if target_path:
            path = Path(str(target_path))
            return {
                "path": str(path),
                "exists": path.exists(),
                "is_file": path.is_file(),
                "is_dir": path.is_dir(),
                "size": path.stat().st_size if path.exists() and path.is_file() else None,
            }

    if backup_record:
        return {"backup_record": backup_record}

    return None


def _get_service_active_state(service: str) -> str:
    """Return the current systemd active state for a service."""
    import subprocess

    try:
        check = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            timeout=5,
        )
        state = check.stdout.strip() or check.stderr.strip()
        return state or f"unknown(rc={check.returncode})"
    except Exception as e:
        return f"unknown({e})"


def _capture_change_diff(
    tool_name: str,
    tool_args: dict,
    backup_record: dict | None,
    before_state: dict | None = None,
) -> str | None:
    """Capture before/after diff for write operations."""
    diff_lines = []
    before_state = before_state or {}

    if tool_name == "kill_process":
        import psutil
        pid = tool_args.get("pid")
        if pid:
            before_exists = before_state.get("pid_exists")
            exists = psutil.pid_exists(pid)
            before_text = "运行中" if before_exists else "不存在"
            diff_lines.append(f"[Before] PID {pid}: {before_text}")
            diff_lines.append(f"[After]  PID {pid}: {'仍在运行' if exists else '已终止'}")

    elif tool_name in ("restart_service", "start_service", "stop_service"):
        service = tool_args.get("service")
        if service:
            before_service_state = before_state.get("service_state", "unknown")
            after_service_state = _get_service_active_state(service)
            diff_lines.append(f"[Before] {service}: {before_service_state}")
            diff_lines.append(f"[After]  {service}: {after_service_state}")

    elif tool_name in {"create_file", "create_directory"}:
        from pathlib import Path

        target_path = tool_args.get("filepath") or tool_args.get("dirpath")
        if target_path:
            path = Path(str(target_path))
            before_exists = before_state.get("exists", False)
            after_exists = path.exists()
            target_label = "目录" if tool_name == "create_directory" else "文件"
            diff_lines.append(f"[Before] {target_label}存在: {'是' if before_exists else '否'}")
            diff_lines.append(f"[After]  {target_label}存在: {'是' if after_exists else '否'}")
            if after_exists and path.is_file():
                before_size = before_state.get("size")
                after_size = path.stat().st_size
                diff_lines.append(f"[Before] 文件大小: {before_size if before_size is not None else 0} bytes")
                diff_lines.append(f"[After]  文件大小: {after_size} bytes")

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


def _format_conversation_excerpt(messages: list, final_response: str, max_chars: int = 2500) -> str:
    """Render the agent turn into compact text for an extraction LLM prompt.

    Includes user / assistant / tool_call / tool_result rows in chronological
    order. Truncated to `max_chars` keeping the tail (most recent context).
    """
    lines = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user" and isinstance(content, str) and content.strip():
            lines.append(f"[用户]: {content.strip()[:300]}")
        elif role == "assistant" and isinstance(content, str) and content.strip():
            lines.append(f"[助手]: {content.strip()[:300]}")
        elif role == "assistant" and msg.get("tool_calls"):
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and "function" in tc:
                    fn = tc["function"]
                    args = fn.get("arguments", "")
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    lines.append(f"[助手→工具]: {fn.get('name', '')}({args[:120]})")
        elif role == "tool" and isinstance(content, str):
            lines.append(f"[工具返回]: {content[:200]}")

    if final_response:
        lines.append(f"[助手最终回复]: {final_response[:500]}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = "...（前面对话省略）...\n" + text[-(max_chars - 50):]
    return text


async def _extract_resolution_summary(messages: list, final_response: str) -> dict | None:
    """Ask the LLM to semantically extract problem / diagnosis / solution.

    The LLM is given the full conversation excerpt so it can correctly identify
    the underlying problem even when the latest user message is just a
    confirmation like "继续" or "执行". This replaces the older keyword/length
    based filter, which was brittle and produced both false positives and
    false negatives.

    Returns:
        dict with keys problem / diagnosis / solution, or None if the LLM
        cannot identify a substantive resolution (which also covers the case
        where the latest user message was a content-free confirmation with no
        preceding problem in the conversation).
    """
    import re

    excerpt = _format_conversation_excerpt(messages, final_response)
    summary_messages = [
        {
            "role": "system",
            "content": (
                "你是运维知识提取器。任务：从一轮 Agent 对话中提取出用户真正想解决的运维问题、"
                "诊断步骤和解决方案。\n\n"
                "**关键规则**：\n"
                "1. 用户的最后一条消息可能只是确认/继续语（如\"继续\"、\"执行\"、\"好的\"、\"那就这样\"）。"
                "**绝不能**把这种确认作为 problem。真正的 problem 应当从更早的用户消息或助手的方案提议中识别。\n"
                "2. problem 字段必须是**自包含**的问题描述（其他人单独看到这个 problem 就能理解），"
                "例如\"重启 nginx 服务\"、\"清理 /tmp 下的大文件\"、\"诊断磁盘空间不足\"。"
                "禁止使用\"继续\"、\"执行\"、\"完成上一步\"等依赖上下文的指代词。\n"
                "3. 如果对话没有形成实质性的问题解决（用户只是闲聊；agent 没真正调用任何工具；"
                "或问题不明确无法概括），回复字符串 null。\n\n"
                "4. 结构化字段只能来自对话和真实工具结果；如果没有证据，字段用空数组或空字符串，不要编造。\n"
                "5. 历史写操作不能被描述为可直接复用；必须强调需要 fresh check 和审批。\n\n"
                "严格按以下 JSON 之一回复，不要任何解释文字：\n"
                "{"
                "\"problem\": \"自包含的问题简述(≤30字)\", "
                "\"diagnosis\": \"诊断步骤简述\", "
                "\"solution\": \"解决方案简述\", "
                "\"symptoms\": [\"症状/告警/用户可观察现象\"], "
                "\"root_cause\": \"已确认根因；未确认则为空字符串\", "
                "\"evidence\": [\"真实工具输出/日志/状态/配置证据摘要\"], "
                "\"successful_actions\": [\"成功执行且有工具结果支持的动作\"], "
                "\"failed_attempts\": [\"失败或被拒绝/跳过的尝试\"], "
                "\"validation_method\": \"如何验证已解决；未验证则为空字符串\", "
                "\"applicability_conditions\": [\"什么条件下可参考该经验\"], "
                "\"non_applicability_conditions\": [\"什么条件下不应复用\"], "
                "\"confidence\": \"low|medium|high\""
                "}\n"
                "或：\n"
                "null"
            ),
        },
        {"role": "user", "content": f"对话记录：\n{excerpt}"},
    ]

    try:
        summary_response = await call_llm(summary_messages)
        summary_text = (summary_response.get("content") or "").strip()

        if not summary_text or summary_text.lower() in ("null", "none", '"null"'):
            return None

        json_match = re.search(r'\{.*\}', summary_text, re.DOTALL)
        if not json_match:
            return None

        data = json.loads(json_match.group())
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        logger.warning(f"Resolution summary extraction failed: {e}")
        return None


# Patterns that indicate the LLM is claiming a WRITE operation was completed.
# Read-only / analysis verbs (检查, 分析, 识别, 看到) are intentionally NOT here
# because legitimate text-only responses use them.
_WRITE_COMPLETION_PATTERNS = (
    "已重启", "已成功重启", "重启完成", "重启成功", "已为您重启",
    "已停止", "已成功停止", "停止完成", "已为您停止",
    "已启动", "已成功启动", "启动完成",
    "已删除", "已成功删除", "删除完成", "已清除", "已为您删除", "已为您清除",
    "已清理", "清理完成", "已成功清理", "已为您清理",
    "已 kill", "已终止", "已杀死", "已为您终止",
    "已修改", "已更新", "已写入", "已应用", "已保存", "已成功修改",
    "已执行完毕", "已为您执行", "已为你执行", "执行完毕", "已经执行",
    "已添加", "已创建", "创建完成", "已成功创建", "已新建", "新建完成", "已配置", "已开启", "已关闭", "已禁用", "已启用",
    "已安装", "安装完成", "已卸载", "卸载完成",
    "started", "start completed", "restarted", "restart completed", "created", "create completed",
    "stopped", "stop completed", "deleted", "delete completed",
    "removed", "remove completed", "modified", "updated", "saved",
    "installed", "install completed", "uninstalled", "uninstall completed",
)


_WRITE_INTENT_PATTERNS = (
    # Explicit operation requests. Keep the gap short so read-only phrases like
    # "请查看 nginx 配置文件" do not treat the noun "配置" as a write verb.
    r"(帮我|请|执行|开始|给我|把|将|立即|现在|麻烦).{0,4}(启动|重启|停止|关闭|删除|清理|修改|写入|保存|应用|启用|禁用|添加|创建|新建|安装|卸载|配置)",
    # Bare imperative-style operation plus an object.
    r"^(启动|重启|停止|关闭|删除|清理|修改|写入|保存|应用|启用|禁用|添加|创建|新建|安装|卸载|配置).{0,30}(服务|进程|文件|目录|配置|端口|用户|软件|包|规则|权限|nginx|mysql|redis|apache|systemd)",
    # Location-first creation requests such as "在 /tmp 下创建 test 目录".
    r"(在|到|向).{0,60}(创建|新建).{0,30}(文件夹|目录|文件)",
    # Mixed read-then-write requests such as "检查并重启 nginx".
    r"(并|然后|之后|后).{0,6}(启动|重启|停止|关闭|删除|清理|修改|写入|保存|应用|启用|禁用|添加|创建|新建|安装|卸载|配置)",
    # English operation requests.
    r"\b(start|restart|stop|delete|remove|clean|modify|write|save|apply|enable|disable|create|install|uninstall)\b.{0,40}\b(service|process|file|directory|config|port|user|package|nginx|mysql|redis|apache)\b",
    # Follow-up confirmations after the assistant proposed a write operation.
    r"^(执行|确认|确定|批准|同意|开始执行|继续|好的|好|可以|那就这样|那就执行)$",
)


_READ_ONLY_INTENT_PATTERNS = (
    r"(查看|查询|读取|获取|检查|检测|分析|列出|显示|搜索|检索|生成.*报告|当前状态|状态|是否运行|是否启动|健康|日志|配置|文件内容)",
    r"\b(show|status|get|check|inspect|read|list|view|query|search|describe|report)\b",
)


def _has_write_intent(text: str) -> bool:
    """Return whether the current user message is likely requesting a write."""
    if not text:
        return False

    import re

    normalized = text.strip()
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _WRITE_INTENT_PATTERNS)


def _is_read_only_intent(text: str) -> bool:
    """Return whether the latest user turn is clearly asking for observation only."""
    if not text or _has_write_intent(text):
        return False

    import re

    normalized = text.strip()
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _READ_ONLY_INTENT_PATTERNS)


def _claims_write_completion(text: str) -> bool:
    """Detect whether an LLM text response claims a write operation was completed.

    Used as a hallucination guard: if the LLM produced this kind of claim without
    actually invoking any tool, the agent must re-prompt itself to either really
    call the tool or retract the false claim.
    """
    if not text:
        return False
    return any(pat in text for pat in _WRITE_COMPLETION_PATTERNS)
