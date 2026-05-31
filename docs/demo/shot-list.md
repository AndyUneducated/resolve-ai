# Demo 分镜清单 & Run Book（Milestone 8）

产出 3 分钟 demo 视频的精确命令。与 `narration.md` 配对使用 voiceover/caption 文本。

## 你将得到

- 四段 beats 的自动化、可重复录制（`apps/web/demo/output/*.webm`）— 无需手动点击。
- Recorder 捕获的两张静态 artifact 页：
  `apps/web/demo/metrics.html`（chaos P95 + ablation table）与
  `apps/web/demo/trace.html`（guardrail/agent trace，含 cross-tenant PermissionError）。

## 一次性 setup

```bash
make install                 # uv sync + npm install（添加 @playwright/test）
cd apps/web && npx playwright install chromium && cd ../..
```

## Step 1 — 产出数据

```bash
# 5K concurrent mock tickets -> reports/chaos/chaos_results.json（P95 gate）
make chaos

# （可选）Ollama 上真实 M7 ablation 数字；否则 demo 页
# 自动生成 fake-backend ablation，保证始终有内容：
uv run python scripts/eval_architecture.py --quick
```

## Step 2 — 生成 demo artifacts

```bash
make demo-assets             # -> apps/web/demo/{metrics.html, trace.html}
```

## Step 3 — 录制

Chat beats（1–2）需要 app 运行。若要 deterministic、快速 take，在 fake backend 下跑 API：

```bash
# terminal A — backend（即时 canned responses）
LLM_BACKEND=fake make api
# terminal B — frontend
make web
# terminal C — record（依赖 demo-assets）
DEMO_PACE_MS=8000 make demo-record
```

若要真实 model latency 的 authentic take，正常启动 API（Ollama 运行中的 `make api`）即可。

视频落在 `apps/web/demo/output/record-*/video.webm`。

> Web app 未运行时，recorder 仍从 artifact pages 捕获 beats 3–4 并产出视频（chat beats graceful skip）。

## Step 4 — 后期 & 发布

```bash
# convert +（可选）加 title card
ffmpeg -i apps/web/demo/output/record-*/video.webm docs/demo/resolveai-demo.mp4
```

上传 Loom / YouTube，按 `narration.md` 加 voiceover，将链接粘贴进 resume bullet（见 `docs/milestone-8-plan.md`）。

## Beat → artifact 映射

| Beat | Time | Source | Shows |
|---|---|---|---|
| 1 | 0:00-0:30 | `/chat?preset=billing` | Triage → Billing step cards（refund） |
| 2 | 0:30-1:30 | `/chat` + `trace.html` | Indirect-injection Layer 1 flag chip |
| 3 | 1:30-2:00 | `trace.html` | Cross-tenant `PermissionError` block |
| 4 | 2:00-3:00 | `metrics.html` | Chaos P95 gate + Architecture Ablation table |
