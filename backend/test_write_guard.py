"""Tests for the structured final-response truthfulness guard.

The old guard tried to infer truthfulness from final Markdown keywords. These
tests exercise the replacement: structured JSON must cite real tool-ledger
call_ids, and write-completion claims are rendered only from successful,
approved WRITE/DESTRUCTIVE ledger rows.
"""

import asyncio
import json
import os
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.final_response import (  # noqa: E402
    generate_structured_final_reply,
    make_tool_ledger_entry,
    render_structured_reply,
    validate_structured_reply,
)
from app.agent.llm import call_llm  # noqa: E402
from app.mcp_tools.process_tools import ToolResult  # noqa: E402


def _read_ledger(call_id: str = "call_read_1") -> list[dict]:
    return [
        make_tool_ledger_entry(
            call_id=call_id,
            tool_name="get_service_status",
            tool_args={"service": "nginx"},
            risk_level="read",
            status="success",
            result=ToolResult(success=True, data="Active: inactive (dead)"),
            execution_state="executed",
            approval_granted=False,
        )
    ]


def _write_ledger(
    *,
    status: str = "success",
    approval_granted: bool = True,
    call_id: str = "call_write_1",
) -> list[dict]:
    return [
        make_tool_ledger_entry(
            call_id=call_id,
            tool_name="start_service",
            tool_args={"service": "nginx"},
            risk_level="write",
            status=status,
            result=ToolResult(
                success=status == "success",
                data="Service nginx started" if status == "success" else "",
                error=None if status == "success" else "systemctl failed",
            ),
            error=None if status == "success" else "systemctl failed",
            execution_state="executed" if status == "success" else "failed",
            approval_granted=approval_granted,
        )
    ]


def test_claim_evidence_call_ids_must_exist() -> None:
    errors = validate_structured_reply(
        {
            "conclusion": "nginx 当前为 inactive",
            "claims": [
                {
                    "text": "nginx 当前为 inactive",
                    "evidence_call_ids": ["missing_call"],
                    "claim_type": "observed_state",
                }
            ],
            "executed_actions": [],
            "recommended_actions": [],
        },
        _read_ledger(),
    )

    assert any("不存在的 call_id" in error for error in errors)


def test_executed_actions_must_be_successful_approved_writes() -> None:
    for ledger in (
        _read_ledger(),
        _write_ledger(status="failure", approval_granted=True),
        _write_ledger(status="success", approval_granted=False),
    ):
        call_id = ledger[0]["call_id"]
        errors = validate_structured_reply(
            {
                "conclusion": "操作已执行",
                "claims": [],
                "executed_actions": [
                    {"tool_name": ledger[0]["tool_name"], "args": {}, "call_id": call_id}
                ],
                "recommended_actions": [],
            },
            ledger,
        )
        assert any("不是已审批且成功的写操作" in error for error in errors)


def test_recommended_actions_must_be_unexecuted() -> None:
    errors = validate_structured_reply(
        {
            "conclusion": "建议启动 nginx",
            "claims": [],
            "executed_actions": [],
            "recommended_actions": [
                {
                    "tool_name": "start_service",
                    "args": {"service": "nginx"},
                    "executed": True,
                    "requires_approval": True,
                }
            ],
        },
        [],
    )

    assert any("executed 必须是 false" in error for error in errors)


def test_backend_rendering_marks_recommendations_as_not_executed() -> None:
    markdown = render_structured_reply(
        {
            "conclusion": "nginx 当前未运行，建议启动。",
            "claims": [
                {
                    "text": "nginx 当前为 inactive",
                    "evidence_call_ids": ["call_read_1"],
                    "claim_type": "observed_state",
                }
            ],
            "executed_actions": [],
            "recommended_actions": [
                {
                    "tool_name": "start_service",
                    "args": {"service": "nginx"},
                    "executed": False,
                    "requires_approval": True,
                }
            ],
        },
        _read_ledger(),
    )

    assert "`call_read_1`/get_service_status" in markdown
    assert "`start_service`" in markdown
    assert "尚未执行" in markdown
    assert "需要审批" in markdown


def test_backend_rendering_executed_write_from_ledger_only() -> None:
    markdown = render_structured_reply(
        {
            "conclusion": "nginx 启动操作已执行。",
            "claims": [],
            "executed_actions": [
                {"tool_name": "start_service", "args": {"service": "nginx"}, "call_id": "call_write_1"}
            ],
            "recommended_actions": [],
        },
        _write_ledger(call_id="call_write_1"),
    )

    assert "`call_write_1`/start_service 已执行成功" in markdown
    assert "审批：已通过" in markdown


def test_real_llm_structured_reply_passes_ledger_validation() -> None:
    async def scenario() -> None:
        result = await generate_structured_final_reply(
            user_message="查看 nginx 当前状态，如果没有运行请给出建议但不要执行",
            messages=[
                {"role": "user", "content": "查看 nginx 当前状态"},
                {
                    "role": "tool",
                    "tool_call_id": "call_read_1",
                    "content": json.dumps(
                        {"success": True, "data": "Active: inactive (dead)", "error": None},
                        ensure_ascii=False,
                    ),
                },
            ],
            draft_response=(
                "nginx 当前为 inactive (dead)。建议调用 start_service 启动 nginx，"
                "但本轮不要执行写操作。"
            ),
            tool_ledger=_read_ledger(),
            llm_call=call_llm,
            require_grounded_output=True,
            max_retries=1,
        )

        assert result["valid"] is True, result
        assert "`call_read_1`/get_service_status" in result["markdown"]
        assert "尚未执行" in result["markdown"]
        assert "已执行成功" not in result["markdown"]
        assert "start_service" in result["markdown"]

    asyncio.run(scenario())
