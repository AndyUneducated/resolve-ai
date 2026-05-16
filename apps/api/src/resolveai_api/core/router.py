"""Cost-aware Model Routing（决策 1）。

Triage = 小模型（Haiku / 4o-mini），专项 Agent = 大模型（Sonnet / 4o）。
单 ticket 端到端 LLM 成本控制在 ¢ 级，对应 Sierra outcome-based pricing 的 unit economics。
"""

from __future__ import annotations

from typing import Literal

from resolveai_api.config import get_settings

AgentTier = Literal["triage", "vertical"]


class ModelRouter:
    def __init__(self) -> None:
        self.settings = get_settings()

    def model_for(self, tier: AgentTier) -> str:
        if tier == "triage":
            return self.settings.triage_model
        return self.settings.vertical_model
