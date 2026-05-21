"""REST API routes for OpsGuard."""

from fastapi import APIRouter

from app.api import (
    alerts,
    backups,
    health_report,
    health_report_pdf,
    incidents,
    knowledge,
    multimodal,
    ops_report,
    ops_report_pdf,
    runbook,
    security_demo,
    sessions,
    system,
    tools,
    topology,
)

router = APIRouter()

router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
router.include_router(system.router, prefix="/system", tags=["system"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
router.include_router(health_report.router, prefix="/health-report", tags=["health-report"])
router.include_router(health_report_pdf.router, prefix="/health-report", tags=["health-report"])
router.include_router(topology.router, prefix="/topology", tags=["topology"])
router.include_router(security_demo.router, prefix="/security", tags=["security-demo"])
router.include_router(tools.router, prefix="/tools", tags=["tools"])
router.include_router(runbook.router, prefix="/runbooks", tags=["runbooks"])
router.include_router(backups.router, prefix="/backups", tags=["backups"])
router.include_router(incidents.router, prefix="/incidents", tags=["incidents"])
router.include_router(ops_report.router, prefix="/ops-report", tags=["ops-report"])
router.include_router(ops_report_pdf.router, prefix="/ops-report", tags=["ops-report"])
router.include_router(multimodal.router, prefix="/multimodal", tags=["multimodal"])
