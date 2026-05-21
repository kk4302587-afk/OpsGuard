"""Knowledge store for historical problem resolution.

Automatically saves successful diagnoses and retrieves relevant
past experience when facing similar problems.
"""

import json
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher

import aiosqlite
from loguru import logger

from app.database import get_knowledge_db_path


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
    "source_modalities",
    "multimodal_evidence",
}
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


def _candidate_text(entry: dict) -> str:
    values = [
        str(entry.get(field) or "")
        for field in ("problem_signature", "diagnosis_path", "solution", "root_cause", "validation_method")
    ]
    for field in _JSON_FIELDS:
        values.append(json.dumps(entry.get(field) or [], ensure_ascii=False, default=str))
    return " ".join(values)


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


def _match_reason(query: str, entry: dict) -> str:
    query_terms = _extract_terms(query)
    candidate_terms = _extract_terms(_candidate_text(entry))
    shared = sorted(query_terms & candidate_terms)[:6]
    reasons = []
    if shared:
        reasons.append(f"shared terms: {', '.join(shared)}")
    if entry.get("root_cause"):
        reasons.append("has structured root cause")
    if entry.get("evidence"):
        reasons.append("has evidence")
    if entry.get("validation_method"):
        reasons.append("has validation method")
    return "; ".join(reasons) or "fuzzy text similarity"


def _safe_to_reuse(entry: dict) -> bool:
    has_validation = bool(entry.get("validation_method"))
    has_applicability = bool(entry.get("applicability_conditions"))
    blockers = entry.get("non_applicability_conditions") or []
    return has_validation and has_applicability and not blockers


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


def _entry_from_row(row: aiosqlite.Row) -> dict:
    entry = {
        "id": row["id"],
        "problem_signature": row["problem_signature"],
        "diagnosis_path": row["diagnosis_path"],
        "solution": row["solution"],
        "tools_used": _json_load_or_default(row["tools_used"], []),
        "success_count": row["success_count"],
    }
    for field in _STRUCTURED_FIELDS:
        if field in row.keys():
            if field in _JSON_FIELDS:
                entry[field] = _json_load_or_default(row[field], [])
            else:
                entry[field] = row[field]
        elif field in _JSON_FIELDS:
            entry[field] = []
        else:
            entry[field] = None
    entry["safe_to_reuse"] = _safe_to_reuse(entry)
    return entry


class KnowledgeStore:
    """Manages the knowledge base of resolved issues."""

    async def save_resolution(
        self,
        problem_signature: str,
        diagnosis_path: str,
        solution: str,
        tools_used: list[str],
        incident_memory: dict | None = None,
    ):
        """Save a successful problem resolution to the knowledge base."""
        try:
            async with aiosqlite.connect(get_knowledge_db_path()) as db:
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
                    "confidence": memory.get("confidence"),
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
                            structured_values["confidence"],
                            structured_values["source_modalities"],
                            structured_values["multimodal_evidence"],
                            existing[0],
                        ),
                    )
                else:
                    await db.execute(
                        """INSERT INTO knowledge_entries
                        (
                            problem_signature, diagnosis_path, solution, tools_used,
                            symptoms, root_cause, evidence, successful_actions,
                            failed_attempts, validation_method, applicability_conditions,
                            non_applicability_conditions, source_incident_id, confidence,
                            source_modalities, multimodal_evidence
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                            structured_values["source_modalities"],
                            structured_values["multimodal_evidence"],
                        ),
                    )
                await db.commit()
                logger.info(f"Knowledge saved: {problem_signature}")
        except Exception as e:
            logger.error(f"Failed to save knowledge: {e}")

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search knowledge base for relevant past resolutions."""
        try:
            async with aiosqlite.connect(get_knowledge_db_path()) as db:
                await ensure_knowledge_schema(db)
                db.row_factory = aiosqlite.Row

                if not _compact_text(query):
                    return []

                cursor = await db.execute(
                    """SELECT *
                    FROM knowledge_entries
                    ORDER BY success_count DESC, last_used DESC"""
                )
                rows = await cursor.fetchall()
                scored = []
                for row in rows:
                    entry = _entry_from_row(row)
                    score = _score_entry(query, entry)
                    if score >= _SEARCH_THRESHOLD:
                        entry["match_score"] = round(score, 4)
                        entry["match_reason"] = _match_reason(query, entry)
                        scored.append(entry)

                scored.sort(key=lambda item: (item["match_score"], item["success_count"]), reverse=True)
                return scored[:limit]
        except Exception as e:
            logger.error(f"Knowledge search failed: {e}")
            raise KnowledgeSearchError(str(e)) from e


# Global knowledge store instance
knowledge_store = KnowledgeStore()
