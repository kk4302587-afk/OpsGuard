"""Evaluate Incident Memory 2.0 retrieval quality on a small benchmark."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

import aiosqlite  # noqa: E402

from app.knowledge import store as store_module  # noqa: E402
from app.knowledge.store import ensure_knowledge_schema, knowledge_store  # noqa: E402


SCENARIOS = [
    {
        "query": "nginx 502 upstream refused",
        "expected": "nginx 502 upstream unavailable",
        "memory": {
            "problem_signature": "nginx 502 upstream unavailable",
            "diagnosis_path": "checked nginx logs and app-api service",
            "solution": "started app-api and verified HTTP 200",
            "incident_memory": {
                "symptoms": ["HTTP 502", "upstream connection refused"],
                "root_cause": "app-api inactive",
                "evidence": ["app-api inactive"],
                "evidence_refs": [{"type": "tool_call", "call_id": "call_nginx", "summary": "app-api inactive"}],
                "tool_call_ids": ["call_nginx"],
                "entities": {"services": ["nginx", "app-api"], "ports": [80, 8080]},
                "validation_method": "curl health endpoint returned 200",
                "applicability_conditions": ["nginx reverse proxy", "same upstream service"],
                "confidence": "high",
            },
        },
    },
    {
        "query": "disk space full /var/log",
        "expected": "disk pressure in /var/log",
        "memory": {
            "problem_signature": "disk pressure in /var/log",
            "diagnosis_path": "checked disk usage and large log files",
            "solution": "rotated logs and verified disk usage",
            "incident_memory": {
                "symptoms": ["disk usage high", "/var/log full"],
                "root_cause": "large application log files",
                "evidence": ["/var/log was 95% used"],
                "evidence_refs": [{"type": "tool_call", "call_id": "call_disk", "summary": "/var/log 95%"}],
                "tool_call_ids": ["call_disk"],
                "entities": {"paths": ["/var/log"]},
                "validation_method": "disk usage dropped below 80%",
                "applicability_conditions": ["same path", "large logs"],
                "confidence": "high",
            },
        },
    },
]


async def main() -> None:
    original_get_path = store_module.get_knowledge_db_path
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "knowledge.db")
        store_module.get_knowledge_db_path = lambda: db_path
        try:
            async with aiosqlite.connect(db_path) as db:
                await ensure_knowledge_schema(db)
                await db.commit()

            for scenario in SCENARIOS:
                memory = scenario["memory"]
                await knowledge_store.save_resolution(
                    problem_signature=memory["problem_signature"],
                    diagnosis_path=memory["diagnosis_path"],
                    solution=memory["solution"],
                    tools_used=["agent"],
                    incident_memory=memory["incident_memory"],
                )

            hits = 0
            fresh_check_hits = 0
            evidence_hits = 0
            for scenario in SCENARIOS:
                results = await knowledge_store.search(scenario["query"], limit=3)
                top_names = [item["problem_signature"] for item in results]
                if scenario["expected"] in top_names:
                    hits += 1
                best = results[0] if results else {}
                if best.get("recommended_fresh_checks"):
                    fresh_check_hits += 1
                if best.get("evidence_refs"):
                    evidence_hits += 1

            total = len(SCENARIOS)
            print(f"retrieval_precision_at_3={hits / total:.2f}")
            print(f"fresh_check_coverage={fresh_check_hits / total:.2f}")
            print(f"evidence_ref_coverage={evidence_hits / total:.2f}")
            if hits != total or fresh_check_hits != total or evidence_hits != total:
                raise SystemExit(1)
        finally:
            store_module.get_knowledge_db_path = original_get_path


if __name__ == "__main__":
    asyncio.run(main())
