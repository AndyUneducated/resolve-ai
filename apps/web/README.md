# `resolveai-web` — Frontend

Next.js 15 (app router) + Tailwind + shadcn-style components.

## 页面

| 路由 | 说明 | 状态 |
|---|---|:---:|
| `/` | 落地页（landing） | ✅ |
| `/chat` | 客户聊天 UI；通过 SSE 接收每个 Agent step + 工具调用 trace | ✅ |
| `/dashboard` | 管理者（leader）视角 | TODO |
| `/admin` | MCP server 配置 + RBAC | TODO |

## Dev

```bash
npm install
npm run dev
# http://localhost:3000
```

依赖后端 `http://localhost:8000`，可通过 `NEXT_PUBLIC_API_URL` 环境变量改。
