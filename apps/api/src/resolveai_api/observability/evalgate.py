"""EvalGate 集成（项目 1）— 自己产品吃自己狗粮。

通过 OTel trace 拉到每条 ticket 的完整 Agent / Tool / Guardrail 时间线，
做 online regression（auto-resolution rate / P95 latency / PII leak count）。
"""

from __future__ import annotations


class EvalGateClient:
    """TODO: HTTP 推 trace summary 给 EvalGate endpoint。"""

    async def push(self, *, ticket_id: str, payload: dict[str, object]) -> None: ...
