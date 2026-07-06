"""Regression checks for AI-SRE 7.6 execution policy."""

import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.agent.execution_policy import evaluate_tool_policy, policy_summary
from app.agent.tools_registry import tools_registry
from app.config import settings


def test_policy_blocks_write_outside_approved_paths() -> None:
    tool = tools_registry.get_tool("delete_file")
    assert tool is not None

    old_allowed = list(settings.policy.allowed_write_paths)
    old_denied = list(settings.policy.denied_paths)
    old_tmp_cleanup = settings.policy.allow_tmp_file_cleanup
    try:
        with tempfile.TemporaryDirectory() as approved:
            settings.policy.allowed_write_paths = [approved]
            settings.policy.denied_paths = []
            settings.policy.allow_tmp_file_cleanup = False
            decision = evaluate_tool_policy("delete_file", {"filepath": "/tmp/outside.txt"}, tool)
            assert decision.allowed is False
            assert "outside approved paths" in policy_summary(decision)

            inside = str(Path(approved) / "inside.txt")
            allowed = evaluate_tool_policy("delete_file", {"filepath": inside}, tool)
            assert allowed.allowed is True
    finally:
        settings.policy.allowed_write_paths = old_allowed
        settings.policy.denied_paths = old_denied
        settings.policy.allow_tmp_file_cleanup = old_tmp_cleanup


def test_policy_allows_bounded_tmp_delete_file_cleanup() -> None:
    tool = tools_registry.get_tool("delete_file")
    assert tool is not None

    old_allowed = list(settings.policy.allowed_write_paths)
    old_denied = list(settings.policy.denied_paths)
    old_tmp_cleanup = settings.policy.allow_tmp_file_cleanup
    tmp_path = Path(tempfile.gettempdir()) / "opsguard-policy-cleanup.dat_1.1.0"
    tmp_path.write_text("temporary data", encoding="utf-8")
    try:
        settings.policy.allowed_write_paths = ["/var/lib/opsguard/**"]
        settings.policy.denied_paths = []
        settings.policy.allow_tmp_file_cleanup = True

        decision = evaluate_tool_policy("delete_file", {"filepath": str(tmp_path)}, tool)
        assert decision.allowed is True

        settings.policy.allow_tmp_file_cleanup = False
        blocked = evaluate_tool_policy("delete_file", {"filepath": str(tmp_path)}, tool)
        assert blocked.allowed is False
        assert "outside approved paths" in policy_summary(blocked)
    finally:
        tmp_path.unlink(missing_ok=True)
        settings.policy.allowed_write_paths = old_allowed
        settings.policy.denied_paths = old_denied
        settings.policy.allow_tmp_file_cleanup = old_tmp_cleanup


def test_policy_tmp_cleanup_does_not_allow_missing_dirs_or_symlinks() -> None:
    tool = tools_registry.get_tool("delete_file")
    assert tool is not None

    old_allowed = list(settings.policy.allowed_write_paths)
    old_denied = list(settings.policy.denied_paths)
    old_tmp_cleanup = settings.policy.allow_tmp_file_cleanup
    tmp_dir = Path(tempfile.mkdtemp(prefix="opsguard-policy-dir-"))
    target = tmp_dir / "target.txt"
    link = Path(tempfile.gettempdir()) / "opsguard-policy-link.txt"
    target.write_text("target", encoding="utf-8")
    try:
        settings.policy.allowed_write_paths = ["/var/lib/opsguard/**"]
        settings.policy.denied_paths = []
        settings.policy.allow_tmp_file_cleanup = True
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)

        cases = [
            tmp_dir,
            Path(tempfile.gettempdir()) / "opsguard-policy-missing.dat",
            link,
        ]
        for path in cases:
            decision = evaluate_tool_policy("delete_file", {"filepath": str(path)}, tool)
            assert decision.allowed is False
            assert "outside approved paths" in policy_summary(decision)
    finally:
        if link.exists() or link.is_symlink():
            link.unlink()
        target.unlink(missing_ok=True)
        tmp_dir.rmdir()
        settings.policy.allowed_write_paths = old_allowed
        settings.policy.denied_paths = old_denied
        settings.policy.allow_tmp_file_cleanup = old_tmp_cleanup


def test_policy_blocks_denied_paths_before_approval() -> None:
    tool = tools_registry.get_tool("write_file")
    assert tool is not None

    decision = evaluate_tool_policy("write_file", {"filepath": "/etc/passwd", "content": "x"}, tool)
    assert decision.allowed is False
    assert any("denied or protected" in reason for reason in decision.reasons)


def test_policy_metadata_includes_execution_identity() -> None:
    tool = tools_registry.get_tool("restart_service")
    assert tool is not None

    decision = evaluate_tool_policy("restart_service", {"service": "nginx"}, tool)
    data = decision.to_dict()
    assert data["approval_level"] == "standard"
    assert data["execution_identity"]["run_as_user"] == settings.execution.run_as_user
    assert data["execution_identity"]["uses_sudo"] is True


if __name__ == "__main__":
    test_policy_blocks_write_outside_approved_paths()
    test_policy_allows_bounded_tmp_delete_file_cleanup()
    test_policy_tmp_cleanup_does_not_allow_missing_dirs_or_symlinks()
    test_policy_blocks_denied_paths_before_approval()
    test_policy_metadata_includes_execution_identity()
    print("execution policy regression OK")
