"""Execution policy checks for write/destructive tool calls.

The policy engine is intentionally deterministic. It runs before approval so a
blocked action never asks the user to approve something the system should not
allow in the first place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from fnmatch import fnmatch
from pathlib import Path
import socket
from typing import Any

from app.config import settings


@dataclass
class PolicyDecision:
    allowed: bool
    action: str
    approval_level: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)
    subject: dict[str, Any] = field(default_factory=dict)
    max_blast_radius: int = 1
    maintenance_window: str = "not_configured"
    execution_identity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "approval_level": self.approval_level,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "matched_rules": self.matched_rules,
            "subject": self.subject,
            "max_blast_radius": self.max_blast_radius,
            "maintenance_window": self.maintenance_window,
            "execution_identity": self.execution_identity,
        }


def evaluate_tool_policy(tool_name: str, tool_args: dict[str, Any], tool_def: Any) -> PolicyDecision:
    """Evaluate configured execution policy for one tool call."""
    policy = settings.policy
    risk_level = _risk_value(getattr(tool_def, "risk_level", "unknown"))
    category = str(getattr(tool_def, "category", "") or "")
    subject = _subject(tool_name, tool_args, category, risk_level)
    identity = _execution_identity(tool_name)
    approval_level = _approval_level(risk_level)
    decision = PolicyDecision(
        allowed=True,
        action="allow",
        approval_level=approval_level,
        subject=subject,
        max_blast_radius=max(1, int(policy.max_blast_radius or 1)),
        maintenance_window="not_configured",
        execution_identity=identity,
    )

    if not policy.enabled:
        decision.warnings.append("Policy engine disabled by configuration")
        return decision

    denied_paths = list(settings.execution.protected_paths or []) + list(policy.denied_paths or [])
    for path_value in subject.get("paths", []):
        if _matches_path(path_value, denied_paths):
            _block(decision, f"Path is denied or protected by policy: {path_value}")

    if policy.enforce_write_path_allowlist and risk_level in {"write", "destructive"}:
        if not policy.allowed_write_paths and subject.get("paths"):
            _block(decision, "Write path allowlist is enforced but no approved paths are configured")
        for path_value in subject.get("paths", []):
            if not _matches_path(path_value, policy.allowed_write_paths) and not _is_allowed_tmp_file_cleanup(tool_name, path_value):
                _block(decision, f"Write path is outside approved paths: {path_value}")

    for service in subject.get("services", []):
        if _matches_any(service, policy.protected_services):
            _block(decision, f"Service is protected by policy: {service}")

    if risk_level in {"write", "destructive"}:
        window_status = _maintenance_window_status(policy.maintenance_windows or [])
        decision.maintenance_window = window_status
        if window_status == "outside":
            _block(decision, "Current time is outside configured maintenance windows")

    if risk_level in {"write", "destructive"} and identity["uses_sudo"]:
        if identity["sudo_allowed"]:
            decision.warnings.append("Sudo command matches configured allowlist")
        elif settings.policy.enforce_sudo_allowlist:
            _block(decision, f"Sudo command is not in allowlist: {identity['sudo_command']}")
        else:
            decision.warnings.append(f"Sudo allowlist not enforced for: {identity['sudo_command']}")

    for rule in policy.rules or []:
        if not _rule_matches(rule, subject):
            continue
        name = rule.name or rule.description or "unnamed_policy_rule"
        decision.matched_rules.append(name)
        action = (rule.action or "allow").lower()
        if action == "deny":
            _block(decision, f"Matched deny policy rule: {name}")
        elif action == "approval":
            decision.approval_level = _stronger_approval(decision.approval_level, "explicit")
            decision.warnings.append(f"Matched explicit approval policy rule: {name}")
        else:
            decision.warnings.append(f"Matched allow policy rule: {name}")

    if risk_level == "destructive":
        decision.approval_level = _stronger_approval(decision.approval_level, "destructive")
    if decision.reasons:
        decision.allowed = False
        decision.action = "deny"
    return decision


def policy_summary(decision: PolicyDecision | dict[str, Any] | None) -> str:
    """Return compact human-readable policy details for approval surfaces."""
    if not decision:
        return "Policy: not evaluated"
    data = decision.to_dict() if isinstance(decision, PolicyDecision) else decision
    status = "allow" if data.get("allowed") else "deny"
    lines = [
        f"策略：{_policy_action_label(status)}",
        f"审批级别：{_approval_level_label(str(data.get('approval_level') or 'standard'))}",
        f"影响上限：{data.get('max_blast_radius') or 1} 个目标",
    ]
    if data.get("maintenance_window") and data.get("maintenance_window") != "not_configured":
        lines.append(f"维护窗口：{data.get('maintenance_window')}")
    if data.get("matched_rules"):
        lines.append("命中规则：" + "、".join(str(item) for item in data["matched_rules"]))
    if data.get("reasons"):
        lines.append("阻断原因：" + "；".join(str(item) for item in data["reasons"]))
    elif data.get("warnings"):
        lines.append("策略提示：" + "；".join(str(item) for item in data["warnings"][:3]))
    identity = data.get("execution_identity") or {}
    if identity:
        lines.append(f"执行身份：{identity.get('run_as_user') or 'current process'}")
    return "\n".join(lines)


def _policy_action_label(action: str) -> str:
    return {
        "allow": "允许",
        "deny": "拒绝",
    }.get(action, action)


def _approval_level_label(level: str) -> str:
    return {
        "none": "无需审批",
        "standard": "标准审批",
        "explicit": "显式审批",
        "destructive": "高危审批",
    }.get(level, level)


def _subject(tool_name: str, tool_args: dict[str, Any], category: str, risk_level: str) -> dict[str, Any]:
    paths = [
        str(tool_args[key])
        for key in ("filepath", "dirpath", "path", "source", "destination")
        if tool_args.get(key) not in (None, "")
    ]
    service = tool_args.get("service")
    user = tool_args.get("username") or tool_args.get("user")
    return {
        "tool": tool_name,
        "category": category,
        "risk_level": risk_level,
        "paths": paths,
        "services": [str(service)] if service else [],
        "users": [str(user)] if user else [],
        "host": settings.policy.host or socket.gethostname(),
        "environment": settings.policy.environment,
    }


def _execution_identity(tool_name: str) -> dict[str, Any]:
    sudo_command = _sudo_command_pattern(tool_name)
    uses_sudo = bool(sudo_command)
    return {
        "run_as_user": settings.execution.run_as_user,
        "uses_sudo": uses_sudo,
        "sudo_command": sudo_command,
        "sudo_allowed": _sudo_allowed(sudo_command) if sudo_command else True,
    }


def _sudo_command_pattern(tool_name: str) -> str:
    return {
        "restart_service": "systemctl restart *",
        "start_service": "systemctl start *",
        "stop_service": "systemctl stop *",
        "kill_process": "kill *",
        "allow_port": "firewall *",
        "block_port": "firewall *",
        "install_package": "package install *",
        "remove_package": "package remove *",
        "change_owner": "chown *",
    }.get(tool_name, "")


def _sudo_allowed(command: str) -> bool:
    if not command:
        return True
    allowlist = settings.execution.sudo_whitelist or []
    return any(fnmatch(command, pattern) or fnmatch(pattern, command) for pattern in allowlist)


def _block(decision: PolicyDecision, reason: str) -> None:
    if reason not in decision.reasons:
        decision.reasons.append(reason)


def _risk_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "unknown")


def _approval_level(risk_level: str) -> str:
    if risk_level == "destructive":
        return "destructive"
    if risk_level == "write":
        return "standard"
    return "none"


def _stronger_approval(current: str, new: str) -> str:
    order = {"none": 0, "standard": 1, "explicit": 2, "destructive": 3}
    return new if order.get(new, 0) > order.get(current, 0) else current


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch(value, pattern) or value == pattern for pattern in patterns or [])


def _matches_path(path_value: str, patterns: list[str]) -> bool:
    if not path_value:
        return False
    try:
        candidate = Path(path_value).expanduser()
        candidate_text = str(candidate.resolve(strict=False))
    except Exception:
        candidate_text = str(path_value)
    for pattern in patterns or []:
        pattern_text = str(pattern)
        if fnmatch(candidate_text, pattern_text):
            return True
        if pattern_text.endswith("/*") and candidate_text.startswith(pattern_text[:-1]):
            return True
        try:
            protected = str(Path(pattern_text).expanduser().resolve(strict=False))
            if candidate_text == protected or candidate_text.startswith(protected.rstrip("/") + "/"):
                return True
        except Exception:
            continue
    return False


def _is_allowed_tmp_file_cleanup(tool_name: str, path_value: str) -> bool:
    """Allow tightly bounded temporary-file cleanup outside the write allowlist."""
    if tool_name != "delete_file" or not settings.policy.allow_tmp_file_cleanup:
        return False
    if not path_value or any(token in path_value for token in ("*", "?", "[", "]", "{", "}", "\x00")):
        return False
    try:
        candidate = Path(path_value).expanduser()
        if candidate.is_symlink() or not candidate.exists() or not candidate.is_file():
            return False
        resolved = candidate.resolve(strict=True)
        tmp_root = Path("/tmp").resolve(strict=True)
    except Exception:
        return False
    if resolved == tmp_root or tmp_root not in resolved.parents:
        return False
    return True


def _maintenance_window_status(windows: list[str]) -> str:
    if not windows:
        return "not_configured"
    now = datetime.now().time()
    for window in windows:
        try:
            start_text, end_text = window.split("-", 1)
            start = time.fromisoformat(start_text.strip())
            end = time.fromisoformat(end_text.strip())
        except Exception:
            continue
        if start <= end and start <= now <= end:
            return "inside"
        if start > end and (now >= start or now <= end):
            return "inside"
    return "outside"


def _rule_matches(rule: Any, subject: dict[str, Any]) -> bool:
    checks = [
        (getattr(rule, "tools", None), [subject["tool"]]),
        (getattr(rule, "categories", None), [subject["category"]]),
        (getattr(rule, "risk_levels", None), [subject["risk_level"]]),
        (getattr(rule, "services", None), subject["services"]),
        (getattr(rule, "users", None), subject["users"]),
        (getattr(rule, "hosts", None), [subject["host"]]),
        (getattr(rule, "environments", None), [subject["environment"]]),
    ]
    for patterns, values in checks:
        if patterns and not any(_matches_any(str(value), list(patterns)) for value in values):
            return False
    paths = getattr(rule, "paths", None)
    if paths and not any(_matches_path(path, list(paths)) for path in subject["paths"]):
        return False
    return True
