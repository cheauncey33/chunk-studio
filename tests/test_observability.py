from __future__ import annotations

from app.observability import MetricsRegistry


def test_metrics_registry_renders_histogram_counter_and_gauge() -> None:
    registry = MetricsRegistry(buckets=(0.1, 1.0))
    registry.increment("requests_total", route="/api/search", status="2xx")
    registry.gauge_add("requests_in_flight", 1)
    registry.gauge_add("requests_in_flight", -1)
    registry.observe("request_duration_seconds", 0.25, route="/api/search")

    output = registry.render_prometheus()

    assert 'requests_total{route="/api/search",status="2xx"} 1' in output
    assert "requests_in_flight 0" in output
    assert 'request_duration_seconds_bucket{le="0.1",route="/api/search"} 0' in output
    assert 'request_duration_seconds_bucket{le="1",route="/api/search"} 1' in output
    assert 'request_duration_seconds_count{route="/api/search"} 1' in output


def test_stage_records_error_before_reraising() -> None:
    registry = MetricsRegistry()
    try:
        with registry.stage("rerank", component="retrieval"):
            raise RuntimeError("failed")
    except RuntimeError:
        pass

    output = registry.render_prometheus()
    assert 'chunk_studio_stage_total{component="retrieval",stage="rerank",status="error"} 1' in output
