/** An HTTP command's request in the Driver Builder: method, path, body,
 *  headers and query parameters. The runtime half is the YAML driver
 *  runtime's `http_commands.py`.
 *
 *  An emptied headers or query table removes its key, so the YAML never
 *  carries an empty map. */

import type { DriverCommandDef } from "../../api/types";
import { type CommandUpdate } from "./commandEdits";
import { FIELD_HINT, FIELD_LABEL } from "./commandFieldParts";
import { KeyValueList } from "./KeyValueList";

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;

/** One of the request's maps, edited as rows. */
function RequestMapField({
  caption,
  field,
  cmd,
  onUpdate,
  keyPlaceholder,
  valuePlaceholder,
  children,
}: {
  caption: string;
  field: "headers" | "query_params";
  cmd: DriverCommandDef;
  onUpdate: CommandUpdate;
  keyPlaceholder: string;
  valuePlaceholder: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: "var(--space-md)" }}>
      <label style={FIELD_LABEL}>{caption}</label>
      <KeyValueList
        values={cmd[field] ?? {}}
        onChange={(map) => onUpdate({ [field]: Object.keys(map).length ? map : undefined })}
        keyPlaceholder={keyPlaceholder}
        valuePlaceholder={valuePlaceholder}
        monoValue
      />
      <div style={FIELD_HINT}>{children}</div>
    </div>
  );
}

/** The request line: the method, and the path under the device's base URL. */
function RequestLine({ cmd, onUpdate }: { cmd: DriverCommandDef; onUpdate: CommandUpdate }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "120px 1fr",
        gap: "var(--space-sm)",
        marginBottom: "var(--space-md)",
      }}
    >
      <div>
        <label style={FIELD_LABEL}>Method</label>
        <select
          value={(cmd.method ?? "GET").toUpperCase()}
          onChange={(e) => onUpdate({ method: e.target.value })}
          style={{ width: "100%" }}
        >
          {HTTP_METHODS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label style={FIELD_LABEL}>Path</label>
        <input
          value={cmd.path ?? ""}
          onChange={(e) => onUpdate({ path: e.target.value })}
          placeholder="/api/{app_key}/lights/{light_id}/state"
          style={{ width: "100%", fontFamily: "var(--font-mono)" }}
        />
      </div>
    </div>
  );
}

/** The fields of an HTTP command's request. */
export function HttpCommandFields({ cmd, onUpdate }: { cmd: DriverCommandDef; onUpdate: CommandUpdate }) {
  return (
    <div style={{ marginBottom: "var(--space-md)" }}>
      <RequestLine cmd={cmd} onUpdate={onUpdate} />
      <div style={FIELD_HINT}>
        Path is appended to the device's base URL. Use{" "}
        <code>{"{param_name}"}</code> for placeholders; both command params
        and device config keys (like <code>{"{host}"}</code>,{" "}
        <code>{"{app_key}"}</code>) are substituted.
      </div>

      <div style={{ marginTop: "var(--space-md)" }}>
        <label style={FIELD_LABEL}>Body</label>
        <textarea
          value={cmd.body ?? ""}
          onChange={(e) => onUpdate({ body: e.target.value })}
          placeholder='{"on": true}  or  &lt;Command>...&lt;/Command>'
          rows={4}
          style={{
            width: "100%",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--font-size-sm)",
            resize: "vertical",
          }}
        />
        <div style={FIELD_HINT}>
          Sent as the request body. JSON is parsed and re-serialized; anything
          else is sent as a raw byte string (set <code>Content-Type</code> in
          headers below for XML, form-urlencoded, etc.). Leave blank for{" "}
          <code>GET</code> / <code>DELETE</code>.
        </div>
      </div>

      <RequestMapField
        caption="Headers"
        field="headers"
        cmd={cmd}
        onUpdate={onUpdate}
        keyPlaceholder="Header-Name"
        valuePlaceholder="e.g. text/xml"
      >
        Per-request headers, applied on top of the transport's default
        headers. Common: <code>Content-Type: text/xml</code> for XML APIs.
        Values support <code>{"{param}"}</code> substitution.
      </RequestMapField>

      <RequestMapField
        caption="Query Parameters"
        field="query_params"
        cmd={cmd}
        onUpdate={onUpdate}
        keyPlaceholder="key"
        valuePlaceholder="value"
      >
        Appended to the URL as <code>?key=value&amp;...</code>. Values support{" "}
        <code>{"{param}"}</code> substitution.
      </RequestMapField>
    </div>
  );
}
