import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  HelpCircle,
  Loader2,
  RotateCcw,
  Send,
  XCircle,
} from "lucide-react";
import * as audit from "../../../../api/auditClient";
import { parseApiError } from "../../../../api/errors";
import { useAuditStore } from "../../../../store/auditStore";
import { CommandParamForm } from "../../../../components/shared/CommandParamForm";
import {
  coerceCommandParams,
  commandParamsBlocked,
  seedCommandParams,
} from "../../../../components/shared/commandParams";
import type { ParamPickers } from "../../../../components/shared/ParamInput";
import {
  ANSWER_CHOICES,
  batchableQueries,
  changedText,
  commandMatches,
  commandProgress,
  commandSections,
  commandStatus,
  currentRun,
  displayBytes,
  MOVED_IN_SENTENCE,
  movedParts,
  movedText,
  movingText,
  paramsText,
  sendWarning,
  trialOutcome,
  type CommandStatusKey,
} from "../auditHelpers";
import { ErrorLine } from "../auditParts";
import { SettingsPanel } from "./SettingsPanel";
import {
  buttonStyle,
  headingStyle,
  hintStyle,
  inputStyle,
  labelStyle,
  panelStyle,
  spinStyle,
} from "../auditStyles";

type Call = () => Promise<{ session: audit.AuditSessionState }>;

/** Step 6: try the driver's commands, one at a time, and say what each did. */
export function CommandsStep() {
  const session = useAuditStore((s) => s.session);
  const run = currentRun(session);
  // The command whose row is open, and its parameter values.
  const [open, setOpen] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"" | "send" | "queries" | "watch">("");
  // An error, and the command (or "queries") it belongs to.
  const [error, setError] = useState<{ where: string; text: string } | null>(null);
  // A send waiting for the person to read the command's warning first.
  const [pending, setPending] = useState<{ command: string; text: string; label: string; call: Call } | null>(null);
  const [showOthers, setShowOthers] = useState(false);
  const [search, setSearch] = useState("");

  const catalog = useMemo(() => run?.commands?.catalog ?? [], [run?.commands?.catalog]);
  const pickerState = run?.commands?.picker_state;
  const sessionId = session?.session_id ?? "";
  // The pickers read the audited driver, which is not a project device.
  const pickers = useMemo<ParamPickers>(
    () => ({
      loadChildren: (childType) =>
        audit.listAuditChildren(sessionId, childType).then((r) => r.children),
      stateValue: (key) => pickerState?.[key],
    }),
    [sessionId, pickerState],
  );
  const { suggested, others } = useMemo(() => commandSections(catalog), [catalog]);

  if (!session || !run) return null;
  const commands = run.commands;
  // Read from the listen state, which the live updates carry; the run's own
  // `active` is only as fresh as the last full state.
  const connected = !!run.listen?.active;
  const trials = commands?.trials ?? [];
  // A setting being written holds the device too.
  const settingBusy = run.settings?.current != null;
  const working =
    commands?.current != null || commands?.batch?.status === "running" || settingBusy;
  const queryCount = batchableQueries(catalog);
  const othersOpen = showOthers || suggested.length === 0;
  const shownOthers = others.filter((c) => commandMatches(c, search));

  const act = async (kind: "send" | "queries" | "watch", where: string, call: Call) => {
    setError(null);
    setPending(null);
    setBusy(kind);
    try {
      const { session: next } = await call();
      useAuditStore.getState().setSession(next);
    } catch (e) {
      setError({ where, text: parseApiError(e) });
    } finally {
      setBusy("");
    }
  };

  /** Send now, or say the command's warning first. */
  const sendOrWarn = (info: audit.AuditCommandInfo | undefined, name: string, label: string, call: Call) => {
    const text = info ? sendWarning(info) : "";
    if (text) setPending({ command: name, text, label, call });
    else void act("send", name, call);
  };

  const toggle = (name: string) => {
    setPending(null);
    setError(null);
    if (open === name) {
      setOpen("");
      return;
    }
    setOpen(name);
    const def = catalog.find((c) => c.name === name);
    setValues(seedCommandParams(def?.params ?? {}));
  };

  const locked = !connected || busy !== "" || working || pending !== null;

  const row = (c: audit.AuditCommandInfo) => {
    const mine = trials.filter((t) => t.command === c.name);
    const params = c.params ?? {};
    const blocked = commandParamsBlocked(params, values);
    return (
      <CommandRow
        key={c.name}
        command={c}
        trials={mine}
        open={open === c.name}
        onToggle={() => toggle(c.name)}
      >
        {Object.keys(params).length > 0 && (
          <CommandParamForm
            params={params}
            values={values}
            onChange={(name, v) => setValues((prev) => ({ ...prev, [name]: v }))}
            pickers={pickers}
          />
        )}
        <div style={{ marginTop: "var(--space-sm)" }}>
          <button
            type="button"
            onClick={() =>
              sendOrWarn(c, c.name, c.label, () =>
                audit.sendAuditCommand(sessionId, c.name, coerceCommandParams(params, values)),
              )
            }
            disabled={blocked || locked}
            style={buttonStyle("primary", blocked || locked)}
          >
            {busy === "send" ? <Loader2 size={14} style={spinStyle} /> : <Send size={14} />}
            Send
          </button>
        </div>
        {pending?.command === c.name && (
          <Warning pending={pending} onSend={() => void act("send", c.name, pending.call)} onCancel={() => setPending(null)} />
        )}
        {error?.where === c.name && (
          <div style={{ marginTop: "var(--space-sm)" }}>
            <ErrorLine text={error.text} />
          </div>
        )}
        {mine.length > 0 && (
          <ol style={{ listStyle: "none", margin: "var(--space-sm) 0 0", padding: 0 }}>
            {[...mine].reverse().map((t) => (
              <TrialRow
                key={t.number}
                trial={t}
                help={c.help}
                disabled={locked}
                onAgain={() =>
                  sendOrWarn(c, c.name, t.label, () =>
                    audit.sendAuditCommand(sessionId, t.command, {}, t.number),
                  )
                }
                onWaitLonger={() => void act("watch", c.name, () => audit.waitLonger(sessionId))}
                onStop={() => void act("watch", c.name, () => audit.stopWatching(sessionId))}
                onAnswer={(answer, note) =>
                  void act("watch", c.name, () => audit.answerAuditCommand(sessionId, t.number, answer, note))
                }
              />
            ))}
          </ol>
        )}
      </CommandRow>
    );
  };

  return (
    <div style={{ maxWidth: 820 }}>
      <h2 style={headingStyle}>Commands</h2>
      <p style={{ fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-xs)" }}>
        This step checks that each of the driver's commands does what it says on this device. Open
        a command and press Send, then watch or listen to the device and say whether it happened.
      </p>
      <ul style={{ fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-md)", paddingLeft: "var(--space-lg)" }}>
        <li>Every command changes the device. Send only the ones that are safe to run on this unit.</li>
        <li>
          {suggested.length > 0
            ? "You don't need to try them all. Start with the suggested ones, then any others you use."
            : "You don't need to try them all. Start with the ones you use most."}
        </li>
        <li>This step is optional. Continue when you are done.</li>
      </ul>

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

      {catalog.length > 0 && (
        <div role="status" style={{ fontSize: "var(--font-size-sm)", fontWeight: 600, marginBottom: "var(--space-md)" }}>
          {commandProgress(catalog, trials)}
        </div>
      )}

      {connected && catalog.length > 0 && (
        <>
          {queryCount > 0 && (
            <div style={{ ...panelStyle, marginBottom: "var(--space-md)" }}>
              <div style={labelStyle}>Status queries</div>
              <div style={{ fontSize: "var(--font-size-sm)" }}>
                {queryCount === 1 ? "This command only asks" : `These ${queryCount} commands only ask`}{" "}
                the device for its status and change nothing, so they can all run at once.
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", marginTop: "var(--space-sm)" }}>
                <button
                  type="button"
                  onClick={() => void act("queries", "queries", () => audit.runAuditQueries(sessionId))}
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
              {error?.where === "queries" && (
                <div style={{ marginTop: "var(--space-sm)" }}>
                  <ErrorLine text={error.text} />
                </div>
              )}
            </div>
          )}

          {suggested.length > 0 && (
            <section aria-label="Suggested commands" style={{ marginBottom: "var(--space-md)" }}>
              <div style={labelStyle}>Suggested ({suggested.length})</div>
              <div style={{ ...hintStyle, marginTop: 0, marginBottom: "var(--space-xs)" }}>
                The commands the driver puts first on a device page.
              </div>
              <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>{suggested.map(row)}</ul>
            </section>
          )}

          <section aria-label={suggested.length > 0 ? "Other commands" : "All commands"}>
            {suggested.length > 0 ? (
              <button
                type="button"
                onClick={() => setShowOthers(!showOthers)}
                aria-expanded={othersOpen}
                style={{
                  ...labelStyle,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  background: "none",
                  border: "none",
                  padding: 0,
                  color: "var(--text-primary)",
                  cursor: "pointer",
                }}
              >
                {othersOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                Other commands ({others.length})
              </button>
            ) : (
              <div style={labelStyle}>All commands ({others.length})</div>
            )}
            {othersOpen && (
              <>
                {others.length > 8 && (
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search commands..."
                    aria-label="Search commands"
                    style={{ ...inputStyle, marginBottom: "var(--space-sm)" }}
                  />
                )}
                <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>{shownOthers.map(row)}</ul>
                {shownOthers.length === 0 && (
                  <div style={{ ...hintStyle, fontSize: "var(--font-size-sm)" }}>
                    No command matches "{search.trim()}".
                  </div>
                )}
              </>
            )}
          </section>
        </>
      )}

      {connected && run.settings && (
        <SettingsPanel
          sessionId={sessionId}
          settings={run.settings}
          busy={commands?.current != null || commands?.batch?.status === "running"}
        />
      )}

      {(trials.length > 0 || (run.settings?.trials.length ?? 0) > 0) && (
        <WhatChanged changed={commands?.changed ?? []} />
      )}

      <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: "var(--space-sm)" }}>
        <button
          type="button"
          onClick={() => useAuditStore.getState().setStep("outage")}
          disabled={working}
          style={buttonStyle("primary", working)}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

/** What reads differently now from before the first command: what the audit
 *  left changed, and apart from it what changes without the audit. */
function WhatChanged({ changed }: { changed: audit.AuditChangedValue[] }) {
  const left = changed.filter((c) => !c.on_its_own);
  const moving = changed.filter((c) => c.on_its_own);
  return (
    <div style={{ ...panelStyle, marginTop: "var(--space-lg)", fontSize: "var(--font-size-sm)" }}>
      <div style={labelStyle}>What changed</div>
      {left.length === 0 ? (
        <div>Nothing the audit sent has left a value changed.</div>
      ) : (
        <>
          <div>These values changed during the audit:</div>
          <ul style={{ margin: "var(--space-xs) 0", paddingLeft: "var(--space-lg)" }}>
            {left.map((c) => (
              <li key={c.key} style={{ overflowWrap: "anywhere" }}>{changedText(c)}</li>
            ))}
          </ul>
          <div style={hintStyle}>
            The audit puts back the settings it wrote, but it cannot undo a command. Set these
            back on the device yourself if you need to.
          </div>
        </>
      )}
      {moving.length > 0 && (
        <div style={{ ...hintStyle, overflowWrap: "anywhere" }}>
          Also different now, but changing without the audit: {moving.map((c) => c.label).join(", ")}.
        </div>
      )}
    </div>
  );
}

const STATUS_ICON: Record<CommandStatusKey, ReactNode> = {
  not_tried: <Circle size={14} style={{ color: "var(--text-secondary)" }} />,
  watching: <Loader2 size={14} style={spinStyle} />,
  not_accepted: <AlertTriangle size={14} style={{ color: "var(--color-warning)" }} />,
  batch: <Check size={14} style={{ color: "var(--text-secondary)" }} />,
  waiting: <HelpCircle size={14} style={{ color: "var(--accent)" }} />,
  yes: <Check size={14} style={{ color: "var(--color-success)" }} />,
  no: <XCircle size={14} style={{ color: "var(--color-error)" }} />,
  partly: <AlertTriangle size={14} style={{ color: "var(--color-warning)" }} />,
  cant_tell: <HelpCircle size={14} style={{ color: "var(--text-secondary)" }} />,
};

/** One command in the list: what it does and where it stands; opened, what
 *  it takes, Send, and each time it was sent. */
function CommandRow({
  command,
  trials,
  open,
  onToggle,
  children,
}: {
  command: audit.AuditCommandInfo;
  trials: audit.AuditCommandTrial[];
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const status = commandStatus(trials);
  const tags = [
    command.query ? "Status query" : "",
    command.restarts_device_for > 0 ? "Restarts the device" : "",
  ].filter(Boolean);
  return (
    <li style={{ ...panelStyle, marginBottom: "var(--space-sm)", fontSize: "var(--font-size-sm)", padding: 0 }}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-label={`${command.label}: ${status.text}`}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          width: "100%",
          padding: "var(--space-sm) var(--space-md)",
          background: "none",
          border: "none",
          color: "var(--text-primary)",
          textAlign: "left",
          cursor: "pointer",
          fontSize: "var(--font-size-sm)",
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span aria-hidden style={{ display: "inline-flex", flexShrink: 0 }}>{STATUS_ICON[status.key]}</span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 600 }}>{command.label}</span>
          {tags.map((t) => (
            <span key={t} style={{ marginLeft: "var(--space-sm)", fontSize: "var(--font-size-xs)", color: "var(--text-secondary)" }}>
              {t}
            </span>
          ))}
          {command.help && (
            <span style={{ display: "block", fontSize: "var(--font-size-xs)", color: "var(--text-secondary)" }}>
              {command.help}
            </span>
          )}
        </span>
        <span style={{ flexShrink: 0, fontSize: "var(--font-size-xs)", color: "var(--text-secondary)" }}>
          {status.text}
        </span>
      </button>
      {open && <div style={{ padding: "0 var(--space-md) var(--space-md)" }}>{children}</div>}
    </li>
  );
}

function Warning({
  pending,
  onSend,
  onCancel,
}: {
  pending: { text: string; label: string };
  onSend: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      role="alert"
      aria-label={`Before sending ${pending.label}`}
      style={{
        ...panelStyle,
        marginTop: "var(--space-sm)",
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
        <button type="button" onClick={onSend} style={buttonStyle("primary")}>
          Send it
        </button>
        <button type="button" onClick={onCancel} style={buttonStyle("muted")}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function TrialRow({
  trial,
  help,
  disabled,
  onAgain,
  onWaitLonger,
  onStop,
  onAnswer,
}: {
  trial: audit.AuditCommandTrial;
  /** What the command should do, in the driver's words, for the question. */
  help: string;
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
  const { moved, moving } = movedParts(trial);
  const running = trial.status !== "done";
  const left = watching && trial.ends_at ? Math.max(0, Math.ceil(trial.ends_at - now)) : null;
  const before = trial.since_previous;
  return (
    <li
      style={{
        borderTop: "1px solid var(--border-color)",
        paddingTop: "var(--space-sm)",
        marginTop: "var(--space-sm)",
      }}
    >
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
      {moved.length > MOVED_IN_SENTENCE && (
        <div style={{ overflowWrap: "anywhere" }}>Everything it changed: {moved.map(movedText).join("; ")}</div>
      )}
      {moving.length > 0 && (
        <div style={{ color: "var(--text-secondary)", overflowWrap: "anywhere" }}>
          Already changing before it was sent, so not counted: {moving.map(movingText).join(", ")}
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
      {!running && !trial.batch && !trial.error && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          <div style={{ fontWeight: 600 }}>Did it happen?</div>
          {help && <div style={{ ...hintStyle, marginTop: 0 }}>What it should do: {help}</div>}
          <div
            role="group"
            aria-label={`Did ${trial.label} happen?`}
            style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-xs)", marginTop: "var(--space-xs)" }}
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
    </li>
  );
}
