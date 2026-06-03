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

    assert "服务状态检查" in markdown
    assert "启动服务：nginx" in markdown
    assert "尚未执行" in markdown
    assert "需要审批" in markdown
    assert "`start_service`" not in markdown
    assert '{"service": "nginx"}' not in markdown


def test_backend_rendering_recommendations_are_user_facing() -> None:
    markdown = render_structured_reply(
        {
            "conclusion": "22 端口暴露范围需要收紧。",
            "claims": [
                {
                    "text": "22 端口由 sshd 监听。",
                    "evidence_call_ids": ["call_read_1"],
                    "claim_type": "observed_state",
                }
            ],
            "executed_actions": [],
            "recommended_actions": [
                {
                    "tool_name": "update_firewall_rules",
                    "args": {"action": "restrict_ssh_access", "source_ip": "specific_internal_ip"},
                    "executed": False,
                    "requires_approval": True,
                },
                {
                    "title": "检查 SSH 子配置",
                    "purpose": "确认主配置引用的子配置是否覆盖登录策略。",
                    "impact": "只读取配置，不改变系统状态。",
                    "precondition": "",
                    "action_type": "只读检查",
                    "next_step": "读取后再决定是否需要提交变更审批。",
                    "tool_name": "read_config_file",
                    "args": {"filepath": "/etc/ssh/sshd_config.d/*.conf"},
                    "executed": False,
                    "requires_approval": False,
                },
            ],
        },
        _read_ledger(),
    )

    assert "调整防火墙访问规则" in markdown
    assert "检查 SSH 子配置" in markdown
    assert "目的：" in markdown
    assert "影响：" in markdown
    assert "类型：访问控制变更，需要审批" in markdown
    assert "类型：只读检查，无需审批" in markdown
    assert "update_firewall_rules" not in markdown
    assert "specific_internal_ip" not in markdown
    assert '{"action": "restrict_ssh_access"' not in markdown


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

    assert "启动服务：nginx：已执行成功" in markdown
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
        assert "服务状态检查" in result["markdown"]
        assert "尚未执行" in result["markdown"]
        assert "已执行成功" not in result["markdown"]
        assert "start_service" not in result["markdown"]

    asyncio.run(scenario())
