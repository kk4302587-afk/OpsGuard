"""Structured final response validation and rendering.

The LLM may draft wording, but the backend owns the final Markdown. Every
factual claim must cite a real tool-ledger call_id, and executed write actions
are rendered from ledger rows rather than free-form model text.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from app.agent.trace_evidence import compact_observed


LLMCall = Callable[[list[dict], list[dict] | None], Awaitable[dict]]


async def generate_structured_final_reply(
    *,
    user_message: str,
    messages: list[dict],
    draft_response: str,
    tool_ledger: list[dict[str, Any]],
    llm_call: LLMCall,
    require_grounded_output: bool = False,
    max_retries: int = 1,
) -> dict[str, Any]:
    """Ask the LLM for structured JSON, validate it, and render Markdown."""
    validation_error = ""
    attempts: list[dict[str, Any]] = []

    for attempt in range(max_retries + 1):
        prompt_messages = _structured_reply_messages(
            user_message=user_message,
            messages=messages,
            draft_response=draft_response,
            tool_ledger=tool_ledger,
            validation_error=validation_error,
        )
        response = await llm_call(prompt_messages, tools=None)
        raw = response.get("content") or ""
        data = parse_structured_reply(raw)
        errors = validate_structured_reply(
            data,
            tool_ledger,
            require_grounded_output=require_grounded_output,
        )
        attempts.append({"attempt": attempt + 1, "raw": raw, "errors": errors})

        if not errors and isinstance(data, dict):
            return {
                "valid": True,
                "markdown": render_structured_reply(data, tool_ledger),
                "data": data,
                "raw": raw,
                "attempts": attempts,
                "errors": [],
            }

        validation_error = "; ".join(errors) or "输出不是合法 JSON 对象"

    return {
        "valid": False,
        "markdown": render_conservative_reply(tool_ledger, validation_error),
        "data": None,
        "raw": attempts[-1]["raw"] if attempts else "",
        "attempts": attempts,
        "errors": attempts[-1]["errors"] if attempts else [validation_error],
    }


def parse_structured_reply(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object from a model response."""
    if not raw:
        return None

    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        return data if isinstance(data, dict) else None
    return None


def validate_structured_reply(
    data: dict[str, Any] | None,
    tool_ledger: list[dict[str, Any]],
    *,
    require_grounded_output: bool = False,
) -> list[str]:
    """Validate structured final JSON against the backend tool ledger."""
    if not isinstance(data, dict):
        return ["最终回复必须是 JSON object"]

    errors: list[str] = []
    ledger_by_call_id = {
        str(item.get("call_id")): item
        for item in tool_ledger
        if item.get("call_id")
    }

    if not isinstance(data.get("conclusion"), str):
        errors.append("conclusion 必须是字符串")

    claims = data.get("claims", [])
    if not isinstance(claims, list):
        errors.append("claims 必须是数组")
    else:
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                errors.append(f"claims[{index}] 必须是对象")
                continue
            if not isinstance(claim.get("text"), str) or not claim.get("text", "").strip():
                errors.append(f"claims[{index}].text 必须是非空字符串")
            evidence_ids = claim.get("evidence_call_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                errors.append(f"claims[{index}].evidence_call_ids 必须是非空数组")
                continue
            for call_id in evidence_ids:
                if str(call_id) not in ledger_by_call_id:
                    errors.append(f"claims[{index}] 引用了不存在的 call_id: {call_id}")
            if not isinstance(claim.get("claim_type", ""), str):
                errors.append(f"claims[{index}].claim_type 必须是字符串")

    executed_actions = data.get("executed_actions", [])
    if not isinstance(executed_actions, list):
        errors.append("executed_actions 必须是数组")
    else:
        for index, action in enumerate(executed_actions):
            if not isinstance(action, dict):
                errors.append(f"executed_actions[{index}] 必须是对象")
                continue
            evidence_ids = _action_evidence_ids(action)
            if not evidence_ids:
                errors.append(f"executed_actions[{index}] 必须引用 call_id/evidence_call_ids")
                continue
            tool_name = str(action.get("tool_name") or "")
            for call_id in evidence_ids:
                ledger_item = ledger_by_call_id.get(str(call_id))
                if not ledger_item:
                    errors.append(f"executed_actions[{index}] 引用了不存在的 call_id: {call_id}")
                    continue
                if tool_name and tool_name != str(ledger_item.get("tool_name") or ""):
                    errors.append(f"executed_actions[{index}] 的 tool_name 与账本不一致")
                if not _is_successful_write(ledger_item):
                    errors.append(
                        f"executed_actions[{index}] 引用的 call_id 不是已审批且成功的写操作: {call_id}"
                    )

    recommended_actions = data.get("recommended_actions", [])
    if not isinstance(recommended_actions, list):
        errors.append("recommended_actions 必须是数组")
    else:
        for index, action in enumerate(recommended_actions):
            if not isinstance(action, dict):
                errors.append(f"recommended_actions[{index}] 必须是对象")
                continue
            if not isinstance(action.get("tool_name"), str) or not action.get("tool_name", "").strip():
                errors.append(f"recommended_actions[{index}].tool_name 必须是非空字符串")
            if not isinstance(action.get("args", {}), dict):
                errors.append(f"recommended_actions[{index}].args 必须是对象")
            if action.get("executed") is not False:
                errors.append(f"recommended_actions[{index}].executed 必须是 false")
            if not isinstance(action.get("requires_approval"), bool):
                errors.append(f"recommended_actions[{index}].requires_approval 必须是布尔值")

    if (
        require_grounded_output
        and not tool_ledger
        and not claims
        and not recommended_actions
    ):
        errors.append("当前请求需要工具证据或未执行建议，但本轮没有工具账本记录")

    return errors


def render_structured_reply(data: dict[str, Any], tool_ledger: list[dict[str, Any]]) -> str:
    """Render trusted Markdown from validated JSON and ledger facts."""
    lines: list[str] = []
    claims = data.get("claims") or []
    recommended_actions = data.get("recommended_actions") or []
    successful_writes = [item for item in tool_ledger if _is_successful_write(item)]
    incomplete_writes = [
        item for item in tool_ledger
        if _is_write(item) and not _is_successful_write(item)
    ]
    if recommended_actions and not claims and not successful_writes and not incomplete_writes:
        conclusion = "本轮没有执行工具操作；以下操作仅为建议，尚未执行。"
    else:
        conclusion = compact_observed(data.get("conclusion") or "处理完成。", max_chars=500)
    lines.append(f"**结论**：{conclusion}")

    if claims:
        lines.append("")
        lines.append("**关键证据**")
        ledger_by_call_id = {str(item.get("call_id")): item for item in tool_ledger if item.get("call_id")}
        for claim in claims[:6]:
            evidence_ids = [str(item) for item in claim.get("evidence_call_ids", [])]
            evidence_labels = []
            for call_id in evidence_ids:
                ledger_item = ledger_by_call_id.get(call_id)
                if not ledger_item:
                    continue
                evidence_labels.append(f"`{call_id}`/{ledger_item.get('tool_name')}")
            suffix = f"（证据：{', '.join(evidence_labels)}）" if evidence_labels else ""
            lines.append(f"- {compact_observed(claim.get('text'), max_chars=220)}{suffix}")
    else:
        read_evidence = [item for item in tool_ledger if str(item.get("risk_level")) == "read"]
        if read_evidence:
            lines.append("")
            lines.append("**关键证据**")
            for item in read_evidence[:5]:
                summary = item.get("error") or item.get("result_summary") or item.get("status")
                lines.append(
                    f"- `{item.get('call_id')}`/{item.get('tool_name')}："
                    f"{compact_observed(summary, max_chars=220)}"
                )

    if successful_writes:
        lines.append("")
        lines.append("**已执行操作**")
        for item in successful_writes:
            summary = item.get("result_summary") or "工具返回成功"
            lines.append(
                f"- `{item.get('call_id')}`/{item.get('tool_name')} 已执行成功"
                f"（审批：已通过）。{compact_observed(summary, max_chars=220)}"
            )

    if incomplete_writes:
        lines.append("")
        lines.append("**未完成的写操作**")
        for item in incomplete_writes:
            reason = item.get("error") or item.get("result_summary") or item.get("status")
            approval = "已通过" if item.get("approval_granted") else "未通过/未审批"
            lines.append(
                f"- `{item.get('call_id')}`/{item.get('tool_name')} 未完成"
                f"（状态：{item.get('status')}，审批：{approval}）。"
                f"{compact_observed(reason, max_chars=220)}"
            )

    if recommended_actions:
        lines.append("")
        lines.append("**建议操作**")
        for action in recommended_actions[:6]:
            approval = "需要审批" if action.get("requires_approval") else "无需审批"
            args = compact_observed(action.get("args") or {}, max_chars=160)
            lines.append(
                f"- `{action.get('tool_name')}` {args}：尚未执行，{approval}。"
            )

    return "\n".join(lines).strip()


def render_conservative_reply(tool_ledger: list[dict[str, Any]], reason: str = "") -> str:
    """Backend-only fallback when structured validation fails twice."""
    lines = [
        "**结论**：最终回复结构化校验未通过，系统没有采信模型自由文本。",
    ]
    if reason:
        lines.append(f"校验原因：{compact_observed(reason, max_chars=300)}")

    if not tool_ledger:
        lines.append("")
        lines.append("本轮没有可验证的工具执行记录，因此不能确认任何系统状态或写操作结果。")
        return "\n".join(lines)

    lines.append("")
    lines.append("**工具账本事实**")
    for item in tool_ledger[:8]:
        state = item.get("execution_state") or item.get("status")
        result = item.get("error") or item.get("result_summary") or ""
        approval = "，审批已通过" if item.get("approval_granted") else ""
        lines.append(
            f"- `{item.get('call_id')}`/{item.get('tool_name')}："
            f"{state}，状态 {item.get('status')}{approval}。"
            f"{compact_observed(result, max_chars=220)}"
        )
    return "\n".join(lines)


def make_tool_ledger_entry(
    *,
    call_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    risk_level: str,
    status: str,
    result: Any = None,
    error: str | None = None,
    execution_state: str | None = None,
    approval_granted: bool = False,
) -> dict[str, Any]:
    """Create one normalized in-memory ledger row for the current turn."""
    result_dict = _result_to_dict(result)
    result_summary = compact_observed(
        result_dict.get("data", result_dict) if isinstance(result_dict, dict) else result_dict,
        max_chars=700,
    )
    error_text = error
    if not error_text and isinstance(result_dict, dict):
        error_text = result_dict.get("error")

    normalized_risk = str(risk_level or "").lower()
    normalized_status = str(status or "").lower()
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "tool_args": tool_args or {},
        "risk_level": normalized_risk,
        "status": normalized_status,
        "result_summary": result_summary,
        "error": compact_observed(error_text, max_chars=500) if error_text else "",
        "execution_state": execution_state or ("executed" if normalized_status == "success" else "failed"),
        "is_write": normalized_risk in {"write", "destructive"},
        "approval_granted": bool(approval_granted),
    }


def _structured_reply_messages(
    *,
    user_message: str,
    messages: list[dict],
    draft_response: str,
    tool_ledger: list[dict[str, Any]],
    validation_error: str = "",
) -> list[dict]:
    correction = ""
    if validation_error:
        correction = (
            "\n\n上一次结构化输出未通过后端校验："
            f"{validation_error}\n请修正 JSON。"
        )

    return [
        {
            "role": "system",
            "content": (
                "你是 OpsGuard 最终回复结构化器。只输出 JSON，不输出 Markdown 或解释。\n"
                "后端工具账本是唯一执行事实来源，不能让自由文本决定工具是否执行。\n"
                "规则：\n"
                "1. claims 中每一条事实都必须有非空 evidence_call_ids，且只能引用工具账本存在的 call_id。\n"
                "2. executed_actions 只能引用已审批、risk_level 为 write/destructive、status 为 success 的账本记录。\n"
                "3. recommended_actions 里的 executed 必须为 false；写操作建议 requires_approval 必须为 true。\n"
                "4. 如果没有证据，不要写成事实 claim；可以放在 recommended_actions 或 conclusion 中说明尚未执行。\n"
                "5. 不要伪造 call_id，不要引用历史对话中没有出现在本轮账本里的工具。\n"
                "严格输出以下 JSON 形状：\n"
                "{"
                "\"conclusion\":\"...\","
                "\"claims\":[{\"text\":\"...\",\"evidence_call_ids\":[\"call_x\"],\"claim_type\":\"observed_state|tool_result|failure|approval_state\"}],"
                "\"executed_actions\":[{\"tool_name\":\"...\",\"args\":{},\"call_id\":\"call_x\"}],"
                "\"recommended_actions\":[{\"tool_name\":\"...\",\"args\":{},\"executed\":false,\"requires_approval\":true}]"
                "}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户请求：\n{user_message}\n\n"
                f"对话摘要：\n{_format_messages_excerpt(messages)}\n\n"
                f"模型草稿（仅供参考，不能当证据）：\n{draft_response[:1500]}\n\n"
                f"本轮工具账本 JSON：\n{json.dumps(tool_ledger, ensure_ascii=False, default=str)}"
                f"{correction}"
            ),
        },
    ]


def _format_messages_excerpt(messages: list[dict], max_chars: int = 2200) -> str:
    lines: list[str] = []
    for msg in messages[-12:]:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user" and isinstance(content, str):
            lines.append(f"[用户] {content[:280]}")
        elif role == "assistant" and isinstance(content, str) and content:
            lines.append(f"[助手] {content[:280]}")
        elif role == "assistant" and msg.get("tool_calls"):
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") if isinstance(tc, dict) else None
                if isinstance(fn, dict):
                    lines.append(f"[工具调用] {fn.get('name')}({str(fn.get('arguments'))[:160]})")
        elif role == "tool" and isinstance(content, str):
            lines.append(f"[工具返回] {content[:240]}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = "...（前文省略）...\n" + text[-max_chars:]
    return text


def _action_evidence_ids(action: dict[str, Any]) -> list[str]:
    ids = action.get("evidence_call_ids")
    if isinstance(ids, list):
        return [str(item) for item in ids]
    call_id = action.get("call_id")
    if isinstance(call_id, str) and call_id:
        return [call_id]
    return []


def _is_write(item: dict[str, Any]) -> bool:
    return bool(item.get("is_write")) or str(item.get("risk_level")) in {"write", "destructive"}


def _is_successful_write(item: dict[str, Any]) -> bool:
    return (
        _is_write(item)
        and item.get("status") == "success"
        and item.get("execution_state") == "executed"
        and bool(item.get("approval_granted"))
    )


def _result_to_dict(result: Any) -> Any:
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    return result
