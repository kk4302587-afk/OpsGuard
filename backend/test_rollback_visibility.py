"""Regression checks for rollback visibility and backup restore."""

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.graph import assess_impact
from app.agent.tools_registry import RiskLevel, tools_registry
from app.mcp_tools import backup


def _isolate_backup_manager(tmpdir: str) -> None:
    backup.backup_manager._backup_dir = Path(tmpdir) / "backups"
    backup.backup_manager._backup_dir.mkdir(parents=True, exist_ok=True)
    backup.backup_manager._manifest_path = backup.backup_manager._backup_dir / "manifest.json"
    backup.backup_manager._manifest = []


def test_backup_list_and_rollback_restore_real_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        _isolate_backup_manager(tmpdir)
        target = Path(tmpdir) / "config.conf"
        target.write_text("before", encoding="utf-8")

        record = backup.backup_manager.backup_file(str(target), operation="write_file")
        assert record and record["id"]

        target.write_text("after", encoding="utf-8")
        listed = backup.list_backups(filepath=str(target), limit=10)
        assert listed.success is True
        assert listed.data["count"] == 1

        restored = backup.rollback_backup(record["id"])
        assert restored.success is True
        assert target.read_text(encoding="utf-8") == "before"


def test_rollback_tool_is_destructive_and_approval_gated() -> None:
    tool = tools_registry.get_tool("rollback_backup")
    assert tool is not None
    assert tool.risk_level == RiskLevel.DESTRUCTIVE
    assert tool.rollback_strategy == "manual"


def test_impact_text_is_truthful_about_rollback() -> None:
    async def scenario() -> None:
        events: list[dict] = []

        async def capture(event: dict) -> None:
            events.append(event)

        service_text = await assess_impact("restart_service", {"service": "nginx"}, "s", capture)
        assert service_text is not None
        assert "no reliable automated rollback" in service_text

        file_text = await assess_impact("write_file", {"filepath": "/tmp/example.conf"}, "s", capture)
        assert file_text is not None
        assert "Rollback: backup strategy" in file_text
        assert any(event.get("phase") == "planning" for event in events)

    asyncio.run(scenario())


def main() -> None:
    test_backup_list_and_rollback_restore_real_file()
    test_rollback_tool_is_destructive_and_approval_gated()
    test_impact_text_is_truthful_about_rollback()
    print("rollback visibility regression OK")


if __name__ == "__main__":
    main()
