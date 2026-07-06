"""Regression checks for rollback visibility and backup restore."""

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.graph import assess_impact
from app.agent.rollback_plan import effective_rollback_capability, prepare_rollback_point
from app.api.backups import rollback_backup as rollback_backup_api
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


def test_parameter_level_rollback_capability_covers_inverse_records() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        existing = root / "existing.conf"
        existing.write_text("before", encoding="utf-8")
        new_file = root / "new.conf"
        new_dir = root / "new-dir"
        copied = root / "copy.conf"

        cases = [
            ("create_file", {"filepath": str(new_file)}, True, "inverse_action"),
            ("create_directory", {"dirpath": str(new_dir)}, True, "inverse_action"),
            ("write_file", {"filepath": str(existing), "content": "after"}, True, "backup"),
            ("copy_file", {"source": str(existing), "destination": str(copied)}, True, "inverse_action"),
            ("change_permissions", {"filepath": str(existing), "mode": "600"}, True, "inverse_action"),
            ("create_file", {"filepath": str(root / "missing-parent" / "x")}, False, "none"),
        ]
        for tool_name, args, expected_supported, expected_strategy in cases:
            tool = tools_registry.get_tool(tool_name)
            assert tool is not None
            supported, strategy = effective_rollback_capability(tool_name, args, tool)
            assert supported is expected_supported, tool_name
            assert strategy == expected_strategy, tool_name


def test_prepare_rollback_point_creates_real_inverse_records() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        _isolate_backup_manager(tmpdir)
        root = Path(tmpdir)
        new_file = root / "new.conf"
        new_dir = root / "new-dir"
        existing = root / "existing.conf"
        existing.write_text("before", encoding="utf-8")
        copied = root / "copied.conf"

        create_record = prepare_rollback_point("create_file", {"filepath": str(new_file)})
        assert create_record["rollback_type"] == "delete_created_path"
        assert create_record["original_path"] == str(new_file.absolute())

        dir_record = prepare_rollback_point("create_directory", {"dirpath": str(new_dir)})
        assert dir_record["rollback_type"] == "delete_created_path"
        assert str(new_dir) in dir_record["created_paths"]

        copy_record = prepare_rollback_point("copy_file", {"source": str(existing), "destination": str(copied)})
        assert copy_record["rollback_type"] == "delete_created_path"

        chmod_record = prepare_rollback_point("change_permissions", {"filepath": str(existing), "mode": "600"})
        assert chmod_record["rollback_type"] == "restore_permissions"
        assert chmod_record["mode"]


def test_inverse_rollback_records_execute_without_backup_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        _isolate_backup_manager(tmpdir)
        root = Path(tmpdir)

        created_file = root / "created.conf"
        file_record = prepare_rollback_point("create_file", {"filepath": str(created_file)})
        created_file.write_text("new", encoding="utf-8")
        assert backup.backup_manager.rollback(file_record["id"]) is True
        assert not created_file.exists()

        created_dir = root / "parent" / "child"
        dir_record = prepare_rollback_point("create_directory", {"dirpath": str(created_dir), "parents": True})
        created_dir.mkdir(parents=True)
        assert backup.backup_manager.rollback(dir_record["id"]) is True
        assert not created_dir.exists()
        assert not (root / "parent").exists()


def test_impact_text_is_truthful_about_rollback() -> None:
    async def scenario() -> None:
        events: list[dict] = []

        async def capture(event: dict) -> None:
            events.append(event)

        service_text = await assess_impact("restart_service", {"service": "nginx"}, "s", capture)
        assert service_text is not None
        assert "回滚：支持服务状态恢复" in service_text
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
            assert "回滚：支持反向操作" in new_file_text

            new_dir_text = await assess_impact("create_directory", {"dirpath": str(Path(tmpdir) / "new-dir")}, "s", capture)
            assert new_dir_text is not None
            assert "操作：创建目录" in new_dir_text
            assert "回滚：支持反向操作" in new_dir_text

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


def test_backup_api_does_not_bypass_rollback_approval() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _isolate_backup_manager(tmpdir)
            target = Path(tmpdir) / "config.conf"
            target.write_text("before", encoding="utf-8")
            record = backup.backup_manager.backup_file(str(target), operation="write_file")
            assert record and record["id"]

            target.write_text("after", encoding="utf-8")
            response = await rollback_backup_api(record["id"])
            assert response["requires_approval"] is True
            assert response["tool_name"] == "rollback_backup"
            assert target.read_text(encoding="utf-8") == "after"

    asyncio.run(scenario())


def main() -> None:
    test_backup_list_and_rollback_restore_real_file()
    test_rollback_tool_is_destructive_and_approval_gated()
    test_parameter_level_rollback_capability_covers_inverse_records()
    test_prepare_rollback_point_creates_real_inverse_records()
    test_inverse_rollback_records_execute_without_backup_artifact()
    test_impact_text_is_truthful_about_rollback()
    test_create_file_tool_creates_real_file_without_overwriting_by_default()
    test_backup_api_does_not_bypass_rollback_approval()
    print("rollback visibility regression OK")


if __name__ == "__main__":
    main()
