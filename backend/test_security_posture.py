"""Regression checks for security posture scans."""

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.api import security_posture  # noqa: E402
from app.agent import graph  # noqa: E402


def test_security_posture_scan_shape_and_persistence() -> None:
    async def scenario() -> None:
        original_get_path = security_posture.get_knowledge_db_path

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            try:
                security_posture.get_knowledge_db_path = lambda: db_path

                generated = await security_posture.generate_security_posture_scan()
                latest = await security_posture.get_latest_security_posture_scan()
            finally:
                security_posture.get_knowledge_db_path = original_get_path

        assert latest["scan_id"] == generated["scan_id"]
        assert isinstance(generated["security_score"], int)
        assert 0 <= generated["security_score"] <= 100
        assert generated["risk_level"] in {"healthy", "attention", "warning", "critical"}
        assert isinstance(generated["metrics"], dict)
        assert isinstance(generated["risks"], list)
        assert isinstance(generated["attack_sources"], list)
        assert isinstance(generated["exposed_services"], list)
        assert isinstance(generated["baseline_checks"], list)
        assert isinstance(generated["intrusion_findings"], list)
        assert isinstance(generated["suspicious_persistence"], list)
        assert isinstance(generated["suspicious_processes"], list)
        assert isinstance(generated["suspicious_files"], list)
        assert isinstance(generated["timeline"], list)
        assert generated["scan_status"] in {"success", "partial"}

    asyncio.run(scenario())


def test_login_correlation_becomes_intrusion_finding() -> None:
    scanner = security_posture.SecurityPostureScanner()
    login_data = {
        "failed_by_ip": {"203.0.113.10": 8},
        "success_by_ip": {"203.0.113.10": 1},
        "failed_events": [],
        "success_events": [],
    }

    findings = scanner._collect_intrusion_findings(login_data, [])

    assert any(item["type"] == "login_correlation" for item in findings)
    correlated = next(item for item in findings if item["type"] == "login_correlation")
    assert correlated["severity"] == "critical"
    assert correlated["ip"] == "203.0.113.10"


def test_attack_risk_contains_approval_remediation_action() -> None:
    scanner = security_posture.SecurityPostureScanner()
    attack_sources = [{
        "ip": "203.0.113.20",
        "severity": "high",
        "attack_types": ["SSH 爆破/失败登录"],
        "failed_logins": 30,
        "successful_logins": 0,
        "web_scan_hits": 0,
        "total_events": 30,
        "recommendation": "建议调查",
    }]
    login_data = {
        "failed_by_ip": {"203.0.113.20": 30},
        "success_by_ip": {},
        "failed_events": [],
        "success_events": [],
    }

    scanner._add_attack_risks(attack_sources, login_data)

    assert scanner.risks
    actions = scanner.risks[0]["remediation_actions"]
    assert any(action["risk"] == "read" for action in actions)
    assert any(action["requires_approval"] for action in actions)
    assert any("203.0.113.20" in action["prompt"] for action in actions)


def test_security_remediation_port_prompt_compiles_to_block_port() -> None:
    tool_call = graph._compile_deterministic_tool_call(
        "请关闭暴露端口 22/tcp，执行前检查当前监听进程和防火墙规则，说明影响范围，并通过正式审批后再执行。"
    )

    assert tool_call is not None
    assert tool_call["name"] == "block_port"
    assert tool_call["arguments"] == {"port": 22, "protocol": "tcp"}


if __name__ == "__main__":
    test_security_posture_scan_shape_and_persistence()
    test_login_correlation_becomes_intrusion_finding()
    test_attack_risk_contains_approval_remediation_action()
    test_security_remediation_port_prompt_compiles_to_block_port()
    print("security posture regression OK")
