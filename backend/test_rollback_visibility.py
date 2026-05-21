"""Regression checks for rollback visibility and backup restore."""

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.graph import assess_impact
from app.agent.tools_registry import RiskLevel, tools_registry
from app.mcp_tools import backup, file_tools


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
        assert "回滚：无可靠自动回滚" in service_text
        assert "预览：仅影响评估" in service_text

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "example.conf"
            target.write_text("before", encoding="utf-8")
            file_text = await assess_impact("write_file", {"filepath": str(target)}, "s", capture)
            assert file_text is not None
            assert "回滚：支持备份回滚" in file_text

            new_file_text = await assess_impact("create_file", {"filepath": str(Path(tmpdir) / "new.conf")}, "s", capture)
            assert new_file_text is not None
            assert "操作：创建文件" in new_file_text
            assert "回滚：无可靠自动回滚" in new_file_text

            new_dir_text = await assess_impact("create_directory", {"dirpath": str(Path(tmpdir) / "new-dir")}, "s", capture)
            assert new_dir_text is not None
            assert "操作：创建目录" in new_dir_text
            assert "回滚：无可靠自动回滚" in new_dir_text

            overwrite_text = await assess_impact(
                "create_file",
                {"filepath": str(target), "overwrite": True},
                "s",
                capture,
            )
            assert overwrite_text is not None
            assert "回滚：支持备份回滚" in overwrite_text
        assert any(event.get("phase") == "planning" for event in events)

    asyncio.run(scenario())


def test_create_file_tool_creates_real_file_without_overwriting_by_default() -> None:
    tool = tools_registry.get_tool("create_file")
    assert tool is not None
    assert tool.risk_level == RiskLevel.WRITE
    assert tool.display_name == "创建文件"
    assert tool.supports_rollback is True
    assert tool.rollback_strategy == "backup"

    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "created.conf"

        created = file_tools.create_file(str(target), "hello")
        assert created.success is True
        assert target.read_text(encoding="utf-8") == "hello"

        duplicate = file_tools.create_file(str(target), "changed")
        assert duplicate.success is False
        assert target.read_text(encoding="utf-8") == "hello"

        overwritten = file_tools.create_file(str(target), "changed", overwrite=True)
        assert overwritten.success is True
        assert target.read_text(encoding="utf-8") == "changed"


def main() -> None:
    test_backup_list_and_rollback_restore_real_file()
    test_rollback_tool_is_destructive_and_approval_gated()
    test_impact_text_is_truthful_about_rollback()
    test_create_file_tool_creates_real_file_without_overwriting_by_default()
    print("rollback visibility regression OK")


if __name__ == "__main__":
    main()
