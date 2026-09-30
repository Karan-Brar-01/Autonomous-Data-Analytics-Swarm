import type { UploadResponse } from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

const WS_BASE =
  process.env.NEXT_PUBLIC_WS_URL?.replace(/\/$/, "") ||
  API_BASE.replace(/^http/, "ws");

export function getApiBase(): string {
  return API_BASE;
}

export function getOrchestrateWsUrl(taskId: string): string {
  return `${WS_BASE}/ws/orchestrate/${taskId}`;
}

export async function uploadCsv(file: File): Promise<UploadResponse> {
  const body = new FormData();
  body.append("file", file);

  const res = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    body,
  });

  if (!res.ok) {
    let detail = `Upload failed (${res.status})`;
    try {
      const payload = await res.json();
      if (payload?.detail) {
        detail =
          typeof payload.detail === "string"
            ? payload.detail
            : JSON.stringify(payload.detail);
      }
    } catch {
      // ignore parse errors
    }
    throw new Error(detail);
  }

  return (await res.json()) as UploadResponse;
}
