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


def _score_entry(query: str, entry: dict) -> float:
    query_compact = _compact_text(query)
    candidate_text = " ".join(
        str(entry.get(field) or "")
        for field in ("problem_signature", "diagnosis_path", "solution")
    )
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


class KnowledgeStore:
    """Manages the knowledge base of resolved issues."""

    async def save_resolution(
        self,
        problem_signature: str,
        diagnosis_path: str,
        solution: str,
        tools_used: list[str],
    ):
        """Save a successful problem resolution to the knowledge base."""
        try:
            async with aiosqlite.connect(get_knowledge_db_path()) as db:
                # Check if similar problem exists
                cursor = await db.execute(
                    "SELECT id, success_count FROM knowledge_entries WHERE problem_signature = ?",
                    (problem_signature,),
                )
                existing = await cursor.fetchone()

                if existing:
                    await db.execute(
                        """UPDATE knowledge_entries
                        SET success_count = success_count + 1,
                            last_used = ?,
                            diagnosis_path = ?,
                            solution = ?
                        WHERE id = ?""",
                        (datetime.now().isoformat(), diagnosis_path, solution, existing[0]),
                    )
                else:
                    await db.execute(
                        """INSERT INTO knowledge_entries
                        (problem_signature, diagnosis_path, solution, tools_used)
                        VALUES (?, ?, ?, ?)""",
                        (
                            problem_signature,
                            diagnosis_path,
                            solution,
                            json.dumps(tools_used, ensure_ascii=False),
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
                db.row_factory = aiosqlite.Row

                if not _compact_text(query):
                    return []

                cursor = await db.execute(
                    """SELECT id, problem_signature, diagnosis_path, solution, tools_used, success_count
                    FROM knowledge_entries
                    ORDER BY success_count DESC, last_used DESC"""
                )
                rows = await cursor.fetchall()
                scored = []
                for row in rows:
                    entry = {
                        "id": row["id"],
                        "problem_signature": row["problem_signature"],
                        "diagnosis_path": row["diagnosis_path"],
                        "solution": row["solution"],
                        "tools_used": json.loads(row["tools_used"]) if row["tools_used"] else [],
                        "success_count": row["success_count"],
                    }
                    score = _score_entry(query, entry)
                    if score >= _SEARCH_THRESHOLD:
                        entry["match_score"] = round(score, 4)
                        scored.append(entry)

                scored.sort(key=lambda item: (item["match_score"], item["success_count"]), reverse=True)
                return scored[:limit]
        except Exception as e:
            logger.error(f"Knowledge search failed: {e}")
            raise KnowledgeSearchError(str(e)) from e


# Global knowledge store instance
knowledge_store = KnowledgeStore()
