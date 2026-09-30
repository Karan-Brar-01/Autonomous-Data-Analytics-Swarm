"use client";

import { useCallback, useRef, useState } from "react";

interface DatasetUploaderProps {
  disabled?: boolean;
  isUploading?: boolean;
  filename?: string | null;
  uploadMeta?: {
    rows: number;
    columns: string[];
  } | null;
  onUpload: (file: File) => Promise<unknown>;
}

export function DatasetUploader({
  disabled,
  isUploading,
  filename,
  uploadMeta,
  onUpload,
}: DatasetUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const handleFile = useCallback(
    async (file: File | undefined | null) => {
      if (!file) return;
      setLocalError(null);
      if (!file.name.toLowerCase().endsWith(".csv")) {
        setLocalError("Only CSV files are supported");
        return;
      }
      try {
        await onUpload(file);
      } catch (err) {
        setLocalError(err instanceof Error ? err.message : "Upload failed");
      }
    },
    [onUpload]
  );

  return (
    <section className="panel flex flex-col gap-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Dataset</p>
          <h2 className="panel-title">Uploader</h2>
        </div>
        {isUploading ? (
          <span className="badge badge-amber animate-pulse">Uploading</span>
        ) : filename ? (
          <span className="badge badge-teal">Ready</span>
        ) : (
          <span className="badge badge-muted">Waiting</span>
        )}
      </header>

      <div
        role="button"
        tabIndex={0}
        aria-disabled={disabled || isUploading}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            if (!disabled && !isUploading) inputRef.current?.click();
          }
        }}
        onClick={() => {
          if (!disabled && !isUploading) inputRef.current?.click();
        }}
        onDragEnter={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(e) => {
          e.preventDefault();
          setDragging(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (disabled || isUploading) return;
          void handleFile(e.dataTransfer.files?.[0]);
        }}
        className={[
          "relative flex min-h-[160px] cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-8 text-center transition",
          dragging
            ? "border-teal-400/80 bg-teal-400/10"
            : "border-white/15 bg-white/[0.03] hover:border-teal-400/50 hover:bg-white/[0.05]",
          disabled || isUploading ? "pointer-events-none opacity-60" : "",
        ].join(" ")}
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-teal-400/10 text-teal-300">
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            aria-hidden
          >
            <path d="M12 16V4" />
            <path d="M7 9l5-5 5 5" />
            <path d="M4 20h16" />
          </svg>
        </div>
        <p className="text-sm font-medium text-slate-100">
          Drag & drop a CSV here
        </p>
        <p className="text-xs text-slate-400">
          or click to browse — UTF-8 tabular data
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          disabled={disabled || isUploading}
          onChange={(e) => {
            void handleFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>

      {localError ? (
        <p className="text-sm text-rose-300" role="alert">
          {localError}
        </p>
      ) : null}

      {filename ? (
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div className="rounded-lg bg-black/25 px-3 py-2">
            <dt className="text-[11px] uppercase tracking-wide text-slate-500">
              File
            </dt>
            <dd className="truncate font-medium text-slate-100">{filename}</dd>
          </div>
          <div className="rounded-lg bg-black/25 px-3 py-2">
            <dt className="text-[11px] uppercase tracking-wide text-slate-500">
              Shape
            </dt>
            <dd className="font-medium text-slate-100">
              {uploadMeta
                ? `${uploadMeta.rows} × ${uploadMeta.columns.length}`
                : "—"}
            </dd>
          </div>
        </dl>
      ) : null}
    </section>
  );
}
