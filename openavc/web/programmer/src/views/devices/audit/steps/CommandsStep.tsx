import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Loader2, RotateCcw, Send } from "lucide-react";
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
  ANSWER_CHOICES,
  batchableQueries,
  changeText,
  commandGroups,
  currentRun,
  displayBytes,
  paramsText,
  sendWarning,
  trialOutcome,
} from "../auditHelpers";
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

type Call = () => Promise<{ session: audit.AuditSessionState }>;

/** Step 6: send the driver's commands, one at a time, and see what each does. */
export function CommandsStep() {
  const session = useAuditStore((s) => s.session);
  const run = currentRun(session);
  const [selected, setSelected] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"" | "send" | "queries" | "watch">("");
  const [error, setError] = useState("");
  // A send waiting for the person to read the command's warning first.
  const [pending, setPending] = useState<{ text: string; label: string; call: Call } | null>(null);

  const catalog = useMemo(() => run?.commands?.catalog ?? [], [run?.commands?.catalog]);
  const groups = useMemo<SelectGroup[]>(() => {
    const { queries, commands } = commandGroups(catalog);
    const option = (c: audit.AuditCommandInfo) => ({
      value: c.name,
      label: c.label,
      hint: c.name !== c.label ? c.name : undefined,
      badge: c.restarts_device_for > 0 ? "Restarts the device" : undefined,
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

  const act = async (kind: "send" | "queries" | "watch", call: Call) => {
    setError("");
    setPending(null);
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

  /** Send now, or say the command's warning first. */
  const sendOrWarn = (info: audit.AuditCommandInfo | undefined, label: string, call: Call) => {
    const text = info ? sendWarning(info) : "";
    if (text) setPending({ text, label, call });
    else void act("send", call);
  };

  const choose = (name: string) => {
    setSelected(name);
    setPending(null);
    const def = catalog.find((c) => c.name === name);
    setValues(seedValues(def?.params ?? {}));
  };

  const again = (trial: audit.AuditCommandTrial) =>
    sendOrWarn(
      catalog.find((c) => c.name === trial.command),
      trial.label,
      () => audit.sendAuditCommand(sessionId, trial.command, {}, trial.number),
    );

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
                  sendOrWarn(command, command.label, () =>
                    audit.sendAuditCommand(sessionId, command.name, typedValues(params, values)),
                  )
                }
                disabled={blocked || busy !== "" || working || pending !== null}
                style={buttonStyle("primary", blocked || busy !== "" || working || pending !== null)}
              >
                {busy === "send" ? <Loader2 size={14} style={spinStyle} /> : <Send size={14} />}
                Send
              </button>
            </div>
          </div>
        </>
      )}

      {pending && (
        <div
          role="alert"
          aria-label={`Before sending ${pending.label}`}
          style={{
            ...panelStyle,
            marginTop: "var(--space-md)",
            background: "var(--color-warning-bg)",
            border: "1px solid var(--color-warning)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2, color: "var(--color-warning)" }} />
            <div>
              <div style={{ fontWeight: 600 }}>Before sending {pending.label}</div>
              <div>{pending.text}</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
            <button type="button" onClick={() => void act("send", pending.call)} style={buttonStyle("primary")}>
              Send it
            </button>
            <button type="button" onClick={() => setPending(null)} style={buttonStyle("muted")}>
              Cancel
            </button>
          </div>
        </div>
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
              <TrialRow
                key={t.number}
                trial={t}
                disabled={!connected || busy !== "" || working || pending !== null}
                onAgain={() => again(t)}
                onWaitLonger={() => void act("watch", () => audit.waitLonger(sessionId))}
                onStop={() => void act("watch", () => audit.stopWatching(sessionId))}
                onAnswer={(answer, note) =>
                  void act("watch", () => audit.answerAuditCommand(sessionId, t.number, answer, note))
                }
              />
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

function TrialRow({
  trial,
  disabled,
  onAgain,
  onWaitLonger,
  onStop,
  onAnswer,
}: {
  trial: audit.AuditCommandTrial;
  disabled: boolean;
  onAgain: () => void;
  onWaitLonger: () => void;
  onStop: () => void;
  onAnswer: (answer: audit.AuditCommandAnswer, note: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(() => trial.answer?.note ?? "");
  const [now, setNow] = useState(() => Date.now() / 1000);
  const watching = trial.status === "watching";
  useEffect(() => {
    if (!watching) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [watching]);
  const params = paramsText(trial.params);
  const running = trial.status !== "done";
  const left = watching && trial.ends_at ? Math.max(0, Math.ceil(trial.ends_at - now)) : null;
  const before = trial.since_previous;
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
          ) : trial.error || trial.sent_nothing || trial.refusals.last_error || trial.refusals.device_errors ? (
            <AlertTriangle size={14} style={{ color: "var(--color-warning)" }} aria-label="Needs a look" />
          ) : (
            <Check size={14} style={{ color: "var(--color-success)" }} aria-label="Sent" />
          )}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>
            {trial.number}. {trial.label}
            {params && <span style={{ fontWeight: 400 }}> ({params})</span>}
            {trial.batch && (
              <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}> with the status queries</span>
            )}
          </div>
          {trial.attempt > 1 && before && (
            <div style={{ color: "var(--text-secondary)" }}>
              Sent {trial.attempt} times; this one {before.seconds.toFixed(1)} s after {before.label}.
            </div>
          )}
          <div style={{ overflowWrap: "anywhere" }}>
            {trialOutcome(trial)}
            {left !== null && ` Watching, ${left} s left.`}
          </div>
          {trial.changes.length > 0 && (
            <div style={{ color: "var(--text-secondary)", overflowWrap: "anywhere" }}>
              Changed while watched: {trial.changes.map(changeText).join("; ")}
            </div>
          )}
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)", marginTop: "var(--space-xs)" }}>
            {watching && (
              <>
                <button type="button" onClick={onWaitLonger} style={{ ...buttonStyle("muted"), padding: "2px var(--space-sm)" }}>
                  Wait longer
                </button>
                <button type="button" onClick={onStop} style={{ ...buttonStyle("muted"), padding: "2px var(--space-sm)" }}>
                  Stop watching
                </button>
              </>
            )}
            {!running && (
              <button
                type="button"
                onClick={onAgain}
                disabled={disabled}
                style={{ ...buttonStyle("muted", disabled), padding: "2px var(--space-sm)" }}
              >
                <RotateCcw size={12} /> Try again
              </button>
            )}
            {trial.traffic.entries.length > 0 && (
              <button
                type="button"
                onClick={() => setOpen(!open)}
                aria-expanded={open}
                style={{ ...buttonStyle("muted"), padding: "2px var(--space-sm)" }}
              >
                {open ? "Hide the traffic" : "Show the traffic"}
              </button>
            )}
          </div>
          {!running && !trial.batch && (
            <div style={{ marginTop: "var(--space-sm)" }}>
              <div style={{ fontWeight: 600 }}>Did the device do it?</div>
              <div
                role="group"
                aria-label={`Did the device do ${trial.label}?`}
                style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)", marginTop: 2 }}
              >
                {ANSWER_CHOICES.map((choice) => {
                  const chosen = trial.answer?.answer === choice.key;
                  return (
                    <button
                      key={choice.key}
                      type="button"
                      aria-pressed={chosen}
                      onClick={() => onAnswer(choice.key, note.trim())}
                      style={{
                        ...buttonStyle(chosen ? "primary" : "muted"),
                        padding: "2px var(--space-sm)",
                      }}
                    >
                      {chosen && <Check size={12} />}
                      {choice.label}
                    </button>
                  );
                })}
              </div>
              <input
                type="text"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                onBlur={() => {
                  if (trial.answer && note.trim() !== trial.answer.note) {
                    onAnswer(trial.answer.answer, note.trim());
                  }
                }}
                placeholder="A note about what you saw (optional)"
                aria-label={`A note about ${trial.label}`}
                maxLength={2000}
                style={{ ...inputStyle, marginTop: "var(--space-xs)" }}
              />
            </div>
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
