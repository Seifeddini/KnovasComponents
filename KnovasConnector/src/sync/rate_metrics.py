"""Client-side ingest rate metrics (parity with SemantixBenchmark rate_curve)."""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IngestRateMetrics:
    chars_transmitted: int = 0
    ingestion_requests_sent: int = 0
    errors: int = 0
    rate_limited_count: int = 0
    latencies_seconds: list[float] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    def record_request(
        self,
        *,
        chars: int,
        latency_seconds: float | None,
        http_status: int,
        success: bool,
    ) -> None:
        self.ingestion_requests_sent += 1
        self.chars_transmitted += max(chars, 0)
        if http_status == 429:
            self.rate_limited_count += 1
        if not success:
            self.errors += 1
        if latency_seconds is not None and latency_seconds >= 0:
            self.latencies_seconds.append(latency_seconds)

    def as_dict(self) -> dict[str, Any]:
        elapsed = max(time.monotonic() - self.started_at, 1e-6)
        total = self.ingestion_requests_sent
        error_rate = (self.errors / total) if total else 0.0
        rate_limited_rate = (self.rate_limited_count / total) if total else 0.0
        latencies = sorted(self.latencies_seconds)
        p50 = statistics.median(latencies) if latencies else None
        p95 = latencies[int(0.95 * (len(latencies) - 1))] if len(latencies) > 1 else p50
        return {
            "chars_transmitted": self.chars_transmitted,
            "achieved_chars_per_second": self.chars_transmitted / elapsed,
            "ingestion_requests_sent": self.ingestion_requests_sent,
            "error_rate": error_rate,
            "rate_limited_rate": rate_limited_rate,
            "latency_p50_seconds": p50,
            "latency_p95_seconds": p95,
        }
