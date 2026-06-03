"""Read-only observability connectors for Prometheus and Loki."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import settings
from app.mcp_tools.process_tools import ToolResult


def prometheus_query(query: str, time_: str = "") -> ToolResult:
    """Run a Prometheus instant query and return compact metric evidence."""
    base_url = _clean_base_url(settings.observability.prometheus_base_url)
    if not base_url:
        return ToolResult(success=False, data="", error="Prometheus base URL is not configured")
    if not str(query or "").strip():
        return ToolResult(success=False, data="", error="Prometheus query is required")

    params: dict[str, Any] = {"query": query}
    if time_:
        params["time"] = time_
    try:
        payload = _http_get_json(base_url, "/api/v1/query", params)
        status = payload.get("status")
        if status != "success":
            return ToolResult(success=False, data="", error=_api_error(payload, "Prometheus query failed"))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        result = data.get("result") if isinstance(data.get("result"), list) else []
        return ToolResult(
            success=True,
            data={
                "source": "prometheus",
                "query_type": "instant",
                "query": query,
                "time": time_ or "now",
                "result_type": data.get("resultType") or "",
                "series_count": len(result),
                "results": [_compact_prometheus_sample(item) for item in result[:20]],
                "summary": _summarize_prometheus_result(result),
            },
        )
    except Exception as exc:
        return ToolResult(success=False, data="", error=str(exc))


def prometheus_range_query(
    query: str,
    start: str = "",
    end: str = "",
    step: str = "60s",
    range_minutes: int | None = None,
) -> ToolResult:
    """Run a Prometheus range query over a bounded window."""
    base_url = _clean_base_url(settings.observability.prometheus_base_url)
    if not base_url:
        return ToolResult(success=False, data="", error="Prometheus base URL is not configured")
    if not str(query or "").strip():
        return ToolResult(success=False, data="", error="Prometheus query is required")

    safe_range = max(1, min(int(range_minutes or settings.observability.default_range_minutes or 30), 24 * 60))
    now = datetime.now(timezone.utc)
    start = start or str(int((now - timedelta(minutes=safe_range)).timestamp()))
    end = end or str(int(now.timestamp()))
    params = {"query": query, "start": start, "end": end, "step": step or "60s"}
    try:
        payload = _http_get_json(base_url, "/api/v1/query_range", params)
        if payload.get("status") != "success":
            return ToolResult(success=False, data="", error=_api_error(payload, "Prometheus range query failed"))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        result = data.get("result") if isinstance(data.get("result"), list) else []
        return ToolResult(
            success=True,
            data={
                "source": "prometheus",
                "query_type": "range",
                "query": query,
                "start": start,
                "end": end,
                "step": params["step"],
                "result_type": data.get("resultType") or "",
                "series_count": len(result),
                "results": [_compact_prometheus_series(item) for item in result[:20]],
                "summary": _summarize_prometheus_result(result),
            },
        )
    except Exception as exc:
        return ToolResult(success=False, data="", error=str(exc))


def loki_query(query: str, limit: int = 50, time_: str = "") -> ToolResult:
    """Run a Loki instant log query and return compact log evidence."""
    base_url = _clean_base_url(settings.observability.loki_base_url)
    if not base_url:
        return ToolResult(success=False, data="", error="Loki base URL is not configured")
    if not str(query or "").strip():
        return ToolResult(success=False, data="", error="Loki query is required")

    safe_limit = _safe_log_limit(limit)
    params: dict[str, Any] = {"query": query, "limit": safe_limit}
    if time_:
        params["time"] = time_
    try:
        payload = _http_get_json(base_url, "/loki/api/v1/query", params)
        if payload.get("status") != "success":
            return ToolResult(success=False, data="", error=_api_error(payload, "Loki query failed"))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        streams = data.get("result") if isinstance(data.get("result"), list) else []
        return ToolResult(
            success=True,
            data={
                "source": "loki",
                "query_type": "instant",
                "query": query,
                "limit": safe_limit,
                "stream_count": len(streams),
                "entries": _compact_loki_streams(streams, safe_limit),
                "summary": _summarize_loki_streams(streams),
            },
        )
    except Exception as exc:
        return ToolResult(success=False, data="", error=str(exc))


def loki_range_query(
    query: str,
    start: str = "",
    end: str = "",
    limit: int = 50,
    range_minutes: int | None = None,
) -> ToolResult:
    """Run a Loki range query over a bounded window."""
    base_url = _clean_base_url(settings.observability.loki_base_url)
    if not base_url:
        return ToolResult(success=False, data="", error="Loki base URL is not configured")
    if not str(query or "").strip():
        return ToolResult(success=False, data="", error="Loki query is required")

    safe_limit = _safe_log_limit(limit)
    safe_range = max(1, min(int(range_minutes or settings.observability.default_range_minutes or 30), 24 * 60))
    now_ns = int(time.time() * 1_000_000_000)
    start = start or str(now_ns - safe_range * 60 * 1_000_000_000)
    end = end or str(now_ns)
    params = {"query": query, "start": start, "end": end, "limit": safe_limit}
    try:
        payload = _http_get_json(base_url, "/loki/api/v1/query_range", params)
        if payload.get("status") != "success":
            return ToolResult(success=False, data="", error=_api_error(payload, "Loki range query failed"))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        streams = data.get("result") if isinstance(data.get("result"), list) else []
        return ToolResult(
            success=True,
            data={
                "source": "loki",
                "query_type": "range",
                "query": query,
                "start": start,
                "end": end,
                "limit": safe_limit,
                "stream_count": len(streams),
                "entries": _compact_loki_streams(streams, safe_limit),
                "summary": _summarize_loki_streams(streams),
            },
        )
    except Exception as exc:
        return ToolResult(success=False, data="", error=str(exc))


def _http_get_json(base_url: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    url = urljoin(base_url + "/", path.lstrip("/"))
    with httpx.Client(timeout=settings.observability.timeout) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Observability API returned non-object JSON")
    return payload


def _clean_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _api_error(payload: dict[str, Any], fallback: str) -> str:
    return str(payload.get("error") or payload.get("errorType") or fallback)


def _safe_log_limit(limit: int) -> int:
    max_limit = max(1, int(settings.observability.max_log_limit or 100))
    return max(1, min(int(limit or 50), max_limit))


def _compact_prometheus_sample(item: dict[str, Any]) -> dict[str, Any]:
    metric = item.get("metric") if isinstance(item.get("metric"), dict) else {}
    value = item.get("value") if isinstance(item.get("value"), list) else []
    return {
        "metric": metric,
        "timestamp": value[0] if len(value) > 0 else None,
        "value": value[1] if len(value) > 1 else None,
    }


def _compact_prometheus_series(item: dict[str, Any]) -> dict[str, Any]:
    metric = item.get("metric") if isinstance(item.get("metric"), dict) else {}
    values = item.get("values") if isinstance(item.get("values"), list) else []
    return {
        "metric": metric,
        "sample_count": len(values),
        "first": values[0] if values else None,
        "last": values[-1] if values else None,
    }


def _summarize_prometheus_result(result: list[Any]) -> str:
    if not result:
        return "Prometheus query returned no series"
    labels = []
    for item in result[:5]:
        metric = item.get("metric") if isinstance(item, dict) and isinstance(item.get("metric"), dict) else {}
        name = metric.get("__name__") or metric.get("job") or metric.get("service") or metric.get("instance") or "series"
        labels.append(str(name))
    return f"Prometheus returned {len(result)} series: {', '.join(labels)}"


def _compact_loki_streams(streams: list[Any], limit: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") if isinstance(stream.get("stream"), dict) else {}
        values = stream.get("values") if isinstance(stream.get("values"), list) else []
        for value in values:
            if not isinstance(value, list) or len(value) < 2:
                continue
            entries.append({"labels": labels, "timestamp": value[0], "line": _compact_line(value[1])})
            if len(entries) >= limit:
                return entries
    return entries


def _summarize_loki_streams(streams: list[Any]) -> str:
    entry_count = 0
    for stream in streams:
        if isinstance(stream, dict) and isinstance(stream.get("values"), list):
            entry_count += len(stream["values"])
    return f"Loki returned {entry_count} log entries across {len(streams)} streams"


def _compact_line(line: Any, max_chars: int = 300) -> str:
    text = " ".join(str(line or "").split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")
