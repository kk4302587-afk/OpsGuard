"""Knowledge store for historical problem resolution.

Automatically saves successful diagnoses and retrieves relevant
past experience when facing similar problems.
"""

import json
from datetime import datetime

import aiosqlite
from loguru import logger

from app.database import get_knowledge_db_path


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

                keywords = query.split()
                if not keywords:
                    return []

                conditions = " OR ".join(["problem_signature LIKE ?" for _ in keywords])
                params = [f"%{kw}%" for kw in keywords]

                cursor = await db.execute(
                    f"""SELECT problem_signature, diagnosis_path, solution, tools_used, success_count
                    FROM knowledge_entries
                    WHERE {conditions}
                    ORDER BY success_count DESC
                    LIMIT ?""",
                    params + [limit],
                )
                rows = await cursor.fetchall()
                return [
                    {
                        "problem_signature": row["problem_signature"],
                        "diagnosis_path": row["diagnosis_path"],
                        "solution": row["solution"],
                        "tools_used": json.loads(row["tools_used"]) if row["tools_used"] else [],
                        "success_count": row["success_count"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Knowledge search failed: {e}")
            return []


# Global knowledge store instance
knowledge_store = KnowledgeStore()
