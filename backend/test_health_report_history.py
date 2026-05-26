"""Regression checks for saved health report snapshots."""

import asyncio
import os
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)

from app.api import health_report


def test_generated_health_report_is_saved_as_latest() -> None:
    async def scenario() -> None:
        original_get_path = health_report.get_knowledge_db_path

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "knowledge.db")
            try:
                health_report.get_knowledge_db_path = lambda: db_path

                generated = await health_report.generate_health_report()
                latest = await health_report.get_latest_health_report()
            finally:
                health_report.get_knowledge_db_path = original_get_path

        assert latest["generated_at"] == generated["generated_at"]
        assert latest["overall_status"] == generated["overall_status"]
        assert latest["sections"]

    asyncio.run(scenario())


def main() -> None:
    test_generated_health_report_is_saved_as_latest()
    print("health report history regression OK")


if __name__ == "__main__":
    main()
