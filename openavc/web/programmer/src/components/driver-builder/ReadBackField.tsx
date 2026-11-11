/** A command's `read_back` field in the Driver Builder: the queries sent once
 *  the command has gone out, so its new value is read at once instead of at
 *  the next poll. The runtime half is `ConfigurableDriver._read_back`.
 *
 *  What each form of command can read back with is one row of
 *  `READ_BACK_FORMS`: an OSC or HTTP command may read itself back (`true`),
 *  a byte-stream command only names queries. */

import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import type { DriverCommandDef } from "../../api/types";
import { FIELD_HINT, FIELD_LABEL, FlagField } from "./commandFieldParts";

type ReadBack = DriverCommandDef["read_back"];
type ReadBackForm = "osc" | "http" | "raw";

/** Which read-back form the field offers a command. An address is OSC, a path
 *  or method is HTTP, as the runtime picks; the driver's transport answers for
 *  a command that declares neither yet. `commandRoute` in validateDriver.ts
 *  reads the command's own fields only, the way the runtime sends it. */
export function readBackForm(cmd: DriverCommandDef, transport: string | undefined): ReadBackForm {
  if (cmd.address !== undefined || transport === "osc") return "osc";
  const requestShape = cmd.path !== undefined || cmd.method !== undefined;
  return requestShape || transport === "http" ? "http" : "raw";
}

/** Per form: how the command reads itself back (null: it cannot), what the
 *  query rows hold, and a query's example. */
const READ_BACK_FORMS: Record<ReadBackForm, { own: string | null; rows: string; placeholder: string }> = {
  osc: {
    own: "This command's own address, with no arguments",
    rows: "Each is an OSC address, sent with no arguments, or the name of a command to run.",
    placeholder: "e.g., /ch/{channel:02d}/mix/fader",
  },
  http: {
    own: "A GET of this command's own path",
    rows: "Each is a path read with GET, or the name of a command to run, for an API that reads with POST.",
    placeholder: "e.g., /api/status",
  },
  raw: {
    own: null,
    rows: "Each is sent as its own message, the way a poll query is, or names a command to run.",
    placeholder: "e.g., GET VOLUME\\r",
  },
};

/** A `read_back` value as the rows the field edits: `true` is no rows (the
 *  command's own address or path), a string one row, a list its rows. */
export function readBackRows(value: ReadBack): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === "string");
  return [];
}

/** The rows as the value the driver carries: one query is a string, more a
 *  list, none removes the key. Blank rows are not queries. */
export function readBackValue(rows: string[]): string | string[] | undefined {
  const queries = rows.filter((r) => r.trim() !== "");
  if (queries.length === 0) return undefined;
  return queries.length === 1 ? queries[0] : queries;
}

/** The rows being edited and whether the box is ticked, held here: a blank
 *  row being typed into, or the box ticked with no query yet, is not a value
 *  the driver can carry. Both follow a value changed from outside (an undo, a
 *  reloaded draft) without dropping the blank row somebody is typing into. */
function useReadBackState(value: ReadBack) {
  const fromValue = readBackRows(value);
  const [rows, setRows] = useState<string[]>(fromValue.length > 0 ? fromValue : [""]);
  const [ticked, setTicked] = useState(value !== undefined && value !== false);
  const valueKey = JSON.stringify(fromValue);
  useEffect(() => {
    const kept = rows.filter((r) => r.trim() !== "");
    if (JSON.stringify(kept) !== valueKey) {
      setRows(fromValue.length > 0 ? fromValue : [""]);
    }
    if (value !== undefined) setTicked(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [valueKey, value === true]);
  return { rows, setRows, ticked, setTicked };
}

/** The query rows, with the commands that may be named suggested. */
function QueryRows({
  commandId,
  rows,
  others,
  form,
  captioned,
  onWrite,
  onAdd,
}: {
  commandId: string;
  rows: string[];
  others: string[];
  form: ReadBackForm;
  captioned: boolean;
  onWrite: (rows: string[]) => void;
  onAdd: () => void;
}) {
  const listId = `read-back-commands-${commandId}`;
  return (
    <>
      {captioned && <label style={FIELD_LABEL}>Read back with</label>}
      <datalist id={listId}>
        {others.map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
      {rows.map((row, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-xs)",
            alignItems: "center",
          }}
        >
          <input
            aria-label={`Read-back query ${i + 1}`}
            list={listId}
            value={row}
            onChange={(e) => onWrite(rows.map((r, j) => (j === i ? e.target.value : r)))}
            placeholder={READ_BACK_FORMS[form].placeholder}
            style={{ flex: 1, fontFamily: "var(--font-mono)" }}
          />
          <button
            aria-label={`Remove read-back query ${i + 1}`}
            onClick={() => {
              const next = rows.filter((_, j) => j !== i);
              onWrite(next.length > 0 ? next : [""]);
            }}
            style={{ padding: "2px", color: "var(--text-muted)" }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        onClick={onAdd}
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--accent)",
          padding: "var(--space-xs) 0",
        }}
      >
        + Add a query
      </button>
      <div style={FIELD_HINT}>
        {READ_BACK_FORMS[form].rows} {"{param}"} takes this command&apos;s value as it was
        sent. A command named here is sent with this command&apos;s
        values for its parameters.
      </div>
    </>
  );
}

/** A command's `read_back`. Unticked removes the key. Ticked with no query
 *  typed yet is kept here, because an empty `read_back` is not a value the
 *  driver can carry. */
export function ReadBackField({
  commandId,
  value,
  form,
  commandNames,
  onChange,
}: {
  commandId: string;
  value: ReadBack;
  form: ReadBackForm;
  commandNames: string[];
  onChange: (readBack: ReadBack) => void;
}) {
  const ownLabel = READ_BACK_FORMS[form].own;
  const { rows, setRows, ticked, setTicked } = useReadBackState(value);
  const enabled = ticked || value !== undefined;
  const own = value === true;

  const writeRows = (next: string[]) => {
    setRows(next);
    onChange(readBackValue(next));
  };

  return (
    <div style={{ marginTop: "var(--space-md)" }}>
      <FlagField
        checked={enabled}
        onChange={(checked) => {
          setTicked(checked);
          if (!checked) {
            setRows([""]);
            onChange(undefined);
          } else if (ownLabel !== null) onChange(true);
          else setRows([""]);
        }}
        title="Reads its value back"
      >
        Tick this when the device&apos;s answer to this command does not say
        the new value, or the device never reports a change it was sent.
        OpenAVC then reads the value straight after sending instead of at
        the next poll, except while Pause Polling holds the poll. Keep the
        value in Poll Queries as well: OpenAVC before 0.39.0 reads it only
        there.
      </FlagField>
      {enabled && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          {ownLabel !== null && (
            <>
              <label style={FIELD_LABEL}>Read back with</label>
              <select
                aria-label="Read back with"
                value={own ? "own" : "queries"}
                onChange={(e) => {
                  if (e.target.value === "own") onChange(true);
                  else onChange(readBackValue(rows));
                }}
                style={{ width: "100%", marginBottom: "var(--space-sm)" }}
              >
                <option value="own">{ownLabel}</option>
                <option value="queries">The queries below</option>
              </select>
            </>
          )}
          {!own && (
            <QueryRows
              commandId={commandId}
              rows={rows}
              others={commandNames.filter((n) => n !== commandId)}
              form={form}
              captioned={ownLabel === null}
              onWrite={writeRows}
              onAdd={() => setRows([...rows, ""])}
            />
          )}
        </div>
      )}
    </div>
  );
}
