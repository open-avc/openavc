import { useMemo, useState } from "react";
import { AlertTriangle, Check, Loader2, Send } from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import type { DriverParamDef } from "../../../../api/types";
import { useAuditStore } from "../../../../store/auditStore";
import { ParamInput } from "../../../../components/shared/ParamInput";
import { SearchableSelect, type SelectGroup } from "../../../../components/shared/SearchableSelect";
import {
  hasInvalidParams,
  hasMissingRequiredParams,
} from "../../../../components/shared/paramValidation";
import { coerceParam } from "../../actionParamFields";
import {
  batchableQueries,
  commandGroups,
  currentRun,
  displayBytes,
  paramsText,
  trialOutcome,
} from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import { buttonStyle, headingStyle, hintStyle, labelStyle, panelStyle, spinStyle } from "../auditStyles";

/** A parameter starts at the value the driver declares, else empty: the
 *  audit never picks a value the person did not choose, because it sends to
 *  a real device. */
function seedValues(params: Record<string, Partial<DriverParamDef>>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, def] of Object.entries(params)) {
    out[name] = def.default !== undefined && def.default !== null ? String(def.default) : "";
  }
  return out;
}

function typedValues(
  params: Record<string, Partial<DriverParamDef>>,
  values: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, def] of Object.entries(params)) {
    const raw = values[name] ?? "";
    if (raw === "") continue;
    out[name] = coerceParam(raw, def.type);
  }
  return out;
}

/** Step 6: send the driver's commands, one at a time, and see what each does. */
export function CommandsStep() {
  const session = useAuditStore((s) => s.session);
  const run = currentRun(session);
  const [selected, setSelected] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"" | "send" | "queries">("");
  const [error, setError] = useState("");

  const catalog = useMemo(() => run?.commands?.catalog ?? [], [run?.commands?.catalog]);
  const groups = useMemo<SelectGroup[]>(() => {
    const { queries, commands } = commandGroups(catalog);
    const option = (c: audit.AuditCommandInfo) => ({
      value: c.name,
      label: c.label,
      hint: c.name !== c.label ? c.name : undefined,
      keywords: c.help,
    });
    const out: SelectGroup[] = [];
    if (queries.length > 0) out.push({ label: "Status queries", options: queries.map(option) });
    if (commands.length > 0) out.push({ label: "Commands", options: commands.map(option) });
    return out;
  }, [catalog]);

  if (!session || !run) return null;
  const sessionId = session.session_id;
  const commands = run.commands;
  const connected = run.active;
  const trials = commands?.trials ?? [];
  const working = commands?.current != null || commands?.batch?.status === "running";
  const command = catalog.find((c) => c.name === selected);
  const params = command?.params ?? {};
  const blocked =
    !command || hasInvalidParams(params, values) || hasMissingRequiredParams(params, values);
  const queryCount = batchableQueries(catalog);

  const act = async (kind: "send" | "queries", call: () => Promise<{ session: audit.AuditSessionState }>) => {
    setError("");
    setBusy(kind);
    try {
      const { session: next } = await call();
      useAuditStore.getState().setSession(next);
    } catch (e) {
      setError(parseApiError(e));
    } finally {
      setBusy("");
    }
  };

  const choose = (name: string) => {
    setSelected(name);
    const def = catalog.find((c) => c.name === name);
    setValues(seedValues(def?.params ?? {}));
  };

  return (
    <div style={{ maxWidth: 820 }}>
      <h2 style={headingStyle}>Commands</h2>
      <p style={{ fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-md)" }}>
        Each command changes the device. Send only the ones you are comfortable running on this
        unit. This step is optional.
      </p>

      {!connected && (
        <div style={{ ...panelStyle, marginBottom: "var(--space-md)", fontSize: "var(--font-size-sm)" }}>
          The driver is not connected to the device, so no command can be sent.{" "}
          <button
            type="button"
            onClick={() => useAuditStore.getState().setStep("listen")}
            style={{ ...buttonStyle("muted"), display: "inline-flex", marginLeft: "var(--space-sm)" }}
          >
            Back to Connect and listen
          </button>
        </div>
      )}

      {connected && catalog.length === 0 && (
        <div style={{ ...hintStyle, fontSize: "var(--font-size-sm)" }}>
          This driver declares no commands.
        </div>
      )}

      {connected && catalog.length > 0 && (
        <>
          {queryCount > 0 && (
            <div style={{ ...panelStyle, marginBottom: "var(--space-md)" }}>
              <div style={labelStyle}>Status queries</div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>
                The driver says {queryCount === 1 ? "this command only asks" : `these ${queryCount} commands only ask`}{" "}
                the device for its status. They can run together.
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
                <button
                  type="button"
                  onClick={() => void act("queries", () => audit.runAuditQueries(sessionId))}
                  disabled={busy !== "" || working}
                  style={buttonStyle("muted", busy !== "" || working)}
                >
                  {busy === "queries" && <Loader2 size={14} style={spinStyle} />}
                  Run all status queries
                </button>
                {commands?.batch && (
                  <span role="status" style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
                    {commands.batch.status === "running"
                      ? `Sending ${Math.min(commands.batch.sent + 1, commands.batch.total)} of ${commands.batch.total}.`
                      : `Sent ${commands.batch.sent} of ${commands.batch.total}.`}
                  </span>
                )}
              </div>
              {commands?.batch && commands.batch.skipped.length > 0 && (
                <div style={hintStyle}>
                  Not included, because they need a value: {commands.batch.skipped.join(", ")}. Send
                  them one at a time below.
                </div>
              )}
            </div>
          )}

          <div style={panelStyle}>
            <div style={labelStyle}>Send a command</div>
            <SearchableSelect
              value={selected}
              onChange={choose}
              groups={groups}
              allowEmpty={false}
              placeholder="Select a command..."
              searchPlaceholder="Search commands..."
              testId="audit-command-picker"
            />
            {command?.help && <div style={hintStyle}>{command.help}</div>}
            {command && Object.keys(params).length > 0 && (
              <div style={{ marginTop: "var(--space-sm)" }}>
                {Object.entries(params).map(([name, def]) => (
                  <div key={name} style={{ marginBottom: "var(--space-sm)" }}>
                    <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: 2 }}>
                      {def.label || name}
                      {def.required ? " *" : ""}
                    </div>
                    <ParamInput
                      def={def}
                      value={values[name] ?? ""}
                      onChange={(v) => setValues((prev) => ({ ...prev, [name]: v }))}
                      values={values}
                      params={params}
                      placeholder={name}
                      style={{ width: "100%" }}
                    />
                    {(def.help || def.description) && (
                      <div style={hintStyle}>{def.help || def.description}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: "var(--space-sm)" }}>
              <button
                type="button"
                onClick={() =>
                  command &&
                  void act("send", () =>
                    audit.sendAuditCommand(sessionId, command.name, typedValues(params, values)),
                  )
                }
                disabled={blocked || busy !== "" || working}
                style={buttonStyle("primary", blocked || busy !== "" || working)}
              >
                {busy === "send" ? <Loader2 size={14} style={spinStyle} /> : <Send size={14} />}
                Send
              </button>
            </div>
          </div>
        </>
      )}

      {error && (
        <div style={{ marginTop: "var(--space-md)" }}>
          <ErrorLine text={error} />
        </div>
      )}

      {trials.length > 0 && (
        <div style={{ marginTop: "var(--space-lg)" }}>
          <div style={labelStyle}>Sent ({trials.length})</div>
          <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {[...trials].reverse().map((t) => (
              <TrialRow key={t.number} trial={t} />
            ))}
          </ol>
        </div>
      )}

      <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: "var(--space-sm)" }}>
        <button
          type="button"
          onClick={() => useAuditStore.getState().setStep("report")}
          disabled={working}
          style={buttonStyle("primary", working)}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

function TrialRow({ trial }: { trial: audit.AuditCommandTrial }) {
  const [open, setOpen] = useState(false);
  const params = paramsText(trial.params);
  const running = trial.status !== "done";
  return (
    <li
      style={{
        ...panelStyle,
        marginBottom: "var(--space-sm)",
        fontSize: "var(--font-size-sm)",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: "var(--space-sm)" }}>
        <span style={{ flexShrink: 0, marginTop: 2 }}>
          {running ? (
            <Loader2 size={14} style={spinStyle} aria-label="Watching" />
          ) : trial.error ? (
            <AlertTriangle size={14} style={{ color: "var(--color-warning)" }} aria-label="Not accepted" />
          ) : (
            <Check size={14} style={{ color: "var(--color-success)" }} aria-label="Sent" />
          )}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>
            {trial.number}. {trial.label}
            {params && <span style={{ fontWeight: 400 }}> ({params})</span>}
            {trial.attempt > 1 && (
              <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>
                {" "}
                (sent {trial.attempt} times)
              </span>
            )}
            {trial.batch && (
              <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}> with the status queries</span>
            )}
          </div>
          <div style={{ overflowWrap: "anywhere" }}>{trialOutcome(trial)}</div>
          {trial.traffic.entries.length > 0 && (
            <button
              type="button"
              onClick={() => setOpen(!open)}
              aria-expanded={open}
              style={{ ...buttonStyle("muted"), padding: "2px var(--space-sm)", marginTop: "var(--space-xs)" }}
            >
              {open ? "Hide the traffic" : "Show the traffic"}
            </button>
          )}
          {open && (
            <div
              role="log"
              aria-label={`Traffic for ${trial.label}`}
              style={{
                marginTop: "var(--space-xs)",
                fontFamily: "var(--font-mono, monospace)",
                fontSize: "var(--font-size-xs)",
              }}
            >
              {trial.traffic.entries.map((e) => (
                <div key={e.seq} style={{ display: "flex", gap: "var(--space-sm)" }}>
                  <span
                    style={{ flexShrink: 0, color: e.direction === "tx" ? "var(--accent)" : "var(--color-success)" }}
                    aria-label={e.direction === "tx" ? "Sent" : "Received"}
                  >
                    {e.direction === "tx" ? "→" : "←"}
                  </span>
                  <span style={{ overflowWrap: "anywhere" }}>{displayBytes(e.text, e.hex)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
