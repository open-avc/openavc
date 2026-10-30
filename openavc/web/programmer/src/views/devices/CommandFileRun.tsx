import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Download, Square } from "lucide-react";
import {
  cancelCommandRun,
  deviceUploadOpener,
  downloadCommandRunFile,
  listCommandRuns,
  startCommandRun,
  uploadCommandFiles,
  type CommandRun,
} from "../../api/commandFileClient";
import { ApiError, parseApiError } from "../../api/errors";
import type { ProblemReportContext } from "../../api/problemReportClient";
import { onMessage } from "../../api/wsClient";
import { describeSize } from "../../components/shared/commandFiles";
import { commandResultText } from "../../components/shared/commandParams";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { ReportLink } from "./ProblemReportDialog";
import { reportableCommandFailure } from "./problemReport";

/**
 * Send Command for a command that takes a file or hands one back. The files
 * go up first, then the command runs in the background on the server
 * (`core/command_runs.py`), so a long one (firmware) keeps going if this page
 * closes; opening the page again picks it back up.
 */
export type FileRunView =
  | { kind: "idle" }
  | { kind: "uploading"; label: string; fileName: string; sent: number; total: number }
  | { kind: "failed"; text: string; command?: string; status?: number; code?: string }
  | { kind: "run"; run: CommandRun };

export function useCommandFileRun(deviceId: string) {
  const [view, setView] = useState<FileRunView>({ kind: "idle" });
  const abort = useRef<AbortController | null>(null);
  const runId = view.kind === "run" ? view.run.run_id : null;
  const runIdRef = useRef<string | null>(null);
  runIdRef.current = runId;

  // A running command, or one whose file is still waiting, is shown when the
  // page opens; nothing older.
  useEffect(() => {
    let cancelled = false;
    setView({ kind: "idle" });
    listCommandRuns(deviceId)
      .then((runs) => {
        if (cancelled) return;
        const shown = [...runs].reverse().find(
          (r) => r.status === "running" || (r.returned && !r.returned.gone),
        );
        if (shown) setView({ kind: "run", run: shown });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [deviceId]);

  useEffect(
    () =>
      onMessage((msg) => {
        if (msg.type !== "command.run" || msg.device_id !== deviceId) return;
        const run = msg as unknown as CommandRun;
        // This page's run, or one started elsewhere while it shows nothing.
        if (runIdRef.current && run.run_id !== runIdRef.current) return;
        setView((current) =>
          current.kind === "uploading" ? current : { kind: "run", run },
        );
      }),
    [deviceId],
  );

  const start = useCallback(
    async (
      command: string,
      label: string,
      params: Record<string, unknown>,
      files: Record<string, File>,
    ) => {
      const controller = new AbortController();
      abort.current = controller;
      try {
        let uploads: Record<string, string> = {};
        if (Object.keys(files).length > 0) {
          const first = Object.values(files)[0];
          setView({ kind: "uploading", label, fileName: first.name, sent: 0, total: 1 });
          uploads = await uploadCommandFiles(
            deviceUploadOpener(deviceId, command),
            files,
            (sent, total, file) =>
              setView({ kind: "uploading", label, fileName: file.name, sent, total }),
            controller.signal,
          );
        }
        const run = await startCommandRun(deviceId, command, params, uploads);
        runIdRef.current = run.run_id;
        setView((current) =>
          current.kind === "run" && current.run.run_id === run.run_id ? current : { kind: "run", run },
        );
      } catch (e) {
        if (controller.signal.aborted) {
          setView({ kind: "failed", text: `${label} stopped before the file was uploaded.` });
        } else {
          setView({
            kind: "failed",
            text: parseApiError(e),
            command,
            status: e instanceof ApiError ? e.status : undefined,
            code: e instanceof ApiError ? structuredCode(e.body) : undefined,
          });
        }
      } finally {
        abort.current = null;
      }
    },
    [deviceId],
  );

  const stop = useCallback(async () => {
    if (abort.current) {
      abort.current.abort();
      return;
    }
    if (view.kind === "run" && view.run.status === "running") {
      try {
        await cancelCommandRun(deviceId, view.run.run_id);
      } catch (e) {
        setView({ kind: "failed", text: parseApiError(e) });
      }
    }
  }, [deviceId, view]);

  const clear = useCallback(() => {
    runIdRef.current = null;
    setView({ kind: "idle" });
  }, []);

  const busy =
    view.kind === "uploading" || (view.kind === "run" && view.run.status === "running");
  return { view, start, stop, clear, busy };
}

function structuredCode(body: string): string {
  try {
    const code = JSON.parse(body)?.code;
    return typeof code === "string" ? code : "";
  } catch {
    return "";
  }
}

function percent(sent: number, total: number): number {
  return total > 0 ? Math.min(100, Math.floor((sent / total) * 100)) : 0;
}

function Bar({ value }: { value: number }) {
  return (
    <div
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
      style={{
        height: 6,
        borderRadius: 3,
        background: "var(--bg-hover)",
        overflow: "hidden",
        marginTop: 4,
      }}
    >
      <div style={{ width: `${value}%`, height: "100%", background: "var(--accent)" }} />
    </div>
  );
}

const lineStyle = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-xs)",
} as const;

export function CommandFileRunStatus({
  view,
  deviceId,
  deviceName,
  onStop,
  onReport,
}: {
  view: FileRunView;
  deviceId: string;
  deviceName: string;
  onStop: () => void;
  /** Report a problem, for a failure the driver could have caused. */
  onReport?: (context: ProblemReportContext) => void;
}) {
  const [confirmStop, setConfirmStop] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  if (view.kind === "idle") return null;

  const stopButton = (
    <button
      type="button"
      onClick={() => {
        // Once the device has part of the file, stopping is asked first.
        if (view.kind === "run" && view.run.sent > 0) setConfirmStop(true);
        else onStop();
      }}
      style={{
        ...lineStyle,
        padding: "2px var(--space-sm)",
        borderRadius: "var(--border-radius)",
        border: "1px solid var(--border-color)",
        color: "var(--text-secondary)",
        fontSize: 12,
      }}
    >
      <Square size={10} /> Stop
    </button>
  );

  if (view.kind === "failed") {
    return (
      <div role="status" style={{ ...lineStyle, fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>
        <AlertTriangle size={14} />
        <span>{view.text}</span>
        {onReport && reportableCommandFailure(view.status, view.code) && (
          <ReportLink
            onClick={() =>
              onReport({
                source: "command_failure",
                error: view.text,
                command: view.command ?? "",
                status: view.status,
                code: view.code ?? "",
              })
            }
          />
        )}
      </div>
    );
  }

  if (view.kind === "uploading") {
    const pct = percent(view.sent, view.total);
    return (
      <div role="status" style={{ fontSize: "var(--font-size-sm)" }}>
        <div style={{ ...lineStyle, justifyContent: "space-between" }}>
          <span>
            Uploading {view.fileName} to OpenAVC: {pct}%
          </span>
          {stopButton}
        </div>
        <Bar value={pct} />
      </div>
    );
  }

  const { run } = view;
  if (run.status === "running") {
    const sending = run.total > 0 && run.sent < run.total;
    const pct = percent(run.sent, run.total);
    const fileName = run.files.find((f) => f.read < f.size)?.name ?? run.files[0]?.name;
    return (
      <div role="status" style={{ fontSize: "var(--font-size-sm)" }}>
        <div style={{ ...lineStyle, justifyContent: "space-between" }}>
          <span>
            {sending
              ? `Sending ${fileName} to ${deviceName}: ${pct}%`
              : `${run.label}: waiting for ${deviceName} to finish.`}
          </span>
          {stopButton}
        </div>
        {sending && <Bar value={pct} />}
        {confirmStop && (
          <ConfirmDialog
            title={`Stop ${run.label}?`}
            message={`${deviceName} has received part of the file. Check it before you use it.`}
            confirmLabel="Stop"
            destructive
            onConfirm={() => {
              setConfirmStop(false);
              onStop();
            }}
            onCancel={() => setConfirmStop(false)}
          />
        )}
      </div>
    );
  }

  if (run.status === "error" || run.status === "cancelled") {
    const text = run.status === "cancelled" ? `${run.label} stopped.` : run.error || `${run.label} did not finish.`;
    return (
      <div role="status" style={{ ...lineStyle, fontSize: "var(--font-size-sm)", color: "var(--color-error)" }}>
        <AlertTriangle size={14} />
        <span>{text}</span>
        {onReport && run.status === "error" && reportableCommandFailure(run.error_status, run.error_code) && (
          <ReportLink
            onClick={() =>
              onReport({
                source: "command_failure",
                error: text,
                command: run.command,
                status: run.error_status ?? null,
                code: run.error_code ?? "",
              })
            }
          />
        )}
      </div>
    );
  }

  const returned = run.returned;
  const answered = returned ? null : commandResultText(run.label, { result: run.result });
  return (
    <div role="status" style={{ fontSize: "var(--font-size-sm)" }}>
      <div style={{ ...lineStyle, color: "var(--color-success)" }}>
        <Check size={14} />
        <span>
          {returned
            ? returned.gone
              ? `${run.label} done. ${returned.name} was saved.`
              : `${run.label} done. ${returned.name} is ready.`
            : answered?.text}
        </span>
      </div>
      {returned && !returned.gone && (
        <button
          type="button"
          onClick={() => {
            setDownloadError("");
            downloadCommandRunFile(deviceId, run.run_id, returned.name).catch((e) =>
              setDownloadError(parseApiError(e)),
            );
          }}
          style={{
            ...lineStyle,
            marginTop: "var(--space-xs)",
            padding: "var(--space-xs) var(--space-md)",
            borderRadius: "var(--border-radius)",
            background: "var(--accent-bg)",
            color: "var(--text-on-accent)",
          }}
        >
          <Download size={14} /> Download {returned.name} ({describeSize(returned.size)})
        </button>
      )}
      {answered?.detail && (
        <pre
          style={{
            background: "var(--bg-base)",
            padding: "var(--space-md)",
            borderRadius: "var(--border-radius)",
            fontFamily: "var(--font-mono)",
            overflow: "auto",
            maxHeight: 200,
            whiteSpace: "pre-wrap",
            margin: "var(--space-xs) 0 0",
          }}
        >
          {answered.detail}
        </pre>
      )}
      {downloadError && (
        <div style={{ ...lineStyle, color: "var(--color-error)", marginTop: 4 }}>
          <AlertTriangle size={14} />
          <span>{downloadError}</span>
        </div>
      )}
    </div>
  );
}
