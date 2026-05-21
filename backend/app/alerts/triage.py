"""Read-only alert webhook auto-triage.

The webhook flow is deterministic by design: it normalizes external alert
payloads, picks a built-in read-only template, executes only registered
``RiskLevel.READ`` tools, and persists every check as audit + incident evidence.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiosqlite
from loguru import logger

from app.agent.tools_registry import RiskLevel, tools_registry
from app.agent.trace_evidence import build_evidence, trace_event, tool_result_evidence
from app.database import get_audit_db_path, get_knowledge_db_path
from app.incidents import store as incident_store


@dataclass
class NormalizedAlert:
    """Stable internal representation of one external alert."""

    alertname: str
    status: str = "firing"
    service: str = ""
    instance: str = ""
    severity: str = ""
    description: str = ""
    mountpoint: str = ""
    labels: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TriageStep:
    """A single deterministic read-only triage step."""

    tool_name: str
    tool_args: dict[str, Any]
    purpose: str
    skip_reason: str = ""


def normalize_alert_payload(payload: dict[str, Any]) -> list[NormalizedAlert]:
    """Normalize Alertmanager, Grafana, or simple custom alert payloads."""
    candidates = payload.get("alerts")
    if not isinstance(candidates, list):
        candidates = [payload]

    alerts: list[NormalizedAlert] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        labels = _coerce_dict(item.get("labels"))
        annotations = _coerce_dict(item.get("annotations"))
        merged = {**_coerce_dict(payload.get("commonLabels")), **labels}
        merged_annotations = {**_coerce_dict(payload.get("commonAnnotations")), **annotations}

        alertname = _first_text(
            merged.get("alertname"),
            item.get("alertname"),
            payload.get("alertname"),
            merged_annotations.get("summary"),
            "External alert",
        )
        service = _first_text(
            merged.get("service"),
            merged.get("service_name"),
            merged.get("unit"),
            merged.get("job"),
            merged.get("app"),
            item.get("service"),
            payload.get("service"),
        )
        alerts.append(
            NormalizedAlert(
                alertname=alertname,
                status=_first_text(item.get("status"), payload.get("status"), "firing"),
                service=service,
                instance=_first_text(
                    merged.get("instance"),
                    merged.get("host"),
                    merged.get("node"),
                    merged.get("pod"),
                    item.get("instance"),
                    payload.get("instance"),
                ),
                severity=_first_text(merged.get("severity"), item.get("severity"), payload.get("severity")),
                description=_first_text(
                    merged_annotations.get("description"),
                    merged_annotations.get("message"),
                    merged_annotations.get("summary"),
                    item.get("description"),
                    payload.get("description"),
                ),
                mountpoint=_first_text(
                    merged.get("mountpoint"),
                    merged.get("path"),
                    item.get("mountpoint"),
                    payload.get("mountpoint"),
                    "/",
                ),
                labels=merged,
                annotations=merged_annotations,
                raw=item,
            )
        )
    return alerts


async def run_alert_auto_triage(payload: dict[str, Any]) -> dict[str, Any]:
    """Run read-only auto-triage for all alerts in a webhook payload."""
    alerts = normalize_alert_payload(payload)
    results = []
    for alert in alerts:
        results.append(await _triage_one_alert(alert))

    response: dict[str, Any] = {"count": len(results), "results": results}
    if len(results) == 1:
        response.update(results[0])
    return response


async def _triage_one_alert(alert: NormalizedAlert) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    now = _now()
    title = _session_title(alert)
    user_message = _alert_user_message(alert)

    await _create_session_with_message(session_id, title, user_message, now)

    incident_id = await incident_store.create_incident(
        session_id=session_id,
        problem_statement=user_message,
        source="alert_webhook",
        metadata={"alert": _alert_to_dict(alert)},
    )

    await _record_trace(
        session_id=session_id,
        incident_id=incident_id,
        event=trace_event(
            phase="input_received",
            event_type="success",
            content=f"告警 Webhook 已接收：{alert.alertname}",
            evidence=build_evidence(
                claim="告警 Webhook 已转换为自动分析请求",
                evidence_type="user input",
                source="alert_webhook",
                observed=_alert_to_dict(alert),
                confidence="high",
                execution_state="executed",
            ),
            metadata={"alert": _alert_to_dict(alert)},
        ),
    )

    template = _match_template(alert)
    checks: list[dict[str, Any]] = []
    for step in _steps_for_template(template, alert):
        checks.append(await _execute_step(session_id, incident_id, step))

    report = _build_report(alert, template, checks)
    final_status = "failed" if any(check["status"] == "failed" for check in checks) else "resolved"
    summary = await incident_store.finalize_incident(
        incident_id=incident_id,
        final_summary=report,
        status=final_status,
    )
    report = await incident_store.append_incident_reference(report, incident_id)
    message_id = await _save_assistant_message(session_id, report)

    await _record_trace(
        session_id=session_id,
        incident_id=incident_id,
        event=trace_event(
            phase="response",
            event_type="success",
            content="告警自动分析报告已生成",
            evidence=build_evidence(
                claim="告警自动分析报告已保存为智能体回复",
                evidence_type="user input",
                source="alert_webhook",
                observed={"message_id": message_id, "incident_id": incident_id},
                confidence="high",
                execution_state="executed",
            ),
        ),
    )

    return {
        "session_id": session_id,
        "incident_id": incident_id,
        "message_id": message_id,
        "template": template,
        "alert": _alert_to_dict(alert),
        "checks": checks,
        "report": report,
        "incident_summary": summary,
    }


async def _execute_step(
    session_id: str,
    incident_id: str,
    step: TriageStep,
) -> dict[str, Any]:
    display_name = _display_tool_name(step.tool_name)
    if step.skip_reason:
        evidence = build_evidence(
            claim=f"{display_name} 已在执行前跳过",
            evidence_type="command",
            source=step.tool_name,
            observed=step.skip_reason,
            confidence="high",
            execution_state="skipped",
            next_check=step.skip_reason,
        )
        await _record_trace(
            session_id=session_id,
            incident_id=incident_id,
            event=trace_event(
                phase="execution",
                event_type="skipped",
                content=f"已跳过 {display_name}: {step.skip_reason}",
                evidence=evidence,
                metadata={"tool_name": step.tool_name, "tool_args": step.tool_args},
            ),
        )
        return _check_summary(step, "skipped", step.skip_reason, evidence)

    tool_def = tools_registry.get_tool(step.tool_name)
    if not tool_def:
        reason = f"工具未注册: {display_name}"
        evidence = build_evidence(
            claim=f"{display_name} 无法执行：工具未注册",
            evidence_type="command",
            source=step.tool_name,
            observed=reason,
            confidence="high",
            execution_state="failed",
            failure_reason=reason,
        )
        await _record_trace(
            session_id=session_id,
            incident_id=incident_id,
            event=trace_event(
                phase="execution",
                event_type="failure",
                content=reason,
                evidence=evidence,
            ),
        )
        return _check_summary(step, "failed", reason, evidence)

    if tool_def.risk_level != RiskLevel.READ:
        reason = f"告警自动分析已阻断非只读工具: {display_name}"
        evidence = build_evidence(
            claim=f"{display_name} 已被阻断：告警自动分析只允许只读检查",
            evidence_type="command",
            source=step.tool_name,
            observed={"risk_level": tool_def.risk_level},
            confidence="high",
            execution_state="skipped",
            failure_reason=reason,
        )
        await _record_trace(
            session_id=session_id,
            incident_id=incident_id,
            event=trace_event(
                phase="execution",
                event_type="blocked",
                content=reason,
                evidence=evidence,
            ),
        )
        return _check_summary(step, "skipped", reason, evidence)

    await _record_trace(
        session_id=session_id,
        incident_id=incident_id,
        event=trace_event(
            phase="tool_call",
            event_type="start",
            content=f"告警自动分析准备执行只读工具: {display_name}",
            evidence=build_evidence(
                claim=f"告警自动分析准备执行只读工具: {display_name}",
                evidence_type="command",
                source=step.tool_name,
                observed=step.tool_args,
                confidence="medium",
                execution_state="skipped",
            ),
            metadata={"tool_name": step.tool_name, "tool_args": step.tool_args, "purpose": step.purpose},
        ),
    )

    try:
        result = tool_def.function(**step.tool_args)
    except Exception as exc:
        logger.exception(f"Alert auto-triage tool exception: {step.tool_name}")
        evidence = build_evidence(
            claim=f"{display_name} 在告警自动分析中执行异常",
            evidence_type="command",
            source=step.tool_name,
            observed=str(exc),
            confidence="high",
            execution_state="failed",
            failure_reason=str(exc),
        )
        await _record_trace(
            session_id=session_id,
            incident_id=incident_id,
            event=trace_event(
                phase="execution",
                event_type="failure",
                content=f"{display_name} 执行异常: {exc}",
                evidence=evidence,
            ),
        )
        return _check_summary(step, "failed", str(exc), evidence)

    success = bool(getattr(result, "success", True))
    evidence = tool_result_evidence(
        tool_name=step.tool_name,
        tool_args=step.tool_args,
        tool_def=tool_def,
        result=result,
        claim=f"{step.purpose}: {display_name} 返回{'成功' if success else '失败'}",
    )
    observed = evidence.get("observed") or getattr(result, "error", "")
    await _record_trace(
        session_id=session_id,
        incident_id=incident_id,
        event=trace_event(
            phase="execution",
            event_type="success" if success else "failure",
            content=f"{display_name} {'执行完成' if success else '执行失败'}",
            evidence=evidence,
            metadata={"tool_name": step.tool_name, "tool_args": step.tool_args, "purpose": step.purpose},
        ),
    )
    return _check_summary(step, "executed" if success else "failed", observed, evidence)


def _steps_for_template(template: str, alert: NormalizedAlert) -> list[TriageStep]:
    if template == "high_disk_usage":
        return [
            TriageStep("get_disk_usage", {"path": alert.mountpoint or "/"}, "检查当前磁盘使用率"),
            TriageStep("get_recent_changes", {"window_hours": 24, "limit": 30}, "检查近期系统变更"),
        ]

    return [
        TriageStep(
            "get_service_status",
            {"service": alert.service},
            "检查 systemd 服务状态",
            "" if alert.service else "告警未包含 service/unit 标签",
        ),
        TriageStep(
            "get_service_logs",
            {"service": alert.service, "lines": 50},
            "查看最近服务日志",
            "" if alert.service else "告警未包含 service/unit 标签",
        ),
        TriageStep("get_listening_ports", {}, "检查监听端口"),
        TriageStep("get_recent_changes", {"window_hours": 24, "limit": 30}, "检查近期系统变更"),
    ]


def _match_template(alert: NormalizedAlert) -> str:
    haystack = " ".join(
        str(value).lower()
        for value in [
            alert.alertname,
            alert.description,
            alert.labels.get("alertname"),
            alert.labels.get("mountpoint"),
        ]
        if value
    )
    disk_tokens = ("disk", "filesystem", "fs", "space", "inode", "volume", "storage")
    if alert.mountpoint and any(token in haystack for token in disk_tokens):
        return "high_disk_usage"
    if any(token in haystack for token in ("highdisk", "diskfull", "filesystemfull")):
        return "high_disk_usage"
    return "service_down"


def _display_tool_name(tool_name: str) -> str:
    tool_def = tools_registry.get_tool(tool_name)
    return getattr(tool_def, "display_name", "") or tool_name


async def _create_session_with_message(
    session_id: str,
    title: str,
    content: str,
    timestamp: str,
) -> None:
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, status) VALUES (?, ?, ?, ?, 'active')",
            (session_id, title[:200], timestamp, timestamp),
        )
        await db.execute(
            "INSERT INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, 'user', ?, ?)",
            (str(uuid.uuid4()), session_id, content, timestamp),
        )
        await db.commit()


async def _save_assistant_message(session_id: str, report: str) -> str:
    message_id = str(uuid.uuid4())
    now = _now()
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await db.execute(
            "INSERT INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, 'assistant', ?, ?)",
            (message_id, session_id, report, now),
        )
        await db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        await db.commit()
    return message_id


async def _record_trace(session_id: str, incident_id: str, event: dict[str, Any]) -> None:
    event.setdefault("timestamp", _now())
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    async with aiosqlite.connect(get_audit_db_path()) as db:
        await db.execute(
            """
            INSERT INTO audit_logs (session_id, timestamp, phase, event_type, content, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                event["timestamp"],
                str(event.get("phase") or "unknown"),
                str(event.get("event_type") or "event"),
                str(event.get("content") or ""),
                json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None,
            ),
        )
        await db.commit()

    await incident_store.record_incident_from_message(
        incident_id=incident_id,
        session_id=session_id,
        message=event,
    )


def _build_report(alert: NormalizedAlert, template: str, checks: list[dict[str, Any]]) -> str:
    lines = [
        f"告警自动分析报告: {alert.alertname}",
        "",
        "告警摘要",
        f"- 状态: {alert.status}",
        f"- 严重级别: {alert.severity or '未知'}",
        f"- 服务: {alert.service or '未提供'}",
        f"- 实例: {alert.instance or '未提供'}",
        f"- 匹配模板: {template}",
    ]
    if template == "high_disk_usage":
        lines.append(f"- 挂载点: {alert.mountpoint or '/'}")
    if alert.description:
        lines.append(f"- 描述: {alert.description}")

    lines.extend(["", "检查结果"])
    for check in checks:
        lines.append(f"- {_display_tool_name(check['tool_name'])}: {_format_check_status(check['status'])} - {check['summary']}")

    failures = [check for check in checks if check["status"] == "failed"]
    skipped = [check for check in checks if check["status"] == "skipped"]
    lines.extend(["", "下一步建议"])
    if failures:
        lines.append("- 先复核失败检查的错误信息，再决定是否采信本次自动分析结果。")
    if skipped:
        lines.append("- 补充缺失的告警标签，便于下次自动执行被跳过的检查。")
    if not failures and not skipped:
        lines.append("- 查看事件时间线；如果症状仍存在，继续人工 RCA。")
    return "\n".join(lines)


def _format_check_status(status: str) -> str:
    return {
        "executed": "已执行",
        "failed": "失败",
        "skipped": "已跳过",
    }.get(status, status)


def _check_summary(
    step: TriageStep,
    status: str,
    observed: Any,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "tool_name": step.tool_name,
        "tool_args": step.tool_args,
        "purpose": step.purpose,
        "status": status,
        "summary": _compact(observed),
        "evidence": evidence,
    }


def _alert_user_message(alert: NormalizedAlert) -> str:
    parts = [
        f"告警: {alert.alertname}",
        f"状态: {alert.status}",
        f"严重级别: {alert.severity or '未知'}",
    ]
    if alert.service:
        parts.append(f"服务: {alert.service}")
    if alert.instance:
        parts.append(f"实例: {alert.instance}")
    if alert.mountpoint:
        parts.append(f"挂载点: {alert.mountpoint}")
    if alert.description:
        parts.append(f"描述: {alert.description}")
    parts.append("请求: 只执行只读自动分析，不要自动修复。")
    return "\n".join(parts)


def _session_title(alert: NormalizedAlert) -> str:
    target = alert.service or alert.instance or alert.mountpoint or "unknown target"
    return f"[Alert] {alert.alertname} - {target}"


def _alert_to_dict(alert: NormalizedAlert) -> dict[str, Any]:
    return {
        "alertname": alert.alertname,
        "status": alert.status,
        "service": alert.service,
        "instance": alert.instance,
        "severity": alert.severity,
        "description": alert.description,
        "mountpoint": alert.mountpoint,
        "labels": alert.labels,
        "annotations": alert.annotations,
    }


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def _compact(value: Any, max_chars: int = 500) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value or "")
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _now() -> str:
    return datetime.now().isoformat()
