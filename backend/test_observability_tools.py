"""Regression checks for Prometheus/Loki read-only tools."""

import os
from pathlib import Path

import httpx

os.chdir(Path(__file__).parent)

from app.config import settings
from app.mcp_tools import observability_tools


def test_prometheus_query_returns_metric_evidence_summary() -> None:
    original_url = settings.observability.prometheus_base_url
    original_get = httpx.Client.get

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"__name__": "up", "job": "nginx", "instance": "vm-1"}, "value": [1710000000, "1"]}
                    ],
                },
            }

    def fake_get(self, url, params=None):
        assert url == "http://prom.example/api/v1/query"
        assert params["query"] == "up"
        return FakeResponse()

    try:
        settings.observability.prometheus_base_url = "http://prom.example"
        httpx.Client.get = fake_get
        result = observability_tools.prometheus_query("up")
    finally:
        settings.observability.prometheus_base_url = original_url
        httpx.Client.get = original_get

    assert result.success is True
    assert result.data["source"] == "prometheus"
    assert result.data["series_count"] == 1
    assert "Prometheus returned 1 series" in result.data["summary"]


def test_loki_range_query_returns_compact_log_entries() -> None:
    original_url = settings.observability.loki_base_url
    original_get = httpx.Client.get

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "stream": {"service": "nginx"},
                            "values": [["1710000000000000000", "upstream connect failed"]],
                        }
                    ]
                },
            }

    def fake_get(self, url, params=None):
        assert url == "http://loki.example/loki/api/v1/query_range"
        assert params["query"] == '{service="nginx"} |= "502"'
        return FakeResponse()

    try:
        settings.observability.loki_base_url = "http://loki.example"
        httpx.Client.get = fake_get
        result = observability_tools.loki_range_query('{service="nginx"} |= "502"', limit=10)
    finally:
        settings.observability.loki_base_url = original_url
        httpx.Client.get = original_get

    assert result.success is True
    assert result.data["source"] == "loki"
    assert result.data["entries"][0]["line"] == "upstream connect failed"
    assert "Loki returned 1 log entries" in result.data["summary"]


def test_unconfigured_observability_tools_fail_truthfully() -> None:
    original_prom = settings.observability.prometheus_base_url
    original_loki = settings.observability.loki_base_url
    try:
        settings.observability.prometheus_base_url = ""
        settings.observability.loki_base_url = ""
        prom = observability_tools.prometheus_query("up")
        loki = observability_tools.loki_query('{service="nginx"}')
    finally:
        settings.observability.prometheus_base_url = original_prom
        settings.observability.loki_base_url = original_loki

    assert prom.success is False
    assert "not configured" in prom.error
    assert loki.success is False
    assert "not configured" in loki.error


def main() -> None:
    test_prometheus_query_returns_metric_evidence_summary()
    test_loki_range_query_returns_compact_log_entries()
    test_unconfigured_observability_tools_fail_truthfully()
    print("observability tools regression OK")


if __name__ == "__main__":
    main()
