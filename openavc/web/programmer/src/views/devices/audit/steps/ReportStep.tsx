import { useCallback, useEffect, useState } from "react";
import { Check, Download, Loader2 } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import { useAuditStore } from "../../../../store/auditStore";
import { summaryLines } from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import {
  buttonStyle,
  headingStyle,
  hintStyle,
  inputStyle,
  labelStyle,
  panelStyle,
  spinStyle,
} from "../auditStyles";

/** Step 3: what the audit found, who ran it, and the file. */
export function ReportStep() {
  const session = useAuditStore((s) => s.session);
  const sessionId = session?.session_id ?? "";
  const active = session?.status === "active";
  const [report, setReport] = useState<audit.AuditReport | null>(null);
  const [tester, setTester] = useState<audit.AuditTester>(() => ({ ...(session?.tester ?? {}) }));
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState<"" | "download" | "finish" | "another">("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!sessionId || !active) return;
    let alive = true;
    audit
      .getAuditReport(sessionId)
      .then((r) => alive && setReport(r))
      .catch((e) => alive && setError(parseApiError(e)));
    return () => {
      alive = false;
    };
  }, [sessionId, active]);

  const saveTester = useCallback(async () => {
    await audit.setAuditTester(sessionId, {
      name: tester.name ?? "",
      company: tester.company ?? "",
      email: tester.email ?? "",
      notes: tester.notes ?? "",
      leave_out_serial: !!tester.leave_out_serial,
    });
  }, [sessionId, tester]);

  const download = async () => {
    setError("");
    setBusy("download");
    try {
      await saveTester();
      setSaved(await audit.downloadAuditReport(sessionId));
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy("");
    }
  };

  const finish = async () => {
    setError("");
    setBusy("finish");
    try {
      await saveTester();
      await audit.endAudit(sessionId);
      useAuditStore.getState().closeWizard();
    } catch (e) {
      setError(parseApiError(e));
      setBusy("");
    }
  };

  const anotherDriver = async () => {
    setError("");
    setBusy("another");
    try {
      await saveTester();
      const { session: next } = await audit.nextAuditDriver(sessionId);
      useAuditStore.getState().setSession(next);
      useAuditStore.getState().setStep("driver");
    } catch (e) {
      setError(parseApiError(e));
      setBusy("");
    }
  };

  if (!session) return null;
  const tested = session.runs?.length ?? 0;
  const field = (key: "name" | "company" | "email", label: string, type = "text") => (
    <div>
      <label htmlFor={`audit-tester-${key}`} style={labelStyle}>
        {label}
      </label>
      <input
        id={`audit-tester-${key}`}
        type={type}
        value={tester[key] ?? ""}
        onChange={(e) => setTester({ ...tester, [key]: e.target.value })}
        autoComplete="off"
        style={inputStyle}
      />
    </div>
  );

  return (
    <div style={{ maxWidth: 720 }}>
      <h2 style={headingStyle}>Report</h2>

      {report && (
        <div style={panelStyle}>
          <div style={{ fontSize: "var(--font-size-md)", fontWeight: 600, marginBottom: "var(--space-sm)" }}>
            {report.verdict.sentence}
          </div>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--font-size-sm)" }}>
            <tbody>
              {summaryLines(report).map((line) => (
                <tr key={line.label}>
                  <th
                    scope="row"
                    style={{
                      textAlign: "left",
                      fontWeight: 500,
                      color: "var(--text-secondary)",
                      padding: "2px var(--space-md) 2px 0",
                      width: 160,
                      verticalAlign: "top",
                    }}
                  >
                    {line.label}
                  </th>
                  <td style={{ padding: "2px 0", overflowWrap: "anywhere" }}>{line.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.limits.length > 0 && (
            <div style={{ marginTop: "var(--space-md)" }}>
              <div style={{ fontWeight: 600, fontSize: "var(--font-size-sm)" }}>
                What the audit could not see
              </div>
              <ul
                style={{
                  margin: "var(--space-xs) 0 0",
                  paddingLeft: "var(--space-lg)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--text-secondary)",
                }}
              >
                {report.limits.map((limit, i) => (
                  <li key={`${limit.id}-${i}`}>{limit.text}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <h3 style={{ ...headingStyle, fontSize: "var(--font-size-sm)", marginTop: "var(--space-lg)" }}>
        About you (optional)
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-md)" }}>
        {field("name", "Name")}
        {field("company", "Company")}
        {field("email", "Email", "email")}
      </div>
      <label htmlFor="audit-tester-notes" style={{ ...labelStyle, marginTop: "var(--space-md)" }}>
        Notes
      </label>
      <textarea
        id="audit-tester-notes"
        value={tester.notes ?? ""}
        onChange={(e) => setTester({ ...tester, notes: e.target.value })}
        rows={3}
        style={{ ...inputStyle, resize: "vertical" }}
      />
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          marginTop: "var(--space-sm)",
          fontSize: "var(--font-size-sm)",
        }}
      >
        <input
          type="checkbox"
          checked={!!tester.leave_out_serial}
          onChange={(e) => setTester({ ...tester, leave_out_serial: e.target.checked })}
        />
        Leave the serial number out of the report
      </label>

      {error && (
        <div style={{ marginTop: "var(--space-md)" }}>
          <ErrorLine text={error} />
        </div>
      )}

      <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={() => void download()}
          disabled={!active || busy !== ""}
          style={buttonStyle("primary", !active || busy !== "")}
        >
          {busy === "download" ? <Loader2 size={14} style={spinStyle} /> : <Download size={14} />}
          Download report
        </button>
        <button
          type="button"
          onClick={() => void finish()}
          disabled={!active || busy !== ""}
          style={buttonStyle("muted", !active || busy !== "")}
        >
          {busy === "finish" && <Loader2 size={14} style={spinStyle} />}
          Finish
        </button>
        {tested > 0 && (
          <button
            type="button"
            onClick={() => void anotherDriver()}
            disabled={!active || busy !== ""}
            style={buttonStyle("muted", !active || busy !== "")}
          >
            {busy === "another" && <Loader2 size={14} style={spinStyle} />}
            Test another driver
          </button>
        )}
      </div>
      {saved && (
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
          <Check size={14} style={{ color: "var(--color-success)" }} /> Saved {saved}.
        </div>
      )}
      <p style={{ ...hintStyle, marginTop: "var(--space-md)", fontSize: "var(--font-size-sm)" }}>
        Send the file to whoever asked you to run this audit, or attach it to a driver test report
        on GitHub. Finish reconnects any project device the audit paused.
      </p>
    </div>
  );
}
