"""Fuzzy-match incoming user messages against saved Runbooks.

Returns the best-matching Runbook (if any) for an incoming user message,
so that the WebSocket gateway can proactively offer to replay it instead of
spinning up a fresh Agent pipeline. This is the "C" side of the B+C feature.

The match uses `difflib.SequenceMatcher.ratio()` against both the runbook's
`name` and `trigger_pattern`, taking the max. No external NLP dependency —
keeping the bar low and the matching transparent.
"""

import re
from difflib import SequenceMatcher

import aiosqlite
from loguru import logger

from app.agent.runbook_governance import ensure_runbook_schema, serialize_runbook
from app.agent.runbook_preflight import preflight_runbook
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


def _match_variants(text: str) -> list[str]:
    """Return normalized variants so parameters don't drown out intent text."""
    normalized = _normalize(text)
    if not normalized:
        return []
    without_assignments = re.sub(
        r"\b[A-Za-z_][A-Za-z0-9_]*\s*[=:：]\s*[^\s，。；;]+",
        " ",
        normalized,
    )
    without_paths = re.sub(r"/[^\s，。；;]+", " ", without_assignments)
    compact = re.sub(r"\s+", " ", without_paths).strip()
    variants = [normalized]
    if compact and compact != normalized:
        variants.append(compact)
    return variants


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
    user_variants = _match_variants(user_message)
    if not user_variants or max(len(item) for item in user_variants) < MIN_QUERY_LENGTH:
        return None

    try:
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            await ensure_runbook_schema(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * "
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
        ratio = max(
            [
                SequenceMatcher(None, user_norm, candidate).ratio()
                for user_norm in user_variants
                for candidate in (name_norm, trigger_norm)
                if candidate
            ]
            or [0.0]
        )
        if ratio > best_ratio:
            best_ratio = ratio
            best = row

    if best is None or best_ratio < min_ratio:
        return None

    result = serialize_runbook(best)
    result["match_ratio"] = round(best_ratio, 3)
    result["preflight"] = await preflight_runbook(result, user_message)
    return result


async def load_runbook_for_suggestion(
    runbook_id: str,
    user_message: str,
    *,
    match_ratio: float = 1.0,
) -> dict | None:
    """Load one Runbook by id and recompute preflight for clarified input."""
    try:
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            await ensure_runbook_schema(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM runbooks WHERE id = ?", (runbook_id,))
            row = await cursor.fetchone()
    except Exception as e:
        logger.debug(f"Runbook load for suggestion skipped ({e})")
        return None

    if not row:
        return None

    result = serialize_runbook(row)
    result["match_ratio"] = round(match_ratio, 3)
    result["preflight"] = await preflight_runbook(result, user_message)
    return result
