"""Helpers for evidence-aware trace events.

Trace events are streamed to the UI and may also be persisted through audit
metadata. Keep this module free of I/O so tests can validate trace truthfulness
without booting the whole application.
"""

from typing import Any


_CATEGORY_EVIDENCE_TYPES = {
    "config": "config",
    "file": "config",
    "log": "log",
    "network": "command",
    "service": "command",
    "process": "command",
    "disk": "command",
    "system": "command",
    "package": "command",
    "user": "command",
    "firewall": "command",
    "cron": "command",
}


def compact_observed(value: Any, max_chars: int = 500) -> str:
    """Return a compact human-readable observation string."""
    if value in (None, ""):
        return ""

    import json

    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def build_evidence(
    *,
    claim: str,
    evidence_type: str,
    source: str,
    observed: Any = "",
    confidence: str = "medium",
    execution_state: str = "inferred",
    failure_reason: str | None = None,
    next_check: str | None = None,
) -> dict:
    """Build a normalized evidence payload, omitting empty optional fields."""
    evidence = {
        "claim": claim,
        "evidence_type": evidence_type,
        "source": source,
        "observed": compact_observed(observed),
        "confidence": confidence,
        "execution_state": execution_state,
    }
    if failure_reason:
        evidence["failure_reason"] = compact_observed(failure_reason)
    if next_check:
        evidence["next_check"] = next_check
    return evidence


def trace_event(
    *,
    phase: str,
    event_type: str,
    content: str,
    evidence: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build a trace event with optional top-level evidence fields.

    The top-level fields are convenient for the live WebSocket UI; the same
    evidence is mirrored into metadata so persisted audit traces can reload it.
    """
    event: dict[str, Any] = {
        "type": "trace",
        "phase": phase,
        "event_type": event_type,
        "content": content,
    }
    if metadata:
        event["metadata"] = dict(metadata)
    if evidence:
        event.update(evidence)
        event.setdefault("metadata", {})["evidence"] = evidence
    return event


def tool_result_evidence(
    *,
    tool_name: str,
    tool_args: dict,
    tool_def,
    result: Any,
    claim: str | None = None,
) -> dict:
    """Create evidence from a real tool result."""
    result_repr = result.__dict__ if hasattr(result, "__dict__") else result
    success = result_repr.get("success", True) if isinstance(result_repr, dict) else True
    error = result_repr.get("error") if isinstance(result_repr, dict) else None
    data = result_repr.get("data") if isinstance(result_repr, dict) else result_repr
    category = getattr(tool_def, "category", "")
    evidence_type = _CATEGORY_EVIDENCE_TYPES.get(category, "command")

    target = _target_from_args(tool_args)
    default_claim = (
        f"{tool_name} executed against {target}"
        if success
        else f"{tool_name} failed against {target}"
    )
    return build_evidence(
        claim=claim or default_claim,
        evidence_type=evidence_type,
        source=tool_name,
        observed=data if success else error,
        confidence="high",
        execution_state="executed" if success else "failed",
        failure_reason=None if success else (error or "ToolResult.success is false"),
        next_check=None if success else "Review the tool error and run a safer read-only check before retrying.",
    )


def tool_plan_evidence(tool_name: str, tool_args: dict) -> dict:
    """Evidence for a planned tool call that has not executed yet."""
    return build_evidence(
        claim=f"Planning to call {tool_name}",
        evidence_type="user input",
        source=tool_name,
        observed=tool_args,
        confidence="medium",
        execution_state="skipped",
    )


def verification_evidence(
    *,
    claim: str,
    source: str,
    observed: Any,
    success: bool,
    evidence_type: str = "command",
) -> dict:
    """Evidence for post-action verification or before/after comparison."""
    return build_evidence(
        claim=claim,
        evidence_type=evidence_type,
        source=source,
        observed=observed,
        confidence="high",
        execution_state="executed" if success else "failed",
        failure_reason=None if success else compact_observed(observed),
        next_check=None if success else "Re-run a read-only status/config check to isolate the mismatch.",
    )


def knowledge_evidence(count: int, observed: Any, *, failed: bool = False) -> dict:
    """Evidence for knowledge retrieval results."""
    if failed:
        return build_evidence(
            claim="Knowledge retrieval failed",
            evidence_type="knowledge",
            source="knowledge_store.search",
            observed=observed,
            confidence="high",
            execution_state="failed",
            failure_reason=compact_observed(observed),
            next_check="Check the knowledge database/search backend before relying on history.",
        )
    return build_evidence(
        claim=f"Knowledge search returned {count} matching entries",
        evidence_type="knowledge",
        source="knowledge_store.search",
        observed=observed,
        confidence="high" if count else "medium",
        execution_state="executed",
    )


def inference_evidence(claim: str, source: str, observed: Any = "") -> dict:
    """Evidence for planning or LLM-only inference steps."""
    return build_evidence(
        claim=claim,
        evidence_type="user input",
        source=source,
        observed=observed,
        confidence="medium",
        execution_state="inferred",
    )


def _target_from_args(tool_args: dict) -> str:
    """Return a compact target name from common tool arguments."""
    for key in ("service", "filepath", "path", "dirpath", "source", "destination", "port", "username", "name"):
        value = tool_args.get(key)
        if value not in (None, ""):
            return str(value)
    return "current system"
