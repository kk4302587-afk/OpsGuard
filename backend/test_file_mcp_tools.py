"""Regression checks for common filesystem MCP tools."""

import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.tools_registry import RiskLevel, tools_registry
from app.mcp_tools import file_tools


def test_common_file_tools_are_registered_with_correct_risk() -> None:
    expected = {
        "list_directory": RiskLevel.READ,
        "read_file": RiskLevel.READ,
        "find_files": RiskLevel.READ,
        "create_directory": RiskLevel.WRITE,
    }

    for name, risk in expected.items():
        tool = tools_registry.get_tool(name)
        assert tool is not None, name
        assert tool.risk_level == risk, name
        assert tool.category == "file", name


def test_list_directory_and_read_file_return_real_content() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        visible = root / "sample.txt"
        hidden = root / ".secret"
        visible.write_text("hello opsguard", encoding="utf-8")
        hidden.write_text("hidden", encoding="utf-8")

        listed = file_tools.list_directory(str(root))
        assert listed.success is True
        names = [entry["name"] for entry in listed.data["entries"]]
        assert "sample.txt" in names
        assert ".secret" not in names

        listed_hidden = file_tools.list_directory(str(root), show_hidden=True)
        hidden_names = [entry["name"] for entry in listed_hidden.data["entries"]]
        assert ".secret" in hidden_names

        read = file_tools.read_file(str(visible))
        assert read.success is True
        assert read.data["content"] == "hello opsguard"
        assert read.data["truncated"] is False


def test_find_files_and_create_directory_execute_real_operations() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        nested = root / "opsguard" / "test"

        created = file_tools.create_directory(str(nested))
        assert created.success is True
        assert nested.is_dir()

        duplicate = file_tools.create_directory(str(nested))
        assert duplicate.success is False
        assert "目录已存在" in (duplicate.error or "")

        ok_existing = file_tools.create_directory(str(nested), exist_ok=True)
        assert ok_existing.success is True

        sample = nested / "sample.log"
        sample.write_text("line", encoding="utf-8")

        found_file = file_tools.find_files(str(root), "*.log", file_type="file")
        assert found_file.success is True
        assert str(sample) in [item["path"] for item in found_file.data["matches"]]

        found_dir = file_tools.find_files(str(root), "test", file_type="directory")
        assert found_dir.success is True
        assert str(nested) in [item["path"] for item in found_dir.data["matches"]]


def main() -> None:
    test_common_file_tools_are_registered_with_correct_risk()
    test_list_directory_and_read_file_return_real_content()
    test_find_files_and_create_directory_execute_real_operations()
    print("file MCP tools regression OK")


if __name__ == "__main__":
    main()
