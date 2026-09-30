"use client";

import { DatasetUploader } from "@/components/DatasetUploader";
import { DataInsightsPanel } from "@/components/DataInsightsPanel";
import { LiveTerminal } from "@/components/LiveTerminal";
import { SwarmStatusVisualizer } from "@/components/SwarmStatusVisualizer";
import { useSwarmOrchestration } from "@/hooks/useSwarmOrchestration";

export function SwarmDashboard() {
  const swarm = useSwarmOrchestration();

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div className="pointer-events-none absolute inset-0 bg-grid opacity-40" />
      <div className="pointer-events-none absolute -left-24 top-0 h-80 w-80 rounded-full bg-teal-500/10 blur-3xl" />
      <div className="pointer-events-none absolute -right-16 top-40 h-72 w-72 rounded-full bg-sky-500/10 blur-3xl" />

      <div className="relative mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <header className="mb-8 flex flex-col gap-4 border-b border-white/10 pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="eyebrow text-teal-300/90">Autonomous Data Analytics</p>
            <h1 className="mt-1 font-[family-name:var(--font-geist-sans)] text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              Swarm Console
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-400">
              Upload a CSV, launch the multi-agent pipeline, and watch Profiler,
              Janitor, Supervisor, and Statistician collaborate in real time.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {swarm.errorMessage ? (
              <span className="badge badge-rose max-w-xs truncate" title={swarm.errorMessage}>
                Alert
              </span>
            ) : null}
            <button
              type="button"
              disabled={!swarm.taskId || swarm.isUploading || swarm.isRunning}
              onClick={() => swarm.startSwarm()}
              className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
            >
              {swarm.isRunning ? (
                <span className="inline-flex items-center gap-2">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-slate-950" />
                  Swarm running…
                </span>
              ) : (
                "Launch swarm"
              )}
            </button>
          </div>
        </header>

        {swarm.errorMessage ? (
          <div
            className="mb-6 rounded-xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-100"
            role="alert"
          >
            {swarm.errorMessage}
          </div>
        ) : null}

        <div className="grid gap-6 lg:grid-cols-12">
          <div className="flex flex-col gap-6 lg:col-span-4">
            <DatasetUploader
              isUploading={swarm.isUploading}
              disabled={swarm.isRunning}
              filename={swarm.filename}
              uploadMeta={swarm.uploadMeta}
              onUpload={swarm.uploadDataset}
            />
            <DataInsightsPanel
              runStatus={swarm.runStatus}
              missingAlerts={swarm.missingAlerts}
              statisticalSummary={swarm.statisticalSummary}
              schemaInfo={swarm.schemaInfo}
            />
          </div>

          <div className="flex flex-col gap-6 lg:col-span-8">
            <SwarmStatusVisualizer
              activeAgent={swarm.activeAgent}
              completedAgents={swarm.completedAgents}
              runStatus={swarm.runStatus}
              iterations={swarm.iterations}
              maxRetries={swarm.maxRetries}
            />
            <LiveTerminal
              lines={swarm.terminalLines}
              isRunning={swarm.isRunning}
              onClear={swarm.clearTerminal}
            />
          </div>
        </div>

        {swarm.taskId ? (
          <footer className="mt-8 flex flex-wrap gap-4 border-t border-white/10 pt-4 font-[family-name:var(--font-geist-mono)] text-[11px] text-slate-500">
            <span>task: {swarm.taskId}</span>
            {swarm.datasetPath ? (
              <span className="truncate">path: {swarm.datasetPath}</span>
            ) : null}
          </footer>
        ) : null}
      </div>
    </div>
  );
}
