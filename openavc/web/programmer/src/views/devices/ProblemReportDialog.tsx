import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, ClipboardCheck, Download, Loader2, Send, X } from "lucide-react";
import { Modal } from "../../components/shared/Modal";
import { InlineError } from "../../components/shared/InlineError";
import { getAuditDeviceTarget } from "../../api/auditClient";
import { inRemoteSession } from "../../api/auth";
import { parseApiError } from "../../api/errors";
import {
  createProblemReport,
  downloadProblemReport,
  getProblemReportPreview,
  postReportFromBrowser,
  problemReportFile,
  sendProblemReport,
  type ProblemReportBody,
  type ProblemReportContext,
  type ProblemReportPreview,
  type SavedProblemReport,
} from "../../api/problemReportClient";
import { useAuditStore } from "../../store/auditStore";
import { buttonStyle, hintStyle, inputStyle, labelStyle } from "./audit/auditStyles";
import { rememberContact, rememberedContact } from "./contactMemory";
import {
  failureText,
  finalFailure,
  includedLines,
  sendReport,
  sentText,
  type SendOutcome,
} from "./problemReport";

const spin = { animation: "spin 1s linear infinite" } as const;

/**
 * Report a problem with one device: what happened (optional), a name and email
 * (optional, remembered in this browser), what the report includes, and Send to
 * OpenAVC or Download. The report is built from what OpenAVC already recorded,
 * so building it sends the device nothing.
 */
export function ProblemReportDialog({
  deviceId,
  deviceName,
  context,
  onClose,
}: {
  deviceId: string;
  deviceName: string;
  context: ProblemReportContext;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<ProblemReportPreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [message, setMessage] = useState("");
  const [contact, setContact] = useState(rememberedContact);
  const [leaveOutSerial, setLeaveOutSerial] = useState(false);
  const [busy, setBusy] = useState<"" | "send" | "download">("");
  const [outcome, setOutcome] = useState<SendOutcome | null>(null);
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  // Where an audit of this device would start, once the report is sent.
  const [auditAddress, setAuditAddress] = useState("");
  // The file built for what is typed now: Send and Download hand over the
  // same one until something changes.
  const built = useRef<{ key: string; report: SavedProblemReport } | null>(null);

  useEffect(() => {
    let alive = true;
    getProblemReportPreview(deviceId)
      .then((p) => alive && setPreview(p))
      .catch((e) => alive && setPreviewError(parseApiError(e)));
    return () => {
      alive = false;
    };
  }, [deviceId]);

  const sent = outcome?.kind === "sent" ? outcome : null;
  const failed = outcome?.kind === "failed" ? outcome : null;

  // Offered once sent, for a device an audit can reach. An audit of a
  // simulated device would test the simulator.
  useEffect(() => {
    if (!sent || !preview || preview.simulated) return;
    let alive = true;
    getAuditDeviceTarget(deviceId)
      .then((target) => alive && target.auditable && setAuditAddress(target.address))
      .catch(() => {
        /* no offer */
      });
    return () => {
      alive = false;
    };
  }, [sent, preview, deviceId]);

  const ready = preview !== null || previewError !== "";
  const locked = busy !== "" || sent !== null;
  const canSend = !preview || preview.report_url !== "";

  const build = async (): Promise<SavedProblemReport> => {
    const body: ProblemReportBody = {
      message,
      name: contact.name,
      email: contact.email,
      leave_out_serial: preview?.serial_found ? leaveOutSerial : false,
      context,
    };
    const key = JSON.stringify(body);
    if (built.current?.key === key) return built.current.report;
    const report = await createProblemReport(deviceId, body);
    built.current = { key, report };
    return report;
  };

  const send = async () => {
    setError("");
    setOutcome(null);
    setBusy("send");
    rememberContact(contact);
    try {
      const report = await build();
      setOutcome(
        await sendReport({
          serverSend: () => sendProblemReport(report.name),
          fetchFile: problemReportFile,
          post: postReportFromBrowser,
          remote: inRemoteSession(),
          kind: "problem_report",
          openavcVersion: preview?.openavc_version ?? "",
        }),
      );
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy("");
    }
  };

  const download = async () => {
    setError("");
    setBusy("download");
    rememberContact(contact);
    try {
      const report = sent ? built.current?.report ?? (await build()) : await build();
      setSaved(await downloadProblemReport(report.name));
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy("");
    }
  };

  const runAudit = (follows: { report_id: string; reference: string }) => {
    onClose();
    useAuditStore.getState().openWizard({ address: auditAddress, deviceId, followsReport: follows });
  };

  return (
    <Modal
      labelledBy="problem-report-title"
      onClose={onClose}
      closeOnBackdrop={false}
      initialFocus="textarea"
      panelStyle={{
        width: "min(560px, 94vw)",
        maxHeight: "80vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          padding: "var(--space-md) var(--space-lg)",
          borderBottom: "1px solid var(--border-color)",
        }}
      >
        <h2
          id="problem-report-title"
          style={{ flex: 1, minWidth: 0, fontSize: "var(--font-size-lg)", fontWeight: 600, margin: 0 }}
        >
          Report a problem with {deviceName}
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          title="Close"
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--text-muted)" }}
        >
          <X size={16} />
        </button>
      </div>

      <div style={{ overflowY: "auto", padding: "var(--space-lg)" }}>
        {sent ? (
          <SentView
            reference={sent.reference}
            busy={busy}
            saved={saved}
            auditAddress={auditAddress}
            onDownload={() => void download()}
            onRunAudit={() =>
              built.current && runAudit({ report_id: built.current.report.report_id, reference: sent.reference })
            }
            onClose={onClose}
          />
        ) : (
          <>
            <label htmlFor="problem-report-message" style={labelStyle}>
              What happened?
            </label>
            <textarea
              id="problem-report-message"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              disabled={locked}
              rows={4}
              placeholder="What you expected, and what the device did instead."
              style={{ ...inputStyle, resize: "vertical" }}
            />

            {context.error && (
              <div style={{ marginTop: "var(--space-md)" }}>
                <div style={labelStyle}>The error on screen</div>
                <div
                  style={{
                    fontSize: "var(--font-size-sm)",
                    color: "var(--text-secondary)",
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                  }}
                >
                  {context.error}
                </div>
              </div>
            )}

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "var(--space-md)",
                marginTop: "var(--space-md)",
              }}
            >
              <div>
                <label htmlFor="problem-report-name" style={labelStyle}>
                  Your name (optional)
                </label>
                <input
                  id="problem-report-name"
                  value={contact.name}
                  onChange={(e) => setContact({ ...contact, name: e.target.value })}
                  disabled={locked}
                  autoComplete="name"
                  style={inputStyle}
                />
              </div>
              <div>
                <label htmlFor="problem-report-email" style={labelStyle}>
                  Your email (optional)
                </label>
                <input
                  id="problem-report-email"
                  type="email"
                  value={contact.email}
                  onChange={(e) => setContact({ ...contact, email: e.target.value })}
                  disabled={locked}
                  autoComplete="email"
                  style={inputStyle}
                />
              </div>
            </div>
            <div style={hintStyle}>Leave an email if you'd like a reply.</div>

            {preview?.serial_found && (
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-sm)",
                  marginTop: "var(--space-md)",
                  fontSize: "var(--font-size-sm)",
                }}
              >
                <input
                  type="checkbox"
                  checked={leaveOutSerial}
                  onChange={(e) => setLeaveOutSerial(e.target.checked)}
                  disabled={locked}
                />
                Leave the serial number out of the report
              </label>
            )}

            <details style={{ marginTop: "var(--space-md)" }}>
              <summary
                style={{
                  cursor: "pointer",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-muted)",
                  userSelect: "none",
                }}
              >
                What's included
              </summary>
              {preview && (
                <ul
                  style={{
                    margin: "var(--space-xs) 0 0",
                    paddingLeft: "var(--space-lg)",
                    fontSize: "var(--font-size-sm)",
                    color: "var(--text-secondary)",
                    overflowWrap: "anywhere",
                  }}
                >
                  {includedLines(preview).map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
            </details>
            {!ready && (
              <div
                role="status"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  marginTop: "var(--space-xs)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-muted)",
                }}
              >
                <Loader2 size={14} style={spin} /> Reading what this report would include…
              </div>
            )}
            {previewError && (
              <InlineError message={previewError} style={{ marginTop: "var(--space-sm)" }} />
            )}

            {failed && (
              <div style={{ marginTop: "var(--space-md)" }}>
                <SendResultLine outcome={failed} maxBytes={preview?.max_bytes} />
              </div>
            )}
            {error && <InlineError message={error} style={{ marginTop: "var(--space-md)" }} />}

            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: "var(--space-sm)",
                marginTop: "var(--space-lg)",
                flexWrap: "wrap",
              }}
            >
              <button
                type="button"
                onClick={() => void download()}
                disabled={!ready || busy !== ""}
                style={buttonStyle(failed || !canSend ? "primary" : "muted", !ready || busy !== "")}
              >
                {busy === "download" ? <Loader2 size={14} style={spin} /> : <Download size={14} />}
                Download
              </button>
              {canSend && !(failed && finalFailure(failed.failure)) && (
                <button
                  type="button"
                  onClick={() => void send()}
                  disabled={!ready || busy !== ""}
                  style={buttonStyle(failed ? "muted" : "primary", !ready || busy !== "")}
                >
                  {busy === "send" ? <Loader2 size={14} style={spin} /> : <Send size={14} />}
                  {busy === "send" ? "Sending…" : "Send to OpenAVC"}
                </button>
              )}
            </div>
            {saved && <SavedLine name={saved} />}
          </>
        )}
      </div>
    </Modal>
  );
}

/** Report a problem at the end of a failed command's line. */
export function ReportLink({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: "none",
        border: "none",
        padding: 0,
        marginLeft: "var(--space-xs)",
        cursor: "pointer",
        fontSize: "inherit",
        color: "inherit",
        textDecoration: "underline",
      }}
    >
      Report a problem
    </button>
  );
}

/** What one press of Send to OpenAVC came to, in the page's words. */
export function SendResultLine({ outcome, maxBytes }: { outcome: SendOutcome; maxBytes?: number }) {
  const ok = outcome.kind === "sent";
  return (
    <div
      role="status"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: "var(--space-xs)",
        fontSize: "var(--font-size-sm)",
        color: ok ? "var(--text-primary)" : "var(--color-error)",
      }}
    >
      {ok ? (
        <Check size={14} style={{ flexShrink: 0, marginTop: 2, color: "var(--color-success)" }} />
      ) : (
        <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
      )}
      <span>
        {outcome.kind === "sent" ? sentText(outcome.reference) : failureText(outcome.failure, maxBytes)}
      </span>
    </div>
  );
}

function SavedLine({ name }: { name: string }) {
  return (
    <div
      role="status"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-xs)",
        marginTop: "var(--space-sm)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      <Check size={14} style={{ color: "var(--color-success)" }} /> Saved {name}.
    </div>
  );
}

function SentView({
  reference,
  busy,
  saved,
  auditAddress,
  onDownload,
  onRunAudit,
  onClose,
}: {
  reference: string;
  busy: "" | "send" | "download";
  saved: string;
  auditAddress: string;
  onDownload: () => void;
  onRunAudit: () => void;
  onClose: () => void;
}) {
  return (
    <div>
      <div
        role="status"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-md)",
          fontWeight: 600,
        }}
      >
        <Check size={16} style={{ color: "var(--color-success)" }} /> {sentText(reference)}
      </div>
      <button
        type="button"
        onClick={onDownload}
        disabled={busy !== ""}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          marginTop: "var(--space-sm)",
          padding: 0,
          background: "none",
          border: "none",
          color: "var(--accent)",
          fontSize: "var(--font-size-sm)",
          textDecoration: "underline",
          cursor: busy !== "" ? "default" : "pointer",
        }}
      >
        {busy === "download" && <Loader2 size={12} style={spin} />}
        Download a copy
      </button>
      {saved && <SavedLine name={saved} />}
      {auditAddress && (
        <p style={{ ...hintStyle, fontSize: "var(--font-size-sm)", marginTop: "var(--space-lg)" }}>
          A Device Audit tests this device for a few minutes and gives the OpenAVC team much more
          to go on. The device is paused while it runs.
        </p>
      )}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: "var(--space-sm)",
          marginTop: "var(--space-lg)",
          flexWrap: "wrap",
        }}
      >
        {auditAddress && (
          <button type="button" onClick={onRunAudit} style={buttonStyle("muted")}>
            <ClipboardCheck size={14} /> Run a Device Audit
          </button>
        )}
        <button type="button" onClick={onClose} style={buttonStyle("primary")}>
          Close
        </button>
      </div>
    </div>
  );
}
