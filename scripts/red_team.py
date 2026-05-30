"""Compatibility shim.

`scripts/red_team.py` now delegates to `scripts/eval_adversarial.py`.
Use `scripts/eval_adversarial.py` directly for full control.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    script = Path(__file__).with_name("eval_adversarial.py")
    cmd = [sys.executable, str(script), "--quick", "--configs", "baseline"]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
