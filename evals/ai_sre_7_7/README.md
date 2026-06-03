# AI-SRE 7.7 Evaluation Framework

This directory contains the AI-SRE 7.7 benchmark suite.

## Files

- `benchmark_cases.json`: repeatable incident scenarios for RCA, evidence, safety, retrieval, and Runbook applicability scoring.
- `high_risk_prompts.json`: deterministic high-risk prompt regression set. It covers hard blocks, approval-required write paths, rollback approval, and approval-bypass attempts.

## Run

Dry-run validation, suitable for quick CI checks:

```bash
backend/.venv/bin/python scripts/ai_sre_eval_runner.py --dry-run --fail-on-regression
```

Live Agent evaluation, requires the backend and configured LLM:

```bash
backend/.venv/bin/python scripts/ai_sre_eval_runner.py --fail-on-regression
```

Reports are written to `docs/ai-sre-7-7-evaluation-report-<timestamp>.md`.

## Scored Metrics

- RCA accuracy
- required evidence coverage
- unsafe action attempt rate
- hallucinated execution rate
- approval bypass rate
- rollback availability
- mean tool calls to diagnosis
- mean time to useful answer
- Runbook applicability accuracy
- fresh evidence compliance

The MVP scorer is deterministic and uses trace events, stream events, approval
requests, tool names, and expected terms. It intentionally avoids an LLM judge so
CI results remain stable.
