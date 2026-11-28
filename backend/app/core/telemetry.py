"""
LLD v1.3 Items 57 & 58: OpenTelemetry-Compatible Span Tracing & Operational Metrics.
Propagates trace/correlation IDs across WS, ASR, RAG, LLM, DB, and Workers without attaching PII/audio.
"""
import os
import time
import uuid
from contextlib import contextmanager
from typing import Dict, Any, List, Optional


class MetricsRegistry:
    def __init__(self):
        self.counters: Dict[str, int] = {
            "ws_active_connections": 0,
            "ws_reconnects": 0,
            "audio_dropped_frames": 0,
            "prediction_survived": 0,
            "prediction_superseded": 0,
            "llm_fallbacks": 0,
            "circuit_breaker_trips": 0,
        }
        self.histograms: Dict[str, List[float]] = {
            "asr_latency_ms": [],
            "retrieval_latency_ms": [],
            "llm_ttft_ms": [],
            "llm_completion_ms": [],
        }

    def incr(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def observe(self, name: str, value_ms: float) -> None:
        bucket = self.histograms.setdefault(name, [])
        bucket.append(round(value_ms, 2))
        if len(bucket) > 1000:
            del bucket[: len(bucket) - 1000]

    def p95(self, name: str) -> float:
        vals = sorted(self.histograms.get(name, []))
        if not vals:
            return 0.0
        idx = min(len(vals) - 1, int(0.95 * len(vals)))
        return vals[idx]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "p95_ms": {k: self.p95(k) for k in self.histograms},
        }


class SpanTracer:
    def __init__(self):
        self.otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        self.recent_spans: List[Dict[str, Any]] = []

    @contextmanager
    def start_span(self, name: str, trace_id: Optional[str] = None, attributes: Optional[Dict[str, Any]] = None):
        span_id = uuid.uuid4().hex[:16]
        t_start = time.perf_counter()
        status = "ok"
        try:
            yield {"trace_id": trace_id or uuid.uuid4().hex, "span_id": span_id, "name": name}
        except Exception:
            status = "error"
            raise
        finally:
            duration_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
            record = {
                "name": name,
                "trace_id": trace_id,
                "span_id": span_id,
                "duration_ms": duration_ms,
                "status": status,
                "attributes": {
                    k: v for k, v in (attributes or {}).items()
                    if k not in ("raw_audio", "transcript", "prompt", "answer")
                },
            }
            self.recent_spans.append(record)
            if len(self.recent_spans) > 200:
                del self.recent_spans[: len(self.recent_spans) - 200]


metrics_registry = MetricsRegistry()
span_tracer = SpanTracer()
