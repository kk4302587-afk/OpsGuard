"""Incident handoff and postmortem draft generation.

Drafts are built from persisted incident timeline events only. The generator is
intentionally deterministic for the MVP so it cannot invent root cause, impact,
or mitigation details that the timeline does not contain.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.incidents.store import get_incident, get_incident_events


CONFIRMED_STATES = {"executed", "failed"}
INFERRED_STATES = {"inferred"}


async def generate_handoff_note(incident_id: str, *, db_path: str | None = None) -> dict | None:
    """Generate a short operational handoff note for one incident."""
    incident = await get_incident(incident_id, db_path=db_path)
    if not incident:
        return None
    events = await get_incident_events(incident_id, db_path=db_path)
    facts = _confirmed_facts(events)
    failures = _failure_events(events)
    next_checks = _next_checks(events)

    lines = [
        f"# Handoff: Incident {incident['id']}",
        "",
        f"- Status: {incident.get('status') or 'unknown'}",
        f"- Session: {incident.get('session_id') or 'unknown'}",
        f"- Created: {_fmt_time(incident.get('created_at'))}",
        f"- Updated: {_fmt_time(incident.get('updated_at'))}",
        "",
        "## Problem",
        _text_or_placeholder(incident.get("problem_statement"), "No problem statement captured."),
        "",
        "## Current State",
        _current_state(incident, failures),
        "",
        "## Confirmed Facts",
        *_bullet_lines(facts, "No confirmed execution evidence is available yet."),
        "",
        "## Failures / Risks",
        *_bullet_lines(failures, "No failed execution evidence is recorded."),
        "",
        "## Next Checks",
        *_bullet_lines(next_checks, "Continue diagnosis from the incident timeline."),
    ]
    markdown = "\n".join(lines)
    return {"incident": incident, "type": "handoff", "markdown": markdown}


async def generate_postmortem_draft(incident_id: str, *, db_path: str | None = None) -> dict | None:
    """Generate a detailed postmortem Markdown draft for one incident."""
    incident = await get_incident(incident_id, db_path=db_path)
    if not incident:
        return None
    events = await get_incident_events(incident_id, db_path=db_path)
    facts = _confirmed_facts(events)
    hypotheses = _inferred_hypotheses(events)
    failures = _failure_events(events)
    timeline = _timeline_lines(events)
    mitigations = _mitigation_lines(events)
    verification = _verification_lines(events)
    action_items = _action_items(incident, events)
    runbook_suggestions = _runbook_suggestions(events)

    lines = [
        f"# Postmortem Draft: Incident {incident['id']}",
        "",
        "## Summary",
        _summary_line(incident, failures),
        "",
        "## Customer / Business Impact",
        "[Placeholder] Add customer-facing impact, duration, and affected scope after confirming with monitoring/support data.",
        "",
        "## Timeline",
        *_bullet_lines(timeline, "No timeline events are available."),
        "",
        "## Confirmed Facts",
        *_bullet_lines(facts, "No confirmed execution evidence is available yet."),
        "",
        "## Inferred Hypotheses",
        *_bullet_lines(hypotheses, "No inferred hypotheses are recorded."),
        "",
        "## Cause",
        _cause_line(facts, failures),
        "",
        "## Mitigation",
        *_bullet_lines(mitigations, "No mitigation action is confirmed in the incident timeline."),
        "",
        "## Verification",
        *_bullet_lines(verification, "No verification evidence is recorded."),
        "",
        "## Action Items",
        *_bullet_lines(action_items, "Review the incident manually and add follow-up owners/dates."),
        "",
        "## Runbook Improvement Suggestions",
        *_bullet_lines(runbook_suggestions, "No Runbook-specific improvement was identified from the available evidence."),
        "",
        "## Evidence Boundary",
        "Confirmed facts above come only from timeline evidence with execution_state=executed or execution_state=failed. "
        "Hypotheses and placeholders are explicitly labeled and should be verified before publication.",
    ]
    markdown = "\n".join(lines)
    return {"incident": incident, "type": "postmortem", "markdown": markdown}


def _confirmed_facts(events: list[dict]) -> list[str]:
    facts = []
    for event in events:
        evidence = _evidence(event)
        if evidence.get("execution_state") in CONFIRMED_STATES:
            facts.append(_event_fact(event, evidence))
    return _dedupe(facts)


def _inferred_hypotheses(events: list[dict]) -> list[str]:
    hypotheses = []
    for event in events:
        evidence = _evidence(event)
        if evidence.get("execution_state") in INFERRED_STATES:
            hypotheses.append(_event_fact(event, evidence, prefix="Hypothesis"))
    return _dedupe(hypotheses)


def _failure_events(events: list[dict]) -> list[str]:
    failures = []
    for event in events:
        evidence = _evidence(event)
        if event.get("event_type") in {"failure", "blocked"} or evidence.get("execution_state") == "failed":
            reason = evidence.get("failure_reason") or event.get("detail") or event.get("title")
            failures.append(_compact(reason))
    return _dedupe(failures)


def _next_checks(events: list[dict]) -> list[str]:
    checks = []
    for event in events:
        next_check = _evidence(event).get("next_check")
        if next_check:
            checks.append(_compact(next_check))
    return _dedupe(checks)


def _timeline_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        stamp = _fmt_time(event.get("timestamp"))
        title = _compact(event.get("title") or event.get("detail") or event.get("phase"))
        state = _evidence(event).get("execution_state")
        suffix = f" [{state}]" if state else ""
        lines.append(f"{stamp} - {event.get('phase')} / {event.get('event_type')}: {title}{suffix}")
    return lines


def _mitigation_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        text = " ".join(str(event.get(key) or "") for key in ("title", "detail")).lower()
        evidence = _evidence(event)
        if evidence.get("execution_state") == "executed" and any(word in text for word in ("rollback", "restore", "restart", "start", "stop", "write", "runbook")):
            lines.append(_event_fact(event, evidence))
    return _dedupe(lines)


def _verification_lines(events: list[dict]) -> list[str]:
    lines = []
    for event in events:
        evidence = _evidence(event)
        if event.get("phase") == "verification" or "verify" in str(evidence.get("claim", "")).lower():
            lines.append(_event_fact(event, evidence))
    return _dedupe(lines)


def _action_items(incident: dict, events: list[dict]) -> list[str]:
    items = []
    failures = _failure_events(events)
    if incident.get("status") != "resolved":
        items.append("Assign an owner to continue diagnosis until the incident is resolved.")
    if failures:
        items.append("Review failed checks and document whether each failure was environmental, permission-related, or service-related.")
    if not _verification_lines(events):
        items.append("Add explicit verification evidence before closing the postmortem.")
    if not incident.get("final_summary"):
        items.append("Add a final incident summary once mitigation is confirmed.")
    return items


def _runbook_suggestions(events: list[dict]) -> list[str]:
    suggestions = []
    sources = [_evidence(event).get("source") for event in events]
    if "get_recent_changes" in sources:
        suggestions.append("Add a Runbook step to check recent local system changes early in the diagnosis.")
    if any(event.get("phase") == "approval_request" for event in events):
        suggestions.append("Document approval impact and rollback expectations in the related Runbook.")
    if any(source in sources for source in ("get_service_status", "get_service_logs")):
        suggestions.append("Add service status and recent log checks as reusable Runbook validation steps.")
    return _dedupe(suggestions)


def _summary_line(incident: dict, failures: list[str]) -> str:
    status = incident.get("status") or "unknown"
    problem = _compact(incident.get("problem_statement") or "No problem statement captured")
    if status == "resolved":
        return f"Incident is marked resolved. Problem: {problem}"
    if failures:
        return f"Incident status is {status}. Failure evidence exists and requires follow-up. Problem: {problem}"
    return f"Incident status is {status}. Problem: {problem}"


def _current_state(incident: dict, failures: list[str]) -> str:
    status = incident.get("status") or "unknown"
    if status == "resolved":
        return "Resolved according to the incident record. Confirm service health before handoff completion."
    if failures:
        return "Not fully resolved. Failed checks remain in the timeline and need follow-up."
    return "Open or unresolved. Continue diagnosis from the latest timeline event."


def _cause_line(facts: list[str], failures: list[str]) -> str:
    if failures:
        return "Confirmed root cause is not automatically claimed. Failure evidence should be reviewed and linked to a verified cause."
    if facts:
        return "No explicit root cause is confirmed by the timeline. Use confirmed facts above as evidence inputs."
    return "[Placeholder] Root cause not yet confirmed by incident evidence."


def _event_fact(event: dict, evidence: dict, prefix: str = "Fact") -> str:
    source = evidence.get("source") or event.get("phase") or "timeline"
    claim = evidence.get("claim") or event.get("title") or event.get("detail") or "timeline event"
    observed = evidence.get("observed") or event.get("detail") or ""
    state = evidence.get("execution_state") or "unknown"
    return f"{prefix}: {source} [{state}] - {_compact(claim)}" + (f" | observed: {_compact(observed)}" if observed else "")


def _evidence(event: dict) -> dict:
    evidence = event.get("evidence")
    return evidence if isinstance(evidence, dict) else {}


def _bullet_lines(items: list[str], empty_text: str) -> list[str]:
    values = items or [empty_text]
    return [f"- {item}" for item in values]


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _text_or_placeholder(value: Any, placeholder: str) -> str:
    text = _compact(value)
    return text or placeholder


def _compact(value: Any, max_chars: int = 400) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _fmt_time(value: Any) -> str:
    text = str(value or "")
    if not text:
        return "unknown"
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text.replace("T", " ")[:19]

