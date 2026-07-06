"""Replay a saved Runbook step-by-step with full safety + approval.

For each step:
- The command is re-checked against the rule engine (writes can't slip through
  even if config patterns changed after the runbook was recorded).
- WRITE/DESTRUCTIVE tools route through the **same** approval_manager the
  Agent uses, so the user must explicitly approve each write step *every
  single replay*, identical to a fresh Agent run.
- READ tools execute directly.
- Each step pushes trace events so the existing TracePanel UI works without
  modification.

This is the "B" side of the B+C integration. The matcher (C) merely chooses
which runbook to suggest; once the user confirms, this module does the work.
"""

import asyncio
import json
import re
import uuid
from typing import Callable

import aiosqlite
from loguru import logger

from app.agent.tool_executor import execute_tool
from app.agent.tool_execution_store import record_tool_execution
from app.agent.tools_registry import tools_registry, RiskLevel
from app.agent.runbook_governance import ensure_runbook_schema, record_runbook_result
from app.agent.runbook_preflight import preflight_runbook
from app.agent.runbook_governance import serialize_runbook
from app.agent.execution_policy import evaluate_tool_policy, policy_summary
from app.agent.operation_preview import build_operation_preview
from app.agent.change_plan import build_change_plan, change_plan_summary, mark_change_plan_rollback
from app.agent.rollback_plan import effective_rollback_capability, prepare_rollback_point, rollback_summary
from app.agent.llm import call_llm
from app.agent.trace_evidence import (
    build_evidence,
    trace_event,
    verification_evidence,
)
from app.audit.logger import audit_logger, AuditPhase, AuditEventType
from app.config import settings
from app.database import get_knowledge_db_path
from app.incidents import store as incident_store
from app.safety.guardrail import SafetyGuardrail
from app.websocket.approval import approval_manager

# Module-level instance shared with the Agent pipeline.
_guardrail = SafetyGuardrail()

_RISK_LABELS = {
    RiskLevel.READ: "只读检查",
    RiskLevel.WRITE: "写操作",
    RiskLevel.DESTRUCTIVE: "破坏性操作",
}


class RunbookAgentFallback(Exception):
    """Signal that Runbook execution should hand control back to the Agent."""

    def __init__(self, summary: str):
        super().__init__(summary)
        self.summary = summary


def _technical_call(tool_name: str, tool_args: dict) -> str:
    """Return the raw tool call for audit-style details."""
    return f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)})"


def _format_tool_args(tool_args: dict) -> str:
    """Return compact, user-readable tool arguments for trace cards."""
    if not tool_args:
        return "无参数"
    labels = {
        "service": "服务",
        "path": "路径",
        "filepath": "文件",
        "dirpath": "目录",
        "source": "来源",
        "destination": "目标",
        "port": "端口",
        "protocol": "协议",
        "lines": "行数",
        "limit": "数量上限",
        "min_size": "最小大小",
        "pattern": "匹配条件",
        "content": "内容",
        "append": "追加模式",
    }
    parts = []
    for key, value in tool_args.items():
        label = labels.get(key, key)
        if isinstance(value, str):
            display = value if len(value) <= 120 else value[:117] + "..."
        else:
            display = json.dumps(value, ensure_ascii=False, default=str)
        parts.append(f"{label}={display}")
    return "；".join(parts)


def _display_tool_name(tool_name: str, tool_def=None) -> str:
    """Return the Chinese display name for a tool where possible."""
    tool_def = tool_def or tools_registry.get_tool(tool_name)
    return getattr(tool_def, "display_name", "") or tool_name


def _target_from_args(tool_args: dict) -> str:
    """Pick the most useful target value from common tool argument names."""
    for key in ("service", "path", "filepath", "dirpath", "source", "destination", "port", "username", "name"):
        value = tool_args.get(key)
        if value not in (None, ""):
            return str(value)
    return "当前系统"


def _describe_step(tool_name: str, tool_args: dict, tool_def) -> dict:
    """Build human-facing text for a Runbook step."""
    target = _target_from_args(tool_args)
    risk_label = _RISK_LABELS.get(tool_def.risk_level, str(tool_def.risk_level))
    display_name = _display_tool_name(tool_name, tool_def)

    if tool_name == "get_directory_size":
        action = f"统计目录 {target} 的占用大小"
    elif tool_name == "list_directory":
        action = f"列出目录 {target} 的内容"
    elif tool_name == "read_file":
        action = f"读取文件 {target} 的内容"
    elif tool_name == "find_files":
        action = f"在 {target} 下查找文件或目录"
    elif tool_name == "find_large_files":
        min_size = tool_args.get("min_size") or "指定大小"
        limit = tool_args.get("limit")
        suffix = f"，最多列出 {limit} 个" if limit else ""
        action = f"查找 {target} 下超过 {min_size} 的大文件{suffix}"
    elif tool_name == "get_user_sessions":
        action = "查看当前登录用户，辅助判断是否有文件正在被使用"
    elif tool_name == "get_disk_usage":
        action = f"检查 {target} 的磁盘使用率"
    elif tool_name == "restart_service":
        action = f"重启服务 {target}"
    elif tool_name == "start_service":
        action = f"启动服务 {target}"
    elif tool_name == "stop_service":
        action = f"停止服务 {target}"
    elif tool_name == "get_service_status":
        action = f"检查服务 {target} 的状态"
    elif tool_name == "get_service_logs":
        action = f"查看服务 {target} 的最近日志"
    elif tool_name == "delete_file":
        action = f"删除文件 {target}"
    elif tool_name == "create_directory":
        action = f"创建目录 {target}"
    elif tool_name == "delete_directory":
        action = f"删除目录 {target}"
    elif tool_name == "write_file":
        action = f"写入文件 {target}"
    elif tool_name == "read_config_file":
        action = f"读取配置文件 {target}"
    elif tool_name == "check_config_syntax":
        action = f"检查配置文件 {target} 的语法"
    elif tool_name == "allow_port":
        protocol = tool_args.get("protocol", "tcp")
        action = f"开放防火墙端口 {target}/{protocol}"
    elif tool_name == "block_port":
        protocol = tool_args.get("protocol", "tcp")
        action = f"关闭防火墙端口 {target}/{protocol}"
    else:
        action = f"{display_name}: {tool_def.description}"

    return {
        "action": action,
        "target": target,
        "risk_label": risk_label,
        "display_name": display_name,
        "args_text": _format_tool_args(tool_args),
        "technical": _technical_call(tool_name, tool_args),
    }


def _preview_text(value, max_chars: int = 220) -> str:
    """Compact a result value for trace and final summaries."""
    if value in (None, ""):
        return "无输出"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _shorten(value, max_chars: int = 120) -> str:
    text = _preview_text(value, max_chars=max_chars)
    return text


def _parse_table_first_data_line(data: str) -> str:
    lines = [line for line in str(data or "").splitlines() if line.strip()]
    if len(lines) >= 2:
        return " ".join(lines[1].split())
    if lines:
        return " ".join(lines[0].split())
    return "无输出"


def _summarize_result(tool_name: str, result_repr) -> str:
    """Create a short user-facing summary from a real tool result."""
    if not isinstance(result_repr, dict):
        return _preview_text(result_repr)

    if not result_repr.get("success", True):
        return result_repr.get("error") or "工具返回失败"

    data = result_repr.get("data")
    if tool_name == "system_overview" and isinstance(data, dict):
        parts = []
        if data.get("uptime"):
            parts.append(f"运行时间 {data.get('uptime')}")
        if isinstance(data.get("load_avg"), dict):
            load = data["load_avg"]
            one = load.get("1min") or load.get("1")
            five = load.get("5min") or load.get("5")
            fifteen = load.get("15min") or load.get("15")
            if one or five or fifteen:
                parts.append(f"负载 {one}/{five}/{fifteen}")
        if isinstance(data.get("memory"), dict):
            memory = data["memory"]
            used = memory.get("percent") or memory.get("used_percent") or memory.get("usage")
            if used:
                parts.append(f"内存 {used}")
        if data.get("disk_root"):
            parts.append(f"根分区 {_shorten(data.get('disk_root'), 60)}")
        return "；".join(parts) if parts else "系统概览已获取"
    if tool_name == "health_check" and isinstance(data, dict):
        status = data.get("status") or "unknown"
        issues = data.get("issues") or []
        if issues:
            issue_text = "；".join(
                _shorten(item.get("detail") if isinstance(item, dict) else item, 60)
                for item in issues[:3]
            )
            suffix = "；..." if len(issues) > 3 else ""
            return f"健康状态 {status}，发现 {len(issues)} 个问题：{issue_text}{suffix}"
        return f"健康状态 {status}，未发现明显问题"
    if tool_name in {"list_services", "get_failed_services", "get_listening_ports", "get_connections", "get_journal_logs", "get_recent_errors", "tail_log_file", "search_logs"}:
        if isinstance(data, str):
            lines = [line for line in data.splitlines() if line.strip()]
            if not lines:
                return "未返回记录"
            return f"返回 {len(lines)} 行，摘要: {_shorten(lines[0], 120)}"
        if isinstance(data, dict) and "count" in data:
            return f"返回 {data.get('count')} 条结果"
    if tool_name in {"get_disk_usage", "get_inode_usage"}:
        return _parse_table_first_data_line(str(data or ""))
    if tool_name == "get_service_status":
        text = str(data or "")
        for line in text.splitlines():
            if "Active:" in line or "Loaded:" in line:
                return _shorten(line.strip(), 140)
        return _shorten(text, 140)
    if tool_name in {"read_config_file", "read_file"}:
        if isinstance(data, dict):
            size = data.get("size")
            truncated = "，内容已截断" if data.get("truncated") else ""
            return f"已读取文件，大小 {size if size is not None else '未知'} bytes{truncated}"
        return f"已读取内容，预览: {_shorten(data, 100)}"
    if tool_name == "check_file_info" and isinstance(data, dict):
        stat = data.get("stat") or ""
        first = stat.splitlines()[0] if isinstance(stat, str) and stat else ""
        return f"文件信息已获取{': ' + _shorten(first, 100) if first else ''}"
    if tool_name == "list_directory" and isinstance(data, dict):
        return f"目录包含 {data.get('count', 0)} 个条目" + ("，结果已截断" if data.get("truncated") else "")
    if tool_name == "find_files" and isinstance(data, dict):
        return f"找到 {data.get('count', 0)} 个匹配项" + ("，结果已截断" if data.get("truncated") else "")
    if tool_name == "find_large_files" and isinstance(data, dict):
        count = data.get("count", 0)
        files = data.get("files") or []
        if files:
            return f"找到 {count} 个候选大文件，示例: {_preview_text(files[0], 120)}"
        return "未找到符合条件的大文件"
    if tool_name == "get_directory_size":
        return f"目录占用: {_preview_text(data, 160)}"
    if tool_name == "get_user_sessions":
        return f"登录会话: {_preview_text(data, 160)}"
    if isinstance(data, dict):
        if "count" in data:
            return f"返回 {data.get('count')} 条结果"
        if "valid" in data:
            return "检查通过" if data.get("valid") else f"检查未通过: {_preview_text(data.get('errors'), 160)}"
    return _preview_text(data)


def _extract_service_status(data, service: str = "") -> dict[str, str]:
    """Extract the service state from common service-status result shapes."""
    status = {
        "service": service,
        "active": "",
        "substate": "",
        "loaded": "",
        "unit_file_state": "",
    }
    if isinstance(data, dict):
        status["service"] = str(data.get("service") or data.get("name") or service)
        for key in ("active", "ActiveState", "active_state", "status"):
            value = data.get(key)
            if value not in (None, ""):
                status["active"] = str(value)
                break
        for key in ("substate", "SubState", "sub_state"):
            value = data.get(key)
            if value not in (None, ""):
                status["substate"] = str(value)
                break
        for key in ("loaded", "LoadState", "load_state"):
            value = data.get(key)
            if value not in (None, ""):
                status["loaded"] = str(value)
                break
        for key in ("unit_file_state", "UnitFileState", "enabled"):
            value = data.get(key)
            if value not in (None, ""):
                status["unit_file_state"] = str(value)
                break
        return status

    text = str(data or "")
    loaded_match = re.search(r"Loaded:\s*([A-Za-z-]+)(?:\s+\(([^)]*)\))?", text, re.IGNORECASE)
    active_match = re.search(r"Active:\s*([A-Za-z-]+)(?:\s+\(([^)]+)\))?", text, re.IGNORECASE)
    if loaded_match:
        status["loaded"] = loaded_match.group(1).strip()
        if loaded_match.group(2):
            for part in (item.strip() for item in loaded_match.group(2).split(";")):
                if part in {"enabled", "disabled", "static", "masked", "indirect", "generated"}:
                    status["unit_file_state"] = part
                    break
    if active_match:
        status["active"] = active_match.group(1).strip()
        if active_match.group(2):
            status["substate"] = active_match.group(2).strip()
    return status


def _service_status_label(status: dict[str, str]) -> str:
    active = (status.get("active") or "").lower()
    substate = (status.get("substate") or "").lower()
    if active in {"active", "running"} or substate == "running":
        return "运行中"
    if active in {"inactive", "dead"} or substate == "dead":
        return "未运行"
    if active in {"failed", "error"}:
        return "异常"
    if active in {"activating", "deactivating", "reloading"}:
        return "状态切换中"
    return "未知"


def _service_status_detail(status: dict[str, str]) -> str:
    parts = []
    active = status.get("active") or ""
    substate = status.get("substate") or ""
    loaded = status.get("loaded") or ""
    unit_file_state = status.get("unit_file_state") or ""
    if active:
        detail = active
        if substate:
            detail += f" ({substate})"
        parts.append(f"Active={detail}")
    if loaded:
        parts.append(f"Loaded={loaded}")
    if unit_file_state:
        parts.append(f"开机状态={unit_file_state}")
    return "，".join(parts) if parts else "未解析到 systemd 状态字段"


def _key_findings(executed: list[dict]) -> list[str]:
    findings: list[str] = []
    for item in executed:
        if not item.get("success"):
            continue
        if item.get("tool") == "get_service_status":
            status = item.get("service_status") or {}
            service = status.get("service") or item.get("target") or "服务"
            findings.append(
                f"{service} 当前状态：{_service_status_label(status)}（{_service_status_detail(status)}）"
            )
        elif item.get("tool") == "health_check":
            findings.append(f"系统健康检查：{item.get('summary')}")
        elif item.get("tool") in {"get_disk_usage", "get_inode_usage"}:
            findings.append(f"{item.get('action')}：{item.get('summary')}")
        elif item.get("tool") == "find_large_files":
            findings.append(f"大文件检查：{item.get('summary')}")
    return findings


def _runbook_ledger(runbook_name: str, plan_steps: list[dict], executed: list[dict]) -> dict:
    """Build a compact evidence ledger for LLM-only report polishing."""
    return {
        "runbook": runbook_name,
        "step_count": len(plan_steps),
        "success_count": sum(1 for item in executed if item.get("success")),
        "read_steps": sum(1 for step in plan_steps if step.get("risk_level") == RiskLevel.READ),
        "write_steps": sum(1 for step in plan_steps if step.get("risk_level") == RiskLevel.WRITE),
        "destructive_steps": sum(1 for step in plan_steps if step.get("risk_level") == RiskLevel.DESTRUCTIVE),
        "key_findings": _key_findings(executed),
        "executed_steps": [
            {
                "step": item.get("step"),
                "tool": item.get("tool"),
                "display_name": item.get("display_name"),
                "action": item.get("action"),
                "risk": item.get("risk"),
                "success": bool(item.get("success")),
                "summary": item.get("summary"),
                "target": item.get("target"),
                "service_status": item.get("service_status"),
            }
            for item in executed
        ],
    }


async def _hybrid_final_summary(
    *,
    runbook_name: str,
    plan_steps: list[dict],
    executed: list[dict],
    failed_step: int | None,
    abort_reason: str | None,
    deterministic_summary: str,
) -> str:
    """Ask the LLM to polish the final report without changing execution facts."""
    if not settings.runbook.hybrid_final_summary:
        return deterministic_summary

    ledger = _runbook_ledger(runbook_name, plan_steps, executed)
    failed_text = "无" if failed_step is None else f"步骤 {failed_step}: {abort_reason or '未知'}"
    messages = [
        {
            "role": "system",
            "content": (
                "你是 OpsGuard Runbook 执行报告润色器。只能基于用户提供的 JSON 证据和确定性摘要写最终回复。\n"
                "禁止新增工具、禁止声称执行了 evidence 中没有的操作、禁止编造当前状态。\n"
                "输出中文 Markdown，结构固定为：**结论**、**关键结论**、**执行概览**、**执行明细**、**下一步建议**。\n"
                "对服务状态要一眼可见，例如 nginx 当前状态：运行中/未运行/异常，并保留 Active/Loaded 关键字段。\n"
                "如果只有只读步骤，必须明确本次未修改系统。不要输出 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Runbook 名称：{runbook_name}\n"
                f"失败信息：{failed_text}\n\n"
                f"确定性摘要（可作为兜底格式）：\n{deterministic_summary}\n\n"
                f"唯一可信执行证据 JSON：\n{json.dumps(ledger, ensure_ascii=False, default=str)}"
            ),
        },
    ]

    try:
        response = await call_llm(messages, tools=None)
        polished = (response.get("content") or "").strip()
        validation_error = _validate_hybrid_summary(polished, runbook_name, ledger, deterministic_summary)
        if validation_error:
            logger.warning(f"Runbook hybrid summary rejected: {validation_error}")
            return deterministic_summary
        return polished
    except Exception as e:
        logger.warning(f"Runbook hybrid summary failed; falling back to deterministic summary: {e}")
        return deterministic_summary


def _validate_hybrid_summary(markdown: str, runbook_name: str, ledger: dict, deterministic_summary: str) -> str:
    if not markdown:
        return "empty response"
    if len(markdown) > 5000:
        return "response too long"
    if runbook_name not in markdown:
        return "missing runbook name"
    if "**结论**" not in markdown:
        return "missing conclusion section"
    if "执行概览" not in markdown:
        return "missing execution overview"
    if ledger.get("key_findings") and "关键结论" not in markdown:
        return "missing key findings section"
    forbidden_markers = [
        "我已重启",
        "已重启服务",
        "已启动服务",
        "已停止服务",
        "已删除",
        "已清理",
        "已修改",
    ]
    has_successful_write = any(
        item.get("success") and item.get("risk") in {"写操作", "破坏性操作"}
        for item in ledger.get("executed_steps", [])
    )
    if not has_successful_write and any(marker in markdown for marker in forbidden_markers):
        return "claims write action without successful write evidence"
    for finding in ledger.get("key_findings") or []:
        anchor = str(finding).split("（", 1)[0].strip()
        if anchor and anchor not in markdown and anchor not in deterministic_summary:
            return f"missing key finding anchor: {anchor}"
    return ""


def _runbook_step_success(tool_name: str, result_repr) -> bool:
    """Return whether a tool result should let the Runbook continue normally."""
    if not isinstance(result_repr, dict):
        return True
    if not result_repr.get("success", True):
        return False
    data = result_repr.get("data")
    if isinstance(data, dict) and data.get("valid") is False:
        return False
    return True


def _format_plan(runbook_name: str, plan_steps: list[dict]) -> str:
    """Format a complete Runbook plan for the trace panel."""
    read_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.READ)
    write_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.WRITE)
    destructive_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.DESTRUCTIVE)
    lines = [
        f"准备执行 Runbook「{runbook_name}」",
        f"共 {len(plan_steps)} 步：只读 {read_count}，写操作 {write_count}，破坏性 {destructive_count}",
        "执行计划：",
    ]
    for step in plan_steps:
        lines.extend([
            f"{step['index']}. [{step['risk_label']}] {step['action']}",
            f"   工具：{step['display_name']}（{step['tool_name']}）",
            f"   参数：{step['args_text']}",
        ])
    if write_count or destructive_count:
        lines.append("写操作/破坏性步骤会在执行到该步时再次请求审批。")
    else:
        lines.append("本 Runbook 只包含读取/检查步骤，不会修改系统。")
    return "\n".join(lines)


def _failure_action(step: dict) -> str:
    value = step.get("on_failure", "abort")
    if isinstance(value, dict):
        return str(value.get("action") or "abort")
    return str(value or "abort")


def _branch_steps(runbook: dict, branch_name: str) -> list[dict]:
    """Return named failure-branch steps from Runbook 2.0 metadata."""
    branches = runbook.get("failure_branches") or []
    if not isinstance(branches, list):
        return []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        name = branch.get("name") or branch.get("id") or branch.get("branch")
        if str(name) != str(branch_name):
            continue
        steps = branch.get("steps") or branch.get("runbook_steps") or []
        return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
    return []


def _failure_branch_name(step: dict) -> str:
    value = step.get("on_failure")
    if isinstance(value, dict):
        return str(value.get("branch") or value.get("failure_branch") or "")
    if isinstance(value, str) and value.startswith("branch:"):
        return value.split(":", 1)[1].strip()
    return ""


def _next_step_number(step: dict, *, success: bool, default: int | None) -> int | None:
    key = "on_success" if success else "on_failure"
    value = step.get(key)
    if isinstance(value, dict):
        target = value.get("next_step") or value.get("step") or value.get("target")
        action = value.get("action")
        if action in {"abort", "fallback_agent"}:
            return None
    else:
        target = value
    if target in (None, "", "next"):
        return default
    if target in {"abort", "stop", "fallback_agent"}:
        return None
    try:
        parsed = int(target)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _plan_steps_from_raw_steps(steps: list[dict]) -> list[dict]:
    """Build executable plan metadata from stored Runbook steps."""
    plan_steps: list[dict] = []
    for idx, step in enumerate(steps, start=1):
        tool_name = step.get("tool_name") or ""
        tool_args = step.get("tool_args") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}
        tool_def = tools_registry.get_tool(tool_name)
        if tool_def:
            step_info = _describe_step(tool_name, tool_args, tool_def)
            risk_level = tool_def.risk_level
        else:
            step_info = {
                "action": f"无法识别工具 {tool_name or '(empty)'}",
                "target": "未知",
                "risk_label": "未知工具",
                "display_name": tool_name or "(empty)",
                "args_text": _format_tool_args(tool_args),
                "technical": _technical_call(tool_name, tool_args),
            }
            risk_level = RiskLevel.READ
        step_info.update({
            "index": idx,
            "total": len(steps),
            "tool_name": tool_name,
            "tool_args": tool_args,
            "risk_level": risk_level,
            "tool_def": tool_def,
            "raw_step": step,
        })
        plan_steps.append(step_info)
    return plan_steps


def _append_branch_plan(plan_steps: list[dict], current_index: int, branch_steps: list[dict]) -> list[dict]:
    """Replace remaining plan with a named failure branch."""
    updated = plan_steps[:current_index] + _plan_steps_from_raw_steps(branch_steps)
    for offset, step in enumerate(updated, start=1):
        step["index"] = offset
        step["total"] = len(updated)
    return updated


def _format_step_trace(step_info: dict, result_summary: str | None = None) -> str:
    """Format one Runbook step trace with human text first."""
    lines = [
        f"[步骤 {step_info['index']}/{step_info['total']}] {step_info['action']}",
        f"风险级别: {step_info['risk_label']}",
        f"调用工具: {step_info['display_name']}（{step_info['tool_name']}）",
        f"执行参数: {step_info['args_text']}",
        f"目标对象: {step_info['target']}",
    ]
    if result_summary:
        lines.append(f"结果摘要: {result_summary}")
    return "\n".join(lines)

def _format_final_summary(
    runbook_name: str,
    plan_steps: list[dict],
    executed: list[dict],
    failed_step: int | None,
    abort_reason: str | None,
) -> str:
    """Return the assistant-facing Runbook execution report."""
    success_count = sum(1 for item in executed if item["success"])
    read_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.READ)
    write_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.WRITE)
    destructive_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.DESTRUCTIVE)
    executed_changes = [
        item for item in executed
        if item.get("success") and item.get("risk_level") in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE)
    ]
    if not (write_count or destructive_count):
        changed_text = "本次只执行读取/检查步骤，没有修改系统。"
    elif executed_changes:
        changed_text = f"本次成功执行 {len(executed_changes)} 个写操作/破坏性步骤，均已按步骤审批。"
    else:
        changed_text = "Runbook 计划包含写操作/破坏性步骤，但本次没有成功执行系统变更。"

    if failed_step is None:
        header = f"Runbook「{runbook_name}」执行完成"
        status = f"共 {len(plan_steps)} 步，成功 {success_count} 步。"
    else:
        header = f"Runbook「{runbook_name}」在步骤 {failed_step}/{len(plan_steps)} 中止"
        status = f"已成功执行 {success_count} 步。原因: {abort_reason or '未知'}"

    lines = [
        header,
        "",
        "执行概览：",
        f"- {status}",
        f"- 步骤类型: 只读 {read_count}，写操作 {write_count}，破坏性 {destructive_count}",
        f"- 系统影响: {changed_text}",
        "",
        "执行明细：",
    ]
    for item in executed:
        status_label = "成功" if item["success"] else "失败"
        lines.append(
            f"{item['step']}. [{status_label}] {item['action']}\n"
            f"   工具：{item.get('display_name') or item.get('tool')}\n"
            f"   参数：{item.get('args_text') or '无参数'}\n"
            f"   结果：{_shorten(item['summary'], 120)}"
        )

    not_run = [step for step in plan_steps if step["index"] > len(executed)]
    for step in not_run:
        lines.append(
            f"{step['index']}. [未执行] {step['action']}\n"
            f"   工具：{step['display_name']}\n"
            f"   参数：{step['args_text']}"
        )

    findings = _key_findings(executed)
    if findings:
        insert_at = 7
        lines[insert_at:insert_at] = [
            "",
            "关键结论：",
            *(f"- {finding}" for finding in findings[:5]),
        ]

    if failed_step is None and not (write_count or destructive_count):
        next_step = "下一步建议：如果需要真正清理或修改系统，请基于以上检查结果再发起确认操作。"
        if any(item.get("tool") == "get_service_status" for item in executed):
            next_step = "下一步建议：如果状态异常，再基于上面的服务状态发起修复操作；如果状态正常，无需处理。"
        lines.extend(["", next_step])
    return "\n".join(lines)


async def execute_runbook(
    session_id: str,
    runbook_id: str,
    send_to_client: Callable,
    *,
    approval_timeout: float = 300.0,
    user_message: str = "",
) -> str:
    """Replay a Runbook by id, returning a final summary string.

    The summary is suitable for storing as the assistant's reply in the
    conversation history. All intermediate progress is streamed via
    ``send_to_client`` trace events.
    """
    # === Load the runbook ===
    try:
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            await ensure_runbook_schema(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM runbooks WHERE id = ?",
                (runbook_id,),
            )
            row = await cursor.fetchone()
    except Exception as e:
        logger.error(f"Runbook load failed for {runbook_id}: {e}")
        return f"无法加载 Runbook: {e}"

    if not row:
        return f"Runbook {runbook_id} 不存在"

    runbook = serialize_runbook(row)
    preflight = await preflight_runbook(runbook, user_message)
    if preflight.get("missing_variables"):
        missing = "、".join(str(item) for item in preflight.get("missing_variables") or [])
        return (
            f"Runbook「{runbook.get('name') or runbook_id}」还缺少必要参数，未执行。\n\n"
            f"缺少参数：{missing}\n\n"
            "请补充这些参数后再执行，例如说明服务名、路径、端口或软件包名。"
        )
    if preflight["status"] == "not_applicable":
        reason = preflight.get("summary") or "预检失败"
        try:
            async with aiosqlite.connect(get_knowledge_db_path()) as db:
                await record_runbook_result(
                    db,
                    runbook_id=runbook_id,
                    succeeded=False,
                    failure_reason=reason,
                )
        except Exception as e:
            logger.warning(f"Runbook preflight bookkeeping failed: {e}")
        return (
            f"Runbook「{runbook.get('name') or runbook_id}」当前不适用，未执行。\n\n"
            f"原因：{reason}\n\n建议：改用普通 Agent 调查当前问题。"
        )

    runbook_name = row["name"] or "(unnamed)"
    incident_id = None
    incident_db_path = get_knowledge_db_path()
    try:
        incident_id = await incident_store.create_incident(
            session_id=session_id,
            problem_statement=user_message or f"执行 Runbook「{runbook_name}」",
            source="runbook_executor",
            metadata={"runbook_id": runbook_id},
            db_path=incident_db_path,
        )
    except Exception as e:
        logger.warning(f"Incident creation failed for runbook {runbook_id}: {e}")

    if incident_id:
        original_send_to_client = send_to_client

        async def send_to_client(data: dict):
            try:
                if not (data.get("type") == "trace" and data.get("phase") == "input_received"):
                    await incident_store.record_incident_from_message(
                        incident_id=incident_id,
                        session_id=session_id,
                        message=data,
                        db_path=incident_db_path,
                    )
            except Exception as e:
                logger.warning(f"Incident event recording failed for {incident_id}: {e}")
            await original_send_to_client(data)

    steps = preflight.get("rendered_steps") or runbook.get("steps") or []

    if not steps:
        response = f"Runbook 「{runbook_name}」没有可执行的步骤"
        if incident_id:
            await incident_store.finalize_incident(
                incident_id=incident_id,
                final_summary=response,
                status="failed",
                db_path=incident_db_path,
            )
            response = await incident_store.append_incident_reference(
                response,
                incident_id,
                db_path=incident_db_path,
            )
        return response

    plan_steps = _plan_steps_from_raw_steps(steps)

    # === Announce ===
    await send_to_client(trace_event(
        phase="input_received",
        event_type="start",
        content=user_message or f"执行 Runbook「{runbook_name}」",
        evidence=build_evidence(
            claim=f"用户触发 Runbook「{runbook_name}」执行",
            evidence_type="user input",
            source="Runbook页面" if user_message else "Runbook执行器",
            observed={"runbook_id": runbook_id, "runbook_name": runbook_name, "origin": user_message or "direct"},
            confidence="high",
            execution_state="executed",
        ),
    ))
    await send_to_client(trace_event(
        phase="planning",
        event_type="start",
        content=_format_plan(runbook_name, plan_steps),
        evidence=build_evidence(
            claim=f"Runbook「{runbook_name}」执行计划已生成",
            evidence_type="user input",
            source="Runbook执行器",
            observed=_format_plan(runbook_name, plan_steps),
            confidence="medium",
            execution_state="inferred",
        ),
    ))
    await audit_logger.log(
        session_id, AuditPhase.PLANNING, AuditEventType.START,
        f"Runbook replay started: {runbook_name}",
        {"runbook_id": runbook_id, "step_count": len(steps)},
    )

    executed: list[dict] = []
    failed_step: int | None = None
    abort_reason: str | None = None
    next_step_number: int | None = 1
    step_by_number = {step["index"]: step for step in plan_steps}
    retry_counts: dict[int, int] = {}

    # === Step loop ===
    while next_step_number is not None and next_step_number <= len(plan_steps):
        step_info = step_by_number.get(next_step_number)
        if not step_info:
            break
        idx = step_info["index"]
        tool_name = step_info["tool_name"]
        tool_args = step_info["tool_args"]
        tool_def = step_info["tool_def"]
        original_step = step_info.get("raw_step") or {}
        step_header = f"[步骤 {idx}/{len(steps)}] {step_info['action']}"

        if not tool_def:
            await send_to_client(trace_event(
                phase="tool_call",
                event_type="failure",
                content=f"{step_header}\n结果摘要: 工具不存在，Runbook 中止",
                evidence=build_evidence(
                    claim=f"Runbook 步骤 {idx} 因工具不存在而无法执行",
                    evidence_type="command",
                    source=tool_name or "Runbook",
                    observed=step_info["action"],
                    confidence="high",
                    execution_state="failed",
                    failure_reason="工具未注册",
                    next_check="请先校验或更新 Runbook，再重新执行。",
                ),
            ))
            failed_step = idx
            abort_reason = f"工具不存在: {tool_name}"
            executed.append({
                "step": idx,
                "tool": tool_name,
                "display_name": step_info["display_name"],
                "args_text": step_info["args_text"],
                "action": step_info["action"],
                "risk": step_info["risk_label"],
                "risk_level": step_info["risk_level"],
                "success": False,
                "summary": abort_reason,
            })
            next_step_number = None
            break

        await send_to_client(trace_event(
            phase="tool_call",
            event_type="start",
            content=_format_step_trace(step_info),
            evidence=build_evidence(
                claim=f"准备执行 Runbook 步骤 {idx}",
                evidence_type="user input",
                source=step_info["display_name"],
                observed=(
                    f"工具: {step_info['tool_name']}；参数: {step_info['args_text']}；"
                    f"目标: {step_info['target']}；风险: {step_info['risk_label']}"
                ),
                confidence="medium",
                execution_state="skipped",
            ),
        ))
        await audit_logger.log(
            session_id, AuditPhase.TOOL_CALL, AuditEventType.START,
            f"Runbook step {idx}: {tool_name}", {"args": tool_args},
        )
        before_change_state = None
        backup_record = None
        change_plan = None

        # === Rule-engine command check (re-run because patterns may have evolved) ===
        cmd_check = _guardrail.check_command(json.dumps(tool_args))
        if not cmd_check.is_safe:
            await send_to_client(trace_event(
                phase="tool_call",
                event_type="blocked",
                content=f"{step_header} 被规则引擎拦截: {cmd_check.detail}",
                evidence=build_evidence(
                    claim=f"Runbook 步骤 {idx} 执行前被安全规则拦截",
                    evidence_type="command",
                    source="安全规则",
                    observed=cmd_check.detail,
                    confidence="high",
                    execution_state="skipped",
                    failure_reason=cmd_check.detail,
                ),
            ))
            failed_step = idx
            abort_reason = f"规则引擎拦截: {cmd_check.detail}"
            next_step_number = None
            break

        policy_decision = None
        if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
            policy_decision = evaluate_tool_policy(tool_name, tool_args, tool_def)
            if not policy_decision.allowed:
                policy_text = policy_summary(policy_decision)
                await send_to_client(trace_event(
                    phase="tool_call",
                    event_type="blocked",
                    content=f"{step_header} 被策略引擎阻断: {policy_text}",
                    evidence=build_evidence(
                        claim=f"Runbook 步骤 {idx} 执行前被策略引擎阻断",
                        evidence_type="command",
                        source="execution_policy",
                        observed=policy_decision.to_dict(),
                        confidence="high",
                        execution_state="skipped",
                        failure_reason="; ".join(policy_decision.reasons),
                    ),
                ))
                await audit_logger.log(
                    session_id,
                    AuditPhase.TOOL_CALL,
                    AuditEventType.BLOCKED,
                    f"Runbook policy blocked step {idx}: {tool_name}",
                    {"args": tool_args, "policy": policy_decision.to_dict()},
                )
                failed_step = idx
                abort_reason = f"策略阻断: {policy_text}"
                executed.append({
                    "step": idx,
                    "tool": tool_name,
                    "display_name": step_info["display_name"],
                    "args_text": step_info["args_text"],
                    "action": step_info["action"],
                    "risk": step_info["risk_label"],
                    "risk_level": step_info["risk_level"],
                    "success": False,
                    "summary": abort_reason,
                })
                next_step_number = None
                break

        # === Approval for write/destructive (always, on every replay) ===
        if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
            request_id = f"rb_{runbook_id[:8]}_{idx}_{uuid.uuid4().hex[:6]}"
            loop = asyncio.get_running_loop()
            approval_future: asyncio.Future = loop.create_future()
            supports_rollback, rollback_strategy = effective_rollback_capability(tool_name, tool_args, tool_def)
            preview = build_operation_preview(tool_name, tool_args, tool_def)
            change_plan = build_change_plan(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_def=tool_def,
                preview=preview,
                policy=policy_decision.to_dict() if policy_decision else {},
                runbook={"id": runbook_id, "name": runbook_name, "step_index": idx, "total_steps": len(steps)},
                step_index=idx,
                total_steps=len(steps),
                approval_status="pending",
                approval_request_id=request_id,
            )
            impact_text = (
                f"Runbook「{runbook_name}」步骤 {idx}/{len(steps)}: {step_info['action']}\n"
                f"{policy_summary(policy_decision)}"
            )
            try:
                approval_manager.register_pending(
                    request_id, session_id, tool_name, tool_args,
                    tool_def.risk_level, tool_def.description, approval_future,
                    impact=impact_text,
                    rollback_strategy=rollback_strategy,
                    supports_rollback=supports_rollback,
                    preview_strategy=tool_def.preview_strategy,
                    preview=preview,
                    change_plan=change_plan,
                    policy=policy_decision.to_dict() if policy_decision else {},
                    approval_level=policy_decision.approval_level if policy_decision else "standard",
                    execution_identity=policy_decision.execution_identity if policy_decision else {},
                )
            except TypeError:
                approval_manager.register_pending(
                    request_id, session_id, tool_name, tool_args,
                    tool_def.risk_level, tool_def.description, approval_future,
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
                "change_plan": change_plan,
                "policy": policy_decision.to_dict() if policy_decision else {},
                "approval_level": policy_decision.approval_level if policy_decision else "standard",
                "execution_identity": policy_decision.execution_identity if policy_decision else {},
            })
            await send_to_client(trace_event(
                phase="approval_request",
                event_type="pending",
                content=f"等待用户审批 {step_header}",
                evidence=build_evidence(
                    claim=f"Runbook 步骤 {idx} 需要用户审批后才能执行",
                    evidence_type="user input",
                    source="审批管理器",
                    observed={
                        "target": step_info["target"],
                        "risk": step_info["risk_label"],
                        "preview": preview,
                        "change_plan": change_plan,
                    },
                    confidence="high",
                    execution_state="skipped",
                ),
            ))

            try:
                approved = await asyncio.wait_for(approval_future, timeout=approval_timeout)
            except asyncio.TimeoutError:
                approved = False
            finally:
                approval_manager.remove_pending(request_id)

            if not approved:
                await send_to_client(trace_event(
                    phase="approval_response",
                    event_type="failure",
                    content=f"{step_header} 被拒绝，Runbook 中止",
                    evidence=build_evidence(
                        claim=f"Runbook 步骤 {idx} 因审批未通过而未执行",
                        evidence_type="user input",
                        source="审批管理器",
                        observed="用户拒绝或审批超时",
                        confidence="high",
                        execution_state="skipped",
                        failure_reason="用户未批准该操作",
                    ),
                ))
                failed_step = idx
                abort_reason = "用户拒绝执行"
                executed.append({
                    "step": idx,
                    "tool": tool_name,
                    "display_name": step_info["display_name"],
                    "args_text": step_info["args_text"],
                    "action": step_info["action"],
                    "risk": step_info["risk_label"],
                    "risk_level": step_info["risk_level"],
                    "success": False,
                    "summary": abort_reason,
                })
                next_step_number = None
                break

            await send_to_client(trace_event(
                phase="approval_response",
                event_type="success",
                content=f"{step_header} 已批准",
                evidence=build_evidence(
                    claim=f"Runbook 步骤 {idx} 已通过用户审批",
                    evidence_type="user input",
                    source="审批管理器",
                    observed="已批准",
                    confidence="high",
                    execution_state="executed",
                ),
            ))

            # Rollback point is created only after approval and immediately
            # before execution, matching the normal Agent path.
            try:
                if supports_rollback:
                    backup_record = prepare_rollback_point(
                        tool_name,
                        tool_args,
                        operation_prefix=f"runbook:{runbook_id}:{idx}",
                    )
                    mark_change_plan_rollback(change_plan, backup_record)
                    if backup_record:
                        await send_to_client(trace_event(
                            phase="execution",
                            event_type="start",
                            content=f"{step_header} 已创建回滚点：{rollback_summary(backup_record)}\n{change_plan_summary(change_plan)}",
                            evidence=build_evidence(
                                claim=f"Runbook 步骤 {idx} 执行前已创建回滚点",
                                evidence_type="config",
                                source="rollback_plan.prepare_rollback_point",
                                observed={"rollback": backup_record, "change_plan": change_plan},
                                confidence="high",
                                execution_state="executed",
                            ),
                        ))
            except Exception as e:
                logger.warning(f"Runbook rollback point skipped: {e}")

            try:
                from app.agent.graph import _capture_pre_change_state
                before_change_state = _capture_pre_change_state(tool_name, tool_args, backup_record)
            except Exception as e:
                logger.debug(f"Runbook pre-change snapshot skipped: {e}")

        # === Execute ===
        try:
            result = await execute_tool(tool_name, tool_args, tool_def)
            result_repr = result.__dict__ if hasattr(result, "__dict__") else result
            result_str = json.dumps(result_repr, ensure_ascii=False, default=str)
            success = _runbook_step_success(tool_name, result_repr)
            result_summary = _summarize_result(tool_name, result_repr)
            service_status = (
                _extract_service_status(result_repr.get("data"), str(tool_args.get("service") or step_info["target"]))
                if tool_name == "get_service_status" and isinstance(result_repr, dict)
                else None
            )
            verification_error: str | None = None
            call_id = f"runbook:{runbook_id}:{idx}:{uuid.uuid4().hex[:8]}"
            executed.append({
                "step": idx,
                "tool": tool_name,
                "display_name": step_info["display_name"],
                "args_text": step_info["args_text"],
                "action": step_info["action"],
                "risk": step_info["risk_label"],
                "risk_level": step_info["risk_level"],
                "success": success,
                "summary": result_summary,
                "preview": result_str[:200],
                "target": step_info["target"],
                "service_status": service_status,
            })

            if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                try:
                    from app.agent.graph import _capture_change_diff, _verify_tool_result

                    verification = _verify_tool_result(tool_name, tool_args, result)
                    if verification:
                        await send_to_client(trace_event(
                            phase="verification",
                            event_type=verification["status"],
                            content=f"{step_header}\n验证结果: {verification['message']}",
                            evidence=verification_evidence(
                                claim=f"Runbook 步骤 {idx} 执行后验证",
                                source=step_info["display_name"],
                                observed=verification["message"],
                                success=verification["status"] == "success",
                            ),
                        ))
                        if verification["status"] == "failure":
                            success = False
                            verification_error = verification["message"]
                            executed[-1]["success"] = False
                            executed[-1]["summary"] = verification_error

                    change_diff = _capture_change_diff(tool_name, tool_args, backup_record, before_change_state)
                    if change_diff:
                        await send_to_client(trace_event(
                            phase="verification",
                            event_type="success",
                            content=f"{step_header}\n变更对比:\n{change_diff}",
                            evidence=verification_evidence(
                                claim=f"Runbook 步骤 {idx} 已记录执行前后对比",
                                source=step_info["display_name"],
                                observed=change_diff,
                                success=True,
                            ),
                        ))
                except Exception as e:
                    logger.warning(f"Runbook verification failed for step {idx}: {e}")
                    success = False
                    verification_error = f"验证异常: {e}"
                    executed[-1]["success"] = False
                    executed[-1]["summary"] = verification_error
                    await send_to_client(trace_event(
                        phase="verification",
                        event_type="failure",
                        content=f"{step_header}\n验证异常: {e}",
                        evidence=verification_evidence(
                            claim=f"Runbook 步骤 {idx} 验证过程异常",
                            source=step_info["display_name"],
                            observed=str(e),
                            success=False,
                        ),
                    ))

            await record_tool_execution(
                session_id=session_id,
                incident_id=incident_id or "",
                call_id=call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=tool_def.risk_level.value,
                status="success" if success else "failure",
                result=result,
                error=verification_error,
                execution_state="executed" if success else "failed",
                approval_granted=tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE),
            )

            if success:
                await audit_logger.log(
                    session_id, AuditPhase.EXECUTION, AuditEventType.SUCCESS,
                    f"Runbook step {idx} succeeded: {tool_name}",
                )
                await send_to_client(trace_event(
                    phase="execution",
                    event_type="success",
                    content=_format_step_trace(step_info, result_summary),
                    evidence=build_evidence(
                        claim=f"Runbook 步骤 {idx} 执行成功",
                        evidence_type="command",
                        source=step_info["display_name"],
                        observed=result_summary,
                        confidence="high",
                        execution_state="executed",
                    ),
                ))
            else:
                err_msg = (
                    verification_error or result_repr.get("error", "工具返回 success=False")
                    if isinstance(result_repr, dict) else "工具返回失败"
                )
                executed[-1]["summary"] = err_msg
                await audit_logger.log(
                    session_id, AuditPhase.EXECUTION, AuditEventType.FAILURE,
                    f"Runbook step {idx} returned failure: {err_msg}",
                )
                await send_to_client(trace_event(
                    phase="execution",
                    event_type="failure",
                    content=_format_step_trace(step_info, err_msg),
                    evidence=build_evidence(
                        claim=f"Runbook 步骤 {idx} 执行失败",
                        evidence_type="command",
                        source=step_info["display_name"],
                        observed=err_msg,
                        confidence="high",
                        execution_state="failed",
                        failure_reason=err_msg,
                    ),
                ))
                failed_step = idx
                abort_reason = f"步骤返回失败: {err_msg}"
                action = _failure_action(original_step)
                max_retries = int(original_step.get("max_retries") or 0)
                retry_count = retry_counts.get(idx, 0)
                if retry_count < max_retries:
                    retry_counts[idx] = retry_count + 1
                    failed_step = None
                    abort_reason = None
                    continue
                if action == "continue" or bool(original_step.get("continue_on_failure", False)):
                    failed_step = None
                    abort_reason = None
                    next_step_number = _next_step_number(original_step, success=False, default=idx + 1)
                    continue
                if action == "fallback_agent":
                    next_step_number = None
                    break
                branch_name = _failure_branch_name(original_step)
                branch_steps = _branch_steps(runbook, branch_name) if branch_name else []
                if branch_steps:
                    plan_steps = _append_branch_plan(plan_steps, idx, branch_steps)
                    step_by_number = {step["index"]: step for step in plan_steps}
                    failed_step = None
                    abort_reason = None
                    next_step_number = idx + 1
                    await send_to_client(trace_event(
                        phase="planning",
                        event_type="start",
                        content=f"步骤 {idx} 失败，切换到失败分支「{branch_name}」，追加 {len(branch_steps)} 个步骤。",
                        evidence=build_evidence(
                            claim=f"Runbook 已切换到失败分支 {branch_name}",
                            evidence_type="user input",
                            source="Runbook执行器",
                            observed=f"branch={branch_name}, steps={len(branch_steps)}",
                            confidence="medium",
                            execution_state="inferred",
                        ),
                    ))
                    continue
                alt = _next_step_number(original_step, success=False, default=None)
                if alt is not None:
                    failed_step = None
                    abort_reason = None
                    next_step_number = alt
                    continue
                next_step_number = None
                break

        except Exception as e:
            logger.error(f"Runbook step {idx} raised: {e}")
            await record_tool_execution(
                session_id=session_id,
                incident_id=incident_id or "",
                call_id=f"runbook:{runbook_id}:{idx}:{uuid.uuid4().hex[:8]}",
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=tool_def.risk_level.value if tool_def else "unknown",
                status="failure",
                result={"success": False, "data": "", "error": str(e)},
                error=str(e),
                execution_state="failed",
                approval_granted=bool(tool_def and tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE)),
            )
            await audit_logger.log(
                session_id, AuditPhase.EXECUTION, AuditEventType.FAILURE,
                f"Runbook step {idx} raised: {e}",
            )
            await send_to_client(trace_event(
                phase="execution",
                event_type="failure",
                content=f"{step_header}\n结果摘要: 异常: {e}",
                evidence=build_evidence(
                    claim=f"Runbook 步骤 {idx} 执行异常",
                    evidence_type="command",
                    source=step_info["display_name"],
                    observed=str(e),
                    confidence="high",
                    execution_state="failed",
                    failure_reason=str(e),
                    next_check="请检查 Runbook 步骤参数，并先用只读校验确认后再重试。",
                ),
            ))
            failed_step = idx
            abort_reason = f"步骤异常: {e}"
            executed.append({
                "step": idx,
                "tool": tool_name,
                "display_name": step_info["display_name"],
                "args_text": step_info["args_text"],
                "action": step_info["action"],
                "risk": step_info["risk_label"],
                "risk_level": step_info["risk_level"],
                "success": False,
                "summary": abort_reason,
            })
            action = _failure_action(original_step)
            max_retries = int(original_step.get("max_retries") or 0)
            retry_count = retry_counts.get(idx, 0)
            if retry_count < max_retries:
                retry_counts[idx] = retry_count + 1
                failed_step = None
                abort_reason = None
                continue
            if action == "continue" or bool(original_step.get("continue_on_failure", False)):
                failed_step = None
                abort_reason = None
                next_step_number = _next_step_number(original_step, success=False, default=idx + 1)
                continue
            if action == "fallback_agent":
                next_step_number = None
                break
            branch_name = _failure_branch_name(original_step)
            branch_steps = _branch_steps(runbook, branch_name) if branch_name else []
            if branch_steps:
                plan_steps = _append_branch_plan(plan_steps, idx, branch_steps)
                step_by_number = {step["index"]: step for step in plan_steps}
                failed_step = None
                abort_reason = None
                next_step_number = idx + 1
                await send_to_client(trace_event(
                    phase="planning",
                    event_type="start",
                    content=f"步骤 {idx} 异常，切换到失败分支「{branch_name}」，追加 {len(branch_steps)} 个步骤。",
                    evidence=build_evidence(
                        claim=f"Runbook 已切换到失败分支 {branch_name}",
                        evidence_type="user input",
                        source="Runbook执行器",
                        observed=f"branch={branch_name}, steps={len(branch_steps)}",
                        confidence="medium",
                        execution_state="inferred",
                    ),
                ))
                continue
            alt = _next_step_number(original_step, success=False, default=None)
            if alt is not None:
                failed_step = None
                abort_reason = None
                next_step_number = alt
                continue
            next_step_number = None
            break

        next_step_number = _next_step_number(original_step, success=True, default=idx + 1)

    # === Bookkeeping: update success/failure governance metadata ===
    try:
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            await record_runbook_result(
                db,
                runbook_id=runbook_id,
                succeeded=failed_step is None,
                failure_reason=abort_reason,
            )
    except Exception as e:
        logger.warning(f"Runbook governance bookkeeping failed: {e}")

    # === Final summary message ===
    if failed_step is None:
        deterministic_summary = _format_final_summary(runbook_name, plan_steps, executed, failed_step, abort_reason)
        summary = await _hybrid_final_summary(
            runbook_name=runbook_name,
            plan_steps=plan_steps,
            executed=executed,
            failed_step=failed_step,
            abort_reason=abort_reason,
            deterministic_summary=deterministic_summary,
        )
        if summary != deterministic_summary:
            await send_to_client(trace_event(
                phase="response",
                event_type="success",
                content="Runbook 最终回复已由 LLM 基于真实执行证据润色。",
                evidence=build_evidence(
                    claim=f"Runbook「{runbook_name}」最终回复使用混合模式生成",
                    evidence_type="config",
                    source="runbook_hybrid_summary",
                    observed="LLM 只润色最终报告，执行事实来自 Runbook 工具账本",
                    confidence="high",
                    execution_state="executed",
                ),
            ))
        await send_to_client(trace_event(
            phase="response",
            event_type="success",
            content=f"Runbook 完成: {len(steps)} 步全部成功\n系统影响: {'包含已审批的变更步骤' if any(step['risk_level'] in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE) for step in plan_steps) else '仅检查，未修改系统'}",
            evidence=build_evidence(
                claim=f"Runbook「{runbook_name}」执行完成",
                evidence_type="command",
                source="Runbook执行器",
                observed=f"成功执行 {len(executed)} 步",
                confidence="high",
                execution_state="executed",
            ),
        ))
    else:
        deterministic_summary = _format_final_summary(runbook_name, plan_steps, executed, failed_step, abort_reason)
        summary = await _hybrid_final_summary(
            runbook_name=runbook_name,
            plan_steps=plan_steps,
            executed=executed,
            failed_step=failed_step,
            abort_reason=abort_reason,
            deterministic_summary=deterministic_summary,
        )
        if summary != deterministic_summary:
            await send_to_client(trace_event(
                phase="response",
                event_type="success",
                content="Runbook 中止报告已由 LLM 基于真实执行证据润色。",
                evidence=build_evidence(
                    claim=f"Runbook「{runbook_name}」中止报告使用混合模式生成",
                    evidence_type="config",
                    source="runbook_hybrid_summary",
                    observed="LLM 只润色最终报告，执行事实来自 Runbook 工具账本",
                    confidence="high",
                    execution_state="executed",
                ),
            ))
        await send_to_client(trace_event(
            phase="response",
            event_type="failure",
            content=f"Runbook 中止于步骤 {failed_step}: {abort_reason}",
            evidence=build_evidence(
                claim=f"Runbook「{runbook_name}」未完成全部步骤",
                evidence_type="command",
                source="Runbook执行器",
                observed=f"已执行 {len(executed)} 步，中止步骤 {failed_step}",
                confidence="high",
                execution_state="failed",
                failure_reason=abort_reason or "Runbook did not complete",
                next_check="Review the failing step evidence before replaying or editing the runbook.",
            ),
        ))

    await audit_logger.log(
        session_id, AuditPhase.RESPONSE, AuditEventType.SUCCESS,
        f"Runbook replay finished: {runbook_name}",
        {"executed": len(executed), "failed_at": failed_step, "abort": abort_reason},
    )
    if incident_id:
        await incident_store.finalize_incident(
            incident_id=incident_id,
            final_summary=summary,
            status="resolved" if failed_step is None else "failed",
            db_path=incident_db_path,
        )
        summary = await incident_store.append_incident_reference(
            summary,
            incident_id,
            db_path=incident_db_path,
        )
    if failed_step is not None:
        failed_raw_step = (step_by_number.get(failed_step) or {}).get("raw_step") or {}
        if _failure_action(failed_raw_step) == "fallback_agent":
            raise RunbookAgentFallback(summary + "\n\n已按 Runbook 分支设置转交普通 Agent 继续调查。")
    return summary
