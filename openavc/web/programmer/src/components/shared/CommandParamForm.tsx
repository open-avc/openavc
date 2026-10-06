import type { CSSProperties } from "react";
import type { CommandParamDefs } from "./commandParams";
import { ParamInput, type ParamPickers } from "./ParamInput";
import { paramLabel } from "./paramLabel";

/**
 * The parameters of one command, as every form that sends a command draws
 * them: the device page's Send Command, the Driver Builder's Test tab, and a
 * device audit's Commands step. Each field is the shared `ParamInput`, so an
 * enum is the same picker everywhere and a child or a state-fed list is live
 * wherever there is something to read it from.
 *
 * What the form starts with, what it sends and when it cannot send are the
 * helpers in `commandParams.ts`; the surfaces differ only in layout:
 *
 * - `stacked`: each label above its field (a device audit).
 * - `inline`: the name beside its field, the key a script sends on hover (the
 *   device page).
 * - `grid`: tiles, several to a row (the Driver Builder's Test tab).
 *
 * A field is named by `paramLabel`: its label, else its key made readable.
 */
export function CommandParamForm({
  params,
  values,
  onChange,
  deviceId,
  pickers,
  layout = "stacked",
}: {
  params: CommandParamDefs;
  values: Record<string, string>;
  onChange: (name: string, value: string) => void;
  /** A project device: its live children and status values feed the pickers. */
  deviceId?: string;
  /** Anywhere else the pickers read from. */
  pickers?: ParamPickers;
  layout?: "stacked" | "inline" | "grid";
}) {
  const names = Object.keys(params);
  if (names.length === 0) return null;

  const field = (name: string) => {
    const def = params[name];
    const help = def.help ?? def.description;
    const shown = paramLabel(name, def);
    const input = (
      <ParamInput
        def={def}
        value={values[name] ?? ""}
        onChange={(v) => onChange(name, v)}
        deviceId={deviceId}
        pickers={pickers}
        values={values}
        params={params}
        placeholder={layout === "grid" ? undefined : shown}
        style={layout === "inline" ? { flex: 1 } : { width: "100%" }}
      />
    );
    if (layout === "inline") {
      return (
        <div key={name} style={{ marginBottom: "var(--space-sm)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
            <label title={name} style={{ width: 120, fontSize: "var(--font-size-sm)", color: "var(--text-secondary)" }}>
              {shown}
            </label>
            {input}
          </div>
          {help && <div style={{ ...helpStyle, fontSize: 11, marginLeft: 120 }}>{help}</div>}
        </div>
      );
    }
    const label = `${shown}${def.required ? " *" : ""}`;
    if (layout === "grid") {
      return (
        <div key={name}>
          <label style={{ display: "block", fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>
            {label}
          </label>
          {input}
          {help && <div style={{ ...helpStyle, fontSize: 10 }}>{help}</div>}
        </div>
      );
    }
    return (
      <div key={name} style={{ marginBottom: "var(--space-sm)" }}>
        <div style={{ fontSize: "var(--font-size-sm)", color: "var(--text-secondary)", marginBottom: 2 }}>
          {label}
        </div>
        {input}
        {help && <div style={{ ...helpStyle, fontSize: 11 }}>{help}</div>}
      </div>
    );
  };

  if (layout === "grid") {
    return (
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: "var(--space-sm)",
        }}
      >
        {names.map(field)}
      </div>
    );
  }
  return <>{names.map(field)}</>;
}

const helpStyle: CSSProperties = { color: "var(--text-muted)", marginTop: 2 };
