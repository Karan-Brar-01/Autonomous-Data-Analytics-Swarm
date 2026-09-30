"use client";

import { useEffect, useRef } from "react";
import { AGENT_LABELS, type AgentName, type TerminalLine } from "@/lib/types";

interface LiveTerminalProps {
  lines: TerminalLine[];
  isRunning?: boolean;
  onClear?: () => void;
}

export function LiveTerminal({ lines, isRunning, onClear }: LiveTerminalProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <section className="panel flex min-h-[340px] flex-col">
      <header className="mb-3 flex items-center justify-between gap-3">
        <div>
          <p className="eyebrow">Stream</p>
          <h2 className="panel-title">Live Terminal</h2>
        </div>
        <div className="flex items-center gap-2">
          {isRunning ? (
            <span className="badge badge-teal animate-pulse">Live</span>
          ) : (
            <span className="badge badge-muted">Idle</span>
          )}
          {onClear ? (
            <button
              type="button"
              onClick={onClear}
              className="rounded-md border border-white/10 px-2.5 py-1 text-xs text-slate-300 transition hover:border-white/25 hover:text-white"
            >
              Clear
            </button>
          ) : null}
        </div>
      </header>

      <div
        ref={scrollerRef}
        className="terminal-surface flex-1 overflow-auto rounded-xl border border-white/5 p-4 font-[family-name:var(--font-geist-mono)] text-[12px] leading-relaxed"
      >
        {lines.length === 0 ? (
          <p className="text-slate-500">
            Waiting for swarm output… code generation, container stdout/stderr,
            and self-heal traces will appear here.
          </p>
        ) : (
          <ul className="space-y-2">
            {lines.map((line) => (
              <li key={line.id} className="group">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="text-slate-600">
                    {new Date(line.ts).toLocaleTimeString()}
                  </span>
                  {line.agent ? (
                    <span className="text-teal-500/90">
                      [{AGENT_LABELS[line.agent as AgentName] || line.agent}]
                    </span>
                  ) : null}
                  <span className={kindClass(line.kind)}>
                    {kindPrefix(line.kind)}
                  </span>
                </div>
                <pre
                  className={[
                    "mt-1 whitespace-pre-wrap break-words",
                    kindTextClass(line.kind),
                  ].join(" ")}
                >
                  {line.text}
                </pre>
              </li>
            ))}
          </ul>
        )}
        {isRunning ? (
          <p className="mt-3 animate-pulse text-teal-500/80">▋</p>
        ) : null}
      </div>
    </section>
  );
}

function kindPrefix(kind: TerminalLine["kind"]): string {
  switch (kind) {
    case "code":
      return "code";
    case "stdout":
      return "stdout";
    case "stderr":
      return "stderr";
    case "error":
      return "error";
    case "system":
      return "sys";
    default:
      return "log";
  }
}

function kindClass(kind: TerminalLine["kind"]): string {
  switch (kind) {
    case "code":
      return "text-sky-400";
    case "stdout":
      return "text-emerald-400";
    case "stderr":
      return "text-amber-400";
    case "error":
      return "text-rose-400";
    case "system":
      return "text-slate-400";
    default:
      return "text-slate-500";
  }
}

function kindTextClass(kind: TerminalLine["kind"]): string {
  switch (kind) {
    case "code":
      return "text-sky-100/90";
    case "stdout":
      return "text-emerald-100/90";
    case "stderr":
      return "text-amber-100/90";
    case "error":
      return "text-rose-200";
    default:
      return "text-slate-200";
  }
}
