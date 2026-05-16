# `resolveai-web` — Frontend

Next.js 14 (app router) + Tailwind + shadcn-style components.

## 页面

- `/` — landing
- `/chat` — 客户聊天 UI，SSE 接收每个 Agent step + tool trace
- `/dashboard` (TODO) — leader 视角
- `/admin` (TODO) — MCP server 配置 + RBAC

## Dev

```bash
npm install
npm run dev
# http://localhost:3000
```

依赖后端 `http://localhost:8000`，可通过 `NEXT_PUBLIC_API_URL` 环境变量改。
