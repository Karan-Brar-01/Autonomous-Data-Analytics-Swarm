/** Shared API / WebSocket types for the analytics swarm UI. */

export type AgentName =
  | "profiler"
  | "janitor"
  | "supervisor"
  | "statistician"
  | "system";

export type RunStatus =
  | "idle"
  | "uploading"
  | "ready"
  | "connecting"
  | "running"
  | "completed"
  | "failed";

export interface UploadResponse {
  task_id: string;
  dataset_path: string;
  filename: string;
  rows: number;
  columns: string[];
  message: string;
}

export interface ExecutionPayload {
  success?: boolean;
  stdout?: string;
  stderr?: string;
  error?: string | null;
  exit_code?: number;
  timed_out?: boolean;
  artifacts?: Array<{
    name?: string;
    relative_path?: string;
    size_bytes?: number;
    text_preview?: string;
  }>;
}

export interface SwarmEvent {
  type: "status" | "agent_update" | "complete" | "error" | "warning";
  task_id?: string;
  agent?: string;
  status?: string;
  message?: string;
  iterations?: number;
  next_route?: string;
  logs?: string[];
  all_logs?: string[];
  code_generated?: string | null;
  execution?: ExecutionPayload | null;
  stdout?: string | null;
  stderr?: string | null;
  error_trace?: string | null;
  schema_info?: Record<string, unknown>;
  statistical_summary?: Record<string, unknown>;
  dataset_path?: string;
}

export interface TerminalLine {
  id: string;
  ts: number;
  kind: "info" | "code" | "stdout" | "stderr" | "error" | "system";
  agent?: string;
  text: string;
}

export interface MissingAlert {
  column: string;
  null_ratio: number;
  severity?: string;
  recommendation?: string;
}

export interface DistributionRow {
  column: string;
  best_fit: string;
  details?: Record<string, unknown>;
}

export const AGENT_PIPELINE: AgentName[] = [
  "profiler",
  "janitor",
  "supervisor",
  "statistician",
];

export const AGENT_LABELS: Record<AgentName, string> = {
  profiler: "Profiler",
  janitor: "Janitor",
  supervisor: "Supervisor",
  statistician: "Statistician",
  system: "System",
};
