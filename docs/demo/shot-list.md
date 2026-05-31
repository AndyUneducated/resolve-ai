# Demo Shot List & Run Book (Milestone 8)

Exact commands to produce the 3-minute demo video. Pair with
`narration.md` for the voiceover/caption text.

## What you get

- An automated, repeatable recording (`apps/web/demo/output/*.webm`) of all four
  beats — no manual clicking required.
- Two static artifact pages the recorder captures:
  `apps/web/demo/metrics.html` (chaos P95 + ablation table) and
  `apps/web/demo/trace.html` (guardrail/agent trace incl. the cross-tenant
  PermissionError).

## One-time setup

```bash
make install                 # uv sync + npm install (adds @playwright/test)
cd apps/web && npx playwright install chromium && cd ../..
```

## Step 1 — Produce the data

```bash
# 5K concurrent mock tickets -> reports/chaos/chaos_results.json (P95 gate)
make chaos

# (optional) real M7 ablation numbers on Ollama; otherwise the demo page
# auto-generates a fake-backend ablation so it is always populated:
uv run python scripts/eval_architecture.py --quick
```

## Step 2 — Generate the demo artifacts

```bash
make demo-assets             # -> apps/web/demo/{metrics.html, trace.html}
```

## Step 3 — Record

The chat beats (1-2) need the app running. For a deterministic, fast take, run
the API under the fake backend:

```bash
# terminal A — backend (instant canned responses)
LLM_BACKEND=fake make api
# terminal B — frontend
make web
# terminal C — record (depends on demo-assets)
DEMO_PACE_MS=8000 make demo-record
```

For an authentic take with real model latency, start the API normally
(`make api` with Ollama running) instead.

The video lands at `apps/web/demo/output/record-*/video.webm`.

> If the web app isn't running, the recorder still captures beats 3-4 from the
> artifact pages and produces a video (chat beats are skipped gracefully).

## Step 4 — Post-process & publish

```bash
# convert + (optionally) add a title card
ffmpeg -i apps/web/demo/output/record-*/video.webm docs/demo/resolveai-demo.mp4
```

Upload to Loom / YouTube, add voiceover from `narration.md`, and paste the link
into the resume bullet (see `docs/milestone-8-plan.md`).

## Beat → artifact map

| Beat | Time | Source | Shows |
|---|---|---|---|
| 1 | 0:00-0:30 | `/chat?preset=billing` | Triage → Billing step cards (refund) |
| 2 | 0:30-1:30 | `/chat` + `trace.html` | Indirect-injection Layer 1 flag chip |
| 3 | 1:30-2:00 | `trace.html` | Cross-tenant `PermissionError` block |
| 4 | 2:00-3:00 | `metrics.html` | Chaos P95 gate + Architecture Ablation table |
