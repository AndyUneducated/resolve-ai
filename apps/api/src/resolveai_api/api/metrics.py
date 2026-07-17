"""`/metrics` — Prometheus scrape endpoint (M11).

Exposes the process-global counters/histograms recorded in
`observability/metrics.py`. Unauthenticated by design (same trust boundary as
`/healthz`); in production it would be bound to the internal network only.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from resolveai_api.observability import metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    body, content_type = metrics.render_latest()
    return Response(content=body, media_type=content_type)
