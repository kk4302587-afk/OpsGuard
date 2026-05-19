"""Operations report API - on-demand summary generation.

Generates a structured operations report from audit logs and session history.
Supports custom time ranges. Uses LLM to produce human-readable summaries.
"""

import json
from datetime import datetime, timedelta

import aiosqlite
from fastapi import APIRouter, Query

from app.database import get_audit_db_path, get_knowledge_db_path
from app.incidents.store import get_recent_incident_stats

router = APIRouter()


@router.get("/generate")
async def generate_ops_report(hours: int = Query(default=24, description="回溯时间（小时）")):
    """Generate an operations report for the specified time range.

    Aggregates:
    - All sessions and conversations
    - Tool calls executed
    - Safety events (blocks, warnings)
    - Problems resolved
    - Runbooks generated
    """
    since = (datetime.now() - timedelta(hours=hours)).isoformat()

    report = {
        "generated_at": datetime.now().isoformat(),
        "time_range": f"最近 {hours} 小时",
        "since": since,
        "sections": {},
    }

    # 1. Session summary
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        db.row_factory = aiosqlite.Row

        # Sessions created in time range
        cursor = await db.execute(
            "SELECT id, title, created_at FROM sessions WHERE created_at >= ? ORDER BY created_at DESC",
            (since,),
        )
        sessions = [dict(row) for row in await cursor.fetchall()]
        report["sections"]["sessions"] = {
            "title": "会话记录",
            "count": len(sessions),
            "items": sessions[:20],
        }

        # Messages count
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt, role FROM messages WHERE timestamp >= ? GROUP BY role",
            (since,),
        )
        msg_stats = {row["role"]: row["cnt"] for row in await cursor.fetchall()}
        report["sections"]["messages"] = {
            "title": "消息统计",
            "user_messages": msg_stats.get("user", 0),
            "agent_responses": msg_stats.get("assistant", 0),
        }

        # Knowledge entries created
        cursor = await db.execute(
            "SELECT problem_signature, solution FROM knowledge_entries WHERE created_at >= ? ORDER BY created_at DESC",
            (since,),
        )
        knowledge = [dict(row) for row in await cursor.fetchall()]
        report["sections"]["knowledge"] = {
            "title": "新增知识",
            "count": len(knowledge),
            "items": knowledge[:10],
        }

        # Runbooks generated
        cursor = await db.execute(
            "SELECT name, trigger_pattern, created_at FROM runbooks WHERE created_at >= ? ORDER BY created_at DESC",
            (since,),
        )
        runbooks = [dict(row) for row in await cursor.fetchall()]
        report["sections"]["runbooks"] = {
            "title": "新增 Runbook",
            "count": len(runbooks),
            "items": runbooks[:10],
        }

    incident_stats = await get_recent_incident_stats(since=since, limit=10)
    report["sections"]["incidents"] = {
        "title": "事件时间线",
        "count": incident_stats["total"],
        "by_status": incident_stats["by_status"],
        "items": incident_stats["recent"],
    }

    # 2. Audit log summary
    async with aiosqlite.connect(get_audit_db_path()) as db:
        db.row_factory = aiosqlite.Row

        # Tool calls
        cursor = await db.execute(
            "SELECT content, event_type FROM audit_logs WHERE phase = 'tool_call' AND timestamp >= ?",
            (since,),
        )
        tool_calls = await cursor.fetchall()
        tool_stats = {}
        for row in tool_calls:
            # Extract tool name from content like "工具调用: tool_name"
            content = row["content"]
            if ":" in content:
                tool_name = content.split(":")[-1].strip().split("(")[0].strip()
                tool_stats[tool_name] = tool_stats.get(tool_name, 0) + 1

        report["sections"]["tool_calls"] = {
            "title": "工具调用统计",
            "total": len(tool_calls),
            "by_tool": dict(sorted(tool_stats.items(), key=lambda x: x[1], reverse=True)[:15]),
        }

        # Safety events
        cursor = await db.execute(
            "SELECT content, event_type FROM audit_logs WHERE phase = 'safety_check' AND event_type = 'blocked' AND timestamp >= ?",
            (since,),
        )
        blocks = [dict(row) for row in await cursor.fetchall()]
        report["sections"]["security"] = {
            "title": "安全事件",
            "blocks": len(blocks),
            "details": [b["content"] for b in blocks[:10]],
        }

        # Approval events
        cursor = await db.execute(
            "SELECT content, event_type FROM audit_logs WHERE phase IN ('approval_request', 'approval_response') AND timestamp >= ?",
            (since,),
        )
        approvals = await cursor.fetchall()
        approved = sum(1 for a in approvals if a["event_type"] == "success")
        rejected = sum(1 for a in approvals if a["event_type"] == "failure")
        report["sections"]["approvals"] = {
            "title": "审批记录",
            "total_requests": len([a for a in approvals if "等待" in a["content"]]),
            "approved": approved,
            "rejected": rejected,
        }

    # 3. Generate summary text
    report["summary"] = _generate_summary_text(report)

    return report


def _generate_summary_text(report: dict) -> str:
    """Generate a human-readable summary from report data."""
    sections = report["sections"]
    lines = []

    lines.append(f"运维报告 ({report['time_range']})")
    lines.append(f"生成时间: {report['generated_at'][:19].replace('T', ' ')}")
    lines.append("")

    # Sessions
    s = sections.get("sessions", {})
    lines.append(f"会话: {s.get('count', 0)} 个")

    # Messages
    m = sections.get("messages", {})
    lines.append(f"交互: 用户 {m.get('user_messages', 0)} 条, Agent {m.get('agent_responses', 0)} 条")

    # Tools
    t = sections.get("tool_calls", {})
    lines.append(f"工具调用: {t.get('total', 0)} 次")
    if t.get("by_tool"):
        top3 = list(t["by_tool"].items())[:3]
        lines.append(f"  热门工具: {', '.join(f'{name}({cnt})' for name, cnt in top3)}")

    # Security
    sec = sections.get("security", {})
    if sec.get("blocks", 0) > 0:
        lines.append(f"安全拦截: {sec['blocks']} 次")

    # Approvals
    app = sections.get("approvals", {})
    if app.get("total_requests", 0) > 0:
        lines.append(f"审批: {app['approved']} 批准, {app['rejected']} 拒绝")

    # Knowledge
    k = sections.get("knowledge", {})
    if k.get("count", 0) > 0:
        lines.append(f"新增知识: {k['count']} 条")

    # Runbooks
    r = sections.get("runbooks", {})
    if r.get("count", 0) > 0:
        lines.append(f"新增 Runbook: {r['count']} 个")

    # Incidents
    inc = sections.get("incidents", {})
    if inc.get("count", 0) > 0:
        by_status = inc.get("by_status") or {}
        lines.append(
            f"事件时间线: {inc['count']} 个 "
            f"(resolved {by_status.get('resolved', 0)}, failed {by_status.get('failed', 0)}, open {by_status.get('open', 0)})"
        )

    return "\n".join(lines)
