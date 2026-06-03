"""LangGraph Agent workflow definition.

Uses LangGraph StateGraph to implement the Plan → Approve → Execute → Verify control loop.
Each node is a step in the reasoning pipeline, with full audit logging.
"""

import json
import re
import uuid
from typing import TypedDict, Annotated, Callable
from datetime import datetime
from pathlib import Path

from loguru import logger
from langgraph.graph import StateGraph, END

from app.agent.llm import call_llm
from app.agent.context_manager import build_context_package
from app.agent.trace_evidence import (
    build_evidence,
    compact_observed,
    inference_evidence,
    knowledge_evidence,
    tool_plan_evidence,
    tool_result_evidence,
    trace_event,
    verification_evidence,
)
from app.agent.final_response import (
    generate_structured_final_reply,
    make_tool_ledger_entry,
    render_conservative_reply,
)
from app.agent.tool_executor import execute_tool, get_tools_for_llm
from app.agent.tool_execution_store import (
    record_tool_execution,
)
from app.agent.tools_registry import tools_registry, RiskLevel
from app.agent.execution_policy import evaluate_tool_policy, policy_summary
from app.agent.operation_preview import build_operation_preview
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
    current_turn_tool_ledger: list[dict]
    structured_validation_result: dict
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
            evidence=knowledge_evidence(0, "检索完成，未找到匹配经验"),
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
        result = await execute_tool("get_recent_changes", {"window_hours": 24, "limit": 30}, tool_def)
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
    history_recall_intent = _is_history_recall_intent(user_message)

    await send_to_client(trace_event(
        phase="planning",
        event_type="start",
        content="正在分析问题并制定方案...",
        evidence=inference_evidence("智能体正在规划下一步检查或操作", "LLM", user_message[:200]),
    ))
    await audit_logger.log(session_id, AuditPhase.PLANNING, AuditEventType.START, "开始推理")

    tool_call_explanation = _render_tool_call_explanation_request(user_message)
    if tool_call_explanation:
        await audit_logger.log(
            session_id,
            AuditPhase.RESPONSE,
            AuditEventType.SUCCESS,
            "解释工具调用，未执行工具",
        )
        await send_to_client(trace_event(
            phase="response",
            event_type="success",
            content="已解释工具调用，未执行任何操作",
            evidence=build_evidence(
                claim="用户请求解释工具调用，系统仅基于工具注册表生成说明",
                evidence_type="config",
                source="tools_registry",
                observed=user_message[:500],
                confidence="high",
                execution_state="skipped",
            ),
        ))
        return {
            "final_response": tool_call_explanation,
            "messages": list(state.get("messages", [])) + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": tool_call_explanation},
            ],
            "iteration": 0,
            "current_turn_tool_count": 0,
            "is_blocked": False,
            "block_reason": "",
        }

    fresh_read_plan = _fresh_read_tool_plan(user_message)

    multimodal_hint = state.get("multimodal_hint", "")
    fresh_evidence_hint = _format_fresh_evidence_requirement(user_message, fresh_read_plan)
    context_package = await build_context_package(
        session_id=session_id,
        user_message=user_message,
        conversation_history=state.get("messages", []),
        knowledge_hint=knowledge_hint,
        recent_changes_hint=recent_changes_hint,
        multimodal_hint=multimodal_hint,
        fresh_evidence_hint=fresh_evidence_hint,
        include_recent_tool_ledger=history_recall_intent,
        llm_call=call_llm,
    )
    messages = context_package.messages
    if risk_warning:
        messages[-1]["content"] = messages[-1]["content"] + risk_warning
    await send_to_client(trace_event(
        phase="context_management",
        event_type="success",
        content=(
            "上下文已按层级和预算注入："
            f"最近消息 {len(messages) - 1} 条，"
            f"会话摘要 {'已使用' if context_package.session_summary else '无'}，"
            f"历史工具账本 {'已注入' if context_package.recent_tool_ledger_hint else '未注入'}"
        ),
        evidence=build_evidence(
            claim="Agent prompt 已应用 Context Management 2.0 分层和预算规则",
            evidence_type="config",
            source="context_manager.build_context_package",
            observed={
                "layers": [
                    {"name": layer.name, "state_label": layer.state_label, "chars": len(layer.content or "")}
                    for layer in context_package.layers
                ],
                "message_count": len(messages),
            },
            confidence="high",
            execution_state="executed",
        ),
    ))

    all_tools = await get_tools_for_llm()
    max_iterations = 10
    iteration = 0
    current_turn_tool_count = 0
    guard_blocked_final_response = False
    approval_rejected_final_response = False
    tool_ledger_this_turn: list[dict] = []
    policy_compiled_tool_used = False

    if fresh_read_plan:
        preflight = await _execute_forced_read_tools(
            session_id=session_id,
            incident_id=incident_id,
            plan=fresh_read_plan,
            messages=messages,
            send_to_client=send_to_client,
        )
        current_turn_tool_count += preflight["tool_count"]
        tool_ledger_this_turn.extend(preflight.get("tool_ledger", []))

    while iteration < max_iterations:
        iteration += 1
        llm_response = await call_llm(messages, tools=all_tools)

        if not llm_response["tool_calls"] and not policy_compiled_tool_used:
            compiled_tool_call = _compile_deterministic_tool_call(user_message)
            if compiled_tool_call:
                if _has_equivalent_tool_call(tool_ledger_this_turn, compiled_tool_call["name"], compiled_tool_call["arguments"]):
                    policy_compiled_tool_used = True
                    await send_to_client(trace_event(
                        phase="planning",
                        event_type="success",
                        content=(
                            "策略层检测到本轮已处理等价工具调用，跳过重复补全："
                            f"{compiled_tool_call['name']}({json.dumps(compiled_tool_call['arguments'], ensure_ascii=False)})"
                        ),
                        evidence=build_evidence(
                            claim="后端策略层已跳过重复的确定性工具调用",
                            evidence_type="command",
                            source="intent_policy_compiler",
                            observed={
                                "tool_name": compiled_tool_call["name"],
                                "tool_args": compiled_tool_call["arguments"],
                            },
                            confidence="high",
                            execution_state="skipped",
                        ),
                    ))
                else:
                    policy_compiled_tool_used = True
                    llm_response["tool_calls"] = [compiled_tool_call]
                    await send_to_client(trace_event(
                        phase="planning",
                        event_type="success",
                        content=(
                            "策略层已将高确定性运维意图编译为工具调用："
                            f"{compiled_tool_call['name']}({json.dumps(compiled_tool_call['arguments'], ensure_ascii=False)})"
                        ),
                        evidence=build_evidence(
                            claim="后端策略层已补全模型未发起的必要工具调用",
                            evidence_type="user input",
                            source="intent_policy_compiler",
                            observed={
                                "user_message": user_message,
                                "tool_name": compiled_tool_call["name"],
                                "tool_args": compiled_tool_call["arguments"],
                            },
                            confidence="high",
                            execution_state="inferred",
                        ),
                    ))

        if llm_response["tool_calls"]:
            for tool_call in llm_response["tool_calls"]:
                tool_name = tool_call["name"]
                tool_args = tool_call["arguments"]
                tool_def = tools_registry.get_tool(tool_name)
                call_id = tool_call.get("id", "") or f"{tool_name}_{iteration}_{uuid.uuid4().hex[:8]}"

                if not tool_def:
                    error_text = f"Unknown tool '{tool_name}'"
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                    ]})
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": f"Error: {error_text}"})
                    ledger_entry = make_tool_ledger_entry(
                        call_id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level="unknown",
                        status="failure",
                        result={"success": False, "data": "", "error": error_text},
                        error=error_text,
                        execution_state="failed",
                        approval_granted=False,
                    )
                    tool_ledger_this_turn.append(ledger_entry)
                    await record_tool_execution(
                        session_id=session_id,
                        incident_id=incident_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level="unknown",
                        status="failure",
                        result={"success": False, "data": "", "error": error_text},
                        error=error_text,
                        execution_state="failed",
                        approval_granted=False,
                    )
                    continue

                display_name = tool_def.display_name or tool_name
                if _is_read_only_intent(user_message) and tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                    block_reason = (
                        "用户本轮请求是只读查询，不能执行会改变系统状态的操作。"
                        f"已阻断工具: {display_name}"
                    )
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args, ensure_ascii=False)}}
                    ]})
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": f"BLOCKED_READ_ONLY_INTENT: {block_reason}"})
                    ledger_entry = make_tool_ledger_entry(
                        call_id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level=tool_def.risk_level.value,
                        status="blocked",
                        result={"success": False, "data": "", "error": block_reason},
                        error=block_reason,
                        execution_state="skipped",
                        approval_granted=False,
                    )
                    tool_ledger_this_turn.append(ledger_entry)
                    await record_tool_execution(
                        session_id=session_id,
                        incident_id=incident_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level=tool_def.risk_level.value,
                        status="blocked",
                        result={"success": False, "data": "", "error": block_reason},
                        error=block_reason,
                        execution_state="skipped",
                        approval_granted=False,
                    )
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

                if _is_noop_create_directory(tool_name, tool_args):
                    noop_message = f"目录已存在，无需创建: {tool_args.get('dirpath')}"
                    messages.append({"role": "assistant", "content": None, "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args, ensure_ascii=False)}}
                    ]})
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": f"SKIPPED_NOOP: {noop_message}"})
                    await send_to_client(trace_event(
                        phase="tool_call",
                        event_type="success",
                        content=f"跳过无需执行的目录创建：{tool_args.get('dirpath')}",
                        evidence=build_evidence(
                            claim="目标目录已经存在，create_directory 不需要审批或执行",
                            evidence_type="config",
                            source="noop_write_guard",
                            observed=tool_args,
                            confidence="high",
                            execution_state="skipped",
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
                            {"id": call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                        ]})
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": f"BLOCKED: {cmd_check.detail}"})
                        ledger_entry = make_tool_ledger_entry(
                            call_id=call_id,
                            tool_name=tool_name,
                            tool_args=tool_args,
                            risk_level=tool_def.risk_level.value,
                            status="blocked",
                            result={"success": False, "data": "", "error": cmd_check.detail},
                            error=cmd_check.detail,
                            execution_state="skipped",
                            approval_granted=False,
                        )
                        tool_ledger_this_turn.append(ledger_entry)
                        await record_tool_execution(
                            session_id=session_id,
                            incident_id=incident_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            tool_args=tool_args,
                            risk_level=tool_def.risk_level.value,
                            status="blocked",
                            result={"success": False, "data": "", "error": cmd_check.detail},
                            error=cmd_check.detail,
                            execution_state="skipped",
                            approval_granted=False,
                        )
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

                    policy_decision = evaluate_tool_policy(tool_name, tool_args, tool_def)
                    if not policy_decision.allowed:
                        policy_text = policy_summary(policy_decision)
                        messages.append({"role": "assistant", "content": None, "tool_calls": [
                            {"id": call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args, ensure_ascii=False)}}
                        ]})
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": f"BLOCKED_POLICY: {policy_text}"})
                        ledger_entry = make_tool_ledger_entry(
                            call_id=call_id,
                            tool_name=tool_name,
                            tool_args=tool_args,
                            risk_level=tool_def.risk_level.value,
                            status="blocked",
                            result={"success": False, "data": "", "error": policy_text},
                            error=policy_text,
                            execution_state="skipped",
                            approval_granted=False,
                        )
                        tool_ledger_this_turn.append(ledger_entry)
                        await record_tool_execution(
                            session_id=session_id,
                            incident_id=incident_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            tool_args=tool_args,
                            risk_level=tool_def.risk_level.value,
                            status="blocked",
                            result={"success": False, "data": "", "error": policy_text},
                            error=policy_text,
                            execution_state="skipped",
                            approval_granted=False,
                        )
                        await audit_logger.log(
                            session_id,
                            AuditPhase.TOOL_CALL,
                            AuditEventType.BLOCKED,
                            f"Policy blocked tool call: {tool_name}",
                            {"tool_name": tool_name, "tool_args": tool_args, "policy": policy_decision.to_dict()},
                        )
                        await send_to_client(trace_event(
                            phase="tool_call",
                            event_type="blocked",
                            content=f"策略阻断: {policy_text}",
                            evidence=build_evidence(
                                claim=f"{display_name} 执行前被策略引擎阻断",
                                evidence_type="command",
                                source="execution_policy",
                                observed=policy_decision.to_dict(),
                                confidence="high",
                                execution_state="skipped",
                                failure_reason="; ".join(policy_decision.reasons),
                            ),
                        ))
                        continue

                    # Request approval
                    from app.websocket.approval import approval_manager
                    import asyncio as _asyncio

                    request_id = call_id
                    impact_text = await assess_impact(tool_name, tool_args, session_id, send_to_client)
                    policy_text = policy_summary(policy_decision)
                    if impact_text:
                        impact_text = f"{impact_text}\n{policy_text}"
                    else:
                        impact_text = policy_text
                    preview = build_operation_preview(tool_name, tool_args, tool_def)

                    loop = _asyncio.get_running_loop()
                    approval_future = loop.create_future()
                    supports_rollback, rollback_strategy = _effective_rollback_capability(tool_name, tool_args, tool_def)
                    try:
                        approval_manager.register_pending(
                            request_id,
                            session_id,
                            tool_name,
                            tool_args,
                            tool_def.risk_level,
                            tool_def.description,
                            approval_future,
                            impact=impact_text,
                            rollback_strategy=rollback_strategy,
                            supports_rollback=supports_rollback,
                            preview_strategy=tool_def.preview_strategy,
                            preview=preview,
                            policy=policy_decision.to_dict(),
                            approval_level=policy_decision.approval_level,
                            execution_identity=policy_decision.execution_identity,
                        )
                    except TypeError:
                        approval_manager.register_pending(
                            request_id,
                            session_id,
                            tool_name,
                            tool_args,
                            tool_def.risk_level,
                            tool_def.description,
                            approval_future,
                        )
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
                        "preview": preview,
                        "policy": policy_decision.to_dict(),
                        "approval_level": policy_decision.approval_level,
                        "execution_identity": policy_decision.execution_identity,
                    })
                    await send_to_client(trace_event(
                        phase="approval_request",
                        event_type="pending",
                        content=f"等待用户审批: {display_name}",
                        evidence=build_evidence(
                            claim=f"{display_name} 需要用户审批后才能执行",
                            evidence_type="user input",
                            source="approval_manager",
                            observed={
                                "impact": impact_text or tool_args,
                                "policy": policy_decision.to_dict(),
                                "preview": preview,
                            },
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
                            {"id": call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                        ]})
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": "REJECTED: User denied this operation"})
                        reject_reason = "用户拒绝或审批超时"
                        ledger_entry = make_tool_ledger_entry(
                            call_id=call_id,
                            tool_name=tool_name,
                            tool_args=tool_args,
                            risk_level=tool_def.risk_level.value,
                            status="rejected",
                            result={"success": False, "data": "", "error": reject_reason},
                            error=reject_reason,
                            execution_state="skipped",
                            approval_granted=False,
                        )
                        tool_ledger_this_turn.append(ledger_entry)
                        await record_tool_execution(
                            session_id=session_id,
                            incident_id=incident_id,
                            call_id=call_id,
                            tool_name=tool_name,
                            tool_args=tool_args,
                            risk_level=tool_def.risk_level.value,
                            status="rejected",
                            result={"success": False, "data": "", "error": reject_reason},
                            error=reject_reason,
                            execution_state="skipped",
                            approval_granted=False,
                        )
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
                        final_content = (
                            f"操作已取消：{display_name} 未执行。\n\n"
                            "原因：你在审批弹窗中拒绝了该操作。系统不会继续执行这个写操作；"
                            "如需重新尝试，请再次发送明确请求并重新审批。"
                        )
                        llm_response["content"] = final_content
                        approval_rejected_final_response = True
                        break

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
                    result = await execute_tool(tool_name, tool_args, tool_def)
                    result_success = bool(getattr(result, "success", True))
                    result_error = getattr(result, "error", None)
                    approval_granted = tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE)
                    ledger_entry = make_tool_ledger_entry(
                        call_id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level=tool_def.risk_level.value,
                        status="success" if result_success else "failure",
                        result=result,
                        error=result_error,
                        execution_state="executed" if result_success else "failed",
                        approval_granted=approval_granted,
                    )
                    if tool_name == "read_document":
                        ledger_entry["raw_result"] = result.__dict__ if hasattr(result, "__dict__") else result
                    tool_ledger_this_turn.append(ledger_entry)
                    await record_tool_execution(
                        session_id=session_id,
                        incident_id=incident_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level=tool_def.risk_level.value,
                        status="success" if result_success else "failure",
                        result=result,
                        error=result_error,
                        execution_state="executed" if result_success else "failed",
                        approval_granted=approval_granted,
                    )
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
                    approval_granted = tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE)
                    ledger_entry = make_tool_ledger_entry(
                        call_id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level=tool_def.risk_level.value,
                        status="failure",
                        result={"success": False, "data": "", "error": str(e)},
                        error=str(e),
                        execution_state="failed",
                        approval_granted=approval_granted,
                    )
                    tool_ledger_this_turn.append(ledger_entry)
                    await record_tool_execution(
                        session_id=session_id,
                        incident_id=incident_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        risk_level=tool_def.risk_level.value,
                        status="failure",
                        result={"error": str(e)},
                        error=str(e),
                        execution_state="failed",
                        approval_granted=approval_granted,
                    )
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
                    {"id": call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}
                ]})
                messages.append({"role": "tool", "tool_call_id": call_id, "content": tool_result_str})
            if approval_rejected_final_response:
                break
        else:
            # LLM returned text only. Treat it as a draft; the final answer is
            # validated later through structured JSON plus the tool ledger.
            break

    draft_final_response = llm_response.get("content", "") or "分析完成，请查看推理链路了解详情。"
    verbatim_document_response = _render_verbatim_document_response(user_message, tool_ledger_this_turn)
    final_response = verbatim_document_response or draft_final_response
    structured_validation_result: dict | None = None
    should_structure_final_response = (
        not guard_blocked_final_response
        and not approval_rejected_final_response
        and not verbatim_document_response
        and (
            bool(tool_ledger_this_turn)
            or _requires_fresh_tool_evidence(user_message)
            or _has_write_intent(user_message)
        )
    )
    if should_structure_final_response:
        try:
            structured_validation_result = await generate_structured_final_reply(
                user_message=user_message,
                messages=messages,
                draft_response=draft_final_response,
                tool_ledger=tool_ledger_this_turn,
                llm_call=call_llm,
                require_grounded_output=(
                    _requires_fresh_tool_evidence(user_message)
                    or _has_write_intent(user_message)
                ),
                max_retries=1,
            )
            final_response = structured_validation_result["markdown"]
            if not structured_validation_result.get("valid"):
                guard_blocked_final_response = True
                await audit_logger.log(
                    session_id,
                    AuditPhase.RESPONSE,
                    AuditEventType.FAILURE,
                    "结构化最终回复校验失败，已使用后端保守回复",
                    {"errors": structured_validation_result.get("errors", [])},
                )
                await send_to_client(trace_event(
                    phase="response",
                    event_type="failure",
                    content="结构化最终回复未通过账本校验，已使用后端保守回复",
                    evidence=build_evidence(
                        claim="最终回复结构化校验未通过",
                        evidence_type="command",
                        source="structured_final_response_guard",
                        observed=structured_validation_result.get("errors", []),
                        confidence="high",
                        execution_state="failed",
                        failure_reason="claims 或 executed_actions 未能匹配工具账本 call_id",
                        next_check="请基于工具账本重新生成带 evidence_call_ids 的结构化最终回复。",
                    ),
                ))
            else:
                await send_to_client(trace_event(
                    phase="response",
                    event_type="success",
                    content="结构化最终回复已通过工具账本校验",
                    evidence=build_evidence(
                        claim="最终回复中的事实已通过工具账本 call_id 校验",
                        evidence_type="command",
                        source="structured_final_response_guard",
                        observed={
                            "claims": len((structured_validation_result.get("data") or {}).get("claims") or []),
                            "ledger_entries": len(tool_ledger_this_turn),
                        },
                        confidence="high",
                        execution_state="executed",
                    ),
                ))
        except Exception as e:
            logger.warning(f"Structured final response guard failed unexpectedly: {e}")
            guard_blocked_final_response = True
            final_response = render_conservative_reply(tool_ledger_this_turn, str(e))
            await audit_logger.log(
                session_id,
                AuditPhase.RESPONSE,
                AuditEventType.FAILURE,
                "结构化最终回复守卫异常，已使用后端保守回复",
                {"error": str(e)},
            )
            await send_to_client(trace_event(
                phase="response",
                event_type="failure",
                content="结构化最终回复守卫异常，已使用后端保守回复",
                evidence=build_evidence(
                    claim="最终回复结构化校验异常",
                    evidence_type="command",
                    source="structured_final_response_guard",
                    observed=str(e),
                    confidence="high",
                    execution_state="failed",
                    failure_reason=str(e),
                ),
            ))
    if not guard_blocked_final_response:
        await audit_logger.log(session_id, AuditPhase.RESPONSE, AuditEventType.SUCCESS, f"生成回复: {final_response[:200]}")
        await send_to_client(trace_event(
            phase="response",
            event_type="success",
            content="回复已生成",
            evidence=build_evidence(
                claim=(
                    "最终回复已由后端根据结构化 JSON 和工具账本渲染"
                    if structured_validation_result
                    else "已基于现有证据和上下文生成最终回复"
                ),
                evidence_type="command" if structured_validation_result else "user input",
                source="structured_final_response_guard" if structured_validation_result else "LLM",
                observed=final_response[:500],
                confidence="high" if structured_validation_result else "medium",
                execution_state="executed" if structured_validation_result else "inferred",
            ),
        ))
    else:
        await audit_logger.log(session_id, AuditPhase.RESPONSE, AuditEventType.FAILURE, f"最终回复由真实性守卫替换: {final_response[:200]}")

    return {
        "final_response": final_response,
        "messages": messages,
        "iteration": iteration,
        "current_turn_tool_count": current_turn_tool_count,
        "current_turn_tool_ledger": tool_ledger_this_turn,
        "structured_validation_result": structured_validation_result or {},
        "is_blocked": guard_blocked_final_response,
        "block_reason": "structured_final_response_guard" if guard_blocked_final_response else "",
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
    tool_ledger = state.get("current_turn_tool_ledger", []) or []
    structured_validation_result = state.get("structured_validation_result", {}) or {}
    send_to_client = state["send_to_client"]

    # No tool was executed in the current turn → nothing actionable happened →
    # nothing to save. Historical tool messages in the same conversation must
    # not make a pure image/voice turn look like a verified resolution.
    if int(state.get("current_turn_tool_count", 0) or 0) <= 0:
        return {}
    if not tool_ledger:
        logger.debug("Skip save: no current-turn tool ledger evidence")
        return {}
    if structured_validation_result and structured_validation_result.get("valid") is False:
        logger.debug("Skip save: structured final response validation failed")
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
        evidence_refs = _build_knowledge_evidence_refs(
            session_id=session_id,
            incident_id=incident_id,
            tool_ledger=tool_ledger,
            extracted_evidence=evidence,
        )
        trace_event_ids = await _load_memory_trace_event_ids(incident_id)
        validation_method = summary_data.get("validation_method") or ""
        validation_status = "validated" if validation_method else "missing"
        confidence = summary_data.get("confidence") or "medium"
        if validation_status == "missing":
            confidence = "low"

        knowledge_id = await knowledge_store.save_resolution(
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
                "validation_method": validation_method,
                "applicability_conditions": applicability_conditions,
                "non_applicability_conditions": summary_data.get("non_applicability_conditions") or [],
                "source_incident_id": incident_id,
                "source_session_id": session_id,
                "entities": _extract_memory_entities(summary_data, tool_ledger),
                "incident_type": _infer_incident_type(problem, summary_data),
                "source_modality": "real_tool_execution",
                "evidence_refs": evidence_refs,
                "tool_call_ids": [item.get("call_id") for item in tool_ledger if item.get("call_id")],
                "trace_event_ids": trace_event_ids,
                "evidence_summaries": [item.get("summary") for item in evidence_refs if item.get("summary")],
                "has_write_action": any(bool(item.get("is_write")) for item in tool_ledger),
                "write_approved": any(bool(item.get("is_write")) and bool(item.get("approval_granted")) for item in tool_ledger),
                "validation_status": validation_status,
                "structured_final_valid": bool(structured_validation_result.get("valid")) if structured_validation_result else None,
                "confidence": confidence,
                "source_modalities": multimodal_memory.get("source_modalities", []) if multimodal_memory else ["real_tool_execution"],
                "multimodal_evidence": multimodal_memory.get("items", []) if multimodal_memory else [],
            },
        )
        await send_to_client({
            "type": "trace",
            "phase": "knowledge_save",
            "event_type": "success",
            "content": f"经验已保存 #{knowledge_id}: {problem[:30]}",
        })
    except Exception as e:
        logger.warning(f"Knowledge save failed: {e}")
        await send_to_client({
            "type": "trace",
            "phase": "knowledge_save",
            "event_type": "failure",
            "content": f"经验保存失败: {e}",
        })

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
        "current_turn_tool_ledger": [],
        "structured_validation_result": {},
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
        f"- 历史事故: {entry.get('problem_signature')}",
        "  边界: 以下内容只代表历史记录，不是当前系统事实；当前事实必须来自本轮工具/遥测结果。",
        f"  匹配: {entry.get('match_reason') or '历史文本相似'} "
        f"(match={entry.get('match_score')}, confidence={entry.get('confidence') or 'medium'})",
    ]
    if entry.get("retrieval_sources"):
        lines.append(f"  召回来源: {', '.join(map(str, entry.get('retrieval_sources') or []))}")
    if entry.get("symptoms"):
        lines.append(f"  历史症状: {', '.join(map(str, entry.get('symptoms') or []))}")
    if entry.get("root_cause"):
        lines.append(f"  历史根因: {entry.get('root_cause')}")
    if entry.get("evidence"):
        evidence_preview = "; ".join(map(str, (entry.get("evidence") or [])[:3]))
        lines.append(f"  历史证据摘要: {evidence_preview}")
    if entry.get("evidence_refs"):
        refs = []
        for ref in (entry.get("evidence_refs") or [])[:3]:
            if isinstance(ref, dict):
                refs.append(f"{ref.get('type') or 'evidence'}:{ref.get('call_id') or ref.get('id') or ref.get('summary')}")
            else:
                refs.append(str(ref))
        if refs:
            lines.append(f"  历史证据引用: {'; '.join(refs)}")
    if entry.get("successful_actions"):
        actions_preview = "; ".join(map(str, (entry.get("successful_actions") or [])[:3]))
        lines.append(f"  历史有效动作: {actions_preview}")
    if entry.get("failed_attempts"):
        failed_preview = "; ".join(map(str, (entry.get("failed_attempts") or [])[:3]))
        lines.append(f"  历史无效尝试: {failed_preview}")
    if entry.get("validation_method"):
        lines.append(f"  历史验证方法: {entry.get('validation_method')}")
    if entry.get("applicability_conditions"):
        lines.append(f"  适用条件: {', '.join(map(str, entry.get('applicability_conditions') or []))}")
    if entry.get("non_applicability_conditions"):
        lines.append(f"  不适用条件: {', '.join(map(str, entry.get('non_applicability_conditions') or []))}")
    if entry.get("recommended_fresh_checks"):
        lines.append(f"  本轮必须重新检查: {', '.join(map(str, entry.get('recommended_fresh_checks') or []))}")
    lines.append(
        "  复用安全性: "
        + ("可作为参考，但写操作仍需重新检查和审批" if entry.get("safe_to_reuse") else "仅供参考，不能直接复用写操作")
    )
    lines.append("  写作要求: 最终回复如果提到历史内容，必须称为“历史记录/历史经验”；不得写成当前已确认状态。")
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


def _build_knowledge_evidence_refs(
    *,
    session_id: str,
    incident_id: str,
    tool_ledger: list[dict],
    extracted_evidence: list,
) -> list[dict]:
    """Build durable evidence references for an incident-memory entry."""
    refs: list[dict] = []
    for item in tool_ledger:
        call_id = item.get("call_id")
        if not call_id:
            continue
        refs.append({
            "type": "tool_call",
            "call_id": call_id,
            "session_id": session_id,
            "incident_id": incident_id,
            "tool_name": item.get("tool_name"),
            "status": item.get("status"),
            "summary": compact_observed(
                item.get("result_summary") or item.get("error") or "",
                max_chars=240,
            ),
            "is_write": bool(item.get("is_write")),
            "approval_granted": bool(item.get("approval_granted")),
        })
    for index, summary in enumerate(extracted_evidence[:5]):
        refs.append({
            "type": "evidence_summary",
            "id": f"summary_{index + 1}",
            "session_id": session_id,
            "incident_id": incident_id,
            "summary": compact_observed(summary, max_chars=240),
        })
    return refs[:12]


async def _load_memory_trace_event_ids(incident_id: str) -> list[str]:
    """Return timeline event ids that can substantiate a saved memory entry."""
    if not incident_id:
        return []
    try:
        events = await incident_store.get_incident_events(incident_id, limit=200)
    except Exception as exc:
        logger.warning(f"Failed to load trace event ids for knowledge memory: {exc}")
        return []

    useful_phases = {"execution", "verification", "response", "knowledge_retrieval"}
    ids: list[str] = []
    for event in events:
        if event.get("phase") not in useful_phases:
            continue
        if event.get("event_type") not in {"success", "failure", "blocked"}:
            continue
        event_id = event.get("id")
        if event_id:
            ids.append(str(event_id))
    return ids[:20]


def _extract_memory_entities(summary_data: dict, tool_ledger: list[dict]) -> dict:
    """Extract simple service/path/port/host entities from summary and tool args."""
    entities = {"services": [], "ports": [], "paths": [], "hosts": []}
    text_parts = [
        summary_data.get("problem") or "",
        summary_data.get("diagnosis") or "",
        summary_data.get("solution") or "",
        summary_data.get("root_cause") or "",
        " ".join(map(str, summary_data.get("symptoms") or [])),
    ]
    for item in tool_ledger:
        args = item.get("args") or item.get("tool_args") or {}
        if not isinstance(args, dict):
            continue
        for key, value in args.items():
            key_l = str(key).lower()
            if value in (None, ""):
                continue
            if key_l in {"service", "service_name", "unit"}:
                entities["services"].append(str(value))
            elif key_l in {"port", "local_port"}:
                entities["ports"].append(str(value))
            elif "path" in key_l or key_l in {"filepath", "file", "directory", "dir"}:
                entities["paths"].append(str(value))
            elif key_l in {"host", "hostname", "target_host"}:
                entities["hosts"].append(str(value))
        text_parts.append(json.dumps(args, ensure_ascii=False, default=str))

    text = "\n".join(text_parts)
    for match in re.findall(r"\b(?:nginx|mysql|redis|postgresql|postgres|apache|httpd|docker|ssh|sshd)\b", text, re.IGNORECASE):
        entities["services"].append(match.lower())
    for match in re.findall(r"(?<![\d.])\b([1-9][0-9]{1,4})\b(?![\d.])", text):
        port = int(match)
        if 1 <= port <= 65535:
            entities["ports"].append(str(port))
    for match in re.findall(r"(?<!\S)(/[A-Za-z0-9._~:/%+\-]+)", text):
        entities["paths"].append(match.rstrip(".,;:，。；："))

    return {
        key: list(dict.fromkeys(values))[:10]
        for key, values in entities.items()
        if values
    }


def _infer_incident_type(problem: str, summary_data: dict) -> str:
    text = " ".join([
        problem or "",
        summary_data.get("root_cause") or "",
        " ".join(map(str, summary_data.get("symptoms") or [])),
    ]).lower()
    if any(term in text for term in ("disk", "磁盘", "空间", "inode")):
        return "disk"
    if any(term in text for term in ("nginx", "502", "http", "端口", "port")):
        return "service_connectivity"
    if any(term in text for term in ("cpu", "load", "负载")):
        return "cpu_load"
    if any(term in text for term in ("memory", "内存", "oom")):
        return "memory"
    if any(term in text for term in ("config", "配置")):
        return "configuration"
    return "general"


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


def _fresh_read_tool_plan(user_message: str) -> list[dict]:
    """Return deterministic read-only checks required for fresh-state requests.

    The LLM may still choose extra tools later, but these checks are executed
    before generation so current-state answers cannot be fabricated from
    historical conversation text.
    """
    if not user_message or _is_history_recall_intent(user_message):
        return []

    text = user_message.strip()
    plan: list[dict] = []

    def add(tool_name: str, tool_args: dict | None = None, reason: str = "") -> None:
        if any(item["tool_name"] == tool_name and item["tool_args"] == (tool_args or {}) for item in plan):
            return
        tool_def = tools_registry.get_tool(tool_name)
        if not tool_def or tool_def.risk_level != RiskLevel.READ:
            return
        plan.append({
            "tool_name": tool_name,
            "tool_args": tool_args or {},
            "reason": reason or "用户请求当前系统事实，需要本轮只读证据",
        })

    resource_overview_query = _matches_any(text, (
        r"(整体|系统|健康|状态|资源|概览|巡检|负载|load|cpu|CPU|处理器|内存|memory|磁盘|disk|空间)",
        r"\b(status|health|overview|load|cpu|memory|disk)\b",
    ))
    if resource_overview_query:
        add("system_overview", {}, "获取当前 CPU、内存、磁盘、负载等系统概览")
        add("health_check", {}, "获取当前系统健康检查结果")

    if _matches_any(text, (
        r"(错误日志|异常日志|错误|报错|日志|最近.*错|error|errors|failed|失败服务)",
        r"\b(log|logs|error|errors|failed)\b",
    )):
        add("get_recent_errors", {"lines": 50}, "获取最近 24 小时错误级别日志")
        add("get_failed_services", {}, "检查当前失败的 systemd 服务")

    if _matches_any(text, (
        r"(监听端口|开放端口|端口监听|当前端口|网络连接|连接数|端口)",
        r"\b(listening ports?|ports?|connections?|network)\b",
    )):
        add("get_listening_ports", {}, "获取当前监听端口")

    port_owner = _extract_port_owner_lookup(text)
    if port_owner is not None:
        add("check_port", {"port": port_owner}, f"精确检查 {port_owner} 端口被哪个进程占用")

    service_name = _extract_service_status_target(text)
    if service_name:
        add("get_service_status", {"service": service_name}, f"获取 {service_name} 当前服务状态")
        if _matches_any(text, (r"(日志|log|logs)",)):
            add("get_service_logs", {"service": service_name, "lines": 50}, f"获取 {service_name} 最近服务日志")

    filepath = _extract_read_file_target(text)
    if filepath:
        if _is_verbatim_document_request(text):
            add("read_document", {"filepath": filepath}, f"原文读取 {filepath} 的当前内容")
        else:
            add("read_file", {"filepath": filepath}, f"读取 {filepath} 的当前内容")

    return plan


def _format_fresh_evidence_requirement(user_message: str, plan: list[dict]) -> str:
    """Prompt block telling the LLM which fresh evidence is mandatory."""
    if not plan:
        return ""
    lines = [
        "",
        "## 本轮实时证据要求",
        "用户请求的是当前/最近系统事实，后端会在本轮强制执行以下只读工具。",
        "最终回复必须只把这些本轮工具结果称为“当前/最近”事实；历史对话中的结果只能称为历史观察。",
    ]
    for item in plan:
        args = json.dumps(item["tool_args"], ensure_ascii=False)
        lines.append(f"- {item['tool_name']}({args}): {item['reason']}")
    return "\n" + "\n".join(lines) + "\n"


def _render_tool_call_explanation_request(user_message: str) -> str:
    """Return a deterministic explanation for a pasted tool call, if requested.

    Users often ask about the raw command shown in an approval card, for example
    ``create_directory({"dirpath": "/tmp/x", "exist_ok": true})``. That is a
    read-only explanation request, not a request to execute the write tool.
    """
    parsed = _extract_tool_call_to_explain(user_message)
    if not parsed:
        return ""

    tool_name, tool_args = parsed
    tool_def = tools_registry.get_tool(tool_name)
    if not tool_def:
        return (
            f"**结论**：这是一条工具调用说明请求，本轮没有执行任何操作。\n\n"
            f"无法识别工具 `{tool_name}`，它不在当前工具注册表中。请确认命令名称是否完整。"
        )

    display_name = tool_def.display_name or tool_name
    risk_text = {
        RiskLevel.READ: "只读操作，通常不需要审批",
        RiskLevel.WRITE: "写操作，执行前必须通过审批",
        RiskLevel.DESTRUCTIVE: "破坏性操作，执行前必须通过审批并重点确认影响",
    }.get(tool_def.risk_level, str(tool_def.risk_level))
    preview = _preview_strategy_label(tool_def.preview_strategy)
    supports_rollback, rollback_strategy = _effective_rollback_capability(tool_name, tool_args, tool_def)
    rollback_text = (
        f"支持{_rollback_strategy_label(rollback_strategy)}，但只有真正执行前成功创建回滚点才可信"
        if supports_rollback
        else "当前参数下没有可靠自动回滚"
    )

    lines = [
        "**结论**：这是对审批命令的解释，本轮没有执行任何工具操作。",
        "",
        "**命令含义**",
        f"- 工具：`{tool_name}`（{display_name}）",
        f"- 作用：{tool_def.description}",
        f"- 风险级别：{tool_def.risk_level.value}，{risk_text}",
        f"- 预览能力：{preview}",
        f"- 回滚能力：{rollback_text}",
    ]

    if tool_args:
        lines.append("")
        lines.append("**参数说明**")
        properties = (tool_def.parameters or {}).get("properties") or {}
        required = set((tool_def.parameters or {}).get("required") or [])
        for key, value in tool_args.items():
            spec = properties.get(key) or {}
            desc = spec.get("description") or _fallback_arg_description(key)
            required_text = "必填" if key in required else "可选"
            default_text = f"，默认值 `{spec.get('default')}`" if "default" in spec else ""
            lines.append(
                f"- `{key}` = `{compact_observed(value, max_chars=160)}`："
                f"{desc}（{required_text}{default_text}）"
            )
    else:
        lines.append("")
        lines.append("**参数说明**")
        lines.append("- 这条工具调用没有携带参数。")

    impact = _explain_tool_call_impact(tool_name, tool_args)
    if impact:
        lines.append("")
        lines.append("**可能影响**")
        lines.extend(f"- {item}" for item in impact)

    lines.append("")
    lines.append("**审批提示**")
    if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
        lines.append("- 只有你在系统审批弹窗中确认后，这条工具才会执行。")
        lines.append("- 在聊天里询问含义、参数或风险不会触发执行。")
    else:
        lines.append("- 该工具是只读工具，通常可直接执行；本次回复仍只是解释，没有调用工具。")
    return "\n".join(lines)


def _extract_tool_call_to_explain(text: str) -> tuple[str, dict] | None:
    if not text:
        return None

    if not _matches_any(text, (
        r"(解释|说明|含义|什么意思|作用|参数|影响|风险)",
        r"\b(explain|describe|meaning|parameter|impact|risk)\b",
    )):
        return None

    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    if not match:
        return None
    tool_name = match.group(1)
    if not tools_registry.get_tool(tool_name):
        return None

    open_index = text.find("(", match.start())
    close_index = _find_matching_paren(text, open_index)
    if close_index == -1:
        return tool_name, {}

    args_text = text[open_index + 1:close_index].strip()
    if not args_text:
        return tool_name, {}

    try:
        parsed_args = json.loads(args_text)
    except json.JSONDecodeError:
        return tool_name, {}
    if not isinstance(parsed_args, dict):
        return tool_name, {}
    return tool_name, parsed_args


def _find_matching_paren(text: str, open_index: int) -> int:
    if open_index < 0 or open_index >= len(text) or text[open_index] != "(":
        return -1
    depth = 0
    in_string = False
    escape = False
    quote = ""
    for index in range(open_index, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_string = False
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _fallback_arg_description(name: str) -> str:
    return {
        "filepath": "目标文件路径",
        "dirpath": "目标目录路径",
        "path": "目标路径",
        "service": "目标服务名称",
        "exist_ok": "目标已存在时是否视为成功",
        "parents": "是否递归创建父目录",
        "mode": "权限模式",
        "content": "写入内容",
        "append": "是否追加写入",
        "overwrite": "是否允许覆盖已有内容",
    }.get(name, "工具参数")


def _explain_tool_call_impact(tool_name: str, tool_args: dict) -> list[str]:
    if tool_name == "create_directory":
        path = tool_args.get("dirpath") or tool_args.get("path") or "目标目录"
        exist_ok = bool(tool_args.get("exist_ok", False))
        return [
            f"会尝试创建目录 `{path}`。",
            "如果父目录不存在且 `parents` 未关闭，会一并创建缺失的父目录。",
            (
                "如果目录已经存在，会视为成功。"
                if exist_ok
                else "如果目录已经存在，工具会返回失败，不会假装创建成功。"
            ),
        ]
    if tool_name == "write_file":
        path = tool_args.get("filepath") or "目标文件"
        return [
            f"会修改文件 `{path}` 的内容。",
            "追加模式只在文件末尾增加内容；非追加模式会覆盖文件原内容。",
        ]
    if tool_name == "delete_file":
        path = tool_args.get("filepath") or "目标文件"
        return [f"会删除文件 `{path}`，执行前应确认路径和回滚点。"]
    if tool_name in {"restart_service", "start_service", "stop_service"}:
        service = tool_args.get("service") or "目标服务"
        return [f"会改变服务 `{service}` 的运行状态，可能造成短暂中断或连接重建。"]
    return []


async def _execute_forced_read_tools(
    *,
    session_id: str,
    incident_id: str,
    plan: list[dict],
    messages: list[dict],
    send_to_client,
) -> dict:
    """Execute deterministic read-only preflight tools and append tool results."""
    executed_tools: set[str] = set()
    read_tools: set[str] = set()
    tool_ledger: list[dict] = []
    tool_count = 0

    if not plan:
        return {
            "tool_count": 0,
            "executed_tools": executed_tools,
            "read_tools": read_tools,
            "tool_ledger": tool_ledger,
        }

    await send_to_client(trace_event(
        phase="planning",
        event_type="start",
        content=_format_forced_read_plan(plan),
        evidence=build_evidence(
            claim="已识别为实时只读查询，开始强制获取本轮证据",
            evidence_type="user input",
            source="fresh_evidence_guard",
            observed=[{"tool": item["tool_name"], "args": item["tool_args"]} for item in plan],
            confidence="high",
            execution_state="inferred",
        ),
    ))

    for item in plan:
        tool_name = item["tool_name"]
        tool_args = item["tool_args"]
        tool_def = tools_registry.get_tool(tool_name)
        if not tool_def or tool_def.risk_level != RiskLevel.READ:
            continue

        call_id = f"fresh_{tool_name}_{uuid.uuid4().hex[:8]}"
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": tool_name, "arguments": json.dumps(tool_args, ensure_ascii=False)}}
        ]})
        await audit_logger.log(
            session_id,
            AuditPhase.TOOL_CALL,
            AuditEventType.START,
            f"实时证据预检工具调用: {tool_name}",
            {"args": tool_args},
        )
        await send_to_client(trace_event(
            phase="tool_call",
            event_type="start",
            content=f"实时证据预检：调用 {tool_def.display_name or tool_name}\n原因：{item.get('reason', '')}",
            evidence=tool_plan_evidence(tool_name, tool_args),
        ))

        try:
            result = await execute_tool(tool_name, tool_args, tool_def)
            result_success = bool(getattr(result, "success", True))
            result_error = getattr(result, "error", None)
            ledger_entry = make_tool_ledger_entry(
                call_id=call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=tool_def.risk_level.value,
                status="success" if result_success else "failure",
                result=result,
                error=result_error,
                execution_state="executed" if result_success else "failed",
                approval_granted=False,
            )
            if tool_name == "read_document":
                ledger_entry["raw_result"] = result.__dict__ if hasattr(result, "__dict__") else result
            tool_ledger.append(ledger_entry)
            await record_tool_execution(
                session_id=session_id,
                incident_id=incident_id,
                call_id=call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=tool_def.risk_level.value,
                status="success" if result_success else "failure",
                result=result,
                error=result_error,
                execution_state="executed" if result_success else "failed",
                approval_granted=False,
            )
            tool_count += 1
            executed_tools.add(tool_name)
            read_tools.add(tool_name)
            tool_result_str = json.dumps(result.__dict__ if hasattr(result, "__dict__") else result, ensure_ascii=False, default=str)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": tool_result_str})

            if result_success:
                await audit_logger.log(
                    session_id,
                    AuditPhase.EXECUTION,
                    AuditEventType.SUCCESS,
                    f"实时证据预检成功: {tool_name}",
                )
                await send_to_client(trace_event(
                    phase="execution",
                    event_type="success",
                    content=f"实时证据已获取: {tool_def.display_name or tool_name}",
                    evidence=tool_result_evidence(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_def=tool_def,
                        result=result,
                        claim=f"{tool_def.display_name or tool_name} 已返回本轮实时只读证据",
                    ),
                ))
            else:
                await audit_logger.log(
                    session_id,
                    AuditPhase.EXECUTION,
                    AuditEventType.FAILURE,
                    f"实时证据预检失败: {tool_name} - {result_error or 'success=False'}",
                )
                await send_to_client(trace_event(
                    phase="execution",
                    event_type="failure",
                    content=f"实时证据获取失败: {tool_def.display_name or tool_name} - {result_error or 'success=False'}",
                    evidence=tool_result_evidence(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_def=tool_def,
                        result=result,
                    ),
                ))
        except Exception as e:
            tool_count += 1
            executed_tools.add(tool_name)
            read_tools.add(tool_name)
            tool_result_str = json.dumps({"success": False, "data": "", "error": str(e)}, ensure_ascii=False)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": tool_result_str})
            ledger_entry = make_tool_ledger_entry(
                call_id=call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=tool_def.risk_level.value,
                status="failure",
                result={"success": False, "data": "", "error": str(e)},
                error=str(e),
                execution_state="failed",
                approval_granted=False,
            )
            tool_ledger.append(ledger_entry)
            await record_tool_execution(
                session_id=session_id,
                incident_id=incident_id,
                call_id=call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=tool_def.risk_level.value,
                status="failure",
                result={"success": False, "data": "", "error": str(e)},
                error=str(e),
                execution_state="failed",
                approval_granted=False,
            )
            await send_to_client(trace_event(
                phase="execution",
                event_type="failure",
                content=f"实时证据获取异常: {tool_def.display_name or tool_name} - {e}",
                evidence=build_evidence(
                    claim=f"{tool_def.display_name or tool_name} 实时证据获取异常",
                    evidence_type="command",
                    source=tool_name,
                    observed=str(e),
                    confidence="high",
                    execution_state="failed",
                    failure_reason=str(e),
                    next_check="请检查本机命令可用性和权限后重试。",
                ),
            ))

    return {
        "tool_count": tool_count,
        "executed_tools": executed_tools,
        "read_tools": read_tools,
        "tool_ledger": tool_ledger,
    }


def _format_forced_read_plan(plan: list[dict]) -> str:
    lines = ["实时证据预检计划："]
    for idx, item in enumerate(plan, start=1):
        args = json.dumps(item["tool_args"], ensure_ascii=False)
        tool_def = tools_registry.get_tool(item["tool_name"])
        display_name = getattr(tool_def, "display_name", "") or item["tool_name"]
        lines.append(f"{idx}. {display_name}({args}) - {item.get('reason', '')}")
    return "\n".join(lines)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _compile_deterministic_tool_call(user_message: str) -> dict | None:
    """Compile high-confidence operational intents when the LLM only wrote prose.

    This is intentionally narrow: it covers requests where the target, operation
    and tool arguments are explicit enough that the backend can safely route to
    the normal approval/execution path instead of trusting the model to remember
    to emit a tool call.
    """
    if not user_message:
        return None

    text = user_message.strip()

    append_plan = _extract_append_file_intent(text)
    if append_plan:
        return _tool_call("write_file", append_plan)

    cleanup_plan = _extract_bounded_tmp_cleanup_intent(text)
    if cleanup_plan:
        return _tool_call("delete_file", cleanup_plan)

    protected_delete_plan = _extract_protected_delete_policy_test_intent(text)
    if protected_delete_plan:
        return _tool_call("delete_file", protected_delete_plan)

    cron_plan = _extract_add_cron_intent(text)
    if cron_plan:
        return _tool_call("add_cron_job", cron_plan)

    block_port_plan = _extract_block_port_intent(text)
    if block_port_plan:
        return _tool_call("block_port", block_port_plan)

    allow_port_plan = _extract_allow_port_intent(text)
    if allow_port_plan:
        return _tool_call("allow_port", allow_port_plan)

    return None


def _has_equivalent_tool_call(tool_ledger: list[dict], tool_name: str, tool_args: dict) -> bool:
    """Return True when this turn already handled the same effective tool call."""
    for item in tool_ledger:
        if item.get("tool_name") != tool_name:
            continue
        if item.get("status") not in {"success", "rejected", "blocked"}:
            continue
        if _canonical_tool_args(tool_name, item.get("tool_args") or {}) == _canonical_tool_args(tool_name, tool_args):
            return True
    return False


def _canonical_tool_args(tool_name: str, tool_args: dict) -> dict:
    if tool_name == "write_file":
        return {
            "filepath": str(tool_args.get("filepath") or ""),
            "content": str(tool_args.get("content") or ""),
            "append": bool(tool_args.get("append", False)),
        }
    if tool_name == "create_directory":
        return {
            "dirpath": str(tool_args.get("dirpath") or ""),
            "parents": bool(tool_args.get("parents", True)),
            "exist_ok": bool(tool_args.get("exist_ok", False)),
            "mode": str(tool_args.get("mode") or "755"),
        }
    return tool_args


def _is_noop_create_directory(tool_name: str, tool_args: dict) -> bool:
    if tool_name != "create_directory":
        return False
    dirpath = tool_args.get("dirpath")
    if not dirpath:
        return False
    path = Path(str(dirpath))
    return path.exists() and path.is_dir()


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "id": f"policy_{name}_{uuid.uuid4().hex[:8]}",
        "name": name,
        "arguments": arguments,
    }


def _extract_port_owner_lookup(text: str) -> int | None:
    if not _matches_any(text, (
        r"(端口).{0,20}(谁|哪个|进程|占用|监听)",
        r"(谁|哪个|进程).{0,20}(占用|监听).{0,20}(端口)",
        r"\b(port)\b.{0,30}\b(who|owner|process|pid|listening|occupied)\b",
    )):
        return None
    match = re.search(r"\b([1-9][0-9]{0,4})\b", text)
    if not match:
        return None
    port = int(match.group(1))
    if 1 <= port <= 65535:
        return port
    return None


def _extract_append_file_intent(text: str) -> dict | None:
    if not _matches_any(text, (r"(追加|写入|加到|补一行|append)",)):
        return None
    path_match = re.search(r"(/[^\s`'\"，。；;]+)", text)
    if not path_match:
        return None
    filepath = path_match.group(1).rstrip("。；;,，")

    content = ""
    # Common evaluator/user style: "追加 hello-from-opsguard".
    after_append = re.search(r"(?:追加|写入|加到|补一行)\s*([^，。；;]+)", text)
    if after_append:
        content = after_append.group(1).strip()
        # Drop leading target-path fragments when the user says "在 <path> 追加 X".
        if filepath in content:
            content = content.split(filepath, 1)[-1].strip()
    quoted = re.search(r"[`'\"“”]([^`'\"“”]+)[`'\"“”]", text)
    if quoted:
        content = quoted.group(1).strip()

    # If the phrase was "在 <path> 追加 X", extract the tail after the path.
    tail_after_path = text.split(filepath, 1)[-1]
    tail_match = re.search(r"(?:追加|写入|加到|补一行)\s*([^，。；;]+)", tail_after_path)
    if tail_match:
        content = tail_match.group(1).strip()

    content = content.strip(" ：:，,。；;`'\"“”")
    if not content or content in {"内容", "一行"}:
        return None
    return {"filepath": filepath, "content": content, "append": True}


def _extract_bounded_tmp_cleanup_intent(text: str) -> dict | None:
    if not _matches_any(text, (r"(清理|删除|移除)", r"\b(clean|delete|remove)\b")):
        return None
    if "/tmp/" not in text and not text.strip().startswith("/tmp"):
        return None
    if _matches_any(text, (r"(所有|全部|整个|递归|rm\s+-rf|根目录)", r"\b(all|everything|recursive)\b")):
        return None

    path_matches = re.findall(r"(/[^\s`'\"，。；;]+)", text)
    file_match = re.search(r"\b([A-Za-z0-9_.@+-]+\.[A-Za-z0-9_.@+-]+)\b", text)
    if file_match:
        from pathlib import Path

        filename = file_match.group(1)
        base_dir = ""
        for path in path_matches:
            candidate = path.rstrip("。；;,，")
            if candidate.startswith("/tmp/") and not candidate.endswith(filename):
                base_dir = candidate
                break
        if base_dir:
            return {"filepath": str(Path(base_dir) / filename)}

    if path_matches:
        filepath = path_matches[-1].rstrip("。；;,，")
        # Only compile concrete file deletes, not directory cleanup.
        if re.search(r"\.[A-Za-z0-9_.@+-]+$", filepath):
            return {"filepath": filepath}
    return None


def _extract_protected_delete_policy_test_intent(text: str) -> dict | None:
    """Compile explicit protected-file delete tests so policy blocking is stable."""
    if not _matches_any(text, (r"(删除|移除)", r"\b(delete|remove)\b")):
        return None
    path_matches = re.findall(r"(/[^\s`'\"，。；;]+)", text)
    for path in path_matches:
        filepath = path.rstrip("。；;,，")
        if filepath in {"/etc/passwd", "/etc/shadow", "/etc/sudoers"}:
            return {"filepath": filepath}
    return None


def _extract_add_cron_intent(text: str) -> dict | None:
    if not _matches_any(text, (r"(定时任务|cron)",)):
        return None
    if not _matches_any(text, (r"(添加|创建|新增|add|create)",)):
        return None

    schedule = ""
    if _matches_any(text, (r"(每分钟|每 1 分钟|每一分钟)",)):
        schedule = "* * * * *"
    quoted_schedule = re.search(r"([*0-9/, -]+\s+[*0-9/, -]+\s+[*0-9/, -]+\s+[*0-9/, -]+\s+[*0-9/, -]+)", text)
    if quoted_schedule:
        schedule = " ".join(quoted_schedule.group(1).split())
    if not schedule:
        return None

    command = ""
    command_match = re.search(r"(?:执行|运行|run)\s*(.+?)(?:的定时任务|定时任务|$)", text, re.IGNORECASE)
    if command_match:
        command = command_match.group(1).strip()
    quoted = re.search(r"[`'\"“”]([^`'\"“”]+)[`'\"“”]", text)
    if quoted:
        command = quoted.group(1).strip()
    command = command.strip(" ：:，,。；;`'\"“”")
    if not command:
        return None
    return {"schedule": schedule, "command": command}


def _extract_block_port_intent(text: str) -> dict | None:
    if not _matches_any(text, (
        r"(关闭|封禁|阻断|禁止|拦截|禁用).{0,30}(端口|port)",
        r"(端口|port).{0,30}(关闭|封禁|阻断|禁止|拦截|禁用)",
        r"\b(block|deny|close|disable)\b.{0,30}\b(port)\b",
        r"\b(port)\b.{0,30}\b(block|deny|close|disable)\b",
    )):
        return None
    return _extract_firewall_port_args(text)


def _extract_allow_port_intent(text: str) -> dict | None:
    if not _matches_any(text, (
        r"(开放|放行|允许|启用).{0,30}(端口|port)",
        r"(端口|port).{0,30}(开放|放行|允许|启用)",
        r"\b(allow|open|enable)\b.{0,30}\b(port)\b",
        r"\b(port)\b.{0,30}\b(allow|open|enable)\b",
    )):
        return None
    return _extract_firewall_port_args(text)


def _extract_firewall_port_args(text: str) -> dict | None:
    match = re.search(r"\b([1-9][0-9]{0,4})(?:\s*/\s*(tcp|udp))?\b", text, re.IGNORECASE)
    if not match:
        return None
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        return None
    protocol = (match.group(2) or "tcp").lower()
    return {"port": port, "protocol": protocol}


def _extract_service_status_target(text: str) -> str:
    """Best-effort service name extraction for status/log requests."""
    explicit_unit = re.search(r"\b([A-Za-z0-9_.@-]+\.service)\b", text, re.IGNORECASE)
    if explicit_unit and _matches_any(text, (r"(状态|日志|服务|status|logs?)",)):
        return _normalize_service_name(explicit_unit.group(1))

    common = re.search(r"\b(nginx|mysql|mysqld|redis|redis-server|sshd?|apache2?|httpd|docker|containerd)\b", text, re.IGNORECASE)
    if common and _matches_any(text, (r"(状态|日志|服务|status|logs?)",)):
        return _normalize_service_name(common.group(1))

    service_match = re.search(
        r"(?:查看|查询|检查|分析|get|status|日志|状态).{0,30}\b([A-Za-z0-9_.@-]+)(?:\.service)?\b.{0,12}(?:服务|service|状态|日志|status|logs?)",
        text,
        re.IGNORECASE,
    )
    if service_match:
        return _normalize_service_name(service_match.group(1))
    reverse_match = re.search(
        r"\b([A-Za-z0-9_.@-]+)(?:\.service)?\b.{0,12}(?:服务|service).{0,20}(?:状态|日志|status|logs?)",
        text,
        re.IGNORECASE,
    )
    if reverse_match:
        return _normalize_service_name(reverse_match.group(1))
    return ""


def _normalize_service_name(name: str) -> str:
    name = (name or "").strip().strip("`'\"")
    if not name:
        return ""
    if name.endswith(".service"):
        name = name[:-8]
    if _is_non_service_status_target(name):
        return ""
    if name == "ssh":
        return "ssh"
    return name


def _is_non_service_status_target(name: str) -> bool:
    """Reject resource words that are often followed by "status" but are not services."""
    normalized = (name or "").strip().strip("`'\"").lower()
    if normalized.endswith(".service"):
        normalized = normalized[:-8]
    return normalized in {
        "cpu",
        "mem",
        "memory",
        "ram",
        "disk",
        "load",
        "system",
        "host",
        "health",
        "overview",
        "status",
    }


def _extract_read_file_target(text: str) -> str:
    if not _matches_any(text, (r"(读取|查看|显示|输出|展示|打印|文件内容|文档内容|read|cat|show|view|print)",)):
        return ""
    match = re.search(r"(/[^\s`'\"，。；;]+)", text)
    return _normalize_extracted_file_path(match.group(1)) if match else ""


def _normalize_extracted_file_path(path: str) -> str:
    """Trim intent words that users often attach directly after a path."""
    path = (path or "").rstrip("。；;,，")
    if not path:
        return ""

    suffix_words = (
        "的文档内容",
        "文档内容",
        "的文件内容",
        "文件内容",
        "的内容",
        "内容",
        "原文",
        "全文",
    )
    common_extensions = (
        "txt",
        "md",
        "log",
        "json",
        "yaml",
        "yml",
        "conf",
        "cfg",
        "ini",
        "csv",
        "xml",
        "html",
        "py",
        "sh",
        "service",
    )
    extension_pattern = "|".join(re.escape(ext) for ext in common_extensions)
    for word in suffix_words:
        if not path.endswith(word):
            continue
        candidate = path[: -len(word)]
        if re.search(rf"\.({extension_pattern})$", candidate, re.IGNORECASE):
            return candidate
    return path


def _is_verbatim_document_request(text: str) -> bool:
    """Return True when the user asks to display file content verbatim."""
    return _matches_any(text or "", (
        r"(原文|原样|全文|完整.*内容|直接.*输出|输出.*内容|展示.*内容|打印.*内容|不要总结|不要概括)",
        r"\b(cat|verbatim|raw|full content|print file|show file)\b",
    ))


def _render_verbatim_document_response(user_message: str, tool_ledger: list[dict]) -> str:
    """Render read_document output directly for explicit document-display requests."""
    if not _is_verbatim_document_request(user_message):
        return ""

    for item in reversed(tool_ledger):
        if item.get("tool_name") != "read_document" or item.get("status") != "success":
            continue
        data = _extract_tool_result_data(item)
        if not isinstance(data, dict) or data.get("render_mode") != "verbatim":
            continue
        path = str(data.get("path") or item.get("tool_args", {}).get("filepath") or "")
        content = str(data.get("content") or "")
        truncated = bool(data.get("truncated"))
        size = data.get("size")
        max_bytes = data.get("max_bytes")
        lines = [
            f"**文档内容**：{path}",
            "",
            "```text",
            content.rstrip("\n"),
            "```",
        ]
        if truncated:
            lines.extend([
                "",
                f"> 已按读取上限截断：文件大小 {size} bytes，本次最多读取 {max_bytes} bytes。",
            ])
        return "\n".join(lines).strip()

    return ""


def _extract_tool_result_data(ledger_item: dict) -> object:
    result = ledger_item.get("raw_result") or ledger_item.get("result")
    if isinstance(result, dict):
        if isinstance(result.get("data"), (dict, list, str)):
            return result.get("data")
        return result
    return {}


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
        "service_state": "服务状态恢复",
        "snapshot_restore": "快照恢复",
        "none": "无可靠自动回滚",
    }.get(strategy or "none", strategy or "无可靠自动回滚")


def _effective_rollback_capability(tool_name: str, tool_args: dict, tool_def) -> tuple[bool, str]:
    """Return rollback capability that can be truthfully claimed before approval."""
    if not tool_def or not tool_def.supports_rollback:
        return False, "none"

    if tool_def.rollback_strategy in {"service_state", "snapshot_restore"}:
        return True, tool_def.rollback_strategy

    if tool_def.rollback_strategy != "backup":
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
            if rollback_strategy == "backup":
                impact_lines.append(f"回滚：支持{_rollback_strategy_label(rollback_strategy)}，备份创建成功后可信度较高")
            elif rollback_strategy == "service_state":
                impact_lines.append(f"回滚：支持{_rollback_strategy_label(rollback_strategy)}，执行前会记录 systemd active 状态")
            elif rollback_strategy == "snapshot_restore":
                impact_lines.append(f"回滚：支持{_rollback_strategy_label(rollback_strategy)}，执行前会记录相关规则或任务快照")
            else:
                impact_lines.append(f"回滚：支持{_rollback_strategy_label(rollback_strategy)}")
        elif tool_name == "rollback_backup":
            impact_lines.append("回滚：这是恢复动作本身，不再声明二次自动回滚")
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
    "已修改", "已更新", "已写入", "已追加", "追加完成", "成功追加", "已应用", "已保存", "已成功修改",
    "成功写入", "成功添加", "成功向", "已成功添加", "已成功追加", "已成功写入",
    "已移动", "移动完成", "已重命名", "重命名完成", "已改名", "改名完成",
    "已复制", "复制完成", "已成功复制", "已成功将", "已为您移动", "已为您重命名", "已为您复制",
    "已执行完毕", "已为您执行", "已为你执行", "执行完毕", "已经执行",
    "已添加", "已创建", "创建完成", "已成功创建", "已新建", "新建完成", "已配置", "已开启", "已关闭", "已禁用", "已启用",
    "已安装", "安装完成", "已卸载", "卸载完成",
    "started", "start completed", "restarted", "restart completed", "created", "create completed",
    "stopped", "stop completed", "deleted", "delete completed",
    "removed", "remove completed", "modified", "updated", "saved",
    "appended", "append completed", "moved", "move completed", "renamed", "rename completed",
    "copied", "copy completed",
    "installed", "install completed", "uninstalled", "uninstall completed",
)


_WRITE_INTENT_PATTERNS = (
    # Explicit operation requests. Keep the gap short so read-only phrases like
    # "请查看 nginx 配置文件" do not treat the noun "配置" as a write verb.
    r"(帮我|请|执行|开始|给我|把|将|立即|现在|麻烦).{0,60}(启动|重启|停止|关闭|删除|清理|修改|写入|追加|保存|应用|启用|禁用|添加|创建|新建|安装|卸载|改名|重命名|移动|复制)",
    # Bare imperative-style operation plus an object.
    r"^(启动|重启|停止|关闭|删除|清理|修改|写入|追加|保存|应用|启用|禁用|添加|创建|新建|安装|卸载|改名|重命名|移动|复制).{0,80}(服务|进程|文件|目录|配置|端口|用户|软件|包|规则|权限|路径|nginx|mysql|redis|apache|systemd|txt|conf|log)",
    # Explicit configuration changes. Avoid treating "查看 nginx 配置文件" as a write.
    r"^(配置).{0,40}(防火墙|规则|端口|用户|权限|服务)",
    # Location-first creation requests such as "在 /tmp 下创建 test 目录".
    r"(在|到|向).{0,60}(创建|新建).{0,30}(文件夹|目录|文件)",
    # File/path-first mutations such as "把 a.txt 改名为 b.txt" or "向 a.txt 追加一行".
    r"(把|将|向).{0,120}(改名|重命名|移动|复制|追加|写入|保存|删除)",
    # Path-first mutations such as "/tmp/a.txt 追加 hello".
    r"(/[\w./~@:+-]+|[\w.-]+\.(txt|conf|log|json|yaml|yml|ini|env|sh|service)).{0,80}(追加|写入|添加|保存|删除|移动|复制|改名|重命名)",
    # Mixed read-then-write requests such as "检查并重启 nginx".
    r"(并|然后|之后|后).{0,6}(启动|重启|停止|关闭|删除|清理|修改|写入|追加|保存|应用|启用|禁用|添加|创建|新建|安装|卸载|改名|重命名|移动|复制)",
    # Follow-up mutation requests often omit the target because it is in history.
    r"^(继续|接着|再|再次).{0,20}(追加|写入|添加|修改|保存|删除|清理|重启|启动|停止|关闭|移动|复制|改名|重命名)",
    # English operation requests.
    r"\b(start|restart|stop|delete|remove|clean|modify|write|append|save|apply|enable|disable|create|move|rename|copy|install|uninstall)\b.{0,80}\b(service|process|file|directory|config|port|user|package|path|nginx|mysql|redis|apache|txt|conf|log)\b",
    # Follow-up confirmations after the assistant proposed a write operation.
    r"^(是|对|执行|确认|确定|批准|同意|开始执行|继续|好的|好|可以|可以执行|那就这样|那就执行)$",
)


_READ_ONLY_MUTATION_STATE_PATTERNS = (
    # "请检查 nginx 是否已停止" asks for state, not for stop_service.
    r"(查看|查询|检查|检测|确认|验证|看看|判断).{0,80}(是否|是不是|有没有|是否已经|是否已|是否.*已经).{0,50}(启动|重启|停止|关闭|删除|清理|修改|写入|追加|保存|应用|启用|禁用|添加|创建|安装|卸载|改名|重命名|移动|复制)",
    # "查看 nginx 启动状态" / "查询已停止的服务" are read-only status/list requests.
    r"(查看|查询|检查|检测|获取|列出|显示).{0,80}(启动|重启|停止|启用|禁用|安装|卸载|删除|修改).{0,24}(状态|记录|历史|列表|服务)",
    r"(查看|查询|列出|显示).{0,40}已(启动|重启|停止|启用|禁用|安装|卸载|删除|修改).{0,24}(服务|任务|软件|文件|记录|列表)",
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
    if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _READ_ONLY_MUTATION_STATE_PATTERNS):
        return False
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _WRITE_INTENT_PATTERNS)


def _is_read_only_intent(text: str) -> bool:
    """Return whether the latest user turn is clearly asking for observation only."""
    if not text or _has_write_intent(text):
        return False

    import re

    normalized = text.strip()
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _READ_ONLY_INTENT_PATTERNS)


def _is_history_recall_intent(text: str) -> bool:
    """Return whether the user asks about prior evidence instead of fresh state."""
    if not text:
        return False

    import re

    normalized = text.strip()
    history_markers = (
        r"(刚刚|刚才|上一轮|上次|之前|前面|刚检测|刚检查|刚分析|刚才检测|刚才检查|刚才分析)",
        r"(刚刚|刚才).{0,40}(最大风险|风险|结果|结论|检测到|检查到|发现|是什么)",
        r"\b(previous|last|earlier|just now|before)\b",
    )
    fresh_markers = (
        r"(重新|再次|现在|当前|实时|最新|再检查|重新检查|重新检测|刷新)",
        r"\b(now|current|latest|refresh|rerun|recheck|again)\b",
    )
    if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in fresh_markers):
        return False
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in history_markers)


def _requires_fresh_tool_evidence(user_message: str) -> bool:
    """Whether the user is asking for current system/file/service evidence."""
    if not user_message:
        return False

    import re

    normalized = user_message.strip()
    evidence_patterns = (
        r"(验证|确认|检查|查看|读取|获取|查询|列出|显示|对比|diff|当前内容|内容|文件大小|行数|是否存在|状态|日志)",
        r"\b(verify|validate|check|read|view|show|get|list|stat|status|diff)\b",
        r"(/[\w./~@:+-]+|[\w.-]+\.(txt|conf|log|json|yaml|yml|ini|env|sh|service))",
    )
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in evidence_patterns)
