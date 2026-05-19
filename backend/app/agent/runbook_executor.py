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
from datetime import datetime
from typing import Callable

import aiosqlite
from loguru import logger

from app.agent.tools_registry import tools_registry, RiskLevel
from app.audit.logger import audit_logger, AuditPhase, AuditEventType
from app.database import get_knowledge_db_path
from app.safety.guardrail import SafetyGuardrail
from app.websocket.approval import approval_manager

# Module-level instance shared with the Agent pipeline.
_guardrail = SafetyGuardrail()


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
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, name, description, steps FROM runbooks WHERE id = ?",
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

    # === Announce ===
    await send_to_client({
        "type": "trace",
        "phase": "planning",
        "event_type": "start",
        "content": f"开始执行 Runbook「{runbook_name}」({len(steps)} 步)",
    })
    await audit_logger.log(
        session_id, AuditPhase.PLANNING, AuditEventType.START,
        f"Runbook replay started: {runbook_name}",
        {"runbook_id": runbook_id, "step_count": len(steps)},
    )

    executed: list[dict] = []
    failed_step: int | None = None
    abort_reason: str | None = None

    # === Step loop ===
    for idx, step in enumerate(steps, start=1):
        tool_name = step.get("tool_name") or ""
        tool_args = step.get("tool_args") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        tool_def = tools_registry.get_tool(tool_name)
        step_header = f"[步骤 {idx}/{len(steps)}] {tool_name}"

        if not tool_def:
            await send_to_client({
                "type": "trace", "phase": "tool_call", "event_type": "failure",
                "content": f"{step_header}: 工具不存在，跳过",
            })
            continue

        await send_to_client({
            "type": "trace", "phase": "tool_call", "event_type": "start",
            "content": f"{step_header}({json.dumps(tool_args, ensure_ascii=False)})",
        })
        await audit_logger.log(
            session_id, AuditPhase.TOOL_CALL, AuditEventType.START,
            f"Runbook step {idx}: {tool_name}", {"args": tool_args},
        )
        before_change_state = None
        backup_record = None

        # === Rule-engine command check (re-run because patterns may have evolved) ===
        cmd_check = _guardrail.check_command(json.dumps(tool_args))
        if not cmd_check.is_safe:
            await send_to_client({
                "type": "trace", "phase": "tool_call", "event_type": "blocked",
                "content": f"{step_header} 被规则引擎拦截: {cmd_check.detail}",
            })
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
                "impact": f"Runbook「{runbook_name}」步骤 {idx}/{len(steps)}",
            })
            await send_to_client({
                "type": "trace", "phase": "approval_request", "event_type": "pending",
                "content": f"等待用户审批 {step_header}",
            })

            try:
                approved = await asyncio.wait_for(approval_future, timeout=approval_timeout)
            except asyncio.TimeoutError:
                approved = False
            finally:
                approval_manager.remove_pending(request_id)

            if not approved:
                await send_to_client({
                    "type": "trace", "phase": "approval_response", "event_type": "failure",
                    "content": f"{step_header} 被拒绝，Runbook 中止",
                })
                failed_step = idx
                abort_reason = "用户拒绝执行"
                break

            await send_to_client({
                "type": "trace", "phase": "approval_response", "event_type": "success",
                "content": f"{step_header} 已批准",
            })

            # Backup (best-effort; ignore failures, the file may not be a path)
            try:
                from app.mcp_tools.backup import backup_manager
                target_path = tool_args.get("filepath") or tool_args.get("path") or tool_args.get("service")
                if target_path and isinstance(target_path, str):
                    backup_record = backup_manager.backup_file(target_path, operation=f"runbook:{tool_name}")
                    if backup_record:
                        await send_to_client({
                            "type": "trace", "phase": "execution", "event_type": "start",
                            "content": f"{step_header} 已备份 {target_path}",
                        })
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
            verification_error: str | None = None

            executed.append({
                "step": idx, "tool": tool_name,
                "success": success, "preview": result_str[:200],
            })

            if tool_def.risk_level in (RiskLevel.WRITE, RiskLevel.DESTRUCTIVE):
                try:
                    from app.agent.graph import _capture_change_diff, _verify_tool_result

                    verification = _verify_tool_result(tool_name, tool_args, result)
                    if verification:
                        await send_to_client({
                            "type": "trace",
                            "phase": "verification",
                            "event_type": verification["status"],
                            "content": f"{step_header} {verification['message']}",
                        })
                        if verification["status"] == "failure":
                            success = False
                            verification_error = verification["message"]
                            executed[-1]["success"] = False

                    change_diff = _capture_change_diff(tool_name, tool_args, backup_record, before_change_state)
                    if change_diff:
                        await send_to_client({
                            "type": "trace",
                            "phase": "verification",
                            "event_type": "success",
                            "content": f"{step_header} 变更对比:\n{change_diff}",
                        })
                except Exception as e:
                    logger.warning(f"Runbook verification failed for step {idx}: {e}")
                    success = False
                    verification_error = f"验证异常: {e}"
                    executed[-1]["success"] = False
                    await send_to_client({
                        "type": "trace",
                        "phase": "verification",
                        "event_type": "failure",
                        "content": f"{step_header} 验证异常: {e}",
                    })

            if success:
                await audit_logger.log(
                    session_id, AuditPhase.EXECUTION, AuditEventType.SUCCESS,
                    f"Runbook step {idx} succeeded: {tool_name}",
                )
                await send_to_client({
                    "type": "trace", "phase": "execution", "event_type": "success",
                    "content": f"{step_header} 执行成功",
                })
            else:
                err_msg = (
                    verification_error or result_repr.get("error", "工具返回 success=False")
                    if isinstance(result_repr, dict) else "工具返回失败"
                )
                await audit_logger.log(
                    session_id, AuditPhase.EXECUTION, AuditEventType.FAILURE,
                    f"Runbook step {idx} returned failure: {err_msg}",
                )
                await send_to_client({
                    "type": "trace", "phase": "execution", "event_type": "failure",
                    "content": f"{step_header} 执行失败: {err_msg}",
                })
                failed_step = idx
                abort_reason = f"步骤返回失败: {err_msg}"
                break

        except Exception as e:
            logger.error(f"Runbook step {idx} raised: {e}")
            await audit_logger.log(
                session_id, AuditPhase.EXECUTION, AuditEventType.FAILURE,
                f"Runbook step {idx} raised: {e}",
            )
            await send_to_client({
                "type": "trace", "phase": "execution", "event_type": "failure",
                "content": f"{step_header} 异常: {e}",
            })
            failed_step = idx
            abort_reason = f"步骤异常: {e}"
            break

    # === Bookkeeping: increment run_count even on partial failure ===
    try:
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            await db.execute(
                "UPDATE runbooks SET run_count = run_count + 1, last_run = ? WHERE id = ?",
                (datetime.now().isoformat(), runbook_id),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"Runbook run_count bump failed: {e}")

    # === Final summary message ===
    if failed_step is None:
        summary = (
            f"✅ Runbook「{runbook_name}」执行完成\n\n"
            f"共 {len(steps)} 步全部成功，详情见推理链路。"
        )
        await send_to_client({
            "type": "trace", "phase": "response", "event_type": "success",
            "content": f"Runbook 完成: {len(steps)} 步全部成功",
        })
    else:
        ok = sum(1 for e in executed if e["success"])
        summary = (
            f"⚠️ Runbook「{runbook_name}」在步骤 {failed_step}/{len(steps)} 中止\n\n"
            f"原因: {abort_reason or '未知'}\n"
            f"已成功执行 {ok} 步，详情见推理链路。"
        )
        await send_to_client({
            "type": "trace", "phase": "response", "event_type": "failure",
            "content": f"Runbook 中止于步骤 {failed_step}: {abort_reason}",
        })

    await audit_logger.log(
        session_id, AuditPhase.RESPONSE, AuditEventType.SUCCESS,
        f"Runbook replay finished: {runbook_name}",
        {"executed": len(executed), "failed_at": failed_step, "abort": abort_reason},
    )
    return summary
