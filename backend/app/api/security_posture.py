"""Security posture API.

Read-only host security scanning for the Security Posture page. The scanner is
intentionally conservative: it reports evidence and uncertainty, never performs
mitigation actions, and degrades to partial results when a local data source is
unavailable.
"""

from __future__ import annotations

import json
import os
import platform
import re
import stat
import subprocess
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
import psutil
from fastapi import APIRouter, HTTPException

from app.database import get_knowledge_db_path

router = APIRouter()

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
FAILED_LOGIN_RE = re.compile(
    r"(?P<timestamp>^\S+\s+\d+\s+\d+:\d+:\d+|^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}).*"
    r"(?:Failed password|Invalid user|authentication failure).*?\bfrom\s+(?P<ip>(?:\d{1,3}\.){3}\d{1,3})",
    re.IGNORECASE,
)
ACCEPTED_LOGIN_RE = re.compile(
    r"(?P<timestamp>^\S+\s+\d+\s+\d+:\d+:\d+|^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}).*"
    r"Accepted\s+(?:password|publickey|keyboard-interactive).*?\bfrom\s+(?P<ip>(?:\d{1,3}\.){3}\d{1,3})",
    re.IGNORECASE,
)
WEB_SCAN_PATH_RE = re.compile(
    r"(/\.env|/wp-admin|/wp-login|/phpmyadmin|/cgi-bin|/manager/html|/admin|/shell|/boaform)",
    re.IGNORECASE,
)
SUSPICIOUS_COMMAND_RE = re.compile(
    r"(curl\s+.*\|\s*(?:bash|sh)|wget\s+.*\|\s*(?:bash|sh)|/dev/tcp|bash\s+-i|nc\s+-e|"
    r"base64\s+-d|chmod\s+\+x|xmrig|kinsing|mirai|nohup\s+.*(?:curl|wget)|python\s+-c\s+.*socket)",
    re.IGNORECASE,
)
SUSPICIOUS_SERVICE_RE = re.compile(
    r"(curl|wget|/tmp/|/var/tmp/|/dev/shm|base64|nc\s+-e|bash\s+-i|xmrig|kinsing|mirai)",
    re.IGNORECASE,
)

DANGEROUS_PORTS: dict[int, str] = {
    22: "SSH 远程登录",
    2375: "Docker API 明文端口",
    3306: "MySQL 数据库",
    5432: "PostgreSQL 数据库",
    6379: "Redis 数据库",
    9200: "Elasticsearch",
    9300: "Elasticsearch 集群通信",
    11211: "Memcached",
    27017: "MongoDB",
    6443: "Kubernetes API",
}


@router.get("/scan")
async def generate_security_posture_scan() -> dict[str, Any]:
    """Run a read-only security posture scan and persist the result."""
    scanner = SecurityPostureScanner()
    report = scanner.run()
    await _save_security_posture_scan(report)
    return report


@router.get("/latest")
async def get_latest_security_posture_scan() -> dict[str, Any]:
    """Return the most recent saved security posture scan."""
    report = await _load_latest_security_posture_scan()
    if report is None:
        raise HTTPException(status_code=404, detail="暂无安全态势扫描结果")
    return report


async def ensure_security_posture_schema(db: aiosqlite.Connection) -> None:
    """Create storage for security posture scan snapshots."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS security_posture_scans (
            id TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            security_score INTEGER NOT NULL,
            risk_level TEXT NOT NULL,
            hostname TEXT,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_security_posture_generated_at ON security_posture_scans(generated_at)"
    )


class SecurityPostureScanner:
    """Small, local, read-only host security scanner."""

    def __init__(self) -> None:
        self.generated_at = datetime.now().isoformat()
        self.risks: list[dict[str, Any]] = []
        self.timeline: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.data_sources: list[str] = []

    def run(self) -> dict[str, Any]:
        login_data = self._collect_login_activity()
        exposed_services = self._collect_exposed_services()
        baseline_checks = self._collect_baseline_checks()
        persistence = self._collect_suspicious_persistence()
        suspicious_files = self._collect_suspicious_files()
        suspicious_processes = self._collect_suspicious_processes()
        intrusion_findings = self._collect_intrusion_findings(login_data, suspicious_processes)
        web_sources = self._collect_web_scan_sources()

        attack_sources = self._build_attack_sources(login_data, web_sources)
        self._add_attack_risks(attack_sources, login_data)
        self._add_exposure_risks(exposed_services)
        self._add_baseline_risks(baseline_checks)
        self._add_collection_risks("intrusion", "入侵迹象", intrusion_findings)
        self._add_collection_risks("persistence", "可疑持久化", persistence)
        self._add_collection_risks("suspicious_process", "可疑进程", suspicious_processes)
        self._add_collection_risks("suspicious_file", "可疑文件", suspicious_files)

        risk_counts = Counter(item["severity"] for item in self.risks)
        score = self._score()
        risk_level = self._risk_level(score, risk_counts)
        metrics = {
            "critical": risk_counts.get("critical", 0),
            "high": risk_counts.get("high", 0),
            "medium": risk_counts.get("medium", 0),
            "low": risk_counts.get("low", 0),
            "attack_ips": len(attack_sources),
            "exposed_ports": len(exposed_services),
            "intrusion_findings": len(intrusion_findings),
            "suspicious_persistence": len(persistence),
            "suspicious_processes": len(suspicious_processes),
            "suspicious_files": len(suspicious_files),
            "baseline_failed": len([item for item in baseline_checks if item["status"] == "failed"]),
        }

        return {
            "scan_id": f"secscan_{uuid.uuid4().hex[:12]}",
            "generated_at": self.generated_at,
            "hostname": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "security_score": score,
            "risk_level": risk_level,
            "summary": self._summary(score, metrics),
            "metrics": metrics,
            "risks": self.risks,
            "attack_sources": attack_sources,
            "exposed_services": exposed_services,
            "baseline_checks": baseline_checks,
            "intrusion_findings": intrusion_findings,
            "suspicious_persistence": persistence,
            "suspicious_processes": suspicious_processes,
            "suspicious_files": suspicious_files,
            "timeline": sorted(self.timeline, key=lambda item: item.get("timestamp", ""), reverse=True)[:80],
            "data_sources": sorted(set(self.data_sources)),
            "errors": self.errors,
            "scan_status": "partial" if self.errors else "success",
        }

    def _collect_login_activity(self) -> dict[str, Any]:
        lines = self._auth_log_lines()
        failed_by_ip: Counter[str] = Counter()
        success_by_ip: Counter[str] = Counter()
        failed_events: list[dict[str, Any]] = []
        success_events: list[dict[str, Any]] = []

        for line in lines:
            failed = FAILED_LOGIN_RE.search(line)
            if failed:
                ip = failed.group("ip")
                failed_by_ip[ip] += 1
                event = {
                    "timestamp": failed.group("timestamp"),
                    "type": "failed_login",
                    "title": f"SSH 登录失败：{ip}",
                    "source": "auth_log",
                    "severity": "medium",
                    "detail": _compact(line, 240),
                }
                failed_events.append(event)
                continue
            accepted = ACCEPTED_LOGIN_RE.search(line)
            if accepted:
                ip = accepted.group("ip")
                success_by_ip[ip] += 1
                event = {
                    "timestamp": accepted.group("timestamp"),
                    "type": "accepted_login",
                    "title": f"SSH 登录成功：{ip}",
                    "source": "auth_log",
                    "severity": "info",
                    "detail": _compact(line, 240),
                }
                success_events.append(event)

        self.timeline.extend((failed_events + success_events)[-40:])
        return {
            "failed_by_ip": failed_by_ip,
            "success_by_ip": success_by_ip,
            "failed_events": failed_events,
            "success_events": success_events,
        }

    def _auth_log_lines(self) -> list[str]:
        commands = [
            ["journalctl", "--no-pager", "--since", "24h ago", "-u", "sshd", "-u", "ssh", "-n", "500"],
            ["journalctl", "--no-pager", "--since", "24h ago", "-g", "Failed password|Invalid user|Accepted", "-n", "500"],
        ]
        for cmd in commands:
            result = _run_command(cmd, timeout=8)
            if result["ok"] and result["stdout"].strip():
                self.data_sources.append("journalctl:ssh")
                return result["stdout"].splitlines()

        for path in ("/var/log/auth.log", "/var/log/secure"):
            log_path = Path(path)
            if log_path.exists() and log_path.is_file():
                try:
                    self.data_sources.append(path)
                    return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
                except OSError as exc:
                    self.errors.append(f"读取 {path} 失败: {exc}")

        self.errors.append("未能读取 SSH 登录日志，攻击来源统计可能不完整")
        return []

    def _collect_web_scan_sources(self) -> dict[str, Counter[str]]:
        sources: dict[str, Counter[str]] = defaultdict(Counter)
        for path in ("/var/log/nginx/access.log", "/var/log/httpd/access_log", "/var/log/apache2/access.log"):
            log_path = Path(path)
            if not log_path.exists() or not log_path.is_file():
                continue
            try:
                self.data_sources.append(path)
                for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]:
                    if not WEB_SCAN_PATH_RE.search(line):
                        continue
                    ip_match = IP_RE.search(line)
                    if not ip_match:
                        continue
                    ip = ip_match.group(0)
                    sources[ip]["web_scan"] += 1
                    self.timeline.append({
                        "timestamp": _first_token(line),
                        "type": "web_scan",
                        "title": f"Web 扫描请求：{ip}",
                        "source": path,
                        "severity": "medium",
                        "detail": _compact(line, 240),
                    })
            except OSError as exc:
                self.errors.append(f"读取 {path} 失败: {exc}")
        return sources

    def _build_attack_sources(
        self,
        login_data: dict[str, Any],
        web_sources: dict[str, Counter[str]],
    ) -> list[dict[str, Any]]:
        totals: dict[str, Counter[str]] = defaultdict(Counter)
        for ip, count in login_data["failed_by_ip"].items():
            totals[ip]["ssh_failed"] += count
        for ip, count in login_data["success_by_ip"].items():
            totals[ip]["ssh_success"] += count
        for ip, counter in web_sources.items():
            totals[ip].update(counter)

        rows = []
        for ip, counter in totals.items():
            failed = counter.get("ssh_failed", 0)
            web_scan = counter.get("web_scan", 0)
            success = counter.get("ssh_success", 0)
            total = sum(counter.values())
            severity = "high" if failed >= 20 or (failed >= 5 and success) else "medium" if failed >= 5 or web_scan >= 3 else "low"
            rows.append({
                "ip": ip,
                "severity": severity,
                "attack_types": _attack_type_labels(counter),
                "failed_logins": failed,
                "successful_logins": success,
                "web_scan_hits": web_scan,
                "total_events": total,
                "recommendation": "建议调查是否存在成功登录和持久化痕迹" if severity in {"high", "critical"} else "建议观察",
            })
        return sorted(rows, key=lambda item: (item["severity"] != "high", -item["total_events"]))[:20]

    def _collect_exposed_services(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[int, int | None, str], dict[str, Any]] = {}
        try:
            connections = psutil.net_connections(kind="inet")
            self.data_sources.append("psutil.net_connections")
        except (psutil.AccessDenied, OSError) as exc:
            self.errors.append(f"读取监听端口失败: {exc}")
            return []

        for conn in connections:
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            ip = conn.laddr.ip
            port = int(conn.laddr.port)
            process_name = ""
            if conn.pid:
                try:
                    process_name = psutil.Process(conn.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    process_name = ""
            exposed_all = ip in {"0.0.0.0", "::", "*"}
            risk = "high" if exposed_all and port in DANGEROUS_PORTS else "medium" if exposed_all else "low"
            key = (port, conn.pid, process_name or "unknown")
            existing = grouped.get(key)
            if existing:
                addresses = set(str(existing["listen_address"]).split(", "))
                addresses.add(ip)
                existing["listen_address"] = ", ".join(sorted(addresses))
                if risk == "high" or (risk == "medium" and existing["risk"] == "low"):
                    existing["risk"] = risk
                    existing["reason"] = (
                        f"{DANGEROUS_PORTS[port]} 监听在所有地址"
                        if exposed_all and port in DANGEROUS_PORTS
                        else "监听在所有地址" if exposed_all else existing["reason"]
                    )
                continue
            grouped[key] = {
                "port": port,
                "protocol": "tcp",
                "listen_address": ip,
                "pid": conn.pid,
                "process": process_name or "unknown",
                "service": DANGEROUS_PORTS.get(port, ""),
                "risk": risk,
                "reason": (
                    f"{DANGEROUS_PORTS[port]} 监听在所有地址"
                    if exposed_all and port in DANGEROUS_PORTS
                    else "监听在所有地址" if exposed_all else "仅本地或指定地址监听"
                ),
            }
        services = list(grouped.values())
        return sorted(services, key=lambda item: ({"high": 0, "medium": 1, "low": 2}[item["risk"]], item["port"]))

    def _collect_baseline_checks(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        checks.extend(self._ssh_baseline())
        checks.extend(self._firewall_baseline())
        checks.extend(self._account_baseline())
        checks.extend(self._file_permission_baseline())
        return checks

    def _ssh_baseline(self) -> list[dict[str, Any]]:
        config_text, sources = _read_ssh_config()
        if sources:
            self.data_sources.extend(sources)
        if not config_text:
            return [{
                "id": "ssh_config_readable",
                "title": "SSH 配置可读性",
                "category": "baseline",
                "status": "unknown",
                "severity": "low",
                "evidence": "未找到或无法读取 sshd_config",
                "recommendation": "确认 OpenSSH Server 是否安装以及配置路径",
            }]

        root_login = _last_sshd_value(config_text, "PermitRootLogin")
        password_auth = _last_sshd_value(config_text, "PasswordAuthentication")
        return [
            {
                "id": "ssh_permit_root_login",
                "title": "SSH root 登录策略",
                "category": "baseline",
                "status": "failed" if root_login and root_login.lower() in {"yes", "without-password", "prohibit-password"} else "passed",
                "severity": "medium",
                "evidence": f"PermitRootLogin={root_login or '未显式配置'}",
                "recommendation": "生产环境建议禁用 root 直接登录，改用普通用户 + sudo",
            },
            {
                "id": "ssh_password_auth",
                "title": "SSH 密码登录策略",
                "category": "baseline",
                "status": "warning" if (not password_auth or password_auth.lower() == "yes") else "passed",
                "severity": "low",
                "evidence": f"PasswordAuthentication={password_auth or '未显式配置'}",
                "recommendation": "高安全环境建议使用密钥登录并关闭密码登录",
            },
        ]

    def _firewall_baseline(self) -> list[dict[str, Any]]:
        commands = [
            ("firewalld", ["firewall-cmd", "--state"]),
            ("ufw", ["ufw", "status"]),
            ("iptables", ["iptables", "-L", "-n"]),
        ]
        for name, cmd in commands:
            if not _command_exists(cmd[0]):
                continue
            result = _run_command(cmd, timeout=5)
            self.data_sources.append(f"firewall:{name}")
            if result["ok"]:
                output = result["stdout"].strip()
                inactive = "not running" in output.lower() or "inactive" in output.lower()
                return [{
                    "id": "firewall_status",
                    "title": "防火墙状态",
                    "category": "baseline",
                    "status": "failed" if inactive else "passed",
                    "severity": "medium",
                    "evidence": _compact(output or "命令执行成功", 240),
                    "recommendation": "确认防火墙策略符合当前主机暴露面要求",
                }]
            self.errors.append(f"读取 {name} 防火墙状态失败: {result['stderr'] or result['stdout']}")
        return [{
            "id": "firewall_status",
            "title": "防火墙状态",
            "category": "baseline",
            "status": "unknown",
            "severity": "medium",
            "evidence": "未检测到可用的 firewalld/ufw/iptables 命令或权限不足",
            "recommendation": "确认主机防火墙组件和最小读取权限",
        }]

    def _account_baseline(self) -> list[dict[str, Any]]:
        users = _read_passwd_users()
        self.data_sources.append("/etc/passwd")
        uid0_users = [user["username"] for user in users if user["uid"] == 0 and user["username"] != "root"]
        human_users = [user for user in users if user["uid"] >= 1000 or user["username"] == "root"]
        checks = [
            {
                "id": "uid0_users",
                "title": "UID 0 异常用户",
                "category": "account",
                "status": "failed" if uid0_users else "passed",
                "severity": "critical",
                "evidence": ", ".join(uid0_users) if uid0_users else "未发现 root 之外的 UID 0 用户",
                "recommendation": "若存在 UID 0 异常用户，应立即调查来源并锁定账号",
            },
            {
                "id": "human_users",
                "title": "可登录用户数量",
                "category": "account",
                "status": "warning" if len(human_users) > 8 else "passed",
                "severity": "low",
                "evidence": f"发现 {len(human_users)} 个 root/UID>=1000 用户",
                "recommendation": "定期复核可登录用户和权限",
            },
        ]
        sudo_members = _sudo_group_members()
        if sudo_members:
            self.data_sources.append("/etc/group")
            checks.append({
                "id": "sudo_group_members",
                "title": "sudo/wheel 成员",
                "category": "account",
                "status": "warning" if len(sudo_members) > 5 else "passed",
                "severity": "low",
                "evidence": ", ".join(sudo_members),
                "recommendation": "确认 sudo/wheel 成员均为授权运维账号",
            })
        return checks

    def _file_permission_baseline(self) -> list[dict[str, Any]]:
        checks = []
        expected = {
            "/etc/passwd": {0o644},
            "/etc/shadow": {0o000, 0o400, 0o600, 0o640},
            "/etc/sudoers": {0o440},
        }
        for path, allowed_modes in expected.items():
            target = Path(path)
            if not target.exists():
                checks.append({
                    "id": f"perm_{target.name}",
                    "title": f"{path} 权限",
                    "category": "baseline",
                    "status": "unknown",
                    "severity": "medium",
                    "evidence": "文件不存在",
                    "recommendation": "确认系统关键文件是否异常缺失",
                })
                continue
            mode = stat.S_IMODE(target.stat().st_mode)
            checks.append({
                "id": f"perm_{target.name}",
                "title": f"{path} 权限",
                "category": "baseline",
                "status": "passed" if mode in allowed_modes else "failed",
                "severity": "high" if path != "/etc/passwd" else "medium",
                "evidence": oct(mode),
                "recommendation": "关键账号文件权限异常时应立即复核",
            })
        self.data_sources.extend(expected.keys())
        return checks

    def _collect_suspicious_persistence(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        cron_files = [Path("/etc/crontab")]
        cron_dir = Path("/etc/cron.d")
        if cron_dir.exists():
            cron_files.extend(path for path in cron_dir.iterdir() if path.is_file())
        for path in cron_files:
            if not path.exists() or not path.is_file():
                continue
            try:
                self.data_sources.append(str(path))
                for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                    clean = line.strip()
                    if not clean or clean.startswith("#") or not SUSPICIOUS_COMMAND_RE.search(clean):
                        continue
                    findings.append({
                        "id": f"cron_{len(findings) + 1}",
                        "type": "cron",
                        "severity": "high",
                        "path": str(path),
                        "line": idx,
                        "summary": "cron 中发现下载执行或反弹 shell 特征",
                        "evidence": _compact(clean, 260),
                        "recommendation": "建议调查该定时任务来源，确认后再考虑禁用或删除",
                    })
            except OSError as exc:
                self.errors.append(f"读取 {path} 失败: {exc}")
        findings.extend(self._collect_suspicious_systemd_units())
        findings.extend(self._collect_authorized_keys_findings())
        return findings[:20]

    def _collect_suspicious_systemd_units(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        roots = [Path("/etc/systemd/system"), Path("/usr/lib/systemd/system"), Path("/lib/systemd/system")]
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            self.data_sources.append(str(root))
            scanned = 0
            try:
                for path in root.glob("*.service"):
                    scanned += 1
                    if scanned > 300:
                        break
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    matched_lines = [
                        _compact(line.strip(), 220)
                        for line in text.splitlines()
                        if SUSPICIOUS_SERVICE_RE.search(line)
                    ]
                    if not matched_lines:
                        continue
                    findings.append({
                        "id": f"systemd_{len(findings) + 1}",
                        "type": "systemd",
                        "severity": "high",
                        "path": str(path),
                        "summary": "systemd 服务中发现下载执行、临时目录执行或反弹 shell 特征",
                        "evidence": " | ".join(matched_lines[:3]),
                        "recommendation": "建议确认该服务来源、启用状态和最近变更，确认后再考虑禁用",
                    })
            except OSError as exc:
                self.errors.append(f"扫描 {root} 失败: {exc}")
        return findings[:20]

    def _collect_authorized_keys_findings(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        users = _read_passwd_users()
        for user in users:
            home = user.get("home") or ""
            if not home or home in {"/", "/nonexistent"}:
                continue
            key_path = Path(home) / ".ssh" / "authorized_keys"
            if not key_path.exists() or not key_path.is_file():
                continue
            try:
                st = key_path.stat()
                lines = [line.strip() for line in key_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.startswith("#")]
                self.data_sources.append(str(key_path))
            except OSError as exc:
                self.errors.append(f"读取 {key_path} 失败: {exc}")
                continue
            if not lines:
                continue
            mode = stat.S_IMODE(st.st_mode)
            severity = "medium"
            summary = f"{user['username']} 存在 {len(lines)} 条 SSH authorized_keys"
            if mode & (stat.S_IWGRP | stat.S_IWOTH):
                severity = "high"
                summary += "，且文件可被组或其他用户写入"
            findings.append({
                "id": f"authorized_keys_{user['username']}",
                "type": "authorized_keys",
                "severity": severity,
                "path": str(key_path),
                "summary": summary,
                "evidence": f"mode={oct(mode)}, keys={len(lines)}",
                "recommendation": "建议复核 SSH 公钥来源，删除未知公钥需走审批和备份",
            })
        return findings[:20]

    def _collect_suspicious_processes(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        self.data_sources.append("psutil.process_iter")
        for proc in psutil.process_iter(["pid", "ppid", "name", "username", "cmdline", "exe", "create_time"]):
            try:
                info = proc.info
                cmdline = " ".join(info.get("cmdline") or [])
                exe = info.get("exe") or ""
                name = info.get("name") or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            evidence = cmdline or exe or name
            if not evidence:
                continue
            executable_from_tmp = exe.startswith(("/tmp/", "/var/tmp/", "/dev/shm/"))
            suspicious_name_path = name in {"kworker", "sshd", "systemd", "init"} and exe.startswith(("/tmp/", "/var/tmp/", "/dev/shm/", "/home/"))
            suspicious_command = bool(SUSPICIOUS_COMMAND_RE.search(evidence))
            if not (executable_from_tmp or suspicious_name_path or suspicious_command):
                continue
            severity = "high" if executable_from_tmp or suspicious_command else "medium"
            finding = {
                "id": f"process_{info.get('pid')}",
                "type": "process",
                "severity": severity,
                "pid": info.get("pid"),
                "ppid": info.get("ppid"),
                "name": name,
                "user": info.get("username") or "",
                "path": exe,
                "summary": "发现可疑进程命令行或临时目录可执行进程",
                "evidence": _compact(evidence, 300),
                "recommendation": "建议查看进程详情、网络连接和启动来源；终止进程必须审批",
            }
            findings.append(finding)
            self.timeline.append({
                "timestamp": datetime.fromtimestamp(info.get("create_time") or 0).isoformat() if info.get("create_time") else self.generated_at,
                "type": "suspicious_process",
                "title": f"可疑进程：{name or info.get('pid')}",
                "source": "psutil.process_iter",
                "severity": severity,
                "detail": finding["evidence"],
            })
            if len(findings) >= 30:
                break
        return findings

    def _collect_intrusion_findings(
        self,
        login_data: dict[str, Any],
        suspicious_processes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []

        for ip, failed_count in login_data["failed_by_ip"].items():
            success_count = login_data["success_by_ip"].get(ip, 0)
            if failed_count >= 5 and success_count:
                findings.append({
                    "id": f"login_correlation_{ip}",
                    "type": "login_correlation",
                    "severity": "critical",
                    "summary": f"{ip} 在多次失败登录后出现成功登录",
                    "evidence": f"failed={failed_count}, success={success_count}",
                    "ip": ip,
                    "recommendation": "建议立即检查该来源登录后的 sudo、进程、cron 和 authorized_keys 变化",
                })
            elif failed_count >= 20:
                findings.append({
                    "id": f"ssh_bruteforce_{ip}",
                    "type": "ssh_bruteforce",
                    "severity": "high",
                    "summary": f"{ip} 存在 SSH 高频失败登录",
                    "evidence": f"failed={failed_count}",
                    "ip": ip,
                    "recommendation": "建议确认是否为合法运维来源；确认恶意后可审批封禁",
                })

        users = _read_passwd_users()
        uid0_users = [user["username"] for user in users if user["uid"] == 0 and user["username"] != "root"]
        for username in uid0_users:
            findings.append({
                "id": f"uid0_{username}",
                "type": "uid0_user",
                "severity": "critical",
                "summary": f"发现 root 之外的 UID 0 用户：{username}",
                "evidence": username,
                "user": username,
                "recommendation": "建议立即调查账号来源，锁定或删除账号必须审批",
            })

        for proc in suspicious_processes[:10]:
            findings.append({
                "id": f"intrusion_{proc['id']}",
                "type": "suspicious_process",
                "severity": proc.get("severity", "medium"),
                "summary": proc.get("summary", "发现可疑进程"),
                "evidence": proc.get("evidence", ""),
                "pid": proc.get("pid"),
                "path": proc.get("path", ""),
                "recommendation": proc.get("recommendation", "建议进一步调查"),
            })

        return findings[:30]

    def _collect_suspicious_files(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for root in (Path("/tmp"), Path("/var/tmp"), Path("/dev/shm")):
            if not root.exists() or not root.is_dir():
                continue
            self.data_sources.append(str(root))
            scanned = 0
            try:
                for current_root, dirnames, filenames in os.walk(root):
                    dirnames[:] = dirnames[:20]
                    for filename in filenames:
                        scanned += 1
                        if scanned > 500:
                            break
                        path = Path(current_root) / filename
                        try:
                            st = path.stat()
                        except OSError:
                            continue
                        executable = bool(st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
                        suspicious_name = filename in {"kworker", "sshd", "systemd", "init", "bash", "sh"} or filename.startswith(".")
                        if executable or suspicious_name:
                            findings.append({
                                "id": f"file_{len(findings) + 1}",
                                "severity": "medium" if executable else "low",
                                "path": str(path),
                                "size": st.st_size,
                                "mode": oct(stat.S_IMODE(st.st_mode)),
                                "summary": "临时目录中发现可执行或隐藏可疑文件",
                                "recommendation": "建议先查看文件内容、哈希和关联进程，确认后再处置",
                            })
                    if scanned > 500 or len(findings) >= 30:
                        break
            except OSError as exc:
                self.errors.append(f"扫描 {root} 失败: {exc}")
        return findings[:30]

    def _add_attack_risks(self, attack_sources: list[dict[str, Any]], login_data: dict[str, Any]) -> None:
        successful_ips = set(login_data["success_by_ip"].keys())
        for source in attack_sources:
            if source["severity"] not in {"high", "medium"}:
                continue
            if source["ip"] in successful_ips and source["failed_logins"] >= 5:
                severity = "critical"
                title = f"疑似爆破后成功登录：{source['ip']}"
                score = -30
            else:
                severity = source["severity"]
                title = f"异常攻击来源：{source['ip']}"
                score = -15 if severity == "high" else -8
            self.risks.append(_risk(
                title=title,
                category="attack_source",
                severity=severity,
                score_impact=score,
                summary=f"{source['ip']} 触发 {source['total_events']} 次安全相关事件",
                evidence=[{"source": "auth/web logs", "observed": source, "execution_state": "executed"}],
                entities={"ips": [source["ip"]]},
                recommendations=["让 Agent 调查该 IP 是否存在成功登录和持久化迹象", "如确认恶意，可走审批封禁来源 IP"],
                remediation_actions=[
                    _remediation_action(
                        action_type="investigate",
                        label="调查来源 IP",
                        prompt=f"请调查安全风险：来源 IP {source['ip']} 触发 {source['total_events']} 次安全事件，检查是否有成功登录、sudo、可疑进程、cron、authorized_keys 和外连迹象。",
                        risk="read",
                    ),
                    _remediation_action(
                        action_type="block_ip",
                        label="审批封禁 IP",
                        prompt=f"请封禁可疑来源 IP {source['ip']}，执行前说明影响范围、当前连接、回滚方式，并通过正式审批后再修改防火墙。",
                        risk="write",
                        tool_name="block_port",
                        target=source["ip"],
                    ),
                ],
            ))

    def _add_exposure_risks(self, services: list[dict[str, Any]]) -> None:
        for service in services:
            if service["risk"] == "low":
                continue
            severity = "high" if service["risk"] == "high" else "medium"
            self.risks.append(_risk(
                title=f"暴露服务端口：{service['port']}",
                category="exposure",
                severity=severity,
                score_impact=-12 if severity == "high" else -6,
                summary=service["reason"],
                evidence=[{"source": "psutil.net_connections", "observed": service, "execution_state": "executed"}],
                entities={"ports": [service["port"]], "processes": [service["process"]]},
                recommendations=["确认该端口是否需要对外暴露", "如不需要，可走审批调整防火墙或服务监听地址"],
                remediation_actions=[
                    _remediation_action(
                        action_type="investigate",
                        label="调查暴露服务",
                        prompt=f"请调查暴露端口 {service['port']}（进程 {service['process']}，监听 {service['listen_address']}）的用途、关联服务、访问风险和是否需要对外开放。",
                        risk="read",
                    ),
                    _remediation_action(
                        action_type="close_port",
                        label="审批关闭端口",
                        prompt=f"请关闭暴露端口 {service['port']}/{service['protocol']}，执行前检查当前监听进程和防火墙规则，说明影响范围，并通过正式审批后再执行。",
                        risk="write",
                        tool_name="block_port",
                        target=str(service["port"]),
                    ),
                ],
            ))

    def _add_baseline_risks(self, checks: list[dict[str, Any]]) -> None:
        for check in checks:
            if check["status"] not in {"failed", "warning"}:
                continue
            severity = check["severity"] if check["status"] == "failed" else "low"
            impact = {"critical": -30, "high": -18, "medium": -10, "low": -4}.get(severity, -3)
            self.risks.append(_risk(
                title=check["title"],
                category=check["category"],
                severity=severity,
                score_impact=impact,
                summary=check["evidence"],
                evidence=[{"source": check["id"], "observed": check, "execution_state": "executed"}],
                entities={},
                recommendations=[check["recommendation"]],
                remediation_actions=_baseline_remediation_actions(check),
            ))

    def _add_collection_risks(self, category: str, label: str, findings: list[dict[str, Any]]) -> None:
        for finding in findings:
            severity = finding.get("severity", "medium")
            self.risks.append(_risk(
                title=f"{label}: {finding.get('path', finding.get('type', 'unknown'))}",
                category=category,
                severity=severity,
                score_impact={"high": -15, "medium": -8, "low": -3}.get(severity, -5),
                summary=finding.get("summary", label),
                evidence=[{"source": category, "observed": finding, "execution_state": "executed"}],
                entities={"files": [finding["path"]]} if finding.get("path") else {},
                recommendations=[finding.get("recommendation", "建议进一步调查")],
                remediation_actions=_finding_remediation_actions(category, finding),
            ))

    def _score(self) -> int:
        category_caps = {
            "attack_source": -35,
            "exposure": -30,
            "intrusion": -40,
            "account": -30,
            "baseline": -25,
            "persistence": -25,
            "suspicious_process": -25,
            "suspicious_file": -15,
        }
        by_category: dict[str, int] = defaultdict(int)
        for item in self.risks:
            category = str(item.get("category") or "other")
            by_category[category] += int(item.get("score_impact", 0))

        penalty = 0
        for category, value in by_category.items():
            cap = category_caps.get(category, -20)
            penalty += max(value, cap)

        score = 100 + penalty
        return max(0, min(100, score))

    @staticmethod
    def _risk_level(score: int, counts: Counter[str]) -> str:
        if counts.get("critical", 0) or score < 50:
            return "critical"
        if counts.get("high", 0) or score < 75:
            return "warning"
        if counts.get("medium", 0) or score < 90:
            return "attention"
        return "healthy"

    @staticmethod
    def _summary(score: int, metrics: dict[str, Any]) -> str:
        if score >= 90:
            return "当前未发现明显高危安全风险，建议继续保持定期扫描。"
        if score >= 75:
            return "当前存在需要关注的安全风险，建议优先复核攻击来源和暴露面。"
        if score >= 50:
            return "当前存在较明显安全风险，建议尽快调查高危项并制定处置计划。"
        return "当前安全风险较高，建议立即开展入侵排查和暴露面收敛。"


async def _save_security_posture_scan(report: dict[str, Any]) -> None:
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_security_posture_schema(db)
        now = datetime.now().isoformat()
        await db.execute(
            """
            INSERT INTO security_posture_scans
                (id, generated_at, security_score, risk_level, hostname, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                report["generated_at"],
                int(report.get("security_score") or 0),
                report.get("risk_level") or "unknown",
                report.get("hostname") or "",
                json.dumps(report, ensure_ascii=False),
                now,
            ),
        )
        await db.commit()


async def _load_latest_security_posture_scan() -> dict[str, Any] | None:
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_security_posture_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT payload FROM security_posture_scans
            ORDER BY generated_at DESC, created_at DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return json.loads(row["payload"])


def _run_command(cmd: list[str], timeout: int = 8) -> dict[str, Any]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "returncode": result.returncode,
        }
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc), "returncode": -1}


def _command_exists(command: str) -> bool:
    return _run_command(["which", command], timeout=3)["ok"]


def _risk(
    *,
    title: str,
    category: str,
    severity: str,
    score_impact: int,
    summary: str,
    evidence: list[dict[str, Any]],
    entities: dict[str, Any],
    recommendations: list[str],
    remediation_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"risk_{uuid.uuid4().hex[:10]}",
        "title": title,
        "category": category,
        "severity": severity,
        "status": "open",
        "score_impact": score_impact,
        "summary": summary,
        "evidence": evidence,
        "entities": entities,
        "recommendations": recommendations,
        "remediation_actions": remediation_actions or [],
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }


def _remediation_action(
    *,
    action_type: str,
    label: str,
    prompt: str,
    risk: str,
    tool_name: str = "",
    target: str = "",
) -> dict[str, Any]:
    return {
        "type": action_type,
        "label": label,
        "prompt": prompt,
        "risk": risk,
        "tool_name": tool_name,
        "target": target,
        "requires_approval": risk in {"write", "destructive"},
    }


def _baseline_remediation_actions(check: dict[str, Any]) -> list[dict[str, Any]]:
    check_id = str(check.get("id") or "")
    title = str(check.get("title") or "安全基线")
    evidence = str(check.get("evidence") or "")
    actions = [
        _remediation_action(
            action_type="investigate",
            label="调查基线风险",
            prompt=f"请调查安全基线风险：{title}，证据：{evidence}。请重新读取当前配置并给出修复建议，涉及修改必须走审批。",
            risk="read",
        )
    ]
    if check_id == "ssh_permit_root_login":
        actions.append(_remediation_action(
            action_type="harden_ssh",
            label="审批加固 SSH",
            prompt="请将 SSH 配置加固为禁止 root 直接登录。执行前读取当前 sshd_config、说明影响和回滚方式，并通过正式审批后再修改配置和验证 sshd 语法。",
            risk="write",
            tool_name="write_file",
            target="/etc/ssh/sshd_config",
        ))
    elif check_id == "ssh_password_auth":
        actions.append(_remediation_action(
            action_type="harden_ssh",
            label="审批关闭密码登录",
            prompt="请评估关闭 SSH 密码登录的风险。若确认可执行，请通过正式审批修改 sshd_config，并验证配置语法，避免锁死当前连接。",
            risk="write",
            tool_name="write_file",
            target="/etc/ssh/sshd_config",
        ))
    elif check_id.startswith("uid0_") or check_id == "uid0_users":
        actions.append(_remediation_action(
            action_type="lock_user",
            label="审批锁定异常用户",
            prompt=f"请调查 UID 0 异常用户风险：{evidence}。如确认异常，请通过正式审批锁定相关用户，并说明回滚方式。",
            risk="write",
            tool_name="lock_user",
            target=evidence,
        ))
    return actions


def _finding_remediation_actions(category: str, finding: dict[str, Any]) -> list[dict[str, Any]]:
    summary = str(finding.get("summary") or category)
    evidence = str(finding.get("evidence") or finding.get("path") or "")
    path = str(finding.get("path") or "")
    pid = finding.get("pid")
    actions = [
        _remediation_action(
            action_type="investigate",
            label="调查证据",
            prompt=f"请调查安全发现：{summary}。证据：{evidence}。请用只读工具确认当前状态、来源、影响和下一步处置建议。",
            risk="read",
            target=path or str(pid or ""),
        )
    ]
    if category in {"suspicious_file", "persistence"} and path:
        actions.append(_remediation_action(
            action_type="quarantine_file",
            label="审批隔离/删除",
            prompt=f"请处置可疑文件或持久化项 {path}。执行前先读取文件信息、检查关联进程和备份/回滚能力，确认后通过正式审批进行隔离或删除。",
            risk="write",
            tool_name="delete_file",
            target=path,
        ))
    if category in {"suspicious_process", "intrusion"} and pid:
        actions.append(_remediation_action(
            action_type="kill_process",
            label="审批终止进程",
            prompt=f"请处置可疑进程 PID {pid}。执行前先读取进程详情、网络连接和启动来源，确认影响后通过正式审批终止进程。",
            risk="write",
            tool_name="kill_process",
            target=str(pid),
        ))
    return actions


def _read_ssh_config() -> tuple[str, list[str]]:
    paths = [Path("/etc/ssh/sshd_config")]
    config_dir = Path("/etc/ssh/sshd_config.d")
    if config_dir.exists():
        paths.extend(sorted(path for path in config_dir.glob("*.conf") if path.is_file()))
    chunks = []
    sources = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            sources.append(str(path))
        except OSError:
            continue
    return "\n".join(chunks), sources


def _last_sshd_value(config_text: str, key: str) -> str:
    value = ""
    for line in config_text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        parts = clean.split()
        if len(parts) >= 2 and parts[0].lower() == key.lower():
            value = parts[1]
    return value


def _read_passwd_users() -> list[dict[str, Any]]:
    users = []
    try:
        for line in Path("/etc/passwd").read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split(":")
            if len(parts) < 7:
                continue
            users.append({
                "username": parts[0],
                "uid": int(parts[2]),
                "gid": int(parts[3]),
                "home": parts[5],
                "shell": parts[6],
            })
    except OSError:
        return []
    return users


def _sudo_group_members() -> list[str]:
    members: set[str] = set()
    try:
        for line in Path("/etc/group").read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split(":")
            if len(parts) < 4 or parts[0] not in {"sudo", "wheel"}:
                continue
            members.update(item for item in parts[3].split(",") if item)
    except OSError:
        return []
    return sorted(members)


def _attack_type_labels(counter: Counter[str]) -> list[str]:
    labels = []
    if counter.get("ssh_failed"):
        labels.append("SSH 爆破/失败登录")
    if counter.get("ssh_success"):
        labels.append("SSH 成功登录")
    if counter.get("web_scan"):
        labels.append("Web 扫描")
    return labels or ["异常来源"]


def _compact(text: str, max_chars: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _first_token(text: str) -> str:
    return str(text or "").split(" ", 1)[0]
