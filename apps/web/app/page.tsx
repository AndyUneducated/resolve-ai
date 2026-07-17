import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-8 px-6 py-16">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">
          ResolveAI · Adversarially-Hardened Multi-Agent Customer Support
        </h1>
        <p className="mt-2 text-sm text-foreground/70">
          Sierra / Decagon 风格 · 4 个垂直 Agent · MCP 工具协议 · 四层 defense-in-depth guardrails
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2">
        <Card
          href="/chat?preset=triage"
          title="Triage Agent"
          desc="意图分类 + 路由 (走 Haiku)"
        />
        <Card
          href="/chat?preset=billing"
          title="Billing Agent"
          desc="退款 / 改订阅 (Plan-and-Execute)"
        />
        <Card
          href="/chat?preset=technical"
          title="Technical Agent"
          desc="bug / 配置帮助 (Hybrid retrieval)"
        />
        <Card
          href="/chat?preset=escalation"
          title="Escalation Agent"
          desc="转人工 + Slack notify"
        />
      </section>

      <section className="rounded-lg border border-border bg-muted/40 p-6">
        <h2 className="text-lg font-medium">现状与下一步</h2>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-foreground/80">
          <li>
            <span className="text-foreground/50">[已上线]</span> <code>/chat</code>{" "}
            聊天 UI：SSE 流式 agent_step、每步 tool trace、护栏 flag chip、结束带 token/成本
          </li>
          <li>
            <span className="text-foreground/40">[规划中]</span> <code>/dashboard</code>
            （leader 视角：ticket 流量 + auto-resolve rate）
          </li>
          <li>
            <span className="text-foreground/40">[规划中]</span> <code>/admin</code>
            （IT admin：MCP server 配置 + RBAC）
          </li>
        </ul>
        <Link
          href="/chat"
          className="mt-4 inline-block rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background"
        >
          打开聊天 →
        </Link>
      </section>
    </main>
  );
}

function Card({ href, title, desc }: { href: string; title: string; desc: string }) {
  return (
    <Link
      href={href}
      className="block rounded-lg border border-border p-4 transition hover:border-foreground/30 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20"
    >
      <div className="text-sm font-medium">{title}</div>
      <div className="mt-1 text-xs text-foreground/60">{desc}</div>
      <div className="mt-3 text-xs text-foreground/40">点击进入聊天（预填示例）→</div>
    </Link>
  );
}
