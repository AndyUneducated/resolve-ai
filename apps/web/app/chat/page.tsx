"use client";

import { useState } from "react";

type AgentStep = { agent: string; content: string; flags: string[] };

export default function ChatPage() {
  const [input, setInput] = useState("");
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [streaming, setStreaming] = useState(false);

  async function send() {
    if (!input.trim() || streaming) return;
    setStreaming(true);
    setSteps([]);

    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const res = await fetch(`${apiUrl}/api/v1/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: input,
        customer_id: "demo-customer",
        tenant_id: "demo",
      }),
    });

    if (!res.ok || !res.body) {
      setStreaming(false);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";
      for (const evt of events) {
        const dataLine = evt.split("\n").find((l) => l.startsWith("data: "));
        if (!dataLine) continue;
        try {
          const payload = JSON.parse(dataLine.slice(6));
          if (payload.agent) {
            setSteps((prev) => [...prev, payload as AgentStep]);
          }
        } catch {
          /* ignore non-JSON events */
        }
      }
    }
    setStreaming(false);
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-12">
      <h1 className="text-2xl font-semibold">Chat with ResolveAI</h1>

      <div className="flex flex-col gap-3 rounded-lg border border-border p-4">
        {steps.length === 0 && (
          <div className="text-sm text-foreground/50">还没有消息。试试「我上个月被多扣了 $99」。</div>
        )}
        {steps.map((s, i) => (
          <div key={i} className="rounded-md bg-muted/60 p-3">
            <div className="text-xs uppercase tracking-wide text-foreground/50">{s.agent}</div>
            <div className="mt-1 whitespace-pre-wrap text-sm">{s.content}</div>
            {s.flags?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {s.flags.map((f) => (
                  <span
                    key={f}
                    className="rounded bg-yellow-200/30 px-2 py-0.5 text-[10px] text-yellow-900 dark:text-yellow-200"
                  >
                    {f}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="flex gap-2">
        <input
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          placeholder="描述你的问题…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={streaming}
        />
        <button
          onClick={send}
          disabled={streaming}
          className="rounded-md bg-foreground px-4 py-2 text-sm text-background disabled:opacity-50"
        >
          {streaming ? "Thinking…" : "发送"}
        </button>
      </div>
    </main>
  );
}
