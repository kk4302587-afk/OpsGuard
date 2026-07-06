"""Structured change plans for approval and audit surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent.rollback_plan import effective_rollback_capability
from app.agent.tools_registry import RiskLevel


def build_change_plan(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_def: Any,
    preview: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    runbook: dict[str, Any] | None = None,
    step_index: int | None = None,
    total_steps: int | None = None,
    approval_status: str = "pending",
    approval_request_id: str = "",
) -> dict[str, Any]:
    """Build a normalized object that describes one pending system change."""
    risk_level = _risk_value(getattr(tool_def, "risk_level", "unknown"))
    supports_rollback, rollback_strategy = effective_rollback_capability(tool_name, tool_args, tool_def)
    target = _target(tool_args)
    display_name = getattr(tool_def, "display_name", "") or tool_name
    action = _action_text(tool_name, tool_args, display_name)
    plan_id = approval_request_id or f"plan:{tool_name}:{int(datetime.now().timestamp())}"

    risks = _risk_items(tool_name, tool_args, risk_level, policy)
    rollback = {
        "supported": supports_rollback,
        "strategy": rollback_strategy,
        "label": _rollback_label(rollback_strategy) if supports_rollback else "无可靠自动回滚",
        "precondition": _rollback_precondition(tool_name, rollback_strategy),
        "record_created": False,
        "record_id": "",
        "record": None,
    }

    return {
        "id": plan_id,
        "kind": "runbook_step" if runbook else "agent_tool_call",
        "created_at": datetime.now().isoformat(),
        "tool_name": tool_name,
        "tool_display_name": display_name,
        "description": getattr(tool_def, "description", "") or "",
        "target": target,
        "risk_level": risk_level,
        "approval": {
            "request_id": approval_request_id,
            "status": approval_status,
            "level": (policy or {}).get("approval_level") or _approval_level(risk_level),
        },
        "steps": [
            {
                "index": 1,
                "title": action,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "target": target,
                "risk_level": risk_level,
                "approval_status": approval_status,
            }
        ],
        "risks": risks,
        "rollback": rollback,
        "validation": _validation_items(tool_name, target),
        "preview": {
            "status": (preview or {}).get("status") or "unavailable",
            "type": (preview or {}).get("preview_type") or "none",
            "has_diff": bool((preview or {}).get("diff")),
            "warnings": list((preview or {}).get("warnings") or []),
            "limitations": list((preview or {}).get("limitations") or []),
        },
        "policy": policy or {},
        "runbook": runbook or None,
    }


def mark_change_plan_rollback(change_plan: dict[str, Any] | None, rollback_record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach the actual rollback record created immediately before execution."""
    if not change_plan:
        return change_plan
    rollback = dict(change_plan.get("rollback") or {})
    rollback["record_created"] = bool(rollback_record)
    rollback["record_id"] = str((rollback_record or {}).get("id") or "")
    rollback["record"] = rollback_record
    change_plan["rollback"] = rollback
    return change_plan


def change_plan_summary(change_plan: dict[str, Any] | None) -> str:
    """Compact text for traces and incident details."""
    if not change_plan:
        return ""
    lines = [
        f"变更计划：{change_plan.get('tool_display_name') or change_plan.get('tool_name')}",
        f"目标：{change_plan.get('target') or '当前系统'}",
        f"风险：{change_plan.get('risk_level') or 'unknown'}",
    ]
    rollback = change_plan.get("rollback") or {}
    lines.append(f"回滚：{rollback.get('label') or '无可靠自动回滚'}")
    validation = change_plan.get("validation") or []
    if validation:
        lines.append("验证：" + "；".join(str(item.get("title") or item) for item in validation[:3]))
    return "\n".join(lines)


def _risk_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "unknown")


def _target(tool_args: dict[str, Any]) -> str:
    for key in ("filepath", "dirpath", "path", "source", "destination", "service", "port", "username", "name", "backup_id"):
        value = tool_args.get(key)
        if value not in (None, ""):
            if key == "port":
                return f"{value}/{tool_args.get('protocol') or 'tcp'}"
            return str(value)
    return "当前系统"


def _action_text(tool_name: str, tool_args: dict[str, Any], display_name: str) -> str:
    if tool_name == "write_file":
        return "追加文件内容" if tool_args.get("append") else "覆盖写入文件"
    if tool_name == "create_file":
        return "覆盖创建文件" if tool_args.get("overwrite") else "创建新文件"
    if tool_name == "create_directory":
        return "创建目录"
    if tool_name in {"delete_file", "delete_directory"}:
        return "删除目标"
    if tool_name == "move_file":
        return "移动或重命名路径"
    if tool_name == "copy_file":
        return "复制路径"
    if tool_name == "change_permissions":
        return f"修改权限为 {tool_args.get('mode')}"
    if tool_name == "change_owner":
        return f"修改属主为 {tool_args.get('owner')}"
    if tool_name == "restart_service":
        return "重启服务"
    if tool_name == "start_service":
        return "启动服务"
    if tool_name == "stop_service":
        return "停止服务"
    if tool_name == "allow_port":
        return "开放防火墙端口"
    if tool_name == "block_port":
        return "关闭防火墙端口"
    if tool_name == "add_cron_job":
        return "添加定时任务"
    if tool_name == "remove_cron_job":
        return "删除定时任务"
    if tool_name == "rollback_backup":
        return "恢复回滚点"
    return display_name


def _risk_items(tool_name: str, tool_args: dict[str, Any], risk_level: str, policy: dict[str, Any] | None) -> list[dict[str, str]]:
    items = [{
        "level": risk_level,
        "title": "系统状态会被修改",
        "detail": "该操作只有审批通过后才会执行。",
    }]
    if tool_name in {"delete_file", "delete_directory", "rollback_backup"}:
        items.append({"level": "destructive", "title": "当前内容可能被移除或覆盖", "detail": "请重点核对目标路径和预览差异。"})
    if tool_name in {"restart_service", "start_service", "stop_service"}:
        items.append({"level": "write", "title": "服务状态会变化", "detail": "可能造成短暂中断或连接重建。"})
    if tool_name in {"allow_port", "block_port"}:
        items.append({"level": "write", "title": "网络访问面会变化", "detail": "可能影响入站连接或暴露范围。"})
    reasons = list((policy or {}).get("warnings") or [])
    if reasons:
        items.append({"level": "policy", "title": "策略提示", "detail": "；".join(str(item) for item in reasons[:3])})
    return items


def _validation_items(tool_name: str, target: str) -> list[dict[str, str]]:
    if tool_name in {"create_file", "create_directory", "write_file", "delete_file", "delete_directory", "move_file", "copy_file"}:
        return [{"title": "检查文件系统目标状态", "target": target}]
    if tool_name in {"change_permissions", "change_owner"}:
        return [{"title": "检查目标元数据", "target": target}]
    if tool_name in {"restart_service", "start_service", "stop_service"}:
        return [{"title": "检查 systemd active 状态", "target": target}]
    if tool_name in {"allow_port", "block_port"}:
        return [{"title": "检查防火墙规则状态", "target": target}]
    if tool_name in {"add_cron_job", "remove_cron_job"}:
        return [{"title": "检查 crontab 内容", "target": target}]
    if tool_name == "rollback_backup":
        return [{"title": "检查回滚后目标状态", "target": target}]
    return [{"title": "检查工具执行结果", "target": target}]


def _rollback_precondition(tool_name: str, strategy: str) -> str:
    if strategy == "backup":
        return "执行前必须成功复制当前文件或目录"
    if strategy == "inverse_action":
        return "执行前必须确认目标状态允许反向操作"
    if strategy == "service_state":
        return "执行前必须记录服务 active 状态"
    if strategy == "snapshot_restore":
        return "执行前必须记录当前规则或任务快照"
    if tool_name == "rollback_backup":
        return "恢复动作本身不再声明二次自动回滚"
    return "无"


def _rollback_label(strategy: str) -> str:
    return {
        "backup": "备份回滚",
        "inverse_action": "反向操作",
        "service_state": "服务状态恢复",
        "snapshot_restore": "快照恢复",
        "manual": "手动回滚",
    }.get(strategy or "none", "无可靠自动回滚")


def _approval_level(risk_level: str) -> str:
    if risk_level == RiskLevel.DESTRUCTIVE.value:
        return "destructive"
    if risk_level == RiskLevel.WRITE.value:
        return "standard"
    return "none"
