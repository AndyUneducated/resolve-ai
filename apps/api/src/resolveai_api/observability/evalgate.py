"""EvalGate 集成（项目 1）— 自己产品吃自己狗粮。

通过 OTel trace + `core.usage.RunTrace` 拉到每条 ticket 的完整 Agent / Tool /
Guardrail 时间线，构造一份精简 summary 推给 EvalGate endpoint，做 online
regression（auto-resolution rate / P95 latency / tool-error / PII leak count）。

`push()` 在 `EVALGATE_ENDPOINT` 未配置时是 no-op（与 OTel 装配一致），并且
吞掉网络异常 —— EvalGate 不可用绝不能拖垮线上 ticket 处理。
"""

from __future__ import annotations

import logging
from typing import Any

from resolveai_api.config import get_settings
from resolveai_api.core.usage import RunTrace
from resolveai_api.eval.pricing import trace_cost_usd

logger = logging.getLogger(__name__)


def build_run_summary(
    trace: RunTrace,
    *,
    ticket_id: str,
    latency_ms: float,
    resolved: bool | None = None,
    score: float | None = None,
    blocked: bool = False,
    tool_error: bool | None = None,
    flags: list[str] | None = None,
) -> dict[str, Any]:
    """Flatten a `RunTrace` (+ outcome signals) into an EvalGate payload.

    The same shape feeds `scripts/regression_gate.py`, so the online and batch
    regression paths score identical metrics.
    """
    usage = {
        tier: {
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "calls": u.calls,
        }
        for tier, u in trace.usage_by_tier.items()
    }
    return {
        "ticket_id": ticket_id,
        "latency_ms": round(latency_ms, 2),
        "input_tokens": trace.total_input,
        "output_tokens": trace.total_output,
        "total_tokens": trace.total_tokens,
        "cost_usd": round(trace_cost_usd(trace), 6),
        "usage_by_tier": usage,
        "tool_calls": len(trace.tool_calls),
        "tool_error_count": trace.tool_error_count,
        "tool_error": (
            tool_error if tool_error is not None else trace.tool_error_count > 0
        ),
        "resolved": resolved,
        "score": score,
        "blocked": blocked,
        "flags": flags or [],
    }


class EvalGateClient:
    """HTTP client that pushes per-ticket trace summaries to EvalGate."""

    def __init__(self, *, endpoint: str | None = None, timeout_s: float = 5.0) -> None:
        self._endpoint = (
            endpoint if endpoint is not None else get_settings().evalgate_endpoint
        )
        self._timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self._endpoint)

    async def push(self, *, ticket_id: str, payload: dict[str, Any]) -> bool:
        """POST a trace summary to EvalGate. Returns True iff it was delivered.

        No-op (returns False) when no endpoint is configured. Network/HTTP
        errors are logged and swallowed so EvalGate downtime never blocks ticket
        handling.
        """
        if not self.enabled:
            return False
        url = self._endpoint.rstrip("/") + "/v1/runs"
        body = {"ticket_id": ticket_id, **payload}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
            return True
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("evalgate push failed (%s): %s", url, exc)
            return False
