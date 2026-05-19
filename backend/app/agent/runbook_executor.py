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
import uuid
from typing import Callable

import aiosqlite
from loguru import logger

from app.agent.tools_registry import tools_registry, RiskLevel
from app.agent.runbook_governance import ensure_runbook_schema, record_runbook_result
from app.agent.trace_evidence import (
    build_evidence,
    inference_evidence,
    tool_plan_evidence,
    tool_result_evidence,
    trace_event,
    verification_evidence,
)
from app.audit.logger import audit_logger, AuditPhase, AuditEventType
from app.database import get_knowledge_db_path
from app.safety.guardrail import SafetyGuardrail
from app.websocket.approval import approval_manager

# Module-level instance shared with the Agent pipeline.
_guardrail = SafetyGuardrail()

_RISK_LABELS = {
    RiskLevel.READ: "只读检查",
    RiskLevel.WRITE: "写操作",
    RiskLevel.DESTRUCTIVE: "破坏性操作",
}


def _technical_call(tool_name: str, tool_args: dict) -> str:
    """Return the raw tool call for audit-style details."""
    return f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)})"


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
    display_name = tool_def.display_name or tool_name

    if tool_name == "get_directory_size":
        action = f"统计目录 {target} 的占用大小"
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


def _summarize_result(tool_name: str, result_repr) -> str:
    """Create a short user-facing summary from a real tool result."""
    if not isinstance(result_repr, dict):
        return _preview_text(result_repr)

    if not result_repr.get("success", True):
        return result_repr.get("error") or "工具返回失败"

    data = result_repr.get("data")
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


def _format_plan(runbook_name: str, plan_steps: list[dict]) -> str:
    """Format a complete Runbook plan for the trace panel."""
    read_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.READ)
    write_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.WRITE)
    destructive_count = sum(1 for step in plan_steps if step["risk_level"] == RiskLevel.DESTRUCTIVE)
    lines = [
        f"准备执行 Runbook「{runbook_name}」",
        f"共 {len(plan_steps)} 步：只读 {read_count}，写操作 {write_count}，破坏性 {destructive_count}",
        "执行计划:",
    ]
    for step in plan_steps:
        lines.append(
            f"{step['index']}. [{step['risk_label']}] {step['action']}"
        )
    if write_count or destructive_count:
        lines.append("写操作/破坏性步骤会在执行到该步时再次请求审批。")
    else:
        lines.append("本 Runbook 只包含读取/检查步骤，不会修改系统。")
    return "\n".join(lines)


def _format_step_trace(step_info: dict, result_summary: str | None = None) -> str:
    """Format one Runbook step trace with human text first."""
    lines = [
        f"[步骤 {step_info['index']}/{step_info['total']}] {step_info['action']}",
        f"风险级别: {step_info['risk_label']}",
    ]
    if result_summary:
        lines.append(f"结果摘要: {result_summary}")
    lines.append(f"技术细节: {step_info['technical']}")
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
        header = f"✅ Runbook「{runbook_name}」执行完成"
        status = f"共 {len(plan_steps)} 步，成功 {success_count} 步。"
    else:
        header = f"⚠️ Runbook「{runbook_name}」在步骤 {failed_step}/{len(plan_steps)} 中止"
        status = f"已成功执行 {success_count} 步。原因: {abort_reason or '未知'}"

    lines = [
        header,
        "",
        "执行概览:",
        f"- {status}",
        f"- 步骤类型: 只读 {read_count}，写操作 {write_count}，破坏性 {destructive_count}",
        f"- 系统影响: {changed_text}",
        "",
        "步骤结果:",
    ]
    for item in executed:
        mark = "✅" if item["success"] else "❌"
        lines.append(f"{item['step']}. {mark} {item['action']} - {item['summary']}")

    not_run = [step for step in plan_steps if step["index"] > len(executed)]
    for step in not_run:
        lines.append(f"{step['index']}. ⏭️ {step['action']} - 未执行")

    if failed_step is None and not (write_count or destructive_count):
        lines.extend(["", "下一步建议: 如果需要真正清理或修改系统，请基于以上检查结果再发起确认操作。"])
    return "\n".join(lines)


async def execute_runbook(
    session_id: str,
    runbook_id: str,
    send_to_client: Callable,
    *,
    approval_timeout: float = 300.0,
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

    runbook_name = row["name"] or "(unnamed)"
    try:
        steps = json.loads(row["steps"]) if row["steps"] else []
    except Exception as e:
        return f"Runbook 步骤解析失败: {e}"

    if not steps:
        return f"Runbook 「{runbook_name}」没有可执行的步骤"

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
        })
        plan_steps.append(step_info)

    # === Announce ===
    await send_to_client(trace_event(
        phase="planning",
        event_type="start",
        content=_format_plan(runbook_name, plan_steps),
        evidence=inference_evidence(
            f"Runbook {runbook_name} execution plan prepared",
            "runbook_executor",
            {"runbook_id": runbook_id, "step_count": len(plan_steps)},
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

    # === Step loop ===
    for step_info in plan_steps:
        idx = step_info["index"]
        tool_name = step_info["tool_name"]
        tool_args = step_info["tool_args"]
        tool_def = step_info["tool_def"]
        step_header = f"[步骤 {idx}/{len(steps)}] {step_info['action']}"

        if not tool_def:
            await send_to_client(trace_event(
                phase="tool_call",
                event_type="failure",
                content=f"{step_header}\n结果摘要: 工具不存在，Runbook 中止\n技术细节: {step_info['technical']}",
                evidence=build_evidence(
                    claim=f"Runbook step {idx} cannot execute because the tool is missing",
                    evidence_type="command",
                    source=tool_name or "runbook",
                    observed=step_info["technical"],
                    confidence="high",
                    execution_state="failed",
                    failure_reason="Tool is not registered",
                    next_check="Validate or update the runbook before replaying it.",
                ),
            ))
            failed_step = idx
            abort_reason = f"工具不存在: {tool_name}"
            executed.append({
                "step": idx,
                "tool": tool_name,
                "action": step_info["action"],
                "risk": step_info["risk_label"],
                "risk_level": step_info["risk_level"],
                "success": False,
                "summary": abort_reason,
            })
            break

        await send_to_client(trace_event(
            phase="tool_call",
            event_type="start",
            content=_format_step_trace(step_info),
            evidence=tool_plan_evidence(tool_name, tool_args),
        ))
        await audit_logger.log(
            session_id, AuditPhase.TOOL_CALL, AuditEventType.START,
            f"Runbook step {idx}: {tool_name}", {"args": tool_args},
        )
        before_change_state = None
        backup_record = None

        # === Rule-engine command check (re-run because patterns may have evolved) ===
        cmd_check = _guardrail.check_command(json.dumps(tool_args))
        if not cmd_check.is_safe:
            await send_to_client(trace_event(
                phase="tool_call",
                event_type="blocked",
                content=f"{step_header} 被规则引擎拦截: {cmd_check.detail}",
                evidence=build_evidence(
                    claim=f"Runbook step {idx} was blocked before execution",
                    evidence_type="command",
                    source="SafetyGuardrail.check_command",
                    observed=cmd_check.detail,
                    confidence="high",
                    execution_state="skipped",
                    failure_reason=cmd_check.detail,
                ),
            ))
            failed_step = idx
            abort_reason = f"规则引擎拦截: {cmd_check.detail}"
            break

        # === Approval for write/destructive (always, on every replay) ===
        if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
            request_id = f"rb_{runbook_id[:8]}_{idx}_{uuid.uuid4().hex[:6]}"
            loop = asyncio.get_running_loop()
            approval_future: asyncio.Future = loop.create_future()
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
                "impact": f"Runbook「{runbook_name}」步骤 {idx}/{len(steps)}: {step_info['action']}",
            })
            await send_to_client(trace_event(
                phase="approval_request",
                event_type="pending",
                content=f"等待用户审批 {step_header}\n技术细节: {step_info['technical']}",
                evidence=build_evidence(
                    claim=f"Runbook step {idx} requires approval before execution",
                    evidence_type="user input",
                    source="approval_manager",
                    observed=step_info["technical"],
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
                        claim=f"Runbook step {idx} was not executed because approval was rejected",
                        evidence_type="user input",
                        source="approval_manager",
                        observed="rejected_or_timeout",
                        confidence="high",
                        execution_state="skipped",
                        failure_reason="User approval was not granted",
                    ),
                ))
                failed_step = idx
                abort_reason = "用户拒绝执行"
                executed.append({
                    "step": idx,
                    "tool": tool_name,
                    "action": step_info["action"],
                    "risk": step_info["risk_label"],
                    "risk_level": step_info["risk_level"],
                    "success": False,
                    "summary": abort_reason,
                })
                break

            await send_to_client(trace_event(
                phase="approval_response",
                event_type="success",
                content=f"{step_header} 已批准",
                evidence=build_evidence(
                    claim=f"Runbook step {idx} was approved by the user",
                    evidence_type="user input",
                    source="approval_manager",
                    observed="approved",
                    confidence="high",
                    execution_state="executed",
                ),
            ))

            # Backup (best-effort; ignore failures, the file may not be a path)
            try:
                from app.mcp_tools.backup import backup_manager
                target_path = tool_args.get("filepath") or tool_args.get("path") or tool_args.get("service")
                if target_path and isinstance(target_path, str):
                    backup_record = backup_manager.backup_file(target_path, operation=f"runbook:{tool_name}")
                    if backup_record:
                        await send_to_client(trace_event(
                            phase="execution",
                            event_type="start",
                            content=f"{step_header} 已备份 {target_path}",
                            evidence=build_evidence(
                                claim=f"Backup was created before runbook step {idx}",
                                evidence_type="config",
                                source="BackupManager.backup_file",
                                observed=backup_record,
                                confidence="high",
                                execution_state="executed",
                            ),
                        ))
            except Exception as e:
                logger.debug(f"Runbook backup skipped: {e}")

            try:
                from app.agent.graph import _capture_pre_change_state
                before_change_state = _capture_pre_change_state(tool_name, tool_args, backup_record)
            except Exception as e:
                logger.debug(f"Runbook pre-change snapshot skipped: {e}")

        # === Execute ===
        try:
            result = tool_def.function(**tool_args)
            result_repr = result.__dict__ if hasattr(result, "__dict__") else result
            result_str = json.dumps(result_repr, ensure_ascii=False, default=str)
            success = (
                result_repr.get("success", True) if isinstance(result_repr, dict) else True
            )
            result_summary = _summarize_result(tool_name, result_repr)
            verification_error: str | None = None
            executed.append({
                "step": idx,
                "tool": tool_name,
                "action": step_info["action"],
                "risk": step_info["risk_label"],
                "risk_level": step_info["risk_level"],
                "success": success,
                "summary": result_summary,
                "preview": result_str[:200],
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
                                claim=f"Post-action verification for runbook step {idx}",
                                source=tool_name,
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
                                claim=f"Before/after comparison captured for runbook step {idx}",
                                source=tool_name,
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
                            claim=f"Verification raised for runbook step {idx}",
                            source=tool_name,
                            observed=str(e),
                            success=False,
                        ),
                    ))

            if success:
                await audit_logger.log(
                    session_id, AuditPhase.EXECUTION, AuditEventType.SUCCESS,
                    f"Runbook step {idx} succeeded: {tool_name}",
                )
                await send_to_client(trace_event(
                    phase="execution",
                    event_type="success",
                    content=_format_step_trace(step_info, result_summary),
                    evidence=tool_result_evidence(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_def=tool_def,
                        result=result,
                        claim=f"Runbook step {idx} executed successfully",
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
                    evidence=tool_result_evidence(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_def=tool_def,
                        result=result,
                        claim=f"Runbook step {idx} failed",
                    ),
                ))
                failed_step = idx
                abort_reason = f"步骤返回失败: {err_msg}"
                break

        except Exception as e:
            logger.error(f"Runbook step {idx} raised: {e}")
            await audit_logger.log(
                session_id, AuditPhase.EXECUTION, AuditEventType.FAILURE,
                f"Runbook step {idx} raised: {e}",
            )
            await send_to_client(trace_event(
                phase="execution",
                event_type="failure",
                content=f"{step_header}\n结果摘要: 异常: {e}\n技术细节: {step_info['technical']}",
                evidence=build_evidence(
                    claim=f"Runbook step {idx} raised an exception",
                    evidence_type="command",
                    source=tool_name,
                    observed=str(e),
                    confidence="high",
                    execution_state="failed",
                    failure_reason=str(e),
                    next_check="Inspect the runbook step arguments and retry with a read-only validation first.",
                ),
            ))
            failed_step = idx
            abort_reason = f"步骤异常: {e}"
            executed.append({
                "step": idx,
                "tool": tool_name,
                "action": step_info["action"],
                "risk": step_info["risk_label"],
                "risk_level": step_info["risk_level"],
                "success": False,
                "summary": abort_reason,
            })
            break

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
        summary = _format_final_summary(runbook_name, plan_steps, executed, failed_step, abort_reason)
        await send_to_client(trace_event(
            phase="response",
            event_type="success",
            content=f"Runbook 完成: {len(steps)} 步全部成功\n系统影响: {'包含已审批的变更步骤' if any(step['risk_level'] in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE) for step in plan_steps) else '仅检查，未修改系统'}",
            evidence=build_evidence(
                claim=f"Runbook {runbook_name} completed",
                evidence_type="command",
                source="runbook_executor",
                observed={"executed_steps": len(executed), "failed_step": None},
                confidence="high",
                execution_state="executed",
            ),
        ))
    else:
        summary = _format_final_summary(runbook_name, plan_steps, executed, failed_step, abort_reason)
        await send_to_client(trace_event(
            phase="response",
            event_type="failure",
            content=f"Runbook 中止于步骤 {failed_step}: {abort_reason}",
            evidence=build_evidence(
                claim=f"Runbook {runbook_name} stopped before completing all steps",
                evidence_type="command",
                source="runbook_executor",
                observed={"executed_steps": len(executed), "failed_step": failed_step, "abort_reason": abort_reason},
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
    return summary
