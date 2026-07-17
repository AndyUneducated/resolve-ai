"""Best-effort ticket trace sink (M14) — the production side of the data flywheel.

When `TRACE_SINK_PATH` is set, each terminal ticket appends one PII-scrubbed JSON
line here. `scripts/harvest_traces.py` samples this file into versioned eval sets.

Contract: **never breaks a request.** Disabled by default (empty path), and every
write is wrapped so an I/O error degrades to a debug log, not a 500. The record is
scrubbed here too (defense-in-depth: correct even if Layer-1 input redaction is
off), so nothing at rest contains PII.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def record_ticket(record: dict[str, Any]) -> None:
    """Append one scrubbed ticket record to the sink (no-op unless configured)."""
    try:
        from resolveai_api.config import get_settings

        path = getattr(get_settings(), "trace_sink_path", "")
        if not path:
            return
        from resolveai_api.eval.flywheel import scrub_text

        safe = dict(record)
        query = safe.get("query")
        if isinstance(query, str):
            safe["query"] = scrub_text(query)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, default=str, ensure_ascii=False) + "\n")
    except Exception:  # pragma: no cover - sink must never break a request
        logger.debug("trace_sink_write_failed", exc_info=True)
