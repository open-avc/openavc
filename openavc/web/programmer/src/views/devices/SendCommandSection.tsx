/**
 * Send Command on a device's page: choose one of the driver's commands, fill
 * its parameters, send it, and read what came back.
 *
 * The section owns everything a send needs and stays mounted while the driver
 * lists no commands: a driver that narrows its commands to what the hardware
 * reports does so again on every connect, and the command chosen before a drop
 * is still chosen when the device comes back. A command that erases or resets
 * asks first (commandConfirmMessage); one that takes or hands back a file is
 * uploaded and run in the background (useCommandFileRun), never sent here.
 * A failure the driver could have caused offers Report a problem, which the
 * page opens with what was sent.
 */
import { useCallback, useMemo, useState } from "react";
import { AlertTriangle, Check, Send } from "lucide-react";
import * as api from "../../api/restClient";
import { ApiError, apiErrorCode, parseApiError } from "../../api/errors";
import type { DriverParamDef } from "../../api/types";
import type { ProblemReportContext } from "../../api/problemReportClient";
import { CommandParamForm } from "../../components/shared/CommandParamForm";
import { ConfirmDialog } from "../../components/shared/ConfirmDialog";
import { SearchableSelect } from "../../components/shared/SearchableSelect";
import { fileParamNames, fileParamsBlocked, isFileCommand } from "../../components/shared/commandFiles";
import {
  coerceCommandParams,
  commandConfirmMessage,
  commandParamsBlocked,
  commandResultText,
  seedCommandParams,
  type CommandParamDefs,
} from "../../components/shared/commandParams";
import { commandOptions } from "../../components/shared/pickerOptions";
import { CommandFileRunStatus, useCommandFileRun } from "./CommandFileRun";
import { ReportLink } from "./ProblemReportDialog";
import { reportableCommandFailure } from "./problemReport";
import { sectionStyle, sectionTitleStyle, surfaceStyle } from "./deviceSectionStyles";

type Commands = Record<string, unknown>;
type CommandDef = Record<string, unknown> | undefined;

/** What the last send came to. A failure keeps its HTTP status and code, and
 *  what was sent, for Report a problem. */
interface CommandResult {
  ok: boolean;
  text: string;
  detail?: string;
  status?: number;
  code?: string;
  command?: string;
  params?: Record<string, unknown>;
}

/** The parameters to send, each as its declared type, the way Live Test and
 *  the device audit send them: a blank field is left out, and so is a file,
 *  which goes up on its own. */
function filledParams(
  paramDefs: CommandParamDefs,
  values: Record<string, string>,
  fileParams: Set<string>,
): Record<string, unknown> {
  const params = coerceCommandParams(paramDefs, values);
  for (const k of fileParams) delete params[k];
  return params;
}

/** The chosen file for each of the command's file parameters. */
function chosenFiles(files: Record<string, File | null>, fileParams: Set<string>): Record<string, File> {
  const chosen: Record<string, File> = {};
  for (const [k, f] of Object.entries(files)) {
    if (f && fileParams.has(k)) chosen[k] = f;
  }
  return chosen;
}

function failedResult(e: unknown, command: string, params: Record<string, unknown>): CommandResult {
  return {
    ok: false,
    text: parseApiError(e),
    status: e instanceof ApiError ? e.status : undefined,
    code: apiErrorCode(e),
    command,
    params,
  };
}

/** What the form knows of the chosen command: its definition, parameters and
 *  label, and whether Send waits. Send waits on an invalid or missing
 *  parameter (each field says which) and on a file run still going; the
 *  server checks the parameters too. */
function chosenCommand(commands: Commands, form: CommandFormState, runBusy: boolean) {
  const def = commands[form.selected] as CommandDef;
  const paramDefs = (def?.params as Record<string, Partial<DriverParamDef>>) ?? {};
  return {
    def,
    paramDefs,
    label: (def?.label as string | undefined) || form.selected,
    blocked: commandParamsBlocked(paramDefs, form.values) || fileParamsBlocked(paramDefs, form.files) || runBusy,
  };
}

/** The chosen command, its form's values, and the file chosen for each file
 *  parameter. */
interface CommandFormState {
  selected: string;
  values: Record<string, string>;
  files: Record<string, File | null>;
}

/** Sends the chosen command, or uploads its files and starts its run in the
 *  background. The answer to show, or null for a run: CommandFileRunStatus
 *  follows that. */
async function deliver(
  deviceId: string,
  commands: Commands,
  form: CommandFormState,
  fileRun: ReturnType<typeof useCommandFileRun>,
): Promise<CommandResult | null> {
  const def = commands[form.selected] as { label?: string; params?: CommandParamDefs } | undefined;
  const label = def?.label || form.selected;
  const fileParams = new Set(fileParamNames(def));
  const params = filledParams(def?.params ?? {}, form.values, fileParams);
  try {
    if (isFileCommand(def)) {
      await fileRun.start(form.selected, label, params, chosenFiles(form.files, fileParams));
      return null;
    }
    const response = await api.sendCommand(deviceId, form.selected, params);
    return { ok: true, ...commandResultText(label, response) };
  } catch (e) {
    return failedResult(e, form.selected, params);
  }
}

/**
 * The send, from choosing a command to its answer: the choice, its form's
 * values and files, the question a command asks before it goes, the send or
 * the file run, and what came back. Owned by the always-mounted section, so
 * all of it outlives a drop of the device.
 */
function useCommandSend(deviceId: string, commands: Commands) {
  const [selected, setSelected] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const fileRun = useCommandFileRun(deviceId);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<CommandResult | null>(null);
  // What the driver asks before the chosen command is sent, while asking.
  const [question, setQuestion] = useState<string | null>(null);
  const chosen = chosenCommand(commands, { selected, values, files }, fileRun.busy);

  const send = useCallback(async () => {
    if (!selected) return;
    setSending(true);
    setResult(null);
    try {
      const answer = await deliver(deviceId, commands, { selected, values, files }, fileRun);
      if (answer) setResult(answer);
    } finally {
      setSending(false);
    }
  }, [deviceId, selected, values, files, commands, fileRun]);

  /** A new choice starts its form on the driver's defaults (an enum's
   *  default or first choice, a flag's default or No) and clears the last
   *  answer, and the last file run unless one is still going. */
  const choose = (command: string) => {
    setSelected(command);
    const defs = (commands[command] as Record<string, unknown>)?.params as CommandParamDefs | undefined;
    setValues(seedCommandParams(defs ?? {}, { fillChoices: true }));
    setFiles({});
    setResult(null);
    if (!fileRun.busy) fileRun.clear();
  };

  /** Send, or first ask the driver's question for a command that erases or
   *  resets the device. */
  const requestSend = () => {
    const ask = commandConfirmMessage(chosen.def, chosen.label);
    if (ask) setQuestion(ask);
    else void send();
  };

  /** The answer to the question: yes sends. */
  const answer = (yes: boolean) => {
    setQuestion(null);
    if (yes) void send();
  };

  return {
    selected, values, setValues, files, setFiles, sending, result, question, chosen,
    runView: fileRun.view,
    stopRun: () => void fileRun.stop(),
    choose, requestSend, answer,
  };
}

type CommandSend = ReturnType<typeof useCommandSend>;

export function SendCommandSection({
  deviceId,
  deviceName,
  commands,
  connected,
  onReport,
}: {
  deviceId: string;
  deviceName: string;
  commands: Commands;
  connected: boolean;
  onReport: (context: ProblemReportContext) => void;
}) {
  const send = useCommandSend(deviceId, commands);
  return (
    <div style={sectionStyle}>
      <h3 style={sectionTitleStyle}>Send Command</h3>
      <div style={{ ...surfaceStyle, padding: "var(--space-lg)" }}>
        {Object.keys(commands).length === 0 ? (
          <NoCommands connected={connected} />
        ) : (
          <CommandForm deviceId={deviceId} deviceName={deviceName} commands={commands} send={send} onReport={onReport} />
        )}
      </div>
    </div>
  );
}

/** The chosen command's form: the dropdown and Send, its parameters, a file
 *  run, the last answer, and the question a command asks before it goes. */
function CommandForm({
  deviceId,
  deviceName,
  commands,
  send,
  onReport,
}: {
  deviceId: string;
  deviceName: string;
  commands: Commands;
  send: CommandSend;
  onReport: (context: ProblemReportContext) => void;
}) {
  const { paramDefs, label, blocked } = send.chosen;
  return (
    <>
      <CommandChooser
        commands={commands}
        selected={send.selected}
        onChoose={send.choose}
        ready={Boolean(send.selected) && !blocked}
        sending={send.sending}
        onSend={send.requestSend}
      />
      {Object.keys(paramDefs).length > 0 && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          {/* A child_id parameter lists the device's live children,
              fetched per field as the driver finds them. */}
          <CommandParamForm
            layout="inline"
            params={paramDefs}
            values={send.values}
            onChange={(name, val) => send.setValues((p) => ({ ...p, [name]: val }))}
            deviceId={deviceId}
            files={send.files}
            onFileChange={(name, file) => send.setFiles((f) => ({ ...f, [name]: file }))}
          />
        </div>
      )}
      <CommandFileRunStatus
        view={send.runView}
        deviceId={deviceId}
        deviceName={deviceName}
        onStop={send.stopRun}
        onReport={onReport}
      />
      {send.result !== null && <CommandResultLine result={send.result} onReport={onReport} />}
      {send.question !== null && (
        <ConfirmDialog
          title={label}
          message={send.question}
          confirmLabel="Send"
          destructive
          onConfirm={() => send.answer(true)}
          onCancel={() => send.answer(false)}
        />
      )}
    </>
  );
}

function NoCommands({ connected }: { connected: boolean }) {
  return (
    <div style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
      {!connected
        ? "Device is not connected. Commands will be available once the device connects."
        : "No commands available. The driver may not be loaded or may not define any commands."}
    </div>
  );
}

/** The command dropdown, Send, and the chosen command's help line. */
function CommandChooser({
  commands,
  selected,
  onChoose,
  ready,
  sending,
  onSend,
}: {
  commands: Commands;
  selected: string;
  onChoose: (command: string) => void;
  /** A command is chosen and nothing holds Send. */
  ready: boolean;
  sending: boolean;
  onSend: () => void;
}) {
  const help = selected ? ((commands[selected] as CommandDef)?.help as string | undefined) : undefined;
  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        <CommandPicker commands={commands} value={selected} onChange={onChoose} />
        <button
          onClick={onSend}
          disabled={!ready || sending}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-xs)",
            padding: "var(--space-sm) var(--space-lg)",
            borderRadius: "var(--border-radius)",
            background: ready ? "var(--accent-bg)" : "var(--bg-hover)",
            color: ready ? "var(--text-on-accent)" : "var(--text-muted)",
            opacity: sending ? 0.6 : 1,
          }}
        >
          <Send size={14} /> Send
        </button>
      </div>
      {help ? (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
          {help}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The command dropdown: `SearchableSelect` like every other command dropdown
 * in the product, so a command reads the same here as it does in a macro step
 * or a panel binding. The one picker that offers a command taking or handing
 * back a file.
 */
function CommandPicker({
  commands,
  value,
  onChange,
}: {
  commands: Commands;
  value: string;
  onChange: (cmd: string) => void;
}) {
  const options = useMemo(() => commandOptions(commands, { fileCommands: true }), [commands]);
  return (
    <SearchableSelect
      value={value}
      onChange={onChange}
      options={options}
      allowEmpty={false}
      placeholder="Select a command..."
      searchPlaceholder="Search commands..."
      emptyHint="This driver declares no commands."
      testId="command-picker"
      style={{ flex: 1 }}
    />
  );
}

/** What the last send came to: sent (with the device's answer, if any), or why
 *  not, with Report a problem when the driver could be at fault. */
function CommandResultLine({
  result,
  onReport,
}: {
  result: CommandResult;
  onReport: (context: ProblemReportContext) => void;
}) {
  const report = () =>
    onReport({
      source: "command_failure",
      error: result.text,
      command: result.command,
      params: result.params,
      status: result.status,
      code: result.code ?? "",
    });
  return (
    <div role="status" style={{ fontSize: "var(--font-size-sm)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          color: result.ok ? "var(--color-success)" : "var(--color-error)",
        }}
      >
        {result.ok ? <Check size={14} /> : <AlertTriangle size={14} />}
        <span>{result.text}</span>
        {!result.ok && reportableCommandFailure(result.status, result.code) && <ReportLink onClick={report} />}
      </div>
      {result.detail && (
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
          {result.detail}
        </pre>
      )}
    </div>
  );
}
