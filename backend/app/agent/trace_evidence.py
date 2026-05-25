"""Helpers for evidence-aware trace events.

Trace events are streamed to the UI and may also be persisted through audit
metadata. Keep this module free of I/O so tests can validate trace truthfulness
without booting the whole application.
"""

from typing import Any


_CATEGORY_EVIDENCE_TYPES = {
    "config": "config",
    "file": "config",
    "log": "log",
    "network": "command",
    "service": "command",
    "process": "command",
    "disk": "command",
    "system": "command",
    "package": "command",
    "user": "command",
    "firewall": "command",
    "cron": "command",
    "recent_changes": "command",
}


def compact_observed(value: Any, max_chars: int = 500) -> str:
    """Return a compact human-readable observation string."""
    if value in (None, ""):
        return ""

    import json

    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def build_evidence(
    *,
    claim: str,
    evidence_type: str,
    source: str,
    observed: Any = "",
    confidence: str = "medium",
    execution_state: str = "inferred",
    failure_reason: str | None = None,
    next_check: str | None = None,
) -> dict:
    """Build a normalized evidence payload, omitting empty optional fields."""
    evidence = {
        "claim": claim,
        "evidence_type": evidence_type,
        "source": source,
        "observed": compact_observed(observed),
        "confidence": confidence,
        "execution_state": execution_state,
    }
    if failure_reason:
        evidence["failure_reason"] = compact_observed(failure_reason)
    if next_check:
        evidence["next_check"] = next_check
    return evidence


def trace_event(
    *,
    phase: str,
    event_type: str,
    content: str,
    evidence: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build a trace event with optional top-level evidence fields.

    The top-level fields are convenient for the live WebSocket UI; the same
    evidence is mirrored into metadata so persisted audit traces can reload it.
    """
    event: dict[str, Any] = {
        "type": "trace",
        "phase": phase,
        "event_type": event_type,
        "content": content,
    }
    if metadata:
        event["metadata"] = dict(metadata)
    if evidence:
        event.update(evidence)
        event.setdefault("metadata", {})["evidence"] = evidence
    return event


def tool_result_evidence(
    *,
    tool_name: str,
    tool_args: dict,
    tool_def,
    result: Any,
    claim: str | None = None,
) -> dict:
    """Create evidence from a real tool result."""
    result_repr = result.__dict__ if hasattr(result, "__dict__") else result
    success = result_repr.get("success", True) if isinstance(result_repr, dict) else True
    error = result_repr.get("error") if isinstance(result_repr, dict) else None
    data = result_repr.get("data") if isinstance(result_repr, dict) else result_repr
    category = getattr(tool_def, "category", "")
    evidence_type = _CATEGORY_EVIDENCE_TYPES.get(category, "command")

    target = _target_from_args(tool_args)
    display_name = _display_tool_name(tool_name, tool_def)
    default_claim = (
        f"{display_name} 已对 {target} 执行完成"
        if success
        else f"{display_name} 对 {target} 执行失败"
    )
    return build_evidence(
        claim=claim or default_claim,
        evidence_type=evidence_type,
        source=tool_name,
        observed=data if success else error,
        confidence="high",
        execution_state="executed" if success else "failed",
        failure_reason=None if success else (error or "工具返回 success=False"),
        next_check=None if success else "请先复核工具错误，再用只读检查确认状态后重试。",
    )


def tool_plan_evidence(tool_name: str, tool_args: dict) -> dict:
    """Evidence for a planned tool call that has not executed yet."""
    display_name = _display_tool_name(tool_name)
    return build_evidence(
        claim=f"准备调用工具：{display_name}",
        evidence_type="user input",
        source=tool_name,
        observed=tool_args,
        confidence="medium",
        execution_state="skipped",
    )


def verification_evidence(
    *,
    claim: str,
    source: str,
    observed: Any,
    success: bool,
    evidence_type: str = "command",
) -> dict:
    """Evidence for post-action verification or before/after comparison."""
    return build_evidence(
        claim=claim,
        evidence_type=evidence_type,
        source=source,
        observed=observed,
        confidence="high",
        execution_state="executed" if success else "failed",
        failure_reason=None if success else compact_observed(observed),
        next_check=None if success else "请重新执行只读状态或配置检查，定位执行结果不一致的原因。",
    )


def knowledge_evidence(count: int, observed: Any, *, failed: bool = False) -> dict:
    """Evidence for knowledge retrieval results."""
    if failed:
        return build_evidence(
            claim="知识库检索失败",
            evidence_type="knowledge",
            source="knowledge_store.search",
            observed=observed,
            confidence="high",
            execution_state="failed",
            failure_reason=compact_observed(observed),
            next_check="请先检查知识库或搜索后端，再依赖历史经验。",
        )
    return build_evidence(
        claim=f"知识库检索返回 {count} 条匹配结果",
        evidence_type="knowledge",
        source="knowledge_store.search",
        observed=observed,
        confidence="high" if count else "medium",
        execution_state="executed",
    )


def inference_evidence(claim: str, source: str, observed: Any = "") -> dict:
    """Evidence for planning or LLM-only inference steps."""
    return build_evidence(
        claim=_translate_claim(claim),
        evidence_type="user input",
        source=source,
        observed=observed,
        confidence="medium",
        execution_state="inferred",
    )


def _target_from_args(tool_args: dict) -> str:
    """Return a compact target name from common tool arguments."""
    for key in ("service", "filepath", "path", "dirpath", "source", "destination", "port", "username", "name"):
        value = tool_args.get(key)
        if value not in (None, ""):
            return str(value)
    return "当前系统"


def _display_tool_name(name: str, tool_def: Any = None) -> str:
    """Return a Chinese display label for tools and common trace sources."""
    if not name:
        return "未知来源"
    source_labels = {
        "SafetyGuardrail": "安全护栏",
        "SafetyGuardrail.check_input": "安全护栏",
        "SafetyGuardrail.check_command": "安全规则",
        "knowledge_store.search": "知识库检索",
        "LLM": "模型推断",
        "agent": "智能体",
        "approval_manager": "审批管理器",
        "read_only_intent_guard": "只读意图保护",
        "write_completion_guard": "写操作真实性保护",
        "BackupManager.backup_file": "备份管理器",
        "assess_impact": "影响评估",
        "runbook_executor": "Runbook执行器",
    }
    if name in source_labels:
        return source_labels[name]
    if tool_def is not None and getattr(tool_def, "display_name", ""):
        return tool_def.display_name
    try:
        from app.agent.tools_registry import tools_registry

        tool = tools_registry.get_tool(name)
        if tool and tool.display_name:
            return tool.display_name
    except Exception:
        pass
    return name


def _translate_claim(claim: str) -> str:
    labels = {
        "User request is being checked by safety rules": "正在根据安全规则检查用户请求",
        "Final response was generated from prior evidence and messages": "已基于现有证据和上下文生成最终回复",
        "Knowledge search has been requested": "正在检索历史经验",
        "The agent is planning next checks or actions": "智能体正在规划下一步检查或操作",
    }
    return labels.get(claim, claim)
