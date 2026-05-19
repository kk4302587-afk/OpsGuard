"""Regression checks for incident handoff and postmortem drafts."""

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.incidents import reports
from app.incidents import store as incident_store


async def _seed_incident(db_path: str, status: str = "resolved") -> str:
    incident_id = await incident_store.create_incident(
        session_id="draft-session",
        problem_statement="nginx is returning 502",
        source="test",
        db_path=db_path,
    )
    await incident_store.record_incident_event(
        incident_id=incident_id,
        session_id="draft-session",
        phase="execution",
        event_type="success",
        title="Checked nginx status",
        detail="nginx is inactive",
        evidence={
            "claim": "get_service_status executed against nginx",
            "evidence_type": "command",
            "source": "get_service_status",
            "observed": "inactive",
            "confidence": "high",
            "execution_state": "executed",
        },
        metadata={"tool_name": "get_service_status", "tool_args": {"service": "nginx"}},
        db_path=db_path,
    )
    await incident_store.record_incident_event(
        incident_id=incident_id,
        session_id="draft-session",
        phase="execution",
        event_type="failure",
        title="Checked service logs",
        detail="journalctl unavailable",
        evidence={
            "claim": "get_service_logs failed against nginx",
            "evidence_type": "log",
            "source": "get_service_logs",
            "observed": "journalctl unavailable",
            "confidence": "high",
            "execution_state": "failed",
            "failure_reason": "journalctl unavailable",
            "next_check": "Check journald permissions or service log path.",
        },
        metadata={"tool_name": "get_service_logs", "tool_args": {"service": "nginx"}},
        db_path=db_path,
    )
    await incident_store.record_incident_event(
        incident_id=incident_id,
        session_id="draft-session",
        phase="planning",
        event_type="start",
        title="LLM hypothesis",
        detail="Maybe a config change caused the issue",
        evidence={
            "claim": "Config change may be related",
            "evidence_type": "user input",
            "source": "LLM",
            "observed": "maybe config",
            "confidence": "medium",
            "execution_state": "inferred",
        },
        metadata={},
        db_path=db_path,
    )
    await incident_store.finalize_incident(
        incident_id=incident_id,
        final_summary="nginx diagnosis completed",
        status=status,
        db_path=db_path,
    )
    return incident_id


def test_handoff_and_postmortem_separate_facts_from_hypotheses() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            incident_id = await _seed_incident(db_path)
            handoff = await reports.generate_handoff_note(incident_id, db_path=db_path)
            postmortem = await reports.generate_postmortem_draft(incident_id, db_path=db_path)

        assert handoff is not None
        assert postmortem is not None
        handoff_md = handoff["markdown"]
        postmortem_md = postmortem["markdown"]
        assert "get_service_status [executed]" in handoff_md
        assert "journalctl unavailable" in handoff_md
        assert "get_service_status [executed]" in postmortem_md
        assert "Hypothesis: LLM [inferred]" in postmortem_md
        assert "Customer / Business Impact" in postmortem_md
        assert "[Placeholder]" in postmortem_md
        assert "Confirmed root cause is not automatically claimed" in postmortem_md

    asyncio.run(scenario())


def test_open_incident_postmortem_has_followup_items() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            incident_id = await _seed_incident(db_path, status="open")
            postmortem = await reports.generate_postmortem_draft(incident_id, db_path=db_path)

        assert postmortem is not None
        markdown = postmortem["markdown"]
        assert "Incident status is open" in markdown
        assert "Assign an owner to continue diagnosis" in markdown
        assert "Add explicit verification evidence" in markdown

    asyncio.run(scenario())


def main() -> None:
    test_handoff_and_postmortem_separate_facts_from_hypotheses()
    test_open_incident_postmortem_has_followup_items()
    print("incident report drafts regression OK")


if __name__ == "__main__":
    main()

