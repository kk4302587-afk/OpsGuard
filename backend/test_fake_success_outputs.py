"""Regression checks for fake-success tool outputs.

The tests monkeypatch subprocess calls and do not modify the host system.
They focus on paths that previously could return success even when a command
failed or a verification step did not complete.
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

os.chdir(Path(__file__).parent)

from app.api import topology
from app.mcp_tools import config_tools, firewall_tools, network_tools


def _result(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_network_command_failure() -> None:
    original = network_tools.subprocess.run
    try:
        network_tools.subprocess.run = lambda *args, **kwargs: _result(127, stderr="ss missing")
        result = network_tools.get_listening_ports()
        assert not result.success
        assert "ss missing" in (result.error or "")
    finally:
        network_tools.subprocess.run = original


def test_config_syntax_invalid_is_explicit() -> None:
    original = config_tools.subprocess.run
    try:
        config_tools.subprocess.run = lambda *args, **kwargs: _result(1, stderr="syntax is invalid")
        result = config_tools.check_config_syntax("/etc/nginx/nginx.conf")
        assert result.success
        assert result.data["checked"] is True
        assert result.data["valid"] is False
        assert "syntax is invalid" in result.data["errors"]
    finally:
        config_tools.subprocess.run = original


def test_firewalld_reload_failure_is_not_success() -> None:
    original_detect = firewall_tools._detect_firewall
    original_run = firewall_tools.subprocess.run
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if "--reload" in cmd:
            return _result(1, stderr="reload failed")
        return _result(0, stdout="success")

    try:
        firewall_tools._detect_firewall = lambda: "firewall-cmd"
        firewall_tools.subprocess.run = fake_run
        result = firewall_tools.allow_port(8080, "tcp")
        assert not result.success
        assert "reload failed" in (result.error or "")
        assert any("--reload" in cmd for cmd in calls)
    finally:
        firewall_tools._detect_firewall = original_detect
        firewall_tools.subprocess.run = original_run


def test_topology_marks_inferred_edges() -> None:
    original_system = topology.platform.system
    original_net_connections = topology.psutil.net_connections
    import os as _os
    import subprocess

    original_run = subprocess.run
    original_exists = _os.path.exists

    class Conn:
        status = "LISTEN"
        pid = None

    try:
        topology.platform.system = lambda: "Linux"
        topology.psutil.net_connections = lambda kind="inet": [Conn()]

        subprocess.run = lambda *args, **kwargs: _result(
            0,
            stdout="nginx.service loaded active running nginx\n",
        )
        _os.path.exists = lambda path: path == "/etc/nginx/nginx.conf"

        graph = asyncio.run(topology.get_topology_graph())
        inferred_edges = [edge for edge in graph["edges"] if edge.get("inferred")]
        assert any(edge["relation"] == "configured_by" for edge in inferred_edges)
    finally:
        topology.platform.system = original_system
        topology.psutil.net_connections = original_net_connections
        subprocess.run = original_run
        _os.path.exists = original_exists


def main() -> None:
    test_network_command_failure()
    test_config_syntax_invalid_is_explicit()
    test_firewalld_reload_failure_is_not_success()
    test_topology_marks_inferred_edges()
    print("fake success regression OK")


if __name__ == "__main__":
    main()
