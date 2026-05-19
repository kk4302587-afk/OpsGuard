"""Fuzzy-match incoming user messages against saved Runbooks.

Returns the best-matching Runbook (if any) for an incoming user message,
so that the WebSocket gateway can proactively offer to replay it instead of
spinning up a fresh Agent pipeline. This is the "C" side of the B+C feature.

The match uses `difflib.SequenceMatcher.ratio()` against both the runbook's
`name` and `trigger_pattern`, taking the max. No external NLP dependency —
keeping the bar low and the matching transparent.
"""

import json
from difflib import SequenceMatcher

import aiosqlite
from loguru import logger

from app.database import get_knowledge_db_path

# Default similarity threshold. Tuned conservatively: false negatives (the
# Agent will still handle the request normally) are far less costly than
# false positives (interrupting the user with an irrelevant suggestion).
DEFAULT_MIN_RATIO = 0.6

# Minimum normalized length of the user message before we'll attempt matching.
# Stops "继续" or "执行" from accidentally matching short runbook names.
MIN_QUERY_LENGTH = 4


def _normalize(text: str) -> str:
    """Lowercase + strip whitespace and trailing punctuation for fair comparison."""
    if not text:
        return ""
    return text.strip().rstrip("。.,，！!？?~ ").lower()


async def find_matching_runbook(
    user_message: str,
    *,
    min_ratio: float = DEFAULT_MIN_RATIO,
    top_n: int = 100,
) -> dict | None:
    """Return the best-matching runbook for ``user_message`` or ``None``.

    Args:
        user_message: Raw incoming user text.
        min_ratio: Minimum similarity ratio (0..1) required for a match.
        top_n: Cap on how many runbooks (most-used first) to scan.

    Returns:
        ``dict`` with the runbook fields + ``match_ratio``, or ``None`` if
        nothing crosses the bar (or if the table doesn't exist yet).
    """
    user_norm = _normalize(user_message)
    if not user_norm or len(user_norm) < MIN_QUERY_LENGTH:
        return None

    try:
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, name, description, trigger_pattern, steps, "
                "       run_count, last_run, created_at "
                "FROM runbooks ORDER BY run_count DESC, last_run DESC LIMIT ?",
                (top_n,),
            )
            rows = await cursor.fetchall()
    except Exception as e:
        # Table likely doesn't exist yet (no runbook ever saved). Not an error.
        logger.debug(f"Runbook match: query skipped ({e})")
        return None

    if not rows:
        return None

    best = None
    best_ratio = 0.0
    for row in rows:
        name_norm = _normalize(row["name"] or "")
        trigger_norm = _normalize(row["trigger_pattern"] or "")
        ratio_name = SequenceMatcher(None, user_norm, name_norm).ratio() if name_norm else 0.0
        ratio_trigger = SequenceMatcher(None, user_norm, trigger_norm).ratio() if trigger_norm else 0.0
        ratio = max(ratio_name, ratio_trigger)
        if ratio > best_ratio:
            best_ratio = ratio
            best = row

    if best is None or best_ratio < min_ratio:
        return None

    try:
        steps = json.loads(best["steps"]) if best["steps"] else []
    except Exception:
        steps = []

    return {
        "id": best["id"],
        "name": best["name"],
        "description": best["description"],
        "trigger_pattern": best["trigger_pattern"],
        "steps": steps,
        "step_count": len(steps),
        "run_count": best["run_count"],
        "last_run": best["last_run"],
        "created_at": best["created_at"],
        "match_ratio": round(best_ratio, 3),
    }
