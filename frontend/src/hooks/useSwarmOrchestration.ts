"use client";

import { useCallback, useRef, useState } from "react";
import { getOrchestrateWsUrl, uploadCsv } from "@/lib/api";
import type {
  AgentName,
  MissingAlert,
  RunStatus,
  SwarmEvent,
  TerminalLine,
  UploadResponse,
} from "@/lib/types";

function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function normalizeAgent(raw?: string): AgentName {
  const value = (raw || "system").toLowerCase();
  if (
    value === "profiler" ||
    value === "janitor" ||
    value === "supervisor" ||
    value === "statistician"
  ) {
    return value;
  }
  return "system";
}

function extractMissingAlerts(schemaInfo: Record<string, unknown>): MissingAlert[] {
  const alerts = schemaInfo.missing_value_alerts;
  if (!Array.isArray(alerts)) return [];
  return alerts
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const row = item as Record<string, unknown>;
      const column = String(row.column ?? "");
      const nullRatio = Number(row.null_ratio ?? 0);
      if (!column) return null;
      return {
        column,
        null_ratio: Number.isFinite(nullRatio) ? nullRatio : 0,
        severity: row.severity ? String(row.severity) : undefined,
        recommendation: row.recommendation
          ? String(row.recommendation)
          : undefined,
      };
    })
    .filter(Boolean) as MissingAlert[];
}

export interface SwarmState {
  runStatus: RunStatus;
  activeAgent: AgentName | null;
  completedAgents: AgentName[];
  iterations: number;
  maxRetries: number;
  taskId: string | null;
  datasetPath: string | null;
  filename: string | null;
  uploadMeta: UploadResponse | null;
  terminalLines: TerminalLine[];
  schemaInfo: Record<string, unknown>;
  statisticalSummary: Record<string, unknown>;
  missingAlerts: MissingAlert[];
  errorMessage: string | null;
  isUploading: boolean;
  isRunning: boolean;
}

export function useSwarmOrchestration() {
  const [runStatus, setRunStatus] = useState<RunStatus>("idle");
  const [activeAgent, setActiveAgent] = useState<AgentName | null>(null);
  const [completedAgents, setCompletedAgents] = useState<AgentName[]>([]);
  const [iterations, setIterations] = useState(0);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [datasetPath, setDatasetPath] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [uploadMeta, setUploadMeta] = useState<UploadResponse | null>(null);
  const [terminalLines, setTerminalLines] = useState<TerminalLine[]>([]);
  const [schemaInfo, setSchemaInfo] = useState<Record<string, unknown>>({});
  const [statisticalSummary, setStatisticalSummary] = useState<
    Record<string, unknown>
  >({});
  const [missingAlerts, setMissingAlerts] = useState<MissingAlert[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const maxRetries = 3;

  const pushLine = useCallback((line: Omit<TerminalLine, "id" | "ts">) => {
    setTerminalLines((prev) => [
      ...prev,
      { id: uid(), ts: Date.now(), ...line },
    ]);
  }, []);

  const resetRunArtifacts = useCallback(() => {
    setTerminalLines([]);
    setSchemaInfo({});
    setStatisticalSummary({});
    setMissingAlerts([]);
    setCompletedAgents([]);
    setActiveAgent(null);
    setIterations(0);
    setErrorMessage(null);
  }, []);

  const handleEvent = useCallback(
    (event: SwarmEvent) => {
      if (typeof event.iterations === "number") {
        setIterations(event.iterations);
      }

      if (event.schema_info && Object.keys(event.schema_info).length > 0) {
        setSchemaInfo(event.schema_info);
        setMissingAlerts(extractMissingAlerts(event.schema_info));
      }

      if (
        event.statistical_summary &&
        Object.keys(event.statistical_summary).length > 0
      ) {
        setStatisticalSummary(event.statistical_summary);
      }

      const agent = normalizeAgent(event.agent);

      if (event.type === "status") {
        setRunStatus("running");
        if (agent !== "system") setActiveAgent(agent);
        if (event.message) {
          pushLine({ kind: "system", agent, text: event.message });
        }
        return;
      }

      if (event.type === "warning") {
        pushLine({
          kind: "info",
          agent: "system",
          text: event.message || "Warning from backend",
        });
        return;
      }

      if (event.type === "error") {
        setRunStatus("failed");
        setActiveAgent(null);
        const msg = event.error_trace || event.message || "Swarm failed";
        setErrorMessage(msg);
        pushLine({ kind: "error", agent, text: msg });
        return;
      }

      if (event.type === "complete") {
        setRunStatus(event.status === "failed" ? "failed" : "completed");
        setActiveAgent(null);
        if (event.all_logs?.length) {
          // Prefer complete payload summaries when present.
          if (
            event.statistical_summary &&
            Object.keys(event.statistical_summary).length > 0
          ) {
            setStatisticalSummary(event.statistical_summary);
          }
          if (event.schema_info && Object.keys(event.schema_info).length > 0) {
            setSchemaInfo(event.schema_info);
            setMissingAlerts(extractMissingAlerts(event.schema_info));
          }
        }
        if (event.status === "failed") {
          const msg = event.error_trace || "Orchestration finished with errors";
          setErrorMessage(msg);
          pushLine({ kind: "error", agent: "system", text: msg });
        } else {
          pushLine({
            kind: "system",
            agent: "system",
            text: "Swarm run completed",
          });
        }
        return;
      }

      if (event.type === "agent_update") {
        setRunStatus("running");
        setActiveAgent(agent);
        if (agent !== "system") {
          setCompletedAgents((prev) =>
            prev.includes(agent) ? prev : [...prev, agent]
          );
        }

        for (const log of event.logs || []) {
          const lower = log.toLowerCase();
          const kind =
            lower.includes("fail") || lower.includes("error")
              ? "error"
              : "info";
          pushLine({ kind, agent, text: log });
        }

        if (event.code_generated) {
          pushLine({
            kind: "code",
            agent,
            text: event.code_generated,
          });
        }

        if (event.stdout) {
          pushLine({ kind: "stdout", agent, text: event.stdout });
        }

        if (event.stderr) {
          pushLine({ kind: "stderr", agent, text: event.stderr });
        }

        if (event.error_trace && event.execution && !event.execution.success) {
          pushLine({
            kind: "error",
            agent,
            text: `Self-heal trace: ${event.error_trace}`,
          });
        }
      }
    },
    [pushLine]
  );

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        // ignore
      }
      wsRef.current = null;
    }
  }, []);

  const uploadDataset = useCallback(
    async (file: File) => {
      disconnect();
      resetRunArtifacts();
      setRunStatus("uploading");
      setErrorMessage(null);
      setTaskId(null);
      setDatasetPath(null);
      setFilename(file.name);
      setUploadMeta(null);

      try {
        const result = await uploadCsv(file);
        setUploadMeta(result);
        setTaskId(result.task_id);
        setDatasetPath(result.dataset_path);
        setFilename(result.filename);
        setRunStatus("ready");
        pushLine({
          kind: "system",
          agent: "system",
          text: `Uploaded ${result.filename} (${result.rows} rows × ${result.columns.length} cols)`,
        });
        return result;
      } catch (err) {
        const message = err instanceof Error ? err.message : "Upload failed";
        setRunStatus("failed");
        setErrorMessage(message);
        pushLine({ kind: "error", agent: "system", text: message });
        throw err;
      }
    },
    [disconnect, pushLine, resetRunArtifacts]
  );

  const startSwarm = useCallback(() => {
    if (!taskId || !datasetPath) {
      setErrorMessage("Upload a CSV before starting the swarm");
      return;
    }

    disconnect();
    resetRunArtifacts();
    setRunStatus("connecting");
    setErrorMessage(null);
    pushLine({
      kind: "system",
      agent: "system",
      text: `Connecting to swarm for task ${taskId}…`,
    });

    const ws = new WebSocket(getOrchestrateWsUrl(taskId));
    wsRef.current = ws;

    ws.onopen = () => {
      setRunStatus("running");
      setActiveAgent("profiler");
      pushLine({
        kind: "system",
        agent: "system",
        text: "WebSocket connected — dispatching dataset_path",
      });
      ws.send(JSON.stringify({ dataset_path: datasetPath }));
    };

    ws.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as SwarmEvent;
        handleEvent(event);
      } catch (err) {
        pushLine({
          kind: "error",
          agent: "system",
          text: `Malformed event: ${String(err)}`,
        });
      }
    };

    ws.onerror = () => {
      setErrorMessage("WebSocket connection error");
      pushLine({
        kind: "error",
        agent: "system",
        text: "WebSocket error — is the FastAPI backend running on :8000?",
      });
      setRunStatus((prev) => (prev === "completed" ? prev : "failed"));
    };

    ws.onclose = () => {
      wsRef.current = null;
      setActiveAgent(null);
      setRunStatus((prev) => {
        if (prev === "completed" || prev === "failed") return prev;
        if (prev === "running" || prev === "connecting") return "failed";
        return prev;
      });
    };
  }, [
    datasetPath,
    disconnect,
    handleEvent,
    pushLine,
    resetRunArtifacts,
    taskId,
  ]);

  const clearTerminal = useCallback(() => {
    setTerminalLines([]);
  }, []);

  return {
    runStatus,
    activeAgent,
    completedAgents,
    iterations,
    maxRetries,
    taskId,
    datasetPath,
    filename,
    uploadMeta,
    terminalLines,
    schemaInfo,
    statisticalSummary,
    missingAlerts,
    errorMessage,
    isUploading: runStatus === "uploading",
    isRunning: runStatus === "running" || runStatus === "connecting",
    uploadDataset,
    startSwarm,
    disconnect,
    clearTerminal,
  };
}
