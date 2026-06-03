"""Runbook 2.0 variable extraction and applicability preflight."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.agent.tool_executor import execute_tool
from app.agent.tools_registry import RiskLevel, tools_registry
from app.mcp_tools.service_tools import get_service_status

VARIABLE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


def discover_variables(runbook: dict) -> list[dict]:
    """Return variables declared by schema plus placeholders found in steps."""
    declared = runbook.get("variables") if isinstance(runbook.get("variables"), list) else []
    variables: dict[str, dict] = {}
    for item in declared:
        if isinstance(item, dict) and item.get("name"):
            variables[str(item["name"])] = dict(item)
        elif isinstance(item, str):
            variables[item] = {"name": item, "required": True}

    def collect(value: Any) -> None:
        if isinstance(value, str):
            for name in VARIABLE_PATTERN.findall(value):
                variables.setdefault(name, {"name": name, "required": True})
        elif isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    collect(runbook.get("steps") or [])
    collect(runbook.get("preconditions") or [])
    collect(runbook.get("postconditions") or [])
    return list(variables.values())


def extract_variables(user_message: str, variables: list[dict], runbook: dict | None = None) -> dict[str, str]:
    """Best-effort deterministic extraction for common SRE variables."""
    text = user_message or ""
    extracted: dict[str, str] = {}

    for variable in variables:
        name = str(variable.get("name") or "")
        if not name:
            continue
        value = _extract_named_value(text, name)
        if not value:
            value = _extract_by_type(text, str(variable.get("type") or name))
        if value:
            extracted[name] = value

    # Opportunistic extraction for older Runbooks without declared variables.
    if runbook:
        wanted = {str(item.get("name")) for item in variables if item.get("name")}
        if "service" in wanted and "service" not in extracted:
            service = _extract_service(text)
            if service:
                extracted["service"] = service
        if "path" in wanted and "path" not in extracted:
            path = _extract_path(text)
            if path:
                extracted["path"] = path

    return extracted


def render_template(value: Any, variables: dict[str, str]) -> Any:
    """Replace {{var}} placeholders recursively."""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            return str(variables.get(match.group(1), match.group(0)))
        return VARIABLE_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {key: render_template(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, variables) for item in value]
    return value


def apply_variables_to_steps(steps: list[dict], variables: dict[str, str]) -> list[dict]:
    rendered = []
    for step in steps:
        next_step = deepcopy(step)
        next_step["tool_args"] = render_template(next_step.get("tool_args") or {}, variables)
        rendered.append(next_step)
    return rendered


async def preflight_runbook(runbook: dict, user_message: str = "") -> dict:
    """Assess whether a Runbook is applicable before replay."""
    variables = discover_variables(runbook)
    extracted = extract_variables(user_message, variables, runbook)
    missing = [
        str(item.get("name"))
        for item in variables
        if item.get("required", True) is not False and str(item.get("name")) not in extracted
    ]

    steps = apply_variables_to_steps(runbook.get("steps") or [], extracted)
    checks: list[dict] = []

    for name in missing:
        checks.append({
            "status": "missing",
            "message": f"缺少变量 {name}",
            "variable": name,
        })

    for idx, step in enumerate(steps, start=1):
        tool_name = step.get("tool_name") or ""
        tool_args = step.get("tool_args") if isinstance(step.get("tool_args"), dict) else {}
        tool_def = tools_registry.get_tool(tool_name)
        if not tool_def:
            checks.append({"status": "failed", "step": idx, "message": f"工具不存在: {tool_name or '(empty)'}"})
            continue

        checks.extend(_target_checks(idx, tool_name, tool_args, tool_def.risk_level))

    preconditions = render_template(runbook.get("preconditions") or [], extracted)
    applicability_conditions = render_template(runbook.get("applicability_conditions") or [], extracted)
    non_applicability_conditions = render_template(runbook.get("non_applicability_conditions") or [], extracted)
    checks.extend(await _condition_checks(preconditions, "precondition"))
    checks.extend(await _condition_checks(applicability_conditions, "applicability"))
    checks.extend(await _condition_checks(non_applicability_conditions, "non_applicability"))
    checks.extend(await _recent_change_checks(steps))

    staleness = runbook.get("staleness_status") or "fresh"
    if staleness == "stale":
        checks.append({"status": "warning", "message": "Runbook 已过期，需要谨慎确认"})
    elif staleness == "warning":
        checks.append({"status": "warning", "message": "Runbook 最近有失败或较久未验证"})

    status = "applicable"
    if any(item["status"] == "failed" for item in checks):
        status = "not_applicable"
    elif missing or any(item["status"] in {"missing", "warning", "unknown"} for item in checks):
        status = "uncertain"

    return {
        "status": status,
        "variables": variables,
        "extracted_variables": extracted,
        "missing_variables": missing,
        "checks": checks,
        "rendered_steps": steps,
        "preconditions_summary": _checks_summary(checks, {"precondition", "applicability", "non_applicability"}),
        "rollback_coverage": _rollback_coverage(runbook, steps),
        "summary": _summary(status, checks, extracted, missing),
    }


def _target_checks(index: int, tool_name: str, tool_args: dict, risk_level: RiskLevel) -> list[dict]:
    checks: list[dict] = []
    path_key = next((key for key in ("filepath", "dirpath", "path", "source") if tool_args.get(key)), None)
    if path_key:
        target = Path(str(tool_args[path_key]))
        if tool_name == "write_file" and not target.exists():
            parent = target.parent
            if not parent.exists():
                checks.append({"status": "failed", "step": index, "message": f"父目录不存在: {parent}"})
            else:
                checks.append({"status": "passed", "step": index, "message": f"父目录存在: {parent}"})
        elif not target.exists():
            status = "failed" if risk_level in (RiskLevel.READ, RiskLevel.DESTRUCTIVE) else "warning"
            checks.append({"status": status, "step": index, "message": f"目标路径不存在: {target}"})
        else:
            checks.append({"status": "passed", "step": index, "message": f"目标存在: {target}"})

    service = tool_args.get("service")
    if service:
        result = get_service_status(str(service))
        if getattr(result, "success", False):
            checks.append({"status": "passed", "step": index, "message": f"服务存在: {service}"})
        else:
            checks.append({"status": "failed", "step": index, "message": f"服务不存在或无法读取状态: {service}"})
    return checks


async def _condition_checks(conditions: Any, kind: str) -> list[dict]:
    if not isinstance(conditions, list):
        return []
    checks = []
    for index, condition in enumerate(conditions, start=1):
        if not isinstance(condition, dict):
            continue
        description = condition.get("description") or f"{kind} 条件 {index}"
        tool_name = condition.get("tool_name")
        if not tool_name:
            checks.append({"status": "unknown", "kind": kind, "message": f"{description}: 需要人工确认"})
            continue

        tool_name = str(tool_name)
        tool_def = tools_registry.get_tool(tool_name)
        if not tool_def:
            checks.append({"status": "failed", "kind": kind, "message": f"{description}: 条件工具不存在: {tool_name}"})
            continue
        if tool_def.risk_level != RiskLevel.READ:
            checks.append({
                "status": "failed",
                "kind": kind,
                "tool_name": tool_name,
                "message": f"{description}: 预检只允许只读工具，{tool_name} 是 {tool_def.risk_level}",
            })
            continue

        tool_args = condition.get("tool_args")
        if tool_args is None:
            tool_args = condition.get("args") or {}
        if not isinstance(tool_args, dict):
            checks.append({"status": "failed", "kind": kind, "tool_name": tool_name, "message": f"{description}: 条件参数必须是对象"})
            continue
        if _has_unresolved_template(tool_args):
            checks.append({
                "status": "missing",
                "kind": kind,
                "tool_name": tool_name,
                "message": f"{description}: 条件参数仍包含未填变量",
            })
            continue

        try:
            result = await execute_tool(tool_name, tool_args, tool_def)
        except Exception as exc:
            level = "warning" if condition.get("required") is False else "failed"
            checks.append({
                "status": level,
                "kind": kind,
                "tool_name": tool_name,
                "message": f"{description}: 条件执行异常: {exc}",
            })
            continue

        matched, detail = _condition_matches(condition, result)
        if kind == "non_applicability":
            status = "failed" if matched else "passed"
            message = f"{description}: {'阻断条件命中' if matched else '阻断条件未命中'}"
        else:
            status = "passed" if matched else ("warning" if condition.get("required") is False else "failed")
            message = f"{description}: {'通过' if matched else '未通过'}"
        checks.append({
            "status": status,
            "kind": kind,
            "tool_name": tool_name,
            "message": f"{message} ({detail})" if detail else message,
        })
    return checks


async def _recent_change_checks(steps: list[dict]) -> list[dict]:
    """Warn when an automation runbook touches mutable targets after recent changes."""
    if not _needs_recent_change_check(steps):
        return []
    tool_def = tools_registry.get_tool("get_recent_changes")
    if not tool_def:
        return [{
            "status": "unknown",
            "kind": "recent_changes",
            "message": "最近变更检查不可用: get_recent_changes 工具未注册",
        }]
    try:
        result = await execute_tool("get_recent_changes", {"window_hours": 24, "limit": 10}, tool_def)
    except Exception as exc:
        return [{
            "status": "warning",
            "kind": "recent_changes",
            "message": f"最近变更检查异常: {exc}",
        }]

    result_dict = _result_to_dict(result)
    if not result_dict.get("success", True):
        return [{
            "status": "warning",
            "kind": "recent_changes",
            "message": f"最近变更检查失败: {result_dict.get('error') or 'unknown'}",
        }]
    data = result_dict.get("data")
    changes = data.get("changes") if isinstance(data, dict) else []
    if changes:
        return [{
            "status": "warning",
            "kind": "recent_changes",
            "message": f"过去 24 小时发现 {len(changes)} 条系统变更，执行前需要确认当前证据仍匹配",
        }]
    return [{
        "status": "passed",
        "kind": "recent_changes",
        "message": "过去 24 小时未发现明显相关系统变更",
    }]


def _summary(status: str, checks: list[dict], variables: dict[str, str], missing: list[str]) -> str:
    parts = []
    if variables:
        parts.append("变量: " + ", ".join(f"{k}={v}" for k, v in variables.items()))
    if missing:
        parts.append("缺少变量: " + ", ".join(missing))
    failed = [item for item in checks if item["status"] == "failed"]
    warnings = [item for item in checks if item["status"] in {"warning", "unknown"}]
    if failed:
        parts.append(f"{len(failed)} 项预检失败")
    if warnings:
        parts.append(f"{len(warnings)} 项需要确认")
    if not parts:
        parts.append("预检通过")
    return f"{status}: " + "；".join(parts)


def _checks_summary(checks: list[dict], kinds: set[str]) -> dict:
    scoped = [item for item in checks if item.get("kind") in kinds]
    counts = {
        "passed": sum(1 for item in scoped if item.get("status") == "passed"),
        "failed": sum(1 for item in scoped if item.get("status") == "failed"),
        "warning": sum(1 for item in scoped if item.get("status") == "warning"),
        "missing": sum(1 for item in scoped if item.get("status") == "missing"),
        "unknown": sum(1 for item in scoped if item.get("status") == "unknown"),
    }
    if not scoped:
        label = "未定义自定义预检条件"
    elif counts["failed"]:
        label = f"{counts['failed']} 项未通过"
    elif counts["missing"]:
        label = f"{counts['missing']} 项缺少变量"
    elif counts["warning"] or counts["unknown"]:
        label = f"{counts['warning'] + counts['unknown']} 项需要确认"
    else:
        label = f"{counts['passed']} 项通过"
    return {"total": len(scoped), "counts": counts, "label": label}


def _rollback_coverage(runbook: dict, steps: list[dict]) -> dict:
    rollback_steps = runbook.get("rollback_steps") if isinstance(runbook.get("rollback_steps"), list) else []
    mutable_steps = []
    covered = 0
    has_explicit = bool(rollback_steps)
    for step in steps:
        tool_def = tools_registry.get_tool(str(step.get("tool_name") or ""))
        if not tool_def or tool_def.risk_level not in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
            continue
        mutable_steps.append(step)
        if has_explicit or getattr(tool_def, "supports_rollback", False):
            covered += 1
    total = len(mutable_steps)
    if total == 0:
        label = "无需回滚"
    elif covered >= total:
        label = f"{covered}/{total} 已覆盖"
    elif covered:
        label = f"{covered}/{total} 部分覆盖"
    else:
        label = f"0/{total} 未覆盖"
    return {
        "covered_steps": covered,
        "total_mutating_steps": total,
        "has_explicit_rollback": has_explicit,
        "label": label,
    }


def _needs_recent_change_check(steps: list[dict]) -> bool:
    watched_categories = {"service", "config", "file", "package", "firewall", "user", "cron"}
    for step in steps:
        tool_def = tools_registry.get_tool(str(step.get("tool_name") or ""))
        if not tool_def:
            continue
        if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
            return True
        if tool_def.category in watched_categories and tool_def.risk_level != RiskLevel.READ:
            return True
    return False


def _has_unresolved_template(value: Any) -> bool:
    if isinstance(value, str):
        return bool(VARIABLE_PATTERN.search(value))
    if isinstance(value, dict):
        return any(_has_unresolved_template(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_unresolved_template(item) for item in value)
    return False


def _condition_matches(condition: dict, result: Any) -> tuple[bool, str]:
    result_dict = _result_to_dict(result)
    success = bool(result_dict.get("success", True))
    expected = condition.get("expect")
    if expected is None:
        expected = condition.get("expected")
    if expected is None:
        return success, result_dict.get("error") or ("工具成功" if success else "工具失败")
    if isinstance(expected, bool):
        return success is expected, f"success={success}"
    if isinstance(expected, str):
        text = _result_text(result_dict)
        return expected in text, f"包含 {expected!r}: {expected in text}"
    if not isinstance(expected, dict):
        return success, f"success={success}"

    checks: list[tuple[bool, str]] = []
    if "success" in expected:
        wanted = bool(expected["success"])
        checks.append((success is wanted, f"success={success}"))
    text = _result_text(result_dict)
    if expected.get("contains") is not None:
        needle = str(expected["contains"])
        checks.append((needle in text, f"contains={needle!r}"))
    if expected.get("not_contains") is not None:
        needle = str(expected["not_contains"])
        checks.append((needle not in text, f"not_contains={needle!r}"))
    if expected.get("regex") is not None:
        pattern = str(expected["regex"])
        checks.append((bool(re.search(pattern, text, re.IGNORECASE)), f"regex={pattern!r}"))
    if expected.get("status") is not None:
        actual = _extract_data_field(result_dict, "status")
        checks.append((str(actual) == str(expected["status"]), f"status={actual!r}"))
    if expected.get("equals") is not None:
        checks.append((result_dict.get("data") == expected["equals"], "data equals expected"))
    count = _extract_count(result_dict.get("data"))
    if expected.get("min_count") is not None:
        checks.append((count is not None and count >= int(expected["min_count"]), f"count={count}"))
    if expected.get("max_count") is not None:
        checks.append((count is not None and count <= int(expected["max_count"]), f"count={count}"))
    if not checks:
        return success, f"success={success}"
    return all(item[0] for item in checks), "; ".join(item[1] for item in checks)


def _result_to_dict(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    return {"success": True, "data": result}


def _result_text(result_dict: dict) -> str:
    return json.dumps(result_dict.get("data"), ensure_ascii=False, default=str)


def _extract_data_field(result_dict: dict, field: str) -> Any:
    data = result_dict.get("data")
    if isinstance(data, dict):
        return data.get(field)
    return None


def _extract_count(data: Any) -> int | None:
    if isinstance(data, dict):
        if isinstance(data.get("count"), int):
            return data["count"]
        for key in ("items", "files", "changes", "results", "processes"):
            if isinstance(data.get(key), list):
                return len(data[key])
    if isinstance(data, list):
        return len(data)
    if isinstance(data, str):
        return len([line for line in data.splitlines() if line.strip()])
    return None


def _extract_named_value(text: str, name: str) -> str:
    match = re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*[=:：]\s*([^\s，。；;]+)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _extract_by_type(text: str, kind: str) -> str:
    kind = kind.lower()
    if kind in {"service", "service_name"}:
        return _extract_service(text)
    if kind in {"path", "filepath", "dirpath"}:
        return _extract_path(text)
    if kind == "port":
        match = re.search(r"\b([1-9][0-9]{0,4})\b", text)
        if match and 1 <= int(match.group(1)) <= 65535:
            return match.group(1)
    if kind in {"package", "package_name"}:
        match = re.search(r"(?:安装|卸载|升级|package|包)\s*([A-Za-z0-9_.@+-]+)", text, re.IGNORECASE)
        return match.group(1) if match else ""
    return ""


def _extract_service(text: str) -> str:
    explicit = re.search(r"\b([A-Za-z0-9_.@-]+)\.service\b", text, re.IGNORECASE)
    if explicit:
        return explicit.group(1)
    common = re.search(r"\b(nginx|mysql|mysqld|redis|redis-server|sshd?|apache2?|httpd|docker|containerd)\b", text, re.IGNORECASE)
    if common:
        return common.group(1)
    match = re.search(r"(?:服务|service)\s*([A-Za-z0-9_.@-]+)", text, re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_path(text: str) -> str:
    match = re.search(r"(/[^\s`'\"，。；;]+)", text)
    return match.group(1).rstrip("。；;,，") if match else ""
