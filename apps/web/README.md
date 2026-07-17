# `resolveai-web` — Frontend

Next.js 15（app router）+ Tailwind CSS（无额外组件库，纯 Tailwind 手写）。

## 页面

| 路由 | 说明 | 状态 |
|---|---|:---:|
| `/` | 落地页（landing） | ✅ |
| `/chat` | 客户聊天 UI；消费后端 SSE 全部 5 种事件 | ✅ |
| `/dashboard` | 管理者（leader）视角 | TODO |
| `/admin` | MCP server 配置 + RBAC | TODO |

### `/chat` 处理的 SSE 事件

| 事件 | UI 呈现 |
|---|---|
| `agent_step` | 逐步渲染 Agent 回复 + 可展开的 tool trace + 护栏 flag chip |
| `blocked` | 红色告警条，附 `layer` / `kind` 归因 |
| `awaiting_approval` | HITL 审批面板（批准 / 拒绝，批准后 resume-by-replay） |
| `human_owned` | 人工坐席接管提示条（自动化暂停） |
| `done` | 本次运行页脚：tokens、成本（$）、over-budget 标记、各护栏层延迟 |

流式读取对终止分片做了兜底（末尾 `done` 事件不会因 reader 提前 `break` 而丢失），
组件卸载时通过 `AbortController` 取消在途请求。

## Dev

```bash
npm install
npm run dev
# http://localhost:3000
```

依赖后端 `http://localhost:8000`，可通过 `NEXT_PUBLIC_API_URL` 环境变量改。
