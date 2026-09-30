"use client";

import type { DistributionRow, MissingAlert } from "@/lib/types";

interface DataInsightsPanelProps {
  runStatus: string;
  missingAlerts: MissingAlert[];
  statisticalSummary: Record<string, unknown>;
  schemaInfo: Record<string, unknown>;
}

function asDistributions(summary: Record<string, unknown>): DistributionRow[] {
  const raw = summary.distributions;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const row = item as Record<string, unknown>;
      const column = String(row.column ?? "");
      const bestFit = String(row.best_fit ?? row.bestFit ?? "unknown");
      if (!column) return null;
      return {
        column,
        best_fit: bestFit,
        details:
          row.details && typeof row.details === "object"
            ? (row.details as Record<string, unknown>)
            : undefined,
      };
    })
    .filter(Boolean) as DistributionRow[];
}

function asCorrelation(summary: Record<string, unknown>): {
  columns: string[];
  matrix: number[][];
} | null {
  const corr = summary.correlations;
  if (!corr || typeof corr !== "object") return null;
  const obj = corr as Record<string, unknown>;
  const columns = Array.isArray(obj.columns)
    ? obj.columns.map((c) => String(c))
    : [];
  const matrix = Array.isArray(obj.matrix) ? (obj.matrix as number[][]) : [];
  if (!columns.length || !matrix.length) return null;
  return { columns, matrix };
}

export function DataInsightsPanel({
  runStatus,
  missingAlerts,
  statisticalSummary,
  schemaInfo,
}: DataInsightsPanelProps) {
  const distributions = asDistributions(statisticalSummary);
  const correlation = asCorrelation(statisticalSummary);
  const notes = Array.isArray(statisticalSummary.notes)
    ? statisticalSummary.notes.map(String)
    : [];
  const hasStats = Object.keys(statisticalSummary).length > 0;
  const hasSchema = Object.keys(schemaInfo).length > 0;
  const complete = runStatus === "completed" || hasStats;

  const maxMissing = Math.max(
    0.01,
    ...missingAlerts.map((a) => a.null_ratio || 0)
  );

  return (
    <section className="panel">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Results</p>
          <h2 className="panel-title">Data Insights</h2>
        </div>
        {complete && hasStats ? (
          <span className="badge badge-emerald">Analysis ready</span>
        ) : runStatus === "running" ? (
          <span className="badge badge-amber animate-pulse">Collecting</span>
        ) : (
          <span className="badge badge-muted">Pending</span>
        )}
      </header>

      {!hasSchema && !hasStats ? (
        <p className="text-sm text-slate-400">
          Missing-value charts and statistical summaries appear after the
          Profiler and Statistician finish.
        </p>
      ) : (
        <div className="space-y-6">
          <div>
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
              Missing value ratios
            </h3>
            {missingAlerts.length === 0 ? (
              <p className="text-sm text-emerald-300/90">
                No missing-value alerts from the Profiler.
              </p>
            ) : (
              <ul className="space-y-3">
                {missingAlerts.map((alert) => {
                  const pct = Math.round(alert.null_ratio * 1000) / 10;
                  const width = `${Math.max(
                    4,
                    (alert.null_ratio / maxMissing) * 100
                  )}%`;
                  const tone =
                    alert.severity === "high" || alert.null_ratio >= 0.3
                      ? "bg-rose-400"
                      : alert.severity === "medium" || alert.null_ratio >= 0.1
                        ? "bg-amber-400"
                        : "bg-teal-400";
                  return (
                    <li key={alert.column}>
                      <div className="mb-1 flex items-center justify-between gap-2 text-xs">
                        <span className="truncate font-medium text-slate-200">
                          {alert.column}
                        </span>
                        <span className="shrink-0 text-slate-400">
                          {pct}%
                          {alert.severity ? ` · ${alert.severity}` : ""}
                        </span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-white/10">
                        <div
                          className={`h-full rounded-full ${tone} transition-all duration-700`}
                          style={{ width }}
                        />
                      </div>
                      {alert.recommendation ? (
                        <p className="mt-1 text-[11px] text-slate-500">
                          {alert.recommendation}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div>
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
              Distribution fits
            </h3>
            {!distributions.length ? (
              <p className="text-sm text-slate-400">
                {hasStats
                  ? "No distribution rows in summary."
                  : "Waiting for Statistician…"}
              </p>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-white/10">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-white/[0.04] text-[11px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">Column</th>
                      <th className="px-3 py-2 font-medium">Best fit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {distributions.map((row) => (
                      <tr
                        key={row.column}
                        className="border-t border-white/5 text-slate-200"
                      >
                        <td className="px-3 py-2 font-[family-name:var(--font-geist-mono)] text-xs">
                          {row.column}
                        </td>
                        <td className="px-3 py-2">
                          <span className="badge badge-teal">{row.best_fit}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {correlation ? (
            <div>
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
                Correlation matrix
              </h3>
              <div className="overflow-x-auto rounded-xl border border-white/10">
                <table className="min-w-full text-center text-[11px]">
                  <thead>
                    <tr>
                      <th className="px-2 py-2 text-slate-500" />
                      {correlation.columns.map((c) => (
                        <th
                          key={c}
                          className="px-2 py-2 font-medium text-slate-400"
                        >
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {correlation.matrix.map((row, i) => (
                      <tr key={correlation.columns[i] || i}>
                        <th className="px-2 py-2 text-left font-medium text-slate-400">
                          {correlation.columns[i]}
                        </th>
                        {row.map((value, j) => {
                          const n = Number(value);
                          const intensity = Number.isFinite(n)
                            ? Math.min(1, Math.abs(n))
                            : 0;
                          return (
                            <td
                              key={`${i}-${j}`}
                              className="px-2 py-2 font-[family-name:var(--font-geist-mono)] text-slate-200"
                              style={{
                                backgroundColor: `rgba(45, 212, 191, ${
                                  0.08 + intensity * 0.35
                                })`,
                              }}
                              title={String(value)}
                            >
                              {Number.isFinite(n) ? n.toFixed(2) : "—"}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {notes.length > 0 ? (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
                Notes
              </h3>
              <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
                {notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {statisticalSummary.error ? (
            <p className="rounded-lg border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-sm text-rose-200">
              {String(statisticalSummary.error)}
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}
