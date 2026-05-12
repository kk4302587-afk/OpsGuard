"""System status API endpoints."""

import platform
import psutil
from fastapi import APIRouter

router = APIRouter()


@router.get("/status")
async def get_system_status():
    """Get real-time system status overview."""
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load_avg = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0, 0, 0)

    return {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "cpu": {
            "percent": cpu_percent,
            "cores": psutil.cpu_count(),
        },
        "memory": {
            "total_gb": round(memory.total / (1024**3), 2),
            "used_gb": round(memory.used / (1024**3), 2),
            "percent": memory.percent,
        },
        "disk": {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "percent": round(disk.percent, 1),
        },
        "load_avg": {
            "1min": round(load_avg[0], 2),
            "5min": round(load_avg[1], 2),
            "15min": round(load_avg[2], 2),
        },
    }


@router.get("/processes")
async def get_top_processes():
    """Get top processes by CPU/memory usage."""
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
        try:
            info = proc.info
            if info["cpu_percent"] is not None and info["cpu_percent"] > 0.1:
                processes.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Sort by CPU usage, top 20
    processes.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
    return {"processes": processes[:20]}
