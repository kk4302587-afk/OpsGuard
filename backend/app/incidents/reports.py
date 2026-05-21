"""Incident handoff and postmortem draft generation.

Drafts are built from persisted incident timeline events only. The generator is
intentionally deterministic for the MVP so it cannot invent root cause, impact,
or mitigation details that the timeline does not contain.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent.tools_registry import tools_registry
from app.incidents.store import get_incident, get_incident_events


CONFIRMED_STATES = {"executed", "failed"}
INFERRED_STATES = {"inferred"}


async def generate_handoff_note(incident_id: str, *, db_path: str | None = None) -> dict | None:
    """Generate a short operational handoff note for one incident."""
    incident = await get_incident(incident_id, db_path=db_path)
    if not incident:
        return None
    events = await get_incident_events(incident_id, db_path=db_path)
    facts = _confirmed_facts(events)
    failures = _failure_events(events)
    next_checks = _next_checks(events)

    lines = [
        f"# 事件交接：{incident['id']}",
        "",
        f"- 状态：{_status_label(incident.get('status'))}",
        f"- 会话：{incident.get('session_id') or '未知'}",
        f"- 创建时间：{_fmt_time(incident.get('created_at'))}",
        f"- 更新时间：{_fmt_time(incident.get('updated_at'))}",
        "",
        "## 问题",
        _text_or_placeholder(incident.get("problem_statement"), "未记录问题描述。"),
        "",
        "## 当前状态",
        _current_state(incident, failures),
        "",
        "## 已确认事实",
        *_bullet_lines(facts, "暂无已确认的执行证据。"),
        "",
        "## 失败与风险",
        *_bullet_lines(failures, "暂无失败执行证据。"),
        "",
        "## 下一步检查",
        *_bullet_lines(next_checks, "请基于事件时间线继续诊断。"),
    ]
    markdown = "\n".join(lines)
    return {"incident": incident, "type": "handoff", "markdown": markdown}


async def generate_postmortem_draft(incident_id: str, *, db_path: str | None = None) -> dict | None:
    """Generate a detailed postmortem Markdown draft for one incident."""
    incident = await get_incident(incident_id, db_path=db_path)
    if not incident:
        return None
    events = await get_incident_events(incident_id, db_path=db_path)
    facts = _confirmed_facts(events)
    hypotheses = _inferred_hypotheses(events)
    failures = _failure_events(events)
    timeline = _timeline_lines(events)
    mitigations = _mitigation_lines(events)
    verification = _verification_lines(events)
    action_items = _action_items(incident, events)
    runbook_suggestions = _runbook_suggestions(events)

    lines = [
        f"# 事件复盘草稿：{incident['id']}",
        "",
        "## 摘要",
        _summary_line(incident, failures),
        "",
        "## 用户 / 业务影响",
        "[待补充] 请结合监控、告警或业务反馈补充影响范围、持续时间和受影响对象。",
        "",
        "## 时间线",
        *_bullet_lines(timeline, "暂无事件时间线。"),
        "",
        "## 已确认事实",
        *_bullet_lines(facts, "暂无已确认的执行证据。"),
        "",
        "## 推断假设",
        *_bullet_lines(hypotheses, "暂无推断假设。"),
        "",
        "## 原因",
        _cause_line(facts, failures),
        "",
        "## 缓解措施",
        *_bullet_lines(mitigations, "事件时间线中暂无已确认的缓解动作。"),
        "",
        "## 验证",
        *_bullet_lines(verification, "暂无验证证据。"),
        "",
        "## 后续事项",
        *_bullet_lines(action_items, "请人工复核事件，并补充负责人和计划完成时间。"),
        "",
        "## Runbook 优化建议",
        *_bullet_lines(runbook_suggestions, "现有证据中暂无明确的 Runbook 优化建议。"),
        "",
        "## 证据边界",
        "以上已确认事实仅来自 execution_state=executed 或 execution_state=failed 的时间线证据。"
        "推断假设和待补充内容已明确标注，发布前必须再次核验。",
    ]
    markdown = "\n".join(lines)
    return {"incident": incident, "type": "postmortem", "markdown": markdown}


def _confirmed_facts(events: list[dict]) -> list[str]:
    facts = []
    for event in events:
        evidence = _evidence(event)
        if evidence.get("execution_state") in CONFIRMED_STATES:
            facts.append(_event_fact(event, evidence))
    return _dedupe(facts)


def _inferred_hypotheses(events: list[dict]) -> list[str]:
    hypotheses = []
    for event in events:
        evidence = _evidence(event)
        if evidence.get("execution_state") in INFERRED_STATES:
            hypotheses.append(_event_fact(event, evidence, prefix="推断"))
    return _dedupe(hypotheses)


def _failure_events(events: list[dict]) -> list[str]:
    failures = []
    for event in events:
        evidence = _evidence(event)
        if event.get("event_type") in {"failure", "blocked"} or evidence.get("execution_state") == "failed":
            reason = evidence.get("failure_reason") or event.get("detail") or event.get("title")
            failures.append(_compact(reason))
    return _dedupe(failures)


def _next_checks(events: list[dict]) -> list[str]:
    checks = []
    for event in events:
        next_check = _evidence(event).get("next_check")
        if next_check:
            checks.append(_compact(next_check))
    return _dedupe(checks)


def _timeline_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        stamp = _fmt_time(event.get("timestamp"))
        title = _compact(event.get("title") or event.get("detail") or event.get("phase"))
        state = _evidence(event).get("execution_state")
        suffix = f" [{_state_label(state)}]" if state else ""
        lines.append(f"{stamp} - {_phase_label(event.get('phase'))} / {_event_type_label(event.get('event_type'))}: {title}{suffix}")
    return lines


def _mitigation_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        text = " ".join(str(event.get(key) or "") for key in ("title", "detail")).lower()
        evidence = _evidence(event)
        if evidence.get("execution_state") == "executed" and any(word in text for word in ("rollback", "restore", "restart", "start", "stop", "write", "runbook")):
            lines.append(_event_fact(event, evidence))
    return _dedupe(lines)


def _verification_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        evidence = _evidence(event)
        if event.get("phase") == "verification" or "verify" in str(evidence.get("claim", "")).lower():
            lines.append(_event_fact(event, evidence))
    return _dedupe(lines)


def _action_items(incident: dict, events: list[dict]) -> list[str]:
    items = []
    failures = _failure_events(events)
    if incident.get("status") != "resolved":
        items.append("指定负责人继续诊断，直到事件关闭。")
    if failures:
        items.append("复核失败检查，并记录失败原因属于环境、权限还是服务本身问题。")
    if not _verification_lines(events):
        items.append("关闭复盘前补充明确的验证证据。")
    if not incident.get("final_summary"):
        items.append("缓解确认后补充最终事件总结。")
    return items


def _runbook_suggestions(events: list[dict]) -> list[str]:
    suggestions = []
    sources = [_evidence(event).get("source") for event in events]
    if "get_recent_changes" in sources:
        suggestions.append("在相关 Runbook 前置步骤中加入最近系统变更检查。")
    if any(event.get("phase") == "approval_request" for event in events):
        suggestions.append("在相关 Runbook 中补充审批影响和回滚预期。")
    if any(source in sources for source in ("get_service_status", "get_service_logs")):
        suggestions.append("将服务状态和最近日志检查沉淀为可复用的 Runbook 验证步骤。")
    return _dedupe(suggestions)


def _summary_line(incident: dict, failures: list[str]) -> str:
    status = incident.get("status") or "unknown"
    problem = _compact(incident.get("problem_statement") or "未记录问题描述")
    if status == "resolved":
        return f"事件已标记为已解决。问题：{problem}"
    if failures:
        return f"事件状态为{_status_label(status)}。存在失败证据，需要继续跟进。问题：{problem}"
    return f"事件状态为{_status_label(status)}。问题：{problem}"


def _current_state(incident: dict, failures: list[str]) -> str:
    status = incident.get("status") or "unknown"
    if status == "resolved":
        return "事件记录显示已解决。交接完成前仍建议确认服务健康状态。"
    if failures:
        return "尚未完全解决。时间线中仍有失败检查，需要继续跟进。"
    return "事件仍在处理中或未关闭。请从最新时间线事件继续诊断。"


def _cause_line(facts: list[str], failures: list[str]) -> str:
    if failures:
        return "系统不会自动声称已确认根因。请复核失败证据，并将其关联到可验证的根因。"
    if facts:
        return "时间线尚未确认明确根因。请以上方已确认事实作为证据输入继续分析。"
    return "[待补充] 事件证据尚未确认根因。"


def _event_fact(event: dict, evidence: dict, prefix: str = "事实") -> str:
    source = _source_label(evidence.get("source") or event.get("phase") or "timeline")
    claim = evidence.get("claim") or event.get("title") or event.get("detail") or "timeline event"
    observed = evidence.get("observed") or event.get("detail") or ""
    state = _state_label(evidence.get("execution_state") or "unknown")
    return f"{prefix}：{source} [{state}] - {_compact(claim)}" + (f" | 观测：{_compact(observed)}" if observed else "")


def _evidence(event: dict) -> dict:
    evidence = event.get("evidence")
    return evidence if isinstance(evidence, dict) else {}


def _bullet_lines(items: list[str], empty_text: str) -> list[str]:
    values = items or [empty_text]
    return [f"- {item}" for item in values]


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _text_or_placeholder(value: Any, placeholder: str) -> str:
    text = _compact(value)
    return text or placeholder


def _compact(value: Any, max_chars: int = 400) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _fmt_time(value: Any) -> str:
    text = str(value or "")
    if not text:
        return "未知"
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text.replace("T", " ")[:19]


def _status_label(status: Any) -> str:
    return {
        "resolved": "已解决",
        "failed": "失败",
        "open": "处理中",
        "active": "处理中",
        "unknown": "未知",
    }.get(str(status or "unknown"), str(status or "未知"))


def _state_label(state: Any) -> str:
    return {
        "executed": "已执行",
        "failed": "失败",
        "inferred": "推断",
        "skipped": "未执行",
        "unknown": "未知",
    }.get(str(state or "unknown"), str(state or "未知"))


def _phase_label(phase: Any) -> str:
    return {
        "planning": "规划",
        "safety_check": "安全校验",
        "knowledge_retrieval": "知识检索",
        "tool_call": "工具调用",
        "approval_request": "审批请求",
        "approval_response": "审批结果",
        "execution": "执行",
        "verification": "验证",
        "response": "回复生成",
    }.get(str(phase or ""), str(phase or "未知"))


def _event_type_label(event_type: Any) -> str:
    return {
        "start": "开始",
        "success": "成功",
        "failure": "失败",
        "blocked": "已拦截",
        "pending": "待处理",
        "info": "信息",
    }.get(str(event_type or ""), str(event_type or "未知"))


def _source_label(source: Any) -> str:
    value = str(source or "")
    tool = tools_registry.get_tool(value)
    if tool and tool.display_name:
        return tool.display_name
    return {
        "LLM": "模型推断",
        "timeline": "事件时间线",
        "agent": "智能体",
    }.get(value, value or "未知来源")
