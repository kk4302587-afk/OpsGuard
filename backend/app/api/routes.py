"""REST API routes for OpsGuard."""

from fastapi import APIRouter

from app.api import sessions, system, knowledge, health_report, topology, security_demo, health_report_pdf, tools, runbook

router = APIRouter()

router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
router.include_router(system.router, prefix="/system", tags=["system"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
router.include_router(health_report.router, prefix="/health-report", tags=["health-report"])
router.include_router(health_report_pdf.router, prefix="/health-report", tags=["health-report"])
router.include_router(topology.router, prefix="/topology", tags=["topology"])
router.include_router(security_demo.router, prefix="/security", tags=["security-demo"])
router.include_router(tools.router, prefix="/tools", tags=["tools"])
router.include_router(runbook.router, prefix="/runbooks", tags=["runbooks"])
