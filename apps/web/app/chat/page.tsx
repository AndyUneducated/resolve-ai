"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

type ToolCall = { step?: string; observation?: string; error?: string };
type AgentStep = {
  agent: string;
  content: string;
  flags: string[];
  toolCalls: ToolCall[];
};
type PendingApproval = {
  id: string;
  tool: string;
  capability: string;
  args: Record<string, unknown>;
  status: string;
};
type RunMetrics = {
  tokens?: number;
  costUsd?: number;
  overBudget: boolean;
  guardrailLatencyMs?: Record<string, number>;
};

/** sse-starlette uses CRLF. Normalize before splitting on `\n\n` to prevent merged events and JSON parse failures from trailing `\r`. */
function splitSseEvents(raw: string): { events: string[]; rest: string } {
  const normalized = raw.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  return { events: parts, rest };
}

/** Prefill the input when a home-page card links here with ?preset= for scenario-based demos. */
const PRESETS: Record<string, string> = {
  triage: "What types of issues can you help me with?",
  billing: "I was overcharged $99 last month. Please review the charge and request a refund.",
  technical: "Our API integration occasionally returns a 502. Can you help identify possible causes?",
  escalation: "This issue requires human assistance. Please escalate it and arrange a follow-up from a supervisor.",
};

function ChatPageContent() {
  const searchParams = useSearchParams();
  const presetApplied = useRef(false);
  const [input, setInput] = useState("");
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{ threadRef: string; items: PendingApproval[] } | null>(
    null,
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<RunMetrics | null>(null);
  // Keep the in-flight request abortable so an unmount (navigation) cancels the
  // stream instead of leaking a reader / calling setState on a dead component.
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (presetApplied.current) return;
    const key = searchParams.get("preset") ?? "";
    const text = PRESETS[key];
    if (text) {
      setInput(text);
      presetApplied.current = true;
    }
  }, [searchParams]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  async function send() {
    if (!input.trim() || streaming) return;
    setStreaming(true);
    setSteps([]);
    setError(null);
    setPending(null);
    setNotice(null);
    setMetrics(null);

    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const controller = new AbortController();
    controllerRef.current = controller;
    const timeoutId = window.setTimeout(() => controller.abort(), 180_000);

    try {
      const res = await fetch(`${apiUrl}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: input,
          customer_id: "demo-customer",
          tenant_id: "demo",
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        setError(`Request failed: HTTP ${res.status}${detail ? ` — ${detail.slice(0, 240)}` : ""}`);
        return;
      }
      if (!res.body) {
        setError("The response has no readable body.");
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const handleEvent = (evt: string) => {
        const lines = evt.split("\n");
        const eventLine = lines.find((l) => l.startsWith("event:"));
        const dataLine = lines.find((l) => l.startsWith("data:"));
        if (!dataLine) return;
        const eventName = eventLine?.replace(/^event:\s*/, "").trim() ?? "";
        const jsonPart = dataLine.replace(/^data:\s*/, "").trim();
        if (!jsonPart) return;
        try {
          const payload = JSON.parse(jsonPart) as Record<string, unknown>;
          if (eventName === "blocked") {
            const reason = Array.isArray(payload.reason)
              ? (payload.reason as string[]).join(", ")
              : String(payload.reason ?? "unknown reason");
            const detail = [
              payload.layer ? `layer=${String(payload.layer)}` : "",
              payload.kind ? `kind=${String(payload.kind)}` : "",
            ]
              .filter(Boolean)
              .join(", ");
            setError(
              `Request blocked by safety policy: ${reason}${detail ? ` (${detail})` : ""}`,
            );
            return;
          }
          if (eventName === "awaiting_approval") {
            setPending({
              threadRef: String(payload.thread_ref ?? ""),
              items: Array.isArray(payload.pending)
                ? (payload.pending as PendingApproval[])
                : [],
            });
            return;
          }
          if (eventName === "human_owned") {
            setNotice(
              `This conversation is now owned by a human agent (${String(payload.owner ?? "")}); automation has been paused.`,
            );
            return;
          }
          if (eventName === "done") {
            setMetrics({
              tokens: typeof payload.tokens === "number" ? payload.tokens : undefined,
              costUsd: typeof payload.cost_usd === "number" ? payload.cost_usd : undefined,
              overBudget: Boolean(payload.over_budget),
              guardrailLatencyMs:
                payload.guardrail_latency_ms && typeof payload.guardrail_latency_ms === "object"
                  ? (payload.guardrail_latency_ms as Record<string, number>)
                  : undefined,
            });
            return;
          }
          const agent = payload.agent;
          if (eventName === "agent_step" && typeof agent === "string") {
            setSteps((prev) => [
              ...prev,
              {
                agent,
                content: String(payload.content ?? ""),
                flags: Array.isArray(payload.flags) ? (payload.flags as string[]) : [],
                toolCalls: Array.isArray(payload.tool_calls)
                  ? (payload.tool_calls as ToolCall[])
                  : [],
              },
            ]);
          }
        } catch {
          /* ignore non-JSON / ping comments */
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        // Decode the terminal chunk too: some runtimes deliver the last bytes
        // (often the `done` event) together with done=true.
        if (value) buffer += decoder.decode(value, { stream: !done });
        const { events, rest } = splitSseEvents(buffer);
        buffer = rest;
        for (const evt of events) handleEvent(evt);
        if (done) {
          // Flush a trailing event that wasn't terminated by a blank line.
          if (buffer.trim()) handleEvent(buffer);
          break;
        }
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setError(
          "The request was canceled after waiting more than 3 minutes. The billing flow calls local Ollama several times. Confirm that `ollama serve` is running and that `ollama list` includes the models configured as TRIAGE_MODEL and VERTICAL_MODEL in .env. Also check the API terminal for errors."
        );
      } else {
        setError(
          e instanceof Error
            ? e.message
            : "Network error. Confirm that the backend is running and CORS allows this page's origin."
        );
      }
    } finally {
      window.clearTimeout(timeoutId);
      controllerRef.current = null;
      setStreaming(false);
    }
  }

  /** Approve/deny a parked destructive action; on approve, resume by replay. */
  async function decide(id: string, decision: "approve" | "deny") {
    if (streaming) return;
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    try {
      const res = await fetch(`${apiUrl}/api/v1/approvals/${id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, by: "web-operator" }),
      });
      if (!res.ok) {
        setError(`Approval failed: HTTP ${res.status}`);
        return;
      }
      setPending((prev) =>
        prev ? { ...prev, items: prev.items.filter((it) => it.id !== id) } : prev,
      );
      if (decision === "approve") await send(); // resume-by-replay
    } catch (e) {
      setError(e instanceof Error ? e.message : "Approval request failed");
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-12">
      <h1 className="text-2xl font-semibold">Chat with ResolveAI</h1>

      {error && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
          {error}
        </div>
      )}

      {notice && (
        <div className="rounded-md border border-sky-500/40 bg-sky-500/10 px-3 py-2 text-sm text-sky-200">
          {notice}
        </div>
      )}

      {pending && pending.items.length > 0 && (
        <div className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-4">
          <p className="text-sm font-medium text-amber-200">
            Human approval required: {pending.items.length} high-risk{" "}
            {pending.items.length === 1 ? "action is" : "actions are"} pending
          </p>
          <ul className="mt-3 space-y-3">
            {pending.items.map((it) => (
              <li key={it.id} className="rounded-md bg-background/60 p-3">
                <div className="font-mono text-xs text-foreground/80">
                  {it.tool}
                  <span className="ml-2 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] uppercase text-amber-200">
                    {it.capability}
                  </span>
                </div>
                <pre className="mt-1 overflow-x-auto rounded bg-background/80 p-2 text-[11px] text-foreground/60">
                  {JSON.stringify(it.args, null, 2)}
                </pre>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={() => decide(it.id, "approve")}
                    disabled={streaming}
                    className="rounded-md bg-emerald-600 px-3 py-1 text-xs text-white disabled:opacity-50"
                  >
                    Approve and continue
                  </button>
                  <button
                    type="button"
                    onClick={() => decide(it.id, "deny")}
                    disabled={streaming}
                    className="rounded-md bg-red-600 px-3 py-1 text-xs text-white disabled:opacity-50"
                  >
                    Deny
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-2">
        <input
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          placeholder="Describe your issue…"
          aria-label="Describe your issue"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={streaming}
        />
        <button
          type="button"
          onClick={send}
          disabled={streaming}
          aria-busy={streaming}
          aria-label={streaming ? "Processing" : "Send"}
          className="rounded-md bg-foreground px-4 py-2 text-sm text-background disabled:opacity-50"
        >
          {streaming ? "Thinking…" : "Send"}
        </button>
      </div>

      <div
        className="flex min-h-[12rem] flex-col gap-3 rounded-lg border border-border p-4"
        aria-live="polite"
        aria-busy={streaming}>
        {streaming && steps.length === 0 && (
          <div className="text-sm text-foreground/70">
            <p className="font-medium text-foreground/90">Processing…</p>
            <p className="mt-2 leading-relaxed">
              The first update is sent after the backend completes <strong>Triage</strong>. The
              billing flow then calls the vertical model several times (configured by{" "}
              <code className="rounded bg-muted px-1">VERTICAL_MODEL</code> in .env), which can
              take <strong>some time</strong> on a CPU.
            </p>
            <p className="mt-2 text-xs text-foreground/50">
              If no output appears, run <code className="rounded bg-muted px-1">ollama serve</code>{" "}
              in a terminal, then use <code className="rounded bg-muted px-1">ollama list</code>{" "}
              to confirm that the models configured in .env are installed.
            </p>
          </div>
        )}
        {!streaming && steps.length === 0 && (
          <div className="text-sm text-foreground/50">
            Agent steps will appear here after you send a message. Try “I was overcharged $99
            last month.”
          </div>
        )}
        {steps.map((s, i) => (
          <div key={`${s.agent}-${i}`} className="rounded-md bg-muted/60 p-3">
            <div className="text-xs uppercase tracking-wide text-foreground/50">{s.agent}</div>
            <div className="mt-1 whitespace-pre-wrap text-sm">{s.content}</div>
            {s.toolCalls?.length > 0 && (
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer text-foreground/50">
                  Tool trace ({s.toolCalls.length})
                </summary>
                <ul className="mt-1 space-y-1">
                  {s.toolCalls.map((tc, j) => (
                    <li
                      key={`${s.agent}-${i}-tc-${j}`}
                      className="rounded bg-background/60 px-2 py-1 font-mono text-[11px] text-foreground/70"
                    >
                      <span className="text-foreground/90">{tc.step ?? "tool"}</span>
                      {tc.error ? (
                        <span className="text-red-400"> → error: {tc.error}</span>
                      ) : tc.observation ? (
                        <span> → {String(tc.observation).slice(0, 240)}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {s.flags?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {s.flags.map((f, k) => (
                  <span
                    key={`${s.agent}-${i}-flag-${k}`}
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

      {metrics && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-foreground/70">
          <span className="font-medium text-foreground/90">This run</span>
          <span>
            tokens: <code className="rounded bg-background/60 px-1">{metrics.tokens ?? "—"}</code>
          </span>
          <span>
            cost:{" "}
            <code className="rounded bg-background/60 px-1">
              {typeof metrics.costUsd === "number" ? `$${metrics.costUsd.toFixed(4)}` : "—"}
            </code>
          </span>
          {metrics.overBudget && (
            <span className="rounded bg-red-500/20 px-2 py-0.5 text-red-300">over budget</span>
          )}
          {metrics.guardrailLatencyMs &&
            Object.entries(metrics.guardrailLatencyMs).map(([layer, ms]) => (
              <span key={`gl-${layer}`}>
                {layer}: <code className="rounded bg-background/60 px-1">{ms}ms</code>
              </span>
            ))}
        </div>
      )}
    </main>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-12">
          <h1 className="text-2xl font-semibold">Chat with ResolveAI</h1>
          <p className="text-sm text-foreground/50">Loading…</p>
        </main>
      }
    >
      <ChatPageContent />
    </Suspense>
  );
}
