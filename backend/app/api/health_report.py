"""Health check report generation API."""

import json
import platform
from datetime import datetime
import uuid

import aiosqlite
import psutil
from fastapi import APIRouter, HTTPException

from app.database import get_knowledge_db_path

router = APIRouter()


@router.get("/report")
async def generate_health_report():
    """Generate a comprehensive system health report.

    Returns a structured report with metrics, issues, and recommendations.
    """
    report = {
        "generated_at": datetime.now().isoformat(),
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "sections": [],
    }

    # CPU Section
    cpu_percent = psutil.cpu_percent(interval=0.5, percpu=True)
    avg_cpu = sum(cpu_percent) / len(cpu_percent) if cpu_percent else 0
    cpu_section = {
        "title": "CPU 状态",
        "status": "critical" if avg_cpu > 90 else "warning" if avg_cpu > 70 else "healthy",
        "metrics": {
            "平均使用率": f"{avg_cpu:.1f}%",
            "核心数": psutil.cpu_count(),
            "各核心使用率": [f"{p:.1f}%" for p in cpu_percent],
        },
        "issues": [],
        "recommendations": [],
    }
    if avg_cpu > 90:
        cpu_section["issues"].append("CPU 使用率超过 90%，系统可能响应缓慢")
        cpu_section["recommendations"].append("检查高 CPU 进程，考虑优化或扩容")
    elif avg_cpu > 70:
        cpu_section["issues"].append("CPU 使用率较高，需要关注")
    report["sections"].append(cpu_section)

    # Memory Section
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    mem_section = {
        "title": "内存状态",
        "status": "critical" if mem.percent > 90 else "warning" if mem.percent > 80 else "healthy",
        "metrics": {
            "总内存": f"{mem.total / (1024**3):.1f} GB",
            "已使用": f"{mem.used / (1024**3):.1f} GB ({mem.percent}%)",
            "可用": f"{mem.available / (1024**3):.1f} GB",
            "Swap 使用": f"{swap.used / (1024**3):.1f} GB / {swap.total / (1024**3):.1f} GB",
        },
        "issues": [],
        "recommendations": [],
    }
    if mem.percent > 90:
        mem_section["issues"].append("内存使用率超过 90%，存在 OOM 风险")
        mem_section["recommendations"].append("排查内存泄漏进程，考虑增加内存或优化应用")
    if swap.percent > 50:
        mem_section["issues"].append(f"Swap 使用率 {swap.percent}%，系统性能可能受影响")
        mem_section["recommendations"].append("减少内存使用或增加物理内存")
    report["sections"].append(mem_section)

    # Disk Section
    disk_partitions = psutil.disk_partitions()
    disk_section = {
        "title": "磁盘状态",
        "status": "healthy",
        "metrics": {},
        "issues": [],
        "recommendations": [],
    }
    for partition in disk_partitions:
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            disk_section["metrics"][partition.mountpoint] = {
                "总容量": f"{usage.total / (1024**3):.1f} GB",
                "已使用": f"{usage.used / (1024**3):.1f} GB ({usage.percent}%)",
                "剩余": f"{usage.free / (1024**3):.1f} GB",
            }
            if usage.percent > 90:
                disk_section["status"] = "critical"
                disk_section["issues"].append(f"{partition.mountpoint} 使用率 {usage.percent}%，空间即将耗尽")
                disk_section["recommendations"].append(f"清理 {partition.mountpoint} 下的大文件或日志")
            elif usage.percent > 80:
                if disk_section["status"] == "healthy":
                    disk_section["status"] = "warning"
                disk_section["issues"].append(f"{partition.mountpoint} 使用率 {usage.percent}%，需要关注")
        except (PermissionError, OSError):
            continue
    report["sections"].append(disk_section)

    # Network Section
    net_io = psutil.net_io_counters()
    connections = psutil.net_connections()
    net_section = {
        "title": "网络状态",
        "status": "healthy",
        "metrics": {
            "发送": f"{net_io.bytes_sent / (1024**2):.1f} MB",
            "接收": f"{net_io.bytes_recv / (1024**2):.1f} MB",
            "活跃连接数": len([c for c in connections if c.status == 'ESTABLISHED']),
            "TIME_WAIT 连接": len([c for c in connections if c.status == 'TIME_WAIT']),
        },
        "issues": [],
        "recommendations": [],
    }
    time_wait_count = len([c for c in connections if c.status == 'TIME_WAIT'])
    if time_wait_count > 1000:
        net_section["status"] = "warning"
        net_section["issues"].append(f"TIME_WAIT 连接数 {time_wait_count}，可能存在连接泄漏")
        net_section["recommendations"].append("检查应用连接池配置，调整内核 TCP 参数")
    report["sections"].append(net_section)

    # Overall status
    statuses = [s["status"] for s in report["sections"]]
    if "critical" in statuses:
        report["overall_status"] = "critical"
    elif "warning" in statuses:
        report["overall_status"] = "warning"
    else:
        report["overall_status"] = "healthy"

    report["summary"] = _generate_summary(report)
    await _save_health_report(report)

    return report


@router.get("/latest")
async def get_latest_health_report():
    """Return the most recent saved health report."""
    report = await _load_latest_health_report()
    if report is None:
        raise HTTPException(status_code=404, detail="暂无历史巡检报告")
    return report


async def ensure_health_report_schema(db: aiosqlite.Connection) -> None:
    """Create storage for full health report snapshots."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS health_reports (
            id TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            overall_status TEXT NOT NULL,
            hostname TEXT,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_health_reports_generated_at ON health_reports(generated_at)"
    )


async def _save_health_report(report: dict) -> None:
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_health_report_schema(db)
        now = datetime.now().isoformat()
        await db.execute(
            """
            INSERT INTO health_reports
                (id, generated_at, overall_status, hostname, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                report["generated_at"],
                report.get("overall_status") or "unknown",
                report.get("hostname") or "",
                json.dumps(report, ensure_ascii=False),
                now,
            ),
        )
        await db.commit()


async def _load_latest_health_report() -> dict | None:
    async with aiosqlite.connect(get_knowledge_db_path()) as db:
        await ensure_health_report_schema(db)
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT payload FROM health_reports
            ORDER BY generated_at DESC, created_at DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return json.loads(row["payload"])


def _generate_summary(report: dict) -> str:
    """Generate a human-readable summary of the health report."""
    total_issues = sum(len(s["issues"]) for s in report["sections"])
    total_recommendations = sum(len(s["recommendations"]) for s in report["sections"])

    status_text = {
        "healthy": "系统运行正常",
        "warning": "系统存在潜在风险",
        "critical": "系统存在严重问题，需要立即处理",
    }

    summary = f"巡检时间: {report['generated_at']}\n"
    summary += f"整体状态: {status_text.get(report['overall_status'], '未知')}\n"
    summary += f"发现问题: {total_issues} 项\n"
    summary += f"优化建议: {total_recommendations} 条\n"

    return summary
