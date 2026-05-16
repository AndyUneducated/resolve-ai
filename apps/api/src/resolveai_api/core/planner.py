"""Planner — Plan-and-Execute（决策 1）。

业务 Agent 走 Plan-and-Execute（先生成多步计划再批量执行），
对比 ReAct 单步循环显著降低 chatty tool call。
Triage Agent 走轻量意图分类（不需要 multi-step plan）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Plan:
    steps: list[str] = field(default_factory=list)
    confidence: float = 0.0


class Planner:
    """TODO: 接 LLM (vertical_model) 做多步规划。"""

    async def plan(self, *, goal: str, context: dict[str, object]) -> Plan:
        """生成多步执行计划。"""
        # 占位
        return Plan(steps=[f"step-1 for goal: {goal}"], confidence=0.5)

    async def lightweight_classify(self, *, message: str) -> dict[str, object]:
        """Triage 用 — 一次 LLM 调用做意图分类 + 实体抽取。"""
        return {"intent": "billing", "entities": {}, "confidence": 0.5}
