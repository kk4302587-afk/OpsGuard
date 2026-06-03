#!/usr/bin/env python3
"""AI-SRE 7.7 evaluation runner.

Runs the standardized benchmark cases through the live WebSocket Agent path
when possible, then scores each case with deterministic trace/stream metrics.
Use ``--dry-run`` in CI to validate the suite and scoring model without calling
the live Agent or LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from eval_lib.ai_sre_scoring import EvalCase, EvalScore, markdown_report, score_case  # noqa: E402


BASE_URL = os.environ.get("OPSGUARD_BASE_URL", "http://127.0.0.1:8000")
WS_BASE = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
CASES_PATH = REPO_ROOT / "evals/ai_sre_7_7/benchmark_cases.json"
RUN_ID = datetime.now().strftime("%Y%m%d-%H%M%S")


class Runner:
    def __init__(self, *, dry_run: bool = False, include_deterministic: bool = False, timeout: float = 210) -> None:
        self.dry_run = dry_run
        self.include_deterministic = include_deterministic
        self.timeout = timeout
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=30)
        self.session_id = ""
        self.scores: list[EvalScore] = []

    async def close(self) -> None:
        await self.client.aclose()

    async def post_json(self, path: str, body: dict | None = None) -> dict:
        response = await self.client.post(path, json=body or {})
        response.raise_for_status()
        return response.json()

    async def get_json(self, path: str) -> dict:
        response = await self.client.get(path)
        response.raise_for_status()
        return response.json()

    async def create_session(self) -> str:
        data = await self.post_json("/api/sessions/")
        return str(data["id"])

    async def trace(self) -> list[dict[str, Any]]:
        if not self.session_id:
            return []
        data = await self.get_json(f"/api/sessions/{self.session_id}/trace")
        return list(data.get("trace") or [])

    async def setup(self) -> None:
        if self.dry_run:
            self.session_id = "dry-run"
            return
        self.session_id = await self.create_session()

    async def send(self, case: EvalCase) -> dict[str, Any]:
        uri = f"{WS_BASE}/ws/{self.session_id}"
        events: list[dict[str, Any]] = []
        responses: list[dict[str, Any]] = []
        approval_requests: list[dict[str, Any]] = []
        suggestions: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        started = time.time()

        async with websockets.connect(uri, open_timeout=10, ping_timeout=20) as ws:
            while True:
                try:
                    events.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=0.2)))
                except asyncio.TimeoutError:
                    break

            await ws.send(json.dumps({"type": "message", "content": case.prompt}, ensure_ascii=False))
            while time.time() - started < self.timeout:
                try:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=self.timeout - (time.time() - started)))
                except asyncio.TimeoutError:
                    errors.append({"type": "timeout", "content": f"timeout after {self.timeout}s"})
                    break
                events.append(event)
                typ = event.get("type")
                if typ == "runbook_suggestion":
                    suggestions.append(event)
                    await ws.send(json.dumps({
                        "type": "runbook_decision",
                        "decision": "dismiss",
                        "original_message": event.get("original_message") or case.prompt,
                    }, ensure_ascii=False))
                elif typ == "approval_request":
                    approval_requests.append(event)
                    await ws.send(json.dumps({
                        "type": "approve",
                        "request_id": event["request_id"],
                        "approved": False,
                    }, ensure_ascii=False))
                elif typ == "response":
                    responses.append(event)
                    break
                elif typ == "error":
                    errors.append(event)
                    break

        return {
            "events": events,
            "responses": responses,
            "approval_requests": approval_requests,
            "suggestions": suggestions,
            "errors": errors,
            "elapsed_seconds": time.time() - started,
        }

    def dry_stream(self, case: EvalCase) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
        tool = case.expected_tools_any[0] if case.expected_tools_any else "agent"
        evidence = " ".join(case.required_evidence_terms + case.expected_rca_terms_any[:1])
        trace = [
            {
                "phase": "tool_call",
                "event_type": "success",
                "source": tool,
                "content": f"dry-run evidence for {case.id}: {evidence}",
                "execution_state": "executed",
            }
        ]
        response = f"Dry-run RCA for {case.id}: {evidence or 'no expected terms'}"
        return trace, {"approval_requests": [], "suggestions": [], "elapsed_seconds": 0.0}, response

    async def run_case(self, case: EvalCase) -> None:
        if case.mode == "deterministic" and not self.include_deterministic:
            score = EvalScore(
                case_id=case.id,
                category=case.category,
                passed=True,
                metrics={"skipped_deterministic_fixture": 1.0},
                checks={"skipped_deterministic_fixture": True},
                tools=[],
                approvals=0,
                issue="Skipped by default; deterministic fixture not required for live MVP runner.",
            )
            self.scores.append(score)
            print(f"[skip] {case.id} deterministic fixture")
            return

        if self.dry_run:
            trace, stream, response = self.dry_stream(case)
        else:
            trace_before = await self.trace()
            stream = await self.send(case)
            trace_after = await self.trace()
            trace = trace_after[len(trace_before):]
            response = stream["responses"][-1]["content"] if stream["responses"] else ""
            if stream["errors"] and not response:
                response = json.dumps(stream["errors"], ensure_ascii=False)

        score = score_case(case, trace=trace, stream=stream, response=response)
        self.scores.append(score)
        print(f"[{'pass' if score.passed else 'fail'}] {case.id} tools={','.join(score.tools) or '-'} issue={score.issue or '-'}")

    async def run(self) -> Path:
        await self.setup()
        cases = load_cases(CASES_PATH)
        for case in cases:
            await self.run_case(case)
        report_path = REPO_ROOT / "docs" / f"ai-sre-7-7-evaluation-report-{RUN_ID}.md"
        report_path.write_text(
            markdown_report(self.scores, title="AI-SRE 7.7 Evaluation Report", base_url=BASE_URL, session_id=self.session_id),
            encoding="utf-8",
        )
        return report_path


def load_cases(path: Path) -> list[EvalCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [EvalCase.from_dict(item) for item in data.get("cases", [])]


async def main_async(args: argparse.Namespace) -> None:
    runner = Runner(dry_run=args.dry_run, include_deterministic=args.include_deterministic, timeout=args.timeout)
    try:
        report = await runner.run()
        print(f"report={report}")
        if args.fail_on_regression and any(not score.passed for score in runner.scores):
            raise SystemExit(1)
    finally:
        await runner.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpsGuard AI-SRE 7.7 benchmark evaluations.")
    parser.add_argument("--dry-run", action="store_true", help="Validate suite and scoring without calling the live Agent.")
    parser.add_argument("--include-deterministic", action="store_true", help="Include fixture-backed deterministic cases.")
    parser.add_argument("--timeout", type=float, default=210, help="Per live-agent case timeout in seconds.")
    parser.add_argument("--fail-on-regression", action="store_true", help="Exit non-zero if any scored case fails.")
    return parser.parse_args()


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
