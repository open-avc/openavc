/**
 * How a published option row is drawn when it has something to say.
 *
 * Two surfaces show the same rows and must not describe them differently: the
 * picker on a panel element (OptionSourcePicker) and the Video Streams page.
 * A source that reads "Needs a setting" in one place and something else in the
 * other is the confusion this set of rows exists to remove, so the wording and
 * the inline field live here once.
 *
 * Nothing here knows what any particular row is. The publisher says what it is
 * called, why it cannot be used, and which device config field would change
 * that; this draws it.
 */
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { useProjectStore } from "../../store/projectStore";
import { showError, showSuccess } from "../../store/toastStore";
import type { OptionRow } from "./paramOptions";
import * as api from "../../api/restClient";
import type { DriverInfo } from "../../api/types";
import { coerceConfigValue, configFieldKind } from "../../views/devices/deviceConfigCoerce";

/** How a status word reads on screen. An unknown one is shown as published
 *  rather than swallowed: a surface that hides a word it does not recognise is
 *  how the silence this exists to fix creeps back in. */
const STATUS_WORDS: Record<string, string> = {
  offline: "Offline",
  needs_setup: "Needs a setting",
  unavailable: "No stream",
};

export function statusWord(status: string): string {
  return STATUS_WORDS[status] ?? status.replace(/_/g, " ");
}

export const chipStyle: CSSProperties = {
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: "0.4px",
  padding: "1px 6px",
  borderRadius: 8,
  border: "1px solid var(--border-color)",
  color: "var(--text-muted)",
  whiteSpace: "nowrap",
};

export const groupHeadingStyle: CSSProperties = {
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: "0.4px",
  color: "var(--text-muted)",
};

/** Rows in publish order, bucketed by `group`. Ungrouped first: those belong
 *  to no device, so putting them under a heading would be a lie. */
export function byGroup(rows: OptionRow[]): Array<[string, OptionRow[]]> {
  const groups: Array<[string, OptionRow[]]> = [];
  for (const row of rows) {
    const name = row.group ?? "";
    const found = groups.find(([g]) => g === name);
    if (found) found[1].push(row);
    else groups.push([name, [row]]);
  }
  return groups.sort((a, b) => (a[0] === "" ? -1 : b[0] === "" ? 1 : 0));
}

/** One row, with its mark, its sentence, and the field that would fix it. */
export function OptionRowCard({ row, trailing }: { row: OptionRow; trailing?: ReactNode }) {
  return (
    <div
      style={{
        border: "1px solid var(--border-color)",
        borderRadius: "var(--border-radius)",
        padding: "6px 8px",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          justifyContent: "space-between",
        }}
      >
        <span style={{ fontSize: "var(--font-size-sm)" }}>{row.label}</span>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {row.status && <span style={chipStyle}>{statusWord(row.status)}</span>}
          {trailing}
        </span>
      </div>
      {row.detail && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.5 }}>
          {row.detail}
        </div>
      )}
      {row.setup && <SetupField device={row.setup.device} field={row.setup.field} />}
    </div>
  );
}

/**
 * The setting that would make a row usable, offered where the row is.
 *
 * It writes to the DEVICE's config, which is where it lives and where it would
 * otherwise have to be hunted for. The field is looked up in that driver's own
 * config_schema, so it carries the driver's label, help and type rather than
 * anything invented here.
 */
export function SetupField({ device, field }: { device: string; field: string }) {
  const devices = useProjectStore((s) => s.project?.devices);
  const entry = devices?.find((d) => d.id === device);
  const [drivers, setDrivers] = useState<DriverInfo[] | null>(null);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .listDrivers()
      .then((d) => {
        if (live) setDrivers(d);
      })
      .catch(() => {
        if (live) setDrivers([]);
      });
    return () => {
      live = false;
    };
  }, []);

  const schema = useMemo(() => {
    const driver = drivers?.find((d) => d.id === entry?.driver);
    const all = (driver?.config_schema ?? {}) as Record<string, Record<string, unknown>>;
    return all[field] ?? {};
  }, [drivers, entry?.driver, field]);

  // Seed from what the device already has, so a number that is wrong is on
  // screen to be corrected rather than replaced by an empty box.
  useEffect(() => {
    const current = (entry?.config ?? {})[field];
    setText(current == null ? "" : String(current));
  }, [entry?.config, field]);

  if (!entry) return null;

  const label = String(schema.label || field);
  const help = String(schema.help || schema.description || "");
  const kind = configFieldKind(schema);
  const fieldType = String(schema.type || "string");

  const save = async () => {
    const coerced = coerceConfigValue(text, fieldType, schema.secret === true);
    if (!coerced.ok) {
      showError(`${label}: ${coerced.error}`);
      return;
    }
    setSaving(true);
    try {
      // The whole config goes back: the device endpoint replaces the protocol
      // config with what it is sent, so a partial body would drop every other
      // field this driver has.
      await api.updateDevice(device, {
        config: { ...(entry.config ?? {}), [field]: coerced.value },
      });
      await useProjectStore.getState().load();
      showSuccess(`${label} saved.`);
    } catch {
      showError(`Could not save ${label}.`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 2 }}>
      <label style={groupHeadingStyle}>{label}</label>
      <div style={{ display: "flex", gap: 4 }}>
        <input
          value={text}
          type={kind === "number" ? "number" : "text"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void save();
          }}
          placeholder={schema.default != null ? String(schema.default) : ""}
          style={{ flex: 1, minWidth: 0 }}
        />
        <button onClick={() => void save()} disabled={saving} style={{ whiteSpace: "nowrap" }}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {help && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.4 }}>{help}</div>
      )}
    </div>
  );
}
