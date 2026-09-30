"use client";

import {
  AGENT_LABELS,
  AGENT_PIPELINE,
  type AgentName,
  type RunStatus,
} from "@/lib/types";

interface SwarmStatusVisualizerProps {
  activeAgent: AgentName | null;
  completedAgents: AgentName[];
  runStatus: RunStatus;
  iterations: number;
  maxRetries: number;
}

function nodeState(
  agent: AgentName,
  activeAgent: AgentName | null,
  completedAgents: AgentName[],
  runStatus: RunStatus
): "pending" | "active" | "done" | "failed" {
  if (runStatus === "failed" && activeAgent === agent) return "failed";
  if (activeAgent === agent && (runStatus === "running" || runStatus === "connecting")) {
    return "active";
  }
  if (completedAgents.includes(agent) || runStatus === "completed") {
    // On completed, mark all pipeline nodes done if we reached the end.
    if (runStatus === "completed") return "done";
    if (completedAgents.includes(agent)) return "done";
  }
  return "pending";
}

export function SwarmStatusVisualizer({
  activeAgent,
  completedAgents,
  runStatus,
  iterations,
  maxRetries,
}: SwarmStatusVisualizerProps) {
  const showRetry = iterations > 1 || (iterations === 1 && activeAgent === "janitor" && completedAgents.includes("janitor"));
  const retryLevel =
    iterations >= maxRetries ? "critical" : iterations >= 2 ? "warn" : "info";

  return (
    <section className="panel">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Pipeline</p>
          <h2 className="panel-title">Agent Swarm Status</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip status={runStatus} />
          {iterations > 0 ? (
            <span
              className={[
                "badge",
                retryLevel === "critical"
                  ? "badge-rose"
                  : retryLevel === "warn"
                    ? "badge-amber"
                    : "badge-muted",
              ].join(" ")}
              title="Janitor self-heal attempts"
            >
              Retry {iterations}/{maxRetries}
              {showRetry && iterations > 1 ? " · healing" : ""}
            </span>
          ) : null}
        </div>
      </header>

      <ol className="grid gap-3 sm:grid-cols-4">
        {AGENT_PIPELINE.map((agent, index) => {
          const state = nodeState(
            agent,
            activeAgent,
            completedAgents,
            runStatus
          );
          return (
            <li key={agent} className="relative">
              {index < AGENT_PIPELINE.length - 1 ? (
                <span
                  aria-hidden
                  className="absolute left-[calc(100%+2px)] top-7 hidden h-px w-[calc(100%-4px)] bg-gradient-to-r from-white/20 to-transparent sm:block"
                />
              ) : null}
              <div
                className={[
                  "rounded-xl border px-3 py-4 transition duration-300",
                  state === "active"
                    ? "border-teal-400/60 bg-teal-400/10 shadow-[0_0_0_1px_rgba(45,212,191,0.15)]"
                    : state === "done"
                      ? "border-emerald-400/30 bg-emerald-400/5"
                      : state === "failed"
                        ? "border-rose-400/40 bg-rose-400/10"
                        : "border-white/10 bg-white/[0.02]",
                ].join(" ")}
              >
                <div className="mb-3 flex items-center justify-between">
                  <span className="font-[family-name:var(--font-geist-mono)] text-[10px] uppercase tracking-[0.18em] text-slate-500">
                    0{index + 1}
                  </span>
                  <NodePulse state={state} />
                </div>
                <p className="text-sm font-semibold text-slate-50">
                  {AGENT_LABELS[agent]}
                </p>
                <p className="mt-1 text-xs capitalize text-slate-400">
                  {state === "active"
                    ? "Active"
                    : state === "done"
                      ? "Complete"
                      : state === "failed"
                        ? "Failed"
                        : "Idle"}
                </p>
              </div>
            </li>
          );
        })}
      </ol>

      <p className="mt-4 text-xs text-slate-500">
        Flow: Profiler → Janitor → Supervisor → Statistician. Supervisor routes
        failed cleaning scripts back to Janitor (max {maxRetries} attempts).
      </p>
    </section>
  );
}

function StatusChip({ status }: { status: RunStatus }) {
  const map: Record<RunStatus, { label: string; className: string }> = {
    idle: { label: "Idle", className: "badge-muted" },
    uploading: { label: "Uploading", className: "badge-amber" },
    ready: { label: "Ready", className: "badge-teal" },
    connecting: { label: "Connecting", className: "badge-amber animate-pulse" },
    running: { label: "Running", className: "badge-teal animate-pulse" },
    completed: { label: "Completed", className: "badge-emerald" },
    failed: { label: "Failed", className: "badge-rose" },
  };
  const item = map[status];
  return <span className={`badge ${item.className}`}>{item.label}</span>;
}

function NodePulse({
  state,
}: {
  state: "pending" | "active" | "done" | "failed";
}) {
  const color =
    state === "active"
      ? "bg-teal-400"
      : state === "done"
        ? "bg-emerald-400"
        : state === "failed"
          ? "bg-rose-400"
          : "bg-slate-600";
  return (
    <span className="relative flex h-2.5 w-2.5">
      {state === "active" ? (
        <span
          className={`absolute inline-flex h-full w-full animate-ping rounded-full ${color} opacity-60`}
        />
      ) : null}
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${color}`} />
    </span>
  );
}
