"""灌入演示数据：tenants / customers / 一批 mock tickets / FAQ 文档。

用法:
    uv run python scripts/seed_db.py
"""

from __future__ import annotations

import sys


def main() -> int:
    print("[seed] TODO: 连 Postgres 写入演示数据。")
    print("[seed] - 1 个 tenant: demo")
    print("[seed] - 5 个 customer: cust-001..005")
    print("[seed] - 20 条 mock ticket（覆盖 billing / technical / escalation）")
    print("[seed] - 50 条 FAQ + runbook（带 embedding）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
