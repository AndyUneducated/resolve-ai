"""Generate M5 adversarial evaluation reports from raw JSONL rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from resolveai_api.guardrails.eval_scoring import build_summary, load_jsonl, render_markdown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render eval summary tables from eval JSONL.")
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to reports/eval_<timestamp>.jsonl produced by eval_adversarial.py",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional JSON summary output path. Defaults next to input.",
    )
    parser.add_argument(
        "--md-output",
        type=Path,
        default=None,
        help="Optional markdown summary output path. Defaults next to input.",
    )
    return parser.parse_args()


def _default_output_paths(input_path: Path) -> tuple[Path, Path]:
    stem = input_path.with_suffix("")
    return stem.with_suffix(".json"), stem.with_suffix(".md")


def main() -> int:
    args = parse_args()
    rows = load_jsonl(args.input)
    summary = build_summary(rows)

    default_json, default_md = _default_output_paths(args.input)
    json_output = args.json_output or default_json
    md_output = args.md_output or default_md
    json_output.parent.mkdir(parents=True, exist_ok=True)

    with json_output.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
    with md_output.open("w", encoding="utf-8") as handle:
        handle.write(render_markdown(summary))

    print(f"[eval-report] wrote JSON summary: {json_output}")
    print(f"[eval-report] wrote markdown report: {md_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
