import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-8 px-6 py-16">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">
          ResolveAI · Adversarially-Hardened Multi-Agent Customer Support
        </h1>
        <p className="mt-2 text-sm text-foreground/70">
          Sierra / Decagon style · 4 vertical agents · MCP tool protocol · four layers of
          defense-in-depth guardrails
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2">
        <Card
          href="/chat?preset=triage"
          title="Triage Agent"
          desc="Intent classification + routing (via Haiku)"
        />
        <Card
          href="/chat?preset=billing"
          title="Billing Agent"
          desc="Refunds / subscription changes (Plan-and-Execute)"
        />
        <Card
          href="/chat?preset=technical"
          title="Technical Agent"
          desc="Bug / configuration support (hybrid retrieval)"
        />
        <Card
          href="/chat?preset=escalation"
          title="Escalation Agent"
          desc="Human handoff + Slack notification"
        />
      </section>

      <section className="rounded-lg border border-border bg-muted/40 p-6">
        <h2 className="text-lg font-medium">Current status and next steps</h2>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-foreground/80">
          <li>
            <span className="text-foreground/50">[Live]</span> <code>/chat</code> chat UI:
            streamed SSE agent_step events, per-step tool traces, guardrail flag chips, and
            final token/cost metrics
          </li>
          <li>
            <span className="text-foreground/40">[Planned]</span> <code>/dashboard</code>{" "}
            (leader view: ticket volume + auto-resolution rate)
          </li>
          <li>
            <span className="text-foreground/40">[Planned]</span> <code>/admin</code> (IT
            administration: MCP server configuration + RBAC)
          </li>
        </ul>
        <Link
          href="/chat"
          className="mt-4 inline-block rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background"
        >
          Open chat →
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
      <div className="mt-3 text-xs text-foreground/40">
        Open chat with a prefilled example →
      </div>
    </Link>
  );
}
