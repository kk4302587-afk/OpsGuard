"""Regression checks for AI-SRE 7.6-A/B approval previews."""

import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.operation_preview import build_operation_preview
from app.mcp_tools import backup


def _isolate_backup_manager(tmpdir: str) -> None:
    backup.backup_manager._backup_dir = Path(tmpdir) / "backups"
    backup.backup_manager._backup_dir.mkdir(parents=True, exist_ok=True)
    backup.backup_manager._manifest_path = backup.backup_manager._backup_dir / "manifest.json"
    backup.backup_manager._manifest = []


def test_write_file_append_preview_shows_unified_diff() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "app.conf"
        target.write_text("line1\n", encoding="utf-8")

        preview = build_operation_preview(
            "write_file",
            {"filepath": str(target), "content": "line2\n", "append": True},
        )

        assert preview["status"] == "available"
        assert preview["preview_type"] == "diff"
        assert "line2" in preview["diff"]
        assert str(target) in preview["before_summary"]


def test_delete_file_preview_shows_metadata_and_backup_capability() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "old.conf"
        target.write_text("remove me", encoding="utf-8")

        preview = build_operation_preview("delete_file", {"filepath": str(target)})

        assert preview["status"] == "available"
        assert preview["target"] == "file"
        assert preview["metadata"]["backup_capable"] is True
        assert preview["metadata"]["metadata"]["size"] == len("remove me")
        assert "will be removed" in preview["after_summary"]


def test_rollback_backup_preview_shows_restore_overwrite_impact() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        _isolate_backup_manager(tmpdir)
        target = Path(tmpdir) / "service.conf"
        target.write_text("before\nkeep\n", encoding="utf-8")
        record = backup.backup_manager.backup_file(str(target), operation="write_file")
        assert record
        target.write_text("after\nkeep\n", encoding="utf-8")

        preview = build_operation_preview("rollback_backup", {"backup_id": record["id"]})

        assert preview["status"] == "available"
        assert preview["preview_type"] == "restore_preview"
        assert preview["metadata"]["backup_id"] == record["id"]
        assert preview["metadata"]["path"] == record["original_path"]
        assert preview["metadata"]["planned_bytes"] == len("before\nkeep\n")
        assert preview["metadata"]["current_bytes"] == len("after\nkeep\n")
        assert preview["metadata"]["will_overwrite"] is True
        assert "-after" in preview["diff"]
        assert "+before" in preview["diff"]
        assert str(target) in preview["diff"]
        assert record["original_path"] in preview["after_summary"]


def test_package_preview_falls_back_cleanly_when_simulation_unavailable() -> None:
    preview = build_operation_preview("install_package", {"name": ""})

    assert preview["status"] == "unavailable"
    assert preview["preview_type"] == "command_dry_run"
    assert preview["limitations"]


def main() -> None:
    test_write_file_append_preview_shows_unified_diff()
    test_delete_file_preview_shows_metadata_and_backup_capability()
    test_rollback_backup_preview_shows_restore_overwrite_impact()
    test_package_preview_falls_back_cleanly_when_simulation_unavailable()
    print("operation preview regression OK")


if __name__ == "__main__":
    main()
