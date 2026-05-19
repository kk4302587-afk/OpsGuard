"""Regression checks for change-aware RCA recent-change evidence."""

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent import graph
from app.agent.tools_registry import RiskLevel, ToolDefinition, tools_registry
from app.mcp_tools import recent_changes
from app.mcp_tools.process_tools import ToolResult


def test_recent_changes_reports_real_config_mtime() -> None:
    """A real modified config file is returned as an observed change."""
    original_config_paths = recent_changes._CONFIG_PATHS
    original_config_dirs = recent_changes._CONFIG_DIRS
    original_package_logs = recent_changes._PACKAGE_HISTORY_FILES
    original_firewall_files = recent_changes._FIREWALL_FILES

    with tempfile.TemporaryDirectory() as tmpdir:
        config = Path(tmpdir) / "nginx.conf"
        config.write_text("worker_processes auto;\n", encoding="utf-8")

        try:
            recent_changes._CONFIG_PATHS = [str(config)]
            recent_changes._CONFIG_DIRS = []
            recent_changes._PACKAGE_HISTORY_FILES = []
            recent_changes._FIREWALL_FILES = []

            result = recent_changes.get_recent_changes(window_hours=24, limit=10)
        finally:
            recent_changes._CONFIG_PATHS = original_config_paths
            recent_changes._CONFIG_DIRS = original_config_dirs
            recent_changes._PACKAGE_HISTORY_FILES = original_package_logs
            recent_changes._FIREWALL_FILES = original_firewall_files

    assert result.success is True
    assert isinstance(result.data, dict)
    changes = result.data["changes"]
    assert any(change["target"] == str(config) for change in changes)
    assert any(change["change_type"] == "config_file_modified" for change in changes)
    config_status = result.data["source_status"]["config_mtime"]
    assert config_status["status"] == "ok"
    assert config_status["observed"] >= 1


def test_recent_changes_preserves_failed_source_status() -> None:
    """A collector failure must not be collapsed into a fake no-change result."""
    original_which = recent_changes.shutil.which
    original_run_command = recent_changes._run_command
    original_config_paths = recent_changes._CONFIG_PATHS
    original_config_dirs = recent_changes._CONFIG_DIRS
    original_package_logs = recent_changes._PACKAGE_HISTORY_FILES
    original_firewall_files = recent_changes._FIREWALL_FILES

    def fake_run_command(args: list[str], *, timeout: int):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="journal unavailable")

    try:
        recent_changes.shutil.which = lambda name: "/usr/bin/journalctl" if name == "journalctl" else None
        recent_changes._run_command = fake_run_command
        recent_changes._CONFIG_PATHS = []
        recent_changes._CONFIG_DIRS = []
        recent_changes._PACKAGE_HISTORY_FILES = []
        recent_changes._FIREWALL_FILES = []

        result = recent_changes.get_recent_changes(window_hours=24, limit=10)
    finally:
        recent_changes.shutil.which = original_which
        recent_changes._run_command = original_run_command
        recent_changes._CONFIG_PATHS = original_config_paths
        recent_changes._CONFIG_DIRS = original_config_dirs
        recent_changes._PACKAGE_HISTORY_FILES = original_package_logs
        recent_changes._FIREWALL_FILES = original_firewall_files

    assert result.success is True
    assert isinstance(result.data, dict)
    assert result.data["source_status"]["systemd_journal"]["status"] == "failed"
    assert "journal unavailable" in result.data["source_status"]["systemd_journal"]["reason"]
    assert "unavailable" in result.data["summary"] or "limited sources" in result.data["summary"]


def test_tool_registry_exposes_recent_changes_as_read_only() -> None:
    tool = tools_registry.get_tool("get_recent_changes")
    assert tool is not None
    assert tool.risk_level == RiskLevel.READ
    assert tool.category == "recent_changes"


def test_recent_changes_node_emits_trace_and_context() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        original_get_tool = graph.tools_registry.get_tool
        original_log = graph.audit_logger.log

        def fake_recent_changes(window_hours: int = 24, limit: int = 30) -> ToolResult:
            return ToolResult(
                success=True,
                data={
                    "window_hours": window_hours,
                    "changes": [
                        {
                            "source": "config_mtime",
                            "change_type": "config_file_modified",
                            "target": "/etc/nginx/nginx.conf",
                            "timestamp": "2026-05-19T10:00:00",
                            "detail": {"sha256": "abc"},
                            "confidence": "high",
                        }
                    ],
                    "source_status": {"config_mtime": {"status": "ok", "observed": 1}},
                    "summary": "Found 1 recent changes in the last 24h",
                },
            )

        def fake_get_tool(name: str):
            if name == "get_recent_changes":
                return ToolDefinition(
                    name="get_recent_changes",
                    description="Collect recent local system changes for RCA",
                    parameters={"type": "object", "properties": {}},
                    function=fake_recent_changes,
                    risk_level=RiskLevel.READ,
                    category="recent_changes",
                )
            return original_get_tool(name)

        async def capture_event(event: dict) -> None:
            events.append(event)

        try:
            graph.tools_registry.get_tool = fake_get_tool
            graph.audit_logger.log = lambda *args, **kwargs: asyncio.sleep(0)

            result = await graph.recent_changes_node(
                {
                    "session_id": "recent-node",
                    "user_message": "nginx failed",
                    "messages": [],
                    "final_response": "",
                    "is_blocked": False,
                    "block_reason": "",
                    "risk_warning": "",
                    "knowledge_hint": "",
                    "recent_changes_hint": "",
                    "iteration": 0,
                    "send_to_client": capture_event,
                }
            )
        finally:
            graph.tools_registry.get_tool = original_get_tool
            graph.audit_logger.log = original_log

        assert "Recent Change Evidence" in result["recent_changes_hint"]
        assert "/etc/nginx/nginx.conf" in result["recent_changes_hint"]
        success_events = [
            event for event in events
            if event.get("phase") == "recent_changes" and event.get("event_type") == "success"
        ]
        assert success_events
        assert success_events[0].get("execution_state") == "executed"
        assert success_events[0].get("source") == "get_recent_changes"

    asyncio.run(scenario())


def main() -> None:
    test_recent_changes_reports_real_config_mtime()
    test_recent_changes_preserves_failed_source_status()
    test_tool_registry_exposes_recent_changes_as_read_only()
    test_recent_changes_node_emits_trace_and_context()
    print("recent changes regression OK")


if __name__ == "__main__":
    main()
