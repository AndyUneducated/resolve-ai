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

/** sse-starlette 使用 CRLF；用 `\n\n` 切事件前需归一化，否则会整包粘在一起、JSON 带 `\r` 解析失败。 */
function splitSseEvents(raw: string): { events: string[]; rest: string } {
  const normalized = raw.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  return { events: parts, rest };
}

/** 首页卡片带 ?preset= 跳转时预填输入框，便于按场景试用。 */
const PRESETS: Record<string, string> = {
  triage: "我想先了解一下你们主要能处理哪些类型的问题？",
  billing: "我上个月被多扣了 $99，请帮我查扣款记录并申请退款。",
  technical: "我们集成的 API 偶尔返回 502，能帮我看看可能原因吗？",
  escalation: "这个问题必须人工处理，请帮我升级并安排主管回访。",
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

  useEffect(() => {
    if (presetApplied.current) return;
    const key = searchParams.get("preset") ?? "";
    const text = PRESETS[key];
    if (text) {
      setInput(text);
      presetApplied.current = true;
    }
  }, [searchParams]);

  async function send() {
    if (!input.trim() || streaming) return;
    setStreaming(true);
    setSteps([]);
    setError(null);
    setPending(null);
    setNotice(null);

    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const controller = new AbortController();
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
        setError(`请求失败 HTTP ${res.status}${detail ? ` — ${detail.slice(0, 240)}` : ""}`);
        return;
      }
      if (!res.body) {
        setError("响应没有可读取的正文（body）。");
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, rest } = splitSseEvents(buffer);
        buffer = rest;
        for (const evt of events) {
          const eventLine = evt.split("\n").find((l) => l.startsWith("event:"));
          const dataLine = evt.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          const eventName = eventLine?.replace(/^event:\s*/, "").trim() ?? "";
          const jsonPart = dataLine.replace(/^data:\s*/, "").trim();
          if (!jsonPart) continue;
          try {
            const payload = JSON.parse(jsonPart) as Record<string, unknown>;
            if (eventName === "blocked") {
              const reason = Array.isArray(payload.reason)
                ? (payload.reason as string[]).join(", ")
                : String(payload.reason ?? "unknown reason");
              setError(`Request blocked by safety policy: ${reason}`);
              continue;
            }
            if (eventName === "awaiting_approval") {
              setPending({
                threadRef: String(payload.thread_ref ?? ""),
                items: Array.isArray(payload.pending)
                  ? (payload.pending as PendingApproval[])
                  : [],
              });
              continue;
            }
            if (eventName === "human_owned") {
              setNotice(
                `该会话已被人工坐席接管（${String(payload.owner ?? "")}），自动化已暂停。`,
              );
              continue;
            }
            const agent = payload.agent;
            if (eventName === "agent_step" && typeof agent === "string") {
              setSteps((prev) => [
                ...prev,
                {
                  agent,
                  content: String(payload.content ?? ""),
                  flags: Array.isArray(payload.flags)
                    ? (payload.flags as string[])
                    : [],
                  toolCalls: Array.isArray(payload.tool_calls)
                    ? (payload.tool_calls as ToolCall[])
                    : [],
                },
              ]);
            }
          } catch {
            /* ignore non-JSON / ping comments */
          }
        }
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setError(
          "等待超过 3 分钟已中止。计费链路会多次调用本地 Ollama，请确认本机已运行 ollama serve，且 ollama list 中已有 .env 里 TRIAGE_MODEL / VERTICAL_MODEL 对应的模型；同时查看运行 API 的终端是否有报错。"
        );
      } else {
        setError(
          e instanceof Error ? e.message : "网络错误（请确认后端已启动且 CORS 允许本页来源）"
        );
      }
    } finally {
      window.clearTimeout(timeoutId);
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
        setError(`审批失败 HTTP ${res.status}`);
        return;
      }
      setPending((prev) =>
        prev ? { ...prev, items: prev.items.filter((it) => it.id !== id) } : prev,
      );
      if (decision === "approve") await send(); // resume-by-replay
    } catch (e) {
      setError(e instanceof Error ? e.message : "审批请求失败");
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
            需人工审批（Human-in-the-Loop）：{pending.items.length} 个高风险动作已挂起
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
                    批准并继续
                  </button>
                  <button
                    type="button"
                    onClick={() => decide(it.id, "deny")}
                    disabled={streaming}
                    className="rounded-md bg-red-600 px-3 py-1 text-xs text-white disabled:opacity-50"
                  >
                    拒绝
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
          placeholder="描述你的问题…"
          aria-label="描述你的问题"
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
          aria-label={streaming ? "处理中" : "发送"}
          className="rounded-md bg-foreground px-4 py-2 text-sm text-background disabled:opacity-50"
        >
          {streaming ? "Thinking…" : "发送"}
        </button>
      </div>

      <div
        className="flex min-h-[12rem] flex-col gap-3 rounded-lg border border-border p-4"
        aria-live="polite"
        aria-busy={streaming}>
        {streaming && steps.length === 0 && (
          <div className="text-sm text-foreground/70">
            <p className="font-medium text-foreground/90">正在处理…</p>
            <p className="mt-2 leading-relaxed">
              首条内容要等后端跑完 <strong>Triage</strong> 后才会推送；进入计费后还会多次调用垂直模型（默认与 .env 中{" "}
              <code className="rounded bg-muted px-1">VERTICAL_MODEL</code> 一致），在 CPU 上仍可能需<strong>较久</strong>。
            </p>
            <p className="mt-2 text-xs text-foreground/50">
              若一直无输出：在终端执行 <code className="rounded bg-muted px-1">ollama serve</code>，并{" "}
              <code className="rounded bg-muted px-1">ollama list</code> 确认已有 .env 中配置的模型名称。
            </p>
          </div>
        )}
        {!streaming && steps.length === 0 && (
          <div className="text-sm text-foreground/50">
            发送后，Agent 步骤会显示在这里。可试「我上个月被多扣了 $99」。
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
    </main>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-12">
          <h1 className="text-2xl font-semibold">Chat with ResolveAI</h1>
          <p className="text-sm text-foreground/50">加载中…</p>
        </main>
      }
    >
      <ChatPageContent />
    </Suspense>
  );
}
