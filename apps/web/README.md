# `resolveai-web` — Frontend

Next.js 15 (app router) + Tailwind CSS (no extra component library; Tailwind only, written by hand).

## Pages

| Route | Description | Status |
|---|---|:---:|
| `/` | Landing page | ✅ |
| `/chat` | Customer chat UI; consumes all 5 backend SSE event types | ✅ |
| `/dashboard` | Leader / manager view | TODO |
| `/admin` | MCP server config + RBAC | TODO |

### SSE events handled by `/chat`

| Event | UI |
|---|---|
| `agent_step` | Incremental agent replies + expandable tool trace + guardrail flag chips |
| `blocked` | Red alert bar with `layer` / `kind` attribution |
| `awaiting_approval` | HITL approval panel (approve / deny; after approve, resume-by-replay) |
| `human_owned` | Human-agent takeover banner (automation paused) |
| `done` | Run footer: tokens, cost ($), over-budget flag, per-layer guardrail latency |

Streaming has a fallback for a truncated last chunk (the trailing `done` event is not lost if the reader `break`s early).
In-flight requests are cancelled with `AbortController` on unmount.

## Dev

```bash
npm install
npm run dev
# http://localhost:3000
```

Depends on the backend at `http://localhost:8000`; override with `NEXT_PUBLIC_API_URL`.
