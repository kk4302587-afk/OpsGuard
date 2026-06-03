"""Knowledge store for historical problem resolution.

Automatically saves successful diagnoses and retrieves relevant
past experience when facing similar problems.
"""

import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

import aiosqlite
from loguru import logger

from app.database import get_knowledge_db_path
from app.knowledge.embeddings import cosine_similarity, get_embedding_provider


class KnowledgeSearchError(RuntimeError):
    """Raised when the knowledge store cannot complete a real search."""


_SEARCH_THRESHOLD = 0.34
_STRUCTURED_FIELDS = (
    "symptoms",
    "root_cause",
    "evidence",
    "successful_actions",
    "failed_attempts",
    "validation_method",
    "applicability_conditions",
    "non_applicability_conditions",
    "source_incident_id",
    "source_session_id",
    "entities",
    "incident_type",
    "source_modality",
    "evidence_refs",
    "tool_call_ids",
    "trace_event_ids",
    "evidence_summaries",
    "has_write_action",
    "write_approved",
    "validation_status",
    "structured_final_valid",
    "owner",
    "review_status",
    "ttl_days",
    "last_validated_at",
    "deprecated_at",
    "confidence",
    "source_modalities",
    "multimodal_evidence",
)
_JSON_FIELDS = {
    "symptoms",
    "evidence",
    "successful_actions",
    "failed_attempts",
    "applicability_conditions",
    "non_applicability_conditions",
    "entities",
    "evidence_refs",
    "tool_call_ids",
    "trace_event_ids",
    "evidence_summaries",
    "source_modalities",
    "multimodal_evidence",
}
_BOOL_FIELDS = {"has_write_action", "write_approved", "structured_final_valid"}
_FTS_TABLE = "knowledge_entries_fts"
_TOKEN_RE = re.compile(r"[a-z0-9_.-]+|[\u4e00-\u9fff]+")
_KNOWN_CJK_TERMS = (
    "重启", "启动", "开启", "停止", "关闭", "删除", "清理", "修改", "配置",
    "服务", "进程", "端口", "日志", "防火墙", "安装", "卸载", "用户", "权限",
    "磁盘", "内存", "CPU", "错误", "检查", "查看", "读取",
)
_STOP_TERMS = {
    "帮我", "请帮", "麻烦", "一下", "一个", "这个", "那个", "进行", "是否",
    "需要", "可以", "请问", "我想",
}
_CANONICAL_TERMS = {
    "重启": "restart",
    "restart": "restart",
    "reload": "restart",
    "启动": "start",
    "开启": "start",
    "start": "start",
    "停止": "stop",
    "关闭": "stop",
    "stop": "stop",
    "服务": "service",
    "service": "service",
    "日志": "log",
    "log": "log",
    "错误": "error",
    "error": "error",
}


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _compact_text(text: str) -> str:
    return "".join(_TOKEN_RE.findall(_normalize_text(text)))


def _add_term(terms: set[str], term: str) -> None:
    term = _normalize_text(term).strip()
    if not term or term in _STOP_TERMS:
        return
    terms.add(term)
    canonical = _CANONICAL_TERMS.get(term)
    if canonical:
        terms.add(canonical)


def _extract_terms(text: str) -> set[str]:
    """Extract mixed Chinese/English operation terms for fuzzy matching."""
    terms: set[str] = set()
    for token in _TOKEN_RE.findall(_normalize_text(text)):
        if not token:
            continue
        if re.fullmatch(r"[a-z0-9_.-]+", token):
            _add_term(terms, token)
            continue

        for known in _KNOWN_CJK_TERMS:
            if _normalize_text(known) in token:
                _add_term(terms, known)

        if len(token) <= 6:
            for i in range(len(token) - 1):
                _add_term(terms, token[i:i + 2])
    return terms


async def ensure_knowledge_schema(db: aiosqlite.Connection) -> None:
    """Create or migrate the knowledge table to the current schema."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            problem_signature TEXT NOT NULL,
            diagnosis_path TEXT NOT NULL,
            solution TEXT NOT NULL,
            tools_used TEXT,
            success_count INTEGER DEFAULT 1,
            last_used DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor = await db.execute("PRAGMA table_info(knowledge_entries)")
    existing = {row[1] for row in await cursor.fetchall()}
    for column in _STRUCTURED_FIELDS:
        if column not in existing:
            await db.execute(f"ALTER TABLE knowledge_entries ADD COLUMN {column} TEXT")
    await db.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE}
        USING fts5(
            problem_signature,
            diagnosis_path,
            solution,
            symptoms,
            root_cause,
            evidence,
            entities,
            incident_type
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_last_used ON knowledge_entries(last_used)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_confidence ON knowledge_entries(confidence)"
    )


def _candidate_text(entry: dict) -> str:
    values = [
        str(entry.get(field) or "")
        for field in ("problem_signature", "diagnosis_path", "solution", "root_cause", "validation_method")
    ]
    for field in _JSON_FIELDS:
        values.append(json.dumps(entry.get(field) or [], ensure_ascii=False, default=str))
    return " ".join(values)


def _fts_text(entry: dict, field: str) -> str:
    value = entry.get(field)
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else default
    except Exception:
        return default


async def _upsert_fts_row(db: aiosqlite.Connection, row_id: int, entry: dict) -> None:
    """Keep the FTS5 index in sync without relying on SQLite triggers."""
    await db.execute(f"DELETE FROM {_FTS_TABLE} WHERE rowid = ?", (row_id,))
    await db.execute(
        f"""
        INSERT INTO {_FTS_TABLE}
            (rowid, problem_signature, diagnosis_path, solution, symptoms,
             root_cause, evidence, entities, incident_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            _fts_text(entry, "problem_signature"),
            _fts_text(entry, "diagnosis_path"),
            _fts_text(entry, "solution"),
            _fts_text(entry, "symptoms"),
            _fts_text(entry, "root_cause"),
            _fts_text(entry, "evidence"),
            _fts_text(entry, "entities"),
            _fts_text(entry, "incident_type"),
        ),
    )


async def _rebuild_fts_index(db: aiosqlite.Connection) -> None:
    """Recreate the FTS table content from durable knowledge rows."""
    await db.execute(f"DROP TABLE IF EXISTS {_FTS_TABLE}")
    await db.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE}
        USING fts5(
            problem_signature,
            diagnosis_path,
            solution,
            symptoms,
            root_cause,
            evidence,
            entities,
            incident_type
        )
        """
    )
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM knowledge_entries")
    rows = await cursor.fetchall()
    for row in rows:
        item = dict(row)
        await _upsert_fts_row(
            db,
            int(item["id"]),
            {
                "problem_signature": item.get("problem_signature"),
                "diagnosis_path": item.get("diagnosis_path"),
                "solution": item.get("solution"),
                "symptoms": _json_loads(item.get("symptoms"), []),
                "root_cause": item.get("root_cause") or "",
                "evidence": _json_loads(item.get("evidence"), []),
                "entities": _json_loads(item.get("entities"), {}),
                "incident_type": item.get("incident_type") or "",
            },
        )


async def _rebuild_fts_if_empty(db: aiosqlite.Connection) -> None:
    cursor = await db.execute(f"SELECT COUNT(*) FROM {_FTS_TABLE}")
    count = (await cursor.fetchone())[0]
    if count:
        return
    db.row_factory = aiosqlite.Row
    cursor = await db.execute("SELECT * FROM knowledge_entries")
    rows = await cursor.fetchall()
    for row in rows:
        entry = _entry_from_row(row)
        await _upsert_fts_row(db, row["id"], entry)


def _score_entry(query: str, entry: dict) -> float:
    query_compact = _compact_text(query)
    candidate_text = _candidate_text(entry)
    candidate_compact = _compact_text(candidate_text)
    if not query_compact or not candidate_compact:
        return 0.0

    if query_compact in candidate_compact or candidate_compact in query_compact:
        substring_score = 0.95
    else:
        substring_score = 0.0

    sequence_score = SequenceMatcher(None, query_compact, candidate_compact).ratio()
    query_terms = _extract_terms(query)
    candidate_terms = _extract_terms(candidate_text)
    if query_terms and candidate_terms:
        shared = query_terms & candidate_terms
        token_score = len(shared) / max(1, min(len(query_terms), len(candidate_terms)))
        important_shared = shared & {"restart", "start", "stop", "nginx", "mysql", "redis", "apache"}
        if important_shared:
            token_score += 0.12
    else:
        token_score = 0.0

    success_boost = min(int(entry.get("success_count") or 0), 5) * 0.01
    return min(1.0, max(substring_score, sequence_score, token_score) + success_boost)


def _keyword_score(query: str, entry: dict) -> float:
    query_terms = _extract_terms(query)
    candidate_terms = _extract_terms(_candidate_text(entry))
    if not query_terms or not candidate_terms:
        return 0.0
    shared = query_terms & candidate_terms
    return min(1.0, len(shared) / max(1, len(query_terms)))


def _semantic_score(query: str, entry: dict) -> float:
    query_terms = _extract_terms(query)
    entity_terms = _extract_entity_terms(entry)
    symptom_terms = _extract_terms(" ".join(map(str, entry.get("symptoms") or [])))
    root_cause_terms = _extract_terms(str(entry.get("root_cause") or ""))
    semantic_terms = entity_terms | symptom_terms | root_cause_terms
    if not query_terms or not semantic_terms:
        return 0.0
    shared = query_terms & semantic_terms
    return min(1.0, len(shared) / max(1, min(len(query_terms), len(semantic_terms))))


def _extract_entity_terms(entry: dict) -> set[str]:
    terms: set[str] = set()
    entities = entry.get("entities") or {}
    if isinstance(entities, dict):
        for value in entities.values():
            if isinstance(value, list):
                for item in value:
                    terms |= _extract_terms(str(item))
            else:
                terms |= _extract_terms(str(value))
    elif isinstance(entities, list):
        for item in entities:
            terms |= _extract_terms(str(item))
    for field in ("problem_signature", "diagnosis_path", "solution"):
        terms |= _extract_terms(str(entry.get(field) or ""))
    return terms


def _recency_score(entry: dict) -> float:
    timestamp = entry.get("last_used") or entry.get("created_at")
    if not timestamp:
        return 0.2
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        days = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).days)
    except Exception:
        return 0.2
    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.8
    if days <= 90:
        return 0.55
    if days <= 180:
        return 0.35
    return 0.15


def _validation_score(entry: dict) -> float:
    status = str(entry.get("validation_status") or "").lower()
    if status == "validated":
        return 1.0
    if entry.get("validation_method"):
        return 0.85
    if status == "missing":
        return 0.0
    return 0.25


def _evidence_coverage_score(entry: dict) -> float:
    score = 0.0
    if entry.get("evidence_refs"):
        score += 0.35
    if entry.get("tool_call_ids"):
        score += 0.25
    if entry.get("evidence") or entry.get("evidence_summaries"):
        score += 0.25
    if entry.get("source_incident_id") or entry.get("source_session_id"):
        score += 0.15
    return min(1.0, score)


def _success_score(entry: dict) -> float:
    return min(1.0, max(0, int(entry.get("success_count") or 0)) / 5)


def _environment_similarity_score(query: str, entry: dict) -> float:
    query_terms = _extract_terms(query)
    environment_terms = _extract_entity_terms(entry)
    for condition in entry.get("applicability_conditions") or []:
        environment_terms |= _extract_terms(str(condition))
    if not query_terms or not environment_terms:
        return 0.0
    return min(1.0, len(query_terms & environment_terms) / max(1, min(len(query_terms), len(environment_terms))))


def _embedding_score(query: str, entry: dict) -> float:
    provider = get_embedding_provider()
    if provider.name == "disabled":
        return 0.0
    return cosine_similarity(provider.embed(query), provider.embed(_candidate_text(entry)))


def _score_breakdown(query: str, entry: dict, fts_score: float = 0.0) -> dict:
    fuzzy_score = _score_entry(query, entry)
    keyword_score = max(_keyword_score(query, entry), fts_score)
    semantic_score = _semantic_score(query, entry)
    embedding_score = _embedding_score(query, entry)
    environment_score = _environment_similarity_score(query, entry)
    recency_score = _recency_score(entry)
    validation_score = _validation_score(entry)
    success_score = _success_score(entry)
    evidence_score = _evidence_coverage_score(entry)
    match_score = max(fuzzy_score, keyword_score, semantic_score, embedding_score)
    final_score = (
        match_score * 0.48
        + recency_score * 0.10
        + environment_score * 0.14
        + validation_score * 0.12
        + success_score * 0.06
        + evidence_score * 0.10
    )
    return {
        "match_score": round(match_score, 4),
        "final_score": round(min(1.0, final_score), 4),
        "fuzzy_score": round(fuzzy_score, 4),
        "keyword_score": round(keyword_score, 4),
        "semantic_score": round(semantic_score, 4),
        "embedding_score": round(embedding_score, 4),
        "recentness": round(recency_score, 4),
        "environment_similarity": round(environment_score, 4),
        "validation_completeness": round(validation_score, 4),
        "success_weight": round(success_score, 4),
        "evidence_coverage": round(evidence_score, 4),
    }


def _retrieval_sources(breakdown: dict, fts_hit: bool) -> list[str]:
    sources: list[str] = []
    if fts_hit or breakdown.get("keyword_score", 0) >= _SEARCH_THRESHOLD:
        sources.append("fts5_keyword")
    if breakdown.get("fuzzy_score", 0) >= _SEARCH_THRESHOLD:
        sources.append("fuzzy_text")
    if breakdown.get("semantic_score", 0) >= _SEARCH_THRESHOLD:
        sources.append("structured_semantic")
    if breakdown.get("embedding_score", 0) >= _SEARCH_THRESHOLD:
        sources.append("embedding")
    if breakdown.get("environment_similarity", 0) >= _SEARCH_THRESHOLD:
        sources.append("environment_similarity")
    return sources or ["rerank"]


def _match_reason(query: str, entry: dict) -> str:
    query_terms = _extract_terms(query)
    candidate_terms = _extract_terms(_candidate_text(entry))
    shared = sorted(query_terms & candidate_terms)[:6]
    reasons = []
    if shared:
        reasons.append(f"shared terms: {', '.join(shared)}")
    if entry.get("entities"):
        reasons.append("has structured entities")
    if entry.get("root_cause"):
        reasons.append("has structured root cause")
    if entry.get("evidence") or entry.get("evidence_refs"):
        reasons.append("has evidence")
    if entry.get("validation_method"):
        reasons.append("has validation method")
    return "; ".join(reasons) or "fuzzy text similarity"


def _safe_to_reuse(entry: dict) -> bool:
    has_validation = bool(entry.get("validation_method"))
    has_applicability = bool(entry.get("applicability_conditions"))
    blockers = entry.get("non_applicability_conditions") or []
    return has_validation and has_applicability and not blockers


def _validation_status(memory: dict) -> str:
    explicit = str(memory.get("validation_status") or "").strip().lower()
    if explicit in {"validated", "missing", "failed", "unknown"}:
        return explicit
    return "validated" if memory.get("validation_method") else "missing"


def _normalize_confidence(memory: dict) -> str:
    confidence = str(memory.get("confidence") or "medium").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"
    if _validation_status(memory) == "missing":
        return "low"
    return confidence


def _recommended_fresh_checks(entry: dict) -> list[str]:
    checks: list[str] = []
    entities = entry.get("entities") or {}
    services = []
    ports = []
    paths = []
    if isinstance(entities, dict):
        services = [str(item) for item in entities.get("services", []) if item]
        ports = [str(item) for item in entities.get("ports", []) if item]
        paths = [str(item) for item in entities.get("paths", []) if item]

    for service in services[:3]:
        checks.append(f"重新检查当前服务状态: {service}")
        checks.append(f"重新查看当前服务日志: {service}")
    for port in ports[:3]:
        checks.append(f"重新检查当前端口监听: {port}")
    for path in paths[:3]:
        checks.append(f"重新检查当前路径/配置: {path}")
    if entry.get("validation_method"):
        checks.append(f"重新执行历史验证方法: {entry.get('validation_method')}")
    if not checks:
        checks.extend([
            "先执行只读工具获取当前状态",
            "对照历史根因前重新验证当前症状",
        ])
    return list(dict.fromkeys(checks))[:6]


def _staleness_status(entry: dict) -> str:
    if entry.get("deprecated_at") or entry.get("review_status") == "deprecated":
        return "deprecated"
    try:
        ttl = int(entry.get("ttl_days") or 90)
    except Exception:
        ttl = 90
    timestamp = entry.get("last_validated_at") or entry.get("last_used") or entry.get("created_at")
    if not timestamp:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = max(0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).days)
    except Exception:
        return "unknown"
    if age_days > ttl:
        return "stale"
    if age_days > max(1, int(ttl * 0.8)):
        return "review_due"
    return "fresh"


def _json_dump_or_none(value) -> str | None:
    if value in (None, "", [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_load_or_default(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _bool_to_db(value) -> int:
    return 1 if bool(value) else 0


def _fts_query(query: str) -> str:
    terms = []
    for term in _extract_terms(query):
        if len(term) < 2:
            continue
        cleaned = re.sub(r"[^a-z0-9_.\-\u4e00-\u9fff]", " ", term).strip()
        if cleaned:
            terms.append(f'"{cleaned}"')
    return " OR ".join(sorted(set(terms))[:12])


def _apply_filters(entry: dict, filters: dict | None) -> bool:
    if entry.get("review_status") == "deprecated" or entry.get("deprecated_at"):
        if not (filters or {}).get("include_deprecated"):
            return False
    if not filters:
        return True

    entities = entry.get("entities") or {}

    def entity_contains(key: str, expected) -> bool:
        if expected in (None, ""):
            return True
        values = entities.get(key, []) if isinstance(entities, dict) else []
        if not isinstance(values, list):
            values = [values]
        expected_text = _normalize_text(str(expected))
        return any(expected_text == _normalize_text(str(item)) for item in values)

    if not entity_contains("services", filters.get("service")):
        return False
    if not entity_contains("hosts", filters.get("host")):
        return False
    if not entity_contains("paths", filters.get("path")):
        return False
    if not entity_contains("ports", filters.get("port")):
        return False
    if filters.get("incident_type") and _normalize_text(filters["incident_type"]) != _normalize_text(str(entry.get("incident_type") or "")):
        return False
    if filters.get("source_modality"):
        modality = _normalize_text(str(filters["source_modality"]))
        modalities = {_normalize_text(str(item)) for item in entry.get("source_modalities") or []}
        if modality != _normalize_text(str(entry.get("source_modality") or "")) and modality not in modalities:
            return False
    if filters.get("confidence"):
        allowed = filters["confidence"]
        allowed_set = {allowed} if isinstance(allowed, str) else set(allowed)
        if str(entry.get("confidence") or "medium") not in allowed_set:
            return False
    if filters.get("min_success_count") is not None:
        if int(entry.get("success_count") or 0) < int(filters["min_success_count"]):
            return False
    if filters.get("max_age_days") is not None:
        recency = _recency_score(entry)
        max_age = int(filters["max_age_days"])
        minimum = 1.0 if max_age <= 7 else 0.8 if max_age <= 30 else 0.55 if max_age <= 90 else 0.15
        if recency < minimum:
            return False
    if filters.get("review_status"):
        allowed = filters["review_status"]
        allowed_set = {allowed} if isinstance(allowed, str) else set(allowed)
        if str(entry.get("review_status") or "draft") not in allowed_set:
            return False
    return True


def _entry_from_row(row: aiosqlite.Row) -> dict:
    entry = {
        "id": row["id"],
        "problem_signature": row["problem_signature"],
        "diagnosis_path": row["diagnosis_path"],
        "solution": row["solution"],
        "tools_used": _json_load_or_default(row["tools_used"], []),
        "success_count": row["success_count"],
        "last_used": _row_get(row, "last_used"),
        "created_at": _row_get(row, "created_at"),
    }
    for field in _STRUCTURED_FIELDS:
        if field in row.keys():
            if field in _JSON_FIELDS:
                entry[field] = _json_load_or_default(row[field], [])
            elif field in _BOOL_FIELDS:
                entry[field] = bool(row[field]) if row[field] is not None else False
            else:
                entry[field] = row[field]
        elif field in _JSON_FIELDS:
            entry[field] = []
        elif field in _BOOL_FIELDS:
            entry[field] = False
        else:
            entry[field] = None
    if not entry.get("validation_status"):
        entry["validation_status"] = "validated" if entry.get("validation_method") else "missing"
    if not entry.get("confidence"):
        entry["confidence"] = "low" if entry["validation_status"] == "missing" else "medium"
    entry["safe_to_reuse"] = _safe_to_reuse(entry)
    entry["recommended_fresh_checks"] = _recommended_fresh_checks(entry)
    entry["staleness_status"] = _staleness_status(entry)
    return entry


def _row_get(row: aiosqlite.Row, key: str, default=None):
    try:
        return row[key]
    except Exception:
        return default


class KnowledgeStore:
    """Manages the knowledge base of resolved issues."""

    async def save_resolution(
        self,
        problem_signature: str,
        diagnosis_path: str,
        solution: str,
        tools_used: list[str],
        incident_memory: dict | None = None,
    ) -> int:
        """Save a successful problem resolution to the knowledge base."""
        try:
            async with aiosqlite.connect(get_knowledge_db_path(), timeout=30) as db:
                await db.execute("PRAGMA busy_timeout = 30000")
                await ensure_knowledge_schema(db)
                # Check if similar problem exists
                cursor = await db.execute(
                    "SELECT id, success_count FROM knowledge_entries WHERE problem_signature = ?",
                    (problem_signature,),
                )
                existing = await cursor.fetchone()
                memory = incident_memory or {}
                structured_values = {
                    "symptoms": _json_dump_or_none(memory.get("symptoms")),
                    "root_cause": memory.get("root_cause"),
                    "evidence": _json_dump_or_none(memory.get("evidence")),
                    "successful_actions": _json_dump_or_none(memory.get("successful_actions")),
                    "failed_attempts": _json_dump_or_none(memory.get("failed_attempts")),
                    "validation_method": memory.get("validation_method"),
                    "applicability_conditions": _json_dump_or_none(memory.get("applicability_conditions")),
                    "non_applicability_conditions": _json_dump_or_none(memory.get("non_applicability_conditions")),
                    "source_incident_id": memory.get("source_incident_id"),
                    "source_session_id": memory.get("source_session_id"),
                    "entities": _json_dump_or_none(memory.get("entities")),
                    "incident_type": memory.get("incident_type"),
                    "source_modality": memory.get("source_modality"),
                    "evidence_refs": _json_dump_or_none(memory.get("evidence_refs")),
                    "tool_call_ids": _json_dump_or_none(memory.get("tool_call_ids")),
                    "trace_event_ids": _json_dump_or_none(memory.get("trace_event_ids")),
                    "evidence_summaries": _json_dump_or_none(memory.get("evidence_summaries")),
                    "has_write_action": _bool_to_db(memory.get("has_write_action")),
                    "write_approved": _bool_to_db(memory.get("write_approved")),
                    "validation_status": _validation_status(memory),
                    "structured_final_valid": (
                        None
                        if memory.get("structured_final_valid") is None
                        else _bool_to_db(memory.get("structured_final_valid"))
                    ),
                    "owner": memory.get("owner"),
                    "review_status": memory.get("review_status") or "draft",
                    "ttl_days": memory.get("ttl_days") or 90,
                    "last_validated_at": memory.get("last_validated_at") or (
                        datetime.now().isoformat() if memory.get("validation_method") else None
                    ),
                    "deprecated_at": memory.get("deprecated_at"),
                    "confidence": _normalize_confidence(memory),
                    "source_modalities": _json_dump_or_none(memory.get("source_modalities")),
                    "multimodal_evidence": _json_dump_or_none(memory.get("multimodal_evidence")),
                }

                if existing:
                    await db.execute(
                        """UPDATE knowledge_entries
                        SET success_count = success_count + 1,
                            last_used = ?,
                            diagnosis_path = ?,
                            solution = ?,
                            symptoms = COALESCE(?, symptoms),
                            root_cause = COALESCE(?, root_cause),
                            evidence = COALESCE(?, evidence),
                            successful_actions = COALESCE(?, successful_actions),
                            failed_attempts = COALESCE(?, failed_attempts),
                            validation_method = COALESCE(?, validation_method),
                            applicability_conditions = COALESCE(?, applicability_conditions),
                            non_applicability_conditions = COALESCE(?, non_applicability_conditions),
                            source_incident_id = COALESCE(?, source_incident_id),
                            source_session_id = COALESCE(?, source_session_id),
                            entities = COALESCE(?, entities),
                            incident_type = COALESCE(?, incident_type),
                            source_modality = COALESCE(?, source_modality),
                            evidence_refs = COALESCE(?, evidence_refs),
                            tool_call_ids = COALESCE(?, tool_call_ids),
                            trace_event_ids = COALESCE(?, trace_event_ids),
                            evidence_summaries = COALESCE(?, evidence_summaries),
                            has_write_action = ?,
                            write_approved = ?,
                            validation_status = COALESCE(?, validation_status),
                            structured_final_valid = COALESCE(?, structured_final_valid),
                            owner = COALESCE(?, owner),
                            review_status = COALESCE(?, review_status),
                            ttl_days = COALESCE(?, ttl_days),
                            last_validated_at = COALESCE(?, last_validated_at),
                            deprecated_at = COALESCE(?, deprecated_at),
                            confidence = COALESCE(?, confidence),
                            source_modalities = COALESCE(?, source_modalities),
                            multimodal_evidence = COALESCE(?, multimodal_evidence)
                        WHERE id = ?""",
                        (
                            datetime.now().isoformat(),
                            diagnosis_path,
                            solution,
                            structured_values["symptoms"],
                            structured_values["root_cause"],
                            structured_values["evidence"],
                            structured_values["successful_actions"],
                            structured_values["failed_attempts"],
                            structured_values["validation_method"],
                            structured_values["applicability_conditions"],
                            structured_values["non_applicability_conditions"],
                            structured_values["source_incident_id"],
                            structured_values["source_session_id"],
                            structured_values["entities"],
                            structured_values["incident_type"],
                            structured_values["source_modality"],
                            structured_values["evidence_refs"],
                            structured_values["tool_call_ids"],
                            structured_values["trace_event_ids"],
                            structured_values["evidence_summaries"],
                            structured_values["has_write_action"],
                            structured_values["write_approved"],
                            structured_values["validation_status"],
                            structured_values["structured_final_valid"],
                            structured_values["owner"],
                            structured_values["review_status"],
                            structured_values["ttl_days"],
                            structured_values["last_validated_at"],
                            structured_values["deprecated_at"],
                            structured_values["confidence"],
                            structured_values["source_modalities"],
                            structured_values["multimodal_evidence"],
                            existing[0],
                        ),
                    )
                    row_id = int(existing[0])
                else:
                    cursor = await db.execute(
                        """INSERT INTO knowledge_entries
                        (
                            problem_signature, diagnosis_path, solution, tools_used,
                            symptoms, root_cause, evidence, successful_actions,
                            failed_attempts, validation_method, applicability_conditions,
                            non_applicability_conditions, source_incident_id, confidence,
                            source_session_id, entities, incident_type, source_modality,
                            evidence_refs, tool_call_ids, trace_event_ids, evidence_summaries,
                            has_write_action, write_approved, validation_status,
                            structured_final_valid,
                            owner, review_status, ttl_days, last_validated_at, deprecated_at,
                            source_modalities, multimodal_evidence
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            problem_signature,
                            diagnosis_path,
                            solution,
                            json.dumps(tools_used, ensure_ascii=False),
                            structured_values["symptoms"],
                            structured_values["root_cause"],
                            structured_values["evidence"],
                            structured_values["successful_actions"],
                            structured_values["failed_attempts"],
                            structured_values["validation_method"],
                            structured_values["applicability_conditions"],
                            structured_values["non_applicability_conditions"],
                            structured_values["source_incident_id"],
                            structured_values["confidence"],
                            structured_values["source_session_id"],
                            structured_values["entities"],
                            structured_values["incident_type"],
                            structured_values["source_modality"],
                            structured_values["evidence_refs"],
                            structured_values["tool_call_ids"],
                            structured_values["trace_event_ids"],
                            structured_values["evidence_summaries"],
                            structured_values["has_write_action"],
                            structured_values["write_approved"],
                            structured_values["validation_status"],
                            structured_values["structured_final_valid"],
                            structured_values["owner"],
                            structured_values["review_status"],
                            structured_values["ttl_days"],
                            structured_values["last_validated_at"],
                            structured_values["deprecated_at"],
                            structured_values["source_modalities"],
                            structured_values["multimodal_evidence"],
                        ),
                    )
                    row_id = int(cursor.lastrowid)
                fts_entry = {
                    "problem_signature": problem_signature,
                    "diagnosis_path": diagnosis_path,
                    "solution": solution,
                    "symptoms": memory.get("symptoms") or [],
                    "root_cause": memory.get("root_cause") or "",
                    "evidence": memory.get("evidence") or [],
                    "entities": memory.get("entities") or {},
                    "incident_type": memory.get("incident_type") or "",
                }
                await db.commit()
                try:
                    await _upsert_fts_row(db, row_id, fts_entry)
                    await db.commit()
                except Exception as fts_error:
                    logger.warning(f"Knowledge FTS sync failed, rebuilding index: {fts_error}")
                    try:
                        await _rebuild_fts_index(db)
                        await db.commit()
                    except Exception as rebuild_error:
                        logger.warning(f"Knowledge FTS rebuild skipped: {rebuild_error}")
                logger.info(f"Knowledge saved: {problem_signature}")
                return row_id
        except Exception as e:
            logger.error(f"Failed to save knowledge: {e}")
            raise

    async def search(self, query: str, limit: int = 5, filters: dict | None = None) -> list[dict]:
        """Search knowledge base for relevant past resolutions."""
        try:
            async with aiosqlite.connect(get_knowledge_db_path()) as db:
                await ensure_knowledge_schema(db)
                try:
                    await _rebuild_fts_if_empty(db)
                except Exception as exc:
                    logger.warning(f"FTS warmup skipped: {exc}")
                db.row_factory = aiosqlite.Row

                if not _compact_text(query):
                    return []

                fts_scores: dict[int, float] = {}
                fts_query = _fts_query(query)
                if fts_query:
                    try:
                        cursor = await db.execute(
                            f"""
                            SELECT rowid, bm25({_FTS_TABLE}) AS rank
                            FROM {_FTS_TABLE}
                            WHERE {_FTS_TABLE} MATCH ?
                            ORDER BY rank
                            LIMIT 50
                            """,
                            (fts_query,),
                        )
                        for row in await cursor.fetchall():
                            rank = abs(float(row["rank"] or 0.0))
                            fts_scores[int(row["rowid"])] = 1.0 / (1.0 + rank)
                    except Exception as exc:
                        logger.debug(f"FTS search skipped: {exc}")

                cursor = await db.execute(
                    """SELECT *
                    FROM knowledge_entries
                    ORDER BY success_count DESC, last_used DESC"""
                )
                rows = await cursor.fetchall()
                scored = []
                for row in rows:
                    entry = _entry_from_row(row)
                    if not _apply_filters(entry, filters):
                        continue
                    breakdown = _score_breakdown(query, entry, fts_scores.get(int(entry["id"]), 0.0))
                    if breakdown["match_score"] >= _SEARCH_THRESHOLD or int(entry["id"]) in fts_scores:
                        entry["match_score"] = breakdown["match_score"]
                        entry["final_score"] = breakdown["final_score"]
                        entry["score_breakdown"] = breakdown
                        entry["retrieval_sources"] = _retrieval_sources(breakdown, int(entry["id"]) in fts_scores)
                        entry["match_reason"] = _match_reason(query, entry)
                        scored.append(entry)

                scored.sort(key=lambda item: (item["final_score"], item["match_score"], item["success_count"]), reverse=True)
                return scored[:limit]
        except Exception as e:
            logger.error(f"Knowledge search failed: {e}")
            raise KnowledgeSearchError(str(e)) from e

    async def set_review_status(
        self,
        entry_id: int,
        *,
        review_status: str,
        owner: str | None = None,
    ) -> dict:
        """Mark one knowledge entry as reviewed, draft, or deprecated."""
        if review_status not in {"draft", "reviewed", "deprecated"}:
            raise ValueError("review_status must be draft, reviewed, or deprecated")

        now = datetime.now().isoformat()
        deprecated_at = now if review_status == "deprecated" else None
        async with aiosqlite.connect(get_knowledge_db_path()) as db:
            await ensure_knowledge_schema(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,))
            existing = await cursor.fetchone()
            if not existing:
                raise KeyError(f"knowledge entry not found: {entry_id}")
            await db.execute(
                """
                UPDATE knowledge_entries
                SET review_status = ?,
                    owner = COALESCE(?, owner),
                    last_validated_at = CASE WHEN ? = 'reviewed' THEN ? ELSE last_validated_at END,
                    deprecated_at = CASE WHEN ? = 'deprecated' THEN ? ELSE NULL END
                WHERE id = ?
                """,
                (
                    review_status,
                    owner,
                    review_status,
                    now,
                    review_status,
                    deprecated_at,
                    entry_id,
                ),
            )
            await db.commit()
            cursor = await db.execute("SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,))
            row = await cursor.fetchone()
        return _entry_from_row(row)


# Global knowledge store instance
knowledge_store = KnowledgeStore()
